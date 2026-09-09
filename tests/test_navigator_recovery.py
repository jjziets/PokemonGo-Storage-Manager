# TRACEWEAVER: file-role=storage-count-verification; req=REQ-SCAN-003; trace=TRACE-SCAN-006; ver=VER-SCAN-001
# TRACEWEAVER: verifies=VER-SCAN-001; req=REQ-SCAN-003; trace=TRACE-SCAN-006
import unittest
from unittest.mock import Mock, call, patch

import numpy as np
from PIL import Image

from pokemgr.adb.device import DeviceInfo
from pokemgr.adb.navigator import GameNavigator
from pokemgr.calibration.profile import CalibrationProfile


def _tablet_regions():
    return CalibrationProfile.create_default(DeviceInfo(
        model="tablet",
        serial="test",
        width=1440,
        height=2304,
        density=280,
    )).regions


class NavigatorRecoveryTests(unittest.TestCase):
    def test_opaque_rgba_black_frame_is_detected_as_screen_off(self):
        nav = GameNavigator(Mock(), _tablet_regions())
        image = Image.new("RGBA", (1440, 2304), (0, 0, 0, 255))

        self.assertEqual("screen_off", nav.detect_screen(image))

    def test_dark_pokemon_detail_is_not_misclassified_as_exit_dialog(self):
        pixels = np.zeros((2304, 1440, 3), dtype=np.uint8)
        pixels[850:1100, 300:1140] = 255
        pixels[1050:1060, 300:1140] = (100, 230, 180)
        image = Image.fromarray(pixels)
        nav = GameNavigator(Mock(), _tablet_regions())

        self.assertEqual("detail", nav.detect_screen(image))

    @patch("pokemgr.adb.navigator.time.sleep")
    def test_unknown_surface_restarts_without_blind_coordinate_actions(
            self, _sleep):
        adb = Mock()
        nav = GameNavigator(adb, _tablet_regions())
        nav.detect_screen = Mock(side_effect=["other", "storage"])
        nav.ensure_pokemon_go = Mock()

        self.assertTrue(nav.navigate_to_storage())

        nav.ensure_pokemon_go.assert_called_once_with(restart=True)
        adb.tap.assert_not_called()
        adb.key_event.assert_not_called()

    @patch("pokemgr.adb.navigator.time.sleep")
    def test_awake_black_frame_clears_touch_protection_before_restart(self, _sleep):
        adb = Mock()
        adb.is_screen_on.return_value = True
        nav = GameNavigator(adb, _tablet_regions())
        nav.detect_screen = Mock(side_effect=["screen_off", "storage"])
        nav.ensure_pokemon_go = Mock()
        nav.clear_touch_protection = Mock(return_value=True)

        self.assertTrue(nav.navigate_to_storage())

        nav.clear_touch_protection.assert_called_once_with()
        nav.ensure_pokemon_go.assert_not_called()
        adb.wake_screen.assert_not_called()

    @patch("pokemgr.adb.navigator.time.sleep")
    def test_persistent_awake_black_storage_restarts_only_once(self, _sleep):
        adb = Mock()
        adb.is_screen_on.return_value = True
        nav = GameNavigator(adb, _tablet_regions())
        nav.detect_screen = Mock(return_value="screen_off")
        nav.ensure_pokemon_go = Mock()
        nav.clear_touch_protection = Mock(return_value=True)

        self.assertFalse(nav.navigate_to_storage())

        nav.clear_touch_protection.assert_called_once_with()
        nav.ensure_pokemon_go.assert_called_once_with(restart=True)
        adb.tap.assert_not_called()
        adb.swipe.assert_not_called()
        adb.key_event.assert_not_called()

    @patch("pokemgr.adb.navigator.time.sleep")
    def test_persistent_awake_black_appraisal_restarts_only_once(self, _sleep):
        adb = Mock()
        adb.is_screen_on.return_value = True
        nav = GameNavigator(adb, _tablet_regions())
        nav.detect_screen = Mock(return_value="screen_off")
        nav.ensure_pokemon_go = Mock()
        nav.clear_touch_protection = Mock(return_value=True)

        self.assertFalse(nav.navigate_to_appraisal())

        nav.clear_touch_protection.assert_called_once_with()
        nav.ensure_pokemon_go.assert_called_once_with(restart=True)
        adb.tap.assert_not_called()
        adb.swipe.assert_not_called()
        adb.key_event.assert_not_called()

    @patch("pokemgr.adb.navigator.human_delay")
    def test_map_uses_calibrated_tablet_targets(self, _delay):
        adb = Mock()
        regions = _tablet_regions()
        nav = GameNavigator(adb, regions)
        nav.detect_screen = Mock(side_effect=["game_map", "storage"])

        self.assertTrue(nav.navigate_to_storage())

        self.assertEqual([
            call(*regions.map_pokeball, jitter=5),
            call(*regions.map_pokemon_button, jitter=5),
        ], adb.tap.call_args_list)

    @patch("pokemgr.adb.navigator.human_delay")
    def test_stop_between_map_taps_prevents_later_device_action(self, delay):
        adb = Mock()
        regions = _tablet_regions()
        stopped = {"value": False}
        nav = GameNavigator(
            adb,
            regions,
            cancelled=lambda: stopped["value"],
        )
        nav.detect_screen = Mock(return_value="game_map")
        delay.side_effect = lambda *_args, **_kwargs: stopped.update(value=True)

        self.assertFalse(nav.navigate_to_storage())

        self.assertEqual([
            call(*regions.map_pokeball, jitter=5),
        ], adb.tap.call_args_list)

    @patch("pokemgr.adb.navigator.time.sleep")
    @patch("pokemgr.adb.navigator.human_delay")
    def test_search_uses_calibrated_tablet_targets(self, _delay, _sleep):
        adb = Mock()
        regions = _tablet_regions()
        nav = GameNavigator(adb, regions)

        nav.enter_search("!shiny")

        self.assertEqual([
            call(*regions.storage_search_clear, jitter=3),
            call(*regions.storage_search_bar, jitter=3),
        ], adb.tap.call_args_list)
        adb.input_text.assert_called_once_with("!shiny")
        self.assertEqual(
            [call(123), *([call(67)] * 40), call(66)],
            adb.key_event.call_args_list,
        )
        adb.shell.assert_not_called()

    @patch("pokemgr.adb.navigator.time.sleep")
    @patch("pokemgr.adb.navigator.human_delay")
    def test_verified_search_reads_full_query_and_clears_actual_remaining_length(self, _delay, _sleep):
        adb = Mock()
        nav = GameNavigator(adb, _tablet_regions())
        nav.detect_screen = Mock(return_value="storage")
        query = "!shiny&!shadow&!dynamax&!gigantamax&!lucky"
        with patch("pokemgr.adb.search_text.read_search_text", side_effect=["old", "", query]) as read:
            self.assertTrue(nav.enter_search(query, verify=True))
        self.assertEqual(3, read.call_count)
        self.assertEqual([call(123), *[call(67)] * 3, call(66)], adb.key_event.call_args_list)
        adb.input_text.assert_called_once_with(query)
        self.assertEqual(5, nav.detect_screen.call_count)

    @patch("pokemgr.adb.navigator.time.sleep")
    @patch("pokemgr.adb.navigator.human_delay")
    def test_verified_search_holds_on_missing_or_nonempty_clear_or_wrong_complete_text(self, _delay, _sleep):
        for readings in ([None], ["old", "old"], ["", "", "shiny&!shadow"], ["", "", None]):
            with self.subTest(readings=readings):
                adb = Mock()
                nav = GameNavigator(adb, _tablet_regions())
                nav.detect_screen = Mock(return_value="storage")
                with patch("pokemgr.adb.search_text.read_search_text", side_effect=readings):
                    self.assertFalse(nav.enter_search("!shiny&!shadow", verify=True))
                self.assertNotIn(call(66), adb.key_event.call_args_list)
                if len(readings) < 3:
                    adb.input_text.assert_not_called()

    @patch("pokemgr.adb.navigator.time.sleep")
    @patch("pokemgr.adb.navigator.human_delay")
    def test_verified_empty_filter_does_not_type_placeholder_or_empty_adb_argument(self, _delay, _sleep):
        adb = Mock()
        nav = GameNavigator(adb, _tablet_regions())
        nav.detect_screen = Mock(return_value="storage")
        with patch("pokemgr.adb.search_text.read_search_text", return_value=""):
            self.assertTrue(nav.enter_search("", verify=True))
        adb.input_text.assert_not_called()
        self.assertEqual([call(123), call(66)], adb.key_event.call_args_list)

    @patch("pokemgr.adb.navigator.time.sleep")
    @patch("pokemgr.adb.navigator.human_delay")
    def test_abort_during_search_readback_does_not_apply_query(self, _delay, _sleep):
        adb = Mock()
        stopped = {"value": False}
        nav = GameNavigator(adb, _tablet_regions(), cancelled=lambda: stopped["value"])
        nav.detect_screen = Mock(return_value="storage")
        def read(_adb):
            stopped["value"] = True
            return ""
        with patch("pokemgr.adb.search_text.read_search_text", side_effect=read):
            self.assertFalse(nav.enter_search("shiny", verify=True))
        adb.input_text.assert_not_called()
        adb.key_event.assert_not_called()

    @patch("pokemgr.adb.navigator.time.sleep")
    @patch("pokemgr.adb.navigator.human_delay")
    def test_other_game_editor_cannot_be_used_as_storage_search(self, _delay, _sleep):
        for screens, should_type in ((["detail"], False), (["storage", "detail"], False),
                                     (["storage", "storage", "detail"], False),
                                     (["storage", "storage", "storage", "detail"], True)):
            with self.subTest(screens=screens):
                adb = Mock()
                nav = GameNavigator(adb, _tablet_regions())
                nav.detect_screen = Mock(side_effect=screens)
                with patch("pokemgr.adb.search_text.read_search_text", side_effect=["", "", "shiny"]):
                    self.assertFalse(nav.enter_search("shiny", verify=True))
                self.assertNotIn(call(66), adb.key_event.call_args_list)
                self.assertEqual(should_type, adb.input_text.called)

    @patch("pokemgr.adb.navigator.time.sleep")
    def test_clean_restart_force_stops_before_launch(self, _sleep):
        adb = Mock()
        nav = GameNavigator(adb, _tablet_regions())

        nav.ensure_pokemon_go(restart=True)

        self.assertEqual([
            call('am force-stop com.nianticlabs.pokemongo'),
            call(
                'am start -n com.nianticlabs.pokemongo/'
                'com.nianticproject.holoholo.libholoholo.unity.UnityMainActivity'
            ),
        ], adb.shell.call_args_list)

    def test_filtered_count_crop_keeps_full_digit_height(self):
        adb = Mock()
        adb.screencap.return_value = Image.new("RGB", (968, 2376), "white")
        nav = GameNavigator(adb, _tablet_regions())
        nav.detect_screen = Mock(return_value="storage")

        def _read_count(image, **_kwargs):
            # The live Q(2622) header was misread as Q(9677) when a 30px crop
            # clipped the glyphs.  The expanded 50px source crop becomes 250px
            # after the existing 5x OCR resize.
            self.assertEqual(250, image.shape[0])
            return "Q(2622)"

        with patch("pytesseract.image_to_string", side_effect=_read_count):
            self.assertEqual(2622, nav.read_filtered_count())

    def test_unfiltered_storage_count_uses_owned_value_not_capacity(self):
        adb = Mock()
        adb.screencap.return_value = Image.new("RGB", (968, 2376), "white")
        nav = GameNavigator(adb, _tablet_regions())
        nav.detect_screen = Mock(return_value="storage")

        with patch(
            "pytesseract.image_to_string",
            return_value="2947/3250",
        ):
            self.assertEqual(2947, nav.read_filtered_count())


if __name__ == "__main__":
    unittest.main()
