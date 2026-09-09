"""Mass actions share stable same-frame reading without CP work for categories."""

from collections import Counter
from dataclasses import replace
from itertools import product
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from pokemgr.calibration.profile import CalibrationProfile
from pokemgr.calibration.regions import ScreenRegions
from pokemgr.execution.executor import Executor
from pokemgr.indexer.snapshot import AppraisalSnapshot, SnapshotDecision


def snapshot(**changes):
    values = dict(display_name="Sparky", detected_species="Pikachu", caught_species="Pikachu",
                  hp=60, cp=500, atk=12, def_=13, sta=14,
                  shiny=False, shadow=False, lucky=False, favorited=False, gender="male",
                  weight_tag="", height_tag="", is_dynamax=False,
                  detail_confidence=.95, appraisal_confidence=.95)
    return AppraisalSnapshot(**(values | changes))


def frame(read, star="off", screen="appraisal"):
    image = Image.new("RGB", (968, 2376), "white")
    image.info.update(read=read, star=star, screen=screen)
    return image


def pokemon(read=None, **changes):
    read = read or snapshot()
    return SimpleNamespace(**(dict(
        species=read.detected_species, display_name="Old nickname", cp=read.cp,
        hp=read.hp, atk=read.atk, def_=read.def_, sta=read.sta,
        shiny=read.shiny, shadow=read.shadow, lucky=read.lucky,
        is_dynamax=read.is_dynamax, favorited=False, decision="KEEP",
        scan_session_id="one-scan", iv_pct=.87, decision_reason="test",
    ) | changes))


class MassActionScanningTests(unittest.TestCase):
    def setUp(self):
        profile = CalibrationProfile("test", "serial", "968x2376", 420,
                                     ScreenRegions.default_for_resolution(968, 2376, density=420))
        self.adb, self.db = Mock(), Mock()
        self.executor = Executor(self.adb, profile, self.db)
        self.scanner = self.executor._scanner
        self.reader = self.executor.reader
        self.reader.prepare_native_ocr = Mock()
        self.reader.read_cp = Mock(side_effect=AssertionError("Category action must not read CP"))
        self.reader.are_bars_visible = Mock(return_value=True)
        self.executor.nav.detect_screen = Mock(side_effect=lambda image: image.info["screen"])
        self.scanner._read_appraisal_snapshot = Mock(side_effect=lambda image: (
            image.info["read"].as_detail() | {"snapshot_read_complete": image.info["read"].read_complete},
            image.info["read"].as_appraisal(),
        ))
        self.executor._advance_action = Mock(return_value=True)
        self.executor._write_log = Mock()
        self.enterContext(patch("pokemgr.execution.executor.time.sleep"))
        self.enterContext(patch("pokemgr.execution.executor.favorite_state",
                                side_effect=lambda image, _region: image.info["star"]))

    def setup_positions(self, reads, target=True, stars=None):
        pairs = [(frame(read), frame(read)) for read in reads]
        pending = iter(pairs)
        def settled(**_kwargs):
            pair = next(pending)
            self.scanner._settled_frame_pair = pair
            return pair[1], "stable"
        self.scanner._wait_for_stable_appraisal = Mock(side_effect=settled)
        self.executor._open_pass = Mock(return_value=len(reads))
        images = []
        for index, read in enumerate(reads):
            before = stars[index] if stars else ("off" if target else "on")
            images.append(frame(read, before))
            if before != ("on" if target else "off"):
                images.append(frame(read, "on" if target else "off"))
        self.scanner._fast_screencap = Mock(side_effect=images)
        return pairs

    def test_category_reads_one_complete_pair_and_confirms_each_star_without_cp(self):
        self.setup_positions([snapshot(), snapshot(caught_species="Raichu", detected_species="Raichu", hp=90)])

        result = self.executor.favorite_by_filter("shiny", "Shiny")

        self.assertNotIn("error", result)
        self.assertEqual((2, 2), (result["checked"], result["favorited"]))
        self.assertEqual(2, self.adb.tap.call_count)
        self.assertEqual(8, self.scanner._read_appraisal_snapshot.call_count)
        self.executor._advance_action.assert_called_once()
        self.reader.read_cp.assert_not_called()
        self.executor._open_pass.assert_called_once_with("shiny")

    def test_five_identical_tuples_remain_five_verified_positions(self):
        self.setup_positions([snapshot()] * 5)

        result = self.executor.favorite_by_filter("4*")

        self.assertEqual((5, 5), (result["checked"], result["favorited"]))
        self.assertEqual(4, self.executor._advance_action.call_count)

    def test_unfavorite_visits_stable_all_storage_and_verifies_off_state(self):
        self.setup_positions([snapshot(), snapshot()], target=False, stars=["off", "on"])

        result = self.executor.unfavorite_all()

        self.assertNotIn("error", result)
        self.assertEqual((2, 1), (result["checked"], result["unfavorited"]))
        self.executor._open_pass.assert_called_once_with("cp0-")
        self.adb.tap.assert_called_once()
        self.reader.read_cp.assert_not_called()

    def test_changed_hp_before_star_stops_without_count_or_blind_swipe(self):
        self.setup_positions([snapshot()])
        self.scanner._fast_screencap.side_effect = [frame(snapshot(hp=61))]

        result = self.executor.favorite_by_filter("shiny")

        self.assertIn("identity changed", result["error"])
        self.assertEqual((0, 0), (result["checked"], result["favorited"]))
        self.adb.tap.assert_not_called()
        self.executor._advance_action.assert_not_called()

    def test_unknown_star_does_not_default_to_off(self):
        self.setup_positions([snapshot()])
        self.scanner._fast_screencap.side_effect = [frame(snapshot(), "unknown")] * 3

        result = self.executor.favorite_by_filter("shiny")

        self.assertIn("star is unreadable", result["error"])
        self.adb.tap.assert_not_called()

    def test_failed_readback_never_repeats_a_star_toggle(self):
        self.setup_positions([snapshot()])
        self.scanner._fast_screencap.side_effect = [frame(snapshot(), "off")] * 4

        result = self.executor.favorite_by_filter("shiny")

        self.assertIn("not confirmed", result["error"])
        self.adb.tap.assert_called_once()
        self.assertEqual(0, result["favorited"])

    def test_abort_during_tap_sends_no_further_input_or_readback(self):
        self.setup_positions([snapshot()])
        self.adb.tap.side_effect = lambda *_args, **_kwargs: self.executor.abort()

        result = self.executor.favorite_by_filter("shiny")

        self.assertTrue(result["aborted"])
        self.assertEqual(1, self.scanner._fast_screencap.call_count)
        self.adb.tap.assert_called_once()
        self.executor._advance_action.assert_not_called()

    def test_unobserved_identical_position_cannot_be_counted_again(self):
        pairs = self.setup_positions([snapshot()])
        def settled(**_kwargs):
            self.scanner._settled_frame_pair = pairs[0]
            return pairs[0][1], "stable_transition_unobserved"
        self.scanner._wait_for_stable_appraisal.side_effect = settled

        with self.assertRaisesRegex(RuntimeError, "position could not be verified"):
            self.executor._acquire_identity(frame(snapshot()), snapshot(), True)

        self.adb.tap.assert_not_called()

    def test_different_numeric_identity_can_confirm_unobserved_transition(self):
        pairs = self.setup_positions([snapshot(hp=61)])
        def settled(**_kwargs):
            self.scanner._settled_frame_pair = pairs[0]
            return pairs[0][1], "stable_transition_unobserved"
        self.scanner._wait_for_stable_appraisal.side_effect = settled

        read, _frame = self.executor._acquire_identity(frame(snapshot()), snapshot(), True)

        self.assertEqual(61, read.hp)
        self.adb.tap.assert_not_called()

    def test_keeper_lookup_ignores_nickname_but_distinguishes_cp_and_flags(self):
        key = self.executor._keeper_key(pokemon())
        self.assertEqual(key, self.executor._snapshot_keeper_key(snapshot(display_name="Renamed")))
        for changes in ({"cp": 501}, {"shiny": True}, {"shadow": True}, {"lucky": True}, {"is_dynamax": True}):
            self.assertNotEqual(key, self.executor._snapshot_keeper_key(snapshot(**changes)))

    def test_keeper_multiset_includes_already_favorite_identical_occurrence(self):
        self.db.get_all.return_value = [pokemon(favorited=True), pokemon()]
        observed = []
        def run(_query, remaining, *_args, **_kwargs):
            observed.append(dict(remaining))
            remaining.clear()
            return {"favorited": 1, "checked": 2}
        self.executor._run_favorite_pass = Mock(side_effect=run)

        result = self.executor.favorite_keepers(selected_passes=["Normal"])

        self.assertEqual([2], list(observed[0].values()))
        self.assertEqual(0, result["unmatched"])

    def test_keeper_pass_does_not_finish_at_already_starred_identical_neighbor(self):
        read = snapshot()
        remaining = Counter({self.executor._snapshot_keeper_key(read): 2})
        self.executor._open_pass = Mock(return_value=2)
        decision = SnapshotDecision(True, "exact", read, cp_source="calculated")
        self.scanner._acquire_validated_snapshot = Mock(side_effect=[
            (decision, frame(read, "on"), "ok", "exact"),
            (decision, frame(read), "ok", "exact"),
        ])
        self.scanner._fast_screencap = Mock(side_effect=[
            frame(read, "on"), frame(read, "off"), frame(read, "on"),
        ])

        result = self.executor._run_favorite_pass("4*", remaining, False, 2)

        self.assertEqual((2, 1), (result["checked"], result["favorited"]))
        self.assertEqual({}, remaining)
        self.adb.tap.assert_called_once()
        self.executor._advance_action.assert_called_once()

    def test_keeper_dry_run_counts_two_occurrences_without_star_mutation(self):
        read = snapshot()
        remaining = Counter({self.executor._snapshot_keeper_key(read): 2})
        self.executor._open_pass = Mock(return_value=2)
        decision = SnapshotDecision(True, "exact", read, cp_source="calculated")
        self.scanner._acquire_validated_snapshot = Mock(side_effect=[
            (decision, frame(read), "ok", "exact") for _ in range(2)
        ])
        self.scanner._fast_screencap = Mock(side_effect=[frame(read), frame(read)])

        result = self.executor._run_favorite_pass("4*", remaining, True, 2)

        self.assertEqual((2, 2), (result["checked"], result["favorited"]))
        self.adb.tap.assert_not_called()
        self.adb.key_event.assert_not_called()

    def test_form_ambiguous_family_cannot_consume_or_star_an_exact_keeper(self):
        read = snapshot(detected_species="Zigzagoon", caught_species="Zigzagoon",
                        cp=272, hp=83, atk=0, def_=15, sta=11)
        from pokemgr.indexer.snapshot import validate_snapshot
        decision = validate_snapshot(read, allow_calculated_cp=True)
        self.assertTrue(decision.accepted)
        self.assertFalse(decision.exact_form)
        remaining = Counter({self.executor._snapshot_keeper_key(read): 1})
        self.executor._open_pass = Mock(return_value=1)
        self.scanner._acquire_validated_snapshot = Mock(return_value=(decision, frame(read), "ok", "exact"))

        result = self.executor._run_favorite_pass("4*", remaining, False, 1)

        self.assertEqual((1, 1, 0), (result["checked"], result["skipped"], result["favorited"]))
        self.assertEqual(1, sum(remaining.values()))
        self.adb.tap.assert_not_called()

    def test_recovered_cp_conflict_on_fresh_frame_prevents_keeper_star(self):
        read = snapshot()
        self.scanner._fast_screencap = Mock(return_value=frame(read))
        self.reader.read_cp.side_effect = None
        self.reader.read_cp.return_value = (501, .95)
        original = SnapshotDecision(True, "recovered", read, cp_source="screen_after_animation")
        conflict = SnapshotDecision(True, "visible", replace(read, cp=501))

        with patch("pokemgr.execution.executor.validate_snapshot", return_value=conflict):
            with self.assertRaisesRegex(RuntimeError, "visible CP changed"):
                self.executor._set_star(read, frame(read), True, cp_decision=original)

        self.adb.tap.assert_not_called()

    def test_already_on_star_does_not_hide_a_contradictory_keeper_cp(self):
        read = snapshot()
        self.scanner._fast_screencap = Mock(return_value=frame(read, "on"))
        self.reader.read_cp.side_effect = None
        self.reader.read_cp.return_value = (501, .95)
        original = SnapshotDecision(True, "recovered", read, cp_source="screen_after_animation")
        conflict = SnapshotDecision(True, "visible", replace(read, cp=501))

        with patch("pokemgr.execution.executor.validate_snapshot", return_value=conflict):
            with self.assertRaisesRegex(RuntimeError, "visible CP changed"):
                self.executor._set_star(read, frame(read, "on"), True, cp_decision=original)

        self.adb.tap.assert_not_called()

    def test_real_reader_placeholder_flags_cannot_consume_normal_keeper_in_shiny_pass(self):
        raw = snapshot(shiny=False)  # Real ScreenReader defers shiny to the pass.
        normal_key = self.executor._snapshot_keeper_key(raw)
        shiny_key = self.executor._snapshot_keeper_key(replace(raw, shiny=True))
        remaining = Counter({normal_key: 1, shiny_key: 1})
        self.executor._open_pass = Mock(return_value=1)
        decision = SnapshotDecision(True, "exact", raw, cp_source="calculated")
        self.scanner._acquire_validated_snapshot = Mock(return_value=(decision, frame(raw), "ok", "exact"))
        self.scanner._fast_screencap = Mock(side_effect=[frame(raw), frame(raw, "on")])

        result = self.executor._run_pass(
            "shiny&!shadow&!lucky&!dynamax&!gigantamax", True, keepers=remaining,
            flags=dict(shiny=True, shadow=False, lucky=False, is_dynamax=False),
        )

        self.assertNotIn("error", result)
        self.assertEqual({normal_key: 1}, remaining)
        self.assertEqual(1, result["favorited"])

    def test_keeper_query_partitions_prove_flags_and_visit_every_selected_combination_once(self):
        required = {self.executor._snapshot_keeper_key(snapshot(shiny=s, shadow=h, lucky=l, is_dynamax=d))
                    for s, h, l, d in product((False, True), repeat=4)}
        for selection in (None, [], ["Shiny", "Shadow"], ["Dynamax"], ["Dynamax", "Gigantamax"]):
            selected = {name for name, _query in Executor.ALL_FAV_PASSES} if selection is None else set(selection)
            queries = list(Executor._keeper_queries(selection, required))
            for shiny, shadow, lucky, dynamax, gigantamax in product((False, True), repeat=5):
                state = dict(shiny=shiny, shadow=shadow, lucky=lucky, dynamax=dynamax, gigantamax=gigantamax)
                matched = [flags for _name, query, flags in queries
                           if all(state[term.lstrip("!")] != term.startswith("!") for term in query.split("&"))]
                expected = (("Normal" in selected and not any((shiny, shadow, dynamax, gigantamax)))
                            or ("Shiny" in selected and shiny) or ("Shadow" in selected and shadow)
                            or ("Dynamax" in selected and dynamax) or ("Gigantamax" in selected and gigantamax))
                self.assertEqual(int(expected), len(matched), (selection, state, matched))
                for flags in matched:
                    self.assertEqual(dict(shiny=shiny, shadow=shadow, lucky=lucky, is_dynamax=dynamax or gigantamax), flags)

    def test_changed_completed_position_cannot_be_swiped_past(self):
        original = snapshot()
        self.scanner._fast_screencap = Mock(return_value=frame(replace(original, hp=61)))

        with self.assertRaisesRegex(RuntimeError, "changed before swipe"):
            Executor._advance_action(self.executor, original, frame(original))

        self.adb.swipe.assert_not_called()

    def test_matching_completed_position_uses_calibrated_swipe_once(self):
        original = snapshot()
        fresh = frame(original)
        self.scanner._fast_screencap = Mock(return_value=fresh)

        self.assertTrue(Executor._advance_action(self.executor, original, frame(original)))

        self.adb.swipe.assert_called_once_with(*self.executor.regions.swipe_start,
                                              *self.executor.regions.swipe_end,
                                              self.executor.regions.swipe_duration_ms, jitter=0)
        self.assertIs(fresh, self.scanner._last_stable_image)

    def test_missing_flag_observation_is_not_known_false(self):
        image = frame(snapshot())
        detail = snapshot().as_detail()
        del detail["shadow"]
        self.scanner._read_appraisal_snapshot.return_value = detail, snapshot().as_appraisal()
        self.scanner._read_appraisal_snapshot.side_effect = None

        read = self.executor._read_identity(image)

        self.assertFalse(read.read_complete)
        self.assertIsNone(self.executor._identity(read))

    def test_mixed_decisions_and_cross_session_multiplicity_are_reported_unmatched(self):
        for extra in (pokemon(decision="TRANSFER"), pokemon(scan_session_id="rescan")):
            with self.subTest(extra=extra):
                self.db.get_all.return_value = [pokemon(), extra]
                self.executor._run_favorite_pass = Mock()

                result = self.executor.favorite_keepers()

                self.assertGreater(result["unmatched"], 0)
                self.executor._run_favorite_pass.assert_not_called()

    def test_empty_selected_passes_does_not_start_all_passes(self):
        self.db.get_all.return_value = [pokemon()]
        self.executor._run_favorite_pass = Mock()

        result = self.executor.favorite_keepers(selected_passes=[])

        self.assertEqual(1, result["unmatched"])
        self.executor._run_favorite_pass.assert_not_called()

    def test_selected_passes_are_disjoint_including_dry_run(self):
        self.assertEqual([("Shiny", "shiny"), ("Dynamax", "dynamax&!shiny")],
                         list(Executor._selected_queries(["Shiny", "Dynamax"])))
        self.assertEqual([("Dynamax", "dynamax")], list(Executor._selected_queries(["Dynamax"])))

    def test_dry_run_checks_fresh_star_but_does_not_tap(self):
        read = snapshot()
        self.scanner._fast_screencap = Mock(return_value=frame(read))

        changed, _frame = self.executor._set_star(read, frame(read), True, dry_run=True)

        self.assertTrue(changed)
        self.adb.tap.assert_not_called()

    def test_zero_count_never_blindly_opens_a_pokemon(self):
        self.executor.nav.navigate_to_storage = Mock(return_value=True)
        self.executor.nav.enter_search = Mock(return_value=True)
        self.executor.nav.read_filtered_count = Mock(return_value=0)
        self.executor.nav.tap_first_pokemon = Mock()

        self.assertEqual(0, self.executor._open_pass("shiny"))

        self.executor.nav.tap_first_pokemon.assert_not_called()
        self.executor.nav.enter_search.assert_called_once_with("shiny", verify=True)

    def test_unverified_search_never_opens_or_assigns_keeper_flags(self):
        self.executor.nav.navigate_to_storage = Mock(return_value=True)
        self.executor.nav.enter_search = Mock(return_value=False)
        self.executor.nav.tap_first_pokemon = Mock()
        remaining = Counter({self.executor._snapshot_keeper_key(snapshot(shiny=True)): 1})

        result = self.executor._run_pass("shiny", True, keepers=remaining,
                                         flags=dict(shiny=True, shadow=False, lucky=False, is_dynamax=False))

        self.assertIn("storage search filter", result["error"])
        self.executor.nav.tap_first_pokemon.assert_not_called()
        self.assertIsNone(self.executor._current_flags)
        self.assertEqual(1, sum(remaining.values()))

    def test_pause_during_filter_readback_restarts_navigation_before_opening(self):
        self.executor.nav.navigate_to_storage = Mock(return_value=True)
        def search(*_args, **_kwargs):
            if self.executor.nav.enter_search.call_count == 1:
                self.executor.pause()
                self.executor.resume()
                return False  # The helper discarded the paused read.
            return True
        self.executor.nav.enter_search = Mock(side_effect=search)
        self.executor.nav.read_filtered_count = Mock(return_value=0)
        self.executor.nav.tap_first_pokemon = Mock()

        self.assertEqual(0, self.executor._open_pass("shiny"))

        self.assertEqual(2, self.executor.nav.navigate_to_storage.call_count)
        self.assertEqual(2, self.executor.nav.enter_search.call_count)
        self.executor.nav.tap_first_pokemon.assert_not_called()

    def test_favorite_dependent_query_is_rejected_before_navigation(self):
        self.executor.nav.navigate_to_storage = Mock()

        with self.assertRaisesRegex(ValueError, "must not depend"):
            self.executor._open_pass("shiny&!favorite")

        self.executor.nav.navigate_to_storage.assert_not_called()

    def test_empty_action_filter_does_not_open_search_suggestion_tiles(self):
        self.executor.nav.navigate_to_storage = Mock()
        with self.assertRaisesRegex(ValueError, "empty search shows suggestions"):
            self.executor._open_pass("")
        self.executor.nav.navigate_to_storage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
