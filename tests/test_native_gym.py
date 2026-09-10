"""Exact frame-local raid text establishes the gym variant with no HP row."""

# TRACEWEAVER: file-role=native-gym-tests; verifies=VER-SCAN-001; req=REQ-SCAN-001; trace=TRACE-SCAN-001
# TRACEWEAVER: file-role=native-gym-review-tests; verifies=VER-SCAN-001; req=REQ-SCAN-004; trace=TRACE-SCAN-004
from dataclasses import fields, replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from pokemgr.calibration.regions import ScreenRegions
from pokemgr.reader.native_ocr import NativeFields, NativeFrameText, TextObservation, parse_appraisal_fields
from pokemgr.reader.screen import ScreenReader


FIRST = TextObservation("Raid in progress! Your Pokémon will return to", .5, (200, 1023, 656, 31))
SECOND = TextObservation("the Gym once the raid is over.", .5, (198, 1063, 431, 30))


def parsed(observations=(FIRST, SECOND), *, scale=1):
    width, height = round(968 * scale), round(2376 * scale)
    observations = tuple(replace(item, bbox=tuple(value * scale for value in item.bbox))
                         for item in observations)
    frame = NativeFrameText("frame", width, height, observations, 10.)
    return parse_appraisal_fields(frame, ScreenRegions.default_for_resolution(width, height))


class NativeGymTests(unittest.TestCase):
    def test_exact_aligned_message_at_native_confidence_and_scaled_size(self):
        for confidence in (.5, 1.):
            for scale in (.5, 1, 1.5, 2):
                with self.subTest(confidence=confidence, scale=scale):
                    observations = tuple(replace(item, confidence=confidence) for item in (SECOND, FIRST))
                    self.assertIs(parsed(observations, scale=scale).in_gym, True)

    def test_complete_single_observation_allows_only_whitespace_case_and_accent_normalization(self):
        text = "RAID IN PROGRESS! Your Pokemon will return to\nthe Gym once the raid is over."
        self.assertTrue(parsed((TextObservation(text, .5, (198, 1023, 658, 70)),)).in_gym)

    def test_partial_generic_changed_or_conflicting_message_is_unavailable(self):
        for observations in (
            (), (FIRST,), (SECOND,),
            (replace(FIRST, text="Raid in progress!"), SECOND),
            (replace(FIRST, text="Your Pokémon will return to"), SECOND),
            (FIRST, replace(SECOND, text="the Gym once the raid is over")),
            (FIRST, replace(SECOND, text="the Gym once the raid is not over.")),
            (FIRST, SECOND, replace(SECOND, text="1500 STARDUST")),
            (FIRST, SECOND, FIRST),
        ):
            with self.subTest(observations=observations):
                self.assertFalse(parsed(observations).in_gym)

    def test_low_invalid_confidence_or_missing_line_never_borrows_evidence(self):
        for confidence in (0., .499, 1.01, float("nan"), float("inf")):
            for index in (0, 1):
                with self.subTest(confidence=confidence, index=index):
                    observations = [FIRST, SECOND]
                    observations[index] = replace(observations[index], confidence=confidence)
                    self.assertFalse(parsed(observations).in_gym)

    def test_message_outside_status_region_or_incoherent_geometry_is_rejected(self):
        for changes in (
            {"bbox": (174, 1023, 656, 31)},  # clipped left edge
            {"bbox": (200, 989, 656, 31)},
            {"bbox": (200, 1023, 710, 31)},
            {"bbox": (200, 1023, 656, 103)},
            {"bbox": (260, 1023, 600, 31)},  # different column
            {"bbox": (200, 995, 656, 20)},  # separated lines
            {"bbox": (200, 1040, 656, 31)}, # overlapping lines
        ):
            with self.subTest(changes=changes):
                self.assertFalse(parsed((replace(FIRST, **changes), SECOND)).in_gym)
        for y_offset in (-450, 600, 1100):
            with self.subTest(y_offset=y_offset):
                moved = tuple(replace(item, bbox=(item.bbox[0], item.bbox[1] + y_offset,
                                                  item.bbox[2], item.bbox[3]))
                              for item in (FIRST, SECOND))
                self.assertFalse(parsed(moved).in_gym)

    def test_flag_is_additive_and_does_not_rewrite_observed_cp_or_infer_hp(self):
        self.assertEqual("in_gym", fields(NativeFields)[-1].name)
        self.assertIs(NativeFields().in_gym, False)
        cp = TextObservation("CP128", .5, (340, 120, 240, 60))
        gym = parsed((FIRST, SECOND, cp))
        normal = parsed((cp,))
        self.assertEqual((128, -1), (gym.cp, gym.hp))
        self.assertEqual((128, -1), (normal.cp, normal.hp))
        self.assertTrue(gym.in_gym)
        self.assertFalse(normal.in_gym)

    def test_reader_reuses_one_native_request_and_copied_frame_needs_its_own_evidence(self):
        regions = ScreenRegions.default_for_resolution(968, 2376)
        with patch.dict("os.environ", {"POKEMGR_NATIVE_OCR": "1"}):
            reader = ScreenReader(SimpleNamespace(regions=regions, density=420))
        worker = Mock()
        worker.recognize.side_effect = lambda image, frame_id: NativeFrameText(
            frame_id, image.width, image.height,
            (FIRST, SECOND) if image.getpixel((0, 0)) == (255, 255, 255) else (), 10.,
        )
        reader._native_ocr = worker
        self.addCleanup(reader.close)
        image = Image.new("RGB", (968, 2376), "white")
        first = reader.native_fields(image)
        self.assertTrue(first.in_gym)
        self.assertIs(first, reader.native_fields(image))
        worker.recognize.assert_called_once()
        copied = image.copy()
        copied.putpixel((0, 0), (0, 0, 0))
        self.assertFalse(reader.native_fields(copied).in_gym)
        self.assertEqual(2, worker.recognize.call_count)
        worker.recognize_region.assert_not_called()


if __name__ == "__main__":
    unittest.main()
