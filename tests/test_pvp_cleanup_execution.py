# TRACEWEAVER: file-role=selective-pvp-cleanup-tests; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
"""Reviewed selective star removal never expands into a blanket cleanup."""

# TRACEWEAVER: req=REQ-MASS-001,REQ-DATA-001; trace=TRACE-MASS-001; verifies=VER-SCAN-001

from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from pokemgr.data.database import PokemonDatabase
from pokemgr.data.models import Pokemon
from pokemgr.execution.executor import CleanupObservation, Executor
from pokemgr.indexer.snapshot import SnapshotDecision
from tests import test_mass_action_scanning as fixtures
from tests.test_database_positions import pokemon_read


def row(pid=1, **changes):
    values = dict(id=pid, species="Pikachu", cp=500 + pid, hp=60,
                  atk=10, def_=12, sta=14, iv_total=36, iv_pct=.8,
                  shiny=False, shadow=False, lucky=False, is_dynamax=False,
                  favorited=True, position=pid, decision="TRANSFER",
                  decision_reason="not retained", scan_session_id="session")
    return Pokemon(**(values | changes))


class PvpCleanupExecutionPlanTests(unittest.TestCase):
    def setUp(self):
        self.executor = object.__new__(Executor)
        self.executor.db = Mock()
        self.executor._abort = self.executor._paused = False
        self.executor.on_progress = self.executor.on_error = None
        self.executor.on_action_progress = None
        self.executor._action_progress_state = None
        self.executor._close_reader = Mock()
        self.executor._run_pass = Mock(side_effect=self.mock_pass)
        self.executor._open_pass = Mock(return_value=1)
        self.executor._scanner = SimpleNamespace(_pause_generation=0)
        self.executor.adb = SimpleNamespace(serial="serial")
        self.executor.profile = SimpleNamespace(serial="serial", fingerprint="device_dpi420", legacy_fingerprint="device")
        self.executor.db.get_session_device_fingerprints.return_value = {"session": "device_dpi420"}
        self.rows = [row()]
        self.executor.db.get_all_for_cleanup.return_value = self.rows

    def mock_pass(self, _query, _target, *, keepers, inventory=None, opened_total=1, **_kwargs):
        if inventory is not None:
            inventory.extend(CleanupObservation(Executor._keeper_key(self.rows[0]), "on")
                             for _ in range(opened_total))
            return dict(unfavorited=0, checked=opened_total, skipped=0)
        count = sum(keepers.values())
        keepers.clear()
        return dict(unfavorited=count, checked=opened_total, skipped=opened_total-count)

    def run_action(self, reviewed=None, **options):
        return self.executor.unfavorite_pvp_candidates(
            self.rows if reviewed is None else reviewed, **options)

    def test_missing_or_invalid_review_never_opens_a_pass(self):
        for reviewed in (None, [], [row(decision="KEEP")], [row(favorited=False)],
                         [row(atk=11)], [row(id=0)], [row(hp=-1)], [row(scan_session_id="")],
                         [row(), row()], [row(atk=True)]):
            with self.subTest(reviewed=reviewed):
                result = self.executor.unfavorite_pvp_candidates(reviewed)
                self.assertIn("error", result)
                self.assertEqual((0, 0, False), (result["unfavorited"], result["db_synced"], result["partial"]))
                self.executor._run_pass.assert_not_called()
                self.executor.db.update_favorited_many.assert_not_called()

    def test_all_reviewed_rows_must_still_match_current_identity_and_authority(self):
        for changes in (dict(id=2), dict(species="Raichu"), dict(cp=999), dict(hp=61),
                        dict(atk=9), dict(def_=11), dict(sta=13), dict(shiny=True),
                        dict(shadow=True), dict(lucky=True), dict(is_dynamax=True),
                        dict(scan_session_id="rescan"), dict(decision="KEEP"),
                        dict(decision=None), dict(favorited=False), dict(atk=11)):
            with self.subTest(changes=changes):
                self.executor.db.get_all_for_cleanup.return_value = [replace(self.rows[0], **changes)]
                result = self.run_action()
                self.assertIn("error", result)
                self.assertEqual(1, result["unmatched"])
                self.executor._run_pass.assert_not_called()
                self.executor.db.update_favorited_many.assert_not_called()

    def test_new_duplicate_including_protected_deselected_or_unstarred_companion_holds_everything(self):
        for changes in (dict(), dict(decision="KEEP"), dict(favorited=False), dict(scan_session_id="rescan")):
            with self.subTest(changes=changes):
                companion = replace(self.rows[0], id=2, **changes)
                self.executor.db.get_all_for_cleanup.return_value = [self.rows[0], companion]
                result = self.run_action()
                self.assertIn("error", result)
                self.assertEqual(1, result["ambiguous"])
                self.executor._run_pass.assert_not_called()

    def test_mixed_review_cannot_partially_start_when_one_candidate_changed(self):
        reviewed = [row(1), row(2)]
        self.executor.db.get_all_for_cleanup.return_value = [reviewed[0], replace(reviewed[1], decision="KEEP")]
        result = self.run_action(reviewed)
        self.assertIn("error", result)
        self.executor._run_pass.assert_not_called()

    def test_caller_cannot_mutate_reviewed_signature_after_action_start(self):
        def current():
            self.rows[0].cp = 777
            return [replace(self.rows[0])]
        self.executor.db.get_all_for_cleanup.side_effect = current
        result = self.run_action()
        self.assertIn("changed", result["error"])
        self.executor._run_pass.assert_not_called()

    def test_stable_batches_verify_all_candidates_without_per_pokemon_search(self):
        reviewed = [row(index + 1, cp=1000 + 2 * index) for index in range(180)]
        self.executor.db.get_all_for_cleanup.return_value = reviewed + [row(999, cp=9000), row(1000, decision="KEEP")]
        queries, visited = [], Counter()
        planned = list(Executor._cleanup_batches(list(Executor._keeper_queries(["Normal"],
                       Counter(Executor._keeper_key(item) for item in reviewed))),
                       Counter(Executor._keeper_key(item) for item in reviewed), "Normal"))
        by_query = {queries[0][0]: keys for _flags, keys, queries in planned}
        self.executor._open_pass.side_effect = lambda query, **_kwargs: len(by_query[query])
        def run(query, target, *, keepers, flags, opened_total, inventory):
            self.assertIs(target, False)
            self.assertFalse({"favorite", "!favorite"} & set(query.split("&")))
            self.assertTrue({"!3*", "!4*"} <= set(query.split("&")))
            self.assertEqual((False,) * 4, tuple(flags.values()))
            self.assertEqual(len(by_query[query]), opened_total)
            self.assertFalse(keepers)
            self.assertFalse(set(visited) & set(by_query[query]))
            visited.update(by_query[query])
            queries.append(query)
            inventory.extend(CleanupObservation(key, "on") for key in by_query[query])
            return dict(unfavorited=0, checked=opened_total, skipped=0)
        self.executor._run_pass.side_effect = run
        result = self.run_action(reviewed, dry_run=True, selected_passes=["Normal"])
        self.assertNotIn("error", result)
        self.assertGreater(len(queries), 1)
        self.assertTrue(all(len(query) <= 500 for query in queries))
        self.assertEqual(Counter(Executor._keeper_key(item) for item in reviewed), visited)
        self.assertEqual((180, 0, 0, 0, False),
                         tuple(result[key] for key in ("unfavorited", "checked", "unmatched", "db_synced", "partial")))
        self.assertEqual(180, result["verified"])
        self.assertEqual(len(planned), self.executor._open_pass.call_count)
        self.executor.db.update_favorited_many.assert_not_called()

    def test_missing_or_wrong_device_fingerprint_and_serial_hold_before_navigation(self):
        for condition in ("missing", "wrong_fingerprint", "wrong_serial", "empty_serial"):
            with self.subTest(condition=condition):
                self.setUp()
                if condition == "missing": self.executor.db.get_session_device_fingerprints.return_value = {}
                elif condition == "wrong_fingerprint": self.executor.db.get_session_device_fingerprints.return_value = {"session": "other-device"}
                else: self.executor.adb.serial = "" if condition == "empty_serial" else "other"
                result = self.run_action()
                self.assertIn("error", result)
                self.executor._open_pass.assert_not_called()
                self.executor._run_pass.assert_not_called()

    def test_live_duplicate_or_missing_member_never_enters_action_traversal(self):
        for count in (0, 2, 3):
            with self.subTest(count=count):
                self.setUp()
                self.executor._open_pass.return_value = count
                result = self.run_action(selected_passes=["Normal"])
                self.assertNotIn("error", result)
                self.assertEqual(1, result["unmatched"])
                self.assertEqual(int(count > 1), result["ambiguous"])
                self.assertTrue(all("inventory" in item.kwargs for item in self.executor._run_pass.call_args_list))
                self.assertEqual(1, self.executor._open_pass.call_count)

    def test_dynamax_and_gigantamax_inventories_are_combined_before_action(self):
        self.rows = [row(is_dynamax=True)]
        self.executor.db.get_all_for_cleanup.return_value = self.rows
        self.executor._open_pass.return_value = 1
        result = self.run_action(selected_passes=["Dynamax"])
        self.assertNotIn("error", result)
        self.assertEqual(1, result["unmatched"])
        self.assertEqual(1, result["ambiguous"])
        self.assertTrue(all("inventory" in item.kwargs for item in self.executor._run_pass.call_args_list))
        calls = self.executor._open_pass.call_args_list
        self.assertTrue(any("!gigantamax" in item.args[0].split("&") for item in calls))
        self.assertTrue(any("gigantamax" in item.args[0].split("&") for item in calls))
        # The selected Dynamax query also constrains its Gmax intersection;
        # retain that manifest in addition to both complete variant inventories.
        self.assertEqual(3, len(calls))

    def test_pause_during_count_proof_holds_before_opening_or_mutation(self):
        def opened(*_args, **_kwargs):
            self.executor._scanner._pause_generation += 1
            return 1
        self.executor._open_pass.side_effect = opened
        result = self.run_action(selected_passes=["Normal"])
        self.assertIn("uniqueness proof", result["error"])
        self.executor._run_pass.assert_not_called()

    def test_reader_close_failure_preserves_successful_counts(self):
        self.executor._close_reader.side_effect = RuntimeError("close failed")
        result = self.run_action(selected_passes=["Normal"])
        self.assertEqual((1, 1, 0), (result["unfavorited"], result["checked"], result["unmatched"]))
        self.assertIn("close failed", result["error"])
        self.assertTrue(result["partial"])
        self.assertIsNone(self.executor._action_progress_state)

    def test_partial_batch_failure_retains_actual_changes_and_remaining_budget(self):
        reviewed = [row(index + 1, cp=1000 + 2 * index) for index in range(180)]
        self.executor.db.get_all_for_cleanup.return_value = reviewed
        def run(_query, _target, *, keepers, inventory=None, **_kwargs):
            if inventory is not None:
                inventory.append(CleanupObservation(Executor._keeper_key(reviewed[0]), "on"))
                return dict(unfavorited=0, checked=1)
            del keepers[next(iter(keepers))]
            return dict(unfavorited=1, checked=2, skipped=1, error="readback held")
        self.executor._run_pass.side_effect = run
        result = self.run_action(reviewed)
        self.assertEqual(2, self.executor._run_pass.call_count)
        self.assertEqual((1, 2, 1, 179, True),
                         tuple(result[key] for key in ("unfavorited", "checked", "skipped", "unmatched", "partial")))
        self.assertEqual("readback held", result["error"])

    def test_stop_before_start_or_from_progress_sends_no_inputs_and_never_finishes(self):
        for timing in ("initial", "starting_pass"):
            with self.subTest(timing=timing):
                self.setUp()
                events = []
                def progress(event):
                    events.append(event)
                    self.executor._abort = True
                self.executor.on_action_progress = progress
                self.executor._abort = timing == "initial"
                result = self.run_action()
                self.assertTrue(result["aborted"])
                self.assertFalse(result["partial"])
                self.executor._run_pass.assert_not_called()
                self.assertNotIn("finished", [event["stage"] for event in events])

    def test_unfavorite_progress_and_selected_pass_counts_preserve_no_pending_categories(self):
        events = []
        self.executor.on_action_progress = events.append
        result = self.run_action(dry_run=True, selected_passes=["Normal", "Shiny"])
        self.assertNotIn("error", result)
        self.assertEqual(["Normal", "Shiny"], [event["pass_name"] for event in events if event["stage"] == "starting_pass"])
        self.assertEqual((1, 0, 0, 0), tuple(events[-1][key] for key in (
            "unfavorited_total", "checked_total", "pending_total", "passes_remaining")))
        self.assertTrue(all(event["action"] == "unfavorite" and event["target_state"] is False for event in events))
        self.assertTrue(all(event["dry_run"] for event in events))


class PvpCleanupLiveVerificationTests(unittest.TestCase):
    """Real executor/star persistence with mocked captures and a temporary DB."""

    def setUp(self):
        fixtures.MassActionScanningTests.setUp(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = PokemonDatabase(Path(self.temp.name) / "cleanup.db")
        self.addCleanup(self.db.close)
        self.db.create_session("one", self.executor.profile.fingerprint)
        self.executor.db = self.db
        self.adb.serial = self.executor.profile.serial
        self.executor._close_reader = Mock()
        self.reader.read_cp = Mock(return_value=(-1, 0))

    setup_positions = fixtures.MassActionScanningTests.setup_positions

    def insert(self, *, cp=500, hp=60, species="Pikachu", ivs=(10, 12, 14), decision="TRANSFER", favorited=True):
        pid = self.db.insert_pokemon(pokemon_read(
            species=species, hp=hp, cp=cp, atk=ivs[0], def_=ivs[1], sta=ivs[2], favorited=favorited,
        ), "one", len(self.db.get_all()) + 1)
        self.db.update_decision(pid, decision, "review test")
        self.db.flush()
        return next(item for item in self.db.get_all() if item.id == pid)

    def prepare(self, rows, *, stars=None):
        reads = [fixtures.snapshot(cp=item.cp, hp=item.hp, atk=item.atk, def_=item.def_, sta=item.sta) for item in rows]
        self.setup_positions(reads, target=False, stars=stars)
        self.executor._open_pass.return_value = len(reads)
        self.scanner._acquire_validated_snapshot = Mock(side_effect=[
            (SnapshotDecision(True, "exact", read, cp_source="calculated"),
             fixtures.frame(read, stars[index] if stars else "on"), "ok", "exact")
            for _traversal in range(2) for index, read in enumerate(reads)
        ])
        return reads

    def states(self):
        return {item.id: item.favorited for item in self.db.get_all()}

    def test_only_exact_reviewed_on_to_off_is_tapped_and_committed(self):
        candidate = self.insert()
        keep = self.insert(cp=501, decision="KEEP")
        three = self.insert(cp=502, ivs=(11, 12, 14))
        perfect = self.insert(cp=503, ivs=(15, 15, 15))
        other = self.insert(cp=504)
        self.prepare([candidate])
        result = self.executor.unfavorite_pvp_candidates([candidate], selected_passes=["Normal"])
        self.assertNotIn("error", result)
        self.assertEqual((1, 1, 0, 0, 1, 0), tuple(result[key] for key in (
            "unfavorited", "checked", "skipped", "unmatched", "db_synced", "db_unresolved")))
        self.assertEqual({candidate.id: False, keep.id: True, three.id: True, perfect.id: True, other.id: True}, self.states())
        self.adb.tap.assert_called_once()
        self.assertEqual(2, self.executor._open_pass.call_count)
        self.assertTrue(self.executor._open_pass.call_args.kwargs["verify_count"])
        self.assertFalse({"favorite", "!favorite"} & set(self.executor._open_pass.call_args.args[0].split("&")))

    def test_malformed_protected_companion_flag_holds_before_navigation(self):
        candidate = self.insert()
        protected = self.insert(decision="KEEP")
        self.db.conn.execute("UPDATE pokemon SET shiny = 2 WHERE id = ?", (protected.id,))
        self.db.conn.commit()
        self.prepare([candidate])
        result = self.executor.unfavorite_pvp_candidates([candidate], selected_passes=["Normal"])
        self.assertIn("ambiguous", result["error"])
        self.assertEqual((0, 1, 1, 0), (result["unfavorited"], result["unmatched"],
                                      result["ambiguous"], result["db_synced"]))
        self.executor._open_pass.assert_not_called()
        self.adb.tap.assert_not_called()
        self.assertTrue(all(self.states().values()))

    def test_companion_flag_corrupted_after_preview_holds_before_star(self):
        candidate = self.insert()
        protected = self.insert(decision="KEEP")
        self.db.conn.execute("UPDATE pokemon SET shiny = 1 WHERE id = ?", (protected.id,))
        self.db.conn.commit()
        self.prepare([candidate])
        original = self.executor._confirm_star_input
        def confirm(*args):
            result = original(*args)
            self.db.conn.execute("UPDATE pokemon SET shiny = 2 WHERE id = ?", (protected.id,))
            self.db.conn.commit()
            return result
        self.executor._confirm_star_input = confirm
        result = self.executor.unfavorite_pvp_candidates([candidate], selected_passes=["Normal"])
        self.assertIn("ambiguous", result["error"])
        self.assertEqual((0, 0), (result["unfavorited"], result["db_synced"]))
        self.adb.tap.assert_not_called()
        self.assertTrue(all(self.states().values()))

    def test_live_protected_or_unreviewed_tuple_cannot_consume_candidate_or_tap(self):
        for changes in (dict(atk=11), dict(atk=15, def_=15, sta=15), dict(cp=501), dict(hp=61)):
            with self.subTest(changes=changes):
                self.setUp()
                candidate = self.insert()
                self.prepare([candidate])
                read = fixtures.snapshot(cp=candidate.cp, hp=candidate.hp, atk=candidate.atk,
                                         def_=candidate.def_, sta=candidate.sta)
                read = replace(read, **changes)
                self.scanner._acquire_validated_snapshot.return_value = (
                    SnapshotDecision(True, "exact", read, cp_source="calculated"), fixtures.frame(read), "ok", "exact")
                self.scanner._acquire_validated_snapshot.side_effect = None
                result = self.executor.unfavorite_pvp_candidates([candidate], selected_passes=["Normal"])
                self.assertNotIn("error", result)
                self.assertEqual((0, 0, 1), (result["unfavorited"], result["checked"], result["unmatched"]))
                self.adb.tap.assert_not_called()
                self.assertTrue(self.states()[candidate.id])

    def test_candidate_newly_keep_before_star_is_held_without_tap(self):
        candidate = self.insert()
        self.prepare([candidate])
        original = self.executor._confirm_star_input
        def confirm(*args):
            result = original(*args)
            self.db.update_decision(candidate.id, "KEEP", "personal protection")
            self.db.flush()
            return result
        self.executor._confirm_star_input = confirm
        result = self.executor.unfavorite_pvp_candidates([candidate], selected_passes=["Normal"])
        self.assertIn("protected", result["error"])
        self.assertEqual(0, result["unfavorited"])
        self.adb.tap.assert_not_called()
        self.assertTrue(self.states()[candidate.id])

    def test_candidate_newly_keep_after_confirmed_tap_reports_unsaved_physical_change(self):
        candidate = self.insert()
        self.prepare([candidate])
        original = self.scanner._safe_tap
        def tap(*args, **kwargs):
            sent = original(*args, **kwargs)
            self.db.update_decision(candidate.id, "KEEP", "personal protection")
            self.db.flush()
            return sent
        self.scanner._safe_tap = tap
        result = self.executor.unfavorite_pvp_candidates([candidate], selected_passes=["Normal"])
        self.assertIn("protected", result["error"])
        self.assertEqual((1, 0, 1), (result["unfavorited"], result["db_synced"], result["db_unresolved"]))
        self.assertTrue(result["partial"])
        self.adb.tap.assert_called_once()
        self.assertTrue(self.states()[candidate.id])

    def test_new_duplicate_before_star_is_held_without_tap(self):
        candidate = self.insert()
        self.prepare([candidate])
        original = self.executor._confirm_star_input
        def confirm(*args):
            result = original(*args)
            self.insert()
            return result
        self.executor._confirm_star_input = confirm
        result = self.executor.unfavorite_pvp_candidates([candidate], selected_passes=["Normal"])
        self.assertIn("ambiguous", result["error"])
        self.adb.tap.assert_not_called()
        self.assertTrue(all(self.states().values()))

    def test_changed_pause_epoch_before_star_never_reuses_singleton_count(self):
        candidate = self.insert()
        read = self.prepare([candidate])[0]
        self.scanner._acquire_validated_snapshot.side_effect = None
        self.scanner._acquire_validated_snapshot.return_value = (
            SnapshotDecision(True, "exact", read, cp_source="calculated"), fixtures.frame(read), "ok", "exact")
        original = self.executor._confirm_star_input
        self.scanner._fast_screencap = Mock(return_value=fixtures.frame(read, "on"))
        first = True
        def confirm(*args):
            nonlocal first
            result = original(*args)
            if first:
                first = False
                self.scanner._pause_generation += 1
            return result
        self.executor._confirm_star_input = confirm
        result = self.executor.unfavorite_pvp_candidates([candidate], selected_passes=["Normal"])
        self.assertIn("uniqueness proof", result["error"])
        self.adb.tap.assert_not_called()
        self.assertTrue(self.states()[candidate.id])

    def test_already_off_live_card_repairs_only_its_stored_status_without_tap(self):
        candidate = self.insert()
        self.prepare([candidate], stars=["off"])
        result = self.executor.unfavorite_pvp_candidates([candidate], selected_passes=["Normal"])
        self.assertNotIn("error", result)
        self.assertEqual((0, 1, 0), (result["unfavorited"], result["db_synced"], result["unmatched"]))
        self.assertFalse(self.states()[candidate.id])
        self.adb.tap.assert_not_called()

    def test_dry_run_does_not_tap_or_save_live_state(self):
        candidate = self.insert()
        self.prepare([candidate])
        result = self.executor.unfavorite_pvp_candidates([candidate], dry_run=True, selected_passes=["Normal"])
        self.assertNotIn("error", result)
        self.assertTrue(result["dry_run"])
        self.assertEqual((1, 0, 0), (result["unfavorited"], result["db_synced"], result["db_unresolved"]))
        self.assertTrue(self.states()[candidate.id])
        self.adb.tap.assert_not_called()

    def test_unknown_star_outcome_holds_without_persisting_or_a_second_tap(self):
        candidate = self.insert()
        read = self.prepare([candidate])[0]
        self.scanner._fast_screencap = Mock(return_value=fixtures.frame(read, "on"))
        result = self.executor.unfavorite_pvp_candidates([candidate], selected_passes=["Normal"])
        self.assertIn("error", result)
        self.assertEqual((0, 0, 1), (result["unfavorited"], result["db_synced"], result["unmatched"]))
        self.adb.tap.assert_called_once()
        self.assertTrue(self.states()[candidate.id])

    def test_source_reset_during_terminal_off_readback_never_saves_or_repeats_tap(self):
        for changed_field in ("pokemgr_stream_session", "pokemgr_source_clock_continuity"):
            with self.subTest(field=changed_field):
                self.setUp()
                candidate = self.insert()
                read = self.prepare([candidate])[0]
                def image(star, *, changed=False):
                    value = fixtures.frame(read, star)
                    value.info.update(pokemgr_stream_session="original", pokemgr_source_clock_continuity="a" * 32)
                    if changed:
                        value.info[changed_field] = "b" * 32
                    return value
                self.scanner._acquire_validated_snapshot.side_effect = [
                    (SnapshotDecision(True, "exact", read, cp_source="calculated"), image("on"), "ok", "exact")
                    for _ in range(2)]
                self.scanner._fast_screencap.side_effect = [image("on"), image("off", changed=True)]
                result = self.executor.unfavorite_pvp_candidates([candidate], selected_passes=["Normal"])
                self.assertIn("source continuity", result["error"])
                self.assertEqual((0, 0, 1), (result["unfavorited"], result["db_synced"], result["unmatched"]))
                self.adb.tap.assert_called_once()
                self.assertTrue(self.states()[candidate.id])

    def test_source_reset_before_swipe_preserves_prior_confirmed_change_without_advancing(self):
        first, second = self.insert(), self.insert(cp=501)
        reads = self.prepare([first, second])
        self.executor._advance_action = Executor._advance_action.__get__(self.executor)
        self.scanner._advance_appraisal = Mock()
        # Inventory advance is still independently mocked here; action's
        # post-star advance exercises the real fresh checkpoint implementation.
        advance = self.executor._advance_action
        def route(snapshot, frame, **kwargs):
            if self.executor._action_progress_state["cleanup_phase"] == "verify":
                return True
            return advance(snapshot, frame, **kwargs)
        self.executor._advance_action = Mock(side_effect=route)
        changed = fixtures.frame(reads[0], "off")
        changed.info.update(pokemgr_stream_session="new", pokemgr_source_clock_continuity="b" * 32)
        self.scanner._fast_screencap.side_effect = [fixtures.frame(reads[0], "on"),
                                                  fixtures.frame(reads[0], "off"), changed]
        result = self.executor.unfavorite_pvp_candidates([first, second], selected_passes=["Normal"])
        self.assertIn("source continuity", result["error"])
        self.assertEqual((1, 1, 1), (result["unfavorited"], result["db_synced"], result["unmatched"]))
        self.scanner._advance_appraisal.assert_not_called()
        self.adb.tap.assert_called_once()
        self.assertEqual({first.id: False, second.id: True}, self.states())

    def test_reopened_count_drift_or_unverified_count_holds_before_any_tile_input(self):
        for counts in ([1, 2], [None], [True]):
            with self.subTest(counts=counts):
                self.setUp()
                candidate = self.insert()
                self.prepare([candidate])
                self.executor._open_pass = Executor._open_pass.__get__(self.executor)
                self.executor.nav.navigate_to_storage = Mock(return_value=True)
                self.executor.nav.enter_search = Mock(return_value=True)
                self.executor.nav.read_filtered_count_verified = Mock(side_effect=counts)
                self.executor.nav.read_filtered_count = Mock(return_value=1)
                self.executor.nav.tap_first_pokemon = Mock(return_value=True)
                self.executor.nav.open_first_appraisal = Mock(return_value=True)
                result = self.executor.unfavorite_pvp_candidates([candidate], selected_passes=["Normal"])
                self.assertIn("error", result)
                self.assertEqual((0, 0, 1), (result["unfavorited"], result["db_synced"], result["unmatched"]))
                self.assertEqual(int(len(counts) > 1), self.executor.nav.tap_first_pokemon.call_count)
                self.assertEqual(int(len(counts) > 1), self.executor.nav.open_first_appraisal.call_count)
                self.executor.nav.read_filtered_count.assert_not_called()
                self.adb.tap.assert_not_called()
                self.assertTrue(self.states()[candidate.id])

    def test_database_failure_preserves_confirmed_phone_changes_and_prior_commit(self):
        first, second = self.insert(), self.insert(cp=501)
        self.prepare([first, second])
        save = self.db.update_favorited_reviewed
        def save_one(rows, *, target):
            if any(item.id == second.id for item in rows):
                raise RuntimeError("temporary storage failure")
            save(rows, target=target)
        with patch.object(self.db, "update_favorited_reviewed", side_effect=save_one):
            self.executor.on_error = Mock(side_effect=RuntimeError("observer failed"))
            result = self.executor.unfavorite_pvp_candidates([first, second], selected_passes=["Normal"])
        self.assertIn("temporary storage failure", result["error"])
        self.assertEqual((2, 1, 1), (result["unfavorited"], result["db_synced"], result["db_unresolved"]))
        self.assertTrue(result["partial"])
        self.assertEqual({first.id: False, second.id: True}, self.states())
        self.assertEqual(2, self.adb.tap.call_count)


class PvpCleanupGroupExecutionTests(unittest.TestCase):
    """Temporary SQLite integration for complete, uniformly unwanted groups."""

    setup_positions = fixtures.MassActionScanningTests.setup_positions
    insert = PvpCleanupLiveVerificationTests.insert
    states = PvpCleanupLiveVerificationTests.states

    def setUp(self):
        PvpCleanupLiveVerificationTests.setUp(self)
        Executor._cleanup_species_evidence.cache_clear()
        self.addCleanup(Executor._cleanup_species_evidence.cache_clear)
        self.species_map = {
            "oricorio_" + form.lower(): dict(id="oricorio_" + form.lower(), name=f"Oricorio ({form})",
                                          base_atk=196, base_def=145, base_sta=181)
            for form in ("Baile", "Sensu", "Pom-Pom", "Pa'u")}
        self.enterContext(patch("pokemgr.pvp.resolver._default_species_map", return_value=self.species_map))

    def oricorio(self, species="Oricorio"):
        return self.insert(species=species, cp=226, hp=50, ivs=(0, 14, 15))

    def prepare(self, rows, *, stars=None, reported=None):
        reads = reported or [fixtures.snapshot(
            detected_species="Oricorio", caught_species="Oricorio", display_name="Personal nickname",
            cp=row.cp, hp=row.hp, atk=row.atk, def_=row.def_, sta=row.sta) for row in rows]
        self.setup_positions(reads, target=False, stars=stars)
        self.executor._open_pass.return_value = len(reads)
        self.scanner._acquire_validated_snapshot = Mock(side_effect=[
            (SnapshotDecision(True, "equivalent forms", read, cp_source="calculated", exact_form=False),
             fixtures.frame(read, stars[index] if stars else "on"), "ok", "equivalent forms")
            for _traversal in range(2) for index, read in enumerate(reads)])
        return reads

    def test_actual_oricorio_generic_record_covers_all_four_authoritative_forms(self):
        candidate = self.oricorio()
        self.prepare([candidate])
        original = self.executor._set_star
        seen = []
        def star(*args, **kwargs):
            seen.append(kwargs["cp_decision"].exact_form)
            return original(*args, **kwargs)
        self.executor._set_star = star
        result = self.executor.unfavorite_pvp_candidates([candidate], selected_passes=["Normal"])
        self.assertNotIn("error", result)
        self.assertEqual((1, 1, 1, 0), tuple(result[name] for name in
                                           ("verified", "unfavorited", "db_synced", "unmatched")))
        self.assertEqual([False], seen)
        self.adb.tap.assert_called_once()
        self.assertFalse(self.states()[candidate.id])
        key = Executor._keeper_key(candidate)
        possible, options = Executor._cleanup_species_evidence(key, "Oricorio", "", False)
        self.assertEqual(5, len(possible))
        self.assertEqual(4, len(options))

    def test_both_generic_duplicates_are_removed_and_saved_only_after_whole_group(self):
        candidates = [self.oricorio(), self.oricorio()]
        self.prepare(candidates)
        before_save = []
        original = self.db.update_favorited_reviewed
        def save(rows, **kwargs):
            before_save.append((len(rows), self.adb.tap.call_count, tuple(self.states().values())))
            original(rows, **kwargs)
        with patch.object(self.db, "update_favorited_reviewed", side_effect=save):
            result = self.executor.unfavorite_pvp_candidates(candidates, selected_passes=["Normal"])
        self.assertNotIn("error", result)
        self.assertEqual((2, 2, 0, 0), tuple(result[name] for name in
                                           ("unfavorited", "db_synced", "db_unresolved", "unmatched")))
        self.assertEqual([(2, 2, (True, True))], before_save)
        self.assertFalse(any(self.states().values()))

    def test_exact_review_of_two_forms_does_not_authorize_unreviewed_other_forms(self):
        candidates = [self.oricorio("Oricorio (Baile)"), self.oricorio("Oricorio (Sensu)")]
        self.prepare(candidates)
        result = self.executor.unfavorite_pvp_candidates(candidates, selected_passes=["Normal"])
        self.assertNotIn("error", result)
        self.assertEqual((0, 0, 2, 2), tuple(result[name] for name in
                                           ("unfavorited", "checked", "ambiguous", "unmatched")))
        self.adb.tap.assert_not_called()
        self.assertTrue(all(self.states().values()))

    def test_all_four_exact_forms_do_not_claim_unknown_form_multiplicities(self):
        candidates = [self.oricorio(value["name"]) for value in self.species_map.values()]
        self.prepare(candidates)
        result = self.executor.unfavorite_pvp_candidates(candidates, selected_passes=["Normal"])
        self.assertNotIn("error", result)
        self.assertEqual((0, 0, 4), (result["unfavorited"], result["db_synced"], result["unmatched"]))
        self.adb.tap.assert_not_called()

    def test_one_exact_and_one_generic_row_cannot_cover_two_ambiguous_forms(self):
        candidates = [self.oricorio("Oricorio (Baile)"), self.oricorio()]
        self.prepare(candidates)
        result = self.executor.unfavorite_pvp_candidates(candidates, selected_passes=["Normal"])
        self.assertNotIn("error", result)
        self.assertEqual((0, 2, 2), (result["unfavorited"], result["ambiguous"], result["unmatched"]))
        self.adb.tap.assert_not_called()

    def test_group_live_count_mismatch_or_deselected_companion_never_taps(self):
        candidates = [self.oricorio(), self.oricorio()]
        self.prepare(candidates[:1])
        result = self.executor.unfavorite_pvp_candidates(candidates, selected_passes=["Normal"])
        self.assertNotIn("error", result)
        self.assertEqual((0, 2, 2), (result["unfavorited"], result["ambiguous"], result["unmatched"]))
        self.adb.tap.assert_not_called()
        self.executor._open_pass.reset_mock()
        result = self.executor.unfavorite_pvp_candidates(candidates[:1], selected_passes=["Normal"])
        self.assertIn("protected", result["error"])
        self.executor._open_pass.assert_not_called()
        self.adb.tap.assert_not_called()

    def test_partial_group_preserves_physical_count_without_arbitrary_row_save(self):
        candidates = [self.oricorio(), self.oricorio()]
        reads = self.prepare(candidates)
        self.scanner._fast_screencap.side_effect = [fixtures.frame(reads[0], "on"), fixtures.frame(reads[0], "off"),
                                                  *[fixtures.frame(reads[1], "on") for _ in range(4)]]
        result = self.executor.unfavorite_pvp_candidates(candidates, selected_passes=["Normal"])
        self.assertIn("not confirmed", result["error"])
        self.assertEqual((1, 0, 1, 2), tuple(result[name] for name in
                                           ("unfavorited", "db_synced", "db_unresolved", "unmatched")))
        self.assertEqual(2, self.adb.tap.call_count)
        self.assertTrue(all(self.states().values()))
        # A new complete traversal can observe the earlier OFF card and the
        # remaining ON card; both affirmative OFF outcomes certify the group.
        self.adb.tap.reset_mock()
        self.prepare(candidates, stars=["off", "on"])
        result = self.executor.unfavorite_pvp_candidates(candidates, selected_passes=["Normal"])
        self.assertNotIn("error", result)
        self.assertEqual((1, 2, 0, 0), tuple(result[name] for name in
                                           ("unfavorited", "db_synced", "db_unresolved", "unmatched")))
        self.adb.tap.assert_called_once()

    def test_new_keep_on_other_group_member_holds_before_next_tap(self):
        candidates = [self.oricorio(), self.oricorio()]
        self.prepare(candidates)
        original = self.executor._confirm_star_input
        def confirm(*args):
            result = original(*args)
            if self.adb.tap.call_count == 1:
                self.db.update_decision(candidates[0].id, "KEEP", "protect group member")
                self.db.flush()
            return result
        self.executor._confirm_star_input = confirm
        result = self.executor.unfavorite_pvp_candidates(candidates, selected_passes=["Normal"])
        self.assertIn("protected", result["error"])
        self.assertEqual((1, 0, 1, 2), tuple(result[name] for name in
                                           ("unfavorited", "db_synced", "db_unresolved", "unmatched")))
        self.adb.tap.assert_called_once()
        self.assertTrue(all(self.states().values()))

    def test_group_database_rejection_keeps_all_rows_unsaved_after_both_confirmations(self):
        candidates = [self.oricorio(), self.oricorio()]
        self.prepare(candidates)
        with patch.object(self.db, "update_favorited_reviewed", side_effect=RuntimeError("atomic save rejected")):
            result = self.executor.unfavorite_pvp_candidates(candidates, selected_passes=["Normal"])
        self.assertIn("atomic save rejected", result["error"])
        self.assertEqual((2, 0, 2, 2), tuple(result[name] for name in
                                           ("unfavorited", "db_synced", "db_unresolved", "unmatched")))
        self.assertTrue(all(self.states().values()))
