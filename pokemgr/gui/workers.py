# TRACEWEAVER: file-role=scan-worker-queue-controls; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
# TRACEWEAVER: entrypoint=ScanWorker.run; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
"""Background workers for ADB operations (run in QThread to keep GUI responsive)."""

import logging
import threading
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

    # TRACEWEAVER: entrypoint=ScanWorker; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001

    progress = Signal(int, object)
    pass_count = Signal(int)
    status = Signal(str)
    finished = Signal(int)
    error = Signal(str)
    failed = Signal(str)
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
        self._control_lock = threading.RLock()
        self._abort_requested = False
        self._pause_requested = False

    def run(self):
        try:
            ensure_dirs()
            with self._control_lock:
                if self._abort_requested:
                    return

            scanner = MultiPassScanner(self.adb, self.profile, self.db)
            with self._control_lock:
                self._scanner = scanner
                if self._abort_requested:
                    scanner.abort()
                    return
                if self._pause_requested:
                    scanner.pause()
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
            if self.selected_passes is not None:
                if not self.selected_passes:
                    raise ValueError("Explicit scan queue is empty; no default passes started")
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

            with self._control_lock:
                if self._abort_requested:
                    return
            # The scanner is already attached: a control request after this
            # check is forwarded before its first navigation boundary.
            self._scanner.start()
            with self._control_lock:
                if not self._abort_requested:
                    self.finished.emit(self._scanner._total_count)

        except Exception as e:
            log.exception("Scan worker error")
            message = str(e)
            self.error.emit(message)
            self.failed.emit(message)

    def pause(self):
        with self._control_lock:
            if self._abort_requested:
                return
            self._pause_requested = True
            if self._scanner is not None:
                self._scanner.pause()

    def resume(self):
        with self._control_lock:
            if self._abort_requested:
                return
            self._pause_requested = False
            if self._scanner is not None:
                self._scanner.resume()

    def abort(self):
        """Force stop — sets abort flag on scanner AND current state machine."""
        with self._control_lock:
            self._abort_requested = True
            self._pause_requested = False
            if self._scanner is not None:
                self._scanner.abort()


class ScanFromCurrentWorker(QThread):
    """Scan from current screen — auto-detects detail or appraisal and starts."""

    progress = Signal(int, object)
    status = Signal(str)
    finished = Signal(int)
    error = Signal(str)
    failed = Signal(str)

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
        self._abort_requested = False

    def run(self):
        try:
            ensure_dirs()
            if self._abort_requested:
                return

            nav = GameNavigator(
                self.adb,
                self.profile.regions,
                cancelled=lambda: self._abort_requested,
            )
            if not nav.clear_touch_protection():
                return
            import time
            time.sleep(0.25)
            if self._abort_requested:
                return
            screen = nav.detect_screen()
            self.status.emit(f"Detected screen: {screen}")

            if screen == 'appraisal':
                pass  # already on appraisal — start scanning
            elif screen == 'detail':
                # Confirmed detail screen — open appraisal.
                self.status.emit("Opening appraisal from detail screen...")
                if not nav.open_first_appraisal():
                    return
                # Verify we got to appraisal
                time.sleep(0.5)
                if self._abort_requested:
                    return
                screen2 = nav.detect_screen()
                if screen2 != 'appraisal':
                    message = (
                        f"Failed to open appraisal (screen: {screen2}). "
                        f"Navigate to a Pokemon detail screen first."
                    )
                    self.error.emit(message)
                    self.failed.emit(message)
                    return
            elif screen in ('game_map', 'storage'):
                self.status.emit("Navigating to the first appraisal...")
                if not nav.navigate_to_appraisal():
                    if self._abort_requested:
                        return
                    message = f"Could not reach appraisal from {screen}."
                    self.error.emit(message)
                    self.failed.emit(message)
                    return
            else:
                message = (
                    f"Not on a Pokemon screen (detected: {screen}). "
                    f"Navigate to a Pokemon first."
                )
                self.error.emit(message)
                self.failed.emit(message)
                return

            self.status.emit("Scanning from current position...")
            if self._abort_requested:
                return
            self._sm = IndexingStateMachine(self.adb, self.profile, self.db)
            self._sm.unfavorite_all = self.unfavorite
            self._sm.on_progress = lambda c, p: self.progress.emit(c, p)
            self._sm.on_error = lambda m: self.error.emit(m)

            if self.max_pokemon > 0:
                self._sm.max_count = self.max_pokemon

            self._sm.start(expected_total=self.max_pokemon or None)
            if not self._abort_requested:
                self.finished.emit(self._sm.count)

        except Exception as e:
            log.exception("Scan worker error")
            message = str(e)
            self.error.emit(message)
            self.failed.emit(message)

    def pause(self):
        if self._sm:
            self._sm.pause()

    def resume(self):
        if self._sm:
            self._sm.resume()

    def abort(self):
        self._abort_requested = True
        if self._sm:
            self._sm.abort()


class _ActionWorker(QThread):
    """Pause/abort survive setup and every exit delivers a terminal result."""

    action_progress = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._executor = None
        self._control_lock = threading.Lock()
        self._abort_requested = False
        self._pause_requested = False

    def _attach_executor(self, executor):
        with self._control_lock:
            self._executor = executor
            aborted = self._abort_requested
            if aborted:
                executor.abort()
            elif self._pause_requested:
                executor.pause()
        if aborted:
            executor._close_reader()
        return not aborted

    def _emit_result(self, result):
        result = dict(result)
        with self._control_lock:
            if self._abort_requested:
                result['aborted'] = True
        self.finished.emit(result)

    def pause(self):
        with self._control_lock:
            self._pause_requested = True
            if self._executor:
                self._executor.pause()

    def resume(self):
        with self._control_lock:
            self._pause_requested = False
            if self._executor:
                self._executor.resume()

    def abort(self):
        with self._control_lock:
            self._abort_requested = True
            self._pause_requested = False
            if self._executor:
                self._executor.abort()


class UnfavoriteWorker(_ActionWorker):
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
        self._abort_requested = False

    def run(self):
        result = {"unfavorited": 0}
        try:
            if self._abort_requested:
                return
            if not self._attach_executor(Executor(self.adb, self.profile, self.db)):
                return
            self._executor.on_progress = lambda cur, tot, msg: self.progress.emit(cur, tot, msg)
            self._executor.on_error = lambda msg: self.error.emit(msg)
            result = self._executor.unfavorite_all()
        except Exception as e:
            log.exception("Unfavorite worker error")
            self.error.emit(str(e))
            result = {"unfavorited": 0, "error": str(e)}
        finally:
            self._emit_result(result)


class FavoriteFilterWorker(_ActionWorker):
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
        self._abort_requested = False

    def run(self):
        db = None
        result = {"favorited": 0}
        try:
            if self._abort_requested:
                return
            from ..execution.executor import Executor
            from ..data.database import PokemonDatabase

            # We need a DB instance but won't do keeper matching
            db = PokemonDatabase()
            if not self._attach_executor(Executor(self.adb, self.profile, db)):
                return
            self._executor.on_progress = lambda cur, tot, msg: self.progress.emit(cur, tot, msg)
            self._executor.on_error = lambda msg: self.error.emit(msg)

            result = self._executor.favorite_by_filter(self.search_query, self.label)
        except Exception as e:
            log.exception("FavoriteFilter worker error")
            self.error.emit(str(e))
            result = {"favorited": 0, "error": str(e)}
        finally:
            try:
                if db is not None:
                    db.close()
            except Exception as exc:
                self.error.emit(str(exc))
                result = {**result, "error": str(exc)}
            self._emit_result(result)


class PvpCleanupWorker(_ActionWorker):
    """Remove stars only for the frozen, reviewed cleanup selection."""

    progress = Signal(int, int, str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, adb, profile, db, reviewed_candidates, *, dry_run=False, parent=None):
        super().__init__(parent)
        from dataclasses import replace
        self.adb, self.profile, self.db = adb, profile, db
        self.reviewed_candidates = tuple(replace(p) for p in reviewed_candidates)
        self.dry_run = dry_run

    def run(self):
        result = {"unfavorited": 0, "checked": 0, "dry_run": self.dry_run}
        try:
            if self._abort_requested:
                return
            if not self._attach_executor(Executor(self.adb, self.profile, self.db)):
                return
            self._executor.on_progress = lambda cur, total, msg: self.progress.emit(cur, total, msg)
            self._executor.on_error = lambda msg: self.error.emit(msg)
            self._executor.on_action_progress = lambda payload: self.action_progress.emit(dict(payload))
            result = self._executor.unfavorite_pvp_candidates(
                self.reviewed_candidates, dry_run=self.dry_run,
            )
        except Exception as exc:
            log.exception("PvP cleanup worker error")
            self.error.emit(str(exc))
            result = {**result, "error": str(exc)}
        finally:
            self._emit_result({**result, "dry_run": self.dry_run})


class FavoriteWorker(_ActionWorker):
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
        self._abort_requested = False

    def run(self):
        result = {"favorited": 0, "checked": 0, "dry_run": self.dry_run}
        try:
            if self._abort_requested:
                return
            if not self._attach_executor(Executor(self.adb, self.profile, self.db)):
                return
            self._executor.on_progress = lambda cur, tot, msg: self.progress.emit(cur, tot, msg)
            self._executor.on_error = lambda msg: self.error.emit(msg)

            self._executor.on_action_progress = lambda payload: self.action_progress.emit(dict(payload))
            result = self._executor.favorite_keepers(
                dry_run=self.dry_run, selected_passes=self.selected_passes
            )

        except Exception as e:
            log.exception("Favorite worker error")
            self.error.emit(str(e))
            result = {"favorited": 0, "error": str(e), "dry_run": self.dry_run}
        finally:
            self._emit_result({**result, "dry_run": self.dry_run})
