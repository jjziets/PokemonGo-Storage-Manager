"""Strict optional glyph evidence uses synthetic pixels, not private captures."""

import unittest

from PIL import Image, ImageDraw

from pokemgr.calibration.regions import BBox
from pokemgr.reader.gender import detect_gender, detect_gender_evidence


# TRACEWEAVER: file-role=gender-evidence-tests; verifies=VER-SCAN-001; req=REQ-SCAN-003; trace=TRACE-SCAN-003
class GenderEvidenceTests(unittest.TestCase):
    def glyph(self, gender, *, arms=True, scale=1., ink=190):
        # Draw a circle plus the mathematical arrow/cross at a larger scale
        # for smooth raster edges. Coordinates differ from detector templates.
        factor = 4
        image = Image.new("RGB", (120 * factor, 100 * factor), "white")
        draw = ImageDraw.Draw(image)
        color = (ink, ink, ink)

        def line(points, width):
            points = [(x * factor, y * factor) for x, y in points]
            draw.line(points, fill=color, width=width * factor)
            radius = width * factor / 2
            for x, y in (points[0], points[-1]):
                draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=color)

        if gender == "male":
            draw.ellipse(tuple(v * factor for v in (35, 44, 75, 84)), outline=color, width=6 * factor)
            line(((67, 52), (84, 31)), 6)
            if arms:
                line(((68, 31), (84, 31), (84, 47)), 6)
        else:
            draw.ellipse(tuple(v * factor for v in (45, 25, 79, 59)), outline=color, width=5 * factor)
            line(((62, 58), (62, 77)), 5)
            if arms:
                line(((54, 68), (70, 68)), 5)
        return image.resize((round(120 * scale), round(100 * scale)), Image.Resampling.LANCZOS)

    def detect(self, image):
        return detect_gender_evidence(image, BBox(0, 0, *image.size))

    def test_complete_male_and_female_glyphs_across_scales(self):
        for gender in ("male", "female"):
            for scale in (.75, 1., 1.5, 2.):
                with self.subTest(gender=gender, scale=scale):
                    self.assertEqual(gender, self.detect(self.glyph(gender, scale=scale)))

    def test_missing_and_low_contrast_symbols_are_unavailable(self):
        self.assertEqual("", self.detect(Image.new("RGB", (120, 100), "white")))
        self.assertEqual("", self.detect(self.glyph("male", ink=235)))
        self.assertEqual("", self.detect(Image.new("RGB", (120, 100), "black")))

    def test_small_aliased_symbols_are_unavailable_instead_of_guessed(self):
        for gender in ("male", "female"):
            with self.subTest(gender=gender):
                self.assertEqual("", self.detect(self.glyph(gender, scale=.5)))

    def test_plain_ring_and_equal_half_shapes_are_not_gender(self):
        for shape in ("circle", "square", "cross", "triangle"):
            with self.subTest(shape=shape):
                image = Image.new("RGB", (120, 100), "white")
                draw = ImageDraw.Draw(image)
                if shape == "circle":
                    draw.ellipse((35, 25, 85, 75), outline="black", width=6)
                elif shape == "square":
                    draw.rectangle((35, 25, 85, 75), outline="black", width=6)
                elif shape == "cross":
                    draw.line((60, 25, 60, 75), fill="black", width=6)
                    draw.line((35, 50, 85, 50), fill="black", width=6)
                else:
                    draw.polygon(((60, 20), (30, 80), (90, 80)), fill="black")
                self.assertEqual("", self.detect(image))

    def test_ring_with_tail_requires_both_arrowhead_or_cross_arms(self):
        for gender in ("male", "female"):
            with self.subTest(gender=gender):
                self.assertEqual("", self.detect(self.glyph(gender, arms=False)))

    def test_one_missing_arrowhead_or_cross_arm_is_unavailable(self):
        for gender, missing_arm in (("male", (66, 27, 79, 35)),
                                    ("female", (50, 64, 58, 72))):
            with self.subTest(gender=gender):
                image = self.glyph(gender)
                ImageDraw.Draw(image).rectangle(missing_arm, fill="white")
                self.assertEqual("", self.detect(image))

    def test_clipped_symbol_on_any_crop_edge_is_unavailable(self):
        for gender in ("male", "female"):
            image = self.glyph(gender)
            dark = image.convert("L").point(lambda value: 255 if value < 220 else 0)
            x1, y1, x2, y2 = dark.getbbox()
            for box in ((x1 + 2, 0, image.width, image.height),
                        (0, 0, x2 - 2, image.height),
                        (0, y1 + 2, image.width, image.height),
                        (0, 0, image.width, y2 - 2)):
                with self.subTest(gender=gender, crop=box):
                    self.assertEqual("", self.detect(image.crop(box)))

    def test_disconnected_noise_or_multiple_symbols_are_unavailable(self):
        image = self.glyph("male")
        draw = ImageDraw.Draw(image)
        draw.rectangle((8, 8, 10, 10), fill="black")
        self.assertEqual("", self.detect(image))
        image = Image.new("RGB", (240, 100), "white")
        image.paste(self.glyph("male"), (0, 0))
        image.paste(self.glyph("female"), (120, 0))
        self.assertEqual("", self.detect(image))

    def test_incorrect_orientation_is_unavailable(self):
        for gender, transform in (("male", Image.Transpose.FLIP_LEFT_RIGHT),
                                  ("male", Image.Transpose.FLIP_TOP_BOTTOM),
                                  ("female", Image.Transpose.FLIP_TOP_BOTTOM),
                                  ("female", Image.Transpose.ROTATE_90)):
            with self.subTest(gender=gender, transform=transform):
                self.assertEqual("", self.detect(self.glyph(gender).transpose(transform)))

    def test_regions_outside_the_source_image_are_unavailable(self):
        image = self.glyph("male")
        for region in (BBox(-1, 0, 120, 100), BBox(0, -1, 120, 100),
                       BBox(0, 0, 121, 100), BBox(0, 0, 120, 101), BBox(0, 0, 0, 100)):
            with self.subTest(region=region):
                self.assertEqual("", detect_gender_evidence(image, region))

    def test_existing_gender_display_reader_keeps_its_legacy_contract(self):
        self.assertEqual("none", detect_gender(Image.new("RGB", (120, 100), "white"),
                                               BBox(0, 0, 120, 100)))
        image = Image.new("RGB", (120, 100), "white")
        ImageDraw.Draw(image).rectangle((40, 25, 80, 75), fill="black")
        self.assertIn(detect_gender(image, BBox(0, 0, 120, 100)), ("male", "female"))
        self.assertEqual("", self.detect(image))


if __name__ == "__main__":
    unittest.main()
