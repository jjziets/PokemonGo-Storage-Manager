"""Lucky metadata must come from the same visible card, not yellow animation."""

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from pokemgr.calibration.regions import ScreenRegions
from pokemgr.reader.native_ocr import NativeFrameText, TextObservation, parse_appraisal_fields
from pokemgr.reader.screen import ScreenReader


class NativeLuckyTests(unittest.TestCase):
    def setUp(self):
        self.regions = ScreenRegions.default_for_resolution(968, 2376)
        self.name = TextObservation("Dragonite", .5, (344, 862, 281, 60))
        self.hp = TextObservation("188/188 HP", .5, (406, 979, 156, 21))
        self.label = TextObservation("LUCKY POKÉMON", .5, (344, 937, 279, 31))

    def frame(self, *observations):
        return NativeFrameText("one-frame", 968, 2376, observations, 10.)

    def parse(self, *observations):
        return parse_appraisal_fields(self.frame(*observations), self.regions)

    def test_exact_label_between_unique_name_and_hp_is_lucky(self):
        hp = replace(self.hp, bbox=(406, 1029, 156, 21))
        for text in ("LUCKY POKÉMON", "Lucky Pokemon", "LUCKY  POKÉMON"):
            self.assertIs(self.parse(self.name, hp, replace(self.label, text=text)).lucky, True)

    def test_normal_card_gap_without_a_label_is_not_lucky(self):
        self.assertIs(self.parse(self.name, self.hp).lucky, False)

    def test_missing_or_conflicting_anchors_cannot_classify_lucky(self):
        for observations in ((self.label,), (self.name, self.label), (self.hp, self.label),
                             (self.name, self.hp, replace(self.hp, text="187/187 HP")),
                             (self.name, self.hp, replace(self.name, text="Salamence")),
                             (self.name, self.hp, replace(self.name, bbox=(344, 855, 281, 60)))):
            with self.subTest(observations=observations):
                self.assertIsNone(self.parse(*observations).lucky)

    def test_unreadable_expanded_lucky_row_is_unknown(self):
        hp = replace(self.hp, bbox=(406, 1029, 156, 21))
        for text in ("LUCKY", "POKÉMON", "LUCKY P0KEMON", "NOT LUCKY POKÉMON"):
            self.assertIsNone(self.parse(self.name, hp, replace(self.label, text=text)).lucky)
        self.assertIsNone(self.parse(self.name, hp).lucky)
        self.assertIsNone(self.parse(self.name, hp, replace(self.label, confidence=.49)).lucky)

    def test_label_outside_card_or_nickname_is_not_lucky_evidence(self):
        outside = replace(self.label, bbox=(344, 350, 279, 31))
        self.assertIs(self.parse(self.name, self.hp, outside).lucky, False)
        nickname = replace(self.name, text="Lucky Pokémon")
        self.assertIs(self.parse(nickname, self.hp).lucky, False)

    def test_unfamiliar_spacing_or_text_in_normal_gap_remains_unknown(self):
        for y in (935, 1010, 1200):
            self.assertIsNone(self.parse(self.name, replace(self.hp, bbox=(406, y, 156, 21))).lucky)
        unreadable = replace(self.label, text="LUC", confidence=.2)
        self.assertIsNone(self.parse(self.name, self.hp, unreadable).lucky)

    def test_scaled_phone_geometry_is_supported_but_tablet_is_unknown(self):
        observations = tuple(replace(o, bbox=tuple(v / 2 for v in o.bbox))
                             for o in (self.name, self.hp))
        frame = NativeFrameText("half", 484, 1188, observations, 10.)
        regions = ScreenRegions.default_for_resolution(484, 1188)
        self.assertIs(parse_appraisal_fields(frame, regions).lucky, False)
        regions = ScreenRegions.default_for_resolution(1440, 2304, density=280)
        frame = NativeFrameText("tablet", 1440, 2304, (
            TextObservation("Dragonite", .5, (570, 1130, 300, 65)),
            TextObservation("188/188 HP", .5, (600, 1252, 240, 25)),
        ), 10.)
        self.assertIsNone(parse_appraisal_fields(frame, regions, density=280).lucky)

    def test_reader_uses_native_true_false_without_extra_requests_and_unknown_falls_back(self):
        profile = SimpleNamespace(regions=self.regions, density=420)
        for expected in (True, False, None):
            with self.subTest(expected=expected), patch.dict("os.environ", {"POKEMGR_NATIVE_OCR": "1"}):
                reader = ScreenReader(profile, read_size_tags=False)
                worker = reader._native_ocr = Mock()
                hp = replace(self.hp, bbox=(406, 1029, 156, 21)) if expected is not False else self.hp
                observations = (self.name, hp, self.label) if expected is True else (self.name, hp)
                worker.recognize.return_value = self.frame(*observations)
                try:
                    with patch("pokemgr.reader.screen.icons.is_lucky", return_value=True) as legacy:
                        detail = reader.read_detail_screen(Image.new("RGB", (968, 2376)), include_cp=False)
                    self.assertIs(detail["lucky"], expected if expected is not None else True)
                    self.assertEqual(legacy.call_count, int(expected is None))
                    worker.recognize.assert_called_once()
                    worker.recognize_region.assert_not_called()
                finally:
                    reader.close()


if __name__ == "__main__":
    unittest.main()
