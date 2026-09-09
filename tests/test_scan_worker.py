import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pokemgr.gui.workers import ScanFromCurrentWorker, ScanWorker


class ScanWorkerFailureTests(unittest.TestCase):
    @patch("pokemgr.gui.workers.ensure_dirs")
    @patch("pokemgr.gui.workers.MultiPassScanner")
    def test_fatal_scan_emits_failed_without_success_finished(
            self, scanner_cls, _ensure_dirs):
        scanner = scanner_cls.return_value
        scanner.passes = [Mock()]
        scanner.start.side_effect = RuntimeError("scan stopped safely")

        worker = ScanWorker(
            Mock(),
            SimpleNamespace(regions=Mock()),
            Mock(),
        )
        errors = []
        failures = []
        finishes = []
        worker.error.connect(errors.append)
        worker.failed.connect(failures.append)
        worker.finished.connect(finishes.append)

        worker.run()

        self.assertEqual(["scan stopped safely"], errors)
        self.assertEqual(["scan stopped safely"], failures)
        self.assertEqual([], finishes)

    @patch("pokemgr.gui.workers.ensure_dirs")
    @patch("pokemgr.gui.workers.MultiPassScanner")
    def test_stop_before_worker_start_performs_no_scan_actions(
            self, scanner_cls, _ensure_dirs):
        worker = ScanWorker(
            Mock(),
            SimpleNamespace(regions=Mock()),
            Mock(),
        )
        finishes = []
        worker.finished.connect(finishes.append)

        worker.abort()
        worker.run()

        scanner_cls.assert_not_called()
        self.assertEqual([], finishes)

    @patch("pokemgr.gui.workers.ensure_dirs")
    @patch("pokemgr.gui.workers.GameNavigator")
    def test_current_scan_stop_before_start_performs_no_device_actions(
            self, navigator_cls, _ensure_dirs):
        worker = ScanFromCurrentWorker(
            Mock(),
            SimpleNamespace(regions=Mock()),
            Mock(),
        )
        finishes = []
        worker.finished.connect(finishes.append)

        worker.abort()
        worker.run()

        navigator_cls.assert_not_called()
        self.assertEqual([], finishes)


if __name__ == "__main__":
    unittest.main()
