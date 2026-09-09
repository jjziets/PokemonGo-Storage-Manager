"""Controller boundaries for the explicit app-only stream source; no ADB."""

import io
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from pokemgr.adb.controller import ADBController, ADBError, StreamCaptureInvalidated
from pokemgr.adb.frame_buffer import FrameBufferError
from pokemgr.adb.stream_capture import StreamCaptureTimeout


QUERY = ["shell", "cmd display get-displays && dumpsys SurfaceFlinger --display-id"]
REPORT = ('Display id 15: DisplayInfo{"scrcpy", real 96 x 128, density 420, '
          'state ON, type VIRTUAL, uniqueId "virtual:scrcpy,5"}\n'
          'Display 115 (Virtual display): displayName="scrcpy"\n')
ENV = {
    "POKEMGR_DISPLAY_ID": "15", "POKEMGR_CAPTURE_DISPLAY_ID": "115",
    "POKEMGR_DEVICE_SERIAL": "phone", "POKEMGR_CAPTURE_FORMAT": "jpeg",
    "POKEMGR_FRAME_BUFFER": "/tmp/private/frames", "POKEMGR_FRAME_SESSION": "a" * 32,
    "POKEMGR_STREAM_PID": "123", "POKEMGR_FRAME_READER": "/tmp/frame-reader.dylib",
}


class StreamControllerTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, ENV, clear=True))
        self.report = REPORT
        self.frame = Image.new("RGB", (96, 128), "white")
        encoded = io.BytesIO()
        self.frame.save(encoded, format="JPEG", quality=100)
        self.jpeg = encoded.getvalue()
        self.adb = ADBController(serial="phone")
        self.adb._run = Mock(side_effect=self.run_adb)
        self.source = Mock()
        self.source.capture.return_value = self.frame
        self.source.frames.return_value = iter((self.frame, self.frame.copy()))
        self.factory = self.enterContext(patch("pokemgr.adb.stream_capture.StreamCapture",
                                               return_value=self.source))
        self.addCleanup(self.adb.close_stream_capture)

    def run_adb(self, args):
        output = self.report.encode() if args == QUERY else self.jpeg if args[:2] == ["exec-out", "screencap"] else b""
        return SimpleNamespace(stdout=output, stderr=b"", returncode=0)

    def capture_commands(self):
        return [call.args[0] for call in self.adb._run.call_args_list
                if call.args[0][:2] == ["exec-out", "screencap"]]

    def test_source_is_lazy_and_frozen_with_verified_geometry(self):
        self.assertTrue(self.adb.has_stream_frames)
        self.factory.assert_not_called()
        os.environ["POKEMGR_FRAME_SESSION"] = "b" * 32
        self.assertIs(self.adb.screencap(), self.frame)
        self.adb.screencap()
        self.factory.assert_called_once_with(
            self.adb, path=ENV["POKEMGR_FRAME_BUFFER"], session="a" * 32,
            writer_pid=123, width=96, height=128, reader_library=ENV["POKEMGR_FRAME_READER"],
        )
        self.assertEqual([QUERY] * 4, [call.args[0] for call in self.adb._run.call_args_list])
        self.assertEqual([], self.capture_commands())

    def test_partial_or_physical_source_configuration_is_rejected(self):
        for missing in ("POKEMGR_FRAME_BUFFER", "POKEMGR_FRAME_SESSION", "POKEMGR_STREAM_PID",
                        "POKEMGR_DISPLAY_ID", "POKEMGR_CAPTURE_DISPLAY_ID"):
            with self.subTest(missing=missing), patch.dict(os.environ, ENV, clear=True):
                os.environ.pop(missing)
                with self.assertRaises(ADBError):
                    ADBController(serial="phone")

    def test_malformed_source_identity_is_rejected(self):
        for key, value in (("POKEMGR_FRAME_BUFFER", "relative"),
                           ("POKEMGR_FRAME_SESSION", "x" * 32),
                           ("POKEMGR_STREAM_PID", "0"),
                           ("POKEMGR_STREAM_PID", "1; echo unsafe"),
                           ("POKEMGR_FRAME_READER", "relative.dylib")):
            with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}):
                with self.assertRaises(ADBError):
                    ADBController(serial="phone")

    def test_unconfigured_legacy_controller_has_no_stream_source(self):
        with patch.dict(os.environ, {}, clear=True):
            adb = ADBController(serial="phone")
        self.assertFalse(adb.has_stream_frames)
        with self.assertRaises(ADBError):
            list(adb.stream_frames(after_ns=1))

    def test_changed_display_never_opens_source(self):
        self.report = REPORT.replace('state ON', 'state OFF')
        with self.assertRaises(ADBError):
            self.adb.screencap()
        self.factory.assert_not_called()
        self.assertEqual([], self.capture_commands())

    def test_source_timeout_falls_back_to_fresh_same_target_jpeg(self):
        self.source.capture.side_effect = StreamCaptureTimeout("no fresh frame")
        frame = self.adb.screencap()
        self.assertEqual((96, 128), frame.size)
        self.assertEqual([["exec-out", "screencap", "-j", "-d", "115"]], self.capture_commands())
        self.assertEqual(3, sum(call.args[0] == QUERY for call in self.adb._run.call_args_list))

    def test_timeout_does_not_fallback_after_display_epoch_changes(self):
        def timeout():
            self.report = REPORT.replace("virtual:scrcpy,5", "virtual:scrcpy,6")
            raise StreamCaptureTimeout("no fresh frame")
        self.source.capture.side_effect = timeout
        with self.assertRaisesRegex(ADBError, "recreated"):
            self.adb.screencap()
        self.assertEqual([], self.capture_commands())

    def test_protocol_failure_is_sticky_and_never_jpeg_fallback(self):
        self.source.capture.side_effect = FrameBufferError("different session")
        for _ in range(2):
            with self.assertRaisesRegex(ADBError, "different session"):
                self.adb.screencap()
        self.assertEqual([], self.capture_commands())
        self.factory.assert_called_once()
        self.source.close.assert_called_once()

    def test_capture_checks_display_again_after_source_wait(self):
        def change():
            self.report = REPORT.replace("virtual:scrcpy,5", "virtual:scrcpy,6")
            return self.frame
        self.source.capture.side_effect = change
        with self.assertRaisesRegex(ADBError, "recreated"):
            self.adb.screencap()
        self.assertEqual([], self.capture_commands())

    def test_invalidation_during_capture_discards_frame_without_jpeg_fallback(self):
        def invalidate():
            self.adb.invalidate_stream_frames()
            return self.frame
        self.source.capture.side_effect = invalidate
        with self.assertRaisesRegex(StreamCaptureInvalidated, "invalidated"):
            self.adb.screencap()
        self.assertIsNone(self.adb._stream_fault)
        self.assertEqual([], self.capture_commands())

    def test_invalidation_during_final_verification_discards_frame(self):
        queries = [0]
        def run(args):
            if args == QUERY:
                queries[0] += 1
                if queries[0] == 2:
                    self.adb.invalidate_stream_frames()
            return self.run_adb(args)
        self.adb._run.side_effect = run
        with self.assertRaisesRegex(StreamCaptureInvalidated, "invalidated"):
            self.adb.screencap()
        self.assertEqual([], self.capture_commands())

    def test_invalidated_timeout_does_not_trigger_jpeg_or_poison_source(self):
        def timeout():
            self.adb.invalidate_stream_frames()
            raise StreamCaptureTimeout("window invalidated")
        self.source.capture.side_effect = timeout
        with self.assertRaises(StreamCaptureInvalidated):
            self.adb.screencap()
        self.assertEqual([], self.capture_commands())
        self.assertIsNone(self.adb._stream_fault)

    def test_invalidation_during_jpeg_fallback_discards_the_result(self):
        self.source.capture.side_effect = StreamCaptureTimeout("no fresh frame")
        def run(args):
            if args[:2] == ["exec-out", "screencap"]:
                self.adb.invalidate_stream_frames()
            return self.run_adb(args)
        self.adb._run.side_effect = run
        with self.assertRaises(StreamCaptureInvalidated):
            self.adb.screencap()
        self.assertEqual(1, len(self.capture_commands()))
        self.assertIsNone(self.adb._stream_fault)

    def test_window_validates_entry_and_exit_without_per_frame_adb(self):
        frames = list(self.adb.stream_frames(after_ns=100, max_frames=2))
        self.assertEqual(2, len(frames))
        self.assertEqual([QUERY, QUERY], [call.args[0] for call in self.adb._run.call_args_list])

    def test_early_window_close_validates_display(self):
        window = self.adb.stream_frames(after_ns=100)
        self.assertIs(next(window), self.frame)
        self.report = REPORT.replace("virtual:scrcpy,5", "virtual:scrcpy,6")
        with self.assertRaisesRegex(ADBError, "recreated"):
            window.close()

    def test_new_input_ends_active_window_and_marks_completion_time(self):
        window = self.adb.stream_frames(after_ns=100)
        next(window)
        with patch("pokemgr.adb.controller.time.monotonic_ns", return_value=300):
            self.adb._input("tap", 1, 2)
        self.source.mark_input.assert_called_once_with(300)
        self.assertEqual([], list(window))

    def test_pause_does_not_advance_source_after_clock_invalidation(self):
        def source_frames(**kwargs):
            yield self.frame
            raise AssertionError("Invalidated source must not advance")
        self.source.frames.side_effect = source_frames
        window = self.adb.stream_frames(after_ns=100)
        next(window)
        self.adb.invalidate_stream_frames()
        self.assertEqual([], list(window))

    def test_input_before_lazy_source_creation_preserves_freshness_boundary(self):
        with patch("pokemgr.adb.controller.time.monotonic_ns", return_value=300):
            self.adb._input("tap", 1, 2)
        self.factory.assert_not_called()
        self.adb.screencap()
        self.source.mark_input.assert_called_once_with(300)

    def test_failed_input_does_not_mark_success(self):
        self.adb.screencap()
        self.adb.shell = Mock(side_effect=ADBError("failed input"))
        with self.assertRaises(ADBError):
            self.adb._input("tap", 1, 2)
        self.source.mark_input.assert_not_called()

    def test_invalidation_and_close_release_existing_source(self):
        self.adb.screencap()
        self.adb.invalidate_stream_frames()
        self.source.invalidate.assert_called_once()
        self.adb.close_stream_capture()
        self.adb.close_stream_capture()
        self.source.close.assert_called_once()

    def test_window_bounds_rejected_without_reading_source(self):
        for arguments in ({"after_ns": 0}, {"after_ns": True},
                          {"after_ns": 1, "max_frames": 31},
                          {"after_ns": 1, "timeout": 1.3}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                list(self.adb.stream_frames(**arguments))
        self.factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
