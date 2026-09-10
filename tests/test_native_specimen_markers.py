# TRACEWEAVER: file-role=specimen-marker-parser-tests; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
"""Optional specimen markers use exact text and anchors from one immutable frame."""

from dataclasses import FrozenInstanceError, replace
import unittest

from pokemgr.calibration.regions import ScreenRegions
from pokemgr.reader.native_ocr import (
    NativeFields, NativeFrameText, TextObservation, parse_appraisal_fields,
)


# TRACEWEAVER: verifies=VER-SCAN-001; req=REQ-SCAN-003; trace=TRACE-SCAN-003
class NativeSpecimenMarkerTests(unittest.TestCase):
    def setUp(self):
        self.regions = ScreenRegions.default_for_resolution(968, 2376)

    def observation(self, text, field="weight", confidence=1.0):
        boxes = {
            "weight": (115., 1124., 171., 40.),
            "weight_label": (143., 1181., 113., 20.),
            "height": (740., 1125., 112., 37.),
            "height_label": (745., 1181., 105., 20.),
            "caught": (65., 2166., 820., 39.),
            "resources": (115., 1420., 171., 40.),
        }
        return TextObservation(text, confidence, boxes[field])

    def parse(self, *items):
        frame = NativeFrameText("current-frame", 968, 2376, tuple(items), 12.0)
        return parse_appraisal_fields(frame, self.regions)

    def markers(self, fields):
        return fields.specimen_weight, fields.specimen_height, fields.caught_date

    def labels(self):
        return (self.observation("WEIGHT", "weight_label"),
                self.observation("HEIGHT", "height_label"))

    def test_exact_markers_are_normalized_without_changing_primary_fields(self):
        fields = self.parse(
            *self.labels(), self.observation("24,34kg"),
            self.observation("0.91 m", "height"),
            self.observation("This Larvesta was caught on 2026/06/20", "caught"),
            TextObservation("CP832", 1., (330., 120., 240., 60.)),
            TextObservation("94 / 94 HP", 1., (405., 978., 170., 24.)),
            TextObservation("Nickname", 1., (350., 855., 250., 55.)),
        )

        self.assertEqual(("24.34", "0.91", "2026-06-20"), self.markers(fields))
        self.assertEqual((832, 94, "Nickname", "Larvesta"),
                         (fields.cp, fields.hp, fields.display_name, fields.caught_species))
        with self.assertRaises(FrozenInstanceError):
            fields.specimen_weight = "37.48"

    def test_integer_decimal_and_locale_equivalents_are_the_same_marker(self):
        for tokens, expected in ((("1kg", "1.0kg", "1,000 kg"), "1"),
                                 (("24.34kg", "24,340kg"), "24.34"),
                                 (("0.010kg", "0,01kg"), "0.01")):
            with self.subTest(tokens=tokens):
                fields = self.parse(*self.labels(), *(self.observation(t) for t in tokens))
                self.assertEqual(expected, fields.specimen_weight)

    def test_conflicting_measurements_only_invalidate_their_own_field(self):
        fields = self.parse(
            *self.labels(), self.observation("24.34kg"), self.observation("37.48kg"),
            self.observation("0.91m", "height"),
            self.observation("This Larvesta was caught on 2026/06/20", "caught"),
        )
        self.assertEqual(("", "0.91", "2026-06-20"), self.markers(fields))
        fields = self.parse(*self.labels(), self.observation("24.34kg"),
                            self.observation("0.91m", "height"),
                            self.observation("1.12m", "height"))
        self.assertEqual(("24.34", "", ""), self.markers(fields))

    def test_value_and_its_label_both_require_high_confidence(self):
        for field in ("weight", "weight_label", "height", "height_label", "caught"):
            with self.subTest(field=field):
                items = {
                    "weight": self.observation("24.34kg"),
                    "weight_label": self.observation("WEIGHT", "weight_label"),
                    "height": self.observation("0.91m", "height"),
                    "height_label": self.observation("HEIGHT", "height_label"),
                    "caught": self.observation("This Larvesta was caught on 2026/06/20", "caught"),
                }
                items[field] = replace(items[field], confidence=.849)
                fields = self.parse(*items.values())
                unavailable = ("specimen_weight" if field.startswith("weight") else
                               "specimen_height" if field.startswith("height") else "caught_date")
                self.assertEqual("", getattr(fields, unavailable))
                items[field] = replace(items[field], confidence=.85)
                self.assertTrue(getattr(self.parse(*items.values()), unavailable))

    def test_unit_and_digits_must_be_in_one_exact_observation(self):
        for token in ("24.34", "24.34 lbs", "24.34mg", "24.34kg extra", "+24.34kg",
                      "-24.34kg", "024.34kg", "24.3Okg", "24 34kg", "24,3.4kg",
                      "２４.３４kg", "24.3456kg", "1e2kg", "NaNkg", "0.00kg"):
            with self.subTest(token=token):
                self.assertEqual("", self.parse(*self.labels(), self.observation(token)).specimen_weight)
        self.assertEqual("", self.parse(*self.labels(), self.observation("24.34"),
                                        self.observation("kg")).specimen_weight)

    def test_nonfinite_or_out_of_range_confidence_is_not_high_confidence(self):
        for confidence in (float("nan"), float("inf"), 1.01):
            with self.subTest(confidence=confidence):
                fields = self.parse(*self.labels(),
                                    self.observation("24.34kg", confidence=confidence),
                                    self.observation("This Larvesta was caught on 2026/06/20",
                                                     "caught", confidence))
                self.assertEqual(("", "", ""), self.markers(fields))

    def test_measurement_requires_exact_label_and_correct_column(self):
        for label in ("HEAVIEST", "CANDY", "WE1GHT", "POWER UP", "HEIGHT", ""):
            with self.subTest(label=label):
                fields = self.parse(self.observation(label, "weight_label"),
                                    self.observation("24.34kg"))
                self.assertEqual("", fields.specimen_weight)
        fields = self.parse(*self.labels(), self.observation("24.34kg", "height"),
                            self.observation("0.91m", "weight"))
        self.assertEqual(("", "", ""), self.markers(fields))

    def test_resources_and_misaligned_values_cannot_become_measurements(self):
        for box in ((115., 1420., 171., 40.), (115., 1020., 171., 40.),
                    (230., 1124., 171., 40.), (115., 1180., 171., 40.),
                    (115., 1090., 171., 200.)):
            with self.subTest(box=box):
                fields = self.parse(*self.labels(),
                                    replace(self.observation("24.34kg"), bbox=box))
                self.assertEqual("", fields.specimen_weight)
        fields = self.parse(self.observation("24.34kg", "resources"),
                            replace(self.observation("WEIGHT", "weight_label"),
                                    bbox=(143., 1477., 113., 20.)))
        self.assertEqual("", fields.specimen_weight)

    def test_lucky_card_shift_keeps_the_same_bounded_anchor_relationship(self):
        items = (*self.labels(), self.observation("24.34kg"),
                 self.observation("0.91m", "height"))
        shifted = tuple(replace(o, bbox=(o.bbox[0], o.bbox[1] + 50, *o.bbox[2:]))
                        for o in items)
        self.assertEqual(("24.34", "0.91", ""), self.markers(self.parse(*shifted)))

    def test_scaled_geometry_preserves_measurements(self):
        items = (*self.labels(), self.observation("24.34kg"),
                 self.observation("0.91m", "height"))
        scaled = tuple(replace(o, bbox=tuple(value * 2 for value in o.bbox)) for o in items)
        frame = NativeFrameText("large-frame", 1936, 4752, scaled, 12.)
        fields = parse_appraisal_fields(frame, ScreenRegions.default_for_resolution(1936, 4752))
        self.assertEqual(("24.34", "0.91", ""), self.markers(fields))

    def test_caught_date_requires_whole_valid_calendar_date_in_same_line(self):
        for token in ("This Larvesta was caught on 2026/02/29",
                      "This Larvesta was caught on 2026/13/01",
                      "This Larvesta was caught on 2026/06/31",
                      "This Larvesta was caught on 2026/6/20",
                      "This Larvesta was caught on 2026-06-20",
                      "This Larvesta was caught on 2026/06/2O",
                      "This Larvesta was caught on 2026/06/200",
                      "This Larvesta was hatched on 2026/06/20",
                      "2026/06/20", "Caught on 2026/06/20",
                      "This Larvesta was caught on 0000/06/20"):
            with self.subTest(token=token):
                self.assertEqual("", self.parse(self.observation(token, "caught")).caught_date)
        self.assertEqual("2024-02-29", self.parse(self.observation(
            "This Larvesta was caught on 2024/02/29.", "caught")).caught_date)
        self.assertEqual("", self.parse(
            self.observation("This Larvesta was caught on", "caught"),
            self.observation("2026/06/20", "caught")).caught_date)

    def test_caught_date_conflict_and_species_conflict_are_unavailable(self):
        original = self.observation("This Larvesta was caught on 2026/06/20", "caught")
        for conflicting in ("This Larvesta was caught on 2026/07/31",
                            "This Quagsire was caught on 2026/06/20"):
            with self.subTest(conflicting=conflicting):
                self.assertEqual("", self.parse(original,
                    self.observation(conflicting, "caught")).caught_date)
        self.assertEqual("2026-06-20", self.parse(original, original).caught_date)

    def test_date_outside_bubble_or_in_oversized_observation_is_unavailable(self):
        for box in ((65., 1400., 820., 39.), (0., 2166., 968., 39.),
                    (65., 2150., 820., 200.)):
            with self.subTest(box=box):
                observation = replace(self.observation(
                    "This Larvesta was caught on 2026/06/20", "caught"), bbox=box)
                self.assertEqual("", self.parse(observation).caught_date)

    def test_missing_markers_never_reuse_an_earlier_frames_values(self):
        first = self.parse(*self.labels(), self.observation("24.34kg"),
                           self.observation("This Larvesta was caught on 2026/06/20", "caught"))
        self.assertEqual(("24.34", "", "2026-06-20"), self.markers(first))
        self.assertEqual(("", "", ""), self.markers(self.parse()))
        self.assertEqual(("", "", ""), self.markers(NativeFields(832, 94, "Larvesta")))


if __name__ == "__main__":
    unittest.main()
