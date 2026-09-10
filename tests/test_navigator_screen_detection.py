# TRACEWEAVER: file-role=screen-marker-regressions; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
# TRACEWEAVER: verifies=VER-SCAN-001; req=REQ-SCAN-003; trace=TRACE-SCAN-003
"""Portable geometry regressions; no private account screenshots required."""

import io
import unittest
from unittest.mock import Mock

import numpy as np
from PIL import Image, ImageDraw

from pokemgr.adb.device import DeviceInfo
from pokemgr.adb.navigator import GameNavigator, _has_pokeball
from pokemgr.calibration.profile import CalibrationProfile


BACKGROUND = (105, 195, 150)
RED = (255, 57, 70)
NEUTRAL = (148, 170, 168)


def map_image(size=(968, 2376), *, center_y=0.943, center_x=0.5):
    image = Image.new("RGB", size, BACKGROUND)
    w, h = size
    x, y, radius = round(w * center_x), round(h * center_y), round(w * 0.057)
    draw = ImageDraw.Draw(image)
    bounds = (x - radius, y - radius, x + radius, y + radius)
    draw.ellipse(bounds, fill="white")
    draw.pieslice(bounds, 180, 360, fill=RED)
    button_radius = round(radius * 0.36)
    draw.ellipse((x - button_radius, y - button_radius,
                  x + button_radius, y + button_radius), fill="white")
    button_radius = round(radius * 0.25)
    draw.ellipse((x - button_radius, y - button_radius,
                  x + button_radius, y + button_radius), fill=NEUTRAL)
    return image


def navigator(image):
    w, h = image.size
    regions = CalibrationProfile.create_default(DeviceInfo(
        model="synthetic", serial="test", width=w, height=h, density=280,
    )).regions
    return GameNavigator(Mock(), regions)


class NavigatorScreenMarkerTests(unittest.TestCase):
    def test_complete_map_ball_on_phone_tablet_and_scaled_frames(self):
        for size, y in (((968, 2376), 0.943), ((1440, 2304), 0.90),
                        ((484, 1188), 0.943), ((360, 780), 0.91)):
            with self.subTest(size=size):
                image = map_image(size, center_y=y)
                self.assertEqual("game_map", navigator(image).detect_screen(image))

    def test_map_ball_survives_capture_compression(self):
        image = map_image()
        encoded = io.BytesIO()
        image.save(encoded, format="JPEG", quality=75)
        compressed = Image.open(io.BytesIO(encoded.getvalue())).convert("RGB")
        self.assertTrue(_has_pokeball(np.asarray(compressed)))

    def test_pink_max_moves_banner_cannot_override_detail(self):
        # The real failure was a 306x52 red banner at (331, 2185), exactly
        # intersecting the old narrow red-pixel ROI. Keep its geometry here.
        image = Image.new("RGB", (968, 2376), (65, 90, 110))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((20, 700, 948, 2350), radius=25, fill="white")
        draw.rectangle((235, 949, 728, 961), fill=(100, 230, 180))
        draw.polygon(((331, 2185), (637, 2185), (612, 2211), (637, 2237),
                      (331, 2237), (356, 2211)), fill=RED)

        self.assertFalse(_has_pokeball(np.asarray(image)))
        self.assertEqual("detail", navigator(image).detect_screen(image))

    def test_hp_colored_map_background_does_not_override_real_ball(self):
        image = map_image()
        draw = ImageDraw.Draw(image)
        # Broad HP heuristics match roads/neutral areas too. Retain the
        # stronger map marker's precedence rather than checking HP first.
        draw.rectangle((194, 1060, 774, 1070), fill=(100, 230, 180))
        draw.rectangle((194, 1090, 774, 1100), fill=(230, 230, 230))
        self.assertEqual("game_map", navigator(image).detect_screen(image))

    def test_compact_red_rectangle_with_white_base_is_not_a_ball(self):
        image = Image.new("RGB", (968, 2376), BACKGROUND)
        draw = ImageDraw.Draw(image)
        draw.rectangle((430, 2186, 538, 2240), fill=RED)
        draw.rectangle((430, 2241, 538, 2294), fill="white")
        draw.ellipse((471, 2227, 497, 2253), fill=NEUTRAL)
        self.assertFalse(_has_pokeball(np.asarray(image)))

    def test_hemisphere_requires_both_white_base_and_neutral_button(self):
        for missing in ("white_base", "button"):
            with self.subTest(missing=missing):
                image = map_image()
                draw = ImageDraw.Draw(image)
                if missing == "white_base":
                    draw.rectangle((430, 2257, 538, 2295), fill=BACKGROUND)
                else:
                    draw.ellipse((468, 2225, 500, 2257), fill="white")
                self.assertFalse(_has_pokeball(np.asarray(image)))

    def test_ball_shape_outside_bottom_center_is_not_a_map_marker(self):
        for x, y in ((0.35, 0.943), (0.5, 0.70)):
            with self.subTest(x=x, y=y):
                image = map_image(center_x=x, center_y=y)
                self.assertFalse(_has_pokeball(np.asarray(image)))

    def test_missing_or_clipped_red_shape_is_not_a_map_marker(self):
        self.assertFalse(_has_pokeball(np.asarray(Image.new("RGB", (968, 2376), "white"))))
        self.assertFalse(_has_pokeball(np.asarray(map_image(center_y=1.0))))


if __name__ == "__main__":
    unittest.main()
