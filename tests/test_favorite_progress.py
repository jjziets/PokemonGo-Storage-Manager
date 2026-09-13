"""Keeper progress observes real counters without changing candidate queries."""

# TRACEWEAVER: file-role=keeper-action-progress-tests; req=REQ-MASS-001; trace=TRACE-MASS-001; verifies=VER-SCAN-001
from collections import Counter
import unittest
from unittest.mock import Mock

from pokemgr.execution.executor import Executor
from pokemgr.execution.keeper_queries import pending_cp_batches
from tests.test_mass_action_scanning import pokemon
from tests import test_nonfavorite_carousel as carousel_fixture


class FavoriteProgressPlanningTests(unittest.TestCase):
    def setUp(self):
        self.executor = object.__new__(Executor)
        self.executor.db = Mock()
        self.executor._abort = self.executor._paused = False
        self.executor.on_progress = Mock()
        self.executor._write_log = Mock()
        self.executor._close_reader = Mock()
        self.events = []
        self.executor.on_action_progress = self.events.append
        self.executor._run_favorite_pass = Mock(side_effect=self.consume)

    def consume(self, _query, pending, *_args, **_kwargs):
        count = sum(pending.values())
        pending.clear()
        return {"favorited": count, "checked": count, "skipped": 0}

    def test_selected_category_indices_include_empty_categories_and_selected_targets_only(self):
        self.executor.db.get_all.return_value = [pokemon(cp=500), pokemon(cp=501),
                                                 pokemon(cp=502, is_dynamax=True), pokemon(hp=0)]

        result = self.executor.favorite_keepers(dry_run=True, selected_passes=["Shiny", "Normal", "Shadow"])

        starts = [event for event in self.events if event["stage"] == "starting_pass"]
        self.assertEqual([("Normal", 1), ("Shiny", 2), ("Shadow", 3)],
                         [(event["pass_name"], event["pass_index"]) for event in starts])
        completed = [event for event in self.events if event["stage"] == "pass_complete"]
        self.assertEqual([(1, 2), (2, 1), (3, 0)],
                         [(event["passes_completed"], event["passes_remaining"]) for event in completed])
        self.assertIn("Nothing pending", completed[1]["note"])
        self.assertEqual((2, 0, 1), tuple(self.events[-1][key]
                                        for key in ("target_total", "pending_total", "ambiguous_total")))
        self.assertEqual((2, 2), tuple(self.events[-1][key] for key in ("checked_total", "favorited_total")))
        self.assertEqual("finished", self.events[-1]["stage"])
        self.assertEqual(2, result["unmatched"], "Unselected/held records retain existing result semantics")
        self.assertTrue(all(event["schema"] == 1 and event["dry_run"] for event in self.events))
        self.assertTrue(all("batch_total" not in event for event in self.events))
        self.assertEqual(["Pass 1/3:", "Pass 2/3:", "Pass 3/3:"],
                         [" ".join(call.args[2].split()[:2]) for call in self.executor.on_progress.call_args_list])
        self.executor.db.get_all.assert_called_once()
        self.assertEqual(1, len(self.executor.db.mock_calls))

    def test_all_already_starred_reports_empty_selected_passes_without_action_or_log_io(self):
        self.executor.db.get_all.return_value = [pokemon(favorited=True)]

        result = self.executor.favorite_keepers(selected_passes=["Normal", "Shiny"])

        self.assertEqual({"favorited": 0, "checked": 0}, result)
        self.assertEqual(["starting_pass", "pass_complete", "starting_pass", "pass_complete", "finished"],
                         [event["stage"] for event in self.events])
        self.assertEqual((2, 0, 0), tuple(self.events[-1][key]
                                        for key in ("passes_completed", "passes_remaining", "target_total")))
        self.executor._run_favorite_pass.assert_not_called()
        self.executor._write_log.assert_not_called()

    def test_empty_plan_with_latched_abort_reports_no_completed_categories(self):
        self.executor.db.get_all.return_value = [pokemon(favorited=True)]
        self.executor._abort = True

        result = self.executor.favorite_keepers(selected_passes=["Normal", "Shiny"])

        self.assertEqual({"favorited": 0, "checked": 0, "dry_run": False, "aborted": True}, result)
        self.assertEqual(["stopped"], [event["stage"] for event in self.events])
        self.assertEqual((0, 2), (self.events[-1]["passes_completed"], self.events[-1]["passes_remaining"]))
        self.executor._run_favorite_pass.assert_not_called()
        self.executor._write_log.assert_not_called()
        self.assertEqual(1, len(self.executor.db.mock_calls))

    def test_abort_from_empty_category_start_does_not_complete_it_or_start_the_next(self):
        for rows in ([pokemon(favorited=True)], [pokemon(cp=501, shiny=True)]):
            with self.subTest(has_pending=not rows[0].favorited):
                self.events.clear()
                self.executor._abort = False
                self.executor.db.get_all.return_value = rows
                def stop(event):
                    self.events.append(event)
                    if event["stage"] == "starting_pass":
                        self.executor._abort = True
                self.executor.on_action_progress = stop

                result = self.executor.favorite_keepers(dry_run=True, selected_passes=["Normal", "Shiny"])

                self.assertTrue(result["aborted"])
                self.assertTrue(result["dry_run"])
                self.assertEqual(["starting_pass", "stopped"], [event["stage"] for event in self.events])
                self.assertEqual((0, 2), (self.events[-1]["passes_completed"], self.events[-1]["passes_remaining"]))
                self.executor._run_favorite_pass.assert_not_called()
                self.executor.db.update_favorited_many.assert_not_called()

    def test_empty_dry_run_preserves_the_result_mode_without_database_status_claims(self):
        self.executor.db.get_all.return_value = [pokemon(favorited=True)]

        result = self.executor.favorite_keepers(dry_run=True, selected_passes=["Normal"])

        self.assertEqual({"favorited": 0, "checked": 0, "dry_run": True}, result)
        self.assertTrue(all(event["dry_run"] for event in self.events))
        self.assertEqual("finished", self.events[-1]["stage"])
        self.executor.db.update_favorited_many.assert_not_called()

    def test_lazy_variant_cp_queries_and_occurrence_budgets_are_unchanged(self):
        rows = [pokemon(cp=1000 + 2 * i, is_dynamax=True) for i in range(180)]
        selected = ["Dynamax", "Gigantamax"]
        self.executor.db.get_all.return_value = rows

        def consume_variant(query, pending):
            before = pending.copy()
            keys = list(pending)
            remove = keys[::2] if "!gigantamax" in query.split("&") else keys
            for key in remove:
                del pending[key]
            return before, sum((before - pending).values())

        # Observe the previous lazy iteration contract, including rebuilding
        # a later variant's CP exclusions from its still-pending occurrences.
        remaining = Executor.plan_keeper_favorites(rows).remaining
        expected = []
        queries = list(Executor._keeper_queries(selected, remaining))
        batches = ((name, batch, flags) for name, query, flags in queries
                   for batch in pending_cp_batches(query + "&!favorite", remaining, flags))
        for _name, batch, flags in batches:
            if not remaining:
                break
            pending = Counter({key: remaining[key] for key in batch.keys if remaining[key] > 0})
            if not pending:
                continue
            before, _changed = consume_variant(batch.query, pending)
            expected.append((batch.query, before, flags))
            remaining.subtract(before - pending)
            remaining = +remaining

        actual = []
        def run(query, pending, *_args, flags):
            before, changed = consume_variant(query, pending)
            actual.append((query, before, flags))
            return {"favorited": changed, "checked": sum(before.values())}
        self.executor._run_favorite_pass.side_effect = run

        result = self.executor.favorite_keepers(dry_run=True, selected_passes=selected)

        self.assertEqual(expected, actual)
        batches = [event for event in self.events if event["stage"] == "starting_batch"]
        for name in selected:
            indices = [event["batch_index"] for event in batches if event["pass_name"] == name]
            self.assertEqual(list(range(1, len(indices) + 1)), indices)
        self.assertEqual((180, 180, 0), tuple(self.events[-1][key]
                                            for key in ("target_total", "favorited_total", "pending_total")))
        self.assertEqual(result["checked"], self.events[-1]["checked_total"])

    def test_partial_error_or_abort_does_not_complete_current_or_future_pass(self):
        for stop in ("error", "abort"):
            with self.subTest(stop=stop):
                self.events.clear()
                self.executor._abort = False
                self.executor.db.get_all.return_value = [pokemon(cp=500), pokemon(cp=501), pokemon(cp=502, shiny=True)]
                def partial(_query, pending, *_args, **_kwargs):
                    del pending[next(iter(pending))]
                    result = {"favorited": 1, "checked": 3, "skipped": 2}
                    if stop == "error":
                        result["error"] = "readback held"
                    else:
                        self.executor._abort = True
                    return result
                self.executor._run_favorite_pass.side_effect = partial

                self.executor.favorite_keepers(dry_run=True, selected_passes=["Normal", "Shiny"])

                self.assertEqual("error" if stop == "error" else "stopped", self.events[-1]["stage"])
                self.assertEqual((0, 2, 3, 1, 2), tuple(self.events[-1][key] for key in
                                                      ("passes_completed", "passes_remaining", "checked_total",
                                                       "favorited_total", "pending_total")))
                self.assertFalse(any(event["stage"] == "pass_complete" for event in self.events))

    def test_observer_exception_and_payload_mutation_cannot_change_execution(self):
        self.executor.db.get_all.return_value = [pokemon()]
        def broken(event):
            self.events.append(dict(event, selected_passes=list(event["selected_passes"])))
            event["selected_passes"].clear()
            event["pending_total"] = 10000
            raise RuntimeError("UI closed")
        self.executor.on_action_progress = broken

        result = self.executor.favorite_keepers(dry_run=True, selected_passes=["Normal"])

        self.assertEqual((1, 0), (result["favorited"], result["unmatched"]))
        self.assertTrue(all(event["selected_passes"] == ["Normal"] for event in self.events))
        self.assertEqual(0, self.events[-1]["pending_total"])


class FavoriteProgressTraversalTests(unittest.TestCase):
    def fixture(self, names="abcde", *, dry=False, hook=None):
        fixture = carousel_fixture.NonfavoriteCarouselTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.mode = "shrinking"
        for name in names:
            fixture.add(name)
        fixture.db.get_all.return_value = [pokemon(card.read, id=i)
                                           for i, card in enumerate(fixture.cards, 1)]
        fixture.executor._write_log = Mock()
        events = []
        fixture.executor.on_action_progress = events.append
        if hook is not None:
            hook(fixture)
        result = fixture.executor.favorite_keepers(dry_run=dry, selected_passes=["Normal", "Shiny"])
        return fixture, events, result

    def test_refreshes_reset_traversal_but_accumulate_action_counts_without_extra_inputs(self):
        fixture, events, result = self.fixture()

        self.assertEqual(["a", "c", "e", "b", "d"], fixture.tap_names)
        self.assertEqual([5, 2, 1, 0], fixture.counts)
        self.assertEqual(5, fixture.db.update_favorited_many.call_count)
        self.assertEqual([1, 2, 3], [event["traversal_index"] for event in events if event["stage"] == "refreshing"])
        counts = [event["checked_total"] for event in events]
        self.assertEqual(sorted(counts), counts)
        self.assertEqual((5, 5, 0, 2, 0), tuple(events[-1][key] for key in
                                              ("checked_total", "favorited_total", "pending_total",
                                               "passes_completed", "passes_remaining")))
        self.assertEqual(result["checked"], events[-1]["checked_total"])
        self.assertEqual(6, len(fixture.db.mock_calls), "One inventory read and five existing confirmed-state writes")

    def test_dry_run_counts_hypothetical_stars_once_without_refresh_or_saves(self):
        fixture, events, result = self.fixture("abc", dry=True)

        self.assertEqual([], fixture.tap_names)
        self.assertEqual([3], fixture.counts)
        fixture.db.update_favorited_many.assert_not_called()
        self.assertFalse(any(event["stage"] == "refreshing" for event in events))
        self.assertEqual((3, 3, 0), tuple(events[-1][key]
                                        for key in ("checked_total", "favorited_total", "pending_total")))
        self.assertTrue(events[-1]["dry_run"])
        self.assertEqual(3, result["favorited"])

    def test_uncertain_post_tap_error_preserves_only_confirmed_progress(self):
        def install(fixture):
            fixture.mode = "frozen"
            def uncertain(image):
                if fixture.tap_names == ["a", "b"]:
                    image.info["star"] = "unknown"
            fixture.capture_hook = uncertain
        fixture, events, result = self.fixture("abc", hook=install)

        self.assertIn("star change was not confirmed", result["error"])
        self.assertEqual(["a", "b"], fixture.tap_names)
        self.assertEqual([3], fixture.counts)
        self.assertEqual("error", events[-1]["stage"])
        self.assertEqual((1, 1, 2, 0, 2), tuple(events[-1][key] for key in
                                              ("checked_total", "favorited_total", "pending_total",
                                               "passes_completed", "passes_remaining")))

    def test_abort_after_confirmed_change_reports_partial_counts_without_more_navigation(self):
        def install(fixture):
            fixture.advance_hook = lambda: fixture.executor.abort() or False
        fixture, events, result = self.fixture("abc", hook=install)

        self.assertTrue(result["aborted"])
        self.assertEqual(["a"], fixture.tap_names)
        self.assertEqual([3], fixture.counts)
        self.assertEqual("stopped", events[-1]["stage"])
        self.assertEqual((1, 1, 2, 0), tuple(events[-1][key] for key in
                                           ("checked_total", "favorited_total", "pending_total", "passes_completed")))


if __name__ == "__main__":
    unittest.main()
