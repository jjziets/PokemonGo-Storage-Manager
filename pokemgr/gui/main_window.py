# TRACEWEAVER: file-role=inventory-queue-gui; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
# TRACEWEAVER: entrypoint=start_scan_queue; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
"""Main application window with tabbed interface."""

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QMessageBox, QStatusBar, QFileDialog, QLabel, QApplication,
)
from PySide6.QtCore import Slot

from ..adb.controller import ADBController, ADBError
from ..calibration.profile import CalibrationProfile
from ..data.database import PokemonDatabase
from ..decision.engine import DecisionEngine
from ..config import ensure_dirs

from .widgets.scan_control import ScanControl
from .widgets.collection_browser import CollectionBrowser
from .widgets.decision_review import DecisionReview
from .workers import ScanWorker, ScanFromCurrentWorker

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Pokemon Go Storage Manager — main window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pokemon Go Storage Manager")
        self.setMinimumSize(1200, 800)
        self.resize(1400, 900)
        # Force window to center of screen
        screen = self.screen().availableGeometry()
        self.move(
            (screen.width() - 1400) // 2,
            (screen.height() - 900) // 2,
        )
        self.raise_()
        self.activateWindow()

        ensure_dirs()

        self.adb: ADBController | None = None
        self.profile: CalibrationProfile | None = None
        self.db = PokemonDatabase()
        self._scan_worker: ScanWorker | ScanFromCurrentWorker | None = None
        self._mass_worker = None
        self._last_session_id: str | None = None

        self._setup_ui()
        self._connect_signals()

        # Battery refresh timer
        from PySide6.QtCore import QTimer
        self._battery_timer = QTimer(self)
        self._battery_timer.timeout.connect(self._refresh_battery)
        self._battery_timer.start(5000)  # every 5 seconds

        from .resource_monitor import ResourceMonitor
        self._resource_label = QLabel("App + helpers: measuring resources…")
        self._resource_label.setObjectName("processResourceUsage")
        self.statusBar().addPermanentWidget(self._resource_label)
        self._resource_monitor = ResourceMonitor(self)
        self._resource_monitor.updated.connect(self._update_resource_usage)
        QApplication.instance().aboutToQuit.connect(self._resource_monitor.stop)
        self._resource_monitor.start()

    @Slot(object)
    def _update_resource_usage(self, snapshot):
        from .resource_monitor import resource_text
        summary, detail = resource_text(snapshot)
        self._resource_label.setText(summary)
        self._resource_label.setToolTip(detail)

    def _setup_ui(self):
        # Tabs
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # Tab 1: Scan Control
        self.scan_tab = ScanControl()
        self.tabs.addTab(self.scan_tab, "Scan")

        # Tab 2: Collection Browser
        self.collection_tab = CollectionBrowser()
        self.tabs.addTab(self.collection_tab, "Collection")

        # Tab 3: Decision Review
        self.decision_tab = DecisionReview()
        self.tabs.addTab(self.decision_tab, "Decisions")

        # Tab 4: Mass Actions
        from .widgets.mass_actions import MassActions
        self.mass_tab = MassActions()
        self.tabs.addTab(self.mass_tab, "Mass Actions")

        # Status bar
        self.statusBar().showMessage("Ready — click Connect to start")

    def _connect_signals(self):
        # Scan tab
        self.scan_tab.connect_btn.clicked.connect(self._connect_device)
        self.scan_tab.phone_screen_on_btn.clicked.connect(self._turn_phone_screen_on)
        self.scan_tab.phone_screen_off_btn.clicked.connect(self._turn_phone_screen_off)
        self.scan_tab.clear_db_btn.clicked.connect(self._clear_database)
        self.scan_tab.start_full_btn.clicked.connect(self._start_full_scan)
        self.scan_tab.start_current_btn.clicked.connect(self._start_current_scan)
        self.scan_tab.pause_btn.clicked.connect(self._toggle_pause)
        self.scan_tab.abort_btn.clicked.connect(self._abort_scan)

        # Decision tab
        self.decision_tab.run_engine_btn.clicked.connect(self._run_decision_engine)
        self.decision_tab.approve_btn.clicked.connect(lambda: self._start_favorite(dry_run=True))
        self.decision_tab.favorite_real_btn.clicked.connect(lambda: self._start_favorite(dry_run=False))
        self.decision_tab.unfavorite_btn.clicked.connect(self._unfavorite_all)
        self.decision_tab.pvp_cleanup_btn.clicked.connect(lambda: self._review_pvp_cleanup())
        self.decision_tab.fav_pause_btn.clicked.connect(self._toggle_fav_pause)
        self.decision_tab.fav_stop_btn.clicked.connect(self._stop_favorite)
        self.decision_tab.decision_requested.connect(self._save_manual_decision)

        # Collection tab
        self.collection_tab.dedup_btn.clicked.connect(self._remove_duplicates)

        # Mass Actions tab
        self.mass_tab.unfav_all_btn.clicked.connect(lambda: self._unfavorite_all(from_mass=True))
        self.mass_tab.pvp_cleanup_btn.clicked.connect(lambda: self._review_pvp_cleanup(from_mass=True))
        self.mass_tab.favorite_filter.connect(self._start_favorite_filter)
        self.mass_tab.fav_keepers_dry_btn.clicked.connect(lambda: self._start_favorite(dry_run=True, from_mass=True))
        self.mass_tab.fav_keepers_real_btn.clicked.connect(lambda: self._start_favorite(dry_run=False, from_mass=True))
        self.mass_tab.stop_action.connect(self._stop_mass_action)
        self.mass_tab.pause_action.connect(self._toggle_mass_pause)

        # Tab change — refresh data
        self.tabs.currentChanged.connect(self._on_tab_changed)

    # ── Device connection ────────────────────────────────────────────

    @staticmethod
    def _running_device_workers(window):
        workers = []
        for name in ("_scan_worker", "_fav_worker", "_unfav_worker", "_mass_worker"):
            worker = getattr(window, name, None)
            if (worker is not None and all(worker is not other for other in workers)
                    and worker.isRunning()):
                workers.append(worker)
        return workers

    @Slot()
    def _connect_device(self, *, show_errors: bool = True,
                        allow_default_profile: bool = True):
        if MainWindow._running_device_workers(self):
            self.scan_tab.on_error("Stop the current operation before reconnecting the device")
            return False
        self.scan_tab.set_swipe_duration(None)
        self.scan_tab.phone_screen_on_btn.setEnabled(False)
        self.scan_tab.phone_screen_off_btn.setEnabled(False)
        try:
            previous_adb = getattr(self, "adb", None)
            if previous_adb is not None:
                previous_adb.close_stream_capture()
            self.adb = None
            self.adb = ADBController()
            self.adb.connect()
            info = self.adb.get_device_info()
            battery = self.adb.get_battery_level()

            stream_label = " — App-only stream" if self.adb.display_id is not None else ""
            self.scan_tab.device_label.setText(
                f"{info.model} ({info.resolution}){stream_label} — Battery: {battery}%"
            )
            self.scan_tab.device_label.setStyleSheet("font-size: 14px; color: #8f8;")

            # Load or create calibration
            self.profile = CalibrationProfile.find_for_device(info)
            if not self.profile:
                if not allow_default_profile:
                    raise ADBError("No saved calibration for this display; calibrate before starting a scan")
                self.profile = CalibrationProfile.create_default(info)
                self.profile.save()

            self.scan_tab.set_swipe_duration(self.profile.regions.swipe_duration_ms)

            self.statusBar().showMessage(f"Connected: {info.model}")
            self.scan_tab.start_full_btn.setEnabled(True)
            self.scan_tab.start_current_btn.setEnabled(True)
            self.scan_tab.phone_screen_on_btn.setEnabled(True)
            self.scan_tab.phone_screen_off_btn.setEnabled(self.adb.display_id is not None)

            # Load existing data if any
            self._refresh_collection()
            return True

        except Exception as e:
            if self.adb is not None:
                self.adb.close_stream_capture()
            self.adb = None
            self.profile = None
            self.scan_tab.set_swipe_duration(None)
            self.scan_tab.start_full_btn.setEnabled(False)
            self.scan_tab.start_current_btn.setEnabled(False)
            log.exception("Device connection failed")
            self.scan_tab.on_error(f"Connection failed: {e}")
            if show_errors:
                QMessageBox.critical(self, "Connection Failed", str(e))
            return False

    @Slot()
    def _turn_phone_screen_on(self):
        if self.adb is None:
            self.scan_tab.phone_screen_on_btn.setEnabled(False)
            return
        try:
            self.adb.turn_phone_screen_on()
            self.statusBar().showMessage("Phone screen-on request sent", 5000)
        except ADBError as exc:
            QMessageBox.critical(self, "Phone Screen", str(exc))

    @Slot()
    def _turn_phone_screen_off(self):
        if self.adb is None or self.adb.display_id is None:
            self.scan_tab.phone_screen_off_btn.setEnabled(False)
            return
        try:
            self.adb.turn_phone_screen_off()
            self.statusBar().showMessage("Phone screen-off request sent", 5000)
        except ADBError as exc:
            QMessageBox.critical(self, "Phone Screen", str(exc))

    @Slot()
    def _clear_database(self):
        if MainWindow._running_device_workers(self):
            QMessageBox.warning(self, "Operation Running", "Stop the current operation before clearing the database.")
            return
        reply = QMessageBox.warning(
            self, "Clear Database",
            "This will DELETE all scanned Pokemon data.\n\nAre you sure?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            # The modal dialog processes events; a worker may have started
            # while the confirmation was open and still owns this connection.
            if MainWindow._running_device_workers(self):
                QMessageBox.warning(self, "Operation Running", "Stop the current operation before clearing the database.")
                return
            try:
                self.db.clear_scanned_data()
            except Exception as exc:
                log.exception("Database clear failed")
                self.scan_tab.status_label.setText("Database clear failed")
                self.statusBar().showMessage("Database clear failed")
                QMessageBox.critical(self, "Database Clear Failed", str(exc))
                return
            self._last_session_id = None
            try:
                # These rows were verified empty by the committed transaction.
                # Unlike ordinary refresh helpers, do not swallow view errors.
                self.collection_tab.load_pokemon([])
                self.decision_tab.load_pokemon([])
            except Exception as exc:
                log.exception("Database cleared but display refresh failed")
                message = "Database cleared, but the display could not be refreshed"
                self.scan_tab.status_label.setText(message)
                self.statusBar().showMessage(message)
                QMessageBox.critical(self, "Display Refresh Failed", f"{message}.\n\n{exc}")
                return
            self.scan_tab.log_view.clear()
            self.scan_tab.progress_bar.setValue(0)
            self.scan_tab.status_label.setText("Database cleared")
            self.statusBar().showMessage("Database cleared — ready for fresh scan")

    # ── Scanning ─────────────────────────────────────────────────────

    # TRACEWEAVER: entrypoint=start_scan_queue; req=REQ-SCAN-003,REQ-DATA-001; trace=TRACE-SCAN-003,TRACE-DATA-001; ver=VER-SCAN-001
    def start_scan_queue(self, ledger_path):
        """Launch explicitly reconciled partitions using the controllable scan worker."""
        from ..indexer.scan_queue import load_scan_queue

        if MainWindow._running_device_workers(self):
            self.scan_tab.on_error("Stop the current operation before starting an inventory queue")
            return
        try:
            selected_passes = load_scan_queue(ledger_path)
        except ValueError as exc:
            self.scan_tab.on_error(f"Scan queue not started: {exc}")
            return
        if not self._connect_device(show_errors=False, allow_default_profile=False):
            return
        if MainWindow._running_device_workers(self):
            self.scan_tab.on_error("An operation started while connecting; inventory queue held")
            return
        try:
            if load_scan_queue(ledger_path) != selected_passes:
                raise ValueError("inventory queue changed while connecting; review it before retrying")
        except ValueError as exc:
            self.scan_tab.on_error(f"Scan queue not started: {exc}")
            return
        tab = self.scan_tab
        tab.max_pokemon_spin.setValue(0)
        tab.skip_spin.setValue(0)
        tab.resume_species.setText("")
        tab.resume_cp.setValue(0)
        tab.unfavorite_check.setChecked(False)
        self._scan_worker = ScanWorker(self.adb, self.profile, self.db,
                                       unfavorite=False, max_pokemon=0)
        self._scan_worker.selected_passes = selected_passes
        self._scan_worker.skip_first_n = 0
        self._scan_worker.skip_delay = tab.skip_delay_spin.value()
        self._scan_worker.resume_target_species = ""
        self._scan_worker.resume_target_cp = 0
        log.info("Starting explicit inventory queue from %s: %s", ledger_path,
                 [(entry["name"], entry["query"]) for entry in selected_passes])
        self._start_scan_worker()

    def start_default_scan(self, *, skip_first: int = 0,
                           resume_species: str = "", resume_cp: int = 0):
        """Run an explicitly requested CLI scan in the normal controllable UI."""
        if self._scan_worker is not None and self._scan_worker.isRunning():
            return
        if not self._connect_device(show_errors=False, allow_default_profile=False):
            return
        tab = self.scan_tab
        for control in (tab.pass_normal, tab.pass_shiny, tab.pass_shadow,
                        tab.pass_dynamax, tab.pass_gigantamax):
            control.setChecked(True)
        tab.pass_lucky.setChecked(False)
        tab.pass_custom.setChecked(False)
        tab.max_pokemon_spin.setValue(0)
        tab.skip_spin.setValue(skip_first)
        tab.resume_species.setText(resume_species)
        tab.resume_cp.setValue(resume_cp)
        tab.unfavorite_check.setChecked(False)
        log.info("Starting five-pass scan requested by --start-scan: skip=%d, target=%s CP%d",
                 skip_first, resume_species, resume_cp)
        self._start_full_scan(confirm=False)

    @Slot()
    def _start_full_scan(self, *, confirm: bool = True):
        if MainWindow._running_device_workers(self):
            QMessageBox.warning(self, "Operation Running", "Stop the current operation before starting another.")
            return
        if not self.adb or not self.profile:
            QMessageBox.warning(self, "Not Connected", "Connect to a device first.")
            return

        selected_passes = self.scan_tab.get_selected_passes()
        if not selected_passes:
            QMessageBox.warning(self, "No Passes Selected", "Select at least one scan pass.")
            return

        max_pokemon = self.scan_tab.max_pokemon_spin.value()
        unfavorite = self.scan_tab.unfavorite_check.isChecked()

        if unfavorite:
            reply = QMessageBox.question(
                self, "Confirm Unfavorite",
                "This will REMOVE the favorite star from all Pokemon during scanning.\n"
                "Are you sure?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        # Show summary
        pass_names = [p["name"] for p in selected_passes]
        if confirm:
            reply = QMessageBox.question(
                self, "Start Multi-Pass Scan",
                f"Will run {len(selected_passes)} passes:\n"
                + "\n".join(f"  {i+1}. {p['name']} ({p['query']})" for i, p in enumerate(selected_passes))
                + f"\n\nMax per pass: {'unlimited' if max_pokemon == 0 else max_pokemon}"
                + f"\nUnfavorite: {'Yes' if unfavorite else 'No'}"
                + "\n\nStart scanning?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        self._scan_worker = ScanWorker(
            self.adb, self.profile, self.db,
            unfavorite=unfavorite,
            max_pokemon=max_pokemon,
        )
        self._scan_worker.selected_passes = selected_passes
        skip_n = self.scan_tab.skip_spin.value()
        self._scan_worker.skip_first_n = skip_n
        self._scan_worker.skip_delay = self.scan_tab.skip_delay_spin.value()

        # Pass resume target through the worker
        self._scan_worker.resume_target_species = self.scan_tab.resume_species.text().strip()
        self._scan_worker.resume_target_cp = self.scan_tab.resume_cp.value()

        # Extra confirmation when resuming
        if skip_n > 0 and confirm:
            target_str = ""
            rs = self.scan_tab.resume_species.text().strip()
            rc = self.scan_tab.resume_cp.value()
            if rs:
                target_str = f"\nTarget: {rs}"
                if rc > 0:
                    target_str += f" CP{rc}"
            reply2 = QMessageBox.question(
                self, "Resume Scan",
                f"Will fast-swipe past {skip_n} Pokemon before scanning.{target_str}\n\n"
                f"Skip delay: {self.scan_tab.skip_delay_spin.value()}s per swipe\n"
                f"Estimated skip time: ~{skip_n * (self.scan_tab.skip_delay_spin.value() + 0.1):.0f}s\n\n"
                f"Continue?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply2 != QMessageBox.Yes:
                return

        self._start_scan_worker()

    @Slot()
    def _start_current_scan(self):
        if MainWindow._running_device_workers(self):
            QMessageBox.warning(self, "Operation Running", "Stop the current operation before starting another.")
            return
        if not self.adb or not self.profile:
            QMessageBox.warning(self, "Not Connected", "Connect to a device first.")
            return

        max_pokemon = self.scan_tab.max_pokemon_spin.value()
        unfavorite = self.scan_tab.unfavorite_check.isChecked()

        self._scan_worker = ScanFromCurrentWorker(
            self.adb, self.profile, self.db,
            unfavorite=unfavorite,
            max_pokemon=max_pokemon,
        )
        self._start_scan_worker()

    def _apply_speed_settings(self):
        """Apply GUI speed settings to the config module.
        Also starts a timer to keep updating live while scanning.
        """
        self._sync_speed_to_config()
        # Start a timer to sync settings live every 500ms
        if not hasattr(self, '_speed_timer'):
            from PySide6.QtCore import QTimer
            self._speed_timer = QTimer(self)
            self._speed_timer.timeout.connect(self._sync_speed_to_config)
        self._speed_timer.start(500)

    def _sync_speed_to_config(self):
        """Push current GUI speed values to config (called live during scan)."""
        from .. import config
        config.STABLE_FRAME_INTERVAL = (self.scan_tab.frame_interval_spin.value(), 0.10)
        config.ANTI_DETECTION_ENABLED = self.scan_tab.anti_detection_check.isChecked()
        config.USE_CALCULATED_CP = self.scan_tab.calc_cp_check.isChecked()
        config.USE_CP_ANIMATION_RECOVERY = self.scan_tab.cp_animation_check.isChecked()
        config.CAPTURE_SIZE_TAGS = self.scan_tab.size_tags_check.isChecked()

    def _start_scan_worker(self):
        profile = getattr(self, "profile", None)
        self.scan_tab.set_swipe_duration(
            getattr(getattr(profile, "regions", None), "swipe_duration_ms", None),
        )
        if self.adb is not None and self.adb.display_id is not None:
            try:
                self.adb.turn_phone_screen_off()
            except ADBError as exc:
                message = f"Scan not started: could not turn off the phone screen. {exc}"
                self.scan_tab.on_error(message)
                QMessageBox.critical(self, "Phone Screen", message)
                return
        self._apply_speed_settings()
        worker = self._scan_worker

        worker.progress.connect(self.scan_tab.on_progress)
        worker.status.connect(self.scan_tab.on_status)
        worker.error.connect(self.scan_tab.on_error)
        worker.finished.connect(self._on_scan_finished)
        if hasattr(worker, 'failed'):
            worker.failed.connect(self._on_scan_failed)
        if hasattr(worker, 'paused'):
            worker.paused.connect(lambda: self.scan_tab.pause_btn.setText("Resume"))
        if hasattr(worker, 'pass_count'):
            worker.pass_count.connect(self.scan_tab.on_pass_count)

        self.scan_tab.set_scanning(True)
        self.scan_tab.log_view.clear()
        self.scan_tab.progress_bar.setValue(0)
        self.scan_tab.status_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #8cf;")

        if hasattr(worker, '_sm') and worker._sm:
            self._last_session_id = worker._sm.session_id

        worker.start()

    @Slot()
    def _toggle_pause(self):
        if self._scan_worker:
            if self.scan_tab.pause_btn.text() == "Pause":
                self._scan_worker.pause()
                self.scan_tab.pause_btn.setText("Resume")
                self.scan_tab.status_label.setText("Paused")
            else:
                self._scan_worker.resume()
                self.scan_tab.pause_btn.setText("Pause")
                self.scan_tab.status_label.setText("Scanning...")

    @Slot()
    def _abort_scan(self):
        worker = self._scan_worker
        if worker:
            worker.abort()
            if (self.adb is not None
                    and getattr(self.adb, "has_stream_frames", False) is True):
                # Mark an epoch without closing pipes/mmaps used by the worker.
                self.adb.invalidate_stream_frames()
            self.scan_tab.status_label.setText("Stopping...")
            self.scan_tab.pause_btn.setEnabled(False)
            self.scan_tab.abort_btn.setEnabled(False)
            # Native OCR/capture owns locks and external resources. Let bounded
            # I/O unwind and its finally blocks run instead of killing QThread.
            from PySide6.QtCore import QTimer
            QTimer.singleShot(250, lambda: self._force_stop_scan(worker))

    def _force_stop_scan(self, worker=None):
        """Poll cooperative shutdown without blocking or terminating a worker."""
        worker = self._scan_worker if worker is None else worker
        if worker is None or worker is not self._scan_worker:
            return  # A delayed callback must never affect a newer scan.
        if worker.isRunning():
            self.scan_tab.status_label.setText("Stopping...")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(250, lambda: self._force_stop_scan(worker))
            return
        self.scan_tab.set_scanning(False)
        self.scan_tab.status_label.setText("Stopped")
        self._refresh_collection()
        if hasattr(self, '_speed_timer'):
            self._speed_timer.stop()

    @Slot(int)
    def _on_scan_finished(self, count: int):
        self.scan_tab.on_finished(count)
        self._refresh_collection()
        self.statusBar().showMessage(f"Scan complete: {count} Pokemon indexed")
        if hasattr(self, '_speed_timer'):
            self._speed_timer.stop()

    @Slot(str)
    def _on_scan_failed(self, message: str):
        self.scan_tab.set_scanning(False)
        self.scan_tab.status_label.setText(f"Scan stopped: {message}")
        self.scan_tab.status_label.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: #f66;"
        )
        self._refresh_collection()
        self.statusBar().showMessage(f"Scan stopped: {message}")
        if hasattr(self, '_speed_timer'):
            self._speed_timer.stop()

    # ── Decision engine ──────────────────────────────────────────────

    @Slot(int, str)
    def _save_manual_decision(self, pokemon_id, decision):
        if MainWindow._running_device_workers(self):
            QMessageBox.warning(self, "Operation Running", "Stop the current operation before changing decisions.")
            return
        if type(pokemon_id) is not int or pokemon_id <= 0 or decision not in ("KEEP", "TRANSFER"):
            return
        try:
            self.db.set_manual_decision(pokemon_id, decision)
        except Exception as exc:
            QMessageBox.critical(self, "Decision Not Saved", str(exc))
            return
        self._refresh_collection()
        self._refresh_decisions()

    @Slot()
    def _run_decision_engine(self):
        if MainWindow._running_device_workers(self):
            QMessageBox.warning(self, "Operation Running", "Stop the current operation before changing decisions.")
            return
        pvp_db = None
        try:
            # Try to load PvP rankings if available
            pvp_db = None
            pvp_path = Path("data/pvp_rankings.db")
            if pvp_path.exists():
                from ..pvp.rankings_db import PvPRankingsDB
                pvp_db = PvPRankingsDB()

            enabled_rules = self.decision_tab.get_enabled_rules()
            engine = DecisionEngine(self.db, pvp_db, enabled_rules=enabled_rules)
            result = engine.run()

            QMessageBox.information(
                self, "Decision Engine Complete",
                f"Rules: {', '.join(enabled_rules)}\n\n"
                f"KEEP: {result['keep']}\nTRANSFER: {result['transfer']}",
            )

            self._refresh_collection()
            self._refresh_decisions()
            self.tabs.setCurrentIndex(2)  # switch to Decisions tab

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
        finally:
            if pvp_db is not None:
                pvp_db.close()

    # TRACEWEAVER: entrypoint=_start_favorite; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
    def _start_favorite(self, dry_run: bool = False, *, from_mass: bool = False):
        if MainWindow._running_device_workers(self):
            QMessageBox.warning(self, "Operation Running", "Stop the current operation before starting another.")
            return
        if not self.adb or not self.profile:
            QMessageBox.warning(self, "Not Connected", "Connect to a device first.")
            return

        from ..execution.executor import Executor
        plan = Executor.plan_keeper_favorites(self.db.get_all())

        if not plan.unstarred:
            QMessageBox.information(
                self, "No Unstarred Keepers",
                f"KEEP records: {plan.total:,}\n"
                f"Already favorited (recorded): {plan.already_favorited:,}\n\n"
                "No unstarred keeper records to match. Phone state has not been checked.",
            )
            return

        selected_passes = self.decision_tab.get_selected_fav_passes()
        if not selected_passes:
            QMessageBox.warning(self, "No Passes", "Select at least one pass in Decisions.")
            return
        mode = "DRY RUN (test)" if dry_run else "FOR REAL"
        reply = QMessageBox.question(
            self, f"Favorite Keepers — {mode}",
            f"KEEP records: {plan.total:,}\n"
            f"Already favorited (recorded): {plan.already_favorited:,}\n"
            f"Unstarred (recorded): {plan.unstarred:,}\n\n"
            f"Of the unstarred records:\n"
            f"Eligible before pass/live checks: {plan.eligible:,}\n"
            f"Held for review: {plan.ambiguous:,}\n\n"
            f"Passes: {', '.join(selected_passes)}\n\n"
            f"Searches pending keeper CP values and excludes existing favorites on the phone.\n"
            f"Only selected-pass matches with validated species/form, CP, HP and IVs qualify; "
            f"names may be nicknames. Recorded favorites may differ from the phone.\n\n"
            f"{'No stars will be tapped.' if dry_run else 'Will tap the star only on confirmed unstarred matches.'}\n\n"
            f"Start?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        from .workers import FavoriteWorker

        if MainWindow._running_device_workers(self):
            QMessageBox.warning(self, "Operation Running", "Stop the current operation before starting another.")
            return

        worker = FavoriteWorker(
            self.adb, self.profile, self.db,
            dry_run=dry_run, selected_passes=selected_passes,
        )
        if from_mass:
            self._start_mass_worker(worker, f"Keepers — {'dry run' if dry_run else 'favoriting'}")
            return
        self._start_decision_worker(worker, dry_run=dry_run)

    def _review_pvp_cleanup(self, *, from_mass=False):
        from PySide6.QtWidgets import QDialog
        from ..execution.executor import Executor
        from ..execution.pvp_cleanup import plan_pvp_cleanup
        from .widgets.pvp_cleanup import PvpCleanupDialog
        from .workers import PvpCleanupWorker

        if MainWindow._running_device_workers(self):
            QMessageBox.warning(self, "Operation Running", "Stop the current operation before starting another.")
            return
        plan = plan_pvp_cleanup(self.db.get_all_for_cleanup(), Executor._keeper_key)
        if not plan.eligible:
            QMessageBox.information(
                self, "No PvP Cleanup Candidates",
                "No unambiguous, favorited 0–2★ TRANSFER records qualify.\n"
                f"Protected favorites: {plan.protected:,}\nHeld for review: {plan.ambiguous:,}\n\n"
                "Review your saved keeper decisions first. Phone state has not been checked.",
            )
            return
        dialog = PvpCleanupDialog(plan, self)
        if dialog.exec() != QDialog.Accepted:
            return
        candidates = dialog.selected_candidates()
        if not candidates:
            return
        if MainWindow._running_device_workers(self):
            QMessageBox.warning(self, "Operation Running", "Stop the current operation before starting another.")
            return
        if not self.adb or not self.profile:
            QMessageBox.warning(self, "Not Connected", "Connect to a device first.")
            return
        worker = PvpCleanupWorker(
            self.adb, self.profile, self.db, candidates, dry_run=dialog.dry_run,
        )
        if from_mass:
            self._start_mass_worker(worker, f"PvP cleanup — {'dry run' if dialog.dry_run else 'unfavoriting'}")
        else:
            self._start_decision_worker(worker, dry_run=dialog.dry_run, action="PvP cleanup")

    def _start_decision_worker(self, worker, *, dry_run=False, action="Favoriting"):
        self._apply_speed_settings()
        self.decision_tab.set_favoriting(True, dry_run=dry_run, action=action)
        self._fav_worker = worker
        self._fav_stop_requested = False
        self._fav_completed_worker = None
        self._fav_result = None
        worker.progress.connect(lambda cur, total, message: self.decision_tab.on_fav_progress(cur, total, message)
                                if worker is self._fav_worker and worker is not self._fav_completed_worker else None)
        worker.action_progress.connect(lambda payload: self.decision_tab.on_action_progress(payload)
                                       if worker is self._fav_worker
                                       and worker is not self._fav_completed_worker else None)
        worker.error.connect(lambda message: self.decision_tab.on_fav_error(message)
                             if worker is self._fav_worker and worker is not self._fav_completed_worker else None)
        worker.finished.connect(lambda result: self._finish_decision_action(result, worker=worker))
        worker.start()

    def _finish_decision_action(self, result, *, worker=None):
        worker = self._fav_worker if worker is None else worker
        if (worker is not self._fav_worker
                or worker is getattr(self, '_fav_completed_worker', None)):
            return
        self._fav_result = dict(result)
        if worker.isRunning():
            from PySide6.QtCore import QTimer
            QTimer.singleShot(25, lambda: self._finish_decision_action(result, worker=worker))
            return
        if getattr(self, '_fav_stop_requested', False):
            self._fav_result['aborted'] = True
        if getattr(worker, 'dry_run', False) is True:
            self._fav_result['dry_run'] = True
        self._fav_completed_worker = worker
        if not self._fav_result.get('dry_run'):
            self._refresh_collection()
            self._refresh_decisions()
        self.decision_tab.on_fav_finished(self._fav_result)
        MainWindow._on_favorite_finished(self, self._fav_result)

    def _toggle_fav_pause(self):
        if (getattr(self, '_fav_worker', None) is not None and self._fav_worker.isRunning()
                and not getattr(self, '_fav_stop_requested', False)):
            if self.decision_tab.fav_pause_btn.text() == "Pause":
                self._fav_worker.pause()
                self.decision_tab.set_fav_paused(True)
            else:
                self._fav_worker.resume()
                self.decision_tab.set_fav_paused(False)

    def _stop_favorite(self):
        worker = getattr(self, '_fav_worker', None)
        if worker:
            self._fav_stop_requested = True
            worker.abort()
            if (self.adb is not None
                    and getattr(self.adb, "has_stream_frames", False) is True):
                self.adb.invalidate_stream_frames()
            self.decision_tab.set_fav_stopping()
            from PySide6.QtCore import QTimer
            QTimer.singleShot(250, lambda: self._force_stop_favorite(worker))

    def _force_stop_favorite(self, worker=None):
        current = getattr(self, '_fav_worker', None)
        worker = current if worker is None else worker
        if worker is None or worker is not current:
            return
        if worker.isRunning():
            self.decision_tab.fav_status_label.setText("Stopping...")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(250, lambda: self._force_stop_favorite(worker))
            return
        result = getattr(self, '_fav_result', None)
        if result is None:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(25, lambda: self._force_stop_favorite(worker))
            return
        MainWindow._finish_decision_action(self, result, worker=worker)

    @Slot()
    def _unfavorite_all(self, *, from_mass: bool = False):
        if MainWindow._running_device_workers(self):
            QMessageBox.warning(self, "Operation Running", "Stop the current operation before starting another.")
            return
        if not self.adb or not self.profile:
            QMessageBox.warning(self, "Not Connected", "Connect to a device first.")
            return

        reply = QMessageBox.warning(
            self, "Unfavorite All Pokemon",
            "This will REMOVE the favorite star from ALL Pokemon.\n\n"
            "It checks every Pokémon in storage and removes the star where present.\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        from .workers import UnfavoriteWorker
        if MainWindow._running_device_workers(self):
            QMessageBox.warning(self, "Operation Running", "Stop the current operation before starting another.")
            return
        worker = UnfavoriteWorker(self.adb, self.profile, self.db)
        if from_mass:
            self._start_mass_worker(worker, "Unfavoriting all Pokémon")
            return
        self._start_decision_worker(worker, action="Unfavoriting")

    # ── Mass Actions ─────────────────────────────────────────────────

    @Slot(str, str)
    def _start_favorite_filter(self, query: str, label: str):
        if MainWindow._running_device_workers(self):
            QMessageBox.warning(self, "Operation Running", "Stop the current operation before starting another.")
            return
        if not self.adb or not self.profile:
            QMessageBox.warning(self, "Not Connected", "Connect to a device first.")
            return

        from .workers import FavoriteFilterWorker
        worker = FavoriteFilterWorker(
            self.adb, self.profile, query, label
        )
        self._start_mass_worker(worker, f"Favoriting: {label} ({query})")

    def _start_mass_worker(self, worker, label):
        self._apply_speed_settings()
        self.decision_tab.set_decision_editing_enabled(False)
        self._mass_worker = worker
        self._mass_stop_requested = False
        self._mass_completed_worker = None
        self._mass_result = None
        worker.progress.connect(lambda cur, total, message: self.mass_tab.on_progress(cur, total, message)
                                if worker is self._mass_worker
                                and worker is not self._mass_completed_worker else None)
        worker.action_progress.connect(lambda payload: self.mass_tab.on_action_progress(payload)
                                       if worker is self._mass_worker
                                       and worker is not self._mass_completed_worker else None)
        worker.error.connect(lambda message: self.mass_tab.on_error(message)
                             if worker is self._mass_worker
                             and worker is not self._mass_completed_worker else None)
        worker.finished.connect(lambda result: self._on_mass_action_finished(result, worker=worker))
        self.mass_tab.set_running(True, label)
        worker.start()

    @Slot(dict)
    def _on_mass_action_finished(self, result: dict, *, worker=None):
        worker = self._mass_worker if worker is None else worker
        if (worker is not self._mass_worker
                or worker is getattr(self, '_mass_completed_worker', None)):
            return
        self._mass_result = dict(result)
        if worker.isRunning():
            # Custom result signals precede QThread's actual return. Keep its
            # controls locked until cleanup has completed and the owner is idle.
            from PySide6.QtCore import QTimer
            QTimer.singleShot(25, lambda: self._on_mass_action_finished(result, worker=worker))
            return
        if getattr(self, '_mass_stop_requested', False):
            self._mass_result['aborted'] = True
        if getattr(worker, 'dry_run', False) is True:
            self._mass_result['dry_run'] = True
        self._mass_completed_worker = worker
        self.decision_tab.set_decision_editing_enabled(True)
        if not self._mass_result.get('dry_run'):
            self._refresh_collection()
            self._refresh_decisions()
        self.mass_tab.on_finished(self._mass_result)

    def _stop_mass_action(self):
        worker = getattr(self, '_mass_worker', None)
        if worker:
            self._mass_stop_requested = True
            worker.abort()
            if (self.adb is not None
                    and getattr(self.adb, "has_stream_frames", False) is True):
                self.adb.invalidate_stream_frames()
            self.mass_tab.set_stopping()
            from PySide6.QtCore import QTimer
            QTimer.singleShot(250, lambda: self._force_stop_mass(worker))

    def _force_stop_mass(self, worker=None):
        current = getattr(self, '_mass_worker', None)
        worker = current if worker is None else worker
        if worker is None or worker is not current:
            return
        if worker.isRunning():
            self.mass_tab.status_label.setText("Stopping...")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(250, lambda: self._force_stop_mass(worker))
            return
        result = getattr(self, '_mass_result', None)
        if result is None:
            # The thread can exit before its queued result signal reaches the
            # UI. Every action worker emits one; never replace its real counts
            # or error with a fabricated "stopped" result.
            from PySide6.QtCore import QTimer
            QTimer.singleShot(25, lambda: self._force_stop_mass(worker))
            return
        MainWindow._on_mass_action_finished(self, result, worker=worker)

    def _toggle_mass_pause(self):
        if (getattr(self, '_mass_worker', None) is not None and self._mass_worker.isRunning()
                and not getattr(self, '_mass_stop_requested', False)):
            if self.mass_tab.pause_btn.text() == "Pause":
                self._mass_worker.pause()
                self.mass_tab.set_paused(True)
            else:
                self._mass_worker.resume()
                self.mass_tab.set_paused(False)

    @Slot(dict)
    def _on_favorite_finished(self, result: dict):
        unfavorite = 'unfavorited' in result
        action = "Unfavoriting" if unfavorite else "Favoriting"
        fav = result.get("unfavorited" if unfavorite else "favorited", 0)
        skip = result.get("skipped", 0)
        unresolved = result.get("unresolved", result.get("unmatched", 0))
        database_counts = []
        if not result.get('dry_run'):
            for key, label in (("db_synced", "Favorite status saved"),
                               ("db_unresolved", "Favorite status not saved")):
                if type(result.get(key)) is int:
                    database_counts.append(f"{label}: {result[key]}")
        database_unresolved = (not result.get('dry_run')
                               and type(result.get('db_unresolved')) is int
                               and result['db_unresolved'] > 0)
        has_error = 'error' in result and result['error'] is not None
        mode = "Dry Run" if result.get('dry_run') else action
        title = (f"{mode} Failed" if has_error else f"{mode} Stopped"
                 if result.get('aborted') else "Dry Run Complete" if result.get('dry_run')
                 else f"{action} Complete")
        if not has_error and not result.get('aborted') and (unresolved or result.get('ambiguous') or database_unresolved):
            title += " — Needs Review"
        count_label = (("Would unfavorite" if unfavorite else "Would favorite") if result.get('dry_run')
                       else "Unfavorited" if unfavorite else "Favorited")
        detail = f"\nError: {result['error'] or 'Operation failed without an error message'}" if has_error else ""
        if database_counts:
            detail += "\n" + "\n".join(database_counts)
        if result.get('note'):
            detail += f"\n{result['note']}"
        QMessageBox.information(
            self, title,
            f"{count_label}: {fav} Pokémon\nSkipped: {skip}\nUnresolved: {unresolved}{detail}"
        )
        database_status = ", " + ", ".join(database_counts) if database_counts else ""
        self.statusBar().showMessage(f"{title}: {fav} matched, {skip} skipped, {unresolved} unresolved{database_status}")

    def _refresh_battery(self):
        """Refresh battery level and connection status."""
        device_connected = False
        if self.adb:
            try:
                devices = self.adb.get_devices()
                if not devices or (self.adb.serial and self.adb.serial not in devices):
                    self.scan_tab.device_label.setText("DISCONNECTED — reconnect USB")
                    self.scan_tab.device_label.setStyleSheet("font-size: 14px; color: #f44;")
                    self.scan_tab.phone_screen_on_btn.setEnabled(False)
                    self.scan_tab.phone_screen_off_btn.setEnabled(False)
                    return

                device_connected = True
                self.scan_tab.phone_screen_on_btn.setEnabled(True)
                self.scan_tab.phone_screen_off_btn.setEnabled(self.adb.display_id is not None)
                battery = self.adb.get_battery_level()
                if battery >= 0:
                    info = self.adb.get_device_info()
                    color = "#8f8" if battery > 20 else "#f88"
                    stream_label = " — App-only stream" if self.adb.display_id is not None else ""
                    self.scan_tab.device_label.setText(
                        f"{info.model} ({info.resolution}){stream_label} — Battery: {battery}%"
                    )
                    self.scan_tab.device_label.setStyleSheet(f"font-size: 14px; color: {color};")
            except Exception:
                self.scan_tab.device_label.setText("DISCONNECTED — reconnect USB")
                self.scan_tab.device_label.setStyleSheet("font-size: 14px; color: #f44;")
                self.scan_tab.phone_screen_on_btn.setEnabled(device_connected)
                self.scan_tab.phone_screen_off_btn.setEnabled(
                    device_connected and self.adb.display_id is not None
                )

    # ── Dedup ─────────────────────────────────────────────────────────

    @Slot()
    def _remove_duplicates(self):
        deleted = self.db.remove_duplicates()
        if deleted > 0:
            self._refresh_collection()
            self.statusBar().showMessage(f"Removed {deleted} duplicates")
        else:
            self.statusBar().showMessage("No duplicates found")

    # ── Data refresh ─────────────────────────────────────────────────

    def _refresh_collection(self):
        try:
            pokemon = self.db.get_all()
            self.collection_tab.load_pokemon(pokemon)
        except Exception as e:
            log.warning("Failed to refresh collection: %s", e)

    def _refresh_decisions(self):
        try:
            pokemon = self.db.get_all()
            self.decision_tab.load_pokemon(pokemon)
        except Exception as e:
            log.warning("Failed to refresh decisions: %s", e)

    @Slot(int)
    def _on_tab_changed(self, index: int):
        if index == 1:  # Collection
            self._refresh_collection()
        elif index == 2:  # Decisions
            self._refresh_decisions()

    def closeEvent(self, event):
        running = MainWindow._running_device_workers(self)
        if running:
            reply = QMessageBox.question(
                self, "Operation Running",
                "An operation is still running. Stop it and exit?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.No:
                event.ignore()
                return
            uncancellable = False
            for worker in running:
                abort = getattr(worker, "abort", None)
                if callable(abort):
                    abort()
                else:
                    uncancellable = True
            if (self.adb is not None
                    and getattr(self.adb, "has_stream_frames", False) is True):
                self.adb.invalidate_stream_frames()
            if uncancellable:
                self.statusBar().showMessage("Wait for the current operation to finish before closing")
                event.ignore()
                return
            import time
            deadline = time.monotonic() + 5.0
            for worker in running:
                remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                if remaining_ms:
                    worker.wait(remaining_ms)
            if any(worker.isRunning() for worker in running):
                # A clock/capture request may still be unwinding. Do not close
                # its mmap, pipes, or database underneath the scan thread.
                self.statusBar().showMessage("Stopping operations; close the window again once they have stopped")
                event.ignore()
                return

        monitor = getattr(self, "_resource_monitor", None)
        if monitor is not None and not monitor.stop():
            self.statusBar().showMessage("Stopping resource monitoring; try closing again")
            event.ignore()
            return
        if self.adb is not None:
            self.adb.close_stream_capture()
        self.db.close()
        event.accept()
