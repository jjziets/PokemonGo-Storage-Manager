"""Only an active game on the selected stream authorizes launcher progress."""

import unittest
from types import SimpleNamespace

from scripts.stream_pokemon import capture_displays, display_geometry, game_on_display


GAME_ACTIVITY = (
    "ActivityRecord{abc123 u0 com.nianticlabs.pokemongo/"
    "com.nianticproject.holoholo.libholoholo.unity.UnityMainActivity t100}"
)
OTHER_ACTIVITY = "ActivityRecord{def456 u0 com.android.settings/.Settings t101}"


def _display(display_id, contents):
    return f"Display #{display_id} (activities from top to bottom):\n{contents}\n"


class StreamLauncherParsingTests(unittest.TestCase):
    def test_phone_and_tablet_both_request_the_same_pixel_and_density_layout(self):
        for dimensions in ((1440, 2304, 280), (1080, 2400, 440), (2376, 968, 420)):
            info = SimpleNamespace(**dict(zip(("width", "height", "density"), dimensions)))
            with self.subTest(dimensions=dimensions):
                self.assertEqual("968x2376/420", display_geometry(info))
                self.assertEqual(f"{info.width}x{info.height}/{info.density}", display_geometry(info, native=True))

    def test_explicit_native_mode_rejects_missing_density(self):
        with self.assertRaises(ValueError):
            display_geometry(SimpleNamespace(width=1080, height=2400, density=0), native=True)

    def test_resumed_game_on_selected_display_is_accepted(self):
        activities = (
            _display("0", f"  topResumedActivity={OTHER_ACTIVITY}")
            + _display("15", f"  topResumedActivity={GAME_ACTIVITY}")
        )

        self.assertTrue(game_on_display(activities, "15"))

    def test_resumed_game_on_wrong_display_is_rejected(self):
        for wrong_display in ("0", "150"):
            with self.subTest(wrong_display=wrong_display):
                activities = (
                    _display(wrong_display, f"  topResumedActivity={GAME_ACTIVITY}")
                    + _display("15", f"  topResumedActivity={OTHER_ACTIVITY}")
                )

                self.assertFalse(game_on_display(activities, "15"))

    def test_historical_game_record_does_not_prove_it_is_resumed(self):
        activities = _display(
            "15",
            f"  topResumedActivity={OTHER_ACTIVITY}\n"
            f"  * Hist #0: {GAME_ACTIVITY}\n"
            "    mState=STOPPED visible=false",
        )

        self.assertFalse(game_on_display(activities, "15"))

    def test_game_on_next_display_cannot_leak_into_selected_display_block(self):
        activities = (
            _display("15", "  topResumedActivity=null")
            + _display("16", f"  topResumedActivity={GAME_ACTIVITY}")
        )

        self.assertFalse(game_on_display(activities, "15"))

    def test_capture_parser_retains_distinct_stream_ids_and_ignores_other_surfaces(self):
        captures = (
            'Display 123 (HWC display 0): displayName="scrcpy"\n'
            'Display 987 (Virtual display): displayName="scrcpy"\n'
            'Display 987 (Virtual display): displayName="scrcpy"\n'
            'Display 988 (Virtual display): displayName="scrcpy"\n'
            'Display 989 (Virtual display): displayName="another-app"\n'
        )

        self.assertEqual({"987", "988"}, capture_displays(captures))


if __name__ == "__main__":
    unittest.main()
