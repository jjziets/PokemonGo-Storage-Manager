# TRACEWEAVER: file-role=pvp-cleanup-batch-tests; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
# TRACEWEAVER: verifies=VER-SCAN-001; req=REQ-MASS-001; trace=TRACE-MASS-001
"""Batch carousel cleanup proves every live occurrence before removing stars."""

from collections import Counter
from dataclasses import replace
from unittest.mock import Mock
import unittest

from pokemgr.execution.executor import CleanupObservation, Executor, _CleanupProof
from pokemgr.indexer.snapshot import SnapshotDecision
from tests import test_mass_action_scanning as fixtures
from tests import test_pvp_cleanup_execution as cleanup


class CleanupInventoryTests(unittest.TestCase):
    def setUp(self):
        fixtures.MassActionScanningTests.setUp(self)
        self.executor._cleanup_proof = _CleanupProof(0)
        self.scanner._pause_generation = 0
        self.executor._favorite_sync = Mock(error=None)
        self.executor._set_star = Mock(return_value=(True, None))
        self.reads = [fixtures.snapshot(cp=500 + index) for index in range(3)]
        self.images = [fixtures.frame(read, "on") for read in self.reads]
        self.scanner._acquire_validated_snapshot = Mock(side_effect=[
            (SnapshotDecision(True, "exact", read, cp_source="calculated"), image, "ok", "exact")
            for read, image in zip(self.reads, self.images)])

    def run_inventory(self, *, total=3):
        inventory = []
        result = self.executor._run_pass("cp500-502", False, keepers=Counter(), opened_total=total,
                                         inventory=inventory)
        return result, inventory

    def test_inventory_visits_every_position_and_never_calls_star_or_sync(self):
        result, inventory = self.run_inventory()
        self.assertNotIn("error", result)
        self.assertEqual((3, 0, 0), (result["checked"], result["unfavorited"], result["restarts"]))
        self.assertEqual([Executor._snapshot_keeper_key(read) for read in self.reads],
                         [item.key for item in inventory])
        self.assertEqual(["on"] * 3, [item.star for item in inventory])
        self.assertEqual(2, self.executor._advance_action.call_count)
        self.executor._set_star.assert_not_called()
        self.assertFalse(self.executor._favorite_sync.mock_calls)

    def test_inventory_unresolved_or_unconfirmed_transition_holds_without_recovery(self):
        for failure in ("invalid", "transition_returned_to_previous", "reacquire"):
            with self.subTest(failure=failure):
                self.setUp()
                read, image = self.reads[1], self.images[1]
                decision = SnapshotDecision(True, "generic", read, exact_form=False) if failure == "generic" else None
                self.scanner._acquire_validated_snapshot.side_effect = [
                    (SnapshotDecision(True, "exact", self.reads[0]), self.images[0], "ok", "exact"),
                    (decision, image, failure, "unresolved")]
                self.scanner._recover_failed_transition = Mock()
                self.executor._open_pass = Mock()
                self.executor._acquire_identity = Mock()
                result, inventory = self.run_inventory()
                self.assertIn("error", result)
                self.assertEqual((1, 1, 0), (len(inventory), result["checked"], result["restarts"]))
                self.scanner._recover_failed_transition.assert_not_called()
                self.executor._open_pass.assert_not_called()
                self.executor._acquire_identity.assert_not_called()
                self.executor._set_star.assert_not_called()

    def test_pause_after_last_inventory_observation_invalidates_complete_manifest(self):
        self.executor.on_progress = lambda *_args: setattr(self.scanner, "_pause_generation", 1)
        result, _inventory = self.run_inventory(total=1)
        self.assertIn("uniqueness proof", result["error"])
        self.executor._set_star.assert_not_called()

    def test_clock_refresh_is_compatible_but_source_continuity_loss_holds(self):
        for change in ("refresh", "session", "continuity", "jpeg"):
            with self.subTest(change=change):
                self.setUp()
                for index, image in enumerate(self.images):
                    image.info.update(pokemgr_stream_session="stream", pokemgr_source_clock_continuity="a" * 32,
                                      pokemgr_source_clock_generation=index + 1)
                if change == "session": self.images[1].info["pokemgr_stream_session"] = "new-stream"
                elif change == "continuity": self.images[1].info["pokemgr_source_clock_continuity"] = "b" * 32
                elif change == "jpeg":
                    self.images[1].info.pop("pokemgr_stream_session")
                    self.images[1].info.pop("pokemgr_source_clock_continuity")
                result, _inventory = self.run_inventory()
                self.assertEqual(change != "refresh", "error" in result)
                self.executor._set_star.assert_not_called()

    def test_inventory_progress_counts_verified_only(self):
        events = []
        self.executor.on_action_progress = events.append
        self.executor._action_progress_state = dict(
            selected_passes=["Normal"], action="unfavorite", checked_total=7,
            unfavorited_total=2, pending_total=3, verified_total=4)
        result, _inventory = self.run_inventory()
        self.assertNotIn("error", result)
        self.assertEqual((7, 2, 7), tuple(events[-1][name] for name in
                                        ("checked_total", "unfavorited_total", "verified_total")))

    def test_inventory_rejects_non_native_numeric_flags_and_acceptance(self):
        for changes in (dict(cp=True), dict(hp=True), dict(atk=True), dict(shiny=1),
                        dict(read_complete=1), dict(accepted=1), dict(exact_form=1)):
            with self.subTest(changes=changes):
                self.setUp()
                decision_fields = {key: value for key, value in changes.items() if key in ("accepted", "exact_form")}
                read = replace(self.reads[0], **{key: value for key, value in changes.items() if key not in decision_fields})
                decision = replace(SnapshotDecision(True, "accepted", read), **decision_fields)
                self.scanner._acquire_validated_snapshot.side_effect = [(decision, fixtures.frame(read), "ok", "accepted")]
                result, inventory = self.run_inventory(total=1)
                self.assertIn("complete accepted identity", result["error"])
                self.assertFalse(inventory)
                self.executor._set_star.assert_not_called()

    def test_complete_generic_without_candidates_is_skip_only_and_does_not_stop_other_actions(self):
        self.executor._favorite_sync = None
        self.executor._cleanup_species_evidence = lambda key, _caught, _candy, exact: (
            (frozenset((key,)), ()) if exact else (frozenset(), ()))
        decisions = [SnapshotDecision(True, "accepted", read, cp_source="calculated", exact_form=index != 1)
                     for index, read in enumerate(self.reads)]
        acquisitions = [(decision, image, "ok", "accepted") for decision, image in zip(decisions, self.images)]
        self.scanner._acquire_validated_snapshot.side_effect = acquisitions
        result, inventory = self.run_inventory()
        self.assertNotIn("error", result)
        self.assertEqual(3, result["checked"])
        self.assertFalse(inventory[1].exact_form)
        self.assertFalse(inventory[1].compatible_keys)
        pending = Counter({inventory[index].key[1:]: 1 for index in (0, 2)})
        self.executor._cleanup_active_groups = {group: (object(),) for group in pending}
        self.scanner._acquire_validated_snapshot.side_effect = acquisitions
        self.executor._set_star.side_effect = lambda _read, image, *_args, **_kwargs: (True, image)
        messages = []
        self.executor.on_progress = lambda _current, _total, message: messages.append(message)
        result = self.executor._run_pass("cp500-502", False, keepers=pending, opened_total=3,
                                         expected_manifest=tuple(inventory), cleanup_groups=True)
        self.assertNotIn("error", result)
        self.assertEqual((2, 3, 1), (result["unfavorited"], result["checked"], result["skipped"]))
        self.assertEqual(2, self.executor._set_star.call_count)
        self.assertTrue(any("form unresolved; left favorited" in message for message in messages))

    def test_generic_possible_key_drift_holds_before_any_star(self):
        read, image = self.reads[0], self.images[0]
        key = Executor._snapshot_keeper_key(read)
        decision = SnapshotDecision(True, "generic", read, exact_form=False)
        self.executor._cleanup_species_evidence = lambda *_args: (frozenset((key,)), (frozenset((key,)),))
        expected = CleanupObservation(key, "on", False, frozenset((key, ("other form", *key[1:]))),
                                      (frozenset((key,)),))
        self.scanner._acquire_validated_snapshot.side_effect = [(decision, image, "ok", "generic")]
        result = self.executor._run_pass("cp500", False, keepers=Counter({key: 1}), opened_total=1,
                                         expected_manifest=(expected,))
        self.assertIn("ordered batch identity", result["error"])
        self.executor._set_star.assert_not_called()

    def test_action_checks_order_before_star_and_preserves_prior_confirmed_changes(self):
        self.executor._favorite_sync = None
        manifest = tuple(CleanupObservation(Executor._snapshot_keeper_key(read), "on") for read in self.reads)
        pending = Counter(item.key for item in manifest)
        self.executor._set_star.side_effect = lambda _read, image, *_args, **_kwargs: (True, image)
        self.scanner._acquire_validated_snapshot.side_effect = [
            (SnapshotDecision(True, "exact", self.reads[0]), self.images[0], "ok", "exact"),
            (SnapshotDecision(True, "exact", self.reads[2]), self.images[2], "ok", "exact")]
        result = self.executor._run_pass("cp500-502", False, keepers=pending, opened_total=3,
                                         expected_manifest=manifest)
        self.assertIn("ordered batch identity", result["error"])
        self.assertEqual((1, 1, 2), (result["unfavorited"], result["checked"], sum(pending.values())))
        self.executor._set_star.assert_called_once()

    def test_action_visits_remaining_manifest_even_after_target_is_consumed(self):
        self.executor._favorite_sync = None
        manifest = tuple(CleanupObservation(Executor._snapshot_keeper_key(read), "on") for read in self.reads)
        pending = Counter({manifest[0].key: 1})
        self.executor._set_star.side_effect = lambda _read, image, *_args, **_kwargs: (True, image)
        result = self.executor._run_pass("cp500-502", False, keepers=pending, opened_total=3,
                                         expected_manifest=manifest)
        self.assertNotIn("error", result)
        self.assertEqual((1, 3, 2), (result["unfavorited"], result["checked"], result["skipped"]))
        self.executor._set_star.assert_called_once()


class CleanupBatchOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = cleanup.PvpCleanupExecutionPlanTests()
        self.fixture.setUp()
        self.executor = self.fixture.executor
        self.rows = self.fixture.rows

    def test_inventory_failure_never_starts_action_even_after_target_seen(self):
        def incomplete(_query, _target, *, inventory, **_kwargs):
            inventory.append(CleanupObservation(Executor._keeper_key(self.rows[0]), "on"))
            return dict(checked=1, error="later unreadable identity")
        self.executor._open_pass.return_value = 2
        self.executor._run_pass.side_effect = incomplete
        result = self.fixture.run_action(selected_passes=["Normal"])
        self.assertEqual((0, 1, 0, 1), (result["unfavorited"], result["verified"], result["checked"], result["unmatched"]))
        self.assertIn("unreadable", result["error"])
        self.executor._run_pass.assert_called_once()

    def test_silently_incomplete_inventory_is_rejected(self):
        self.executor._run_pass.side_effect = lambda *_args, **_kwargs: dict(checked=0)
        result = self.fixture.run_action(selected_passes=["Normal"])
        self.assertIn("inventory was incomplete", result["error"])
        self.assertEqual((0, 1), (result["unfavorited"], result["unmatched"]))
        self.executor._open_pass.assert_called_once()

    def test_group_split_across_dynamax_variants_stays_unmatched_without_partial_taps(self):
        candidates = [cleanup.row(1, is_dynamax=True), cleanup.row(2, cp=501, is_dynamax=True)]
        self.executor.db.get_all_for_cleanup.return_value = candidates
        key = Executor._keeper_key(candidates[0])
        def run(_query, _target, *, inventory, **_kwargs):
            inventory.append(CleanupObservation(key, "on"))
            return dict(checked=1)
        self.executor._run_pass.side_effect = run
        result = self.executor.unfavorite_pvp_candidates(candidates, selected_passes=["Dynamax"])
        self.assertNotIn("error", result)
        self.assertEqual((0, 0, 2, 2), tuple(result[name] for name in
                                           ("unfavorited", "checked", "ambiguous", "unmatched")))
        self.assertTrue(all("inventory" in call.kwargs for call in self.executor._run_pass.call_args_list))

    def test_complete_matching_requires_multiplicity_not_just_matching_union(self):
        first = Executor._keeper_key(cleanup.row())
        second = ("raichu", *first[1:])
        expected = Counter({first: 1, second: 1})
        self.assertFalse(Executor._cleanup_group_matches(
            [CleanupObservation(first, "on"), CleanupObservation(first, "on")], expected))
        generic = CleanupObservation(("generic", *first[1:]), "on", False,
                                     frozenset((first, second)), (frozenset((first,)), frozenset((second,))))
        self.assertFalse(Executor._cleanup_group_matches([generic, CleanupObservation(first, "on")], expected))
        family = ("generic", *first[1:])
        generic = replace(generic, possible_keys=frozenset((first, second, family)),
                          canonical_options=(frozenset((first, family)), frozenset((second, family))))
        self.assertTrue(Executor._cleanup_group_matches([generic, CleanupObservation(first, "on")],
                                                       Counter({first: 1, family: 1})))
        self.assertFalse(Executor._cleanup_group_matches([generic, generic], Counter({first: 1, family: 1})))

    def test_dry_run_reads_tri_state_never_assumes_unknown_star_is_on(self):
        for star in ("on", "off", "unknown"):
            with self.subTest(star=star):
                self.setUp()
                def inventory(_query, _target, *, inventory, **_kwargs):
                    inventory.append(CleanupObservation(Executor._keeper_key(self.rows[0]), star))
                    return dict(checked=1)
                self.executor._run_pass.side_effect = inventory
                result = self.fixture.run_action(dry_run=True, selected_passes=["Normal"])
                self.assertNotIn("error", result)
                self.assertEqual((int(star == "on"), int(star == "unknown"), 0, 1),
                                 (result["unfavorited"], result["unmatched"], result["checked"], result["verified"]))
                self.executor._open_pass.assert_called_once()
                self.assertIsNone(self.executor._favorite_sync)

    def test_180_real_candidates_use_two_searches_per_cp_batch(self):
        rows = [cleanup.row(index + 1, cp=1000 + index * 2) for index in range(180)]
        self.executor.db.get_all_for_cleanup.return_value = rows
        keys = Counter(Executor._keeper_key(row) for row in rows)
        plans = list(Executor._cleanup_batches(list(Executor._keeper_queries(["Normal"], keys)), keys, "Normal"))
        manifests = {queries[0][0]: tuple(CleanupObservation(key, "on") for key in batch_keys)
                     for _flags, batch_keys, queries in plans}
        self.executor._open_pass.side_effect = lambda query, **_kwargs: len(manifests[query])
        def run(query, target, *, inventory=None, expected_manifest=None, keepers, opened_total, **_kwargs):
            self.assertIs(target, False)
            if inventory is not None:
                self.assertFalse(keepers)
                inventory.extend(manifests[query])
                return dict(checked=opened_total)
            self.assertEqual(manifests[query], expected_manifest)
            count = sum(keepers.values())
            keepers.clear()
            return dict(checked=opened_total, unfavorited=count)
        self.executor._run_pass.side_effect = run
        result = self.executor.unfavorite_pvp_candidates(rows, selected_passes=["Normal"])
        self.assertNotIn("error", result)
        self.assertEqual((180, 180, 180, 0), tuple(result[key] for key in
                                                ("verified", "checked", "unfavorited", "unmatched")))
        self.assertEqual(2 * len(plans), self.executor._open_pass.call_count)
        self.assertLess(self.executor._open_pass.call_count, 20)
        for query in manifests:
            calls = [call for call in self.executor._open_pass.call_args_list if call.args[0] == query]
            self.assertEqual(2, len(calls))
            self.assertEqual(len(manifests[query]), calls[-1].kwargs["expected_count"])
            self.assertFalse({"favorite", "!favorite"} & set(query.split("&")))

    def test_gigantamax_selected_scope_does_not_mutate_unselected_dynamax(self):
        candidate = cleanup.row(is_dynamax=True)
        self.executor.db.get_all_for_cleanup.return_value = [candidate]
        key = Executor._keeper_key(candidate)
        def run(query, _target, *, inventory=None, **_kwargs):
            self.assertIsNotNone(inventory)
            if "!gigantamax" in query.split("&"):
                inventory.append(CleanupObservation(key, "on"))
                return dict(checked=1)
            return dict(checked=0)
        self.executor._run_pass.side_effect = run
        self.executor._open_pass.side_effect = lambda query, **_kwargs: int("!gigantamax" in query.split("&"))
        result = self.executor.unfavorite_pvp_candidates([candidate], selected_passes=["Gigantamax"])
        self.assertNotIn("error", result)
        self.assertEqual((0, 1, 1), (result["unfavorited"], result["unmatched"], result["verified"]))

    def test_unique_gigantamax_uses_selected_subset_without_double_counting_inventory(self):
        candidate = cleanup.row(is_dynamax=True)
        self.executor.db.get_all_for_cleanup.return_value = [candidate]
        key = Executor._keeper_key(candidate)
        observation = CleanupObservation(key, "on")
        action_queries = []
        self.executor._open_pass.side_effect = lambda query, **_kwargs: int("gigantamax" in query.split("&"))

        def run(query, target, *, inventory=None, keepers, expected_manifest=None, **_kwargs):
            terms = set(query.split("&"))
            if inventory is not None:
                if "gigantamax" in terms:
                    inventory.append(observation)
                return dict(checked=len(inventory))
            self.assertIs(target, False)
            self.assertTrue({"dynamax", "gigantamax"} <= terms)
            self.assertEqual((observation,), expected_manifest)
            self.assertEqual(Counter({key[1:]: 1}), keepers)
            action_queries.append(query)
            keepers.clear()
            return dict(checked=1, unfavorited=1)

        self.executor._run_pass.side_effect = run
        result = self.executor.unfavorite_pvp_candidates([candidate], selected_passes=["Dynamax"])
        self.assertNotIn("error", result)
        self.assertEqual((2, 1, 1, 0, 0), tuple(result[name] for name in
                                             ("verified", "checked", "unfavorited", "ambiguous", "unmatched")))
        self.assertEqual(1, len(action_queries))
        self.assertEqual(action_queries[0], self.executor._open_pass.call_args.args[0])
        self.assertEqual(1, self.executor._open_pass.call_args.kwargs["expected_count"])

    def test_default_passes_defer_absent_dynamax_subset_to_later_gigantamax(self):
        candidate = cleanup.row(is_dynamax=True)
        self.executor.db.get_all_for_cleanup.return_value = [candidate]
        key = Executor._keeper_key(candidate)
        observation = CleanupObservation(key, "on")
        actions = []
        def count(query, **_kwargs):
            terms = set(query.split("&"))
            return int("gigantamax" in terms and "dynamax" not in terms)
        self.executor._open_pass.side_effect = count
        def run(query, _target, *, inventory=None, keepers, **_kwargs):
            if inventory is not None:
                if count(query):
                    inventory.append(observation)
                return dict(checked=len(inventory))
            self.assertTrue({"gigantamax", "!dynamax"} <= set(query.split("&")))
            actions.append(self.executor._action_progress_state["pass_name"])
            keepers.clear()
            return dict(checked=1, unfavorited=1)
        self.executor._run_pass.side_effect = run
        result = self.executor.unfavorite_pvp_candidates([candidate])
        self.assertNotIn("error", result)
        self.assertEqual((1, 0, 0), (result["unfavorited"], result["ambiguous"], result["unmatched"]))
        self.assertEqual(["Gigantamax"], actions)

    def test_abort_after_full_inventory_never_reopens_action(self):
        def inventory(_query, _target, *, inventory, **_kwargs):
            inventory.append(CleanupObservation(Executor._keeper_key(self.rows[0]), "on"))
            self.executor._abort = True
            return dict(checked=1)
        self.executor._run_pass.side_effect = inventory
        result = self.fixture.run_action(selected_passes=["Normal"])
        self.assertTrue(result["aborted"])
        self.assertEqual((0, 1, 0), (result["unfavorited"], result["verified"], result["checked"]))
        self.executor._open_pass.assert_called_once()
