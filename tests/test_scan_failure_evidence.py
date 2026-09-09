"""Saving failed appraisal pixels must not change scan progress or outcomes."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from pokemgr.calibration.profile import CalibrationProfile
from pokemgr.calibration.regions import ScreenRegions
from pokemgr.indexer.state_machine import IndexingStateMachine


class ScanFailureEvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache = Path(directory.name)
        cache_patch = patch("pokemgr.indexer.state_machine.config.CACHE_DIR", self.cache)
        cache_patch.start()
        self.addCleanup(cache_patch.stop)

        regions = ScreenRegions.default_for_resolution(968, 2376, density=420)
        profile = CalibrationProfile(
            device_model="test", serial="serial", resolution="968x2376",
            density=420, regions=regions,
        )
        self.adb, self.db = Mock(), Mock()
        self.sm = IndexingStateMachine(self.adb, profile, self.db)
        self.sm.session_id = "evidence-session"
        self.frame = Image.new("RGB", (968, 2376), (20, 40, 60))
        self.frame.paste((200, 100, 50), regions.cp_region.as_tuple())

    def test_saved_frame_crop_and_reason_use_resume_offset_plus_visited_positions(self):
        self.sm.skip_first_n = 200
        self.sm.visited_count = 22
        self.sm.count = 17

        self.sm._save_failed_appraisal(self.frame, "no exact CP observation")

        destination = self.cache / "scan_failures" / "evidence-session"
        self.assertEqual({
            "position_00223_appraisal.png", "position_00223_cp.png", "position_00223.json",
        }, {path.name for path in destination.iterdir()})
        metadata = json.loads((destination / "position_00223.json").read_text())
        self.assertEqual({"position": 223, "reason": "no exact CP observation",
                          "phase": "appraisal"}, metadata)
        with Image.open(destination / "position_00223_appraisal.png") as saved:
            self.assertEqual(self.frame.size, saved.size)
            self.assertEqual(self.frame.tobytes(), saved.tobytes())
        with Image.open(destination / "position_00223_cp.png") as saved:
            expected = self.frame.crop(self.sm.regions.cp_region.as_tuple())
            self.assertEqual(expected.size, saved.size)
            self.assertEqual(expected.tobytes(), saved.tobytes())
        self.assertEqual(22, self.sm.visited_count)
        self.assertEqual(17, self.sm.count)
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()
        self.db.insert_pokemon.assert_not_called()

    def test_negative_resume_offset_is_clamped_to_first_storage_position(self):
        self.sm.skip_first_n = -50

        self.sm._save_failed_appraisal(self.frame, "unreadable")

        metadata = json.loads((
            self.cache / "scan_failures" / "evidence-session" / "position_00001.json"
        ).read_text())
        self.assertEqual(1, metadata["position"])

    def test_unrecognized_dialog_saves_actual_modal_with_distinct_phase(self):
        self.sm._save_failed_appraisal(self.frame, "unknown dialog", phase="cp_preview")

        destination = self.cache / "scan_failures" / "evidence-session"
        self.assertTrue((destination / "position_00001_cp_preview.png").exists())
        self.assertFalse((destination / "position_00001_appraisal.png").exists())
        metadata = json.loads((destination / "position_00001.json").read_text())
        self.assertEqual("cp_preview", metadata["phase"])
        self.assertEqual("unknown dialog", metadata["reason"])
        self.adb.tap.assert_not_called()

    def test_missing_frame_creates_no_evidence_directory(self):
        self.sm._save_failed_appraisal(None, "no frame available")

        self.assertFalse((self.cache / "scan_failures").exists())

    def test_directory_image_and_metadata_write_failures_are_logged_without_raising(self):
        for target in ("pathlib.Path.mkdir", "PIL.Image.Image.save", "pathlib.Path.write_text"):
            with self.subTest(target=target), patch(
                target, side_effect=OSError("disk unavailable"),
            ), self.assertLogs("pokemgr.indexer.state_machine", level="ERROR") as logged:
                self.sm._save_failed_appraisal(self.frame, "unreadable")

                self.assertIn("Could not save skipped-position evidence", logged.output[0])
                self.assertFalse(self.sm._abort)

    def test_evidence_write_failure_preserves_normal_invalid_acquisition_result(self):
        self.sm._wait_for_stable_appraisal = Mock(return_value=(self.frame, "stable"))
        self.sm._read_appraisal_snapshot = Mock(return_value=({}, {}))

        with patch("PIL.Image.Image.save", side_effect=OSError("disk full")), self.assertLogs(
            "pokemgr.indexer.state_machine", level="ERROR",
        ):
            decision, frame, failure_kind, reason = self.sm._acquire_validated_snapshot()

        self.assertIsNone(decision)
        self.assertIs(frame, self.frame)
        self.assertEqual("invalid", failure_kind)
        self.assertIn("invalid IVs", reason)
        self.assertFalse(self.sm._abort)
        self.assertEqual(3, self.sm._read_appraisal_snapshot.call_count)
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
