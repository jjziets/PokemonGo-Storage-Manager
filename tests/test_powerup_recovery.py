"""Preview CP is evidence only after cancel and complete identity verification."""

from dataclasses import replace
import unittest
from unittest.mock import Mock, call, patch

from PIL import Image

from pokemgr.calibration.profile import CalibrationProfile
from pokemgr.calibration.regions import ScreenRegions
from pokemgr.indexer.snapshot import AppraisalSnapshot, validate_snapshot
from pokemgr.indexer.state_machine import IndexingStateMachine
from pokemgr.reader.powerup import PowerUpPreview


SPECIES = {
    species_id: {
        "name": name, "base_atk": 174, "base_def": 197, "base_sta": 163,
    }
    for species_id, name in (
        ("weezing", "Weezing"), ("weezing_galarian", "Weezing (Galarian)"),
    )
}
OPEN = (242, 1750)
CANCEL = (484, 2170)


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


def frame(read, screen="detail"):
    image = Image.new("RGB", (968, 2376), "white")
    image.info.update(snapshot=read, screen=screen)
    return image


class PowerupRecoveryTests(unittest.TestCase):
    def setUp(self):
        for target, kwargs, attribute in (
            ("pokemgr.pvp.resolver._default_species_map", {"return_value": SPECIES}, None),
            ("pokemgr.indexer.state_machine.human_delay", {}, "delay"),
            ("pokemgr.reader.powerup.find_powerup_button", {"return_value": OPEN}, "find_button"),
            ("pokemgr.reader.powerup.has_detail_menu", {"return_value": True}, "has_menu"),
            ("pokemgr.reader.powerup.read_powerup_preview", {}, "read_preview"),
            ("pokemgr.reader.powerup.read_powerup_resource_prompt", {"return_value": None}, "read_resource"),
        ):
            patcher = patch(target, **kwargs)
            mocked = patcher.start()
            self.addCleanup(patcher.stop)
            if attribute:
                setattr(self, attribute, mocked)
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
        self.sm.reader.read_detail_screen = Mock(
            side_effect=lambda image: image.info["snapshot"].as_detail(),
        )
        self.sm.reader.read_hp = Mock(side_effect=lambda image: image.info["snapshot"].hp)
        self.sm.reader.read_cp = Mock(return_value=(-1, 0.0))
        self.sm.reader.are_bars_visible = Mock(return_value=True)
        self.sm._read_appraisal_snapshot = Mock(side_effect=lambda image: (
            image.info["snapshot"].as_detail() | {
                "snapshot_read_complete": image.info["snapshot"].read_complete,
            },
            image.info["snapshot"].as_appraisal(),
        ))
        self.sm._reopen_appraisal = Mock(return_value=True)
        self.sm._save_failed_appraisal = Mock()
        self.original = snapshot()
        self.initial = frame(self.original)
        self.fresh = frame(self.original)
        self.preview_frame = frame(self.original, "powerup")
        self.restored = frame(self.original)
        self.sm._fast_screencap = Mock(
            side_effect=[self.fresh, self.preview_frame, self.restored],
        )
        self.read_preview.return_value = PowerUpPreview(2178, 2194, CANCEL)
        self.read_preview.side_effect = lambda image, _names: (
            self.read_preview.return_value if image is self.preview_frame else None
        )

    def recover(self):
        return self.sm._read_cp_from_powerup_preview(self.original, self.initial)

    def assert_only_open_and_cancel(self):
        self.assertEqual(
            [call(*OPEN, jitter=0), call(*CANCEL, jitter=0)],
            self.adb.tap.call_args_list,
        )
        self.adb.swipe.assert_not_called()
        self.db.insert_pokemon.assert_not_called()

    def test_exact_current_cp_is_read_once_cancelled_and_rechecked(self):
        self.assertFalse(validate_snapshot(self.original, True).accepted)
        self.assertTrue(validate_snapshot(replace(self.original, cp=2178), False).accepted)

        cp, result_frame = self.recover()

        self.assertEqual(2178, cp)
        self.assertIs(self.restored, result_frame)
        self.assert_only_open_and_cancel()
        self.assertEqual(3, self.sm._fast_screencap.call_count)
        self.read_preview.assert_called_once_with(
            self.preview_frame, ("Weezing", "Weezing"),
        )
        self.find_button.assert_called_once_with(self.fresh)
        self.has_menu.assert_called_once_with(self.restored, self.sm.regions.menu_button)
        self.assertEqual(
            [call(self.fresh), call(self.restored)], self.sm.reader.read_hp.call_args_list,
        )
        self.sm._reopen_appraisal.assert_not_called()

    def test_passed_frame_never_authorizes_opening_on_a_changed_fresh_detail(self):
        self.fresh.info["snapshot"] = snapshot(display_name="Another Pokemon")

        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            self.recover()

        self.adb.tap.assert_not_called()
        self.find_button.assert_not_called()

    def test_wrong_initial_screen_or_hp_stops_before_any_input(self):
        for changes in ({"screen": "appraisal"}, {"snapshot": snapshot(hp=138)}):
            with self.subTest(changes=changes):
                fresh = frame(self.original)
                fresh.info.update(changes)
                self.sm._fast_screencap.side_effect = [fresh]
                with self.assertRaises(RuntimeError):
                    self.recover()
                self.adb.tap.assert_not_called()
                self.find_button.assert_not_called()

    def test_missing_recognized_opener_returns_without_navigation(self):
        self.find_button.return_value = None

        cp, result_frame = self.recover()

        self.assertIsNone(cp)
        self.assertIs(self.fresh, result_frame)
        self.adb.tap.assert_not_called()
        self.read_preview.assert_called_once_with(self.fresh, ("Weezing", "Weezing"))
        self.assertEqual(1, self.sm._fast_screencap.call_count)
        self.has_menu.assert_called_once_with(self.fresh, self.sm.regions.menu_button)

    def test_missing_opener_and_unrecognized_detail_menu_stops_without_navigation(self):
        self.find_button.return_value = None
        self.has_menu.return_value = False

        with self.assertRaises(RuntimeError):
            self.recover()

        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()
        self.sm._reopen_appraisal.assert_not_called()
        self.assertEqual(1, self.sm._fast_screencap.call_count)

    def test_already_open_recognized_preview_stops_without_generic_navigation(self):
        self.find_button.return_value = None
        self.read_preview.side_effect = None

        with self.assertRaisesRegex(RuntimeError, "already open"):
            self.recover()

        self.adb.tap.assert_not_called()
        self.sm._reopen_appraisal.assert_not_called()
        self.assertEqual(1, self.sm._fast_screencap.call_count)

    def test_unrecognized_preview_stops_without_guessing_cancel_or_confirm(self):
        self.read_preview.return_value = None
        unknown_frames = [frame(self.original, "powerup") for _ in range(3)]
        self.sm._fast_screencap.side_effect = [self.fresh, *unknown_frames]

        with self.assertRaisesRegex(RuntimeError, "not recognized"):
            self.recover()

        self.assertEqual([call(*OPEN, jitter=0)], self.adb.tap.call_args_list)
        self.assertEqual(4, self.sm._fast_screencap.call_count)
        self.assertEqual(3, self.read_preview.call_count)
        self.assertEqual(3, self.read_resource.call_count)
        self.sm._reopen_appraisal.assert_not_called()
        self.sm._save_failed_appraisal.assert_called_once()
        self.assertIs(unknown_frames[-1], self.sm._save_failed_appraisal.call_args.args[0])
        self.assertEqual("cp_preview", self.sm._save_failed_appraisal.call_args.kwargs["phase"])
        self.assertEqual(2, self.delay.call_args_list.count(call(0.2, 1.0)))

    def test_temporarily_unrecognized_preview_is_read_again_without_reopening(self):
        transitional = frame(self.original, "powerup")
        self.sm._fast_screencap.side_effect = [
            self.fresh, transitional, self.preview_frame, self.restored,
        ]

        cp, result_frame = self.recover()

        self.assertEqual(2178, cp)
        self.assertIs(self.restored, result_frame)
        self.assert_only_open_and_cancel()
        self.assertEqual(2, self.read_preview.call_count)
        self.assertEqual(4, self.sm._fast_screencap.call_count)
        self.sm._save_failed_appraisal.assert_not_called()
        self.assertIn(call(0.2, 1.0), self.delay.call_args_list)

    def test_abort_during_recognition_retry_wait_stops_before_another_capture(self):
        self.read_preview.return_value = None
        def abort_before_retry(*_args):
            if self.read_preview.call_count:
                self.sm._abort = True
        self.delay.side_effect = abort_before_retry

        cp, result_frame = self.recover()

        self.assertIsNone(cp)
        self.assertIs(self.preview_frame, result_frame)
        self.assertEqual([call(*OPEN, jitter=0)], self.adb.tap.call_args_list)
        self.assertEqual(2, self.sm._fast_screencap.call_count)
        self.assertEqual(1, self.read_preview.call_count)
        self.sm._save_failed_appraisal.assert_not_called()

    def prepare_resource_prompt(self):
        self.read_preview.return_value = None
        self.read_resource.side_effect = lambda image, _names: (
            CANCEL if image is self.preview_frame else None
        )

    def test_resource_prompt_is_cancelled_once_and_returns_no_cp(self):
        self.prepare_resource_prompt()

        cp, result_frame = self.recover()

        self.assertIsNone(cp)
        self.assertIs(self.restored, result_frame)
        self.assert_only_open_and_cancel()
        self.read_resource.assert_called_once_with(self.preview_frame, ("Weezing", "Weezing"))
        self.assertEqual([call(self.fresh), call(self.restored)], self.sm.reader.read_hp.call_args_list)
        self.sm._save_failed_appraisal.assert_not_called()

    def test_resource_prompt_after_unreadable_frame_does_not_open_again(self):
        self.prepare_resource_prompt()
        self.sm._fast_screencap.side_effect = [
            self.fresh, frame(self.original, "powerup"), self.preview_frame, self.restored,
        ]

        cp, result_frame = self.recover()

        self.assertIsNone(cp)
        self.assertIs(self.restored, result_frame)
        self.assert_only_open_and_cancel()
        self.assertEqual(2, self.read_resource.call_count)

    def test_already_open_resource_prompt_stops_without_generic_navigation(self):
        self.read_preview.return_value = None
        self.read_resource.return_value = CANCEL
        self.find_button.return_value = None

        with self.assertRaisesRegex(RuntimeError, "already open"):
            self.recover()

        self.adb.tap.assert_not_called()
        self.assertEqual(1, self.sm._fast_screencap.call_count)
        self.sm._reopen_appraisal.assert_not_called()

    def test_resource_prompt_missed_cancel_stops_without_more_inputs(self):
        self.prepare_resource_prompt()
        self.has_menu.return_value = False

        with self.assertRaisesRegex(RuntimeError, "did not close"):
            self.recover()

        self.assert_only_open_and_cancel()
        self.sm._reopen_appraisal.assert_not_called()

    def test_resource_prompt_cancel_requires_same_detail_name_and_hp(self):
        self.prepare_resource_prompt()
        for changes in ({"display_name": "Other"}, {"hp": 138}):
            with self.subTest(changes=changes):
                self.restored.info["snapshot"] = snapshot(**changes)
                self.sm._fast_screencap.side_effect = [self.fresh, self.preview_frame, self.restored]
                self.adb.tap.reset_mock()
                with self.assertRaisesRegex(RuntimeError, "identity changed"):
                    self.recover()
                self.assert_only_open_and_cancel()

    def test_abort_during_resource_prompt_read_prevents_cancel_and_capture(self):
        self.read_preview.return_value = None
        def abort(*_args):
            self.sm._abort = True
            return CANCEL
        self.read_resource.side_effect = abort

        cp, result_frame = self.recover()

        self.assertIsNone(cp)
        self.assertIs(self.preview_frame, result_frame)
        self.assertEqual([call(*OPEN, jitter=0)], self.adb.tap.call_args_list)
        self.assertEqual(2, self.sm._fast_screencap.call_count)
        self.sm._reopen_appraisal.assert_not_called()

    def test_outer_resource_prompt_restores_appraisal_and_keeps_ambiguous_cp_rejected(self):
        self.prepare_resource_prompt()
        initial = self.prepare_outer_recovery()

        decision, result_frame = self.sm._recover_cp_with_model_taps(self.original, initial)

        self.assertFalse(decision.accepted)
        self.assertIs(self.final, result_frame)
        self.sm._reopen_appraisal.assert_called_once()
        self.sm._read_appraisal_snapshot.assert_any_call(self.final)
        self.assertEqual(11, self.adb.tap.call_count)
        self.db.insert_pokemon.assert_not_called()

    def test_unreadable_current_cp_is_cancelled_without_using_future_cp(self):
        self.read_preview.return_value = PowerUpPreview(None, 2178, CANCEL)

        cp, result_frame = self.recover()

        self.assertIsNone(cp)
        self.assertIs(self.restored, result_frame)
        self.assert_only_open_and_cancel()

    def test_current_cp_must_fit_exact_hp_ivs_even_when_future_cp_does(self):
        self.read_preview.return_value = PowerUpPreview(9999, 2178, CANCEL)

        cp, result_frame = self.recover()

        self.assertIsNone(cp)
        self.assertIs(self.restored, result_frame)
        self.assert_only_open_and_cancel()

    def test_changed_detail_after_cancel_rejects_observed_cp(self):
        for changes in ({"display_name": "Other"}, {"hp": 138}):
            with self.subTest(changes=changes):
                self.restored.info["snapshot"] = snapshot(**changes)
                self.sm._fast_screencap.side_effect = [self.fresh, self.preview_frame, self.restored]
                self.adb.tap.reset_mock()
                with self.assertRaisesRegex(RuntimeError, "identity changed"):
                    self.recover()
                self.assert_only_open_and_cancel()

    def test_missed_cancel_requires_affirmative_detail_button_before_accepting_cp(self):
        # The underlying name and HP remain readable through an open modal.
        self.has_menu.return_value = False

        with self.assertRaisesRegex(RuntimeError, "did not close"):
            self.recover()

        self.assert_only_open_and_cancel()

    def test_post_cancel_menu_proves_detail_even_when_powerup_opener_is_unavailable(self):
        self.find_button.side_effect = [OPEN, None]

        cp, result_frame = self.recover()

        self.assertEqual(2178, cp)
        self.assertIs(self.restored, result_frame)
        self.assert_only_open_and_cancel()
        self.find_button.assert_called_once_with(self.fresh)
        self.has_menu.assert_called_once_with(self.restored, self.sm.regions.menu_button)

    def test_abort_before_recovery_performs_no_reads_or_inputs(self):
        self.sm._abort = True

        cp, result_frame = self.recover()

        self.assertIsNone(cp)
        self.assertIs(self.initial, result_frame)
        self.sm._fast_screencap.assert_not_called()
        self.adb.tap.assert_not_called()
        self.find_button.assert_not_called()

    def test_incomplete_original_evidence_cannot_open_preview(self):
        self.original = snapshot(caught_species="")

        cp, _result_frame = self.recover()

        self.assertIsNone(cp)
        self.sm._fast_screencap.assert_not_called()
        self.adb.tap.assert_not_called()

    def test_abort_during_opening_stops_before_preview_capture(self):
        def abort(*_args, **_kwargs):
            self.sm._abort = True
        self.adb.tap.side_effect = abort

        cp, result_frame = self.recover()

        self.assertIsNone(cp)
        self.assertIs(self.fresh, result_frame)
        self.assertEqual([call(*OPEN, jitter=0)], self.adb.tap.call_args_list)
        self.assertEqual(1, self.sm._fast_screencap.call_count)
        self.read_preview.assert_not_called()

    def test_abort_during_preview_read_leaves_modal_without_any_more_input(self):
        def abort(*_args):
            self.sm._abort = True
            return PowerUpPreview(2178, 2194, CANCEL)
        self.read_preview.side_effect = abort

        cp, result_frame = self.recover()

        self.assertIsNone(cp)
        self.assertIs(self.preview_frame, result_frame)
        self.assertEqual([call(*OPEN, jitter=0)], self.adb.tap.call_args_list)
        self.assertEqual(2, self.sm._fast_screencap.call_count)
        self.sm._reopen_appraisal.assert_not_called()

    def test_abort_during_cancel_stops_before_detail_capture(self):
        def abort_on_cancel(*target, **_kwargs):
            if target == CANCEL:
                self.sm._abort = True
        self.adb.tap.side_effect = abort_on_cancel

        cp, result_frame = self.recover()

        self.assertIsNone(cp)
        self.assertIs(self.preview_frame, result_frame)
        self.assert_only_open_and_cancel()
        self.assertEqual(2, self.sm._fast_screencap.call_count)

    def prepare_outer_recovery(self, final=None):
        initial = frame(self.original, "appraisal")
        self.final = frame(final or self.original, "appraisal")
        # One post-close frame, four initial tap frames, and four pairs of
        # rotation/post-tap frames must fail before the preview opens.
        detail_frames = [frame(self.original) for _ in range(1 + 4 + 4 * 2)]
        self.sm._fast_screencap.side_effect = [
            initial, *detail_frames, self.fresh, self.preview_frame, self.restored,
        ]
        self.sm._wait_for_stable_appraisal = Mock(return_value=(self.final, "stable"))
        return initial

    def test_outer_recovery_rechecks_full_appraisal_after_preview_and_preserves_source(self):
        initial = self.prepare_outer_recovery()

        decision, result_frame = self.sm._recover_cp_with_model_taps(self.original, initial)

        self.assertTrue(decision.accepted)
        self.assertEqual(2178, decision.snapshot.cp)
        self.assertEqual(self.original.ivs, decision.snapshot.ivs)
        self.assertEqual("screen_after_powerup_preview", decision.cp_source)
        self.assertIs(self.final, result_frame)
        self.sm._read_appraisal_snapshot.assert_any_call(self.final)
        self.sm._reopen_appraisal.assert_called_once()
        self.assertEqual(4, self.adb.swipe.call_count)
        self.assertEqual(11, self.adb.tap.call_count)  # Close, eight model taps, open, cancel.
        self.assertEqual(17, self.sm._fast_screencap.call_count)
        self.read_preview.assert_called_once_with(self.preview_frame, ("Weezing", "Weezing"))
        self.db.insert_pokemon.assert_not_called()

    def test_checkpoint_acquisition_suppresses_unknown_dialog_artifacts_but_still_stops(self):
        initial = self.prepare_outer_recovery()
        unknown_frames = [frame(self.original, "powerup") for _ in range(3)]
        self.sm._fast_screencap.side_effect = [
            *list(self.sm._fast_screencap.side_effect)[:-2], *unknown_frames,
        ]
        self.sm._wait_for_stable_appraisal.return_value = (initial, "stable")
        self.read_preview.return_value = None

        decision, _result_frame, status, reason = self.sm._acquire_validated_snapshot(
            save_failure_evidence=False,
        )

        self.assertIsNone(decision)
        self.assertEqual("cp_recovery_failed", status)
        self.assertIn("not recognized after 3 captures", reason)
        self.assertEqual(3, self.read_preview.call_count)
        self.assertEqual(10, self.adb.tap.call_count)  # Close, eight model taps, one opener.
        self.assertEqual(4, self.adb.swipe.call_count)
        self.sm._save_failed_appraisal.assert_not_called()
        self.sm._reopen_appraisal.assert_not_called()
        self.db.insert_pokemon.assert_not_called()

    def test_outer_recovery_rejects_changed_ivs_or_conflicting_exact_final_cp(self):
        for changes in ({"atk": 13}, {"cp": 2161}):
            with self.subTest(changes=changes):
                initial = self.prepare_outer_recovery(snapshot(**changes))
                with self.assertRaises(RuntimeError):
                    self.sm._recover_cp_with_model_taps(self.original, initial)
                self.db.insert_pokemon.assert_not_called()

    def test_post_cancel_exact_cp_conflict_stops_before_reopening_appraisal(self):
        initial = self.prepare_outer_recovery()
        self.assertEqual(-1, self.fresh.info["snapshot"].cp)
        self.restored.info["snapshot"] = snapshot(cp=2161)
        self.assertTrue(validate_snapshot(snapshot(cp=2161), False).accepted)

        with self.assertRaisesRegex(RuntimeError, "post-cancel visible CP"):
            self.sm._recover_cp_with_model_taps(self.original, initial)

        self.sm._reopen_appraisal.assert_not_called()
        self.sm._wait_for_stable_appraisal.assert_not_called()
        self.assertEqual(call(*CANCEL, jitter=0), self.adb.tap.call_args_list[-1])
        self.db.insert_pokemon.assert_not_called()

    def prepare_transition_confirmation(self, confirmed):
        initial = self.prepare_outer_recovery()
        confirmation = frame(confirmed, "appraisal")
        self.sm._fast_screencap.side_effect = [
            *self.sm._fast_screencap.side_effect, confirmation,
        ]
        self.sm._wait_for_stable_appraisal.side_effect = [
            (initial, "stable_transition_unobserved"), (self.final, "stable"),
        ]
        previous = validate_snapshot(snapshot(cp=2161), False).snapshot
        self.sm._last_validated_identity_key = previous.identity_key
        return frame(previous, "appraisal"), confirmation

    def test_unobserved_transition_confirmation_preserves_preview_cp_and_provenance(self):
        previous, confirmation = self.prepare_transition_confirmation(self.original)

        decision, result_frame, status, _reason = self.sm._acquire_validated_snapshot(
            previous_accepted=previous, require_transition=True,
        )

        self.assertEqual("ok", status)
        self.assertIs(confirmation, result_frame)
        self.assertEqual(2178, decision.snapshot.cp)
        self.assertEqual("screen_after_powerup_preview", decision.cp_source)
        self.assertIn("cancelled power-up preview", decision.reason)
        self.read_preview.assert_called_once()

    def test_unobserved_transition_confirmation_rejects_new_cp_or_changed_identity(self):
        for changes in ({"cp": 2161}, {"atk": 13}):
            with self.subTest(changes=changes):
                previous, confirmation = self.prepare_transition_confirmation(snapshot(**changes))

                decision, result_frame, status, _reason = self.sm._acquire_validated_snapshot(
                    previous_accepted=previous, require_transition=True,
                )

                self.assertIsNone(decision)
                self.assertIs(confirmation, result_frame)
                self.assertEqual("transition_identity_inconsistent", status)
                self.db.insert_pokemon.assert_not_called()

    def test_outer_recovery_never_reopens_appraisal_after_preview_abort(self):
        initial = self.prepare_outer_recovery()
        def abort(*_args):
            self.sm._abort = True
            return PowerUpPreview(2178, 2194, CANCEL)
        self.read_preview.side_effect = abort

        decision, result_frame = self.sm._recover_cp_with_model_taps(self.original, initial)

        self.assertIsNone(decision)
        self.assertIs(self.preview_frame, result_frame)
        self.assertEqual(10, self.adb.tap.call_count)
        self.sm._reopen_appraisal.assert_not_called()
        self.sm._wait_for_stable_appraisal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
