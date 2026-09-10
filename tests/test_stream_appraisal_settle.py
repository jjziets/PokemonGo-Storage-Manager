"""One bounded stream window can supply both independent settled appraisals."""

# TRACEWEAVER: file-role=stream-appraisal-settle-tests; req=REQ-SCAN-002; trace=TRACE-SCAN-002; verifies=VER-SCAN-001

import unittest
from unittest.mock import Mock, patch

from PIL import Image

from pokemgr.adb.controller import ADBError, StreamCaptureInvalidated
from pokemgr.indexer.state_machine import IndexingStateMachine
from tests.test_stable_scan_loop import profile


NOW_NS = 100_000_000_000


def frame(sequence=1, offset_us=10_000, *, color="white", generation=1,
          session="a" * 32, screen="appraisal", bars=True, streamed=True):
    image = Image.new("RGB", (96, 237), color)
    image.info.update(screen=screen, bars=bars)
    if streamed:
        image.info.update(
            pokemgr_stream_session=session, pokemgr_stream_sequence=sequence,
            pokemgr_stream_pts_us=100_000_000 + offset_us,
            pokemgr_source_clock_generation=generation,
            pokemgr_capture_started_at=100 + offset_us / 1e6,
        )
    return image


class StreamAppraisalSettleTests(unittest.TestCase):
    def setUp(self):
        self.adb = Mock(has_stream_frames=True)
        self.sm = IndexingStateMachine(self.adb, profile(), Mock())
        self.sm.nav.detect_screen = Mock(side_effect=lambda image: image.info["screen"])
        self.sm.reader.are_bars_visible = Mock(side_effect=lambda image: image.info["bars"])
        self.sm.reader.appraisal_bars_stable = Mock(return_value=True)
        self.sm._fast_screencap = Mock(side_effect=AssertionError("Unexpected legacy capture"))
        self.enterContext(patch("pokemgr.indexer.state_machine.time.monotonic_ns", return_value=NOW_NS))
        self.enterContext(patch("pokemgr.indexer.state_machine.random.uniform", return_value=.15))
        self.delay = self.enterContext(patch("pokemgr.indexer.state_machine.human_delay"))
        self.enterContext(patch("pokemgr.config.STABLE_FRAME_INTERVAL", (.15, .10)))
        self.closed = []

    def windows(self, windows, *, on_close=None):
        pending = iter(windows)

        def stream(**kwargs):
            images = next(pending)
            try:
                for image in images:
                    if kwargs["should_stop"]():
                        return
                    yield image
            finally:
                self.assertIsNone(self.sm._settled_frame_pair)
                self.closed.append(images)
                if on_close:
                    on_close()

        self.adb.stream_frames = Mock(side_effect=stream)

    def fallback(self, images=None):
        images = images or [frame(streamed=False), frame(streamed=False)]
        self.sm._fast_screencap = Mock(side_effect=images)
        return images

    def assert_no_input(self):
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()

    def test_one_closed_window_retains_two_source_frames_at_configured_spacing(self):
        first, early, newest = frame(), frame(2, 159_999), frame(3, 160_000)
        self.windows([[first, early, newest]])
        result, status = self.sm._wait_for_stable_appraisal()
        self.assertEqual(status, "stable")
        self.assertIs(result, newest)
        self.assertEqual(self.sm._settled_frame_pair, (first, newest))
        self.assertEqual(len(self.closed), 1)
        self.assertEqual(self.adb.stream_frames.call_count, 1)
        args = self.adb.stream_frames.call_args.kwargs
        self.assertEqual((args["after_ns"], args["timeout"], args["max_frames"]), (NOW_NS, 1.2, 30))
        self.assertEqual(self.sm.reader.are_bars_visible.call_count, 2)
        self.sm._fast_screencap.assert_not_called()
        self.delay.assert_not_called()
        self.assert_no_input()

    def test_changed_identity_preserves_strict_transition_proof(self):
        prior = frame(streamed=False)
        first, newest = frame(color="black"), frame(2, 160_000, color="black")
        self.windows([[first, newest]])
        result, status = self.sm._wait_for_stable_appraisal(prior, require_transition=True)
        self.assertIs(result, newest)
        self.assertEqual(status, "stable")

    # TRACEWEAVER: entrypoint=test_moving_bars_delay_settle_without_extra_wait; req=REQ-SCAN-002; trace=TRACE-SCAN-002; ver=VER-SCAN-001
    def test_moving_bars_delay_settle_without_extra_wait(self):
        first, moving, settled = frame(), frame(2, 160_000), frame(3, 310_000)
        self.windows([[first, moving, settled]])
        self.sm.reader.appraisal_bars_stable.side_effect = [False, True]

        result, status = self.sm._wait_for_stable_appraisal()

        self.assertEqual("stable", status)
        self.assertIs(result, settled)
        self.assertEqual((moving, settled), self.sm._settled_frame_pair)
        self.assertEqual(2, self.sm.reader.appraisal_bars_stable.call_count)
        self.delay.assert_not_called()
        self.assert_no_input()

    def test_no_transition_after_six_comparisons_still_fails_strict_callers(self):
        images = [frame(i + 1, 10_000 + i * 150_000) for i in range(7)]
        self.windows([images])
        result, status = self.sm._wait_for_stable_appraisal(frame(streamed=False), require_transition=True)
        self.assertIsNone(result)
        self.assertEqual(status, "transition_not_observed")
        self.assertEqual(self.sm.reader.are_bars_visible.call_count, 12)
        self.sm._fast_screencap.assert_not_called()

    def test_unobserved_and_returned_visual_identity_keep_typed_fallback_status(self):
        for transient, expected in ((False, "stable_transition_unobserved"),
                                    (True, "stable_raw_identity_unchanged")):
            with self.subTest(transient=transient):
                self.sm._settled_frame_pair = None
                colors = ["black", "white", "white"] if transient else ["white", "white"]
                images = [frame(i + 1, 10_000 + i * 150_000, color=color)
                          for i, color in enumerate(colors)]
                self.windows([images])
                result, status = self.sm._wait_for_stable_appraisal(
                    frame(streamed=False), require_transition=True, allow_structured_fallback=True,
                )
                self.assertIs(result, images[-1])
                self.assertEqual(status, expected)

    def test_new_window_discards_old_pair_before_success(self):
        old = [frame(), frame(2, 160_000, color="black")]
        fresh = [frame(3, 200_000), frame(4, 350_000)]
        self.windows([old, fresh])
        result, status = self.sm._wait_for_stable_appraisal()
        self.assertEqual(status, "stable")
        self.assertIs(result, fresh[-1])
        self.assertEqual(self.sm._settled_frame_pair, tuple(fresh))
        self.assertEqual(len(self.closed), 2)
        self.sm._fast_screencap.assert_not_called()

    def test_empty_window_uses_fresh_legacy_pair(self):
        self.windows([[]])
        fresh = self.fallback()
        result, status = self.sm._wait_for_stable_appraisal()
        self.assertEqual(status, "stable")
        self.assertIs(result, fresh[-1])
        self.assertEqual(self.sm._settled_frame_pair, tuple(fresh))
        self.assertEqual(self.sm._fast_screencap.call_count, 2)

    def test_conflicting_stream_identity_or_reordered_source_discards_window(self):
        cases = [
            [frame(), frame(2, 160_000, generation=2)],
            [frame(), frame(2, 160_000, session="b" * 32)],
            [frame(), frame(2, 10_000)],
            [frame(), frame(3, 20_000), frame(2, 160_000)],
            [frame(), frame(2, 160_000, streamed=False)],
            [frame(offset_us=-1)],
        ]
        for images in cases:
            with self.subTest(images=[image.info for image in images]):
                self.sm._settled_frame_pair = None
                self.windows([images])
                fresh = self.fallback()
                result, status = self.sm._wait_for_stable_appraisal()
                self.assertEqual(status, "stable")
                self.assertIs(result, fresh[-1])
                self.assertEqual(self.sm._settled_frame_pair, tuple(fresh))

    def test_final_display_failure_cannot_publish_pair_or_start_fallback(self):
        self.windows([[frame(), frame(2, 160_000)]],
                     on_close=Mock(side_effect=ADBError("display changed")))
        with self.assertRaisesRegex(ADBError, "display changed"):
            self.sm._wait_for_stable_appraisal()
        self.assertIsNone(self.sm._settled_frame_pair)
        self.sm._fast_screencap.assert_not_called()

    def test_pause_resume_during_final_validation_discards_window_proof(self):
        def interrupted():
            self.sm.pause()
            self.sm.resume()
        self.windows([[frame(), frame(2, 160_000)]], on_close=interrupted)
        fresh = self.fallback()
        result, status = self.sm._wait_for_stable_appraisal()
        self.assertEqual(status, "stable")
        self.assertIs(result, fresh[-1])
        self.assertIsNone(self.sm._settled_frame_pair)
        self.assert_no_input()

    def test_abort_during_final_validation_stops_without_returning_evidence(self):
        self.windows([[frame(), frame(2, 160_000)]], on_close=self.sm.abort)
        result, status = self.sm._wait_for_stable_appraisal()
        self.assertEqual((result, status), (None, "aborted"))
        self.sm._fast_screencap.assert_not_called()
        self.assertIsNone(self.sm._settled_frame_pair)

    def test_transient_close_failure_during_pause_uses_only_fresh_fallback(self):
        for resume in (False, True):
            with self.subTest(resume=resume):
                self.sm._paused = False

                def pause_on_close():
                    self.sm.pause()
                    if resume:
                        self.sm.resume()
                    raise StreamCaptureInvalidated("capture generation changed")

                self.windows([[frame(), frame(2, 160_000)]], on_close=pause_on_close)
                fresh = self.fallback()
                result, status = self.sm._wait_for_stable_appraisal()
                self.assertEqual(status, "stable")
                self.assertIs(result, fresh[-1])
                self.assertEqual(self.sm._fast_screencap.call_count, 2)
                self.assertIsNone(self.sm._settled_frame_pair)
                self.assert_no_input()

    def test_transient_close_failure_after_abort_does_not_retry(self):
        def abort_on_close():
            self.sm.abort()
            raise StreamCaptureInvalidated("capture generation changed")

        self.windows([[frame(), frame(2, 160_000)]], on_close=abort_on_close)
        result, status = self.sm._wait_for_stable_appraisal()
        self.assertEqual((result, status), (None, "aborted"))
        self.sm._fast_screencap.assert_not_called()
        self.assertIsNone(self.sm._settled_frame_pair)

    def test_stream_and_legacy_share_one_six_comparison_budget(self):
        self.windows([[frame(), frame(2, 160_000, color="black"), frame(3, 310_000)], []])
        legacy = [frame(streamed=False, color="white" if i % 2 else "black") for i in range(5)]
        self.fallback(legacy)
        result, status = self.sm._wait_for_stable_appraisal()
        self.assertEqual((result, status), (None, "appraisal_not_stable"))
        self.assertEqual(self.sm.reader.are_bars_visible.call_count, 12)
        self.assertEqual(self.sm._fast_screencap.call_count, 5)
        self.assertEqual(self.adb.stream_frames.call_count, 2)

    def test_stream_requires_both_appraisal_screens_even_if_bars_match(self):
        self.windows([[frame(screen="detail"), frame(2, 160_000, screen="detail")], []])
        fresh = self.fallback()
        result, status = self.sm._wait_for_stable_appraisal()
        self.assertEqual(status, "stable")
        self.assertIs(result, fresh[-1])
        self.assertEqual(self.sm.reader.are_bars_visible.call_count, 2)


if __name__ == "__main__":
    unittest.main()
