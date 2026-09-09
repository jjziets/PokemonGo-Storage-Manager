"""Explicit GUI startup uses normal scan guards and predictable five-pass defaults."""

import io
import os
from pathlib import Path
import tempfile
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

import run
from pokemgr.adb.controller import ADBError
from pokemgr.gui.main_window import MainWindow
from pokemgr.gui.widgets.scan_control import ScanControl
from scripts import stream_pokemon


class AutoStartScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch("pokemgr.config.DATA_DIR", Path(directory)))
        self.scan_tab = ScanControl()
        self.addCleanup(self.scan_tab.deleteLater)
        self.adb = Mock(display_id=15)
        self.adb.get_device_info.return_value = SimpleNamespace(
            model="Test phone", resolution="968x2376",
        )
        self.adb.get_battery_level.return_value = 80
        self.worker = Mock(_sm=None)
        self.worker.isRunning.return_value = False
        self.worker_type = self.enterContext(patch(
            "pokemgr.gui.main_window.ScanWorker", return_value=self.worker,
        ))
        self.enterContext(patch("pokemgr.gui.main_window.ADBController", return_value=self.adb))
        self.enterContext(patch(
            "pokemgr.gui.main_window.CalibrationProfile.find_for_device", return_value=Mock(),
        ))
        self.question = self.enterContext(patch(
            "pokemgr.gui.main_window.QMessageBox.question", return_value=QMessageBox.Yes,
        ))
        self.critical = self.enterContext(patch("pokemgr.gui.main_window.QMessageBox.critical"))
        self.enterContext(patch("pokemgr.gui.main_window.QMessageBox.warning"))
        self.window = SimpleNamespace(
            adb=None, profile=None, db=Mock(), _scan_worker=None,
            scan_tab=self.scan_tab, statusBar=Mock(return_value=Mock()),
            _refresh_collection=Mock(), _apply_speed_settings=Mock(),
            _on_scan_finished=Mock(), _on_scan_failed=Mock(),
        )
        for method in ("_connect_device", "_start_full_scan", "_start_scan_worker"):
            setattr(self.window, method, MethodType(getattr(MainWindow, method), self.window))

    def test_explicit_start_resets_stale_scan_settings_and_connects_before_darkening(self):
        for name in ("normal", "shiny", "shadow", "dynamax", "gigantamax"):
            getattr(self.scan_tab, f"pass_{name}").setChecked(False)
        self.scan_tab.pass_lucky.setChecked(True)
        self.scan_tab.pass_custom.setChecked(True)
        self.scan_tab.custom_filter_input.setText("4*")
        self.scan_tab.max_pokemon_spin.setValue(10)
        self.scan_tab.skip_spin.setValue(218)
        self.scan_tab.resume_species.setText("Weezing")
        self.scan_tab.resume_cp.setValue(2178)
        self.scan_tab.unfavorite_check.setChecked(True)
        events = []
        self.adb.connect.side_effect = lambda: events.append("connected")
        self.adb.turn_phone_screen_off.side_effect = lambda: events.append("phone dark")
        self.worker.start.side_effect = lambda: events.append("worker started")

        MainWindow.start_default_scan(self.window)

        self.assertEqual(["connected", "phone dark", "worker started"], events)
        self.assertEqual(
            ["Normal", "Shiny", "Shadow", "Dynamax", "Gigantamax"],
            [scan_pass["name"] for scan_pass in self.worker.selected_passes],
        )
        self.assertEqual(
            "!shiny&!shadow&!dynamax&!gigantamax", self.worker.selected_passes[0]["query"],
        )
        self.assertEqual(0, self.worker.skip_first_n)
        self.assertEqual("", self.worker.resume_target_species)
        self.assertEqual(0, self.worker.resume_target_cp)
        self.assertEqual({"unfavorite": False, "max_pokemon": 0}, self.worker_type.call_args.kwargs)
        self.assertFalse(self.scan_tab.pass_lucky.isChecked())
        self.assertFalse(self.scan_tab.pass_custom.isChecked())
        self.question.assert_not_called()

    def test_failed_connection_cannot_start_with_stale_profile(self):
        self.window.profile = Mock()
        self.adb.connect.side_effect = ADBError("selected phone is offline")

        MainWindow.start_default_scan(self.window)

        self.worker_type.assert_not_called()
        self.worker.start.assert_not_called()
        self.adb.turn_phone_screen_off.assert_not_called()
        self.assertIsNone(self.window.adb)
        self.assertIsNone(self.window.profile)
        self.assertIn("selected phone is offline", self.scan_tab.log_view.toPlainText())

    def test_explicit_resume_preserves_saved_rows_and_populates_normal_worker_controls(self):
        MainWindow.start_default_scan(
            self.window, skip_first=78, resume_species="Zygarde", resume_cp=2575,
        )

        self.assertEqual(78, self.scan_tab.skip_spin.value())
        self.assertEqual("Zygarde", self.scan_tab.resume_species.text())
        self.assertEqual(2575, self.scan_tab.resume_cp.value())
        self.assertEqual(78, self.worker.skip_first_n)
        self.assertEqual("Zygarde", self.worker.resume_target_species)
        self.assertEqual(2575, self.worker.resume_target_cp)
        self.assertEqual(5, len(self.worker.selected_passes))
        self.assertEqual({"unfavorite": False, "max_pokemon": 0}, self.worker_type.call_args.kwargs)
        self.assertEqual([], self.window.db.mock_calls)
        self.question.assert_not_called()
        self.worker.start.assert_called_once()
        self.assertTrue(self.scan_tab.pause_btn.isEnabled())
        self.assertTrue(self.scan_tab.abort_btn.isEnabled())

    def test_explicit_start_preserves_optional_slow_cp_recovery_preference(self):
        for enabled in (False, True):
            with self.subTest(cp_animation=enabled):
                self.window._scan_worker = None
                self.scan_tab.cp_animation_check.setChecked(enabled)

                MainWindow.start_default_scan(self.window)

                self.assertEqual(enabled, self.scan_tab.cp_animation_check.isChecked())
                self.assertFalse(self.scan_tab.cp_animation_check.isEnabled())
                self.question.assert_not_called()

    def test_failed_screen_darkening_prevents_worker_start(self):
        self.adb.turn_phone_screen_off.side_effect = ADBError("stream disappeared")

        MainWindow.start_default_scan(self.window)

        self.adb.connect.assert_called_once()
        self.worker.start.assert_not_called()
        self.window._apply_speed_settings.assert_not_called()
        self.critical.assert_called_once()
        self.assertIn("stream disappeared", self.scan_tab.log_view.toPlainText())

    def test_automatic_start_requires_saved_calibration_without_creating_template(self):
        with patch(
            "pokemgr.gui.main_window.CalibrationProfile.find_for_device", return_value=None,
        ), patch("pokemgr.gui.main_window.CalibrationProfile.create_default") as create:
            MainWindow.start_default_scan(self.window)

        create.assert_not_called()
        self.assertIsNone(self.window.adb)
        self.assertIsNone(self.window.profile)
        self.worker_type.assert_not_called()
        self.worker.start.assert_not_called()
        self.adb.turn_phone_screen_off.assert_not_called()

    def test_manual_start_still_requires_confirmation_and_respects_no(self):
        self.window.adb = self.adb
        self.window.profile = Mock()
        self.question.return_value = QMessageBox.No

        self.window._start_full_scan()

        self.question.assert_called_once()
        self.assertEqual("Start Multi-Pass Scan", self.question.call_args.args[1])
        self.worker_type.assert_not_called()
        self.adb.turn_phone_screen_off.assert_not_called()

    def test_bypassing_start_summary_does_not_bypass_unfavorite_confirmation(self):
        self.window.adb = self.adb
        self.window.profile = Mock()
        self.scan_tab.unfavorite_check.setChecked(True)
        self.question.return_value = QMessageBox.No

        self.window._start_full_scan(confirm=False)

        self.question.assert_called_once()
        self.assertEqual("Confirm Unfavorite", self.question.call_args.args[1])
        self.worker_type.assert_not_called()
        self.worker.start.assert_not_called()

    def test_manual_resume_keeps_both_confirmations_and_respects_no(self):
        self.window.adb = self.adb
        self.window.profile = Mock()
        self.scan_tab.skip_spin.setValue(78)
        self.scan_tab.resume_species.setText("Zygarde")
        self.scan_tab.resume_cp.setValue(2575)
        self.question.side_effect = [QMessageBox.Yes, QMessageBox.No]

        self.window._start_full_scan()

        self.assertEqual(
            ["Start Multi-Pass Scan", "Resume Scan"],
            [call.args[1] for call in self.question.call_args_list],
        )
        self.worker.start.assert_not_called()
        self.adb.turn_phone_screen_off.assert_not_called()


class AutoStartCommandTests(unittest.TestCase):
    def test_gui_only_schedules_explicit_start_after_window_is_shown(self):
        from pokemgr.gui import app as gui_app

        for enabled in (False, True):
            with self.subTest(enabled=enabled), patch.object(
                gui_app, "QApplication",
            ) as application, patch.object(gui_app, "MainWindow") as window_type, patch(
                "PySide6.QtCore.QTimer.singleShot",
            ) as schedule:
                application.return_value.exec.return_value = 0
                events = []
                window_type.return_value.show.side_effect = lambda: events.append("shown")
                schedule.side_effect = lambda *_args: events.append("scheduled")

                with self.assertRaises(SystemExit) as stopped:
                    gui_app.run_gui(
                        start_scan=enabled, skip_first=78,
                        resume_species="Zygarde", resume_cp=2575,
                    )

                self.assertEqual(0, stopped.exception.code)
                self.assertEqual(["shown", "scheduled"] if enabled else ["shown"], events)
                window_type.return_value.start_default_scan.assert_not_called()
                if enabled:
                    self.assertEqual(0, schedule.call_args.args[0])
                    schedule.call_args.args[1]()
                    window_type.return_value.start_default_scan.assert_called_once_with(
                        skip_first=78, resume_species="Zygarde", resume_cp=2575,
                    )
                else:
                    schedule.assert_not_called()

    def test_gui_cli_only_forwards_explicit_start_flag(self):
        for flags, expected in (([], False), (["--start-scan"], True)):
            with self.subTest(flags=flags), patch.object(
                run.sys, "argv", ["run.py", "gui", *flags],
            ), patch.object(run, "ensure_dirs"), patch("pokemgr.gui.app.run_gui") as launch:
                run.main()
                launch.assert_called_once_with(
                    start_scan=expected, skip_first=0, resume_species="", resume_cp=0,
                )

    def test_gui_cli_forwards_explicit_resume_parameters(self):
        with patch.object(run.sys, "argv", [
            "run.py", "gui", "--start-scan", "--skip-first", "78",
            "--resume-species", "  Zygarde  ", "--resume-cp", "2575",
        ]), patch.object(run, "ensure_dirs"), patch("pokemgr.gui.app.run_gui") as launch:
            run.main()

        launch.assert_called_once_with(
            start_scan=True, skip_first=78, resume_species="Zygarde", resume_cp=2575,
        )

    def test_both_entrypoints_reject_invalid_resume_intent_before_device_or_gui_actions(self):
        invalid_flags = (
            ["--skip-first", "0"],
            ["--skip-first", "78"],
            ["--resume-species", "Zygarde"],
            ["--resume-cp", "2575"],
            ["--start-scan", "--skip-first", "-1"],
            ["--start-scan", "--skip-first", "10001"],
            ["--start-scan", "--skip-first", "1.5"],
            ["--start-scan", "--resume-species", "Zygarde"],
            ["--start-scan", "--skip-first", "0", "--resume-cp", "2575"],
            ["--start-scan", "--skip-first", "78", "--resume-species", "  "],
            ["--start-scan", "--skip-first", "78", "--resume-cp", "0"],
            ["--start-scan", "--skip-first", "78", "--resume-cp", "-1"],
            ["--start-scan", "--skip-first", "78", "--resume-cp", "10001"],
        )
        for module, prefix in (
            (run, ["run.py", "gui"]),
            (stream_pokemon, ["stream_pokemon.py"]),
        ):
            for flags in invalid_flags:
                with self.subTest(entrypoint=module.__name__, flags=flags), patch.object(
                    module.sys, "argv", prefix + flags,
                ), patch("sys.stderr", new_callable=io.StringIO), patch.object(
                    run, "ensure_dirs",
                ) as ensure, patch("pokemgr.gui.app.run_gui") as launch, patch.object(
                    stream_pokemon, "ADBController",
                ) as adb_type, patch.object(stream_pokemon.subprocess, "Popen") as popen:
                    with self.assertRaises(SystemExit) as stopped:
                        module.main()

                    self.assertEqual(2, stopped.exception.code)
                    ensure.assert_not_called()
                    launch.assert_not_called()
                    adb_type.assert_not_called()
                    popen.assert_not_called()

    def test_stream_launcher_only_forwards_explicit_start_to_bound_gui(self):
        for flags, expected in (
            ([], False), (["--start-scan"], True),
            (["--start-scan", "--skip-first", "78", "--resume-species", "Zygarde",
              "--resume-cp", "2575"], True),
        ):
            with self.subTest(flags=flags):
                adb = Mock(serial="chosen-phone")
                adb.get_device_info.return_value = SimpleNamespace(width=968, height=2376, density=420)
                adb.shell.side_effect = [
                    "",
                    'Display 987 (Virtual display): displayName="scrcpy"',
                    "Display #15 (activities from top to bottom):\n"
                    "  topResumedActivity=ActivityRecord{x com.nianticlabs.pokemongo/.Game}",
                ]
                adb._run.return_value.returncode = 0
                stream = Mock(stdout=io.StringIO("New display: 968x2376 (id=15)\n"))
                stream.poll.return_value = None
                manager = Mock()
                manager.poll.return_value = 0
                with patch.dict(os.environ), patch.object(
                    stream_pokemon.sys, "argv", ["stream_pokemon.py", "--serial", "chosen-phone",
                                                "--capture-backend=jpeg", *flags],
                ), patch.object(stream_pokemon.shutil, "which", return_value="/mock/scrcpy"), patch.object(
                    stream_pokemon.subprocess, "run", return_value=SimpleNamespace(returncode=1),
                ), patch.object(stream_pokemon, "ADBController", return_value=adb), patch.object(
                    stream_pokemon.subprocess, "Popen", side_effect=[stream, manager],
                ) as popen, patch.object(
                    stream_pokemon.threading, "Thread",
                    side_effect=lambda target, **_kwargs: SimpleNamespace(start=target),
                ), patch.object(stream_pokemon.time, "monotonic", return_value=0):
                    result = stream_pokemon.main()

                self.assertEqual(0, result)
                adb.connect.assert_called_once_with("chosen-phone")
                gui_call = popen.call_args_list[1]
                self.assertEqual(expected, "--start-scan" in gui_call.args[0])
                self.assertEqual("gui", gui_call.args[0][2])
                self.assertEqual(flags, gui_call.args[0][3:])
                self.assertEqual("15", gui_call.kwargs["env"]["POKEMGR_DISPLAY_ID"])
                self.assertEqual("987", gui_call.kwargs["env"]["POKEMGR_CAPTURE_DISPLAY_ID"])
                self.assertEqual("chosen-phone", gui_call.kwargs["env"]["POKEMGR_DEVICE_SERIAL"])


if __name__ == "__main__":
    unittest.main()
