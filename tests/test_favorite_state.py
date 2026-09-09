"""A toggle must be based on an observed star, never the absence of gold."""

from pathlib import Path
import math
import unittest

from PIL import Image, ImageDraw

from pokemgr.calibration.regions import BBox
from pokemgr.reader.icons import favorite_state, is_favorited


GOLD = (250, 200, 0)
GRAY = (195, 205, 210)


def star_points(center=(100, 100), radius=50):
    return [
        (round(center[0] + math.cos(i*math.pi/5-math.pi/2)*radius*(1 if i % 2 == 0 else .42)),
         round(center[1] + math.sin(i*math.pi/5-math.pi/2)*radius*(1 if i % 2 == 0 else .42)))
        for i in range(10)
    ]


def star_image(color=GOLD, outlined=False):
    image = Image.new("RGB", (200, 200), "white")
    draw = ImageDraw.Draw(image)
    if outlined:
        draw.polygon(star_points(), fill="white", outline=color, width=7)
    else:
        draw.polygon(star_points(), fill=color)
    return image


class FavoriteStateTests(unittest.TestCase):
    region = BBox(40, 40, 120, 120)

    def test_gold_filled_star_is_on(self):
        self.assertEqual("on", favorite_state(star_image(), self.region))

    def test_gray_filled_and_outlined_stars_are_affirmatively_off(self):
        for outlined in (False, True):
            with self.subTest(outlined=outlined):
                self.assertEqual("off", favorite_state(star_image(GRAY, outlined), self.region))

    def test_absent_gold_does_not_mean_off(self):
        for color in ("white", "black", GRAY, GOLD, (80, 180, 100)):
            with self.subTest(color=color):
                self.assertEqual("unknown", favorite_state(Image.new("RGB", (200, 200), color), self.region))

    def test_non_star_gray_and_gold_shapes_are_unknown(self):
        for color in (GOLD, GRAY):
            for shape in ("circle", "rectangle", "triangle", "cross"):
                with self.subTest(color=color, shape=shape):
                    image = Image.new("RGB", (200, 200), "white")
                    draw = ImageDraw.Draw(image)
                    if shape == "circle":
                        draw.ellipse((50, 50, 150, 150), fill=color)
                    elif shape == "rectangle":
                        draw.rectangle((50, 50, 150, 150), fill=color)
                    elif shape == "triangle":
                        draw.polygon(((100, 50), (150, 150), (50, 150)), fill=color)
                    else:
                        draw.rectangle((85, 50, 115, 150), fill=color)
                        draw.rectangle((50, 85, 150, 115), fill=color)
                    self.assertEqual("unknown", favorite_state(image, self.region))

    def test_partial_or_occluded_stars_are_unknown(self):
        for color, outlined in ((GOLD, False), (GRAY, True)):
            for cover in ((0, 0, 100, 200), (80, 0, 120, 75)):
                with self.subTest(color=color, cover=cover):
                    image = star_image(color, outlined)
                    ImageDraw.Draw(image).rectangle(cover, fill="white")
                    self.assertEqual("unknown", favorite_state(image, self.region))

    def test_gold_outline_and_wrong_colored_star_are_unknown(self):
        self.assertEqual("unknown", favorite_state(star_image(GOLD, True), self.region))
        self.assertEqual("unknown", favorite_state(star_image((50, 200, 50)), self.region))

    def test_gray_outline_with_colored_center_is_not_off(self):
        image = star_image(GRAY, True)
        ImageDraw.Draw(image).ellipse((88, 88, 112, 112), fill=GOLD)
        self.assertEqual("unknown", favorite_state(image, self.region))

    def test_multiple_star_glyphs_are_ambiguous(self):
        image = Image.new("RGB", (400, 360), "white")
        draw = ImageDraw.Draw(image)
        draw.polygon(star_points((130, 180), 60), fill=GOLD)
        draw.polygon(star_points((265, 180), 60), fill=GRAY)
        self.assertEqual("unknown", favorite_state(image, BBox(80, 80, 200, 200)))

    def test_invalid_or_wrong_region_is_unknown(self):
        image = star_image()
        for region in (BBox(0, 0, 0, 0), BBox(220, 220, 85, 85), BBox(0, 0, 20, 20)):
            self.assertEqual("unknown", favorite_state(image, region))

    def test_original_boolean_api_keeps_its_behavior(self):
        self.assertTrue(is_favorited(star_image(), self.region))
        self.assertFalse(is_favorited(star_image(GRAY, True), self.region))
        # The new tri-state reader adds glyph checks without changing callers
        # that still use the legacy color-only metadata predicate.
        solid_gold = Image.new("RGB", (200, 200), GOLD)
        self.assertTrue(is_favorited(solid_gold, self.region))
        self.assertEqual("unknown", favorite_state(solid_gold, self.region))

    def test_real_star_crops_support_clipped_calibration_and_rgba(self):
        fixtures = Path(__file__).parent / "fixtures/reader"
        for expected in ("on", "off"):
            with self.subTest(expected=expected), Image.open(fixtures / f"favorite_{expected}.png") as image:
                # Original (855,90,85,85) ROI in a crop taken at (800,80).
                region = BBox(55, 10, 85, 85)
                self.assertEqual(expected, favorite_state(image, region))
                self.assertEqual(expected, favorite_state(image.convert("RGBA"), region))


if __name__ == "__main__":
    unittest.main()
