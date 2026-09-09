"""CP animation recovery keeps the complete Pokemon identity authoritative."""

from dataclasses import replace
import unittest
from unittest.mock import Mock, call, patch

from PIL import Image

from pokemgr.calibration.profile import CalibrationProfile
from pokemgr.calibration.regions import ScreenRegions
from pokemgr.indexer.snapshot import AppraisalSnapshot, validate_snapshot
from pokemgr.indexer.state_machine import IndexingStateMachine


# A small, fixed GameMaster fixture keeps these regressions offline while using
# the real exact resolver and snapshot acceptance policy. HP173 and 13/13/14 IVs
# permit both CP5561 (level50) and CP5594 (level50.5), so CP561 cannot be repaired
# by arithmetic alone.
SPECIES = {
    "zacian_crowned_sword": {
        "name": "Zacian (Crowned Sword)",
        "base_atk": 332,
        "base_def": 240,
        "base_sta": 192,
    },
}


def _snapshot(**overrides):
    values = {
        "display_name": "Zacian",
        "detected_species": "Zacian",
        "caught_species": "Zacian",
        "cp": 561,
        "hp": 173,
        "atk": 13,
        "def_": 13,
        "sta": 14,
        "shiny": False,
        "shadow": False,
        "favorited": False,
        "lucky": False,
        "gender": "none",
        "weight_tag": "",
        "height_tag": "",
        "is_dynamax": False,
        "detail_confidence": 0.95,
        "appraisal_confidence": 0.95,
    }
    values.update(overrides)
    return AppraisalSnapshot(**values)


def _frame(screen, snapshot):
    image = Image.new("RGB", (968, 2376), "white")
    image.info["screen"] = screen
    image.info["snapshot"] = snapshot
    return image


class CpModelRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.resolver_patch = patch(
            "pokemgr.pvp.resolver._default_species_map", return_value=SPECIES
        )
        self.resolver_patch.start()
        self.addCleanup(self.resolver_patch.stop)
        self.delay_patch = patch("pokemgr.indexer.state_machine.human_delay")
        self.delay = self.delay_patch.start()
        self.addCleanup(self.delay_patch.stop)

        regions = ScreenRegions.default_for_resolution(968, 2376, density=420)
        profile = CalibrationProfile(
            device_model="test", serial="serial", resolution="968x2376",
            density=420, regions=regions,
        )
        self.adb = Mock()
        self.db = Mock()
        self.sm = IndexingStateMachine(self.adb, profile, self.db)
        self.sm.use_calculated_cp = True
        self.sm.use_cp_animation = True
        self.sm.nav.detect_screen = Mock(
            side_effect=lambda image=None: image.info["screen"]
        )
        self.sm.reader.are_bars_visible = Mock(return_value=True)
        self.sm.reader.read_detail_screen = Mock(
            side_effect=lambda image: image.info["snapshot"].as_detail()
        )
        self.sm.reader.read_hp = Mock(
            side_effect=lambda image: image.info["snapshot"].hp
        )
        self.sm.reader.read_cp = Mock(return_value=(-1, 0.0))
        self.sm._read_cp_from_powerup_preview = Mock(
            side_effect=lambda snapshot, frame, **_kwargs: (None, frame),
        )
        self.sm._read_appraisal_snapshot = Mock(
            side_effect=lambda image: (
                image.info["snapshot"].as_detail() | {
                    "snapshot_read_complete": image.info["snapshot"].read_complete,
                },
                image.info["snapshot"].as_appraisal(),
            )
        )
        self.sm._reopen_appraisal = Mock(return_value=True)
        self.sm._save_failed_appraisal = Mock()
        self.original = _snapshot()
        self.initial = _frame("appraisal", self.original)

    def _prepare(self, cps, *, initial=None, final=None, detail=None,
                 rotation_cps=None, rotation_animation_cps=None):
        original = initial or self.original
        self.initial = _frame("appraisal", original)
        self.final = _frame("appraisal", final or original)
        self.fresh = _frame("appraisal", original)
        self.detail = _frame("detail", detail or original)
        self.attempt_frames = [
            _frame("detail", replace(original, cp=cp)) for cp in cps
        ]
        self.rotation_frames = [
            _frame("detail", replace(original, cp=cp))
            for cp in ([-1] * 4 if rotation_cps is None else rotation_cps)
        ]
        self.rotation_animation_frames = [
            _frame("detail", replace(original, cp=cp))
            for cp in ([-1] * len(self.rotation_frames)
                       if rotation_animation_cps is None else rotation_animation_cps)
        ]
        self.assertEqual(len(self.rotation_frames), len(self.rotation_animation_frames))
        rotated_captures = [
            image for pair in zip(self.rotation_frames, self.rotation_animation_frames)
            for image in pair
        ]
        self.sm._fast_screencap = Mock(
            side_effect=[
                self.fresh, self.detail, *self.attempt_frames, *rotated_captures,
            ]
        )
        self.sm._wait_for_stable_appraisal = Mock(
            return_value=(self.final, "stable")
        )

    def _recover(self, snapshot=None):
        return self.sm._recover_cp_with_model_taps(
            snapshot or self.original, self.initial
        )

    def _model_taps(self):
        regions = self.sm.regions
        target = (
            regions.screen_width // 2,
            (regions.cp_region.y2 + regions.name_region.y) // 2,
        )
        return [call for call in self.adb.tap.call_args_list if call.args == target]

    def test_impossible_zacian_561_is_recovered_from_visible_5561(self):
        self.assertFalse(validate_snapshot(self.original, True).accepted)
        self._prepare([561, -1, 5561])

        decision, frame = self._recover()

        self.assertTrue(decision.accepted)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertEqual("Zacian (Crowned Sword)", decision.snapshot.detected_species)
        self.assertEqual(self.original.ivs, decision.snapshot.ivs)
        self.assertEqual(self.original.hp, decision.snapshot.hp)
        self.assertEqual("screen_after_animation", decision.cp_source)
        self.assertIs(frame, self.final)
        self.assertEqual(3, len(self._model_taps()))
        self.assertEqual(
            self.sm.regions.appraisal_close_x,
            self.adb.tap.call_args_list[0].args,
        )
        self.assertNotEqual(
            self.sm.regions.close_appraisal_target,
            self.adb.tap.call_args_list[0].args,
        )
        self.assertEqual(
            3, sum(call.args == (0.2, 1.0) for call in self.delay.call_args_list)
        )
        self.sm._reopen_appraisal.assert_called_once()
        self.sm._read_appraisal_snapshot.assert_any_call(self.final)
        self.db.insert_pokemon.assert_not_called()
        self.adb.swipe.assert_not_called()

    def test_animation_recovery_works_when_calculated_cp_is_disabled(self):
        self.sm.use_calculated_cp = False
        self._prepare([5561])

        decision, _frame = self._recover()

        self.assertTrue(decision.accepted)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertEqual("screen_after_animation", decision.cp_source)

    def test_cp_exposed_when_appraisal_closes_is_preserved_without_model_taps(self):
        self._prepare([561, -1, 561, -1], detail=_snapshot(cp=5561))

        decision, frame = self._recover()

        self.assertTrue(decision.accepted)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertEqual(self.original.hp, decision.snapshot.hp)
        self.assertEqual(self.original.ivs, decision.snapshot.ivs)
        self.assertIs(frame, self.final)
        self.assertEqual([], self._model_taps())
        self.assertEqual(2, self.sm._fast_screencap.call_count)
        self.sm._reopen_appraisal.assert_called_once()
        self.sm._read_appraisal_snapshot.assert_any_call(self.final)
        self.db.insert_pokemon.assert_not_called()
        self.adb.swipe.assert_not_called()

    def test_hidden_cp_exhaustion_is_bounded_to_four_taps_and_four_rotation_tap_pairs(self):
        self._prepare([561, -1, 561, -1])

        decision, frame = self._recover()

        self.assertFalse(decision.accepted)
        self.assertIsNone(decision.snapshot)
        self.assertIs(frame, self.final)
        self.assertEqual(8, len(self._model_taps()))
        self.assertEqual(4, self.adb.swipe.call_count)
        self.assertEqual(14, self.sm._fast_screencap.call_count)
        self.sm._reopen_appraisal.assert_called_once()
        self.db.insert_pokemon.assert_not_called()

    def test_two_matching_complete_reads_accept_unique_calculated_cp_without_any_inputs(self):
        self.sm.use_cp_animation = False
        unique = _snapshot(cp=-1, hp=174, atk=11, def_=10)
        self.assertTrue(validate_snapshot(unique, True).accepted)
        self._prepare([-1] * 4, initial=unique, final=unique)
        self.sm._wait_for_stable_appraisal.return_value = (self.initial, "stable")

        decision, frame, status, _reason = self.sm._acquire_validated_snapshot()

        self.assertTrue(decision.accepted)
        self.assertEqual("ok", status)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertEqual("calculated", decision.cp_source)
        self.assertIs(frame, self.fresh)
        self.assertIsNot(self.initial, self.fresh)
        self.assertEqual(
            [call(self.initial), call(self.fresh)],
            self.sm._read_appraisal_snapshot.call_args_list,
        )
        self.assertEqual(1, self.sm._fast_screencap.call_count)
        self.sm.reader.read_cp.assert_not_called()
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()
        self.sm._read_cp_from_powerup_preview.assert_not_called()
        self.sm._reopen_appraisal.assert_not_called()

    def test_invalid_positive_ocr_accepts_same_unique_cp_after_second_read_without_inputs(self):
        self.sm.use_cp_animation = False
        unique = _snapshot(cp=561, hp=174, atk=11, def_=10)
        self._prepare([561] * 4, initial=unique, final=unique)
        self.sm._wait_for_stable_appraisal.return_value = (self.initial, "stable")

        decision, frame, status, _reason = self.sm._acquire_validated_snapshot()

        self.assertTrue(decision.accepted)
        self.assertEqual("ok", status)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertEqual("calculated_after_invalid_ocr", decision.cp_source)
        self.assertIs(frame, self.fresh)
        self.assertEqual(
            [call(self.initial), call(self.fresh)],
            self.sm._read_appraisal_snapshot.call_args_list,
        )
        self.sm.reader.read_cp.assert_not_called()
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()
        self.sm._read_cp_from_powerup_preview.assert_not_called()
        self.sm._reopen_appraisal.assert_not_called()

    def test_unique_cp_cannot_be_accepted_when_second_hp_iv_read_changes_or_is_incomplete(self):
        unique = _snapshot(cp=-1, hp=174, atk=11, def_=10)
        for changes in ({"hp": 175}, {"atk": 12}, {"def_": 11}, {"sta": 15}, {"read_complete": False}):
            with self.subTest(changes=changes):
                self._prepare([-1] * 4, initial=unique)
                self.fresh.info["snapshot"] = replace(unique, **changes)

                with self.assertRaisesRegex(RuntimeError, "identity changed"):
                    self._recover(unique)

                self.sm._read_cp_from_powerup_preview.assert_not_called()
                self.sm._reopen_appraisal.assert_not_called()
                self.adb.tap.assert_not_called()
                self.adb.swipe.assert_not_called()
                self.db.insert_pokemon.assert_not_called()

    def test_candy_only_unique_cp_still_requires_an_independent_full_appraisal(self):
        renamed = _snapshot(cp=-1, hp=174, atk=11, def_=10, caught_species="",
                            display_name="My Dog", detected_species="My Dog", candy_family="Zacian")
        self._prepare([], initial=renamed, final=renamed)
        self.sm._wait_for_stable_appraisal.return_value = (self.initial, "stable")

        decision, result, status, _reason = self.sm._acquire_validated_snapshot()

        self.assertEqual("ok", status)
        self.assertTrue(decision.accepted)
        self.assertEqual(("Zacian (Crowned Sword)", 5561),
                         (decision.snapshot.detected_species, decision.snapshot.cp))
        self.assertEqual("My Dog", decision.snapshot.display_name)
        self.assertEqual([call(self.initial), call(self.fresh)], self.sm._read_appraisal_snapshot.call_args_list)
        self.assertIs(self.fresh, result)
        self.adb.tap.assert_not_called()

    def test_changed_candy_authority_cannot_confirm_unique_calculation(self):
        renamed = _snapshot(cp=-1, hp=174, atk=11, def_=10, caught_species="",
                            display_name="My Dog", detected_species="My Dog", candy_family="Zacian")
        self._prepare([], initial=renamed, final=renamed)
        self.fresh.info["snapshot"] = replace(renamed, candy_family="Zamazenta")

        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            self._recover(renamed)

        self.adb.tap.assert_not_called()

    def test_name_ocr_wobble_rereads_without_input_then_confirms_exact_identity(self):
        unique = _snapshot(cp=-1, hp=174, atk=11, def_=10)
        drift = _frame("appraisal", replace(unique, display_name="Zaclan"))
        confirmed = _frame("appraisal", unique)
        self.sm._fast_screencap = Mock(side_effect=[drift, confirmed])

        decision, result = self.sm._recover_cp_with_model_taps(
            unique, _frame("appraisal", unique),
        )

        self.assertTrue(decision.accepted)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertIs(confirmed, result)
        self.assertEqual(2, self.sm._fast_screencap.call_count)
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()

    def test_persistent_name_ocr_wobble_is_reviewable_and_keeps_latest_evidence(self):
        self.initial.info["snapshot"] = replace(self.original, display_name="Zaclan")
        fresh = [_frame("appraisal", self.original) for _ in range(3)]
        self.sm._fast_screencap = Mock(side_effect=fresh)
        self.sm._wait_for_stable_appraisal = Mock(return_value=(self.initial, "stable"))

        decision, result, status, reason = self.sm._acquire_validated_snapshot()

        self.assertIsNone(decision)
        self.assertIs(fresh[-1], result)
        self.assertEqual("invalid", status)
        self.assertIn("name OCR", reason)
        self.assertEqual(3, self.sm._fast_screencap.call_count)
        self.assertEqual(6, self.sm._save_failed_appraisal.call_count)
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()
        self.sm._reopen_appraisal.assert_not_called()

    def test_name_drift_does_not_let_unobserved_transition_skip_current_position(self):
        self.initial.info["snapshot"] = replace(self.original, display_name="Zaclan")
        self.sm._fast_screencap = Mock(side_effect=[
            _frame("appraisal", self.original) for _ in range(3)
        ])
        self.sm._wait_for_stable_appraisal = Mock(return_value=(
            self.initial, "stable_transition_unobserved",
        ))

        decision, _result, status, _reason = self.sm._acquire_validated_snapshot()

        self.assertIsNone(decision)
        self.assertEqual("transition_identity_incomplete", status)
        self.adb.tap.assert_not_called()

    def test_abort_during_name_reread_delay_stops_without_more_reads(self):
        drift = _frame("appraisal", replace(self.original, display_name="Zaclan"))
        self.sm._fast_screencap = Mock(return_value=drift)
        self.delay.side_effect = lambda *_args: setattr(self.sm, "_abort", True)

        decision, result = self._recover()

        self.assertIsNone(decision)
        self.assertIs(drift, result)
        self.assertEqual(1, self.sm._fast_screencap.call_count)
        self.adb.tap.assert_not_called()

    def test_name_drift_with_changed_pixels_is_still_a_fatal_identity_change(self):
        drift = _frame("appraisal", replace(self.original, display_name="Zaclan"))
        drift.paste("black", (0, 0, drift.width, drift.height))
        self.sm._fast_screencap = Mock(return_value=drift)

        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            self._recover()

        self.assertEqual(1, self.sm._fast_screencap.call_count)
        self.adb.tap.assert_not_called()

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_persistent_name_drift_favorites_skips_and_scans_next_position(self, _paddle):
        unique = _snapshot(cp=-1, hp=174, atk=11, def_=10, display_name="Zaclan")
        self.initial = _frame("appraisal", unique)
        fresh = [_frame("appraisal", replace(unique, display_name="Zacian")) for _ in range(3)]
        outlined = _frame("appraisal", replace(unique, display_name="Zaciian"))
        gold = _frame("appraisal", replace(unique, favorited=True))
        self.sm._fast_screencap = Mock(side_effect=[*fresh, outlined, gold])
        self.sm._wait_for_stable_appraisal = Mock(return_value=(self.initial, "stable"))
        acquire = self.sm._acquire_validated_snapshot
        next_decision = validate_snapshot(_snapshot(cp=5561), False)
        operations = iter((lambda **kwargs: acquire(**kwargs),
                           lambda **kwargs: (next_decision, self.initial, "ok", "exact")))
        self.sm._acquire_validated_snapshot = Mock(side_effect=lambda **kwargs: next(operations)(**kwargs))
        self.sm._advance_from_confirmed_appraisal = Mock(return_value=True)

        with patch("pokemgr.reader.icons.favorite_state", side_effect=["off", "on"]):
            self.sm.start(expected_total=2)

        self.assertEqual((1, 1, 2), (self.sm.count, self.sm.skipped_count, self.sm.visited_count))
        self.db.insert_pokemon.assert_called_once()
        self.assertEqual(1, self.db.insert_pokemon.call_args.args[2])
        self.assertEqual([call(*self.sm.regions.favorite_star_region.center, jitter=0)],
                         self.adb.tap.call_args_list)
        self.sm._advance_from_confirmed_appraisal.assert_called_once()

    def test_pause_during_name_retry_discards_both_matching_and_wobbling_confirmation(self):
        for resumed_name in ("Zacian", "Zaclan"):
            with self.subTest(resumed_name=resumed_name):
                drift = _frame("appraisal", replace(self.original, display_name="Zaclan"))
                resumed = _frame("appraisal", replace(self.original, display_name=resumed_name))
                self.sm._fast_screencap = Mock(side_effect=[drift, resumed])
                self.sm._wait_for_stable_appraisal = Mock(return_value=(self.initial, "stable"))
                self.delay.side_effect = lambda *_args: setattr(
                    self.sm, "_pause_generation", self.sm._pause_generation + 1,
                )

                decision, result, status, _reason = self.sm._acquire_validated_snapshot()

                self.assertIsNone(decision)
                self.assertIsNone(result)
                self.assertEqual("reacquire", status)
                self.adb.tap.assert_not_called()

    def test_unique_arithmetic_cp_still_uses_preview_when_calculation_is_disabled(self):
        unique = _snapshot(cp=-1, hp=174, atk=11, def_=10)
        self.sm.use_calculated_cp = False
        self._prepare([-1] * 4, initial=unique, final=unique)

        decision, _frame = self._recover(unique)

        self.assertFalse(decision.accepted)
        self.sm._read_cp_from_powerup_preview.assert_called_once()

    def test_fast_unresolved_cp_returns_rejection_without_inputs_or_close_calibration(self):
        self.sm.use_cp_animation = False
        self.sm.regions.appraisal_close_x = None
        self._prepare([])

        decision, result_frame = self._recover()

        self.assertFalse(decision.accepted)
        self.assertIs(self.fresh, result_frame)
        self.assertEqual(1, self.sm._fast_screencap.call_count)
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()
        self.sm._reopen_appraisal.assert_not_called()
        self.sm._read_cp_from_powerup_preview.assert_not_called()

    def test_fast_unresolved_acquisition_retries_three_reads_then_saves_skipped_evidence(self):
        self.sm.use_cp_animation = False
        self._prepare([])
        self.sm._save_failed_appraisal = Mock()

        decision, result_frame, status, _reason = self.sm._acquire_validated_snapshot()

        self.assertIsNone(decision)
        self.assertEqual("invalid", status)
        self.assertIs(self.final, result_frame)
        self.assertEqual(3, self.sm._wait_for_stable_appraisal.call_count)
        self.assertEqual(1, self.sm._fast_screencap.call_count)
        self.sm._save_failed_appraisal.assert_called_once()
        self.assertIs(self.final, self.sm._save_failed_appraisal.call_args.args[0])
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()
        self.sm._reopen_appraisal.assert_not_called()
        self.sm._read_cp_from_powerup_preview.assert_not_called()

    def test_valid_visible_cp_never_enters_model_recovery(self):
        valid = _snapshot(cp=5561)
        initial = _frame("appraisal", valid)
        self.sm._wait_for_stable_appraisal = Mock(return_value=(initial, "stable"))
        self.sm._recover_cp_with_model_taps = Mock(
            side_effect=AssertionError("valid visible CP must not animate")
        )

        decision, frame, status, _reason = self.sm._acquire_validated_snapshot()

        self.assertTrue(decision.accepted)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertIs(frame, initial)
        self.assertEqual("ok", status)
        self.sm._recover_cp_with_model_taps.assert_not_called()
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()

    def test_acquisition_recovery_budget_is_shared_across_snapshot_retries(self):
        self._prepare([561, -1, 561, -1])

        decision, frame, status, _reason = self.sm._acquire_validated_snapshot()

        self.assertIsNone(decision)
        self.assertIs(frame, self.final)
        self.assertEqual("invalid", status)
        self.assertEqual(8, len(self._model_taps()))
        self.assertEqual(4, self.adb.swipe.call_count)
        self.sm._reopen_appraisal.assert_called_once()

    def test_short_model_rotation_recovers_exact_cp_after_four_unsuccessful_taps(self):
        self._prepare([-1] * 4, rotation_cps=[-1, 5561])

        decision, frame = self._recover()

        self.assertTrue(decision.accepted)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertEqual("screen_after_animation", decision.cp_source)
        self.assertEqual(5, len(self._model_taps()))
        self.assertEqual(2, self.adb.swipe.call_count)
        regions = self.sm.regions
        x = regions.screen_width // 2
        y = (regions.cp_region.y2 + regions.name_region.y) // 2
        for gesture in self.adb.swipe.call_args_list:
            self.assertEqual(
                (x, y, x - round(regions.screen_width * 0.20), y), gesture.args,
            )
            self.assertEqual({"duration_ms": 500, "jitter": 0}, gesture.kwargs)
        self.assertIs(frame, self.final)
        self.sm._read_appraisal_snapshot.assert_any_call(self.final)

    def test_rotated_model_is_confirmed_then_tapped_and_reread_to_reveal_cp(self):
        self._prepare([561] * 4, rotation_cps=[561], rotation_animation_cps=[5561])
        rotated, animated = self.rotation_frames[0], self.rotation_animation_frames[0]
        events = []
        captures = iter(self.sm._fast_screencap.side_effect)
        def label(image):
            return "rotated" if image is rotated else "animated" if image is animated else "initial"
        def capture():
            image = next(captures)
            events.append("capture " + label(image))
            return image
        def read_detail(image):
            events.append("name " + label(image))
            return image.info["snapshot"].as_detail()
        def read_hp(image):
            events.append("hp " + label(image))
            return image.info["snapshot"].hp
        self.sm._fast_screencap.side_effect = capture
        self.sm.reader.read_detail_screen.side_effect = read_detail
        self.sm.reader.read_hp.side_effect = read_hp
        self.adb.swipe.side_effect = lambda *_args, **_kwargs: events.append("rotate")
        self.adb.tap.side_effect = lambda *_args, **_kwargs: events.append("tap")
        def delay(base, jitter):
            if len(self._model_taps()) > 4:
                self.assertEqual((0.2, 1.0), (base, jitter))
                events.append("wait")
        self.delay.side_effect = delay

        decision, result_frame = self._recover()

        self.assertTrue(decision.accepted)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertIs(self.final, result_frame)
        self.assertEqual([
            "rotate", "capture rotated", "name rotated", "hp rotated",
            "tap", "wait", "capture animated", "name animated", "hp animated",
        ], events[events.index("rotate"):])
        self.assertEqual(5, len(self._model_taps()))
        self.assertEqual(1, self.adb.swipe.call_count)
        self.sm._read_cp_from_powerup_preview.assert_not_called()
        self.sm._read_appraisal_snapshot.assert_any_call(self.final)

    def test_exact_cp_exposed_by_rotation_is_preserved_without_post_rotation_tap(self):
        self._prepare([561] * 4, rotation_cps=[5561], rotation_animation_cps=[561])

        decision, _result_frame = self._recover()

        self.assertTrue(decision.accepted)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertEqual(4, len(self._model_taps()))
        self.assertEqual(1, self.adb.swipe.call_count)
        self.assertEqual(7, self.sm._fast_screencap.call_count)
        self.sm.reader.read_detail_screen.assert_any_call(self.rotation_frames[0])
        self.assertFalse(any(
            read.args[0] is self.rotation_animation_frames[0]
            for read in self.sm.reader.read_detail_screen.call_args_list
        ))
        self.sm._read_cp_from_powerup_preview.assert_not_called()

    def test_rotation_uses_exact_ocr_pass_when_initial_cp_read_is_impossible(self):
        self._prepare([561] * 4, rotation_cps=[561])
        rotation_frame = self.rotation_frames[0]
        self.sm.reader.read_cp.side_effect = lambda image, **_kwargs: (
            (5561, 0.68) if image is rotation_frame else (-1, 0.0)
        )

        decision, _frame = self._recover()

        self.assertTrue(decision.accepted)
        self.assertEqual(5561, decision.snapshot.cp)
        self.sm.reader.read_cp.assert_any_call(
            rotation_frame, expected_cps={5561, 5594},
        )
        self.assertEqual(4, len(self._model_taps()))
        self.assertEqual(1, self.adb.swipe.call_count)

    def test_changed_identity_after_rotation_prevents_using_exposed_cp(self):
        self._prepare([561] * 4, rotation_cps=[5561])
        self.rotation_frames[0].info["snapshot"] = _snapshot(cp=5561, hp=174)

        with self.assertRaises(RuntimeError):
            self._recover()

        self.assertEqual(4, len(self._model_taps()))
        self.assertEqual(1, self.adb.swipe.call_count)
        self.sm._reopen_appraisal.assert_not_called()
        self.db.insert_pokemon.assert_not_called()

    def test_lost_detail_after_rotation_stops_before_any_post_rotation_tap(self):
        self._prepare([561] * 4, rotation_cps=[561])
        self.rotation_frames[0].info["screen"] = "game_map"

        with self.assertRaisesRegex(RuntimeError, "lost the detail"):
            self._recover()

        self.assertEqual(4, len(self._model_taps()))
        self.assertEqual(1, self.adb.swipe.call_count)
        self.assertEqual(7, self.sm._fast_screencap.call_count)
        self.sm._reopen_appraisal.assert_not_called()

    def test_abort_during_rotated_identity_read_prevents_followup_tap(self):
        self._prepare([561] * 4, rotation_cps=[561])
        rotated = self.rotation_frames[0]
        def hp(image):
            if image is rotated:
                self.sm._abort = True
            return image.info["snapshot"].hp
        self.sm.reader.read_hp.side_effect = hp

        decision, result_frame = self._recover()

        self.assertIsNone(decision)
        self.assertIs(rotated, result_frame)
        self.assertEqual(4, len(self._model_taps()))
        self.assertEqual(1, self.adb.swipe.call_count)
        self.sm._reopen_appraisal.assert_not_called()

    def test_abort_during_rotated_animation_delay_prevents_capture_or_next_rotation(self):
        self._prepare([561] * 4, rotation_cps=[561], rotation_animation_cps=[5561])
        def abort_after_new_tap(_base, _jitter):
            if len(self._model_taps()) > 4:
                self.sm._abort = True
        self.delay.side_effect = abort_after_new_tap

        decision, result_frame = self._recover()

        self.assertIsNone(decision)
        self.assertIs(self.rotation_frames[0], result_frame)
        self.assertEqual(5, len(self._model_taps()))
        self.assertEqual(1, self.adb.swipe.call_count)
        self.assertEqual(7, self.sm._fast_screencap.call_count)
        self.sm._reopen_appraisal.assert_not_called()
        self.sm._read_cp_from_powerup_preview.assert_not_called()

    def test_abort_during_rotation_prevents_any_more_capture_or_navigation(self):
        self._prepare([561] * 4, rotation_cps=[5561])

        def abort_after_rotation(_base, _jitter):
            if self.adb.swipe.call_count:
                self.sm._abort = True

        self.delay.side_effect = abort_after_rotation

        decision, _frame = self._recover()

        self.assertIsNone(decision)
        self.assertEqual(4, len(self._model_taps()))
        self.assertEqual(1, self.adb.swipe.call_count)
        self.assertEqual(6, self.sm._fast_screencap.call_count)
        self.sm._reopen_appraisal.assert_not_called()
        self.db.insert_pokemon.assert_not_called()

    def test_unobserved_transition_confirmation_preserves_recovered_cp(self):
        self._prepare([5561])
        confirmation = _frame("appraisal", self.original)
        self.sm._fast_screencap.side_effect = [
            self.fresh, self.detail, *self.attempt_frames, confirmation,
        ]
        self.sm._wait_for_stable_appraisal.side_effect = [
            (self.initial, "stable_transition_unobserved"),
            (self.final, "stable"),
        ]
        previous = validate_snapshot(_snapshot(cp=5594), False).snapshot
        self.sm._last_validated_identity_key = previous.identity_key

        decision, frame, status, _reason = self.sm._acquire_validated_snapshot(
            previous_accepted=_frame("appraisal", previous),
            require_transition=True,
        )

        self.assertTrue(decision.accepted)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertEqual("screen_after_animation", decision.cp_source)
        self.assertIs(frame, confirmation)
        self.assertEqual("ok", status)

    def test_changed_transition_confirmation_rejects_recovered_cp(self):
        for changes in ({"cp": 5594}, {"sta": 15}):
            with self.subTest(changes=changes):
                self._prepare([5561])
                confirmation = _frame(
                    "appraisal", replace(self.original, **changes)
                )
                self.sm._fast_screencap.side_effect = [
                    self.fresh, self.detail, *self.attempt_frames, confirmation,
                ]
                self.sm._wait_for_stable_appraisal.side_effect = [
                    (self.initial, "stable_transition_unobserved"),
                    (self.final, "stable"),
                ]
                previous = validate_snapshot(_snapshot(cp=5594), False).snapshot
                self.sm._last_validated_identity_key = previous.identity_key

                decision, frame, status, _reason = (
                    self.sm._acquire_validated_snapshot(
                        previous_accepted=_frame("appraisal", previous),
                        require_transition=True,
                    )
                )

                self.assertIsNone(decision)
                self.assertIs(frame, confirmation)
                self.assertEqual("transition_identity_inconsistent", status)
                self.db.insert_pokemon.assert_not_called()

    def test_fresh_valid_cp_avoids_unnecessary_close_and_model_taps(self):
        self._prepare([5561])
        self.fresh.info["snapshot"] = _snapshot(cp=5561)

        decision, frame = self._recover()

        self.assertTrue(decision.accepted)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertIs(frame, self.fresh)
        self.adb.tap.assert_not_called()
        self.sm._reopen_appraisal.assert_not_called()

    def test_changed_fresh_appraisal_stops_before_any_navigation(self):
        self._prepare([5561])
        self.fresh.info["snapshot"] = _snapshot(sta=15)

        with self.assertRaises(RuntimeError):
            self._recover()

        self.adb.tap.assert_not_called()
        self.sm._reopen_appraisal.assert_not_called()

    def test_abort_during_animation_delay_prevents_capture_and_reopen(self):
        self._prepare([5561])

        def abort_on_model_delay(base, jitter):
            if (base, jitter) == (0.2, 1.0):
                self.sm._abort = True

        self.delay.side_effect = abort_on_model_delay

        decision, _frame = self._recover()

        self.assertIsNone(decision)
        self.assertEqual(1, len(self._model_taps()))
        self.assertEqual(2, self.sm._fast_screencap.call_count)
        self.sm._reopen_appraisal.assert_not_called()
        self.db.insert_pokemon.assert_not_called()

    def test_changed_detail_name_or_hp_stops_before_tapping_model(self):
        for changes in ({"display_name": "Other Pokemon"}, {"hp": 174}):
            with self.subTest(changes=changes):
                self.adb.tap.reset_mock()
                self.sm._reopen_appraisal.reset_mock()
                self._prepare([5561], detail=replace(self.original, **changes))

                with self.assertRaises(RuntimeError):
                    self._recover()

                self.assertEqual([], self._model_taps())
                self.sm._reopen_appraisal.assert_not_called()

    def test_changed_final_appraisal_identity_cannot_borrow_observed_cp(self):
        for changes in (
            {"display_name": "Other Pokemon"},
            {"caught_species": "Zamazenta"},
            {"hp": 174},
            {"atk": 14},
            {"def_": 14},
            {"sta": 15},
            {"read_complete": False},
        ):
            with self.subTest(changes=changes):
                self._prepare([5561], final=replace(self.original, **changes))

                with self.assertRaises(RuntimeError):
                    self._recover()

                self.db.insert_pokemon.assert_not_called()

    def test_conflicting_exact_final_cp_is_not_overwritten_by_animation(self):
        final = _snapshot(cp=5594)
        self.assertTrue(validate_snapshot(final, False).accepted)
        self._prepare([5561], final=final)

        with self.assertRaises(RuntimeError):
            self._recover()

        self.db.insert_pokemon.assert_not_called()

    def test_lost_detail_screen_prevents_model_tap(self):
        self._prepare([5561])
        self.detail.info["screen"] = "storage"

        with self.assertRaises(RuntimeError):
            self._recover()

        self.assertEqual([], self._model_taps())
        self.sm._reopen_appraisal.assert_not_called()

    def test_acquisition_reports_recovery_failure_without_retrying_navigation(self):
        self._prepare([5561], detail=_snapshot(display_name="Other Pokemon"))

        decision, _frame, status, reason = self.sm._acquire_validated_snapshot()

        self.assertIsNone(decision)
        self.assertEqual("cp_recovery_failed", status)
        self.assertIn("identity changed", reason)
        self.assertEqual(1, self.adb.tap.call_count)
        self.sm._reopen_appraisal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
