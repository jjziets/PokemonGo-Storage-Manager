"""Alternate appraisal OCR must stay bound to complete, same-frame evidence."""

from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

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
        cp=561, hp=173, atk=13, def_=13, sta=14,
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


class AppraisalCpFallbackTests(unittest.TestCase):
    def setUp(self):
        species_patch = patch(
            "pokemgr.pvp.resolver._default_species_map", return_value=SPECIES,
        )
        species_patch.start()
        self.addCleanup(species_patch.stop)
        delay_patch = patch("pokemgr.indexer.state_machine.human_delay")
        delay_patch.start()
        self.addCleanup(delay_patch.stop)

        regions = ScreenRegions.default_for_resolution(968, 2376, density=420)
        profile = CalibrationProfile(
            device_model="test", serial="serial", resolution="968x2376",
            density=420, regions=regions,
        )
        self.adb, self.db = Mock(), Mock()
        self.sm = IndexingStateMachine(self.adb, profile, self.db)
        self.sm.use_calculated_cp = True
        self.sm.use_cp_animation = True
        self.sm.nav.detect_screen = Mock(side_effect=lambda image: image.info["screen"])
        self.sm.reader.are_bars_visible = Mock(return_value=True)
        self.sm.reader.read_cp = Mock(return_value=(-1, 0.0))
        self.sm._read_appraisal_snapshot = Mock(side_effect=lambda image: (
            image.info["snapshot"].as_detail() | {
                "snapshot_read_complete": image.info["snapshot"].read_complete,
            },
            image.info["snapshot"].as_appraisal(),
        ))

    def test_exact_primary_cp_is_preserved_without_alternate_ocr(self):
        original = snapshot(cp=5561)
        self.sm.reader.read_cp.return_value = (5594, 0.9)

        decision = self.sm._validate_appraisal_snapshot(original, frame(original))

        self.assertTrue(decision.accepted)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertEqual("screen", decision.cp_source)
        self.sm.reader.read_cp.assert_not_called()

    def test_impossible_primary_uses_observed_exact_cp_from_the_same_frame(self):
        original = snapshot()
        image = frame(original)
        self.sm.reader.read_cp.return_value = (5561, 0.9)

        decision = self.sm._validate_appraisal_snapshot(original, image)

        self.assertTrue(decision.accepted)
        self.assertEqual("screen_alternate_ocr", decision.cp_source)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertEqual(original.ivs, decision.snapshot.ivs)
        self.assertEqual(original.hp, decision.snapshot.hp)
        self.assertEqual(original.favorited, decision.snapshot.favorited)
        self.assertEqual(561, original.cp)
        self.sm.reader.read_cp.assert_called_once_with(
            image, expected_cps={5561, 5594},
        )
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()

    def test_unique_calculation_avoids_alternate_ocr_unless_calculation_is_disabled(self):
        original = snapshot(cp=-1, hp=174, atk=11, def_=10)
        self.assertTrue(validate_snapshot(original, True).accepted)
        self.sm.reader.read_cp.return_value = (5561, 0.9)

        for enabled in (True, False):
            with self.subTest(calculated_cp=enabled):
                self.sm.use_calculated_cp = enabled
                self.sm.reader.read_cp.reset_mock()
                image = frame(original)
                decision = self.sm._validate_appraisal_snapshot(original, image)

                self.assertTrue(decision.accepted)
                self.assertEqual(5561, decision.snapshot.cp)
                if enabled:
                    self.assertEqual("calculated", decision.cp_source)
                    self.sm.reader.read_cp.assert_not_called()
                else:
                    self.assertEqual("screen", decision.cp_source)
                    self.sm.reader.read_cp.assert_called_once_with(
                        image, fast=True,
                    )

    def test_alternate_result_must_independently_fit_exact_evidence(self):
        for cp in (-1, 561, 9999):
            with self.subTest(cp=cp):
                self.sm.reader.read_cp.return_value = (cp, 0.99)
                original = snapshot()

                decision = self.sm._validate_appraisal_snapshot(original, frame(original))

                self.assertFalse(decision.accepted)
                self.assertIsNone(decision.snapshot)

    def test_unique_calculated_fallback_needs_no_alternate_ocr(self):
        for primary_cp in (-1, 561):
            with self.subTest(primary_cp=primary_cp):
                original = snapshot(cp=primary_cp, hp=174, atk=11, def_=10)
                expected = validate_snapshot(original, True)

                decision = self.sm._validate_appraisal_snapshot(original, frame(original))

                self.assertEqual(expected, decision)
                self.assertTrue(decision.cp_source.startswith("calculated"))
                self.sm.reader.read_cp.assert_not_called()

    def test_incomplete_identity_never_authorizes_alternate_ocr(self):
        for changes in (
            {"caught_species": ""}, {"detected_species": ""}, {"display_name": ""},
            {"hp": -1}, {"atk": -1}, {"sta": 16}, {"read_complete": False},
        ):
            with self.subTest(changes=changes):
                original = snapshot(**changes)
                self.sm.use_calculated_cp = False

                decision = self.sm._validate_appraisal_snapshot(original, frame(original))

                self.assertFalse(decision.accepted)
                self.sm.reader.read_cp.assert_not_called()

    def test_no_exact_hp_iv_candidates_never_runs_alternate_ocr(self):
        original = snapshot(hp=999)

        decision = self.sm._validate_appraisal_snapshot(original, frame(original))

        self.assertFalse(decision.accepted)
        self.sm.reader.read_cp.assert_not_called()

    def test_abort_before_or_during_alternate_ocr_discards_new_evidence(self):
        original = snapshot()
        self.sm._abort = True
        decision = self.sm._validate_appraisal_snapshot(original, frame(original))
        self.assertFalse(decision.accepted)
        self.sm.reader.read_cp.assert_not_called()

        self.sm._abort = False

        def cancel_during_read(*_args, **_kwargs):
            self.sm._abort = True
            return 5561, 0.9

        self.sm.reader.read_cp.side_effect = cancel_during_read
        decision = self.sm._validate_appraisal_snapshot(original, frame(original))
        self.assertFalse(decision.accepted)
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()

    def test_initial_appraisal_observation_avoids_model_gestures(self):
        image = frame(snapshot())
        self.sm._wait_for_stable_appraisal = Mock(return_value=(image, "stable"))
        self.sm.reader.read_cp.return_value = (5561, 0.9)
        self.sm._recover_cp_with_model_taps = Mock()

        decision, result_frame, status, _reason = self.sm._acquire_validated_snapshot()

        self.assertEqual("ok", status)
        self.assertIs(image, result_frame)
        self.assertEqual("screen_alternate_ocr", decision.cp_source)
        self.sm._recover_cp_with_model_taps.assert_not_called()

    def test_second_settled_read_can_recover_after_model_budget_is_exhausted(self):
        original = snapshot()
        first, second = frame(original), frame(original)
        self.sm._wait_for_stable_appraisal = Mock(
            side_effect=[(first, "stable"), (second, "stable")],
        )
        self.sm.reader.read_cp.side_effect = [(-1, 0.0), (5561, 0.9)]
        self.sm._recover_cp_with_model_taps = Mock(
            return_value=(validate_snapshot(original, True), first),
        )

        decision, result_frame, status, _reason = self.sm._acquire_validated_snapshot()

        self.assertEqual("ok", status)
        self.assertIs(second, result_frame)
        self.assertEqual("screen_alternate_ocr", decision.cp_source)
        self.assertEqual(5561, decision.snapshot.cp)
        self.sm._recover_cp_with_model_taps.assert_called_once_with(
            original, first, save_failure_evidence=True,
        )
        self.assertIs(second, self.sm.reader.read_cp.call_args.args[0])

    def _prepare_confirmation(self, original):
        first, confirmation = frame(original), frame(original)
        self.sm._last_validated_identity_key = replace(original, cp=5500).identity_key
        self.sm._wait_for_stable_appraisal = Mock(
            return_value=(first, "stable_transition_unobserved"),
        )
        self.sm._fast_screencap = Mock(return_value=confirmation)
        return first, confirmation

    def test_unobserved_transition_independently_rereads_cp_on_confirmation_frame(self):
        for confirmed_cp, expected_status in (
            (5561, "ok"), (5594, "transition_identity_inconsistent"),
        ):
            with self.subTest(confirmed_cp=confirmed_cp):
                first, confirmation = self._prepare_confirmation(snapshot())
                self.sm.reader.read_cp.reset_mock()
                self.sm.reader.read_cp.side_effect = [(5561, 0.9), (confirmed_cp, 0.9)]

                decision, result_frame, status, _reason = self.sm._acquire_validated_snapshot(
                    previous_accepted=first, require_transition=True,
                )

                self.assertEqual(expected_status, status)
                self.assertIs(confirmation, result_frame)
                self.assertIs(confirmation, self.sm.reader.read_cp.call_args.args[0])
                if expected_status == "ok":
                    self.assertEqual(5561, decision.snapshot.cp)
                else:
                    self.assertIsNone(decision)

    def test_abort_during_confirmation_cannot_return_accepted_calculated_fallback(self):
        original = snapshot(cp=-1, hp=174, atk=11, def_=10)
        first, confirmation = self._prepare_confirmation(original)

        read_snapshot = self.sm._read_appraisal_snapshot.side_effect

        def read_and_abort(image):
            if image is confirmation:
                self.sm._abort = True
            return read_snapshot(image)

        self.sm._read_appraisal_snapshot.side_effect = read_and_abort

        decision, _result_frame, status, _reason = self.sm._acquire_validated_snapshot(
            previous_accepted=first, require_transition=True,
        )

        self.assertIsNone(decision)
        self.assertEqual("aborted", status)
        self.db.insert_pokemon.assert_not_called()

    def test_fresh_preclose_appraisal_observation_avoids_all_model_gestures(self):
        original = snapshot()
        fresh = frame(original)
        self.sm._fast_screencap = Mock(return_value=fresh)
        self.sm.reader.read_cp.return_value = (5561, 0.9)

        decision, result_frame = self.sm._recover_cp_with_model_taps(original, frame(original))

        self.assertTrue(decision.accepted)
        self.assertEqual("screen_alternate_ocr", decision.cp_source)
        self.assertIs(fresh, result_frame)
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()

    def test_final_appraisal_can_reveal_cp_after_all_detail_gestures_fail(self):
        original = snapshot()
        initial, fresh, final = frame(original), frame(original), frame(original)
        # One post-close frame, four initial tap frames, then a rotation and
        # post-tap frame for each of the four remaining model angles.
        detail_frames = [frame(original, "detail") for _ in range(1 + 4 + 4 * 2)]
        self.sm._fast_screencap = Mock(side_effect=[fresh, *detail_frames])
        self.sm._wait_for_stable_appraisal = Mock(return_value=(final, "stable"))
        self.sm._reopen_appraisal = Mock(return_value=True)
        self.sm._read_cp_from_powerup_preview = Mock(
            side_effect=lambda snapshot, frame, **_kwargs: (None, frame),
        )
        self.sm.reader.read_detail_screen = Mock(
            side_effect=lambda image: image.info["snapshot"].as_detail(),
        )
        self.sm.reader.read_hp = Mock(return_value=original.hp)
        self.sm.reader.read_cp.side_effect = lambda image, **_kwargs: (
            (5561, 0.9) if image is final else (-1, 0.0)
        )

        decision, result_frame = self.sm._recover_cp_with_model_taps(original, initial)

        self.assertTrue(decision.accepted)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertEqual("screen_alternate_ocr", decision.cp_source)
        self.assertIs(final, result_frame)
        self.assertEqual(9, self.adb.tap.call_count)
        self.assertEqual(4, self.adb.swipe.call_count)
        self.assertEqual(14, self.sm._fast_screencap.call_count)
        self.sm._reopen_appraisal.assert_called_once()


if __name__ == "__main__":
    unittest.main()
