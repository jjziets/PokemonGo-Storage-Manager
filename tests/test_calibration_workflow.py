import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image, ImageChops

import run
from pokemgr.adb.device import DeviceInfo
from pokemgr.calibration.evidence import (
    confirm_calibration_evidence,
    write_calibration_evidence,
)
from pokemgr.calibration.profile import (
    PROFILE_SCHEMA_VERSION,
    CalibrationProfile,
)
from pokemgr.calibration.regions import ScreenRegions


def _info() -> DeviceInfo:
    return DeviceInfo(
        model="SM-F956B",
        serial="test-serial",
        width=968,
        height=2376,
        density=420,
    )


def _legacy_payload() -> dict:
    info = _info()
    regions = ScreenRegions.default_for_resolution(
        info.width, info.height, density=info.density
    ).to_dict()
    for field_name in (
        "storage_first_item", "storage_search_bar", "storage_search_clear",
        "map_pokeball", "map_pokemon_button", "appraisal_close_x",
    ):
        regions.pop(field_name)
    return {
        "device_model": info.model,
        "serial": info.serial,
        "resolution": info.resolution,
        "density": info.density,
        "calibrated_at": "2026-01-01T00:00:00",
        "validation_results": [],
        "regions": regions,
    }


class CalibrationProfileWorkflowTests(unittest.TestCase):
    def test_schema_v1_load_migrates_without_claiming_verification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.json"
            path.write_text(json.dumps(_legacy_payload()))

            profile = CalibrationProfile.load(path)

            self.assertEqual(PROFILE_SCHEMA_VERSION, profile.schema_version)
            self.assertEqual("phone", profile.layout)
            self.assertEqual("legacy_profile", profile.metadata["coordinate_source"])
            self.assertEqual("unverified", profile.metadata["verification_status"])
            self.assertEqual(1, profile.metadata["migrated_from_schema_version"])
            self.assertEqual((484, 2280), profile.regions.map_pokeball)
            self.assertEqual((484, 2260), profile.regions.appraisal_close_x)
            self.assertIn("map_pokeball", profile.metadata["template_filled_fields"])
            self.assertIn(
                "appraisal_close_x", profile.metadata["template_filled_fields"]
            )
            # Loading is a non-destructive in-memory migration.
            self.assertNotIn("schema_version", json.loads(path.read_text()))

    def test_migrated_profile_saves_schema_v2_layout_and_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "legacy.json"
            source.write_text(json.dumps(_legacy_payload()))
            profile = CalibrationProfile.load(source)

            saved = profile.save(Path(temp_dir) / "profiles")
            data = json.loads(saved.read_text())

            self.assertEqual(2, data["schema_version"])
            self.assertEqual("phone", data["layout"])
            self.assertEqual("legacy_profile", data["metadata"]["coordinate_source"])
            self.assertEqual("unverified", data["metadata"]["verification_status"])

    def test_default_profile_is_an_unverified_template(self):
        profile = CalibrationProfile.create_default(_info())

        self.assertEqual("phone", profile.layout)
        self.assertEqual("template", profile.metadata["coordinate_source"])
        self.assertEqual("unverified", profile.metadata["verification_status"])
        self.assertEqual("SM-F956B_968x2376", profile.metadata["template_reference"])
        self.assertEqual((484, 2260), profile.regions.appraisal_close_x)

    def test_tablet_template_scales_real_appraisal_close_x(self):
        profile = CalibrationProfile.create_default(DeviceInfo(
            model="SM-X516B",
            serial="tablet",
            width=1440,
            height=2304,
            density=280,
        ))

        self.assertEqual((720, 2191), profile.regions.appraisal_close_x)
        restored = ScreenRegions.from_dict(profile.regions.to_dict())
        self.assertEqual((720, 2191), restored.appraisal_close_x)

    def test_optional_close_x_accepts_legacy_null(self):
        data = CalibrationProfile.create_default(_info()).regions.to_dict()
        data["appraisal_close_x"] = None

        restored = ScreenRegions.from_dict(data)

        self.assertIsNone(restored.appraisal_close_x)

    def test_existing_schema_v2_profile_receives_new_optional_close_x(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = CalibrationProfile.create_default(_info())
            path = profile.save(temp_dir)
            data = json.loads(path.read_text())
            data["regions"].pop("appraisal_close_x")
            path.write_text(json.dumps(data))

            migrated = CalibrationProfile.load(path)

            self.assertEqual((484, 2260), migrated.regions.appraisal_close_x)
            self.assertIn(
                "appraisal_close_x",
                migrated.metadata["template_filled_fields"],
            )

    def test_backup_preserves_existing_profile_and_never_overwrites_collision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile = CalibrationProfile.create_default(_info())
            source = profile.save(root / "profiles")
            original = source.read_bytes()

            first = CalibrationProfile.backup_for_device(
                _info(), root / "profiles", root / "backups", "fixed"
            )
            second = CalibrationProfile.backup_for_device(
                _info(), root / "profiles", root / "backups", "fixed"
            )

            self.assertEqual(original, first.read_bytes())
            self.assertEqual(original, second.read_bytes())
            self.assertNotEqual(first, second)
            self.assertEqual(original, source.read_bytes())

    def test_evidence_writes_original_overlay_and_unverified_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Image.new("RGB", (968, 2376), "white")
            profile = CalibrationProfile.create_default(_info())

            result = write_calibration_evidence(
                profile,
                image,
                output_root=Path(temp_dir),
                source="test_fixture",
                visible_screen="appraisal",
                note="fixture only",
                captured_at="2026-08-24T12:00:00+00:00",
            )

            manifest = json.loads(result["manifest_path"].read_text())
            evidence_dir = result["manifest_path"].parent
            screenshot = Image.open(evidence_dir / "screenshot.png")
            annotated = Image.open(evidence_dir / "annotated.png").convert("RGB")
            self.assertEqual("unverified", manifest["validation"]["status"])
            self.assertEqual("template", manifest["profile"]["coordinate_source"])
            self.assertEqual("appraisal", manifest["capture"]["visible_screen"])
            self.assertIsNotNone(ImageChops.difference(screenshot, annotated).getbbox())
            self.assertEqual("unverified", result["validation_entry"]["status"])

    def test_known_truth_confirmation_updates_manifest_and_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = CalibrationProfile.create_default(_info())
            result = write_calibration_evidence(
                profile,
                Image.new("RGB", (968, 2376), "white"),
                output_root=Path(temp_dir),
                captured_at="2026-08-24T13:00:00+00:00",
            )
            truth = [
                {"species": "Zubat", "cp": 10},
                {"species": "Zorua", "cp": 882},
            ]

            manifest = confirm_calibration_evidence(
                result["manifest_path"], profile, truth, "two-item pass"
            )

            self.assertEqual("verified", manifest["validation"]["status"])
            self.assertEqual("template", profile.metadata["coordinate_source"])
            self.assertEqual("verified", profile.metadata["verification_status"])
            self.assertEqual(truth, profile.validation_results[-1]["known_truth"])

    def test_confirmation_requires_two_known_truth_results(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = CalibrationProfile.create_default(_info())
            result = write_calibration_evidence(
                profile,
                Image.new("RGB", (968, 2376), "white"),
                output_root=Path(temp_dir),
                captured_at="2026-08-24T14:00:00+00:00",
            )

            with self.assertRaisesRegex(ValueError, "At least two"):
                confirm_calibration_evidence(
                    result["manifest_path"],
                    profile,
                    [{"species": "Zubat", "cp": 10}],
                )

    def test_capture_rejects_screenshot_from_another_resolution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = CalibrationProfile.create_default(_info())

            with self.assertRaisesRegex(ValueError, "Screenshot is 1440x2304"):
                write_calibration_evidence(
                    profile,
                    Image.new("RGB", (1440, 2304), "white"),
                    output_root=Path(temp_dir),
                )

    def test_confirmation_rejects_modified_screenshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = CalibrationProfile.create_default(_info())
            result = write_calibration_evidence(
                profile,
                Image.new("RGB", (968, 2376), "white"),
                output_root=Path(temp_dir),
                captured_at="2026-08-24T14:30:00+00:00",
            )
            screenshot = result["manifest_path"].parent / "screenshot.png"
            screenshot.write_bytes(b"changed")

            with self.assertRaisesRegex(ValueError, "hash does not match"):
                confirm_calibration_evidence(
                    result["manifest_path"],
                    profile,
                    [
                        {"species": "Zubat", "cp": 10},
                        {"species": "Zorua", "cp": 882},
                    ],
                )

    def test_force_command_backs_up_before_saving_new_template(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            calibration_dir = root / "calibrations"
            cache_dir = root / "cache"
            existing = CalibrationProfile.create_default(_info())
            existing.metadata["operator_note"] = "preserve me"
            existing_path = existing.save(calibration_dir)
            previous_bytes = existing_path.read_bytes()

            adb = Mock()
            adb.get_device_info.return_value = _info()
            adb.screencap.return_value = Image.new("RGB", (968, 2376), "white")
            args = Namespace(
                force=True,
                screenshot=None,
                screen_label="map",
                note="interrupted call recovery",
                confirm_verified=None,
                known_truth=[],
            )

            with patch("pokemgr.adb.controller.ADBController", return_value=adb), \
                    patch("pokemgr.calibration.profile.CALIBRATIONS_DIR", calibration_dir), \
                    patch("pokemgr.calibration.evidence.CACHE_DIR", cache_dir):
                run.cmd_calibrate(args)

            backups = list((calibration_dir / "backups").glob("*.bak.json"))
            self.assertEqual(1, len(backups))
            self.assertEqual(previous_bytes, backups[0].read_bytes())
            replacement = json.loads(existing_path.read_text())
            self.assertEqual("template", replacement["metadata"]["coordinate_source"])
            self.assertEqual("unverified", replacement["metadata"]["verification_status"])

    def test_test_read_closes_appraisal_with_real_x_not_next_arrow(self):
        profile = CalibrationProfile.create_default(_info())
        adb = Mock()
        adb.get_device_info.return_value = _info()
        adb.screencap.side_effect = [Mock(name="detail"), Mock(name="appraisal")]
        reader = Mock()
        reader.read_detail_screen.return_value = {
            "species": "Zubat",
            "cp": 10,
            "shiny": False,
            "shadow": False,
            "favorited": False,
            "lucky": False,
            "confidence": 1.0,
        }
        reader.read_appraisal_screen.return_value = {
            "atk": 0,
            "def_": 12,
            "sta": 15,
            "confidence": 1.0,
        }
        navigator = Mock()
        navigator.detect_screen.return_value = "appraisal"
        navigator.appraisal_close_target.return_value = (484, 2260)

        with patch("pokemgr.adb.controller.ADBController", return_value=adb), \
                patch.object(CalibrationProfile, "find_for_device", return_value=profile), \
                patch("pokemgr.reader.screen.ScreenReader", return_value=reader), \
                patch("pokemgr.adb.navigator.GameNavigator", return_value=navigator):
            run.cmd_test_read(Namespace(appraise=True))

        adb.tap.assert_called_once_with(484, 2260)
        self.assertNotEqual(profile.regions.close_appraisal_target, (484, 2260))

    def test_known_truth_parser_keeps_species_names_with_spaces(self):
        self.assertEqual(
            [{"species": "Type: Null", "cp": 123}, {"species": "Zorua", "cp": 882}],
            run._parse_known_truth(["Type: Null=123", "Zorua=882"]),
        )


if __name__ == "__main__":
    unittest.main()
