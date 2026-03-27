"""Background workers for ADB operations (run in QThread to keep GUI responsive)."""

import logging
from PySide6.QtCore import QThread, Signal

from ..adb.controller import ADBController
from ..adb.navigator import GameNavigator
from ..calibration.profile import CalibrationProfile
from ..data.database import PokemonDatabase
from ..indexer.state_machine import IndexingStateMachine
from ..indexer.multi_pass import MultiPassScanner
from ..reader.screen import PokemonRead
from ..execution.executor import Executor
from ..config import ensure_dirs

log = logging.getLogger(__name__)


class ScanWorker(QThread):
    """Multi-pass scan: runs filtered passes (normal, shiny, shadow, dynamax, gmax)."""

    progress = Signal(int, object)
    pass_count = Signal(int)
    status = Signal(str)
    finished = Signal(int)
    error = Signal(str)
    paused = Signal()  # emitted when scan auto-pauses (resume mismatch etc.)

    def __init__(self, adb: ADBController, profile: CalibrationProfile,
                 db: PokemonDatabase, unfavorite: bool = False,
                 max_pokemon: int = 0, parent=None):
        super().__init__(parent)
        self.adb = adb
        self.profile = profile
        self.db = db
        self.unfavorite = unfavorite
        self.max_pokemon = max_pokemon
        self.selected_passes: list[dict] | None = None  # set by main window
        self._scanner: MultiPassScanner | None = None

    def run(self):
        try:
            ensure_dirs()

            self._scanner = MultiPassScanner(self.adb, self.profile, self.db)
            self._scanner.unfavorite_all = self.unfavorite
            if self.max_pokemon > 0:
                self._scanner.max_per_pass = self.max_pokemon
            self._scanner.skip_first_n = getattr(self, 'skip_first_n', 0)
            self._scanner.skip_delay = getattr(self, 'skip_delay', 0.05)
            self._scanner.resume_target_species = getattr(self, 'resume_target_species', '')
            self._scanner.resume_target_cp = getattr(self, 'resume_target_cp', 0)
            log.info("Skip: %d, delay: %.2fs, target: %s CP%d",
                     self._scanner.skip_first_n, self._scanner.skip_delay,
                     self._scanner.resume_target_species, self._scanner.resume_target_cp)

            # Use selected passes if provided
            if self.selected_passes:
                from ..indexer.multi_pass import ScanPass
                self._scanner.passes = [
                    ScanPass(name=p["name"], search_query=p["query"], tags=p["tags"])
                    for p in self.selected_passes
                ]

            total_passes = len(self._scanner.passes)
            self._scanner.on_pass_start = lambda i, name, q: self.status.emit(
                f"Pass {i+1}/{total_passes}: {name} (filter: {q})"
            )
            self._scanner.on_pass_count = lambda count: self.pass_count.emit(count)
            self._scanner.on_progress = lambda c, p: self.progress.emit(c, p)
            self._scanner.on_pass_end = lambda i, c: self.status.emit(
                f"Pass {i+1} done: {c} Pokemon"
            )
            self._scanner.on_error = lambda m: self.error.emit(m)

            self._scanner.start()
            self.finished.emit(self._scanner._total_count)

        except Exception as e:
            log.exception("Scan worker error")
            self.error.emit(str(e))

    def pause(self):
        if self._scanner and self._scanner._current_sm:
            self._scanner._current_sm.pause()

    def resume(self):
        if self._scanner and self._scanner._current_sm:
            self._scanner._current_sm.resume()

    def abort(self):
        """Force stop — sets abort flag on scanner AND current state machine."""
        if self._scanner:
            self._scanner._abort = True
            self._scanner.abort()
        # Also directly abort the state machine if running
        if self._scanner and self._scanner._current_sm:
            self._scanner._current_sm._abort = True


class ScanFromCurrentWorker(QThread):
    """Scan from current screen — auto-detects detail or appraisal and starts."""

    progress = Signal(int, object)
    status = Signal(str)
    finished = Signal(int)
    error = Signal(str)

    def __init__(self, adb: ADBController, profile: CalibrationProfile,
                 db: PokemonDatabase, unfavorite: bool = False,
                 max_pokemon: int = 0, parent=None):
        super().__init__(parent)
        self.adb = adb
        self.profile = profile
        self.db = db
        self.unfavorite = unfavorite
        self.max_pokemon = max_pokemon
        self._sm: IndexingStateMachine | None = None

    def run(self):
        try:
            ensure_dirs()

            nav = GameNavigator(self.adb, self.profile.regions)
            screen = nav.detect_screen()
            self.status.emit(f"Detected screen: {screen}")

            if screen == 'appraisal':
                pass  # already on appraisal — start scanning
            elif screen in ('other', 'detail'):
                # Likely on detail screen — try to open appraisal
                self.status.emit("Opening appraisal from detail screen...")
                nav.open_first_appraisal()
                # Verify we got to appraisal
                import time
                time.sleep(0.5)
                screen2 = nav.detect_screen()
                if screen2 != 'appraisal':
                    self.error.emit(
                        f"Failed to open appraisal (screen: {screen2}). "
                        f"Navigate to a Pokemon detail screen first."
                    )
                    self.finished.emit(0)
                    return
            else:
                self.error.emit(
                    f"Not on a Pokemon screen (detected: {screen}). "
                    f"Navigate to a Pokemon first."
                )
                self.finished.emit(0)
                return

            self.status.emit("Scanning from current position...")
            self._sm = IndexingStateMachine(self.adb, self.profile, self.db)
            self._sm.unfavorite_all = self.unfavorite
            self._sm.on_progress = lambda c, p: self.progress.emit(c, p)
            self._sm.on_error = lambda m: self.error.emit(m)

            if self.max_pokemon > 0:
                self._sm.max_count = self.max_pokemon

            self._sm.start(expected_total=self.max_pokemon or None)
            self.finished.emit(self._sm.count)

        except Exception as e:
            log.exception("Scan worker error")
            self.error.emit(str(e))

    def pause(self):
        if self._sm:
            self._sm.pause()

    def resume(self):
        if self._sm:
            self._sm.resume()

    def abort(self):
        if self._sm:
            self._sm.abort()


class UnfavoriteWorker(QThread):
    """Unfavorites all Pokemon in the background."""

    progress = Signal(int, int, str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, adb: ADBController, profile: CalibrationProfile,
                 db: PokemonDatabase, parent=None):
        super().__init__(parent)
        self.adb = adb
        self.profile = profile
        self.db = db
        self._executor: Executor | None = None

    def run(self):
        try:
            self._executor = Executor(self.adb, self.profile, self.db)
            self._executor.on_progress = lambda cur, tot, msg: self.progress.emit(cur, tot, msg)
            result = self._executor.unfavorite_all()
            self.finished.emit(result)
        except Exception as e:
            log.exception("Unfavorite worker error")
            self.error.emit(str(e))
            self.finished.emit({"unfavorited": 0, "error": str(e)})

    def abort(self):
        if self._executor:
            self._executor.abort()


class FavoriteFilterWorker(QThread):
    """Favorites all Pokemon matching a search filter (swipe through + star all)."""

    progress = Signal(int, int, str)   # (current, total, message)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, adb: ADBController, profile: CalibrationProfile,
                 search_query: str, label: str, parent=None):
        super().__init__(parent)
        self.adb = adb
        self.profile = profile
        self.search_query = search_query
        self.label = label
        self._executor = None

    def run(self):
        try:
            from ..execution.executor import Executor
            from ..data.database import PokemonDatabase

            # We need a DB instance but won't do keeper matching
            db = PokemonDatabase()
            self._executor = Executor(self.adb, self.profile, db)
            self._executor.on_progress = lambda cur, tot, msg: self.progress.emit(cur, tot, msg)
            self._executor.on_error = lambda msg: self.error.emit(msg)

            result = self._executor.favorite_by_filter(self.search_query, self.label)
            self.finished.emit(result)
        except Exception as e:
            log.exception("FavoriteFilter worker error")
            self.error.emit(str(e))
            self.finished.emit({"favorited": 0, "error": str(e)})

    def pause(self):
        if self._executor:
            self._executor.pause()

    def resume(self):
        if self._executor:
            self._executor.resume()

    def abort(self):
        if self._executor:
            self._executor.abort()


class FavoriteWorker(QThread):
    """Favorites all KEEP Pokemon in the background."""

    progress = Signal(int, int, str)   # (current, total, message)
    finished = Signal(dict)            # result dict
    error = Signal(str)

    def __init__(self, adb: ADBController, profile: CalibrationProfile,
                 db: PokemonDatabase, dry_run: bool = False,
                 selected_passes: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.adb = adb
        self.profile = profile
        self.db = db
        self.dry_run = dry_run
        self.selected_passes = selected_passes
        self._executor: Executor | None = None

    def run(self):
        try:
            self._executor = Executor(self.adb, self.profile, self.db)
            self._executor.on_progress = lambda cur, tot, msg: self.progress.emit(cur, tot, msg)
            self._executor.on_error = lambda msg: self.error.emit(msg)

            result = self._executor.favorite_keepers(
                dry_run=self.dry_run, selected_passes=self.selected_passes
            )
            self.finished.emit(result)

        except Exception as e:
            log.exception("Favorite worker error")
            self.error.emit(str(e))
            self.finished.emit({"favorited": 0, "error": str(e)})

    def pause(self):
        if self._executor:
            self._executor.pause()

    def resume(self):
        if self._executor:
            self._executor.resume()

    def abort(self):
        if self._executor:
            self._executor.abort()
