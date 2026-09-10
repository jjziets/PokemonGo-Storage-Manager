"""Explicit ledger launches remain controllable and validate before connecting."""

# TRACEWEAVER: file-role=inventory-queue-launch-tests; req=REQ-SCAN-003,REQ-DATA-001; trace=TRACE-SCAN-003,TRACE-DATA-001; verifies=VER-SCAN-001
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests import test_auto_start_scan as launch_fixture
from tests.test_scan_queue import ledger, partition
from pokemgr.gui.main_window import MainWindow
from pokemgr.gui import app as gui_app
import run
from scripts import stream_pokemon


class ScanQueueLaunchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        launch_fixture.AutoStartScanTests.setUpClass()

    def setUp(self):
        fixture = launch_fixture.AutoStartScanTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.window, self.adb = fixture.window, fixture.adb
        self.worker, self.worker_type = fixture.worker, fixture.worker_type
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.directory) / "inventory queue.json"
        self.value = ledger()
        self.path.write_text(json.dumps(self.value))

    def test_queue_resets_resume_limits_and_uses_existing_pause_stop_worker(self):
        tab = self.window.scan_tab
        tab.max_pokemon_spin.setValue(10)
        tab.skip_spin.setValue(50)
        tab.resume_species.setText("Old target")
        tab.resume_cp.setValue(200)
        tab.unfavorite_check.setChecked(True)

        MainWindow.start_scan_queue(self.window, self.path)

        self.worker_type.assert_called_once_with(self.window.adb, self.window.profile, self.window.db,
                                                 unfavorite=False, max_pokemon=0)
        self.assertEqual([{"name": "category-01", "query": partition()["query"], "tags": {"shiny": True}}],
                         self.worker.selected_passes)
        self.assertEqual((0, "", 0), (self.worker.skip_first_n, self.worker.resume_target_species,
                                      self.worker.resume_target_cp))
        self.assertEqual((0, 0, "", 0, False), (tab.max_pokemon_spin.value(), tab.skip_spin.value(),
                                               tab.resume_species.text(), tab.resume_cp.value(),
                                               tab.unfavorite_check.isChecked()))
        self.worker.start.assert_called_once()
        self.assertTrue(tab.pause_btn.isEnabled())
        self.assertTrue(tab.abort_btn.isEnabled())
        self.assertEqual(self.value, json.loads(self.path.read_text()))
        self.assertEqual([], self.window.db.mock_calls)

    def test_invalid_ledger_never_connects_or_creates_worker(self):
        self.value["normal"]["completed"] = False
        self.path.write_text(json.dumps(self.value))
        MainWindow.start_scan_queue(self.window, self.path)
        self.adb.connect.assert_not_called()
        self.worker_type.assert_not_called()
        self.assertIn("Normal inventory", self.window.scan_tab.log_view.toPlainText())

    def test_any_running_device_worker_blocks_connection(self):
        for name in ("_scan_worker", "_fav_worker", "_unfav_worker", "_mass_worker"):
            with self.subTest(worker=name):
                running = Mock()
                running.isRunning.return_value = True
                setattr(self.window, name, running)
                MainWindow.start_scan_queue(self.window, self.path)
                self.adb.connect.assert_not_called()
                self.worker_type.assert_not_called()
                setattr(self.window, name, None)

    def test_ledger_or_worker_change_during_connection_prevents_launch(self):
        for mode in ("ledger", "worker"):
            with self.subTest(mode=mode):
                self.window._mass_worker = None
                self.path.write_text(json.dumps(ledger()))

                def changed():
                    if mode == "ledger":
                        value = ledger()
                        value["normal"]["active_session"] = "new-session"
                        self.path.write_text(json.dumps(value))
                    else:
                        self.window._mass_worker = Mock()
                        self.window._mass_worker.isRunning.return_value = True

                self.adb.connect.side_effect = changed
                MainWindow.start_scan_queue(self.window, self.path)
                self.worker_type.assert_not_called()
                self.adb.turn_phone_screen_off.assert_not_called()

    def test_saved_calibration_is_required(self):
        with patch("pokemgr.gui.main_window.CalibrationProfile.find_for_device", return_value=None), patch(
            "pokemgr.gui.main_window.CalibrationProfile.create_default",
        ) as create:
            MainWindow.start_scan_queue(self.window, self.path)
        create.assert_not_called()
        self.worker_type.assert_not_called()
        self.adb.turn_phone_screen_off.assert_not_called()


class ScanQueueCommandTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "inventory queue.json"
        self.path.write_text(json.dumps(ledger()))

    def test_gui_entry_schedules_queue_after_show_and_checks_it_before_window_creation(self):
        with patch.object(gui_app, "QApplication") as application, patch.object(
            gui_app, "MainWindow",
        ) as window, patch("PySide6.QtCore.QTimer.singleShot") as schedule:
            application.return_value.exec.return_value = 0
            events = []
            window.return_value.show.side_effect = lambda: events.append("show")
            schedule.side_effect = lambda *_args: events.append("schedule")
            with self.assertRaises(SystemExit):
                gui_app.run_gui(scan_queue=str(self.path))
            self.assertEqual(["show", "schedule"], events)
            schedule.call_args.args[1]()
            window.return_value.start_scan_queue.assert_called_once_with(str(self.path))
            window.return_value.start_default_scan.assert_not_called()
        self.path.write_text("{}")
        with patch.object(gui_app, "QApplication") as application, patch.object(gui_app, "MainWindow") as window:
            with self.assertRaises(ValueError):
                gui_app.run_gui(scan_queue=str(self.path))
            application.assert_not_called()
            window.assert_not_called()

    def test_cli_forwards_queue_path_without_default_scan_or_resume(self):
        with patch.object(run.sys, "argv", ["run.py", "gui", "--scan-queue", str(self.path)]), patch.object(
            run, "ensure_dirs",
        ), patch("pokemgr.gui.app.run_gui") as gui:
            run.main()
        gui.assert_called_once_with(start_scan=False, skip_first=0, resume_species="", resume_cp=0,
                                    scan_queue=str(self.path.resolve()))

    def test_both_entrypoints_reject_conflicting_flags_or_invalid_queue_before_devices(self):
        for module, prefix in ((run, ["run.py", "gui"]), (stream_pokemon, ["stream_pokemon.py"])):
            for flags in (["--start-scan"], ["--skip-first", "0"], ["--resume-cp", "100"], None):
                with self.subTest(module=module.__name__, flags=flags):
                    arguments = ["--scan-queue", str(self.path)] + (flags or [])
                    self.path.write_text(json.dumps(ledger()) if flags is not None else "{}")
                    with patch.object(module.sys, "argv", prefix + arguments), patch("sys.stderr", io.StringIO()), patch.object(
                        run, "ensure_dirs",
                    ) as ensure, patch.object(stream_pokemon, "ADBController") as adb, patch.object(
                        stream_pokemon.subprocess, "Popen",
                    ) as popen, patch("pokemgr.gui.app.run_gui") as gui:
                        with self.assertRaises(SystemExit) as stopped:
                            module.main()
                    self.assertEqual(2, stopped.exception.code)
                    ensure.assert_not_called()
                    adb.assert_not_called()
                    popen.assert_not_called()
                    gui.assert_not_called()

    def test_stream_runner_preserves_queue_path_as_one_argument(self):
        args = SimpleNamespace(scan_queue=str(self.path), start_scan=False,
                               skip_first=None, resume_species=None, resume_cp=None)
        self.assertEqual([stream_pokemon.sys.executable, str(stream_pokemon.ROOT / "run.py"), "gui",
                          "--scan-queue", str(self.path)], stream_pokemon.manager_command(args))
        args.scan_queue = None
        self.assertEqual([stream_pokemon.sys.executable, str(stream_pokemon.ROOT / "run.py"), "gui"],
                         stream_pokemon.manager_command(args))
        del args.scan_queue
        self.assertEqual([stream_pokemon.sys.executable, str(stream_pokemon.ROOT / "run.py"), "gui"],
                         stream_pokemon.manager_command(args))


if __name__ == "__main__":
    unittest.main()
