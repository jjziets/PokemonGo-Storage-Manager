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
        self.nav = GameNavigator(adb, profile.regions)
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
        self._abort = False
        self._current_sm: IndexingStateMachine | None = None

    def start(self):
        from ..scan_logger import start_scan_log, stop_scan_log
        log_path = start_scan_log()
        log.info("Multi-pass scan: %d passes (log: %s)", len(self.passes), log_path)
        self._total_count = 0

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
                log.error("Pass %s failed: %s", scan_pass.name, e)
                if self.on_error:
                    self.on_error(f"Pass {scan_pass.name} failed: {e}")
                pass_count = 0

            if self.on_pass_end:
                self.on_pass_end(i, pass_count)

        log.info("Done: %d total Pokemon, %d skipped", self._total_count, self._total_skipped)
        from ..scan_logger import stop_scan_log
        stop_scan_log()
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

        if self._abort:
            return 0

        # Step 2: Clear old search and enter new filter
        log.info("Entering filter: %s", scan_pass.search_query)
        self._clear_and_search(scan_pass.search_query)

        if self._abort:
            return 0

        # Read how many Pokemon match this filter
        pass_expected = self.nav.read_filtered_count()
        if self.max_per_pass > 0 and pass_expected > 0:
            pass_expected = min(pass_expected, self.max_per_pass)
        self._pass_expected = pass_expected
        log.info("Filter matched %d Pokemon", pass_expected)

        if self.on_pass_count:
            self.on_pass_count(pass_expected)

        # Don't skip on 0 — OCR might have failed to read the count
        # The scan will stop naturally if there are truly no results (end-of-list detection)
        if pass_expected == 0:
            log.warning("Could not read filter count — proceeding anyway")

        # Step 3: Tap first Pokemon and open appraisal
        self.nav.tap_first_pokemon()
        if self._abort:
            return 0

        self.nav.open_first_appraisal()
        if self._abort:
            return 0

        # Step 4: Scan
        sm = IndexingStateMachine(self.adb, self.profile, self.db)
        sm.unfavorite_all = self.unfavorite_all
        # Apply skip for resume (only on first pass)
        if self.skip_first_n > 0:
            sm.skip_first_n = self.skip_first_n
            sm.skip_delay = self.skip_delay
            sm.resume_target_species = self.resume_target_species
            sm.resume_target_cp = self.resume_target_cp
            self.skip_first_n = 0  # only skip on first pass
        if self.max_per_pass > 0:
            sm.max_count = self.max_per_pass
        elif pass_expected > 0:
            # Use OCR count + 5% as safety limit (in case OCR was slightly off)
            sm.max_count = int(pass_expected * 1.05) + 10
        self._current_sm = sm

        tags = scan_pass.tags

        def on_progress(count, pokemon):
            self._total_count += 1
            if self.on_progress:
                self.on_progress(self._total_count, pokemon)

        sm.on_progress = on_progress
        sm.on_error = self.on_error

        sm.start()
        self._total_skipped += sm.skipped_count
        self._current_sm = None

        # Apply tags to ALL Pokemon from this pass's session
        if tags and sm.count > 0:
            log.info("Tagging %d Pokemon from pass '%s' with %s",
                     sm.count, scan_pass.name, tags)
            self._apply_tags_to_session(sm.session_id, tags)

        # After scan ends, close appraisal and get back to storage
        log.info("Pass done (%d scanned), returning to storage...", sm.count)
        # Tap X to close appraisal, then back to storage
        self.adb.tap(*self.nav._s(484, 2260), jitter=3)  # X button
        time.sleep(1)
        self.adb.key_event(4)  # back from detail to storage
        time.sleep(1)

        return sm.count

    def _clear_and_search(self, query: str):
        """Clear any existing search filter and enter a new one.

        The storage search bar might have an old filter from the previous pass.
        We need to: tap the X button to clear → tap search → type new query → enter.
        """
        r = self.nav.r
        w, h = r.screen_width, r.screen_height
        sx = w / 968
        sy = h / 2376

        # First check if search is active (the search bar shows text + X button)
        # The X clear button is at the right end of the search bar
        # Tap it to clear the filter
        x_btn_x = int(920 * sx)
        x_btn_y = int(300 * sy)
        log.info("Clearing search filter...")
        self.adb.tap(x_btn_x, x_btn_y, jitter=3)
        human_delay(0.5, 0.1)

        # Tap the search bar if the X didn't open it
        # (if no search was active, tapping the X area does nothing,
        #  so tap the search bar proper)
        search_x = int(484 * sx)
        search_y = int(300 * sy)
        self.adb.tap(search_x, search_y, jitter=3)
        human_delay(1.0, 0.2)

        # Clear any remaining text
        self.adb.shell("input keyevent 123")  # MOVE_END
        time.sleep(0.1)
        for _ in range(40):
            self.adb.shell("input keyevent 67")  # DEL
        time.sleep(0.2)

        # Type the new query
        self.adb.input_text(query)
        human_delay(0.5, 0.1)

        # Apply
        self.adb.key_event(66)  # ENTER
        human_delay(2.0, 0.5)

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
