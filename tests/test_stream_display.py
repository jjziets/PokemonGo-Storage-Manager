"""An explicitly selected app display never falls back to the phone display."""

import io
import os
import shlex
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from pokemgr.adb.controller import ADBController, ADBError
from pokemgr.adb.navigator import GameNavigator
from pokemgr.calibration.regions import ScreenRegions


LOGICAL_ID = 15
CAPTURE_ID = 11529215049929616469
PHYSICAL_CAPTURE_ID = 4630947194243491972

# Relevant fields from cmd display get-displays and SurfaceFlinger --display-id
# captured on the Fold6. Android input and screencap use different ID namespaces.
PHYSICAL_DISPLAY = (
    'Display id 0: DisplayInfo{"Built-in Screen", displayId 0, '
    'real 968 x 2376, state OFF, committedState OFF, type INTERNAL, '
    'uniqueId "local:4630947194243491972", app 968 x 2376, '
    'density 420 (416.732 x 410.546) dpi, layerStack 0}, isValid=true'
)
VIRTUAL_DISPLAY = (
    'Display id 15: DisplayInfo{"scrcpy", displayId 15, displayGroupId 0, '
    'FLAG_PRESENTATION, FLAG_TRUSTED, FLAG_OWN_DISPLAY_GROUP, '
    'real 968 x 2376, rotation 0, state ON, committedState UNKNOWN, '
    'type VIRTUAL, uniqueId "virtual:com.android.shell,2000,scrcpy,5", '
    'app 968 x 2376, density 420 (420.0 x 420.0) dpi, layerStack 15, '
    'owner com.android.shell (uid 2000), canHostTasks true}, '
    'DisplayMetrics{density=2.625, width=968, height=2376}, isValid=true'
)
CAPTURE_DISPLAYS = (
    'Display 4630947194243491972 (HWC display 3): port=132 '
    'pnpId=QCM screenPartStatus=UNSUPPORTED displayName=""\n'
    'Display 11529215049929616469 (Virtual display): displayName="scrcpy"\n'
)


def _png(size=(968, 2376)):
    output = io.BytesIO()
    Image.new("RGB", size, "white").save(output, format="PNG")
    return output.getvalue()


class StreamDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.png = _png()

    def setUp(self):
        self.env_patch = patch.dict(os.environ)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        os.environ.pop("POKEMGR_DISPLAY_ID", None)
        os.environ.pop("POKEMGR_CAPTURE_DISPLAY_ID", None)
        os.environ.pop("POKEMGR_DEVICE_SERIAL", None)
        os.environ.pop("POKEMGR_EXPECTED_DISPLAY_GEOMETRY", None)
        self.logical_displays = f"Displays:\n{PHYSICAL_DISPLAY}\n{VIRTUAL_DISPLAY}\n"
        self.capture_displays = CAPTURE_DISPLAYS
        self.capture_png = self.png

    def _controller(self, targeted=True):
        if targeted:
            os.environ["POKEMGR_DISPLAY_ID"] = str(LOGICAL_ID)
            os.environ["POKEMGR_CAPTURE_DISPLAY_ID"] = str(CAPTURE_ID)
        adb = ADBController(serial="test-serial")
        adb._run = Mock(side_effect=self._run)
        return adb

    def _run(self, args, **_kwargs):
        if args[:2] == ["exec-out", "screencap"]:
            output = self.capture_png
        elif args[0] == "shell":
            command = " ".join(args[1:])
            if command == "cmd display get-displays && dumpsys SurfaceFlinger --display-id":
                output = (self.logical_displays + self.capture_displays).encode()
            elif command == "getprop ro.product.model":
                output = b"SM-F956B"
            elif command == "getprop ro.serialno":
                output = b"test-serial"
            elif command in {"cmd display power-reset 0", "cmd display power-off 0"}:
                output = b""
            elif command.startswith(("input ", "am ")):
                output = b""
            else:
                raise AssertionError(f"Unexpected device shell command: {command}")
        else:
            raise AssertionError(f"Unexpected ADB command: {args}")
        return SimpleNamespace(returncode=0, stdout=output, stderr=b"")

    def _shell_commands(self, adb):
        return [
            " ".join(call.args[0][1:])
            for call in adb._run.call_args_list
            if call.args[0][0] == "shell"
        ]

    def _inputs(self, adb):
        return [
            shlex.split(command)
            for command in self._shell_commands(adb)
            if command.startswith("input ")
        ]

    def test_requested_geometry_is_checked_before_capture_or_input(self):
        os.environ["POKEMGR_EXPECTED_DISPLAY_GEOMETRY"] = "968x2376/420"
        original = self.logical_displays
        for old, new in (("real 968 x 2376", "real 1080 x 2400"),
                         ("density 420", "density 280"),
                         ("real 968 x 2376", "real 2376 x 968")):
            with self.subTest(new=new):
                self.logical_displays = original.replace(old, new)
                adb = self._controller()
                with self.assertRaisesRegex(ADBError, "requested resolution and density"):
                    adb.screencap()
                with self.assertRaisesRegex(ADBError, "requested resolution and density"):
                    adb.tap(200, 300, jitter=0)
                self.assertFalse(self._inputs(adb))
                self.assertTrue(all(call.args[0][0] != "exec-out" for call in adb._run.call_args_list))

    def test_matching_geometry_uses_virtual_coordinates_and_is_frozen_at_construction(self):
        os.environ["POKEMGR_EXPECTED_DISPLAY_GEOMETRY"] = "968x2376/420"
        adb = self._controller()
        os.environ["POKEMGR_EXPECTED_DISPLAY_GEOMETRY"] = "1440x2304/280"
        info = adb.get_device_info()
        self.assertEqual((968, 2376, 420), (info.width, info.height, info.density))
        self.assertEqual((968, 2376), adb.screencap().size)

    def test_expected_geometry_must_be_valid_and_bound_to_a_virtual_display(self):
        for value in ("968x2376", "0x2376/420", "968x2376/0", "968x2376/-1", ""):
            with self.subTest(value=value), self.assertRaises(ADBError):
                os.environ["POKEMGR_EXPECTED_DISPLAY_GEOMETRY"] = value
                self._controller()
        os.environ["POKEMGR_EXPECTED_DISPLAY_GEOMETRY"] = "968x2376/420"
        os.environ.pop("POKEMGR_DISPLAY_ID", None)
        os.environ.pop("POKEMGR_CAPTURE_DISPLAY_ID", None)
        with self.assertRaisesRegex(ADBError, "requires an app display"):
            self._controller(targeted=False)

    def test_logical_and_capture_ids_are_independent_and_frozen(self):
        adb = self._controller()
        os.environ["POKEMGR_DISPLAY_ID"] = "16"
        os.environ["POKEMGR_CAPTURE_DISPLAY_ID"] = "123"

        self.assertEqual(LOGICAL_ID, adb.display_id)
        self.assertEqual(CAPTURE_ID, adb.capture_display_id)

        frame = adb.screencap()

        self.assertEqual((968, 2376), frame.size)
        self.assertIsNone(frame.fp)
        self.assertIn(
            ["exec-out", "screencap", "-p", "-d", str(CAPTURE_ID)],
            [call.args[0] for call in adb._run.call_args_list],
        )

    def test_each_capture_and_input_gets_one_fresh_combined_display_report(self):
        adb = self._controller()
        query = "cmd display get-displays && dumpsys SurfaceFlinger --display-id"
        for operation in (
            adb.screencap,
            lambda: adb.tap(200, 300, jitter=0),
            lambda: adb.swipe(200, 300, 400, 500, jitter=0),
            lambda: adb.key_event(4),
            lambda: adb.input_text("!shiny"),
        ):
            with self.subTest(operation=operation):
                adb._run.reset_mock()
                operation()
                commands = self._shell_commands(adb)
                self.assertEqual(1, commands.count(query))
                self.assertEqual(["shell", query], adb._run.call_args_list[0].args[0])
                self.assertEqual(2, adb._run.call_count)

    def test_fresh_combined_report_checks_every_required_logical_field(self):
        original = self.logical_displays
        for field in (
            'DisplayInfo{"scrcpy"',
            'uniqueId "virtual:com.android.shell,2000,scrcpy,5"',
            "real 968 x 2376",
            "density 420",
            "state ON",
            "type VIRTUAL",
        ):
            with self.subTest(field=field):
                self.logical_displays = original
                adb = self._controller()
                adb.screencap()
                self.logical_displays = original.replace(
                    VIRTUAL_DISPLAY, VIRTUAL_DISPLAY.replace(field, ""),
                )
                adb._run.reset_mock()

                with self.assertRaises(ADBError):
                    adb.tap(200, 300, jitter=0)

                self.assertEqual([], self._inputs(adb))
                self.assertEqual(1, adb._run.call_count)

    def test_capture_pairing_is_rechecked_after_a_successful_capture(self):
        adb = self._controller()
        adb.screencap()
        self.capture_displays = ""
        adb._run.reset_mock()

        with self.assertRaises(ADBError):
            adb.screencap()

        self.assertEqual(1, adb._run.call_count)
        self.assertEqual("shell", adb._run.call_args.args[0][0])

    def test_launcher_serial_selects_its_phone_instead_of_first_connected_device(self):
        os.environ["POKEMGR_DEVICE_SERIAL"] = "chosen-phone"
        adb = ADBController()
        adb.get_devices = Mock(return_value=["other-phone", "chosen-phone"])

        adb.connect()

        self.assertEqual("chosen-phone", adb.serial)

    def test_missing_launcher_serial_cannot_fall_back_to_another_phone(self):
        os.environ["POKEMGR_DEVICE_SERIAL"] = "chosen-phone"
        adb = ADBController()
        adb.get_devices = Mock(return_value=["other-phone"])

        with self.assertRaises(ADBError):
            adb.connect()

        self.assertNotEqual("other-phone", adb.serial)

    def test_explicit_serial_has_precedence_when_no_virtual_target_is_configured(self):
        os.environ["POKEMGR_DEVICE_SERIAL"] = "launcher-phone"
        adb = ADBController(serial="explicit-phone")
        adb.get_devices = Mock(return_value=["other-phone", "explicit-phone"])

        adb.connect()

        self.assertEqual("explicit-phone", adb.serial)

    def test_virtual_target_rejects_conflicting_launcher_and_constructor_serials(self):
        os.environ["POKEMGR_DEVICE_SERIAL"] = "launcher-phone"
        os.environ["POKEMGR_DISPLAY_ID"] = str(LOGICAL_ID)
        os.environ["POKEMGR_CAPTURE_DISPLAY_ID"] = str(CAPTURE_ID)

        with self.assertRaises(ADBError):
            ADBController(serial="other-phone")

    def test_both_ids_are_required_and_only_positive_decimal_ids_are_accepted(self):
        for logical, capture in (
            ("15", None), (None, str(CAPTURE_ID)),
            ("0", str(CAPTURE_ID)), ("-1", str(CAPTURE_ID)),
            ("abc", str(CAPTURE_ID)), ("15; input tap 1 1", str(CAPTURE_ID)),
            ("15", "0"), ("15", "-1"), ("15", "abc"),
        ):
            with self.subTest(logical=logical, capture=capture):
                os.environ.pop("POKEMGR_DISPLAY_ID", None)
                os.environ.pop("POKEMGR_CAPTURE_DISPLAY_ID", None)
                if logical is not None:
                    os.environ["POKEMGR_DISPLAY_ID"] = logical
                if capture is not None:
                    os.environ["POKEMGR_CAPTURE_DISPLAY_ID"] = capture

                with self.assertRaises((ADBError, ValueError)):
                    ADBController(serial="test-serial")

    @patch("random.randint", return_value=0)
    def test_every_input_uses_logical_display_and_preserves_text_as_one_argument(
            self, _random):
        adb = self._controller()
        text = "!shiny&!shadow'; input tap 1 1"

        adb.tap(200, 300, jitter=0)
        adb.swipe(200, 300, 400, 500, duration_ms=300, jitter=0)
        adb.key_event("KEYCODE_BACK")
        adb.input_text(text)

        self.assertEqual([
            ["input", "-d", "15", "tap", "200", "300"],
            ["input", "-d", "15", "swipe", "200", "300", "400", "500", "300"],
            ["input", "-d", "15", "keyevent", "KEYCODE_BACK"],
            ["input", "-d", "15", "text", text],
        ], self._inputs(adb))

    def test_device_dimensions_and_density_come_from_selected_display_metadata(self):
        self.logical_displays = self.logical_displays.replace(
            VIRTUAL_DISPLAY,
            VIRTUAL_DISPLAY.replace("968", "1440").replace("2376", "2304")
            .replace("density 420", "density 280"),
        )
        adb = self._controller()

        info = adb.get_device_info()

        self.assertEqual((1440, 2304, 280), (info.width, info.height, info.density))
        self.assertNotIn("wm size", self._shell_commands(adb))
        self.assertNotIn("wm density", self._shell_commands(adb))

    def test_missing_logical_display_prevents_input(self):
        adb = self._controller()
        self.logical_displays = f"Displays:\n{PHYSICAL_DISPLAY}\n"

        with self.assertRaises(ADBError):
            adb.tap(200, 300, jitter=0)

        self.assertEqual([], self._inputs(adb))

    def test_missing_capture_display_prevents_input(self):
        adb = self._controller()
        self.capture_displays = ""

        with self.assertRaises(ADBError):
            adb.key_event("KEYCODE_BACK")

        self.assertEqual([], self._inputs(adb))

    def test_failed_display_query_prevents_input(self):
        adb = self._controller()
        adb._run.return_value = SimpleNamespace(
            returncode=1, stdout=b"", stderr=b"device offline",
        )
        adb._run.side_effect = None

        with self.assertRaises(ADBError):
            adb.tap(200, 300, jitter=0)

        self.assertEqual([], self._inputs(adb))

    def test_physical_capture_id_cannot_be_paired_with_virtual_input_id(self):
        os.environ["POKEMGR_DISPLAY_ID"] = str(LOGICAL_ID)
        os.environ["POKEMGR_CAPTURE_DISPLAY_ID"] = str(PHYSICAL_CAPTURE_ID)
        adb = self._controller(targeted=False)

        with self.assertRaises(ADBError):
            adb.tap(200, 300, jitter=0)

        self.assertEqual([], self._inputs(adb))

    def test_different_named_virtual_capture_cannot_receive_logical_display_input(self):
        adb = self._controller()
        self.capture_displays = self.capture_displays.replace(
            'displayName="scrcpy"', 'displayName="another-app"',
        )

        with self.assertRaises(ADBError):
            adb.tap(200, 300, jitter=0)

        self.assertEqual([], self._inputs(adb))

    def test_multiple_same_named_logical_displays_make_pairing_ambiguous(self):
        adb = self._controller()
        self.logical_displays += (
            VIRTUAL_DISPLAY.replace("15", "16").replace("scrcpy,5", "scrcpy,6")
            + "\n"
        )

        with self.assertRaises(ADBError):
            adb.tap(200, 300, jitter=0)

        self.assertEqual([], self._inputs(adb))

    def test_multiple_same_named_capture_displays_make_pairing_ambiguous(self):
        adb = self._controller()
        self.capture_displays += (
            f'Display {CAPTURE_ID + 1} (Virtual display): displayName="scrcpy"\n'
        )

        with self.assertRaises(ADBError):
            adb.tap(200, 300, jitter=0)

        self.assertEqual([], self._inputs(adb))

    def test_recreated_display_id_cannot_receive_any_further_input(self):
        adb = self._controller()
        adb.screencap()
        self.logical_displays = self.logical_displays.replace(
            'uniqueId "virtual:com.android.shell,2000,scrcpy,5"',
            'uniqueId "virtual:com.android.shell,2000,scrcpy,6"',
        )
        adb._run.reset_mock()

        for operation in (
            lambda: adb.tap(200, 300, jitter=0),
            lambda: adb.swipe(200, 300, 400, 500, jitter=0),
            lambda: adb.key_event(4),
            lambda: adb.input_text("!shiny"),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(ADBError):
                    operation()

        self.assertEqual([], self._inputs(adb))

    def test_snapshot_dimensions_must_match_selected_display(self):
        adb = self._controller()
        self.capture_png = _png((1080, 2400))

        with self.assertRaises(ADBError):
            adb.screencap()

        captures = [
            call.args[0] for call in adb._run.call_args_list
            if call.args[0][0] == "exec-out"
        ]
        self.assertTrue(captures)
        self.assertTrue(all(str(CAPTURE_ID) in args for args in captures))

    def test_changed_display_geometry_requires_new_calibration_before_input(self):
        adb = self._controller()
        adb.screencap()
        self.logical_displays = self.logical_displays.replace(
            VIRTUAL_DISPLAY, VIRTUAL_DISPLAY.replace("real 968 x 2376", "real 1440 x 2304"),
        )

        with self.assertRaises(ADBError):
            adb.tap(200, 300, jitter=0)

        self.assertEqual([], self._inputs(adb))

    def test_invalid_targeted_png_never_falls_back_to_default_capture(self):
        adb = self._controller()
        self.capture_png = b"invalid PNG data" * 20

        with self.assertRaises(ADBError):
            adb.screencap()

        self.assertNotIn(
            ["exec-out", "screencap", "-p"],
            [call.args[0] for call in adb._run.call_args_list],
        )

    def test_virtual_wake_screen_never_sends_a_power_key(self):
        adb = self._controller()

        adb.wake_screen()

        self.assertEqual([], self._inputs(adb))
        self.assertNotIn("dumpsys power", self._shell_commands(adb))

    def test_explicit_phone_screen_on_targets_physical_display_without_retargeting_stream(self):
        adb = self._controller()
        adb.get_devices = Mock(return_value=["other-phone", "test-serial"])

        adb.turn_phone_screen_on()

        self.assertEqual([
            "cmd display power-reset 0",
            "input -d 0 keyevent KEYCODE_WAKEUP",
        ], self._shell_commands(adb))
        self.assertTrue(all(
            call.kwargs.get("timeout") == 10 for call in adb._run.call_args_list
        ))
        self.assertEqual("test-serial", adb.serial)
        self.assertEqual(LOGICAL_ID, adb.display_id)
        self.assertEqual(CAPTURE_ID, adb.capture_display_id)

        adb._run.reset_mock()
        adb.tap(200, 300, jitter=0)
        self.assertEqual([
            ["input", "-d", "15", "tap", "200", "300"],
        ], self._inputs(adb))

    def test_explicit_phone_screen_on_rejects_changed_pinned_serial_before_commands(self):
        adb = self._controller()
        adb.serial = "other-phone"
        adb.get_devices = Mock(return_value=["test-serial", "other-phone"])

        with self.assertRaises(ADBError):
            adb.turn_phone_screen_on()

        adb._run.assert_not_called()
        adb.get_devices.assert_not_called()

    def test_explicit_phone_screen_on_rejects_disconnected_selected_phone(self):
        adb = self._controller()
        adb.get_devices = Mock(return_value=["other-phone"])

        with self.assertRaises(ADBError):
            adb.turn_phone_screen_on()

        adb._run.assert_not_called()
        self.assertEqual("test-serial", adb.serial)

    def test_explicit_phone_screen_on_requires_a_selected_phone(self):
        adb = self._controller()
        adb.serial = None

        with self.assertRaises(ADBError):
            adb.turn_phone_screen_on()

        adb._run.assert_not_called()

    def test_explicit_phone_screen_on_reports_each_failed_command_without_fallback(self):
        success = SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        for failed_index, failed_result in (
            (0, SimpleNamespace(returncode=1, stdout=b"", stderr=b"permission denied")),
            (1, SimpleNamespace(returncode=1, stdout=b"", stderr=b"device offline")),
            (0, SimpleNamespace(returncode=0, stdout=b"Unknown command: power-reset", stderr=b"")),
        ):
            with self.subTest(failed_index=failed_index, result=failed_result):
                adb = self._controller()
                adb.get_devices = Mock(return_value=["test-serial"])
                adb._run.side_effect = [success] * failed_index + [failed_result]

                with self.assertRaisesRegex(ADBError, "Could not turn on the phone screen"):
                    adb.turn_phone_screen_on()

                self.assertEqual(failed_index + 1, adb._run.call_count)
                self.assertEqual(LOGICAL_ID, adb.display_id)
                self.assertEqual(CAPTURE_ID, adb.capture_display_id)

    def test_phone_screen_off_validates_stream_before_darkening_only_physical_display(self):
        adb = self._controller()
        adb.get_devices = Mock(return_value=["test-serial"])

        adb.turn_phone_screen_off()

        commands = self._shell_commands(adb)
        self.assertEqual("cmd display power-off 0", commands[-1])
        self.assertIn("cmd display get-displays && dumpsys SurfaceFlinger --display-id", commands[:-1])
        self.assertEqual(10, adb._run.call_args.kwargs["timeout"])
        self.assertEqual([], self._inputs(adb))
        self.assertEqual(LOGICAL_ID, adb.display_id)
        self.assertEqual(CAPTURE_ID, adb.capture_display_id)

    def test_phone_screen_off_is_unavailable_without_a_virtual_stream(self):
        adb = self._controller(targeted=False)

        with self.assertRaises(ADBError):
            adb.turn_phone_screen_off()

        adb._run.assert_not_called()

    def test_phone_screen_off_rejects_changed_or_disconnected_pinned_phone(self):
        for current_serial, connected in (
            ("other-phone", ["other-phone", "test-serial"]),
            ("test-serial", ["other-phone"]),
        ):
            with self.subTest(current_serial=current_serial, connected=connected):
                adb = self._controller()
                adb.serial = current_serial
                adb.get_devices = Mock(return_value=connected)

                with self.assertRaises(ADBError):
                    adb.turn_phone_screen_off()

                adb._run.assert_not_called()

    def test_phone_screen_off_rejects_a_missing_or_inactive_virtual_display(self):
        for virtual_display in ("", VIRTUAL_DISPLAY.replace("state ON", "state OFF")):
            with self.subTest(virtual_display=virtual_display):
                adb = self._controller()
                adb.get_devices = Mock(return_value=["test-serial"])
                self.logical_displays = f"Displays:\n{PHYSICAL_DISPLAY}\n{virtual_display}\n"

                with self.assertRaises(ADBError):
                    adb.turn_phone_screen_off()

                self.assertNotIn("cmd display power-off 0", self._shell_commands(adb))
                self.assertEqual([], self._inputs(adb))

    def test_phone_screen_off_reports_power_command_failure_without_fallback(self):
        for failure in (
            SimpleNamespace(returncode=1, stdout=b"", stderr=b"permission denied"),
            SimpleNamespace(returncode=0, stdout=b"Unknown command: power-off", stderr=b""),
        ):
            with self.subTest(failure=failure):
                adb = self._controller()
                adb.get_devices = Mock(return_value=["test-serial"])

                def fail_power_only(args, **kwargs):
                    if args == ["shell", "cmd display power-off 0"]:
                        return failure
                    return self._run(args, **kwargs)

                adb._run.side_effect = fail_power_only

                with self.assertRaises(ADBError):
                    adb.turn_phone_screen_off()

                self.assertEqual("cmd display power-off 0", self._shell_commands(adb)[-1])
                self.assertEqual([], self._inputs(adb))

    def test_virtual_target_rejects_physical_power_sleep_and_wakeup_keys(self):
        adb = self._controller()

        for key in (26, "KEYCODE_POWER", 223, "KEYCODE_SLEEP", 224, "KEYCODE_WAKEUP"):
            with self.subTest(key=key):
                with self.assertRaises(ADBError):
                    adb.key_event(key)

        self.assertEqual([], self._inputs(adb))

    def test_app_launch_uses_selected_logical_display(self):
        adb = self._controller()

        adb.start_pokemon_go()

        launches = [
            shlex.split(command) for command in self._shell_commands(adb)
            if command.startswith("am start")
        ]
        self.assertEqual([[
            "am", "start", "--display", "15", "-n",
            "com.nianticlabs.pokemongo/"
            "com.nianticproject.holoholo.libholoholo.unity.UnityMainActivity",
        ]], launches)

    def test_navigation_rejects_calibration_for_another_display_size(self):
        adb = self._controller()
        regions = ScreenRegions.default_for_resolution(1440, 2304, density=280)

        with self.assertRaises(ADBError):
            GameNavigator(adb, regions)

        self.assertEqual([], self._inputs(adb))

    def test_unconfigured_controller_retains_default_screencap_command(self):
        adb = self._controller(targeted=False)

        frame = adb.screencap()

        self.assertEqual((968, 2376), frame.size)
        adb._run.assert_called_once_with(["exec-out", "screencap", "-p"])

    @patch("pokemgr.adb.navigator.time.sleep")
    def test_virtual_black_frame_never_wakes_or_unlocks_physical_phone(self, _sleep):
        for entrypoint in ("navigate_to_storage", "navigate_to_appraisal"):
            with self.subTest(entrypoint=entrypoint):
                adb = self._controller()
                adb.wake_screen = Mock(side_effect=AssertionError("must not wake phone"))
                adb.is_screen_on = Mock(return_value=False)
                regions = ScreenRegions.default_for_resolution(968, 2376, density=420)
                nav = GameNavigator(adb, regions)
                nav.detect_screen = Mock(return_value="screen_off")

                self.assertFalse(getattr(nav, entrypoint)())

                adb.wake_screen.assert_not_called()
                self.assertEqual([], self._inputs(adb))


if __name__ == "__main__":
    unittest.main()
