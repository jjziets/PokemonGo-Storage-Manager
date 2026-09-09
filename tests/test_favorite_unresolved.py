"""Favorite unresolved CP only after checking identity, and verify one star tap."""

from dataclasses import replace
import unittest
from unittest.mock import Mock, call, patch

from PIL import Image

from pokemgr.calibration.profile import CalibrationProfile
from pokemgr.calibration.regions import ScreenRegions
from pokemgr.indexer.snapshot import AppraisalSnapshot
from pokemgr.indexer.state_machine import IndexingStateMachine


def snapshot(**overrides):
    values = dict(
        display_name="Weezing", detected_species="Weezing", caught_species="Weezing",
        cp=-1, hp=137, atk=14, def_=14, sta=15,
        shiny=False, shadow=False, favorited=False, lucky=False, gender="none",
        weight_tag="", height_tag="", is_dynamax=False,
        detail_confidence=0.95, appraisal_confidence=0.95,
    )
    values.update(overrides)
    return AppraisalSnapshot(**values)


def frame(read, star="off", screen="appraisal"):
    image = Image.new("RGB", (968, 2376), "white")
    image.info.update(snapshot=read, star=star, screen=screen)
    return image


class FavoriteUnresolvedTests(unittest.TestCase):
    def setUp(self):
        self.delay = self.enterContext(patch("pokemgr.indexer.state_machine.human_delay"))
        self.star = self.enterContext(patch(
            "pokemgr.reader.icons.favorite_state",
            side_effect=lambda image, _region: image.info["star"],
        ))
        profile = CalibrationProfile(
            device_model="test", serial="serial", resolution="968x2376", density=420,
            regions=ScreenRegions.default_for_resolution(968, 2376, density=420),
        )
        self.adb, self.db = Mock(), Mock()
        self.sm = IndexingStateMachine(self.adb, profile, self.db)
        self.sm.nav.detect_screen = Mock(side_effect=lambda image: image.info["screen"])
        self.sm.reader.are_bars_visible = Mock(return_value=True)
        self.sm._read_appraisal_snapshot = Mock(side_effect=lambda image: (
            image.info["snapshot"].as_detail() | {
                "snapshot_read_complete": image.info["snapshot"].read_complete,
            },
            image.info["snapshot"].as_appraisal(),
        ))
        self.original = snapshot()
        self.initial = frame(self.original)
        self.fresh = frame(self.original)
        self.confirmed = frame(replace(self.original, favorited=True), "on")
        self.sm._fast_screencap = Mock(side_effect=[self.fresh, self.confirmed])

    def favorite(self):
        return self.sm._favorite_unresolved_snapshot(self.initial)

    def assert_one_star_tap(self):
        self.assertEqual(
            [call(*self.sm.regions.favorite_star_region.center, jitter=0)],
            self.adb.tap.call_args_list,
        )
        self.adb.swipe.assert_not_called()
        self.db.insert_pokemon.assert_not_called()

    def test_outline_star_is_tapped_once_and_full_identity_is_rechecked_before_return(self):
        result = self.favorite()

        self.assertIs(self.confirmed, result)
        self.assert_one_star_tap()
        self.assertEqual(
            [call(self.initial), call(self.fresh), call(self.confirmed)],
            self.sm._read_appraisal_snapshot.call_args_list,
        )
        self.assertEqual(2, self.sm._fast_screencap.call_count)
        self.assertEqual(
            [call(self.fresh, self.sm.regions.favorite_star_region),
             call(self.confirmed, self.sm.regions.favorite_star_region)],
            self.star.call_args_list,
        )

    def test_already_favorited_fresh_frame_is_preserved_without_tapping(self):
        self.fresh.info["star"] = "on"

        result = self.favorite()

        self.assertIs(self.fresh, result)
        self.adb.tap.assert_not_called()
        self.assertEqual(1, self.sm._fast_screencap.call_count)

    def test_unknown_star_is_reread_before_any_tap(self):
        self.fresh.info["star"] = "unknown"
        outlined = frame(self.original, "off")
        self.sm._fast_screencap.side_effect = [self.fresh, outlined, self.confirmed]
        tapped_after_reads = []
        self.adb.tap.side_effect = lambda *_args, **_kwargs: tapped_after_reads.append(self.star.call_count)

        result = self.favorite()

        self.assertIs(self.confirmed, result)
        self.assertEqual([2], tapped_after_reads)
        self.assert_one_star_tap()

    def test_three_unknown_star_reads_stop_without_guessing_or_tapping(self):
        self.sm._fast_screencap.side_effect = [frame(self.original, "unknown") for _ in range(3)]

        with self.assertRaises(RuntimeError):
            self.favorite()

        self.assertEqual(3, self.sm._fast_screencap.call_count)
        self.assertEqual(3, self.star.call_count)
        self.adb.tap.assert_not_called()

    def test_delayed_favorite_confirmation_never_repeats_toggle(self):
        self.sm._fast_screencap.side_effect = [
            self.fresh, frame(self.original, "off"), frame(self.original, "unknown"), self.confirmed,
        ]

        result = self.favorite()

        self.assertIs(self.confirmed, result)
        self.assert_one_star_tap()
        self.assertEqual(4, self.sm._fast_screencap.call_count)
        self.assertEqual([call(0.2, 1.0)] * 3, self.delay.call_args_list)

    def test_missed_star_tap_stops_after_three_readbacks_without_retoggling(self):
        self.sm._fast_screencap.side_effect = [
            self.fresh, *[frame(self.original, "off") for _ in range(3)],
        ]

        with self.assertRaises(RuntimeError):
            self.favorite()

        self.assert_one_star_tap()
        self.assertEqual(4, self.sm._fast_screencap.call_count)

    def test_changed_name_hp_or_ivs_before_tap_rejects_fresh_frame(self):
        for changes in ({"display_name": "Other"}, {"hp": 138}, {"atk": 13}, {"caught_species": "Koffing"}):
            with self.subTest(changes=changes):
                self.fresh.info["snapshot"] = replace(self.original, **changes)
                self.sm._fast_screencap.side_effect = [self.fresh]
                with self.assertRaises(RuntimeError):
                    self.favorite()
                self.adb.tap.assert_not_called()

    def test_changed_identity_after_star_tap_cannot_be_confirmed_by_gold_star(self):
        self.confirmed.info["snapshot"] = replace(self.original, def_=13, favorited=True)

        with self.assertRaises(RuntimeError):
            self.favorite()

        self.assert_one_star_tap()

    def test_exeggutor_ocr_spelling_drift_can_be_favorited_with_unchanged_stats_and_pixels(self):
        original = snapshot(display_name="Exegeutor", detected_species="Exeggutor", caught_species="Exeggutor")
        self.initial = frame(original)
        self.fresh = frame(replace(original, display_name="Exeggeutor"))
        self.confirmed = frame(replace(original, display_name="Exeggutor", favorited=True), "on")
        self.sm._fast_screencap.side_effect = [self.fresh, self.confirmed]

        self.assertIs(self.confirmed, self.favorite())

        self.assert_one_star_tap()

    def test_name_wobble_cannot_hide_changed_hp_or_iv(self):
        for change in ({"hp": 138}, {"atk": 13}, {"def_": 13}, {"sta": 14}):
            with self.subTest(change=change):
                self.sm._fast_screencap.side_effect = [
                    frame(replace(self.original, display_name="Weezlng", **change)),
                ]
                with self.assertRaisesRegex(RuntimeError, "identity changed"):
                    self.favorite()
                self.adb.tap.assert_not_called()

    def test_numbered_nickname_change_is_not_default_name_ocr_drift(self):
        self.initial.info["snapshot"] = replace(self.original, display_name="Weezing1")
        self.sm._fast_screencap.side_effect = [
            frame(replace(self.original, display_name="Weezing2")),
        ]

        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            self.favorite()

        self.adb.tap.assert_not_called()

    def test_identity_change_while_waiting_for_outline_star_prevents_tap(self):
        self.fresh.info["star"] = "unknown"
        different = frame(replace(self.original, hp=138), "off")
        self.sm._fast_screencap.side_effect = [self.fresh, different]

        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            self.favorite()

        self.adb.tap.assert_not_called()
        self.assertEqual(2, self.sm._fast_screencap.call_count)

    def test_incomplete_original_appraisal_never_authorizes_favorite(self):
        self.initial.info["snapshot"] = replace(self.original, read_complete=False)

        with self.assertRaises(RuntimeError):
            self.favorite()

        self.sm._fast_screencap.assert_not_called()
        self.adb.tap.assert_not_called()

    def test_lost_appraisal_on_readback_stops_after_single_tap(self):
        self.confirmed.info["screen"] = "detail"

        with self.assertRaises(RuntimeError):
            self.favorite()

        self.assert_one_star_tap()

    def test_abort_before_favorite_performs_no_reads_or_inputs(self):
        self.sm._abort = True

        self.assertIsNone(self.favorite())

        self.sm._read_appraisal_snapshot.assert_not_called()
        self.sm._fast_screencap.assert_not_called()
        self.adb.tap.assert_not_called()

    def test_abort_during_star_tap_stops_before_readback(self):
        def abort(*_args, **_kwargs):
            self.sm._abort = True
        self.adb.tap.side_effect = abort

        self.assertIsNone(self.favorite())

        self.assert_one_star_tap()
        self.assertEqual(1, self.sm._fast_screencap.call_count)

    def test_abort_during_unknown_star_wait_stops_without_more_captures(self):
        self.fresh.info["star"] = "unknown"
        def abort(*_args):
            self.sm._abort = True
        self.delay.side_effect = abort

        self.assertIsNone(self.favorite())

        self.adb.tap.assert_not_called()
        self.assertEqual(1, self.sm._fast_screencap.call_count)

    def test_abort_during_gold_star_read_does_not_return_verified_frame(self):
        def read_and_abort(image, _region):
            if image is self.confirmed:
                self.sm._abort = True
            return image.info["star"]
        self.star.side_effect = read_and_abort

        self.assertIsNone(self.favorite())

        self.assert_one_star_tap()
        self.assertEqual(2, self.sm._fast_screencap.call_count)


if __name__ == "__main__":
    unittest.main()
