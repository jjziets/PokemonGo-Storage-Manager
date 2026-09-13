"""Checkpoint navigation uses observed arrows without relaxing position proof."""

# TRACEWEAVER: file-role=checkpoint-arrow-tests; req=REQ-SCAN-003; trace=TRACE-SCAN-003; verifies=VER-SCAN-001

import unittest
from unittest.mock import Mock, patch

from PIL import ImageDraw, ImageFilter

from pokemgr.reader.appraisal_navigation import next_appraisal_target, previous_appraisal_target
from tests import test_transition_retry as retry
from tests.test_appraisal_navigation import appraisal, arrow


class PreviousAppraisalArrowTests(unittest.TestCase):
    def test_left_arrow_uses_original_left_bar_layout_at_supported_sizes(self):
        for size in ((968, 2376), (484, 1188), (1440, 2304)):
            with self.subTest(size=size):
                frame = appraisal(size)
                left, top, right, bottom = arrow(frame, direction="left", x=round(size[0] * .02))
                target = previous_appraisal_target(frame)
                self.assertIsNotNone(target)
                self.assertTrue(left < target[0] < right - 1 and top < target[1] < bottom - 1)
                self.assertEqual((255, 255, 255), frame.getpixel(target))
                self.assertIsNone(next_appraisal_target(frame))

    def test_shifted_antialiased_rgba_and_both_arrows_remain_independent(self):
        for shift in (-50, 0, 54):
            frame = appraisal(shift=shift)
            arrow(frame, direction="left", x=18, center_y=1925 + shift)
            arrow(frame, center_y=1925 + shift)
            for candidate in (frame, frame.filter(ImageFilter.GaussianBlur(.7)), frame.convert("RGBA")):
                with self.subTest(shift=shift, mode=candidate.mode):
                    previous = previous_appraisal_target(candidate)
                    following = next_appraisal_target(candidate)
                    self.assertIsNotNone(previous)
                    self.assertIsNotNone(following)
                    self.assertLess(previous[0], frame.width * .09)
                    self.assertGreater(following[0], frame.width * .91)
                    self.assertLess(abs(previous[1] - (1925 + shift)), 3)

    def test_absent_wrong_direction_clipped_colored_and_unrelated_shapes_hold(self):
        for options in (None, {"direction": "right"}, {"x": -1}, {"x": 80},
                        {"color": (255, 235, 145)}, {"shape": "rectangle"},
                        {"shape": "diamond"}, {"center_y": 1750}):
            with self.subTest(options=options):
                frame = appraisal()
                if options is not None:
                    arrow(frame, **({"direction": "left", "x": 18} | options))
                self.assertIsNone(previous_appraisal_target(frame))

    def test_ambiguous_and_hollow_left_arrows_hold(self):
        frame = appraisal()
        for y in (1906, 1942):
            arrow(frame, direction="left", x=18, center_y=y, width=17, height=25)
        self.assertIsNone(previous_appraisal_target(frame))
        frame = appraisal()
        x, y, right, bottom = arrow(frame, direction="left", x=18)
        ImageDraw.Draw(frame).polygon(((right - 5, y + 9), (x + 7, (y + bottom) // 2),
                                      (right - 5, bottom - 10)), fill=(35, 95, 125))
        self.assertIsNone(previous_appraisal_target(frame))


class CheckpointArrowRecoveryTests(unittest.TestCase):
    def setUp(self):
        fixture = retry.TransitionRetryTests()
        fixture.setUp()
        self.fixture = fixture
        self.sm, self.adb, self.db = fixture.sm, fixture.adb, fixture.db
        self.frames = [appraisal() for _ in range(4)]
        for frame in self.frames:
            arrow(frame, direction="left", x=18)
            arrow(frame)
        self.sm._fast_screencap = Mock(side_effect=self.frames)
        self.sm._acquire_validated_snapshot = Mock(side_effect=fixture.checkpoint_reads())
        self.enterContext(patch("pokemgr.indexer.state_machine.human_delay"))

    def recover(self):
        result = self.sm._recover_failed_transition(self.fixture.last_frame)
        self.assertEqual([], self.db.rows)
        self.assertEqual((2, 2, 0), (self.sm.count, self.sm.visited_count, self.sm.skipped_count))
        self.assertEqual(self.fixture.previous.snapshot.identity_key, self.sm._previous_validated_identity_key)
        self.assertEqual(self.fixture.last.snapshot.identity_key, self.sm._last_validated_identity_key)
        return result

    def test_exact_reverse_restore_and_forward_use_only_three_observed_taps(self):
        result = self.recover()
        self.assertIs(self.fixture.next, result[0])
        self.assertEqual("ok", result[2])
        expected = [previous_appraisal_target(self.frames[0]),
                    next_appraisal_target(self.frames[1]), next_appraisal_target(self.frames[2])]
        self.assertEqual([("tap", point, {"jitter": 0}) for point in expected], self.adb.actions)
        self.assertEqual(3, self.sm._acquire_validated_snapshot.call_count)
        self.assertEqual(3, self.sm._fast_screencap.call_count)

    def test_wrong_reverse_checkpoint_reads_three_times_and_never_moves_forward(self):
        self.sm._acquire_validated_snapshot.side_effect = None
        self.sm._acquire_validated_snapshot.return_value = retry.exact(self.fixture.last, self.fixture.last_frame)
        result = self.recover()
        self.assertEqual("transition_retry_failed", result[2])
        self.assertIn("previous checkpoint did not match", result[3])
        self.assertEqual(3, self.sm._acquire_validated_snapshot.call_count)
        self.assertEqual([("tap", previous_appraisal_target(self.frames[0]), {"jitter": 0})], self.adb.actions)

    def test_pause_resume_during_detection_reacquires_without_duplicate_input(self):
        def detect(frame):
            if frame is self.frames[0]:
                self.sm.pause()
                self.sm.resume()
            return "appraisal"
        self.sm.nav.detect_screen.side_effect = detect
        result = self.recover()
        self.assertEqual("ok", result[2])
        self.assertEqual(4, self.sm._fast_screencap.call_count)
        self.assertEqual(3, len(self.adb.actions))

    def test_pause_during_sent_arrow_does_not_repeat_phase(self):
        original_tap = self.adb.tap
        def tap(*args, **kwargs):
            original_tap(*args, **kwargs)
            if len(self.adb.actions) == 1:
                self.sm.pause()
        self.adb.tap = tap
        with patch("pokemgr.indexer.state_machine.time.sleep", side_effect=lambda _seconds: self.sm.resume()):
            result = self.recover()
        self.assertEqual("ok", result[2])
        self.assertEqual(3, len(self.adb.actions))
        self.assertEqual(3, self.sm._acquire_validated_snapshot.call_count)

    def test_abort_during_reverse_arrow_prevents_all_later_input(self):
        original_tap = self.adb.tap
        def tap(*args, **kwargs):
            original_tap(*args, **kwargs)
            self.sm.abort()
        self.adb.tap = tap
        result = self.recover()
        self.assertEqual("aborted", result[2])
        self.assertEqual(1, len(self.adb.actions))
        self.sm._acquire_validated_snapshot.assert_not_called()
