"""Multi-pass scanning using Pokemon Go search filters.

Flow per pass:
  1. Get to storage (smart navigation)
  2. Clear old search → type new filter → apply
  3. Tap first Pokemon → open appraisal
  4. Read → swipe → repeat (state machine)
  5. When done: navigate back to storage for next pass
"""

import logging
import time
from dataclasses import dataclass

from ..adb.controller import ADBController
from ..adb.navigator import GameNavigator
from ..calibration.profile import CalibrationProfile
from ..data.database import PokemonDatabase
from ..indexer.state_machine import IndexingStateMachine
from ..config import human_delay

log = logging.getLogger(__name__)


@dataclass
class ScanPass:
    name: str
    search_query: str
    tags: dict


DEFAULT_PASSES = [
    ScanPass("Normal", "!shiny&!shadow", {}),
    ScanPass("Shiny", "shiny", {"shiny": True}),
    ScanPass("Shadow", "shadow", {"shadow": True}),
]


class MultiPassScanner:
    def __init__(self, adb: ADBController, profile: CalibrationProfile,
                 db: PokemonDatabase):
        self.adb = adb
        self.profile = profile
        self._abort = False
        self.nav = GameNavigator(
            adb,
            profile.regions,
            cancelled=lambda: self._abort,
        )
        self.db = db
        self.passes = list(DEFAULT_PASSES)
        self.unfavorite_all = False
        self.max_per_pass = 0
        self.skip_first_n = 0          # for resume — skip N Pokemon on first pass
        self.skip_delay = 0.05         # delay between swipes when skipping
        self.resume_target_species = ""
        self.resume_target_cp = 0

        self.on_pass_start = None   # (pass_index, pass_name, query)
        self.on_pass_count = None   # (total_expected) — called after reading filtered count
        self.on_progress = None     # (total_count, pokemon)
        self.on_pass_end = None     # (pass_index, pass_count)
        self.on_error = None
        self.on_finished = None

        self._total_count = 0
        self._total_skipped = 0
        self._current_sm: IndexingStateMachine | None = None

    def start(self):
        from ..scan_logger import start_scan_log, stop_scan_log
        # Calls and idle periods can reactivate Samsung Touch Protection.  Clear
        # it at the exact start boundary before any screen classification/taps.
        self.nav.clear_touch_protection()
        time.sleep(0.25)
        log_path = start_scan_log()
        log.info("Multi-pass scan: %d passes (log: %s)", len(self.passes), log_path)
        self._total_count = 0
        fatal_failure = None

        try:
            for i, scan_pass in enumerate(self.passes):
                if self._abort:
                    break

                log.info("=== Pass %d/%d: %s (%s) ===",
                         i + 1, len(self.passes), scan_pass.name, scan_pass.search_query)

                if self.on_pass_start:
                    self.on_pass_start(i, scan_pass.name, scan_pass.search_query)

                try:
                    pass_count = self._run_pass(scan_pass)
                except Exception as e:
                    log.exception("Pass %s failed: %s", scan_pass.name, e)
                    fatal_failure = (scan_pass.name, e)
                    break

                if self.on_pass_end:
                    self.on_pass_end(i, pass_count)
        finally:
            stop_scan_log()

        if fatal_failure:
            pass_name, cause = fatal_failure
            log.error("Stopped remaining passes after %s failed", pass_name)
            raise RuntimeError(
                f"Pass {pass_name} failed; scan stopped: {cause}"
            ) from cause

        log.info("Done: %d total Pokemon, %d skipped", self._total_count, self._total_skipped)
        if self.on_finished:
            self.on_finished(self._total_count)

    def _run_pass(self, scan_pass: ScanPass) -> int:
        if self._abort:
            return 0

        # Step 1: Get to storage (from wherever we are)
        log.info("Getting to storage...")
        if not self._abort and not self.nav.navigate_to_storage():
            if self._abort:
                return 0
            self.nav.ensure_pokemon_go()
            time.sleep(3)
            if self._abort:
                return 0
            if not self.nav.navigate_to_storage():
                raise Exception("Cannot get to Pokemon storage")

        if self._abort:
            return 0

        # Step 2: Clear old search and enter new filter
        log.info("Entering filter: %s", scan_pass.search_query)
        self._clear_and_search(scan_pass.search_query)

        if self._abort:
            return 0

        # Read how many Pokemon match this filter.  A resume skip is a position
        # offset, not work completed in this run, so the state-machine target is
        # the number of new positions remaining after the skipped prefix.
        filtered_count = self.nav.read_filtered_count()
        resume_skip = max(0, int(self.skip_first_n))
        if filtered_count > 0 and resume_skip >= filtered_count:
            raise RuntimeError(
                f"Resume skip {resume_skip} leaves no Pokemon to scan "
                f"(filter count: {filtered_count})"
            )

        pass_expected = (
            max(0, filtered_count - resume_skip)
            if filtered_count > 0 else 0
        )
        if self.max_per_pass > 0:
            pass_expected = (
                min(pass_expected, self.max_per_pass)
                if pass_expected > 0 else self.max_per_pass
            )
        self._pass_expected = pass_expected
        log.info(
            "Filter matched %d Pokemon; %d new positions after resume/cap",
            filtered_count,
            pass_expected,
        )

        if self.on_pass_count:
            self.on_pass_count(pass_expected)

        # Don't skip on 0 — OCR might have failed to read the count
        # The scan will stop naturally if there are truly no results (end-of-list detection)
        if pass_expected == 0:
            log.warning("Could not read filter count — proceeding anyway")

        # Step 3: Tap the first Pokemon, but only use detail-screen menu
        # coordinates after screen detection confirms that the detail screen
        # actually opened.  A missed grid tap previously made the appraisal
        # taps land on storage and could return the scan to the game map.
        if not self._prepare_first_appraisal(scan_pass.search_query):
            if self._abort:
                return 0
            raise Exception("Cannot open first Pokemon appraisal")

        # Step 4: Scan
        sm = IndexingStateMachine(self.adb, self.profile, self.db)
        sm.unfavorite_all = self.unfavorite_all
        # Apply skip for resume (only on first pass)
        if resume_skip > 0:
            sm.skip_first_n = resume_skip
            sm.skip_delay = self.skip_delay
            sm.resume_target_species = self.resume_target_species
            sm.resume_target_cp = self.resume_target_cp
            self.skip_first_n = 0  # only skip on first pass
        if self.max_per_pass > 0:
            sm.max_count = self.max_per_pass
        self._current_sm = sm

        tags = scan_pass.tags

        def on_progress(count, pokemon):
            self._total_count += 1
            if self.on_progress:
                self.on_progress(self._total_count, pokemon)

        sm.on_progress = on_progress
        sm.on_error = self.on_error

        try:
            sm.start(expected_total=pass_expected if pass_expected > 0 else None)
        finally:
            # Do not leave a failed state machine attached while the outer
            # scanner reports the error or the GUI requests Stop.
            self._current_sm = None
            self._total_skipped += sm.skipped_count

            # A fatal OCR/runtime error can happen after valid rows were
            # stored.  Preserve those partial rows with the correct pass tags
            # even though the session remains visibly incomplete.
            if tags and sm.count > 0:
                log.info("Tagging %d Pokemon from pass '%s' with %s",
                         sm.count, scan_pass.name, tags)
                self._apply_tags_to_session(sm.session_id, tags)

        # After scan ends, close appraisal and get back to storage
        log.info("Pass done (%d scanned), returning to storage...", sm.count)
        if not self._abort and not self.nav.navigate_to_storage():
            raise RuntimeError("Pass ended but storage could not be confirmed")

        return sm.count

    def _clear_and_search(self, query: str):
        """Clear any existing search filter and enter a new one.

        The storage search bar might have an old filter from the previous pass.
        Route through GameNavigator so calibrated tablet targets are used.  The
        former duplicate implementation scaled Fold coordinates directly and
        missed the tablet search bar.
        """
        self.nav.enter_search(query)

    def _prepare_first_appraisal(self, search_query: str) -> bool:
        """Open and verify the first appraisal before starting OCR."""
        self.nav.tap_first_pokemon()
        if self._abort:
            return False

        screen = self.nav.detect_screen()
        if screen == "detail":
            self.nav.open_first_appraisal()
            if self._abort:
                return False
            screen = self.nav.detect_screen()

        if screen == "appraisal":
            return True

        log.warning(
            "First Pokemon did not reach appraisal (got %s); recovering",
            screen,
        )
        return self.nav.navigate_to_appraisal(search_query or None)

    def _apply_tags_to_session(self, session_id: str, tags: dict):
        """Apply tags to ALL Pokemon in a scan session."""
        for field, value in tags.items():
            self.db.conn.execute(
                f"UPDATE pokemon SET {field} = ? WHERE scan_session_id = ?",
                (int(value), session_id),
            )
        self.db.conn.commit()
        log.info("Tagged session %s with %s", session_id, tags)

    def abort(self):
        self._abort = True
        if self._current_sm:
            self._current_sm.abort()
