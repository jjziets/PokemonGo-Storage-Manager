# TRACEWEAVER: file-role=stream-star-readback-timing-tests; req=REQ-MASS-001,REQ-SCAN-004; trace=TRACE-MASS-001,TRACE-SCAN-004; ver=VER-SCAN-001
"""Fresh stream readback can finish early without losing delayed attempts."""

from dataclasses import replace
import unittest
from unittest.mock import Mock, call, patch

from pokemgr.calibration.profile import CalibrationProfile
from pokemgr.calibration.regions import ScreenRegions
from pokemgr.execution.executor import Executor
from tests.test_mass_action_scanning import frame, snapshot


class StarReadbackTimingTests(unittest.TestCase):
    def setUp(self):
        profile = CalibrationProfile(
            "test", "serial", "968x2376", 420,
            ScreenRegions.default_for_resolution(968, 2376, density=420),
        )
        self.adb, self.db = Mock(), Mock()
        self.adb.has_stream_frames = True
        self.executor = Executor(self.adb, profile, self.db)
        self.scanner = self.executor._scanner
        self.original = snapshot()
        self.now = 0.0
        self.capture_times = []
        self.observed_frames = []
        self.state_after_tap = lambda: "on"
        self.scanner._fast_screencap = Mock(side_effect=self.capture)
        self.executor._read_identity = Mock(side_effect=lambda image: image.info["read"])
        self.sleep = self.enterContext(patch(
            "pokemgr.execution.executor.time.sleep", side_effect=self.advance_time,
        ))
        self.enterContext(patch(
            "pokemgr.execution.executor.favorite_state",
            side_effect=lambda image, _region: image.info["star"],
        ))

    def advance_time(self, delay):
        self.now += delay

    def capture(self):
        self.capture_times.append(self.now)
        image = frame(self.original, self.state_after_tap() if self.adb.tap.called else "off")
        self.observed_frames.append(image)
        return image

    def set_star(self):
        return self.executor._set_star(self.original, frame(self.original), True)

    def assert_single_tap(self):
        self.adb.tap.assert_called_once_with(
            *self.executor.regions.favorite_star_region.center, jitter=0,
        )
        self.adb.swipe.assert_not_called()
        self.assertEqual([], self.db.mock_calls)

    def test_stream_can_confirm_first_fresh_readback_without_sleep(self):
        changed, observed = self.set_star()

        self.assertTrue(changed)
        self.assertIs(observed, self.observed_frames[-1])
        self.assertEqual([0., 0.], self.capture_times)
        self.sleep.assert_not_called()
        self.assertEqual(2, self.executor._read_identity.call_count)
        self.assertIsNot(self.observed_frames[0], self.observed_frames[1])
        self.assert_single_tap()

    def test_early_stream_read_does_not_consume_any_delayed_attempt(self):
        self.state_after_tap = lambda: "on" if self.now >= .25 else "off"

        changed, _observed = self.set_star()

        self.assertTrue(changed)
        self.assertEqual(5, len(self.capture_times))
        for observed, expected in zip(self.capture_times, (0., 0., .1, .2, .3)):
            self.assertAlmostEqual(expected, observed)
        self.assertEqual([call(.1)] * 3, self.sleep.call_args_list)
        self.assertEqual(5, self.executor._read_identity.call_count)
        self.assert_single_tap()

    def test_stream_failure_is_bounded_and_never_repeats_toggle(self):
        self.state_after_tap = lambda: "off"

        with self.assertRaisesRegex(RuntimeError, "not confirmed after one tap"):
            self.set_star()

        self.assertEqual(5, self.scanner._fast_screencap.call_count)
        self.assertEqual([call(.1)] * 3, self.sleep.call_args_list)
        self.assert_single_tap()

    def test_legacy_keeps_initial_delay_and_three_observation_limit(self):
        self.adb.has_stream_frames = False
        self.state_after_tap = lambda: "off"

        with self.assertRaisesRegex(RuntimeError, "not confirmed after one tap"):
            self.set_star()

        self.assertEqual(4, len(self.capture_times))
        for observed, expected in zip(self.capture_times, (0., .1, .2, .3)):
            self.assertAlmostEqual(expected, observed)
        self.assertEqual([call(.1)] * 3, self.sleep.call_args_list)
        self.assert_single_tap()

    def test_changed_identity_on_immediate_stream_frame_holds(self):
        self.executor._read_identity.side_effect = [self.original, replace(self.original, hp=61)]

        with self.assertRaisesRegex(RuntimeError, "identity changed after star input"):
            self.set_star()

        self.sleep.assert_not_called()
        self.assertEqual(2, self.scanner._fast_screencap.call_count)
        self.assert_single_tap()

    def test_abort_during_toggle_prevents_immediate_readback(self):
        self.adb.tap.side_effect = lambda *_args, **_kwargs: self.executor.abort()

        changed, _observed = self.set_star()

        self.assertFalse(changed)
        self.scanner._fast_screencap.assert_called_once()
        self.sleep.assert_not_called()
        self.assert_single_tap()

    def test_abort_during_retry_wait_prevents_another_capture(self):
        self.state_after_tap = lambda: "off"
        self.sleep.side_effect = lambda _delay: self.executor.abort()

        changed, _observed = self.set_star()

        self.assertFalse(changed)
        self.assertEqual(2, self.scanner._fast_screencap.call_count)
        self.sleep.assert_called_once_with(.1)
        self.assert_single_tap()

    def test_pause_after_toggle_keeps_finish_current_readback_semantics(self):
        self.adb.tap.side_effect = lambda *_args, **_kwargs: self.executor.pause()

        changed, _observed = self.set_star()

        self.assertTrue(changed)
        self.assertTrue(self.executor._paused)
        self.assertEqual(1, self.scanner._pause_generation)
        self.assertEqual(2, self.scanner._fast_screencap.call_count)
        self.sleep.assert_not_called()
        self.assert_single_tap()


if __name__ == "__main__":
    unittest.main()
