"""Pending CP batches preserve the exact occurrence budget and partial work."""

from collections import Counter
import unittest
from unittest.mock import Mock

from pokemgr.execution.executor import Executor
from tests.test_mass_action_scanning import pokemon


class KeeperBatchExecutionTests(unittest.TestCase):
    def setUp(self):
        self.executor = object.__new__(Executor)
        self.executor.db = Mock()
        self.executor._abort = self.executor._paused = False
        self.executor.on_progress = None
        self.executor._write_log = Mock()
        self.executor._close_reader = Mock()

    def test_disjoint_batches_only_contain_pending_cps_and_exclude_starred_neighbors(self):
        rows = [pokemon(cp=1000 + 2 * index) for index in range(180)]
        rows += [pokemon(cp=1000, favorited=True), pokemon(cp=999, favorited=True),
                 pokemon(cp=997, decision="TRANSFER")]
        self.executor.db.get_all.return_value = rows
        visited = Counter()
        queries = []

        def run(query, pending, dry_run, _total, *, flags):
            self.assertTrue(dry_run)
            self.assertTrue(pending)
            self.assertEqual((False,) * 4, tuple(flags.values()))
            self.assertFalse(set(visited) & set(pending))
            queries.append(query)
            self.assertIn("!favorite", query.split("&"))
            visited.update(pending)
            checked = sum(pending.values())
            pending.clear()
            return {"favorited": checked, "checked": checked}

        self.executor._run_favorite_pass = Mock(side_effect=run)

        result = self.executor.favorite_keepers(dry_run=True, selected_passes=["Normal"])

        self.assertGreater(len(queries), 1)
        self.assertTrue(all(len(query) <= 500 for query in queries))
        self.assertEqual(Executor.plan_keeper_favorites(rows).remaining, visited)
        self.assertEqual(1, visited[Executor._keeper_key(rows[0])])
        self.assertEqual(180, result["checked"])
        self.assertEqual(0, result["unmatched"])
        self.assertNotIn("error", result)

    def test_partial_failure_keeps_completed_occurrence_and_holds_later_batches(self):
        self.executor.db.get_all.return_value = [pokemon(cp=1000 + 2 * i) for i in range(180)]

        def run(_query, pending, *_args, **_kwargs):
            del pending[next(iter(pending))]
            return {"favorited": 1, "checked": 3, "skipped": 2, "error": "readback held"}

        self.executor._run_favorite_pass = Mock(side_effect=run)

        result = self.executor.favorite_keepers(selected_passes=["Normal"])

        self.executor._run_favorite_pass.assert_called_once()
        self.assertEqual((1, 3, 2, 179),
                         tuple(result[key] for key in ("favorited", "checked", "skipped", "unmatched")))
        self.assertEqual("readback held", result["error"])

    def test_abort_after_one_batch_does_not_open_another(self):
        self.executor.db.get_all.return_value = [pokemon(cp=1000 + 2 * i) for i in range(180)]

        def run(_query, pending, *_args, **_kwargs):
            checked = sum(pending.values())
            pending.clear()
            self.executor._abort = True
            return {"favorited": checked, "checked": checked, "aborted": True}

        self.executor._run_favorite_pass = Mock(side_effect=run)

        result = self.executor.favorite_keepers(selected_passes=["Normal"])

        self.executor._run_favorite_pass.assert_called_once()
        self.assertTrue(result["aborted"])
        self.assertGreater(result["unmatched"], 0)
        self.assertEqual(180, result["checked"] + result["unmatched"])

    def test_flag_batches_leave_unselected_and_unobserved_occurrences_unmatched(self):
        self.executor.db.get_all.return_value = [pokemon(cp=500), pokemon(cp=500, shiny=True),
                                                 pokemon(cp=501, lucky=True)]
        flags_seen = []

        def run(_query, pending, *_args, flags):
            flags_seen.append(flags)
            if flags["lucky"]:
                return {"checked": 0}
            pending.clear()
            return {"favorited": 1, "checked": 1}

        self.executor._run_favorite_pass = Mock(side_effect=run)

        result = self.executor.favorite_keepers(selected_passes=["Normal"])

        self.assertEqual(2, len(flags_seen))
        self.assertTrue(all(not flags["shiny"] for flags in flags_seen))
        self.assertEqual((1, 2), (result["favorited"], result["unmatched"]))


if __name__ == "__main__":
    unittest.main()
