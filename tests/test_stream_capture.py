"""Freshness selection over a video ring must use original source time."""
# TRACEWEAVER: file-role=fresh-stream-capture-tests; verifies=VER-SCAN-001; req=REQ-STREAM-001; trace=TRACE-STREAM-001

import unittest
import threading
from unittest.mock import Mock, PropertyMock, call, patch

from PIL import Image

from pokemgr.adb.clock_sync import FrameTimeBounds
from pokemgr.adb.frame_buffer import FrameBufferError, StreamFrame
from pokemgr.adb.stream_capture import StreamCapture, StreamCaptureTimeout


class StreamCaptureTests(unittest.TestCase):
    def setUp(self):
        self.now = 100_000_000_000
        self.enterContext(patch("pokemgr.adb.stream_capture.time.monotonic_ns", side_effect=lambda: self.now))
        self.enterContext(patch("pokemgr.adb.stream_capture.time.monotonic", side_effect=lambda: self.now / 1e9))
        self.enterContext(patch("pokemgr.adb.stream_capture.time.sleep", side_effect=self.sleep))
        self.source = StreamCapture.__new__(StreamCapture)
        self.source.buffer = Mock()
        self.source.clock = Mock()
        self.source._clock_sampler = Mock()
        self.source._synchronize = Mock()
        self.source._after_ns = self.now - 10_000_000
        self.source._last_sequence = 0
        self.source._last_pts_us = -1
        self.source._epoch = 0
        self.source._pending_invalidation = False
        self.source._synced_at_ns = self.now - 1
        self.source._lock = threading.Lock()
        self.source.clock.map_pts_us.side_effect = lambda pts, **kwargs: FrameTimeBounds(
            pts * 1000, pts * 1000, 1, self.now + 30_000_000_000,
        )

    def sleep(self, seconds):
        self.now += round(seconds * 1e9)

    def frame(self, seq, timestamp_ns):
        return StreamFrame("a" * 32, seq, timestamp_ns // 1000, self.now,
                           Image.new("RGB", (2, 4), (seq, 2, 3)))

    def test_recent_arrival_cannot_make_pre_request_pixels_fresh(self):
        started = self.now
        old = self.frame(1, started - 1_000_000)
        self.source.buffer.latest_sequence = 1
        self.source.buffer.read.return_value = old
        with self.assertRaises(StreamCaptureTimeout):
            self.source.capture(timeout=0.02)
        self.assertEqual(0, self.source._last_sequence)
        self.assertEqual(-1, self.source._last_pts_us)
        self.source.buffer.read.assert_called_once_with(1)
        self.source.clock.map_pts_us.assert_called_once()
        self.assertEqual(started + 20_000_000, self.now)

    def test_timeout_reports_no_publication_without_extra_frame_read(self):
        self.source._last_sequence = 7
        self.source.buffer.latest_sequence = 7
        with self.assertRaises(StreamCaptureTimeout) as failure:
            self.source.capture(timeout=0.02)
        self.assertEqual({"reason": "no_new_frames", "latest_sequence": 7,
                          "last_accepted_sequence": 7, "examined_frames": 0},
                         failure.exception.details)
        self.source.buffer.read.assert_not_called()
        self.source.clock.map_pts_us.assert_not_called()

    def test_timeout_distinguishes_old_source_from_recent_decoder_arrival(self):
        started = self.now
        old = self.frame(1, started - 500_000_000)
        self.source.buffer.latest_sequence = 1
        self.source.buffer.read.return_value = old
        with self.assertRaises(StreamCaptureTimeout) as failure:
            self.source.capture(timeout=0.02)
        details = failure.exception.details
        self.assertEqual("rejected_stale", details["reason"])
        self.assertEqual([520.0, 520.0], details["source_age_ms"])
        self.assertEqual(20.0, details["host_arrival_age_ms"])
        self.assertEqual(-500.0, details["boundary_delta_ms"])
        self.assertEqual(old.pts_us, details["pts_us"])
        self.assertEqual(1, details["clock_generation"])
        self.assertEqual(0, self.source._last_sequence)
        self.source.buffer.read.assert_called_once_with(1)
        self.source.clock.map_pts_us.assert_called_once()

    def test_timeout_distinguishes_input_and_request_boundaries(self):
        for age, expected in ((11_000_000, "rejected_preinput"),
                              (1_000_000, "rejected_pre_request")):
            with self.subTest(expected=expected):
                self.source._after_ns = self.now - 10_000_000
                self.source.buffer.latest_sequence = 1
                self.source.buffer.read.return_value = self.frame(1, self.now - age)
                with self.assertRaises(StreamCaptureTimeout) as failure:
                    self.source.capture(timeout=0.02)
                self.assertEqual(expected, failure.exception.details["reason"])
                self.assertEqual(0, self.source._last_sequence)

    def test_timeout_reports_slot_race_without_invented_source_age(self):
        self.source.buffer.latest_sequence = 1
        self.source.buffer.read.return_value = None
        with self.assertRaises(StreamCaptureTimeout) as failure:
            self.source.capture(timeout=0.02)
        self.assertEqual("rejected_slot_race", failure.exception.details["reason"])
        self.assertNotIn("source_age_ms", failure.exception.details)
        self.assertNotIn("host_arrival_age_ms", failure.exception.details)
        self.source.buffer.read.assert_called_once_with(1)
        self.source.clock.map_pts_us.assert_not_called()

    def test_timeout_reports_nonincreasing_pts_without_remapping(self):
        self.source._last_pts_us = self.now // 1000
        self.source.buffer.latest_sequence = 2
        self.source.buffer.read.return_value = self.frame(2, self.now)
        with self.assertRaises(StreamCaptureTimeout) as failure:
            self.source.capture(timeout=0.02)
        self.assertEqual("rejected_nonincreasing_pts", failure.exception.details["reason"])
        self.assertNotIn("source_age_ms", failure.exception.details)
        self.source.clock.map_pts_us.assert_not_called()

    def test_repeated_stale_sequence_is_copied_once_before_fresh_frame_arrives(self):
        started = self.now
        old, new = self.frame(1, started - 1_000_000), self.frame(2, started + 3_000_000)
        self.source.buffer.latest_sequence = 1
        self.source.buffer.read.side_effect = lambda sequence: {1: old, 2: new}[sequence]

        def publish_after_wait(seconds):
            self.sleep(seconds)
            if self.now - started >= 15_000_000:
                self.source.buffer.latest_sequence = 2

        self.enterContext(patch("pokemgr.adb.stream_capture.time.sleep",
                                side_effect=publish_after_wait))
        image = self.source.capture(timeout=0.02)
        self.assertIs(new.image, image)
        self.assertGreater(image.info["pokemgr_capture_started_at"], started / 1e9)
        self.assertEqual(2, self.source._last_sequence)
        self.assertEqual([call(1), call(2)], self.source.buffer.read.call_args_list)
        self.assertEqual(2, self.source.clock.map_pts_us.call_count)
        self.assertEqual(started + 15_000_000, self.now)

    def test_rejected_sequence_is_not_consumed_by_a_timed_out_request(self):
        self.source.buffer.latest_sequence = 1
        self.source.buffer.read.return_value = self.frame(1, self.now - 1_000_000)
        for _ in range(2):
            with self.assertRaises(StreamCaptureTimeout):
                self.source.capture(timeout=0.02)
        self.assertEqual([call(1), call(1)], self.source.buffer.read.call_args_list)
        self.assertEqual(0, self.source._last_sequence)

    def test_raced_slot_waits_for_a_later_published_sequence(self):
        started = self.now
        new = self.frame(2, started + 3_000_000)
        self.source.buffer.read.side_effect = lambda sequence: None if sequence == 1 else new
        with patch.object(type(self.source.buffer), "latest_sequence",
                          new_callable=PropertyMock, create=True) as latest:
            latest.side_effect = [1, 1, 2]
            self.assertIs(new.image, self.source.capture(timeout=0.02))
        self.assertEqual([call(1), call(2)], self.source.buffer.read.call_args_list)

    def _assert_barrier_interrupts_stale_wait(self, barrier):
        self.source.buffer.latest_sequence = 1
        self.source.buffer.read.return_value = self.frame(1, self.now - 1_000_000)

        def interrupt_wait(seconds):
            self.sleep(seconds)
            barrier()

        with patch("pokemgr.adb.stream_capture.time.sleep", side_effect=interrupt_wait):
            with self.assertRaisesRegex(StreamCaptureTimeout, "invalidated"):
                self.source.capture(timeout=0.1)
        self.source.buffer.read.assert_called_once_with(1)
        self.assertEqual(0, self.source._last_sequence)
        self.assertEqual(-1, self.source._last_pts_us)

    def test_pause_invalidates_even_when_latest_sequence_was_already_rejected(self):
        self._assert_barrier_interrupts_stale_wait(self.source.invalidate)
        self.assertTrue(self.source._pending_invalidation)

    def test_input_invalidates_even_when_latest_sequence_was_already_rejected(self):
        self._assert_barrier_interrupts_stale_wait(self.source.mark_input)
        self.assertFalse(self.source._pending_invalidation)

    def test_producer_identity_still_checked_while_waiting_after_rejected_sequence(self):
        self.source.buffer.read.return_value = self.frame(1, self.now - 1_000_000)
        with patch.object(type(self.source.buffer), "latest_sequence",
                          new_callable=PropertyMock, create=True) as latest:
            latest.side_effect = [1, FrameBufferError("Stream frame producer belongs to another session")]
            with self.assertRaisesRegex(FrameBufferError, "another session"):
                self.source.capture(timeout=0.02)
        self.source.buffer.read.assert_called_once_with(1)

    def test_duplicate_pts_cannot_be_a_second_independent_frame(self):
        self.source._last_pts_us = self.now // 1000
        self.source.buffer.read.return_value = self.frame(2, self.now)
        self.assertIsNone(self.source._image(2, after_ns=self.now - 1_000_000))

    # TRACEWEAVER: verifies=VER-SCAN-001; req=REQ-SCAN-003; trace=TRACE-SCAN-003
    def test_image_carries_its_own_bounds_continuity_without_substituting_current_clock(self):
        token = "b" * 32
        self.source.clock.continuity_token = "c" * 32
        self.source.clock.map_pts_us.side_effect = None
        self.source.clock.map_pts_us.return_value = FrameTimeBounds(
            self.now - 3000, self.now - 1000, 4, self.now + 10000, token,
        )
        self.source.buffer.read.return_value = self.frame(1, self.now - 3000)
        image = self.source._image(1, after_ns=self.now - 10000)
        self.assertEqual(token, image.info["pokemgr_source_clock_continuity"])
        self.assertEqual(4, image.info["pokemgr_source_clock_generation"])
        self.assertEqual((self.now - 3000) / 1e9, image.info["pokemgr_capture_started_at"])
        self.assertEqual((self.now - 1000) / 1e9, image.info["pokemgr_capture_finished_at"])

    def test_legacy_bounds_do_not_gain_a_continuity_token(self):
        self.source.buffer.read.return_value = self.frame(1, self.now - 3000)
        image = self.source._image(1, after_ns=self.now - 10000)
        self.assertIsNone(image.info["pokemgr_source_clock_continuity"])

    def test_continuity_token_cannot_override_input_or_frame_age_rejection(self):
        for lower in (self.source._after_ns, self.now - 250_000_001):
            with self.subTest(earliest_ns=lower):
                self.source.clock.map_pts_us.side_effect = None
                self.source.clock.map_pts_us.return_value = FrameTimeBounds(
                    lower, self.now - 1000, 4, self.now + 10000, "b" * 32,
                )
                self.source.buffer.read.return_value = self.frame(1, self.now - 3000)
                self.assertIsNone(self.source._image(1, after_ns=self.source._after_ns))
                self.assertEqual(0, self.source._last_sequence)

    def test_input_barrier_and_oldest_possible_source_time_are_enforced(self):
        self.source.mark_input()
        self.now += 50_000_000
        self.source.buffer.read.return_value = self.frame(1, self.now - 5_000_000)
        self.source.clock.map_pts_us.return_value = None
        self.source.clock.map_pts_us.side_effect = lambda *args, **kwargs: FrameTimeBounds(
            self.source._after_ns - 1, self.now - 1, 1, self.now + 1_000_000_000,
        )
        self.assertIsNone(self.source._image(1, after_ns=self.source._after_ns))

    def test_window_has_bounded_count_and_preserves_chronological_pts(self):
        self.source.buffer.sequences.return_value = [1, 2, 3]
        self.source.buffer.read.side_effect = [self.frame(i, self.now - 4_000_000 + i * 1000)
                                               for i in (1, 2, 3)]
        frames = list(self.source.frames(after_ns=self.now - 8_000_000, max_frames=2))
        self.assertEqual(2, len(frames))
        self.assertEqual(2, self.source._last_sequence)
        self.assertEqual(2, self.source.buffer.read.call_count)

    def test_invalidated_clock_requires_new_synchronization_and_frame_boundary(self):
        self.source._synced_at_ns = self.now - 1
        self.source.invalidate()
        self.source.clock.invalidate.assert_not_called()
        self.assertTrue(self.source._pending_invalidation)
        self.assertEqual(0, self.source._synced_at_ns)
        self.assertEqual(self.now, self.source._after_ns)
        StreamCapture._synchronize(self.source)
        self.source.clock.invalidate.assert_called_once()
        self.source.clock.observe.assert_called_once_with(self.source._clock_sampler.sample.return_value)
        self.assertFalse(self.source._pending_invalidation)

    def test_pause_during_clock_exchange_discards_sample_without_poisoning_source(self):
        self.source._synced_at_ns = 0
        self.source._clock_sampler.sample.side_effect = lambda: self.source.invalidate() or object()
        self.source._synchronize = lambda: StreamCapture._synchronize(self.source)
        with self.assertRaisesRegex(StreamCaptureTimeout, "invalidated"):
            self.source.capture()
        self.source.clock.observe.assert_not_called()
        self.source.clock.map_pts_us.assert_not_called()
        self.assertTrue(self.source._pending_invalidation)

    def test_pause_during_frame_copy_does_not_map_against_invalidated_clock(self):
        frame = self.frame(1, self.now + 1_000)
        self.source.buffer.latest_sequence = 1
        self.source.buffer.read.side_effect = lambda sequence: self.source.invalidate() or frame
        with self.assertRaisesRegex(StreamCaptureTimeout, "invalidated"):
            self.source.capture(timeout=0.1)
        self.source.clock.map_pts_us.assert_not_called()
        self.assertEqual(0, self.source._last_sequence)

    def test_pause_while_window_is_yielded_ends_without_another_frame(self):
        self.source.buffer.sequences.return_value = [1, 2]
        self.source.buffer.read.side_effect = [self.frame(i, self.now - 1000 + i * 100)
                                               for i in (1, 2)]
        window = self.source.frames(after_ns=self.now - 1_000_000)
        next(window)
        self.source.invalidate()
        self.assertEqual([], list(window))
        self.assertEqual(1, self.source.buffer.read.call_count)
        self.source.clock.invalidate.assert_not_called()

    def test_input_during_frame_copy_discards_copy_without_resetting_calibration(self):
        frame = self.frame(1, self.now + 1000)
        self.source.buffer.read.side_effect = lambda sequence: self.source.mark_input() or frame
        self.assertIsNone(self.source._image(1, after_ns=self.now - 1, epoch=0))
        self.source.clock.map_pts_us.assert_not_called()
        self.assertFalse(self.source._pending_invalidation)

    def test_stop_does_not_read_more_frames_and_close_releases_both_resources(self):
        self.assertEqual([], list(self.source.frames(after_ns=self.now, should_stop=lambda: True)))
        self.source.buffer.read.assert_not_called()
        self.source.close()
        self.source.buffer.close.assert_called_once()
        self.source._clock_sampler.close.assert_called_once()
