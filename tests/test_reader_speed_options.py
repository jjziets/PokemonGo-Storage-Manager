"""Scanner shortcuts must preserve standalone readers and exact recovery."""

import unittest
from unittest.mock import Mock, call, patch

from PIL import Image

from pokemgr.calibration.profile import CalibrationProfile
from pokemgr.calibration.regions import ScreenRegions
from pokemgr.indexer.state_machine import IndexingStateMachine
from pokemgr.reader.screen import ScreenReader


class ReaderSpeedOptionsTests(unittest.TestCase):
    def setUp(self):
        self.profile = CalibrationProfile(
            device_model="test", serial="serial", resolution="968x2376",
            density=420,
            regions=ScreenRegions.default_for_resolution(968, 2376, density=420),
        )
        self.image = Image.new("RGB", (968, 2376))
        self.cp = self.enterContext(patch(
            "pokemgr.reader.screen.ocr.read_cp", return_value=(4287, 0.8),
        ))
        self.labels = self.enterContext(patch(
            "pokemgr.reader.screen.ocr.read_size_label",
            side_effect=["HEAVIEST", "TALLEST"],
        ))
        for target, value in (
            ("ocr.read_species_name", ("Dragonite", 0.95)),
            ("match_species_name", "Dragonite"),
            ("icons.is_favorited", True),
            ("icons.is_lucky", False),
            ("_detect_gender", "male"),
        ):
            self.enterContext(patch(
                f"pokemgr.reader.screen.{target}", return_value=value,
            ))

    def test_standalone_reader_retains_full_cp_and_size_reading(self):
        reader = ScreenReader(self.profile)

        result = reader.read_detail_screen(self.image)

        self.cp.assert_called_once_with(
            self.image, self.profile.regions.cp_region, expected_cps=None, fast=False,
        )
        self.assertEqual("HEAVIEST", result["weight_tag"])
        self.assertEqual("TALLEST", result["height_tag"])
        self.assertEqual(2, self.labels.call_count)

    def test_fast_cp_applies_only_to_primary_detail_read(self):
        reader = ScreenReader(self.profile, fast_cp=True)

        reader.read_detail_screen(self.image)
        reader.read_cp(self.image, expected_cps={4287, 4313})
        reader.read_cp(self.image)

        self.assertEqual([
            call(self.image, self.profile.regions.cp_region, expected_cps=None, fast=True),
            call(self.image, self.profile.regions.cp_region, expected_cps={4287, 4313}, fast=False),
            call(self.image, self.profile.regions.cp_region, expected_cps=None, fast=False),
        ], self.cp.call_args_list)

    def test_scanner_honors_size_tags_setting_and_preserves_other_metadata(self):
        for size_tags in (False, True):
            with self.subTest(size_tags=size_tags), patch(
                "pokemgr.indexer.state_machine.config.CAPTURE_SIZE_TAGS", size_tags,
            ):
                self.labels.reset_mock(side_effect=True)
                self.labels.side_effect = ["HEAVIEST", "TALLEST"]
                self.cp.reset_mock()
                scanner = IndexingStateMachine(Mock(), self.profile, Mock())

                result = scanner.reader.read_detail_screen(self.image)

                self.cp.assert_called_once_with(
                    self.image, self.profile.regions.cp_region, expected_cps=None, fast=True,
                )
                self.assertEqual(size_tags, scanner.capture_size_tags)
                self.assertEqual("Dragonite", result["species"])
                self.assertEqual("male", result["gender"])
                self.assertTrue(result["favorited"])
                self.assertEqual("HEAVIEST" if size_tags else "", result["weight_tag"])
                self.assertEqual("TALLEST" if size_tags else "", result["height_tag"])
                self.assertEqual(2 if size_tags else 0, self.labels.call_count)


if __name__ == "__main__":
    unittest.main()
