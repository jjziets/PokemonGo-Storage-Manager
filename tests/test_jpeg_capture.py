"""Fast app-display capture keeps request freshness and display identity."""

import io
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from pokemgr.adb.controller import ADBController, ADBError
from scripts import stream_pokemon


DISPLAY_ID = 15
CAPTURE_ID = 11529215049929616469
SIZE = (96, 128)
REPORT = (
    'Display id 15: DisplayInfo{"scrcpy", real 96 x 128, density 420, '
    'state ON, type VIRTUAL, uniqueId "virtual:scrcpy,5"}\n'
    f'Display {CAPTURE_ID} (Virtual display): displayName="scrcpy"\n'
)
QUERY = ["shell", "cmd display get-displays && dumpsys SurfaceFlinger --display-id"]


def encoded(format, size=SIZE, color="white"):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=format, quality=100)
    return buffer.getvalue()


def result(data=b"", error=b"", status=0):
    return SimpleNamespace(stdout=data, stderr=error, returncode=status)


class JpegCaptureTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for key in ("POKEMGR_DISPLAY_ID", "POKEMGR_CAPTURE_DISPLAY_ID",
                    "POKEMGR_CAPTURE_FORMAT", "POKEMGR_DEVICE_SERIAL"):
            os.environ.pop(key, None)
        self.jpeg = encoded("JPEG")
        self.png = encoded("PNG")
        self.report = REPORT

    def controller(self, captures, format="jpeg"):
        os.environ.update({
            "POKEMGR_DISPLAY_ID": str(DISPLAY_ID),
            "POKEMGR_CAPTURE_DISPLAY_ID": str(CAPTURE_ID),
            "POKEMGR_CAPTURE_FORMAT": format,
        })
        adb = ADBController(serial="phone")
        captures = iter(captures)

        def run(args):
            if args == QUERY:
                return result(self.report.encode())
            self.assertEqual(["exec-out", "screencap"], args[:2])
            self.assertEqual(["-d", str(CAPTURE_ID)], args[3:])
            return next(captures)

        adb._run = Mock(side_effect=run)
        adb._screencap_file = Mock(side_effect=AssertionError("Physical fallback is forbidden"))
        return adb

    def flags(self, adb):
        return [call.args[0][2] for call in adb._run.call_args_list
                if call.args[0][:2] == ["exec-out", "screencap"]]

    def test_default_is_png_and_capture_format_is_frozen(self):
        plain = ADBController(serial="phone")
        plain._run = Mock(return_value=result(self.png))
        plain.screencap()
        plain._run.assert_called_once_with(["exec-out", "screencap", "-p"])

        adb = self.controller([result(self.jpeg)])
        os.environ["POKEMGR_CAPTURE_FORMAT"] = "png"
        frame = adb.screencap()
        self.assertEqual(["-j"], self.flags(adb))
        self.assertEqual("RGB", frame.mode)
        self.assertEqual(SIZE, frame.size)
        self.assertEqual(SIZE[0] * SIZE[1] * 3, len(frame.tobytes()))
        self.assertIsNone(getattr(frame, "fp", None))

    def test_invalid_capture_formats_are_rejected(self):
        for format in ("", "jpg", "JPEG", "raw", "png -d 0"):
            with self.subTest(format=format), patch.dict(os.environ, {"POKEMGR_CAPTURE_FORMAT": format}):
                with self.assertRaisesRegex(ADBError, "must be png or jpeg"):
                    ADBController(serial="phone")

    def test_jpeg_requires_explicit_virtual_display(self):
        os.environ["POKEMGR_CAPTURE_FORMAT"] = "jpeg"
        with self.assertRaisesRegex(ADBError, "explicitly selected app display"):
            ADBController(serial="phone")

    def test_successful_captures_each_validate_frozen_display(self):
        adb = self.controller([result(self.jpeg), result(self.jpeg)])
        adb.screencap()
        adb.screencap()
        self.assertEqual([QUERY, ["exec-out", "screencap", "-j", "-d", str(CAPTURE_ID)]] * 2,
                         [call.args[0] for call in adb._run.call_args_list])

    def test_timestamps_exclude_verification_and_decode(self):
        adb = self.controller([result(self.jpeg)])
        clock = [10.0]
        run = adb._run.side_effect
        original_open = Image.open

        def timed_run(args):
            clock[0] += 2.0 if args == QUERY else 0.25
            return run(args)

        def timed_open(*args, **kwargs):
            clock[0] += 0.4
            return original_open(*args, **kwargs)

        adb._run.side_effect = timed_run
        with patch("pokemgr.adb.controller.time.monotonic", side_effect=lambda: clock[0]), \
                patch("pokemgr.adb.controller.Image.open", side_effect=timed_open):
            frame = adb.screencap()
        self.assertEqual(12.0, frame.info["pokemgr_capture_started_at"])
        self.assertEqual(12.25, frame.info["pokemgr_capture_finished_at"])
        self.assertAlmostEqual(12.65, clock[0])

    def test_unsupported_option_falls_back_once_and_remembers_capability(self):
        for error in (b"screencap: invalid option -- j", b"screencap: unknown option 'j'",
                      b"unrecognized option '--jpeg'", b"unsupported option `-j`"):
            with self.subTest(error=error):
                adb = self.controller([result(error=error, status=1), result(self.png), result(self.png)])
                adb.screencap()
                adb.screencap()
                self.assertEqual(["-j", "-p", "-p"], self.flags(adb))
                self.assertEqual(3, sum(call.args[0] == QUERY for call in adb._run.call_args_list))

    def test_ignored_jpeg_flag_discards_png_and_requests_new_frame(self):
        adb = self.controller([result(self.png), result(encoded("PNG", color="black")), result(self.png)])
        clock = iter((1.0, 1.2, 2.0, 2.3, 3.0, 3.2))
        with patch("pokemgr.adb.controller.time.monotonic", side_effect=lambda: next(clock)):
            frame = adb.screencap()
            adb.screencap()
        self.assertEqual((0, 0, 0), frame.getpixel((0, 0)))
        self.assertEqual(2.0, frame.info["pokemgr_capture_started_at"])
        self.assertEqual(2.3, frame.info["pokemgr_capture_finished_at"])
        self.assertEqual(["-j", "-p", "-p"], self.flags(adb))

    def test_unsupported_fallback_revalidates_identity_before_png(self):
        adb = self.controller([result(error=b"invalid option -- j", status=1)])
        run = adb._run.side_effect

        def change_display(args):
            output = run(args)
            if args != QUERY:
                self.report = REPORT.replace('virtual:scrcpy,5', 'virtual:scrcpy,6')
            return output

        adb._run.side_effect = change_display
        with self.assertRaisesRegex(ADBError, "recreated"):
            adb.screencap()
        self.assertEqual(["-j"], self.flags(adb))

    def test_reconnect_revalidates_and_uses_new_capture_timestamps(self):
        adb = self.controller([result(error=b"device offline", status=1), result(self.jpeg)])
        adb.wait_for_device = Mock(return_value=True)
        with patch("pokemgr.adb.controller.time.monotonic", side_effect=(1.0, 1.2, 40.0, 40.25)):
            frame = adb.screencap()
        self.assertEqual(["-j", "-j"], self.flags(adb))
        self.assertEqual(2, sum(call.args[0] == QUERY for call in adb._run.call_args_list))
        self.assertEqual(40.0, frame.info["pokemgr_capture_started_at"])
        self.assertEqual(40.25, frame.info["pokemgr_capture_finished_at"])

    def test_reconnect_changed_target_stops_before_retry(self):
        adb = self.controller([result(error=b"device offline", status=1)])

        def reconnect():
            self.report = REPORT.replace('virtual:scrcpy,5', 'virtual:scrcpy,6')
            return True

        adb.wait_for_device = Mock(side_effect=reconnect)
        with self.assertRaisesRegex(ADBError, "recreated"):
            adb.screencap()
        self.assertEqual(["-j"], self.flags(adb))

    def test_wrong_geometry_never_falls_back_even_when_jpeg_flag_was_ignored(self):
        for format in ("JPEG", "PNG"):
            with self.subTest(format=format):
                adb = self.controller([result(encoded(format, size=(128, 96)))])
                with self.assertRaisesRegex(ADBError, "dimensions"):
                    adb.screencap()
                self.assertEqual(["-j"], self.flags(adb))
                self.assertFalse(adb._jpeg_unsupported)

    def test_malformed_jpeg_fails_without_png_or_capability_change(self):
        for payload in (self.jpeg[:-64], b"\xff\xd8\xff" + b"x" * 200, b"x" * 200, b""):
            with self.subTest(payload_length=len(payload)):
                adb = self.controller([result(payload)])
                with self.assertRaises(ADBError):
                    adb.screencap()
                self.assertEqual(["-j"], self.flags(adb))
                self.assertFalse(adb._jpeg_unsupported)

    def test_other_command_errors_do_not_enable_png_fallback(self):
        for error in (b"Invalid display ID", b"Failed to capture", b"unknown option '-d'"):
            with self.subTest(error=error):
                adb = self.controller([result(error=error, status=1)])
                with self.assertRaises(ADBError):
                    adb.screencap()
                self.assertEqual(["-j"], self.flags(adb))
                self.assertFalse(adb._jpeg_unsupported)

    def test_png_fallback_failure_is_bounded(self):
        adb = self.controller([result(error=b"invalid option -- j", status=1), result(error=b"failed", status=1)])
        with self.assertRaises(ADBError):
            adb.screencap()
        self.assertEqual(["-j", "-p"], self.flags(adb))

    def test_foldable_warning_is_removed_before_jpeg_decode(self):
        adb = self.controller([result(b"Multiple displays warning\n" + self.jpeg)])
        frame = adb.screencap()
        self.assertEqual("RGB", frame.mode)
        self.assertEqual(SIZE, frame.size)

    def test_launcher_sets_jpeg_only_after_discovering_new_app_display(self):
        os.environ.update({
            "POKEMGR_CAPTURE_FORMAT": "jpeg",
            "POKEMGR_DISPLAY_ID": "99",
            "POKEMGR_CAPTURE_DISPLAY_ID": "100",
            "POKEMGR_DEVICE_SERIAL": "old-phone",
        })
        adb = Mock(serial="phone")
        adb.get_device_info.return_value = SimpleNamespace(width=96, height=128, density=420)
        adb.shell.side_effect = [
            "", f'Display {CAPTURE_ID} (Virtual display): displayName="scrcpy"',
            "Display #15 (activities from top to bottom):\n"
            "  topResumedActivity=ActivityRecord{x com.nianticlabs.pokemongo/.Game}",
        ]
        adb._run.return_value.returncode = 0

        def discover_controller():
            for key in ("POKEMGR_CAPTURE_FORMAT", "POKEMGR_DISPLAY_ID",
                        "POKEMGR_CAPTURE_DISPLAY_ID", "POKEMGR_DEVICE_SERIAL"):
                self.assertNotIn(key, os.environ)
            return adb

        stream = Mock(stdout=io.StringIO("New display: 96x128 (id=15)\n"))
        stream.poll.return_value = None
        manager = Mock()
        manager.poll.return_value = 0
        with patch.object(stream_pokemon.sys, "argv", ["stream_pokemon.py", "--serial", "phone", "--capture-backend=jpeg"]), \
                patch.object(stream_pokemon.shutil, "which", return_value="/mock/scrcpy"), \
                patch.object(stream_pokemon.subprocess, "run", return_value=result(status=1)), \
                patch.object(stream_pokemon, "ADBController", side_effect=discover_controller), \
                patch.object(stream_pokemon.subprocess, "Popen", side_effect=[stream, manager]) as popen, \
                patch.object(stream_pokemon.threading, "Thread", side_effect=lambda target, **kwargs: SimpleNamespace(start=target)), \
                patch.object(stream_pokemon.time, "monotonic", return_value=0):
            self.assertEqual(0, stream_pokemon.main())
        environment = popen.call_args_list[1].kwargs["env"]
        self.assertEqual("jpeg", environment["POKEMGR_CAPTURE_FORMAT"])
        self.assertEqual(str(DISPLAY_ID), environment["POKEMGR_DISPLAY_ID"])
        self.assertEqual(str(CAPTURE_ID), environment["POKEMGR_CAPTURE_DISPLAY_ID"])
        self.assertEqual("phone", environment["POKEMGR_DEVICE_SERIAL"])


if __name__ == "__main__":
    unittest.main()
