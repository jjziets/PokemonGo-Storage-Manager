"""Freshness selection over a video ring must use original source time."""

import unittest
import threading
from unittest.mock import Mock, patch

from PIL import Image

from pokemgr.adb.clock_sync import FrameTimeBounds
from pokemgr.adb.frame_buffer import StreamFrame
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

    def test_capture_waits_for_source_after_request_even_without_any_input(self):
        started = self.now
        old, new = self.frame(1, started - 1_000_000), self.frame(2, started + 3_000_000)
        self.source.buffer.latest_sequence = 2
        self.source.buffer.read.side_effect = [old, new]
        image = self.source.capture(timeout=0.02)
        self.assertGreater(image.info["pokemgr_capture_started_at"], started / 1e9)
        self.assertEqual(2, self.source._last_sequence)

    def test_duplicate_pts_cannot_be_a_second_independent_frame(self):
        self.source._last_pts_us = self.now // 1000
        self.source.buffer.read.return_value = self.frame(2, self.now)
        self.assertIsNone(self.source._image(2, after_ns=self.now - 1_000_000))

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
