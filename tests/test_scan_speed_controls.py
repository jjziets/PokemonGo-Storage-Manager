"""Displayed speed controls must drive the actual stable scanner settings."""

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from pokemgr import config
from pokemgr.gui.main_window import MainWindow
from pokemgr.gui.widgets.scan_control import ScanControl


class ScanSpeedControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(config, "DATA_DIR", self.directory))

    def widget(self):
        widget = ScanControl()
        self.addCleanup(widget.deleteLater)
        return widget

    def test_frame_interval_is_the_only_visible_editable_speed_delay(self):
        widget = self.widget()

        self.assertEqual(0.15, widget.frame_interval_spin.value())
        self.assertEqual(0.15, widget.frame_interval_spin.minimum())
        self.assertEqual(1.0, widget.frame_interval_spin.maximum())
        self.assertEqual(0.05, widget.frame_interval_spin.singleStep())
        self.assertIn("Capture time counts", widget.frame_interval_spin.toolTip())
        self.assertIn("0–0.10 s", widget.frame_interval_spin.toolTip())
        self.assertIn("Two stable frames", widget.frame_interval_spin.toolTip())
        labels = {label.text() for label in widget.findChildren(QLabel)}
        self.assertIn("Frame interval:", labels)
        for old in ("Appraise delay:", "Swipe delay:", "Bar wait:", "Swipe ms:"):
            self.assertNotIn(old, labels)
        widget.set_scanning(True)
        self.assertTrue(widget.frame_interval_spin.isEnabled())

    def test_old_values_are_ignored_without_rewriting_or_losing_preferences(self):
        text = json.dumps({
            "appraise_delay": 2.9, "swipe_delay": 0.95, "bar_wait": 2.8,
            "swipe_ms": 320, "anti_detection": False, "calc_cp": False,
            "cp_animation": False, "size_tags": True,
        }, indent=4) + "\n"
        path = self.directory / "speed_settings.json"
        path.write_text(text)

        widget = self.widget()

        self.assertEqual(0.15, widget.frame_interval_spin.value())
        self.assertEqual("Swipe: calibrated", widget.swipe_duration_label.text())
        self.assertFalse(widget.anti_detection_check.isChecked())
        self.assertFalse(widget.calc_cp_check.isChecked())
        self.assertFalse(widget.cp_animation_check.isChecked())
        self.assertTrue(widget.size_tags_check.isChecked())
        self.assertEqual(text, path.read_text())

    def test_new_interval_saves_loads_and_resets_without_dropping_other_preferences(self):
        path = self.directory / "speed_settings.json"
        path.write_text(json.dumps({"swipe_ms": 320, "future_preference": "keep"}))
        widget = self.widget()
        widget.frame_interval_spin.setValue(0.45)
        widget.calc_cp_check.setChecked(False)
        widget.size_tags_check.setChecked(True)

        widget._save_speed_settings()
        saved = json.loads(path.read_text())

        self.assertEqual(0.45, saved["frame_interval"])
        self.assertEqual("keep", saved["future_preference"])
        self.assertNotIn("swipe_ms", saved)
        self.assertFalse(saved["calc_cp"])
        self.assertTrue(saved["size_tags"])
        restored = self.widget()
        self.assertEqual(0.45, restored.frame_interval_spin.value())
        self.assertFalse(restored.calc_cp_check.isChecked())
        self.assertTrue(restored.size_tags_check.isChecked())
        restored._reset_speed_settings()
        self.assertEqual(0.15, restored.frame_interval_spin.value())
        self.assertFalse(path.exists())

    def test_sync_changes_real_frame_interval_and_never_overrides_calibration(self):
        fields = (
            "STABLE_FRAME_INTERVAL", "DELAY_AFTER_APPRAISE_TAP", "DELAY_AFTER_SWIPE",
            "DEFAULT_SWIPE_DURATION_MS", "BAR_WAIT_MAX", "ANTI_DETECTION_ENABLED",
            "USE_CALCULATED_CP", "USE_CP_ANIMATION_RECOVERY", "CAPTURE_SIZE_TAGS",
        )
        before = {name: getattr(config, name) for name in fields}
        self.enterContext(patch.multiple(config, **before))
        widget = self.widget()
        widget.frame_interval_spin.setValue(0.65)
        widget.set_swipe_duration(300)

        MainWindow._sync_speed_to_config(SimpleNamespace(scan_tab=widget))

        self.assertEqual((0.65, 0.10), config.STABLE_FRAME_INTERVAL)
        for old in ("DELAY_AFTER_APPRAISE_TAP", "DELAY_AFTER_SWIPE",
                    "DEFAULT_SWIPE_DURATION_MS", "BAR_WAIT_MAX"):
            self.assertEqual(before[old], getattr(config, old))
        self.assertEqual("Swipe: 300 ms (calibrated)", widget.swipe_duration_label.text())

    def test_calibrated_swipe_label_only_accepts_a_positive_real_integer(self):
        widget = self.widget()
        widget.set_swipe_duration(300)
        self.assertEqual("Swipe: 300 ms (calibrated)", widget.swipe_duration_label.text())
        for value in (None, Mock(), True, "300", 300.0, 0, -1):
            with self.subTest(value=value):
                widget.set_swipe_duration(value)
                self.assertEqual("Swipe: calibrated", widget.swipe_duration_label.text())

    def test_connection_displays_loaded_calibration_duration(self):
        widget = self.widget()
        adb = Mock(display_id=None)
        adb.get_device_info.return_value = SimpleNamespace(model="Test", resolution="968x2376")
        adb.get_battery_level.return_value = 80
        profile = SimpleNamespace(regions=SimpleNamespace(swipe_duration_ms=300))
        window = SimpleNamespace(
            scan_tab=widget, _refresh_collection=Mock(), statusBar=Mock(return_value=Mock()),
        )
        with patch("pokemgr.gui.main_window.ADBController", return_value=adb), patch(
            "pokemgr.gui.main_window.CalibrationProfile.find_for_device", return_value=profile,
        ):
            self.assertTrue(MainWindow._connect_device(window, show_errors=False))
        self.assertEqual("Swipe: 300 ms (calibrated)", widget.swipe_duration_label.text())

    def test_start_refreshes_swipe_duration_from_current_profile_before_worker(self):
        widget = self.widget()
        profile = SimpleNamespace(regions=SimpleNamespace(swipe_duration_ms=300))
        worker = Mock(_sm=None)
        seen = []
        worker.start.side_effect = lambda: seen.append(widget.swipe_duration_label.text())
        window = SimpleNamespace(
            profile=profile, adb=Mock(display_id=None), scan_tab=widget,
            _scan_worker=worker, _apply_speed_settings=Mock(),
            _on_scan_finished=Mock(), _on_scan_failed=Mock(),
            statusBar=Mock(return_value=Mock()),
        )
        MainWindow._start_scan_worker(window)
        self.assertEqual(["Swipe: 300 ms (calibrated)"], seen)
        self.assertEqual(300, profile.regions.swipe_duration_ms)


if __name__ == "__main__":
    unittest.main()
