"""Complete HP/IV evidence selects whether same-frame CP OCR is needed."""

from dataclasses import replace
import unittest
from unittest.mock import Mock, call, patch

from PIL import Image

from pokemgr.calibration.profile import CalibrationProfile
from pokemgr.calibration.regions import ScreenRegions
from pokemgr.indexer.snapshot import AppraisalSnapshot, validate_snapshot
from pokemgr.indexer.state_machine import IndexingStateMachine


SPECIES = {
    "zacian_crowned_sword": {
        "name": "Zacian (Crowned Sword)",
        "base_atk": 332, "base_def": 240, "base_sta": 192,
    },
}


def snapshot(**overrides):
    values = dict(
        display_name="Zacian", detected_species="Zacian", caught_species="Zacian",
        cp=-1, hp=173, atk=13, def_=13, sta=14,
        shiny=False, shadow=False, favorited=True, lucky=False, gender="none",
        weight_tag="", height_tag="", is_dynamax=False,
        detail_confidence=0.95, appraisal_confidence=0.95,
    )
    values.update(overrides)
    return AppraisalSnapshot(**values)


def frame(read, screen="appraisal"):
    image = Image.new("RGB", (968, 2376), "white")
    image.info.update(snapshot=read, screen=screen)
    return image


class HpIvFirstTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("pokemgr.pvp.resolver._default_species_map", return_value=SPECIES))
        self.enterContext(patch("pokemgr.indexer.state_machine.human_delay"))
        regions = ScreenRegions.default_for_resolution(968, 2376, density=420)
        profile = CalibrationProfile(
            device_model="test", serial="serial", resolution="968x2376",
            density=420, regions=regions,
        )
        self.adb, self.db = Mock(), Mock()
        self.sm = IndexingStateMachine(self.adb, profile, self.db)
        self.sm.use_calculated_cp = True
        self.sm.use_cp_animation = True
        self.sm.reader.read_cp = Mock(return_value=(-1, 0.0))
        self.sm.nav.detect_screen = Mock(side_effect=lambda image: image.info["screen"])
        self.sm.reader.are_bars_visible = Mock(return_value=True)

    def test_unique_hp_iv_cp_is_accepted_without_any_cp_ocr(self):
        original = snapshot(hp=174, atk=11, def_=10)

        decision = self.sm._validate_appraisal_snapshot(original, frame(original))

        self.assertTrue(decision.accepted)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertEqual("calculated", decision.cp_source)
        self.sm.reader.read_cp.assert_not_called()
        self.adb.tap.assert_not_called()

    def test_deferring_detail_cp_preserves_name_icon_and_size_metadata(self):
        image = frame(snapshot())
        self.sm.reader._read_size_tags = True
        with patch("pokemgr.reader.screen.ocr.read_cp") as cp_ocr, patch(
            "pokemgr.reader.screen.ocr.read_species_name", return_value=("Zacian", 0.94),
        ), patch("pokemgr.reader.screen.match_species_name", return_value="Zacian"), patch(
            "pokemgr.reader.screen.icons.is_favorited", return_value=True,
        ), patch("pokemgr.reader.screen.icons.is_lucky", return_value=True), patch(
            "pokemgr.reader.screen._detect_gender", return_value="male",
        ), patch("pokemgr.reader.screen.ocr.read_size_label", side_effect=["HEAVIEST", "TALLEST"]):
            detail = self.sm.reader.read_detail_screen(image, include_cp=False)

        cp_ocr.assert_not_called()
        self.assertEqual(-1, detail["cp"])
        self.assertEqual("Zacian", detail["display_name"])
        self.assertEqual("Zacian", detail["species"])
        self.assertTrue(detail["favorited"])
        self.assertTrue(detail["lucky"])
        self.assertEqual("male", detail["gender"])
        self.assertEqual("HEAVIEST", detail["weight_tag"])
        self.assertEqual("TALLEST", detail["height_tag"])
        self.assertEqual(0.94, detail["confidence"])

    def test_hp_and_ivs_finish_before_quick_cp_and_detail_read_defers_cp(self):
        original = snapshot()
        image = frame(original)
        completed = set()
        self.sm.reader.read_detail_screen = Mock(return_value=original.as_detail())
        def hp(_image):
            completed.add("hp")
            return original.hp
        def ivs(_image):
            completed.add("ivs")
            return original.as_appraisal()
        self.sm.reader.read_hp = Mock(side_effect=hp)
        self.sm.reader.read_appraisal_screen = Mock(side_effect=ivs)
        def cp(_image, **kwargs):
            self.assertEqual({"hp", "ivs"}, completed)
            self.assertEqual({"fast": True}, kwargs)
            return 5561, 0.95
        self.sm.reader.read_cp.side_effect = cp
        with patch("pokemgr.reader.ocr.is_in_gym", return_value=False), patch(
            "pokemgr.reader.ocr.read_caught_species", return_value="Zacian",
        ):
            detail, appraisal = self.sm._read_appraisal_snapshot(image)

        self.sm.reader.read_cp.assert_not_called()
        self.sm.reader.read_detail_screen.assert_called_once_with(image, include_cp=False)
        observed = AppraisalSnapshot.from_reads(detail, appraisal)
        self.assertTrue(observed.read_complete)
        self.assertEqual(original.hp, observed.hp)
        self.assertEqual(original.ivs, observed.ivs)
        self.assertTrue(observed.favorited)
        decision = self.sm._validate_appraisal_snapshot(observed, image)
        self.assertTrue(decision.accepted)
        self.sm.reader.read_cp.assert_called_once_with(image, fast=True)

    def test_ambiguous_hp_ivs_accept_exact_quick_cp_without_exhaustive_pass(self):
        original = snapshot()
        image = frame(original)
        self.sm.reader.read_cp.return_value = (5561, 0.9)

        decision = self.sm._validate_appraisal_snapshot(original, image)

        self.assertTrue(decision.accepted)
        self.assertEqual(5561, decision.snapshot.cp)
        self.sm.reader.read_cp.assert_called_once_with(image, fast=True)

    def test_failed_quick_cp_uses_expected_candidates_on_the_same_frame(self):
        original = snapshot()
        image = frame(original)
        self.sm.reader.read_cp.side_effect = [(561, 0.9), (5561, 0.95)]

        decision = self.sm._validate_appraisal_snapshot(original, image)

        self.assertTrue(decision.accepted)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertEqual(
            [call(image, fast=True), call(image, expected_cps={5561, 5594})],
            self.sm.reader.read_cp.call_args_list,
        )

    def test_calculation_disabled_requires_observed_cp_even_for_a_unique_hp_iv_result(self):
        self.sm.use_calculated_cp = False
        original = snapshot(hp=174, atk=11, def_=10)
        image = frame(original)
        self.sm.reader.read_cp.return_value = (5561, 0.9)

        decision = self.sm._validate_appraisal_snapshot(original, image)

        self.assertTrue(decision.accepted)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertFalse(decision.cp_source.startswith("calculated"))
        self.sm.reader.read_cp.assert_called_once_with(image, fast=True)

    def test_existing_exact_positive_cp_is_preserved_without_new_ocr(self):
        original = snapshot(cp=5561)
        self.sm.reader.read_cp.return_value = (5594, 0.99)

        decision = self.sm._validate_appraisal_snapshot(original, frame(original))

        self.assertTrue(decision.accepted)
        self.assertEqual(5561, decision.snapshot.cp)
        self.sm.reader.read_cp.assert_not_called()

    def test_missing_hp_or_name_keeps_legacy_exact_visible_cp_resolution(self):
        for changes in (
            {"hp": -1}, {"caught_species": ""},
            {"display_name": "", "detected_species": ""},
        ):
            with self.subTest(changes=changes):
                original = snapshot(**changes)
                image = frame(original)
                expected = validate_snapshot(replace(original, cp=5561), False)
                self.assertTrue(expected.accepted)
                self.sm.reader.read_cp.reset_mock()
                self.sm.reader.read_cp.return_value = (5561, 0.9)

                decision = self.sm._validate_appraisal_snapshot(original, image)

                self.assertEqual(expected, decision)
                self.sm.reader.read_cp.assert_called_once_with(image, fast=False)
                self.adb.tap.assert_not_called()
                self.adb.swipe.assert_not_called()

    def test_unfinished_or_invalid_iv_read_does_not_authorize_cp_ocr(self):
        for changes in ({"read_complete": False}, {"atk": -1}, {"sta": 16}):
            with self.subTest(changes=changes):
                original = snapshot(**changes)
                self.sm.reader.read_cp.return_value = (5561, 0.9)

                decision = self.sm._validate_appraisal_snapshot(original, frame(original))

                self.assertFalse(decision.accepted)
                self.sm.reader.read_cp.assert_not_called()

    def test_unique_calculated_result_still_needs_two_matching_full_appraisals(self):
        original = snapshot(hp=174, atk=11, def_=10)
        initial, fresh = frame(original), frame(original)
        self.sm._wait_for_stable_appraisal = Mock(return_value=(initial, "stable"))
        self.sm._fast_screencap = Mock(return_value=fresh)
        self.sm._read_appraisal_snapshot = Mock(side_effect=lambda image: (
            image.info["snapshot"].as_detail(), image.info["snapshot"].as_appraisal(),
        ))

        decision, result_frame, status, _reason = self.sm._acquire_validated_snapshot()

        self.assertEqual("ok", status)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertIs(fresh, result_frame)
        self.assertEqual([call(initial), call(fresh)], self.sm._read_appraisal_snapshot.call_args_list)
        self.sm.reader.read_cp.assert_not_called()
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()

    def test_new_final_frame_cp_conflict_rejects_a_model_witness_after_deferred_read(self):
        original = snapshot()
        initial, fresh, final = frame(original), frame(original), frame(original)
        detail = frame(replace(original, cp=5561), "detail")
        self.sm._fast_screencap = Mock(side_effect=[fresh, detail])
        self.sm._wait_for_stable_appraisal = Mock(return_value=(final, "stable"))
        self.sm._reopen_appraisal = Mock(return_value=True)
        self.sm._read_appraisal_snapshot = Mock(side_effect=lambda image: (
            image.info["snapshot"].as_detail(), image.info["snapshot"].as_appraisal(),
        ))
        self.sm.reader.read_detail_screen = Mock(side_effect=lambda image: image.info["snapshot"].as_detail())
        self.sm.reader.read_hp = Mock(return_value=original.hp)
        self.sm.reader.read_cp.side_effect = lambda image, **_kwargs: (
            (5594, 0.95) if image is final else (-1, 0.0)
        )

        with self.assertRaisesRegex(RuntimeError, "conflicts"):
            self.sm._recover_cp_with_model_taps(original, initial)

        self.sm.reader.read_cp.assert_any_call(final)
        self.sm._read_appraisal_snapshot.assert_any_call(final)
        self.sm._reopen_appraisal.assert_called_once()
        self.db.insert_pokemon.assert_not_called()


if __name__ == "__main__":
    unittest.main()
