"""Action-owned native OCR is released after success, failure, and abort."""

import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pokemgr.calibration.regions import ScreenRegions
from pokemgr.execution.executor import Executor


class ExecutorResourceCleanupTests(unittest.TestCase):
    def setUp(self):
        self.reader = Mock()
        self.profile = SimpleNamespace(regions=ScreenRegions.default_for_resolution(968, 2376), density=420)
        with patch("pokemgr.indexer.state_machine.ScreenReader", return_value=self.reader):
            self.executor = Executor(Mock(), self.profile, Mock())

    def test_each_action_closes_reader_and_preserves_result(self):
        actions = (
            ("favorite_keepers", "_favorite_keepers", {"dry_run": True, "selected_passes": ["Normal"]}),
            ("favorite_by_filter", "_favorite_by_filter", {"search_query": "shiny", "label": "Shiny"}),
            ("unfavorite_all", "_unfavorite_all", {}),
        )
        for public, implementation, arguments in actions:
            with self.subTest(action=public):
                self.reader.reset_mock()
                expected = {"action": public, "count": 7}
                with patch.object(self.executor, implementation, return_value=expected):
                    self.assertIs(getattr(self.executor, public)(**arguments), expected)
                self.reader.close.assert_called_once()

    def test_each_action_closes_reader_before_propagating_failure(self):
        for public, implementation, arguments in (
            ("favorite_keepers", "_favorite_keepers", {}),
            ("favorite_by_filter", "_favorite_by_filter", {"search_query": "shiny"}),
            ("unfavorite_all", "_unfavorite_all", {}),
        ):
            with self.subTest(action=public):
                self.reader.reset_mock()
                with patch.object(self.executor, implementation, side_effect=RuntimeError("device lost")):
                    with self.assertRaisesRegex(RuntimeError, "device lost"):
                        getattr(self.executor, public)(**arguments)
                self.reader.close.assert_called_once()

    def test_keeper_empty_early_return_closes_reader_without_navigation(self):
        self.executor.db.get_all.return_value = []
        self.assertEqual(self.executor.favorite_keepers(), {"favorited": 0, "checked": 0})
        self.reader.close.assert_called_once()
        self.executor.adb.tap.assert_not_called()

    def test_abort_closes_reader_without_running_a_pass(self):
        self.executor.db.get_all.return_value = [SimpleNamespace(
            decision="KEEP", favorited=False, species="Dragonite", atk=15, def_=15, sta=15, hp=188,
            cp=4137, shiny=False, shadow=False, lucky=False, is_dynamax=False,
        )]
        self.executor.abort()
        with patch.object(self.executor, "_write_log"), patch.object(self.executor, "_run_favorite_pass") as run:
            self.assertEqual(self.executor.favorite_keepers(),
                             {"favorited": 0, "checked": 0, "dry_run": False, "aborted": True})
        run.assert_not_called()
        self.reader.close.assert_called_once()

    def test_late_background_read_finishes_before_native_worker_close(self):
        entered = threading.Event()
        release = threading.Event()
        joined = threading.Event()
        events = []

        def background_read():
            entered.set()
            release.wait(2)
            events.append("read finished")

        reader_thread = threading.Thread(target=background_read)
        reader_thread.start()
        self.assertTrue(entered.wait(1))
        self.executor._reader_threads.append(reader_thread)
        self.reader.close.side_effect = lambda: events.append("reader closed")

        def finish_action():
            self.executor._close_reader()
            joined.set()

        action_thread = threading.Thread(target=finish_action)
        action_thread.start()
        try:
            self.assertFalse(joined.wait(.02))
            self.reader.close.assert_not_called()
        finally:
            release.set()
            action_thread.join(2)
            reader_thread.join(2)
        self.assertFalse(action_thread.is_alive())
        self.assertEqual(events, ["read finished", "reader closed"])
        self.assertEqual(self.executor._reader_threads, [])


if __name__ == "__main__":
    unittest.main()
