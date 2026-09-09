"""Ambiguous CP recovery defaults on and preserves an explicit saved opt-out."""

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from pokemgr import config
from pokemgr.gui.main_window import MainWindow
from pokemgr.gui.widgets.scan_control import ScanControl


class CpRecoverySettingsTests(unittest.TestCase):
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

    def test_new_setting_defaults_to_recovery_for_unresolved_cp(self):
        widget = self.widget()

        self.assertTrue(config.USE_CP_ANIMATION_RECOVERY)
        self.assertTrue(config.cp_animation_recovery_enabled())
        self.assertTrue(ScanControl.SPEED_DEFAULTS["cp_animation"])
        self.assertTrue(widget.cp_animation_check.isChecked())
        self.assertEqual(
            "Try taps and previews for unresolved CP (slower)",
            widget.cp_animation_check.text(),
        )
        self.assertTrue(widget.calc_cp_check.isChecked())
        self.assertIn("only when HP + IVs", widget.cp_animation_check.toolTip())
        self.assertIn("favorited and skipped", widget.cp_animation_check.toolTip())

    def test_existing_settings_without_new_option_enable_unresolved_cp_recovery(self):
        saved = {"calc_cp": True, "swipe_ms": 320, "size_tags": False}
        path = self.directory / "speed_settings.json"
        path.write_text(json.dumps(saved))

        widget = self.widget()

        self.assertTrue(widget.cp_animation_check.isChecked())
        self.assertTrue(widget.calc_cp_check.isChecked())
        self.assertEqual(0.15, widget.frame_interval_spin.value())
        self.assertEqual("Swipe: calibrated", widget.swipe_duration_label.text())
        self.assertEqual(saved, json.loads(path.read_text()))

    def test_explicit_choice_persists_and_reset_restores_recovery(self):
        for enabled in (False, True):
            with self.subTest(cp_animation=enabled):
                widget = self.widget()
                widget.cp_animation_check.setChecked(enabled)
                widget._save_speed_settings()
                path = self.directory / "speed_settings.json"

                self.assertIs(enabled, json.loads(path.read_text())["cp_animation"])
                restored = self.widget()
                self.assertEqual(enabled, restored.cp_animation_check.isChecked())

                restored._reset_speed_settings()

                self.assertTrue(restored.cp_animation_check.isChecked())
                self.assertFalse(path.exists())
                self.assertTrue(self.widget().cp_animation_check.isChecked())

    def test_gui_sync_updates_new_worker_policy_from_checkbox(self):
        widget = self.widget()
        names = (
            "STABLE_FRAME_INTERVAL",
            "DELAY_AFTER_APPRAISE_TAP", "DELAY_AFTER_SWIPE",
            "ANTI_DETECTION_ENABLED", "DEFAULT_SWIPE_DURATION_MS", "BAR_WAIT_MAX",
            "USE_CALCULATED_CP", "USE_CP_ANIMATION_RECOVERY", "CAPTURE_SIZE_TAGS",
        )
        self.enterContext(patch.multiple(config, **{name: getattr(config, name) for name in names}))
        window = SimpleNamespace(scan_tab=widget)

        for enabled in (True, False):
            with self.subTest(cp_animation=enabled):
                widget.cp_animation_check.setChecked(enabled)
                MainWindow._sync_speed_to_config(window)
                self.assertEqual(enabled, config.USE_CP_ANIMATION_RECOVERY)
                self.assertEqual(enabled, config.cp_animation_recovery_enabled())


if __name__ == "__main__":
    unittest.main()
