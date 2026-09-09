"""Closing/reconnecting never closes stream resources underneath a scan."""

import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QMessageBox
from pokemgr.gui.main_window import MainWindow


class StreamResourceCleanupTests(unittest.TestCase):
    @staticmethod
    def database_window():
        return SimpleNamespace(db=Mock(), scan_tab=Mock(), _scan_worker=None,
                               _refresh_collection=Mock(), _refresh_decisions=Mock(),
                               statusBar=Mock(return_value=Mock()))

    def test_clear_database_refuses_any_running_device_worker_before_confirmation(self):
        for name in ("_scan_worker", "_fav_worker", "_unfav_worker", "_mass_worker"):
            with self.subTest(worker=name):
                window = self.database_window()
                worker = Mock()
                worker.isRunning.return_value = True
                setattr(window, name, worker)
                with patch("pokemgr.gui.main_window.QMessageBox.warning", return_value=QMessageBox.Yes) as warning, \
                     patch("pokemgr.gui.main_window.PokemonDatabase") as factory, \
                     patch("os.remove") as remove:
                    MainWindow._clear_database(window)
                self.assertEqual([call.args[1] for call in warning.call_args_list], ["Operation Running"])
                window.db.close.assert_not_called()
                factory.assert_not_called()
                remove.assert_not_called()

    def test_clear_database_rechecks_workers_after_modal_confirmation(self):
        window = self.database_window()
        worker = Mock()
        worker.isRunning.return_value = True
        def confirm(*args):
            window._mass_worker = worker
            return QMessageBox.Yes
        with patch("pokemgr.gui.main_window.QMessageBox.warning", side_effect=confirm) as warning, \
             patch("pokemgr.gui.main_window.PokemonDatabase") as factory, \
             patch("os.remove") as remove:
            MainWindow._clear_database(window)
        self.assertEqual([call.args[1] for call in warning.call_args_list],
                         ["Clear Database", "Operation Running"])
        window.db.close.assert_not_called()
        factory.assert_not_called()
        remove.assert_not_called()

    def test_idle_database_clear_still_requires_confirmation(self):
        window = self.database_window()
        with patch("pokemgr.gui.main_window.QMessageBox.warning", return_value=QMessageBox.No), \
             patch("pokemgr.gui.main_window.PokemonDatabase") as factory, \
             patch("os.remove") as remove:
            MainWindow._clear_database(window)
        window.db.close.assert_not_called()
        factory.assert_not_called()
        remove.assert_not_called()

    def test_confirmed_idle_database_clear_replaces_only_the_idle_connection(self):
        window = self.database_window()
        original = window.db
        with patch("pokemgr.gui.main_window.QMessageBox.warning", return_value=QMessageBox.Yes), \
             patch("pokemgr.gui.main_window.PokemonDatabase") as factory, \
             patch("os.path.exists", return_value=True), patch("os.remove") as remove:
            MainWindow._clear_database(window)
        original.close.assert_called_once()
        factory.assert_called_once_with()
        remove.assert_called_once()
        self.assertIs(window.db, factory.return_value)
        window._refresh_collection.assert_called_once()
        window._refresh_decisions.assert_called_once()

    @staticmethod
    def stopping_window():
        window = SimpleNamespace(_scan_worker=Mock(), adb=Mock(), scan_tab=Mock(),
                                 _refresh_collection=Mock(), _speed_timer=Mock())
        window._force_stop_scan = lambda worker=None: MainWindow._force_stop_scan(window, worker)
        window.adb.has_stream_frames = True
        return window

    def test_abort_invalidates_evidence_without_closing_active_resources(self):
        window = self.stopping_window()
        with patch("PySide6.QtCore.QTimer.singleShot") as timer:
            MainWindow._abort_scan(window)
        window._scan_worker.abort.assert_called_once()
        window.adb.invalidate_stream_frames.assert_called_once()
        window.adb.close_stream_capture.assert_not_called()
        window._scan_worker.terminate.assert_not_called()
        window.scan_tab.status_label.setText.assert_called_with("Stopping...")
        self.assertEqual(250, timer.call_args.args[0])

    def test_running_worker_stays_stopping_until_bounded_io_unwinds(self):
        window = self.stopping_window()
        window._scan_worker.isRunning.return_value = True
        with patch("PySide6.QtCore.QTimer.singleShot") as timer:
            for _ in range(20):  # A request can outlast the former 3s kill timer.
                MainWindow._force_stop_scan(window)
        self.assertEqual(20, timer.call_count)
        self.assertTrue(all(call.args[0] == 250 for call in timer.call_args_list))
        window._scan_worker.terminate.assert_not_called()
        window._scan_worker.wait.assert_not_called()
        window.adb.close_stream_capture.assert_not_called()
        window.scan_tab.set_scanning.assert_not_called()
        window._refresh_collection.assert_not_called()

    def test_stopped_worker_releases_ui_only_after_thread_has_finished(self):
        window = self.stopping_window()
        window._scan_worker.isRunning.return_value = False
        with patch("PySide6.QtCore.QTimer.singleShot") as timer:
            MainWindow._force_stop_scan(window)
        timer.assert_not_called()
        window.scan_tab.set_scanning.assert_called_once_with(False)
        window.scan_tab.status_label.setText.assert_called_once_with("Stopped")
        window._speed_timer.stop.assert_called_once()
        window._refresh_collection.assert_called_once()
        window._scan_worker.terminate.assert_not_called()

    def test_delayed_stop_callback_cannot_stop_a_new_scan(self):
        window = self.stopping_window()
        old_worker = window._scan_worker
        with patch("PySide6.QtCore.QTimer.singleShot") as timer:
            MainWindow._abort_scan(window)
        callback = timer.call_args.args[1]
        window._scan_worker = Mock()
        window.scan_tab.reset_mock()
        callback()
        window._scan_worker.isRunning.assert_not_called()
        window._scan_worker.abort.assert_not_called()
        window._scan_worker.terminate.assert_not_called()
        window.scan_tab.set_scanning.assert_not_called()

    def test_action_stops_poll_cooperatively_and_never_terminate_or_close_resources(self):
        for attribute, stop, poll, tab, setter, label in (
            ("_fav_worker", MainWindow._stop_favorite, MainWindow._force_stop_favorite,
             "decision_tab", "set_favoriting", "fav_status_label"),
            ("_mass_worker", MainWindow._stop_mass_action, MainWindow._force_stop_mass,
             "mass_tab", "set_running", "status_label"),
        ):
            with self.subTest(worker=attribute):
                worker = Mock()
                worker.isRunning.return_value = True
                window = SimpleNamespace(adb=Mock(), statusBar=Mock(return_value=Mock()),
                                         **{attribute: worker, tab: Mock()})
                window.adb.has_stream_frames = True
                setattr(window, poll.__name__, lambda owner=None: poll(window, owner))
                with patch("PySide6.QtCore.QTimer.singleShot") as timer:
                    stop(window)
                    for _ in range(20):
                        timer.call_args.args[1]()
                worker.abort.assert_called_once()
                window.adb.invalidate_stream_frames.assert_called_once()
                worker.terminate.assert_not_called()
                worker.wait.assert_not_called()
                window.adb.close_stream_capture.assert_not_called()
                controls = getattr(window, tab)
                getattr(controls, setter).assert_not_called()
                getattr(controls, label).setText.assert_called_with("Stopping...")
                self.assertTrue(all(call.args[0] == 250 for call in timer.call_args_list))
                worker.isRunning.return_value = False
                if attribute == '_mass_worker':
                    window._mass_result = {'aborted': True}
                else:
                    window._fav_result = {'aborted': True}
                with patch("PySide6.QtCore.QTimer.singleShot") as timer, \
                     patch('pokemgr.gui.main_window.QMessageBox.information'):
                    poll(window, worker)
                timer.assert_not_called()
                if attribute == '_mass_worker':
                    controls.on_finished.assert_called_once_with({'aborted': True})
                else:
                    controls.on_fav_finished.assert_called_once_with({'aborted': True})

    def test_delayed_action_stop_callbacks_cannot_affect_replacement_workers(self):
        for attribute, poll, tab in (
            ("_fav_worker", MainWindow._force_stop_favorite, "decision_tab"),
            ("_mass_worker", MainWindow._force_stop_mass, "mass_tab"),
        ):
            with self.subTest(worker=attribute):
                current = Mock()
                window = SimpleNamespace(**{attribute: current, tab: Mock()})
                with patch("PySide6.QtCore.QTimer.singleShot") as timer:
                    poll(window, Mock())
                timer.assert_not_called()
                current.isRunning.assert_not_called()
                current.terminate.assert_not_called()

    def test_mass_result_signal_does_not_drop_still_running_thread_owner(self):
        worker = Mock()
        window = SimpleNamespace(_mass_worker=worker, mass_tab=Mock())
        with patch("PySide6.QtCore.QTimer.singleShot") as timer:
            MainWindow._on_mass_action_finished(window, {"favorited": 2})
        timer.assert_called_once()
        window.mass_tab.on_finished.assert_not_called()
        self.assertIs(window._mass_worker, worker)
        self.assertEqual(MainWindow._running_device_workers(window), [worker])

    def test_idle_close_releases_stream_before_database(self):
        events = []
        window = SimpleNamespace(_scan_worker=None, adb=Mock(), db=Mock())
        window.adb.close_stream_capture.side_effect = lambda: events.append("stream")
        window.db.close.side_effect = lambda: events.append("database")
        event = Mock()
        MainWindow.closeEvent(window, event)
        self.assertEqual(events, ["stream", "database"])
        event.accept.assert_called_once()

    def test_worker_that_has_not_finished_keeps_resources_open(self):
        window = SimpleNamespace(_scan_worker=Mock(), adb=Mock(), db=Mock(), statusBar=Mock(return_value=Mock()))
        window._scan_worker.isRunning.return_value = True
        event = Mock()
        with patch("pokemgr.gui.main_window.QMessageBox.question", return_value=QMessageBox.Yes):
            MainWindow.closeEvent(window, event)
        window._scan_worker.abort.assert_called_once()
        window._scan_worker.wait.assert_called_once()
        self.assertTrue(0 < window._scan_worker.wait.call_args.args[0] <= 5000)
        window.adb.close_stream_capture.assert_not_called()
        window.db.close.assert_not_called()
        event.ignore.assert_called_once()

    def test_stopped_worker_is_joined_before_resources_close(self):
        window = SimpleNamespace(_scan_worker=Mock(), adb=Mock(), db=Mock())
        window._scan_worker.isRunning.side_effect = [True, False]
        events = []
        window._scan_worker.wait.side_effect = lambda *_: events.append("joined")
        window.adb.close_stream_capture.side_effect = lambda: events.append("stream")
        event = Mock()
        with patch("pokemgr.gui.main_window.QMessageBox.question", return_value=QMessageBox.Yes):
            MainWindow.closeEvent(window, event)
        self.assertEqual(events, ["joined", "stream"])
        window.db.close.assert_called_once()
        event.accept.assert_called_once()

    def test_reconnect_refuses_to_close_an_active_workers_controller(self):
        window = SimpleNamespace(_scan_worker=Mock(), adb=Mock(), scan_tab=Mock())
        window._scan_worker.isRunning.return_value = True
        with patch("pokemgr.gui.main_window.ADBController") as factory:
            self.assertFalse(MainWindow._connect_device(window))
        factory.assert_not_called()
        window.adb.close_stream_capture.assert_not_called()

    def test_close_keeps_shared_resources_while_any_action_worker_runs(self):
        for name in ("_fav_worker", "_unfav_worker", "_mass_worker"):
            with self.subTest(worker=name):
                worker = Mock()
                worker.isRunning.return_value = True
                window = SimpleNamespace(_scan_worker=None, adb=Mock(), db=Mock(),
                                         statusBar=Mock(return_value=Mock()))
                setattr(window, name, worker)
                event = Mock()
                with patch("pokemgr.gui.main_window.QMessageBox.question", return_value=QMessageBox.Yes):
                    MainWindow.closeEvent(window, event)
                worker.abort.assert_called_once()
                worker.wait.assert_called_once()
                worker.terminate.assert_not_called()
                window.adb.close_stream_capture.assert_not_called()
                window.db.close.assert_not_called()
                event.ignore.assert_called_once()

    def test_reconnect_refuses_while_any_action_worker_uses_old_controller(self):
        for name in ("_fav_worker", "_unfav_worker", "_mass_worker"):
            with self.subTest(worker=name):
                window = SimpleNamespace(_scan_worker=None, adb=Mock(), scan_tab=Mock())
                worker = Mock()
                worker.isRunning.return_value = True
                setattr(window, name, worker)
                with patch("pokemgr.gui.main_window.ADBController") as factory:
                    self.assertFalse(MainWindow._connect_device(window))
                factory.assert_not_called()
                window.adb.close_stream_capture.assert_not_called()

    def test_reconnect_closes_previous_controller_before_creating_next(self):
        previous = Mock()
        current = Mock(display_id=15)
        current.get_device_info.return_value = SimpleNamespace(model="Phone", resolution="968x2376")
        current.get_battery_level.return_value = 80
        window = SimpleNamespace(_scan_worker=None, adb=previous, scan_tab=Mock(),
                                 _refresh_collection=Mock(), statusBar=Mock(return_value=Mock()))
        events = []
        previous.close_stream_capture.side_effect = lambda: events.append("close old")
        profile = SimpleNamespace(regions=SimpleNamespace(swipe_duration_ms=300))
        with patch("pokemgr.gui.main_window.ADBController", side_effect=lambda: events.append("create new") or current), \
             patch("pokemgr.gui.main_window.CalibrationProfile.find_for_device", return_value=profile):
            self.assertTrue(MainWindow._connect_device(window, show_errors=False))
        self.assertEqual(events, ["close old", "create new"])


if __name__ == "__main__":
    unittest.main()
