"""Main application window with tabbed interface."""

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QMessageBox, QStatusBar, QFileDialog,
)
from PySide6.QtCore import Slot

from ..adb.controller import ADBController, ADBError
from ..calibration.profile import CalibrationProfile
from ..data.database import PokemonDatabase
from ..decision.engine import DecisionEngine
from ..config import ensure_dirs, DB_PATH

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
        self.decision_tab.fav_pause_btn.clicked.connect(self._toggle_fav_pause)
        self.decision_tab.fav_stop_btn.clicked.connect(self._stop_favorite)
        self.decision_tab.decisions_changed.connect(self._refresh_collection)

        # Collection tab
        self.collection_tab.dedup_btn.clicked.connect(self._remove_duplicates)

        # Mass Actions tab
        self.mass_tab.unfav_all_btn.clicked.connect(self._unfavorite_all)
        self.mass_tab.favorite_filter.connect(self._start_favorite_filter)
        self.mass_tab.fav_keepers_dry_btn.clicked.connect(lambda: self._start_favorite(dry_run=True))
        self.mass_tab.fav_keepers_real_btn.clicked.connect(lambda: self._start_favorite(dry_run=False))
        self.mass_tab.stop_action.connect(self._stop_mass_action)
        self.mass_tab.pause_action.connect(self._toggle_mass_pause)

        # Tab change — refresh data
        self.tabs.currentChanged.connect(self._on_tab_changed)

    # ── Device connection ────────────────────────────────────────────

    @Slot()
    def _connect_device(self):
        try:
            self.adb = ADBController()
            self.adb.connect()
            info = self.adb.get_device_info()
            battery = self.adb.get_battery_level()

            self.scan_tab.device_label.setText(
                f"{info.model} ({info.resolution}) — Battery: {battery}%"
            )
            self.scan_tab.device_label.setStyleSheet("font-size: 14px; color: #8f8;")

            # Load or create calibration
            self.profile = CalibrationProfile.find_for_device(info)
            if not self.profile:
                self.profile = CalibrationProfile.create_default(info)
                self.profile.save()

            self.statusBar().showMessage(f"Connected: {info.model}")
            self.scan_tab.start_full_btn.setEnabled(True)
            self.scan_tab.start_current_btn.setEnabled(True)

            # Load existing data if any
            self._refresh_collection()

        except ADBError as e:
            QMessageBox.critical(self, "Connection Failed", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    @Slot()
    def _clear_database(self):
        reply = QMessageBox.warning(
            self, "Clear Database",
            "This will DELETE all scanned Pokemon data.\n\nAre you sure?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.db.close()
            import os
            db_path = str(DB_PATH)
            if os.path.exists(db_path):
                os.remove(db_path)
            self.db = PokemonDatabase()
            self._refresh_collection()
            self._refresh_decisions()
            self.scan_tab.log_view.clear()
            self.scan_tab.progress_bar.setValue(0)
            self.scan_tab.status_label.setText("Database cleared")
            self.statusBar().showMessage("Database cleared — ready for fresh scan")

    # ── Scanning ─────────────────────────────────────────────────────

    @Slot()
    def _start_full_scan(self):
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
        if skip_n > 0:
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
        config.DELAY_AFTER_APPRAISE_TAP = (self.scan_tab.appraise_delay_spin.value(), 0.1)
        config.DELAY_AFTER_SWIPE = (self.scan_tab.swipe_delay_spin.value(), 0.0)
        config.ANTI_DETECTION_ENABLED = self.scan_tab.anti_detection_check.isChecked()
        config.DEFAULT_SWIPE_DURATION_MS = self.scan_tab.swipe_duration_spin.value()
        config.BAR_WAIT_MAX = self.scan_tab.bar_wait_spin.value()
        config.USE_CALCULATED_CP = self.scan_tab.calc_cp_check.isChecked()
        config.CAPTURE_SIZE_TAGS = self.scan_tab.size_tags_check.isChecked()

    def _start_scan_worker(self):
        self._apply_speed_settings()
        worker = self._scan_worker

        worker.progress.connect(self.scan_tab.on_progress)
        worker.status.connect(self.scan_tab.on_status)
        worker.error.connect(self.scan_tab.on_error)
        worker.finished.connect(self._on_scan_finished)
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
        if self._scan_worker:
            self._scan_worker.abort()
            self.scan_tab.status_label.setText("Stopping...")
            # Don't block — the finished signal will clean up.
            # If worker is stuck, force-terminate after a short non-blocking check.
            from PySide6.QtCore import QTimer
            QTimer.singleShot(3000, self._force_stop_scan)

    def _force_stop_scan(self):
        if self._scan_worker and self._scan_worker.isRunning():
            log.warning("Worker did not stop in 3s, terminating")
            self._scan_worker.terminate()
            self.scan_tab.set_scanning(False)
            self.scan_tab.status_label.setText("Force stopped")
            self._refresh_collection()

    @Slot(int)
    def _on_scan_finished(self, count: int):
        self.scan_tab.on_finished(count)
        self._refresh_collection()
        self.statusBar().showMessage(f"Scan complete: {count} Pokemon indexed")
        if hasattr(self, '_speed_timer'):
            self._speed_timer.stop()

    # ── Decision engine ──────────────────────────────────────────────

    @Slot()
    def _run_decision_engine(self):
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

    def _start_favorite(self, dry_run: bool = False):
        if not self.adb or not self.profile:
            QMessageBox.warning(self, "Not Connected", "Connect to a device first.")
            return

        all_pokemon = self.db.get_all()
        keepers = [p for p in all_pokemon if p.decision == "KEEP"]
        need_fav = [p for p in keepers if not p.favorited]

        if not need_fav:
            QMessageBox.information(self, "All Done", "All keepers are already favorited.")
            return

        selected_passes = self.decision_tab.get_selected_fav_passes()
        mode = "DRY RUN (test)" if dry_run else "FOR REAL"
        reply = QMessageBox.question(
            self, f"Favorite Keepers — {mode}",
            f"Will {'simulate' if dry_run else 'favorite'} {len(need_fav)} keepers.\n\n"
            f"Passes: {', '.join(selected_passes)}\n\n"
            f"Matches species + IVs + HP against keeper list.\n\n"
            f"{'No stars will be tapped.' if dry_run else 'Will tap the star on matches.'}\n\n"
            f"Start?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        from .workers import FavoriteWorker

        selected_passes = self.decision_tab.get_selected_fav_passes()
        if not selected_passes:
            QMessageBox.warning(self, "No Passes", "Select at least one pass.")
            return

        self._apply_speed_settings()
        self.decision_tab.set_favoriting(True, dry_run=dry_run)

        self._fav_worker = FavoriteWorker(
            self.adb, self.profile, self.db,
            dry_run=dry_run, selected_passes=selected_passes,
        )
        self._fav_worker.progress.connect(self.decision_tab.on_fav_progress)
        self._fav_worker.finished.connect(self.decision_tab.on_fav_finished)
        self._fav_worker.finished.connect(self._on_favorite_finished)
        self._fav_worker.error.connect(
            lambda msg: self.decision_tab.fav_log.append(
                f'<span style="color: #f66;">ERROR: {msg}</span>')
        )
        self._fav_worker.start()

    def _toggle_fav_pause(self):
        if hasattr(self, '_fav_worker') and self._fav_worker:
            if self.decision_tab.fav_pause_btn.text() == "Pause":
                self._fav_worker.pause()
                self.decision_tab.fav_pause_btn.setText("Resume")
                self.decision_tab.fav_status_label.setText("Paused")
            else:
                self._fav_worker.resume()
                self.decision_tab.fav_pause_btn.setText("Pause")
                self.decision_tab.fav_status_label.setText("Favoriting...")

    def _stop_favorite(self):
        if hasattr(self, '_fav_worker') and self._fav_worker:
            self._fav_worker.abort()
            self.decision_tab.fav_status_label.setText("Stopping...")
            # Don't block — the finished signal will clean up.
            from PySide6.QtCore import QTimer
            QTimer.singleShot(3000, self._force_stop_favorite)

    def _force_stop_favorite(self):
        if hasattr(self, '_fav_worker') and self._fav_worker and self._fav_worker.isRunning():
            log.warning("Fav worker did not stop in 3s, terminating")
            self._fav_worker.terminate()
            self.decision_tab.set_favoriting(False)

    @Slot()
    def _unfavorite_all(self):
        if not self.adb or not self.profile:
            QMessageBox.warning(self, "Not Connected", "Connect to a device first.")
            return

        reply = QMessageBox.warning(
            self, "Unfavorite All Pokemon",
            "This will REMOVE the favorite star from ALL Pokemon.\n\n"
            "It searches for 'favorite' and swipes through each one.\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        from .workers import UnfavoriteWorker
        self._unfav_worker = UnfavoriteWorker(self.adb, self.profile, self.db)
        self._unfav_worker.progress.connect(
            lambda cur, tot, msg: self.statusBar().showMessage(f"Unfavoriting: {msg} ({cur}/{tot})")
        )
        self._unfav_worker.finished.connect(
            lambda result: QMessageBox.information(
                self, "Done", f"Unfavorited {result.get('unfavorited', 0)} Pokemon"
            )
        )
        self._unfav_worker.error.connect(
            lambda msg: QMessageBox.warning(self, "Error", msg)
        )
        self._unfav_worker.start()

    # ── Mass Actions ─────────────────────────────────────────────────

    @Slot(str, str)
    def _start_favorite_filter(self, query: str, label: str):
        if not self.adb or not self.profile:
            QMessageBox.warning(self, "Not Connected", "Connect to a device first.")
            return

        from .workers import FavoriteFilterWorker
        self._mass_worker = FavoriteFilterWorker(
            self.adb, self.profile, query, label
        )
        self._mass_worker.progress.connect(self.mass_tab.on_progress)
        self._mass_worker.finished.connect(self._on_mass_action_finished)
        self._mass_worker.error.connect(lambda msg: self.mass_tab.action_log.append(f"ERROR: {msg}"))
        self.mass_tab.set_running(True, f"Favoriting: {label} ({query})")
        self._mass_worker.start()

    @Slot(dict)
    def _on_mass_action_finished(self, result: dict):
        self.mass_tab.on_finished(result)
        self._mass_worker = None

    def _stop_mass_action(self):
        if hasattr(self, '_mass_worker') and self._mass_worker:
            self._mass_worker.abort()
            self.mass_tab.status_label.setText("Stopping...")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(3000, self._force_stop_mass)

    def _force_stop_mass(self):
        if hasattr(self, '_mass_worker') and self._mass_worker and self._mass_worker.isRunning():
            self._mass_worker.terminate()
            self.mass_tab.set_running(False)

    def _toggle_mass_pause(self):
        if hasattr(self, '_mass_worker') and self._mass_worker:
            if self.mass_tab.pause_btn.text() == "Pause":
                self._mass_worker.pause()
                self.mass_tab.pause_btn.setText("Resume")
                self.mass_tab.status_label.setText("Paused")
            else:
                self._mass_worker.resume()
                self.mass_tab.pause_btn.setText("Pause")
                self.mass_tab.status_label.setText("Running...")

    @Slot(dict)
    def _on_favorite_finished(self, result: dict):
        fav = result.get("favorited", 0)
        skip = result.get("skipped", 0)
        QMessageBox.information(
            self, "Favoriting Complete",
            f"Favorited: {fav} Pokemon\n"
            f"Skipped: {skip}\n\n"
            f"All keepers are now protected.\n"
            f"You can now manually mass-transfer in Pokemon Go."
        )
        self.statusBar().showMessage(f"Favoriting done: {fav} favorited, {skip} skipped")

    def _refresh_battery(self):
        """Refresh battery level and connection status."""
        if self.adb:
            try:
                devices = self.adb.get_devices()
                if not devices or (self.adb.serial and self.adb.serial not in devices):
                    self.scan_tab.device_label.setText("DISCONNECTED — reconnect USB")
                    self.scan_tab.device_label.setStyleSheet("font-size: 14px; color: #f44;")
                    return

                battery = self.adb.get_battery_level()
                if battery >= 0:
                    info = self.adb.get_device_info()
                    color = "#8f8" if battery > 20 else "#f88"
                    self.scan_tab.device_label.setText(
                        f"{info.model} ({info.resolution}) — Battery: {battery}%"
                    )
                    self.scan_tab.device_label.setStyleSheet(f"font-size: 14px; color: {color};")
            except Exception:
                self.scan_tab.device_label.setText("DISCONNECTED — reconnect USB")
                self.scan_tab.device_label.setStyleSheet("font-size: 14px; color: #f44;")

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
        if self._scan_worker and self._scan_worker.isRunning():
            reply = QMessageBox.question(
                self, "Scan Running",
                "A scan is still running. Stop it and exit?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.No:
                event.ignore()
                return
            self._scan_worker.abort()
            self._scan_worker.wait(5000)

        self.db.close()
        event.accept()
