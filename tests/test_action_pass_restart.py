# TRACEWEAVER: file-role=bounded-keeper-pass-restart-tests; req=REQ-MASS-001,REQ-SCAN-003,REQ-SCAN-004; trace=TRACE-MASS-001,TRACE-SCAN-003,TRACE-SCAN-004; ver=VER-SCAN-001
"""A full traversal restart preserves changes while rechecking occurrences."""

from collections import Counter
from dataclasses import replace
import unittest
from unittest.mock import Mock, call, patch

from pokemgr.calibration.profile import CalibrationProfile
from pokemgr.calibration.regions import ScreenRegions
from pokemgr.execution.executor import Executor
from pokemgr.indexer.snapshot import SnapshotDecision
from tests.test_mass_action_scanning import frame, pokemon, snapshot
from tests.test_action_preswipe_retry import sourced_frame


class ActionPassRestartTests(unittest.TestCase):
    def setUp(self):
        profile = CalibrationProfile(
            "test", "serial", "968x2376", 420,
            ScreenRegions.default_for_resolution(968, 2376, density=420),
        )
        self.adb, self.db = Mock(), Mock()
        self.adb.has_stream_frames = False
        self.executor = Executor(self.adb, profile, self.db)
        self.scanner = self.executor._scanner
        self.first = snapshot(caught_species="Meditite", detected_species="Meditite",
                              cp=370, hp=69, atk=10, def_=12, sta=15)
        self.second = snapshot(cp=350, hp=68)
        self.stars = {}
        self.tapped = []
        self.current = None
        self.histories = []
        self.query = "!shiny&!shadow&!dynamax&!gigantamax&cp10-370"
        self.executor.on_progress = Mock()
        self.executor.reader.prepare_native_ocr = Mock()
        self.executor._open_pass = Mock(return_value=2)
        self.executor._advance_action = Mock(side_effect=self.advance)
        self.executor._read_identity = Mock(side_effect=lambda image: image.info["read"])
        self.scanner._fast_screencap = Mock(side_effect=self.capture)
        self.scanner._recover_failed_transition = Mock(
            return_value=(None, None, "transition_retry_failed", "distinct checkpoint failed"),
        )
        self.adb.tap.side_effect = self.tap
        self.enterContext(patch("pokemgr.execution.executor.time.sleep"))
        self.enterContext(patch("pokemgr.execution.executor.favorite_state",
                                side_effect=lambda image, _region: image.info["star"]))

    def capture(self):
        read, specimen = self.current
        return frame(read, "on" if self.stars.get(specimen, False) else "off")

    def tap(self, *_args, **_kwargs):
        specimen = self.current[1]
        self.stars[specimen] = not self.stars.get(specimen, False)
        self.tapped.append(specimen)

    def advance(self, *_args):
        self.scanner._last_stable_image = self.capture()
        return True

    def observe(self, *events):
        events = iter(events)

        def acquire(**kwargs):
            self.histories.append((
                kwargs.copy(), self.scanner._previous_validated_identity_key,
                self.scanner._last_validated_identity_key,
                self.scanner._last_accepted_image, self.scanner._settled_frame_pair,
            ))
            event = next(events)
            if isinstance(event, str):
                return None, self.capture(), event, event
            self.current = event
            return (SnapshotDecision(True, "exact", event[0], cp_source="calculated"),
                    self.capture(), "ok", "exact")

        self.scanner._acquire_validated_snapshot = Mock(side_effect=acquire)

    def run_pass(self, counts=None, *, dry_run=False):
        self.pending = Counter(counts or {
            self.executor._snapshot_keeper_key(self.first): 1,
            self.executor._snapshot_keeper_key(self.second): 1,
        })
        self.initial = self.pending.copy()
        return self.executor._run_favorite_pass(self.query, self.pending, dry_run,
                                                 sum(self.pending.values()))

    def assert_reopened_same_query(self):
        self.assertEqual([call(self.query, verify_count=True)] * 2,
                         self.executor._open_pass.call_args_list)
        self.scanner._recover_failed_transition.assert_not_called()
        self.assertEqual([], self.db.mock_calls)

    def use_verified_open(self, counts):
        self.executor._open_pass = Mock(wraps=Executor._open_pass.__get__(self.executor))
        self.executor.nav.navigate_to_storage = Mock(return_value=True)
        self.executor.nav.enter_search = Mock(return_value=True)
        self.executor.nav.read_filtered_count = Mock(return_value=2)
        self.executor.nav.read_filtered_count_verified = Mock(side_effect=counts)
        self.executor.nav.tap_first_pokemon = Mock(return_value=True)
        self.executor.nav.open_first_appraisal = Mock(return_value=True)

    def test_initial_and_restarted_keeper_pass_both_require_independently_verified_count(self):
        self.use_verified_open([2, 2])
        self.observe((self.first, "a"), "transition_returned_to_previous",
                     (self.first, "a"), (self.second, "b"))

        result = self.run_pass()

        self.assertNotIn("error", result)
        self.assertEqual(2, self.executor.nav.read_filtered_count_verified.call_count)
        self.executor.nav.read_filtered_count.assert_not_called()
        self.assertEqual([call(self.query, verify=True)] * 2,
                         self.executor.nav.enter_search.call_args_list)
        self.assertEqual(2, self.executor.nav.tap_first_pokemon.call_count)
        self.assertEqual(["a", "b"], self.tapped)
        self.assert_reopened_same_query()

    def test_uncertain_initial_verified_count_never_opens_a_tile(self):
        self.use_verified_open([None])
        self.observe()

        result = self.run_pass()

        self.assertIn("not independently confirmed", result["error"])
        self.assertEqual((0, 0), (result["checked"], result["favorited"]))
        self.executor.nav.read_filtered_count.assert_not_called()
        self.executor.nav.tap_first_pokemon.assert_not_called()
        self.scanner._acquire_validated_snapshot.assert_not_called()
        self.assertEqual(self.initial, self.pending)

    def test_matching_legacy_count_cannot_authorize_an_uncertain_restart_count(self):
        self.use_verified_open([2, None])
        self.observe((self.first, "a"), "transition_returned_to_previous")

        result = self.run_pass()

        self.assertIn("not independently confirmed", result["error"])
        self.executor.nav.read_filtered_count.assert_not_called()
        self.executor.nav.tap_first_pokemon.assert_called_once()
        self.assertEqual((1, 1), (result["checked"], result["favorited"]))
        self.assertEqual({self.executor._snapshot_keeper_key(self.second): 1}, self.pending)
        self.assertEqual(["a"], self.tapped)

    def test_confirmed_empty_initial_keeper_filter_is_not_count_uncertainty(self):
        self.use_verified_open([0])
        self.observe()

        result = self.run_pass()

        self.assertNotIn("error", result)
        self.assertEqual(0, result["checked"])
        self.executor.nav.tap_first_pokemon.assert_not_called()
        self.assertEqual(self.initial, self.pending)

    def test_confirmed_empty_restart_mismatches_the_original_positive_count(self):
        self.use_verified_open([2, 0])
        self.observe((self.first, "a"), "transition_returned_to_previous")

        result = self.run_pass()

        self.assertIn("expected 2, saw 0", result["error"])
        self.executor.nav.tap_first_pokemon.assert_called_once()
        self.assertEqual((1, 1), (result["checked"], result["favorited"]))
        self.assertEqual({self.executor._snapshot_keeper_key(self.second): 1}, self.pending)

    def test_pause_during_restart_count_requires_a_new_verified_count_without_recounting_stars(self):
        self.use_verified_open(None)
        def count():
            if self.executor.nav.read_filtered_count_verified.call_count == 2:
                self.executor.pause()
                self.executor.resume()
            return 2
        self.executor.nav.read_filtered_count_verified.side_effect = count
        self.observe((self.first, "a"), "transition_returned_to_previous",
                     (self.first, "a"), (self.second, "b"))

        result = self.run_pass()

        self.assertNotIn("error", result)
        self.assertEqual(3, self.executor.nav.read_filtered_count_verified.call_count)
        self.assertEqual(2, self.executor.nav.tap_first_pokemon.call_count)
        self.assertEqual(["a", "b"], self.tapped)
        self.assertEqual((2, 2, 1), (result["checked"], result["favorited"], result["restarts"]))
        self.assertFalse(self.pending)

    def test_first_favorite_is_replayed_without_retapping_or_double_consumption(self):
        self.observe((self.first, "a"), "transition_returned_to_previous",
                     (self.first, "a"), (self.second, "b"))

        result = self.run_pass()

        self.assertNotIn("error", result)
        self.assertEqual((2, 2, 0, 1), tuple(result[k] for k in ("checked", "favorited", "skipped", "restarts")))
        self.assertEqual(["a", "b"], self.tapped)
        self.assertFalse(self.pending)
        reset_kwargs, previous_key, last_key, accepted, pair = self.histories[2]
        self.assertEqual({"previous_accepted": None, "require_transition": False}, reset_kwargs)
        self.assertEqual((None,) * 4, (previous_key, last_key, accepted, pair))
        self.assertTrue(any("restarting" in call_.args[2] for call_ in self.executor.on_progress.call_args_list))
        self.assert_reopened_same_query()

    def test_restarted_traversal_does_not_assume_the_same_sort_order(self):
        self.observe((self.first, "a"), "transition_returned_to_previous",
                     (self.second, "b"), (self.first, "a"))

        result = self.run_pass()

        self.assertNotIn("error", result)
        self.assertEqual(["a", "b"], self.tapped)
        self.assertEqual((2, 2), (result["checked"], result["favorited"]))
        self.assertFalse(self.pending)

    def test_replayed_starred_neighbor_does_not_finish_an_identical_pending_occurrence(self):
        self.executor._open_pass.return_value = 3
        self.stars["a"] = True
        self.observe((self.first, "a"), "transition_returned_to_previous",
                     (self.first, "a"), (self.first, "b"), (self.second, "c"))
        counts = {self.executor._snapshot_keeper_key(self.first): 2,
                  self.executor._snapshot_keeper_key(self.second): 1}

        result = self.run_pass(counts)

        self.assertNotIn("error", result)
        self.assertEqual(["b", "c"], self.tapped)
        self.assertEqual((3, 2), (result["checked"], result["favorited"]))
        self.assertFalse(self.pending)

    def test_exhausted_signature_budget_holds_an_extra_unstarred_identical_specimen(self):
        self.observe((self.first, "a"), "transition_returned_to_previous", (self.first, "extra"))

        result = self.run_pass()

        self.assertIn("allowance exhausted", result["error"])
        self.assertEqual(["a"], self.tapped)
        self.assertEqual((0, 1), (result["checked"], result["favorited"]))
        self.assertEqual(self.initial, self.pending)
        self.assertFalse(self.stars.get("extra", False))

    def test_changed_count_keeps_the_prior_traversal_accounting(self):
        self.executor._open_pass.side_effect = [2, 3]
        self.observe((self.first, "a"), "transition_returned_to_previous")

        result = self.run_pass()

        self.assertIn("filtered count changed", result["error"])
        self.assertEqual((1, 1, 1), (result["checked"], result["favorited"], result["restarts"]))
        self.assertEqual({self.executor._snapshot_keeper_key(self.second): 1}, self.pending)
        self.assertEqual(["a"], self.tapped)

    def test_failed_reopening_keeps_completed_allowance_consumption(self):
        self.executor._open_pass.side_effect = [2, RuntimeError("search readback failed")]
        self.observe((self.first, "a"), "transition_returned_to_previous")

        result = self.run_pass()

        self.assertIn("search readback failed", result["error"])
        self.assertEqual((1, 1), (result["checked"], result["favorited"]))
        self.assertEqual({self.executor._snapshot_keeper_key(self.second): 1}, self.pending)

    def test_second_unconfirmed_advance_holds_without_another_restart(self):
        self.observe((self.first, "a"), "transition_returned_to_previous",
                     (self.first, "a"), "transition_returned_to_previous")

        result = self.run_pass()

        self.assertIn("after one pass restart", result["error"])
        self.assertEqual(["a"], self.tapped)
        self.assertEqual((1, 1), (result["checked"], result["favorited"]))
        self.assertEqual({self.executor._snapshot_keeper_key(self.second): 1}, self.pending)
        self.assert_reopened_same_query()

    def test_identical_previous_checkpoint_can_restart_once(self):
        self.executor._open_pass.return_value = 3
        self.observe((self.first, "a"), (self.first, "b"), "transition_returned_to_previous",
                     (self.first, "a"), (self.first, "b"), (self.second, "c"))
        counts = {self.executor._snapshot_keeper_key(self.first): 2,
                  self.executor._snapshot_keeper_key(self.second): 1}

        result = self.run_pass(counts)

        self.assertNotIn("error", result)
        self.assertEqual(["a", "b", "c"], self.tapped)
        self.assertEqual((3, 3, 1), (result["checked"], result["favorited"], result["restarts"]))
        self.assertFalse(self.pending)
        self.assert_reopened_same_query()

    def test_distinct_previous_checkpoint_keeps_existing_recovery(self):
        self.executor._open_pass.return_value = 3
        third = replace(self.second, cp=300)
        self.observe((self.first, "a"), (self.second, "b"), "transition_returned_to_previous")
        counts = {self.executor._snapshot_keeper_key(read): 1 for read in (self.first, self.second, third)}

        result = self.run_pass(counts)

        self.assertIn("distinct checkpoint failed", result["error"])
        self.executor._open_pass.assert_called_once_with(self.query, verify_count=True)
        self.scanner._recover_failed_transition.assert_called_once()
        self.assertEqual(0, result["restarts"])

    def test_unrelated_cp_failure_does_not_restart(self):
        self.observe((self.first, "a"), "cp_recovery_failed")

        result = self.run_pass()

        self.assertIn("cp_recovery_failed", result["error"])
        self.executor._open_pass.assert_called_once_with(self.query, verify_count=True)
        self.assertEqual(0, result["restarts"])

    def test_pause_resume_during_reopening_does_not_repeat_a_completed_star(self):
        def open_pass(_query, **_kwargs):
            if self.executor._open_pass.call_count == 2:
                self.executor.pause()
                self.executor.resume()
            return 2
        self.executor._open_pass.side_effect = open_pass
        self.observe((self.first, "a"), "transition_returned_to_previous",
                     (self.first, "a"), (self.second, "b"))

        result = self.run_pass()

        self.assertNotIn("error", result)
        self.assertEqual(["a", "b"], self.tapped)
        self.assertEqual(4, self.scanner._acquire_validated_snapshot.call_count)
        self.assertFalse(self.pending)

    def test_abort_during_reopening_retains_completed_work(self):
        def open_pass(_query, **_kwargs):
            if self.executor._open_pass.call_count == 2:
                self.executor.abort()
                return 0
            return 2
        self.executor._open_pass.side_effect = open_pass
        self.observe((self.first, "a"), "transition_returned_to_previous")

        result = self.run_pass()

        self.assertTrue(result["aborted"])
        self.assertEqual((1, 1), (result["checked"], result["favorited"]))
        self.assertEqual(["a"], self.tapped)
        self.assertEqual({self.executor._snapshot_keeper_key(self.second): 1}, self.pending)

    def test_dry_run_resets_hypothetical_changes_for_the_new_traversal(self):
        self.observe((self.first, "a"), "transition_returned_to_previous",
                     (self.first, "a"), (self.second, "b"))

        result = self.run_pass(dry_run=True)

        self.assertNotIn("error", result)
        self.assertEqual((2, 2, 1), (result["checked"], result["favorited"], result["restarts"]))
        self.assertFalse(self.pending)
        self.adb.tap.assert_not_called()

    def test_outer_batch_preserves_completed_changes_when_refresh_count_disagrees(self):
        self.db.get_all.return_value = [pokemon(self.first), pokemon(self.second)]
        self.executor._write_log = Mock()
        self.executor._close_reader = Mock()
        self.use_verified_open([2, 2])
        self.observe((self.first, "a"), "transition_returned_to_previous")

        result = self.executor.favorite_keepers(selected_passes=["Normal"])

        self.assertEqual((1, 1, 1, 0), tuple(result[k] for k in ("favorited", "checked", "unmatched", "restarts")))
        self.assertEqual(["a"], self.tapped)
        self.assertIn("expected 1, saw 2", result["error"])
        self.executor.nav.tap_first_pokemon.assert_called_once()

    def test_outer_batch_preserves_completed_allowance_if_next_lap_read_fails(self):
        self.db.get_all.return_value = [pokemon(self.first), pokemon(self.second)]
        self.executor._write_log = Mock()
        self.executor._close_reader = Mock()
        self.use_verified_open([2, 1])
        self.observe((self.first, "a"), "transition_returned_to_previous", "cp_recovery_failed")

        result = self.executor.favorite_keepers(selected_passes=["Normal"])

        self.assertEqual((1, 1, 0, 1), tuple(result[k] for k in ("favorited", "checked", "skipped", "unmatched")))
        self.assertEqual(["a"], self.tapped)
        self.assertIn("cp_recovery_failed", result["error"])

    def test_pre_input_pause_in_advance_helper_rechecks_position_without_repeating_star(self):
        self.executor._advance_action = Executor._advance_action.__get__(self.executor)
        self.executor.reader.appraisal_bars_stable = Mock(return_value=True)
        captures = [sourced_frame(self.first, seq) for seq in (2, 3, 4)]
        self.scanner._fast_screencap = Mock(side_effect=captures)
        def advance(_frame, **_kwargs):
            if self.scanner._advance_appraisal.call_count == 1:
                self.executor.pause()
                self.executor.resume()
                return False
            return True
        self.scanner._advance_appraisal = Mock(side_effect=advance)

        self.assertTrue(self.executor._advance_action(self.first, sourced_frame(self.first, 1)))

        self.assertEqual(2, self.scanner._advance_appraisal.call_count)
        self.assertEqual([0, 1], [entry.kwargs["pause_generation"] for entry in self.scanner._advance_appraisal.call_args_list])
        self.assertEqual(3, self.scanner._fast_screencap.call_count)
        self.adb.tap.assert_not_called()

    def test_pause_after_advance_input_does_not_repeat_the_completed_gesture(self):
        self.executor._advance_action = Executor._advance_action.__get__(self.executor)
        self.scanner._fast_screencap = Mock(return_value=sourced_frame(self.first, 2))
        def advance(_frame, **_kwargs):
            self.executor.pause()
            return True  # The helper already sent the gesture.
        self.scanner._advance_appraisal = Mock(side_effect=advance)

        self.assertTrue(self.executor._advance_action(self.first, sourced_frame(self.first, 1)))

        self.scanner._advance_appraisal.assert_called_once()
        self.scanner._fast_screencap.assert_called_once()
        self.adb.tap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
