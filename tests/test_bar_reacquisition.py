"""An IV conflict before input requires a new complete, independent pair."""

# TRACEWEAVER: file-role=bar-reacquisition-tests; req=REQ-SCAN-001,REQ-SCAN-003; trace=TRACE-SCAN-001,TRACE-SCAN-003; verifies=VER-SCAN-001
from dataclasses import replace
import unittest
from unittest.mock import Mock

from pokemgr.indexer.snapshot import SnapshotDecision, validate_snapshot
from pokemgr.indexer.state_machine import _PreInputIVConflict
from tests import test_settled_pair_reuse as fixture_module


class BarReacquisitionTests(unittest.TestCase):
    def setUp(self):
        fixture = fixture_module.SettledPairReuseTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.sm, self.adb = fixture.sm, fixture.adb
        self.sm._save_failed_appraisal = Mock()
        self.time = 1.0
        self.original = fixture_module.snapshot()
        self.changed = replace(self.original, atk=self.original.atk + 1)
        self.initial = self.frame(self.original)
        self.conflict = self.frame(self.changed)
        self.sm._fast_screencap = Mock(return_value=self.conflict)
        self.sm._read_cp_from_powerup_preview = Mock(side_effect=AssertionError("No powerup preview"))
        self.sm._reopen_appraisal = Mock(side_effect=AssertionError("No appraisal navigation"))

    def frame(self, read=None, **kwargs):
        image = fixture_module.frame(read or self.changed, **kwargs)
        image.info.update(pokemgr_capture_started_at=self.time,
                          pokemgr_capture_finished_at=self.time + 0.1)
        self.time += 1.0
        return image

    def configure(self, pairs, *, initial_status="stable"):
        waits = iter([(self.initial, None, initial_status), *[
            (pair[-1], pair, "stable") for pair in pairs
        ]])

        def wait(**_kwargs):
            image, pair, status = next(waits)
            self.sm._settled_frame_pair = pair
            return image, status

        self.sm._wait_for_stable_appraisal = Mock(side_effect=wait)

    def assert_no_mutations(self):
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()
        self.sm.db.insert_pokemon.assert_not_called()
        self.sm._read_cp_from_powerup_preview.assert_not_called()
        self.sm._reopen_appraisal.assert_not_called()
        self.assertEqual((0, 0, 0), (self.sm.count, self.sm.visited_count, self.sm.skipped_count))

    def test_two_new_matching_frames_replace_the_whole_conflicting_read(self):
        older, newest = self.frame(), self.frame()
        self.configure([(older, newest)])
        reference = self.frame(self.original)

        decision, image, status, _reason = self.sm._acquire_validated_snapshot(
            previous_accepted=reference, require_transition=True,
        )

        self.assertEqual("ok", status)
        self.assertEqual(validate_snapshot(self.changed, True).snapshot.identity_key,
                         decision.snapshot.identity_key)
        self.assertIs(newest, image)
        self.assertEqual([self.initial, self.conflict, newest, older],
                         [call.args[0] for call in self.sm._read_appraisal_snapshot.call_args_list])
        self.assertEqual([True, True], [call.kwargs["require_transition"]
                                      for call in self.sm._wait_for_stable_appraisal.call_args_list])
        self.assertTrue(all(call.kwargs["previous_accepted"] is reference
                            for call in self.sm._wait_for_stable_appraisal.call_args_list))
        self.assert_no_mutations()

    def test_retry_can_confirm_original_values_without_reusing_original_frames(self):
        older, newest = self.frame(self.original), self.frame(self.original)
        self.configure([(older, newest)])
        decision, image, status, _reason = self.sm._acquire_validated_snapshot()
        self.assertEqual("ok", status)
        self.assertEqual(self.original.ivs, decision.snapshot.ivs)
        self.assertIs(newest, image)
        self.assert_no_mutations()

    def test_non_iv_changes_and_incomplete_reads_are_not_typed_retries(self):
        changes = ({"hp": 175}, {"caught_species": "Other"}, {"display_name": "Other"},
                   {"gender": "male"}, {"shiny": True}, {"favorited": False},
                   {"cp": 123}, {"read_complete": False}, {"atk": -1})
        for change in changes:
            with self.subTest(change=change):
                self.sm._fast_screencap.return_value = self.frame(replace(self.changed, **change))
                with self.assertRaises(RuntimeError) as raised:
                    self.sm._recover_cp_with_model_taps(self.original, self.initial)
                self.assertNotIsInstance(raised.exception, _PreInputIVConflict)
                self.assert_no_mutations()

    def test_unobserved_position_does_not_gain_retry_or_skip_authority(self):
        for status in ("stable_transition_unobserved", "stable_raw_identity_unchanged"):
            with self.subTest(status=status):
                self.configure([], initial_status=status)
                decision, _image, failure, _reason = self.sm._acquire_validated_snapshot(
                    previous_accepted=self.frame(), require_transition=True,
                )
                self.assertIsNone(decision)
                self.assertEqual("cp_recovery_failed", failure)
                self.sm._wait_for_stable_appraisal.assert_called_once()
                self.assert_no_mutations()

    def test_repeated_pair_disagreement_stops_after_three_attempts(self):
        self.configure([(self.frame(self.original), self.frame()),
                        (self.frame(self.original), self.frame())])
        decision, _image, status, reason = self.sm._acquire_validated_snapshot()
        self.assertIsNone(decision)
        self.assertEqual("cp_recovery_failed", status)
        self.assertIn("disagree", reason)
        self.assertEqual(3, self.sm._wait_for_stable_appraisal.call_count)
        self.sm._fast_screencap.assert_called_once()
        self.assert_no_mutations()

    def test_discarded_or_duplicated_frames_cannot_supply_a_new_pair(self):
        for old in (self.initial, self.conflict):
            with self.subTest(old=old.info["snapshot"].atk):
                self.configure([(old, self.frame())])
                result = self.sm._acquire_validated_snapshot()
                self.assertEqual("cp_recovery_failed", result[2])
                self.assert_no_mutations()
        duplicate = self.frame()
        self.configure([(duplicate, duplicate)])
        self.assertEqual("cp_recovery_failed", self.sm._acquire_validated_snapshot()[2])

    def test_missing_backwards_overlapping_or_copied_legacy_times_are_rejected(self):
        for mode in ("missing", "backwards", "overlap", "copied", "nan"):
            with self.subTest(mode=mode):
                older, newest = self.frame(), self.frame()
                if mode == "missing":
                    older.info.pop("pokemgr_capture_started_at")
                elif mode == "backwards":
                    older.info["pokemgr_capture_started_at"] = -1.0
                elif mode == "overlap":
                    newest.info["pokemgr_capture_started_at"] = older.info["pokemgr_capture_finished_at"]
                elif mode == "copied":
                    older.info.update({key: self.conflict.info[key] for key in
                                       ("pokemgr_capture_started_at", "pokemgr_capture_finished_at")})
                else:
                    older.info["pokemgr_capture_finished_at"] = float("nan")
                self.configure([(older, newest)])
                self.assertEqual("cp_recovery_failed", self.sm._acquire_validated_snapshot()[2])
                self.assert_no_mutations()

    def test_duplicate_pts_changed_session_and_clock_generation_are_rejected(self):
        for mode in ("pts", "session", "generation", "missing"):
            with self.subTest(mode=mode):
                older, newest = self.frame(), self.frame()
                for sequence, image in enumerate((self.conflict, older, newest), 1):
                    image.info.update(pokemgr_stream_session="session", pokemgr_stream_sequence=sequence,
                                      pokemgr_stream_pts_us=sequence * 1000,
                                      pokemgr_source_clock_generation=1)
                if mode == "pts":
                    newest.info["pokemgr_stream_pts_us"] = older.info["pokemgr_stream_pts_us"]
                elif mode == "session":
                    newest.info["pokemgr_stream_session"] = "other"
                elif mode == "generation":
                    newest.info["pokemgr_source_clock_generation"] = 2
                else:
                    older.info.pop("pokemgr_stream_pts_us")
                self.configure([(older, newest)])
                self.assertEqual("cp_recovery_failed", self.sm._acquire_validated_snapshot()[2])
                self.assert_no_mutations()

    def test_new_pair_non_iv_identity_or_screen_change_stops(self):
        for change in ({"hp": 175}, {"gender": "male"}, {"read_complete": False}):
            with self.subTest(change=change):
                self.configure([(self.frame(replace(self.changed, **change)), self.frame())])
                self.assertEqual("cp_recovery_failed", self.sm._acquire_validated_snapshot()[2])
                self.assert_no_mutations()
        self.configure([(self.frame(screen="detail"), self.frame())])
        self.assertEqual("cp_recovery_failed", self.sm._acquire_validated_snapshot()[2])

    def test_pause_or_abort_during_replacement_read_cannot_accept_or_recover(self):
        for abort in (False, True):
            with self.subTest(abort=abort):
                self.sm._abort = self.sm._paused = False
                older, newest = self.frame(), self.frame()
                self.configure([(older, newest)])
                read = fixture_module.SettledPairReuseTests.read_snapshot

                def interrupt(image):
                    if image is older:
                        if abort:
                            self.sm.abort()
                        else:
                            self.sm.pause()
                            self.sm.resume()
                    return read(image)

                self.sm._read_appraisal_snapshot.side_effect = interrupt
                result = self.sm._acquire_validated_snapshot()
                self.assertEqual("aborted" if abort else "reacquire", result[2])
                self.assertIsNone(result[0])
                self.assert_no_mutations()

    def test_recovery_budget_is_reset_only_for_zero_input_iv_conflict(self):
        older, newest = self.frame(), self.frame()
        self.configure([(older, newest)])
        real_recover = self.sm._recover_cp_with_model_taps
        real_validate = self.sm._validate_appraisal_snapshot
        expected = validate_snapshot(self.changed, True)

        def validate(read, image):
            return SnapshotDecision(False, "CP unresolved") if image in (older, newest) else real_validate(read, image)

        def recover(read, image, **kwargs):
            if image is self.initial:
                return real_recover(read, image, **kwargs)
            self.assertEqual(self.changed.ivs, read.ivs)
            return expected, image

        self.sm._validate_appraisal_snapshot = Mock(side_effect=validate)
        self.sm._recover_cp_with_model_taps = Mock(side_effect=recover)
        decision, _image, status, _reason = self.sm._acquire_validated_snapshot()
        self.assertEqual("ok", status)
        self.assertEqual(expected.snapshot.identity_key, decision.snapshot.identity_key)
        self.assertEqual(2, self.sm._recover_cp_with_model_taps.call_count)
        self.assert_no_mutations()

    def test_generic_post_input_failure_is_never_caught_as_reacquisition(self):
        self.configure([])

        def failure(*_args, **_kwargs):
            self.adb.tap(1, 2)
            raise RuntimeError("post-input identity changed")

        self.sm._recover_cp_with_model_taps = Mock(side_effect=failure)
        self.assertEqual("cp_recovery_failed", self.sm._acquire_validated_snapshot()[2])
        self.sm._wait_for_stable_appraisal.assert_called_once()
        self.adb.tap.assert_called_once()
        self.sm.db.insert_pokemon.assert_not_called()


if __name__ == "__main__":
    unittest.main()
