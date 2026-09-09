"""Stream scans darken the physical phone before starting any scan worker."""

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from pokemgr.adb.controller import ADBError
from pokemgr.gui.main_window import MainWindow
from pokemgr.gui.widgets.scan_control import ScanControl


def _window(display_id=15):
    worker = Mock()
    worker._sm = None
    return SimpleNamespace(
        adb=Mock(display_id=display_id),
        _scan_worker=worker,
        _apply_speed_settings=Mock(),
        scan_tab=Mock(),
        _on_scan_finished=Mock(),
        _on_scan_failed=Mock(),
        statusBar=Mock(return_value=Mock()),
    )


class ScanStreamStartTests(unittest.TestCase):
    def test_stream_scan_darkens_phone_before_worker_can_start(self):
        window = _window()
        events = []
        window.adb.turn_phone_screen_off.side_effect = lambda: events.append("phone off")
        window._scan_worker.start.side_effect = lambda: events.append("worker start")

        MainWindow._start_scan_worker(window)

        self.assertEqual(["phone off", "worker start"], events)
        window.scan_tab.set_scanning.assert_called_once_with(True)

    @patch("pokemgr.gui.main_window.QMessageBox.critical")
    def test_failed_phone_screen_off_prevents_worker_start_and_reports_error(self, critical):
        window = _window()
        window.adb.turn_phone_screen_off.side_effect = ADBError("virtual display disappeared")

        MainWindow._start_scan_worker(window)

        window._scan_worker.start.assert_not_called()
        window.scan_tab.set_scanning.assert_not_called()
        window._apply_speed_settings.assert_not_called()
        critical.assert_called_once()
        window.scan_tab.on_error.assert_called_once()
        self.assertIn("virtual display disappeared", window.scan_tab.on_error.call_args.args[0])

    def test_physical_display_scan_starts_without_darkening_its_only_display(self):
        window = _window(display_id=None)

        MainWindow._start_scan_worker(window)

        window.adb.turn_phone_screen_off.assert_not_called()
        window._scan_worker.start.assert_called_once()
        window.scan_tab.set_scanning.assert_called_once_with(True)

    def test_explicit_screen_on_stays_available_while_worker_is_running(self):
        window = _window()
        window._scan_worker.isRunning.return_value = True

        MainWindow._turn_phone_screen_on(window)

        window.adb.turn_phone_screen_on.assert_called_once()
        window._scan_worker.abort.assert_not_called()
        window._scan_worker.pause.assert_not_called()


class ScanStreamControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_connected_phone_screen_on_button_remains_enabled_during_scan(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "pokemgr.config.DATA_DIR", Path(directory),
        ):
            widget = ScanControl()
        self.addCleanup(widget.deleteLater)
        self.assertFalse(widget.phone_screen_on_btn.isEnabled())
        widget.phone_screen_on_btn.setEnabled(True)

        widget.set_scanning(True)

        self.assertTrue(widget.phone_screen_on_btn.isEnabled())
        self.assertTrue(widget.abort_btn.isEnabled())
        self.assertFalse(widget.start_full_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
