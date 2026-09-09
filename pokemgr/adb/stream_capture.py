"""Fresh-frame selection over the paired decoder's 30-frame ring."""

import time
import threading

from .clock_sync import AndroidClockSync, ClockSampler
from .frame_buffer import FrameBuffer


class StreamCaptureTimeout(RuntimeError):
    pass


class StreamCapture:
    def __init__(self, adb, path, *, session, writer_pid, width, height,
                 reader_library=None):
        self.adb = adb
        self.buffer = FrameBuffer(path, session=session, writer_pid=writer_pid,
                                  width=width, height=height,
                                  reader_library=reader_library)
        self.clock = AndroidClockSync()
        self._clock_sampler = ClockSampler(adb)
        self._synced_at_ns = 0
        self._after_ns = time.monotonic_ns()
        self._last_sequence = 0
        self._last_pts_us = -1
        self._epoch = 0
        self._pending_invalidation = False
        self._lock = threading.Lock()

    def _synchronize(self):
        # Only the scan thread changes the mapping. UI pause/resume can mark
        # an epoch immediately without clearing a clock another operation uses.
        with self._lock:
            epoch = self._epoch
            if self._pending_invalidation:
                self.clock.invalidate()
                self._pending_invalidation = False
                self._synced_at_ns = 0
            now = time.monotonic_ns()
            if self._synced_at_ns and now - self._synced_at_ns <= 10_000_000_000:
                return
        sample = self._clock_sampler.sample()
        with self._lock:
            if epoch != self._epoch:
                return
            self.clock.observe(sample)
            self._synced_at_ns = time.monotonic_ns()

    def mark_input(self, completed_ns=None):
        completed = time.monotonic_ns() if completed_ns is None else completed_ns
        if type(completed) is not int or completed <= 0:
            raise ValueError("Input completion must use positive monotonic nanoseconds")
        with self._lock:
            self._after_ns = max(self._after_ns, completed)
            self._epoch += 1

    def invalidate(self):
        with self._lock:
            self._epoch += 1
            self._pending_invalidation = True
            self._synced_at_ns = 0
            self._after_ns = max(self._after_ns, time.monotonic_ns())

    def _current_epoch(self):
        with self._lock:
            return self._epoch

    def _image(self, sequence, *, after_ns, max_age_ns=250_000_000, epoch=None):
        frame = self.buffer.read(sequence)
        if frame is None:
            return None
        with self._lock:
            if (self._pending_invalidation or (epoch is not None and epoch != self._epoch)
                    or frame.pts_us <= self._last_pts_us):
                return None
            now = time.monotonic_ns()
            bounds = self.clock.map_pts_us(frame.pts_us, now_ns=now,
                                          received_ns=frame.received_ns)
            if not (bounds.strictly_after(max(after_ns, self._after_ns))
                    and bounds.fresh_at(now, max_age_ns=max_age_ns)):
                return None
            self._last_sequence = frame.sequence
            self._last_pts_us = frame.pts_us
            frame.image.info.update(
                pokemgr_capture_started_at=bounds.earliest_ns / 1e9,
                pokemgr_capture_finished_at=bounds.latest_ns / 1e9,
                pokemgr_source_clock_generation=bounds.sync_generation,
            )
            return frame.image

    def capture(self, *, timeout=1.5):
        """Select a new source frame, never merely a recently decoded old frame."""
        epoch = self._current_epoch()
        requested_ns = time.monotonic_ns()
        self._synchronize()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if epoch != self._current_epoch():
                raise StreamCaptureTimeout("Stream capture was invalidated")
            latest = self.buffer.latest_sequence
            if latest > self._last_sequence:
                image = self._image(latest, after_ns=requested_ns, epoch=epoch)
                if image is not None:
                    if epoch != self._current_epoch():
                        raise StreamCaptureTimeout("Stream capture was invalidated")
                    return image
            time.sleep(0.005)
        raise StreamCaptureTimeout("No new source frame after the last input")

    def frames(self, *, after_ns, timeout=1.2, max_frames=30, should_stop=lambda: False):
        """Yield a bounded chronological window after a gesture; no OCR queue."""
        if not 1 <= max_frames <= 30 or not 0 < timeout <= 3:
            raise ValueError("Stream observation window exceeds its bounds")
        epoch = self._current_epoch()
        self._synchronize()
        boundary = max(after_ns, self._after_ns)
        deadline = time.monotonic() + timeout
        count = 0
        considered = self._last_sequence
        while (count < max_frames and time.monotonic() < deadline
               and not should_stop() and epoch == self._current_epoch()):
            for sequence in self.buffer.sequences(after=considered):
                if (should_stop() or count >= max_frames or time.monotonic() >= deadline
                        or epoch != self._current_epoch()):
                    return
                considered = sequence
                image = self._image(sequence, after_ns=boundary, max_age_ns=1_500_000_000,
                                    epoch=epoch)
                if image is not None:
                    if epoch != self._current_epoch():
                        return
                    count += 1
                    yield image
            time.sleep(0.005)

    def close(self):
        try:
            self._clock_sampler.close()
        finally:
            self.buffer.close()
