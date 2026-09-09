"""Action cancellation survives setup and completion follows local cleanup."""

import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pokemgr.gui.workers import FavoriteFilterWorker, FavoriteWorker, UnfavoriteWorker


class ActionWorkerCleanupTests(unittest.TestCase):
    def workers(self):
        adb, profile, db = Mock(), SimpleNamespace(), Mock()
        return (
            (FavoriteWorker(adb, profile, db), "favorite_keepers", "pokemgr.gui.workers.Executor"),
            (UnfavoriteWorker(adb, profile, db), "unfavorite_all", "pokemgr.gui.workers.Executor"),
            (FavoriteFilterWorker(adb, profile, "shiny", "Shiny"), "favorite_by_filter",
             "pokemgr.execution.executor.Executor"),
        )

    def test_abort_before_run_never_constructs_executor_or_database(self):
        for worker, method, target in self.workers():
            with self.subTest(worker=type(worker).__name__), patch(target) as executor, \
                 patch("pokemgr.data.database.PokemonDatabase") as db:
                worker.abort()
                results = []
                worker.finished.connect(results.append)
                worker.run()
                executor.assert_not_called()
                db.assert_not_called()
                self.assertEqual(len(results), 1)
                self.assertIs(results[0]['aborted'], True)

    def test_abort_during_executor_construction_is_not_lost(self):
        for worker, method, target in self.workers():
            with self.subTest(worker=type(worker).__name__):
                executor = Mock()

                def construct(*args):
                    worker.abort()
                    return executor

                with patch(target, side_effect=construct), \
                     patch("pokemgr.data.database.PokemonDatabase") as db:
                    worker.run()
                executor.abort.assert_called_once()
                executor._close_reader.assert_called_once()
                getattr(executor, method).assert_not_called()
                if isinstance(worker, FavoriteFilterWorker):
                    db.return_value.close.assert_called_once()

    def test_pause_and_resume_survive_setup_for_all_action_types(self):
        for worker, method, target in self.workers():
            with self.subTest(worker=type(worker).__name__):
                executor = Mock()
                getattr(executor, method).return_value = {'checked': 1}
                worker.pause()
                with patch(target, return_value=executor), \
                     patch("pokemgr.data.database.PokemonDatabase"):
                    worker.run()
                executor.pause.assert_called_once()
                worker.resume()
                executor.resume.assert_called_once()

    def test_stop_during_action_preserves_counts_and_marks_aborted_completion(self):
        for worker, method, target in self.workers():
            with self.subTest(worker=type(worker).__name__):
                executor, results = Mock(), []
                worker.finished.connect(results.append)
                def run_action(*args, **kwargs):
                    worker.abort()
                    return {'checked': 7, 'favorited': 3}
                getattr(executor, method).side_effect = run_action
                with patch(target, return_value=executor), \
                     patch("pokemgr.data.database.PokemonDatabase"):
                    worker.run()
                expected = {'checked': 7, 'favorited': 3, 'aborted': True}
                if isinstance(worker, FavoriteWorker):
                    expected['dry_run'] = False
                self.assertEqual(results, [expected])

    def test_empty_keeper_result_still_identifies_the_requested_dry_run(self):
        worker = FavoriteWorker(Mock(), SimpleNamespace(), Mock(), dry_run=True)
        results = []
        worker.finished.connect(results.append)
        with patch('pokemgr.gui.workers.Executor') as executor:
            executor.return_value.favorite_keepers.return_value = {'favorited': 0, 'checked': 0}
            worker.run()
        self.assertEqual(results, [{'favorited': 0, 'checked': 0, 'dry_run': True}])

    def test_all_actions_forward_executor_read_errors_to_the_initiating_ui(self):
        for worker, method, target in self.workers():
            with self.subTest(worker=type(worker).__name__):
                executor, errors = Mock(), []
                worker.error.connect(errors.append)
                def run_action(*args, **kwargs):
                    executor.on_error('Held: unreadable star')
                    return {'checked': 3, 'unresolved': 1}
                getattr(executor, method).side_effect = run_action
                with patch(target, return_value=executor), \
                     patch('pokemgr.data.database.PokemonDatabase'):
                    worker.run()
                self.assertEqual(errors, ['Held: unreadable star'])

    def test_filter_worker_closes_local_database_before_success_signal(self):
        worker = FavoriteFilterWorker(Mock(), SimpleNamespace(), "shiny", "Shiny")
        events = []
        worker.finished.connect(lambda result: events.append(("finished", result)))
        expected = {"favorited": 2, "checked": 2, "label": "Shiny"}
        with patch("pokemgr.execution.executor.Executor") as executor, \
             patch("pokemgr.data.database.PokemonDatabase") as db:
            executor.return_value.favorite_by_filter.return_value = expected
            db.return_value.close.side_effect = lambda: events.append(("db closed", None))
            worker.run()
        self.assertEqual(events, [("db closed", None), ("finished", expected)])

    def test_filter_worker_closes_local_database_before_error_completion(self):
        worker = FavoriteFilterWorker(Mock(), SimpleNamespace(), "shiny", "Shiny")
        events = []
        worker.finished.connect(lambda result: events.append(("finished", result)))
        with patch("pokemgr.execution.executor.Executor") as executor, \
             patch("pokemgr.data.database.PokemonDatabase") as db, \
             self.assertLogs("pokemgr.gui.workers", level="ERROR"):
            executor.return_value.favorite_by_filter.side_effect = RuntimeError("device lost")
            db.return_value.close.side_effect = lambda: events.append(("db closed", None))
            worker.run()
        self.assertEqual(events, [("db closed", None),
                                  ("finished", {"favorited": 0, "error": "device lost"})])


if __name__ == "__main__":
    unittest.main()
