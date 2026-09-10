"""Multi-pass scanning using Pokemon Go search filters.

Flow per pass:
  1. Get to storage (smart navigation)
  2. Clear old search → type new filter → apply
  3. Tap first Pokemon → open appraisal
  4. Read → swipe → repeat (state machine)
  5. When done: navigate back to storage for next pass
"""

# TRACEWEAVER: file-role=verified-pass-coverage; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
# TRACEWEAVER: file-role=pass-session-preservation; req=REQ-DATA-001; trace=TRACE-DATA-001; ver=VER-SCAN-001

import logging
import threading
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


class _PassSetupInvalidated(RuntimeError):
    """A pause discarded the filter, count and first-card setup proof."""


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
        self._paused = False
        self._pause_generation = 0
        self._setup_generation = None
        self._control_lock = threading.RLock()
        self.nav = GameNavigator(
            adb,
            profile.regions,
            cancelled=self._navigation_cancelled,
            observation_generation=lambda: self._pause_generation,
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
        if self._navigation_cancelled():
            return
        # Calls and idle periods can reactivate Samsung Touch Protection.  Clear
        # it at the exact start boundary before any screen classification/taps.
        self.nav.clear_touch_protection()
        time.sleep(0.25)
        log_path = start_scan_log()
        log.info("Multi-pass scan: %d passes (log: %s)", len(self.passes), log_path)
        self._total_count = 0
        self._total_skipped = 0
        fatal_failure = None

        try:
            for i, scan_pass in enumerate(self.passes):
                if self._navigation_cancelled():
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

                if self.on_pass_end and not self._abort:
                    self.on_pass_end(i, pass_count)
        finally:
            stop_scan_log()

        if fatal_failure:
            pass_name, cause = fatal_failure
            log.error("Stopped remaining passes after %s failed", pass_name)
            raise RuntimeError(
                f"Pass {pass_name} failed; scan stopped: {cause}"
            ) from cause

        if self._abort:
            log.info("Aborted: %d total Pokemon, %d skipped", self._total_count, self._total_skipped)
            return
        log.info("Done: %d total Pokemon, %d skipped", self._total_count, self._total_skipped)
        if self.on_finished:
            self.on_finished(self._total_count)

# TRACEWEAVER: entrypoint=_run_pass; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
    def _run_pass(self, scan_pass: ScanPass) -> int:
        # A pause allows the user to change the phone's filter or position.
        # Retry only unstarted setup, once per newly invalidated pause epoch.
        while not self._navigation_cancelled():
            self._setup_generation = self._pause_generation
            try:
                count = self._run_pass_once(scan_pass)
                self._check_pass_setup()
                return count
            except Exception:
                if self._abort:
                    return 0
                if (self._setup_generation is None
                        or self._setup_generation == self._pause_generation):
                    raise
                log.info("Pass setup paused; reapplying the full verified filter before scanning")
            finally:
                self._setup_generation = None
        return 0

    def _check_pass_setup(self):
        if (not self._abort and self._setup_generation is not None
                and self._setup_generation != self._pause_generation):
            raise _PassSetupInvalidated("Pass setup invalidated by pause")

    def _run_pass_once(self, scan_pass: ScanPass) -> int:
        if self._abort:
            return 0

        # Step 1: Get to storage (from wherever we are)
        log.info("Getting to storage...")
        if not self._abort and not self.nav.navigate_to_storage():
            self._check_pass_setup()
            if self._abort:
                return 0
            self.nav.ensure_pokemon_go()
            time.sleep(3)
            if self._abort:
                return 0
            arrived = self.nav.navigate_to_storage()
            self._check_pass_setup()
            if self._abort:
                return 0
            if not arrived:
                raise Exception("Cannot get to Pokemon storage")

        self._check_pass_setup()
        if self._abort:
            return 0

        # Step 2: Clear old search and enter new filter
        log.info("Entering filter: %s", scan_pass.search_query)
        self._clear_and_search(scan_pass.search_query)

        self._check_pass_setup()
        if self._abort:
            return 0

        # Read how many Pokemon match this filter.  A resume skip is a position
        # offset, not work completed in this run, so the state-machine target is
        # the number of new positions remaining after the skipped prefix.
        filtered_count = self.nav.read_filtered_count_verified()
        self._check_pass_setup()
        if self._abort:
            return 0
        if type(filtered_count) is not int or not 0 <= filtered_count <= 10000:
            raise RuntimeError("Filter count could not be verified; no Pokemon opened")
        self._pass_filtered_count = filtered_count
        if filtered_count == 0:
            if self.skip_first_n:
                raise RuntimeError("Resume skip cannot be applied to a verified empty filter")
            self._pass_expected = 0
            if self.on_pass_count:
                self.on_pass_count(0)
            self._check_pass_setup()
            log.info("Pass '%s' verified empty; no Pokemon opened", scan_pass.name)
            return 0
        resume_skip = max(0, int(self.skip_first_n))
        if resume_skip >= filtered_count:
            raise RuntimeError(
                f"Resume skip {resume_skip} leaves no Pokemon to scan "
                f"(filter count: {filtered_count})"
            )

        pass_expected = filtered_count - resume_skip
        if self.max_per_pass > 0:
            pass_expected = min(pass_expected, self.max_per_pass)
        self._pass_expected = pass_expected
        log.info(
            "Filter matched %d Pokemon; %d new positions after resume/cap",
            filtered_count,
            pass_expected,
        )

        if self.on_pass_count:
            self.on_pass_count(pass_expected)
        self._check_pass_setup()

        # Step 3: Tap the first Pokemon, but only use detail-screen menu
        # coordinates after screen detection confirms that the detail screen
        # actually opened.  A missed grid tap previously made the appraisal
        # taps land on storage and could return the scan to the game map.
        prepared = self._prepare_first_appraisal(scan_pass.search_query)
        self._check_pass_setup()
        if not prepared:
            if self._abort:
                return 0
            raise Exception("Cannot open first Pokemon appraisal")
        if self._abort:
            return 0

        # Step 4: Scan
        sm = IndexingStateMachine(self.adb, self.profile, self.db)
        try:
            self._check_pass_setup()
        except _PassSetupInvalidated:
            sm._close_reader()
            raise
        sm.unfavorite_all = self.unfavorite_all
        # Apply skip for resume (only on first pass)
        if resume_skip > 0:
            sm.skip_first_n = resume_skip
            sm.skip_delay = self.skip_delay
            sm.resume_target_species = self.resume_target_species
            sm.resume_target_cp = self.resume_target_cp
        if self.max_per_pass > 0:
            sm.max_count = self.max_per_pass
        with self._control_lock:
            self._current_sm = sm
            if self._abort:
                sm.abort()
            elif self._paused:
                sm.pause()

        tags = scan_pass.tags

        def on_progress(count, pokemon):
            self._total_count += 1
            if self.on_progress:
                self.on_progress(self._total_count, pokemon)

        sm.on_progress = on_progress
        sm.on_error = self.on_error

        started = False
        try:
            # Stop can arrive while the reader is being constructed, before
            # _current_sm was available to abort(). Transfer that cancellation
            # after publishing the new machine and before any scan input.
            if self._navigation_cancelled():
                self._check_pass_setup()
                return 0
            with self._control_lock:
                self._check_pass_setup()
                if self._abort:
                    return 0
                # The attached state machine owns pause/stop after this
                # boundary. Never restart a traversal that may have stored rows.
                self._setup_generation = None
                if resume_skip > 0:
                    self.skip_first_n = 0
                started = True
            sm.start(expected_total=pass_expected)
        finally:
            if not started:
                sm._close_reader()
            # Do not leave a failed state machine attached while the outer
            # scanner reports the error or the GUI requests Stop.
            with self._control_lock:
                self._current_sm = None
            if started:
                self._total_skipped += sm.skipped_count

            # A fatal OCR/runtime error can happen after valid rows were
            # stored.  Preserve those partial rows with the correct pass tags
            # even though the session remains visibly incomplete.
            if started and tags and sm.count > 0:
                log.info("Tagging %d Pokemon from pass '%s' with %s",
                         sm.count, scan_pass.name, tags)
                self._apply_tags_to_session(sm.session_id, tags)

        if self._abort:
            return sm.count
        if sm.visited_count != pass_expected:
            raise RuntimeError(
                f"Pass incomplete: verified {sm.visited_count} of {pass_expected} positions"
            )
        # After exact coverage, close appraisal and get back to storage.
        log.info("Pass done: session=%s, %d stored, %d visited, %d skipped; returning to storage...",
                 sm.session_id, sm.count, sm.visited_count, sm.skipped_count)
        arrived = self.nav.navigate_to_storage()
        if self._abort:
            return sm.count
        if not arrived:
            raise RuntimeError("Pass ended but storage could not be confirmed")

        return sm.count

    def _clear_and_search(self, query: str):
        """Clear any existing search filter and enter a new one.

        The storage search bar might have an old filter from the previous pass.
        Route through GameNavigator so calibrated tablet targets are used.  The
        former duplicate implementation scaled Fold coordinates directly and
        missed the tablet search bar.
        """
        if not self.nav.enter_search(query, verify=True) and not self._abort:
            raise RuntimeError("Full storage search text could not be verified")

    def _prepare_first_appraisal(self, search_query: str) -> bool:
        """Open and verify the first appraisal before starting OCR."""
        if self._abort:
            return False
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
            return not self._abort

        log.warning(
            "First Pokemon did not reach appraisal (got %s); recovering",
            screen,
        )
        if not self.nav.navigate_to_storage() or self._abort:
            return False
        self._clear_and_search(search_query)
        if self._abort:
            return False
        count = self.nav.read_filtered_count_verified()
        if (self._abort or type(count) is not int or count <= 0
                or count != getattr(self, "_pass_filtered_count", None)):
            return False
        self.nav.tap_first_pokemon()
        if self._abort:
            return False
        screen = self.nav.detect_screen()
        if screen == "detail":
            self.nav.open_first_appraisal()
            if self._abort:
                return False
            screen = self.nav.detect_screen()
        return screen == "appraisal" and not self._abort

    def _apply_tags_to_session(self, session_id: str, tags: dict):
        """Apply tags to ALL Pokemon in a scan session."""
        for field, value in tags.items():
            self.db.conn.execute(
                f"UPDATE pokemon SET {field} = ? WHERE scan_session_id = ?",
                (int(value), session_id),
            )
        self.db.conn.commit()
        log.info("Tagged session %s with %s", session_id, tags)

    def _navigation_cancelled(self):
        """Pause on the scan thread before navigation input; Stop wakes it."""
        while True:
            with self._control_lock:
                if (self._setup_generation is not None
                        and self._setup_generation != self._pause_generation):
                    # Inside setup, unwind the old navigation immediately.
                    # The outer pass waits for resume before acquiring a new
                    # full-query proof, including for short search strings.
                    return True
                if self._abort or not self._paused:
                    return self._abort
            time.sleep(0.05)

    def pause(self):
        with self._control_lock:
            if not self._abort:
                self._pause_generation += 1
                self._paused = True
                if self._current_sm:
                    self._current_sm.pause()

    def resume(self):
        with self._control_lock:
            if not self._abort:
                self._paused = False
                if self._current_sm:
                    self._current_sm.resume()

    def abort(self):
        with self._control_lock:
            self._abort = True
            if self._current_sm:
                self._current_sm.abort()
