"""Pixel optimizations preserve bar geometry and signed region differences."""
import unittest

import numpy as np
from PIL import Image, ImageDraw

from pokemgr.indexer.snapshot import (
    _STABILITY_ROIS, appraisal_region_diffs, appraisal_frames_stable,
)
from pokemgr.reader.bars import find_bars


class ScanPixelSpeedTests(unittest.TestCase):
    def test_bar_color_boundaries_preserve_three_bounding_boxes(self):
        accepted = [(226, 146, 56), (254, 184, 99), (206, 111, 111),
                    (239, 144, 144), (219, 219, 219), (231, 231, 231),
                    (219, 228, 219)]
        rejected = [(225, 146, 56), (255, 184, 99), (226, 145, 56),
                    (226, 185, 56), (226, 146, 55), (226, 146, 100),
                    (205, 111, 111), (240, 144, 144), (206, 110, 111),
                    (206, 145, 111), (206, 111, 110), (206, 111, 145),
                    (218, 218, 218), (232, 232, 232), (219, 229, 219)]
        for size in [(968, 2376), (484, 1188), (1440, 2560)]:
            sx, sy = size[0] / 968, size[1] / 2376
            for color in accepted + rejected:
                with self.subTest(size=size, color=color):
                    image = Image.new('RGB', size, 'white')
                    draw = ImageDraw.Draw(image)
                    boxes = [(int(100*sx), int(y*sy), int(455*sx), int((y+16)*sy))
                             for y in (1750, 1830, 1910)]
                    for box in boxes:
                        draw.rectangle(box, fill=color)
                    actual = find_bars(image)
                    if color in accepted:
                        self.assertEqual([(a, b, c+1, d+1) for a,b,c,d in boxes],
                                         [box.as_tuple() for box in actual])
                    else:
                        self.assertIsNone(actual)

    def test_region_differences_match_full_image_signed_reference(self):
        rng = np.random.default_rng(109)
        for size in [(1, 1), (96, 237), (968, 2376)]:
            base = Image.fromarray(rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8))
            other = Image.fromarray(255 - np.asarray(base))
            for mode in ('RGB', 'RGBA', 'L', 'P'):
                with self.subTest(size=size, mode=mode):
                    first, second = base.convert(mode), other.convert(mode)
                    a = np.asarray(first.convert('RGB'), dtype=np.int16)
                    b = np.asarray(second.convert('RGB'), dtype=np.int16)
                    expected = []
                    for x1,y1,x2,y2 in _STABILITY_ROIS:
                        region = np.s_[int(size[1]*y1):int(size[1]*y2), int(size[0]*x1):int(size[0]*x2)]
                        diff = a[region] - b[region]
                        expected.append(float(np.abs(diff).mean()) if diff.size else float('inf'))
                    self.assertEqual(tuple(expected), appraisal_region_diffs(first, second))

    def test_geometry_mismatch_and_unsigned_contrast_never_look_stable(self):
        black = Image.new('RGB', (96, 237), 'black')
        white = Image.new('RGB', black.size, 'white')
        self.assertTrue(all(diff == 255 for diff in appraisal_region_diffs(black, white)))
        self.assertFalse(appraisal_frames_stable(black, white))
        self.assertEqual((float('inf'),), appraisal_region_diffs(black, white.resize((95, 237))))


if __name__ == '__main__':
    unittest.main()
