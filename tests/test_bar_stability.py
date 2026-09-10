"""Bar animation must settle independently of whole-card pixel averages."""

# TRACEWEAVER: file-role=iv-bar-settling-tests; verifies=VER-SCAN-001; req=REQ-SCAN-001; trace=TRACE-SCAN-001
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

from pokemgr.calibration.regions import BBox
from pokemgr.reader.bars import appraisal_bars_stable, read_bars_dynamic, read_iv_bar


ORANGE, PINK, GRAY = (242, 166, 74), (221, 125, 129), (226, 226, 224)


def appraisal(values=(14, 10, 15), *, scale=1, attack_pixels=None,
              full_attack_color=None, offset_y=0):
    image = Image.new("RGB", (round(968 * scale), round(2376 * scale)), "white")
    draw = ImageDraw.Draw(image)
    boxes = []
    for index, value in enumerate(values):
        x, y = round(116 * scale), round((1842 + 89 * index + offset_y) * scale)
        width, height = round(333 * scale), round(20 * scale)
        box = BBox(x, y, width, height)
        boxes.append(box)
        draw.rectangle((x, y, x + width - 1, y + height - 1), fill=GRAY)
        pixels = round(width * value / 15)
        if index == 0 and attack_pixels is not None:
            pixels = round(attack_pixels * scale)
        if pixels:
            color = PINK if value == 15 else ORANGE
            if index == 0 and full_attack_color is not None:
                color = full_attack_color
            draw.rectangle((x, y, x + pixels - 1, y + height - 1), fill=color)
        for third in (1, 2):
            divider = x + round(width * third / 3)
            draw.rectangle((divider, y, divider + max(1, round(2 * scale)) - 1, y + height - 1), fill="white")
    return image, boxes


class BarStabilityTests(unittest.TestCase):
    def test_unchanged_values_including_zero_full_and_scaled_bars_are_stable(self):
        for scale in (.5, 1, 1.5):
            for values in ((0, 5, 15), (14, 10, 15), (15, 15, 15)):
                with self.subTest(scale=scale, values=values):
                    first, _ = appraisal(values, scale=scale)
                    second, _ = appraisal(values, scale=scale)
                    self.assertTrue(appraisal_bars_stable(first, second))

    def test_live_failure_shape_blocks_orange_fourteen_to_pink_fifteen(self):
        first, _ = appraisal((14, 10, 15))
        second, _ = appraisal((15, 10, 15))
        self.assertEqual((14, 15), (read_bars_dynamic(first)["atk"], read_bars_dynamic(second)["atk"]))
        self.assertFalse(appraisal_bars_stable(first, second))

    def test_fill_motion_within_the_same_rounded_iv_is_not_stable(self):
        first, _ = appraisal(attack_pixels=309)
        second, _ = appraisal(attack_pixels=315)
        self.assertEqual(14, read_bars_dynamic(first)["atk"])
        self.assertEqual(14, read_bars_dynamic(second)["atk"])
        self.assertFalse(appraisal_bars_stable(first, second))

    def test_maximum_color_animation_is_rejected_even_if_both_values_are_fifteen(self):
        first, _ = appraisal((15, 10, 15), full_attack_color=ORANGE)
        second, _ = appraisal((15, 10, 15))
        self.assertEqual(15, read_bars_dynamic(first)["atk"])
        self.assertEqual(15, read_bars_dynamic(second)["atk"])
        self.assertFalse(appraisal_bars_stable(first, second))

    def test_minor_compression_noise_and_one_pixel_fill_jitter_are_allowed(self):
        first, _ = appraisal(attack_pixels=311)
        jitter, _ = appraisal(attack_pixels=312)
        self.assertTrue(appraisal_bars_stable(first, jitter))
        data = np.asarray(first).astype(np.int16)
        noise = np.random.default_rng(42).integers(-2, 3, size=data.shape, dtype=np.int16)
        second = Image.fromarray(np.clip(data + noise, 0, 255).astype(np.uint8))
        self.assertTrue(appraisal_bars_stable(first, second))

    def test_changed_defense_hp_or_bar_position_is_rejected(self):
        first, _ = appraisal()
        for values, offset_y in (((14, 11, 15), 0), ((14, 10, 14), 0), ((14, 10, 15), 6)):
            with self.subTest(values=values, offset_y=offset_y):
                second, _ = appraisal(values, offset_y=offset_y)
                self.assertFalse(appraisal_bars_stable(first, second))

    def test_calibrated_fallback_covers_bars_outside_dynamic_search(self):
        first, boxes = appraisal(offset_y=-600)
        second, _ = appraisal(offset_y=-600)
        self.assertIsNone(read_bars_dynamic(first))
        self.assertFalse(appraisal_bars_stable(first, second))
        self.assertTrue(appraisal_bars_stable(first, second, fallback_regions=boxes))
        animated, _ = appraisal(offset_y=-600, attack_pixels=315)
        self.assertEqual(14, read_iv_bar(animated, boxes[0])[0])
        self.assertFalse(appraisal_bars_stable(first, animated, fallback_regions=boxes))

    def test_missing_blank_or_incomplete_fallback_is_not_stable_zero_evidence(self):
        first, boxes = appraisal()
        blank = Image.new("RGB", first.size, "white")
        self.assertFalse(appraisal_bars_stable(blank, blank.copy(), fallback_regions=boxes))
        self.assertFalse(appraisal_bars_stable(first, blank))
        for fallback in (boxes[:2], [BBox(-1, 0, 100, 20)] * 3,
                         [BBox(0, 0, 0, 20)] * 3, [BBox(0, 0, 100, 20)] * 3):
            with self.subTest(fallback=fallback):
                with patch("pokemgr.reader.bars.find_bars", return_value=None):
                    self.assertFalse(appraisal_bars_stable(first, first.copy(), fallback_regions=fallback))

    def test_reused_image_or_changed_resolution_is_unavailable(self):
        first, _ = appraisal()
        second, _ = appraisal(scale=.5)
        self.assertFalse(appraisal_bars_stable(first, first))
        self.assertFalse(appraisal_bars_stable(first, second))


if __name__ == "__main__":
    unittest.main()
