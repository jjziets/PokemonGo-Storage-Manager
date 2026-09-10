"""Observed appraisal arrows can replace forward swipes without guessing targets."""

# TRACEWEAVER: file-role=appraisal-forward-tests; req=REQ-SCAN-003,REQ-MASS-001; trace=TRACE-SCAN-003; verifies=VER-SCAN-001

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw, ImageFilter

from pokemgr.indexer.state_machine import IndexingStateMachine
from pokemgr.reader.appraisal_navigation import next_appraisal_target
from tests.test_stable_scan_loop import _ADB, _DB, profile


def appraisal(size=(968, 2376), shift=0):
    width, height = size
    image = Image.new("RGB", size, (35, 95, 125))
    draw = ImageDraw.Draw(image)
    for base_y in (1842, 1931, 2019):
        y = round((base_y + shift) * height / 2376)
        draw.rectangle((round(116 * width / 968), y,
                        round(449 * width / 968), y + round(18 * height / 2376)),
                       fill=(242, 166, 74))
    return image


def arrow(image, *, center_y=None, direction="right", shape="triangle", x=None,
          width=None, height=None, color="white"):
    scale = image.height / 2376
    w, h = width or round(29 * scale), height or round(46 * scale)
    x = round(image.width * .95) if x is None else x
    cy = round(1925 * scale) if center_y is None else center_y
    y = cy - h // 2
    if shape == "rectangle":
        points = ((x, y), (x + w - 1, y), (x + w - 1, y + h - 1), (x, y + h - 1))
    elif shape == "diamond":
        points = ((x, cy), (x + w // 2, y), (x + w - 1, cy), (x + w // 2, y + h - 1))
    elif direction == "left":
        points = ((x + w - 1, y), (x, cy), (x + w - 1, y + h - 1))
    else:
        points = ((x, y), (x + w - 1, cy), (x, y + h - 1))
    ImageDraw.Draw(image).polygon(points, fill=color)
    return x, y, x + w, y + h


class AppraisalArrowReaderTests(unittest.TestCase):
    def test_phone_half_size_and_tablet_return_observed_interior_without_left_arrow(self):
        for size in ((968, 2376), (484, 1188), (1440, 2304)):
            with self.subTest(size=size):
                image = appraisal(size)
                left, top, right, bottom = arrow(image)
                target = next_appraisal_target(image)
                self.assertIsNotNone(target)
                x, y = target
                self.assertTrue(left < x < right - 1 and top < y < bottom - 1)
                self.assertEqual((255, 255, 255), image.getpixel(target))

    def test_target_follows_shifted_bars_and_pulsing_arrow(self):
        for shift in (-50, 0, 54):
            for x in (916, 923):
                with self.subTest(shift=shift, x=x):
                    image = appraisal(shift=shift)
                    arrow(image, center_y=1925 + shift, x=x)
                    target = next_appraisal_target(image)
                    self.assertIsNotNone(target)
                    self.assertLess(abs(target[1] - (1925 + shift)), 3)
                    self.assertTrue(x < target[0] < x + 28)

    def test_antialiased_triangle_and_rgba_capture_are_supported(self):
        image = appraisal()
        arrow(image)
        for candidate in (image.filter(ImageFilter.GaussianBlur(.7)), image.convert("RGBA")):
            with self.subTest(mode=candidate.mode):
                self.assertIsNotNone(next_appraisal_target(candidate))

    def test_blank_detail_and_preview_like_screens_without_three_bars_are_rejected(self):
        for background in ("black", "white", (35, 95, 125)):
            with self.subTest(background=background):
                image = Image.new("RGB", (968, 2376), background)
                arrow(image)
                self.assertIsNone(next_appraisal_target(image))
        image = appraisal()
        ImageDraw.Draw(image).rectangle((80, 2000, 500, 2100), fill=(35, 95, 125))
        arrow(image)
        self.assertIsNone(next_appraisal_target(image))

    def test_absent_wrong_facing_rectangular_diamond_and_colored_shapes_are_rejected(self):
        for options in ({}, {"direction": "left"}, {"shape": "rectangle"},
                        {"shape": "diamond"}, {"color": (255, 235, 145)}):
            with self.subTest(options=options):
                image = appraisal()
                if options:
                    arrow(image, **options)
                self.assertIsNone(next_appraisal_target(image))

    def test_clipped_and_unrelated_height_glyphs_are_rejected(self):
        for options in ({"x": 950}, {"x": 875}, {"center_y": 1750}, {"center_y": 2150}):
            with self.subTest(options=options):
                image = appraisal()
                arrow(image, **options)
                self.assertIsNone(next_appraisal_target(image))

    def test_two_individually_valid_candidates_are_ambiguous(self):
        positions = (1906, 1942)
        for y in positions:
            image = appraisal()
            arrow(image, center_y=y, width=17, height=25)
            self.assertIsNotNone(next_appraisal_target(image))
        image = appraisal()
        for y in positions:
            arrow(image, center_y=y, width=17, height=25)
        self.assertIsNone(next_appraisal_target(image))

    def test_broad_white_jacket_or_panel_does_not_supply_a_target(self):
        image = appraisal()
        ImageDraw.Draw(image).polygon(((890, 1800), (960, 1925), (910, 2070)), fill="white")
        self.assertIsNone(next_appraisal_target(image))

    def test_hollow_triangle_cannot_supply_a_filled_interior_target(self):
        image = appraisal()
        x, y, right, bottom = arrow(image)
        ImageDraw.Draw(image).polygon(((x + 4, y + 9), (right - 8, (y + bottom) // 2),
                                      (x + 4, bottom - 10)), fill=(35, 95, 125))
        self.assertIsNone(next_appraisal_target(image))

    def test_private_meditite_and_shifted_appraisals_if_available(self):
        root = Path(__file__).resolve().parents[1]
        paths = [root / "cache/favorite-advance-fix/failed-meditite.png",
                 root / "cache/scan-action-timing/mass-live-appraisal-0.png"]
        paths.extend(sorted((root / "cache/keeper-preswipe-proof").glob("frame-*.png")))
        existing = [path for path in paths if path.exists()]
        if not existing:
            self.skipTest("Private operator capture fixtures are not distributed")
        for path in existing:
            with self.subTest(capture=path.name), Image.open(path) as image:
                target = next_appraisal_target(image)
                self.assertIsNotNone(target)
                self.assertGreater(target[0], image.width * .91)
                self.assertGreater(min(image.convert("RGB").getpixel(target)), 224)


class AppraisalForwardDispatchTests(unittest.TestCase):
    def setUp(self):
        self.adb = _ADB()
        self.sm = IndexingStateMachine(self.adb, profile(), _DB())
        self.image = appraisal()
        self.target = (929, 1925)
        self.detector = self.enterContext(patch(
            "pokemgr.reader.appraisal_navigation.next_appraisal_target", return_value=self.target,
        ))

    def test_observed_arrow_sends_exactly_one_zero_jitter_tap(self):
        self.assertTrue(self.sm._advance_appraisal(self.image, pause_generation=0))
        self.assertEqual([("tap", self.target, {"jitter": 0})], self.adb.actions)
        self.assertIs(self.image, self.sm._last_stable_image)
        self.detector.assert_called_once_with(self.image)
        self.assertNotEqual(self.target, self.sm.regions.close_appraisal_target)

    def test_no_observed_arrow_sends_only_existing_calibrated_swipe(self):
        self.detector.return_value = None
        self.assertTrue(self.sm._advance_appraisal(self.image))
        self.assertEqual([("swipe", (*self.sm.regions.swipe_start,
                                      *self.sm.regions.swipe_end,
                                      self.sm.regions.swipe_duration_ms), {"jitter": 0})],
                         self.adb.actions)

    def test_abort_pause_or_previous_generation_sends_no_input(self):
        for state in ("abort", "pause", "generation"):
            with self.subTest(state=state):
                self.sm._abort = state == "abort"
                self.sm._paused = state == "pause"
                self.sm._pause_generation = int(state == "generation")
                self.assertFalse(self.sm._advance_appraisal(self.image, pause_generation=0))
                self.assertEqual([], self.adb.actions)
        self.detector.assert_not_called()

    def test_interruption_during_detection_sends_no_tap_or_fallback_swipe(self):
        for state in ("abort", "pause", "pause_resume"):
            with self.subTest(state=state):
                self.sm._abort = self.sm._paused = False
                def detect(_image):
                    if state == "abort":
                        self.sm.abort()
                    else:
                        self.sm.pause()
                        if state == "pause_resume":
                            self.sm.resume()
                    return self.target
                self.detector.side_effect = detect
                self.assertFalse(self.sm._advance_appraisal(self.image))
                self.assertEqual([], self.adb.actions)

    def test_pause_during_sent_input_keeps_success_and_never_repeats(self):
        for target in (self.target, None):
            with self.subTest(target=target):
                self.sm._paused = False
                self.adb.actions.clear()
                self.detector.return_value = target
                def sent(*args, **kwargs):
                    self.adb.actions.append((args, kwargs))
                    self.sm.pause()
                self.adb.tap = self.adb.swipe = sent
                self.assertTrue(self.sm._advance_appraisal(self.image))
                self.assertEqual(1, len(self.adb.actions))

    def test_unsuccessful_or_raising_tap_never_falls_back_to_swipe(self):
        self.sm._safe_tap = Mock(return_value=False)
        self.sm._fast_swipe = Mock()
        self.assertFalse(self.sm._advance_appraisal(self.image))
        self.sm._fast_swipe.assert_not_called()
        self.sm._safe_tap.side_effect = RuntimeError("transport lost")
        with self.assertRaisesRegex(RuntimeError, "transport lost"):
            self.sm._advance_appraisal(self.image)
        self.sm._fast_swipe.assert_not_called()

    def test_normal_scan_reacquires_after_pause_before_input(self):
        fresh = self.image.copy()
        self.sm._fast_screencap = Mock(side_effect=[self.image, fresh])
        self.sm.nav.detect_screen = Mock(return_value="appraisal")
        def detect(image):
            if image is self.image:
                self.sm.pause()
                self.sm.resume()
            return self.target
        self.detector.side_effect = detect
        self.assertTrue(self.sm._advance_from_confirmed_appraisal())
        self.assertEqual(2, self.sm._fast_screencap.call_count)
        self.assertEqual(1, len(self.adb.actions))
        self.assertIs(fresh, self.sm._last_stable_image)

    def test_normal_scan_waits_through_pause_then_captures_before_input(self):
        self.sm.pause()
        self.sm._fast_screencap = Mock(return_value=self.image)
        self.sm.nav.detect_screen = Mock(return_value="appraisal")
        with patch("pokemgr.indexer.state_machine.time.sleep", side_effect=lambda _delay: self.sm.resume()):
            self.assertTrue(self.sm._advance_from_confirmed_appraisal())
        self.sm._fast_screencap.assert_called_once()
        self.assertEqual(1, len(self.adb.actions))

    def test_normal_scan_rechecks_generation_after_capture_and_screen_recognition(self):
        self.sm._fast_screencap = Mock(side_effect=[self.image, self.image.copy()])
        first = True
        def screen(_image):
            nonlocal first
            if first:
                first = False
                self.sm.pause()
                self.sm.resume()
            return "appraisal"
        self.sm.nav.detect_screen = Mock(side_effect=screen)
        self.assertTrue(self.sm._advance_from_confirmed_appraisal())
        self.assertEqual(2, self.sm._fast_screencap.call_count)
        self.assertEqual(1, len(self.adb.actions))

    def test_normal_scan_lost_appraisal_never_detects_or_taps_arrow(self):
        self.sm._fast_screencap = Mock(return_value=self.image)
        self.sm.nav.detect_screen = Mock(return_value="detail")
        self.assertFalse(self.sm._advance_from_confirmed_appraisal())
        self.detector.assert_not_called()
        self.assertEqual([], self.adb.actions)

    def test_normal_scan_pause_after_tap_cannot_repeat_forward_input(self):
        self.sm._fast_screencap = Mock(return_value=self.image)
        self.sm.nav.detect_screen = Mock(return_value="appraisal")
        def tapped(*args, **kwargs):
            self.adb.actions.append((args, kwargs))
            self.sm.pause()
        self.adb.tap = tapped
        self.assertTrue(self.sm._advance_from_confirmed_appraisal())
        self.assertTrue(self.sm._paused)
        self.sm._fast_screencap.assert_called_once()
        self.assertEqual(1, len(self.adb.actions))


if __name__ == "__main__":
    unittest.main()
