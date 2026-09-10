# TRACEWEAVER: file-role=scan-worker-control-tests; req=REQ-SCAN-003; trace=TRACE-SCAN-003; verifies=VER-SCAN-001
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from pokemgr.gui.workers import ScanFromCurrentWorker, ScanWorker


class ScanWorkerControlTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("pokemgr.gui.workers.ensure_dirs"))
        self.scanner_type = self.enterContext(patch("pokemgr.gui.workers.MultiPassScanner"))
        self.scanner = self.scanner_type.return_value
        self.scanner.passes = [Mock()]
        self.scanner._current_sm = None
        self.scanner._total_count = 0
        self.worker = ScanWorker(Mock(), SimpleNamespace(regions=Mock()), Mock())
        self.finished = []
        self.failed = []
        self.worker.finished.connect(self.finished.append)
        self.worker.failed.connect(self.failed.append)

    def during_construction(self, control):
        def construct(*_args):
            control()
            return self.scanner
        self.scanner_type.side_effect = construct

    def test_abort_during_construction_is_transferred_without_start(self):
        self.during_construction(self.worker.abort)

        self.worker.run()

        self.scanner.abort.assert_called_once_with()
        self.scanner.start.assert_not_called()
        self.assertEqual([], self.finished)
        self.assertEqual([], self.failed)

    def test_pause_before_start_reaches_scanner_before_navigation(self):
        self.worker.pause()

        self.worker.run()

        self.assertEqual([call.pause(), call.start()], self.scanner.mock_calls)
        self.assertEqual([0], self.finished)

    def test_pause_during_construction_is_retained(self):
        self.during_construction(self.worker.pause)

        self.worker.run()

        self.assertEqual([call.pause(), call.start()], self.scanner.mock_calls)

    def test_resume_during_construction_clears_earlier_pause(self):
        self.worker.pause()
        self.during_construction(self.worker.resume)

        self.worker.run()

        self.scanner.pause.assert_not_called()
        self.scanner.start.assert_called_once_with()

    def test_resume_during_pause_handoff_cannot_be_overwritten(self):
        self.worker.pause()
        self.scanner.pause.side_effect = self.worker.resume

        self.worker.run()

        self.assertEqual([call.pause(), call.resume(), call.start()], self.scanner.mock_calls)
        self.assertFalse(self.worker._pause_requested)

    def test_abort_during_pause_handoff_skips_start(self):
        self.worker.pause()
        self.scanner.pause.side_effect = self.worker.abort

        self.worker.run()

        self.assertEqual([call.pause(), call.abort()], self.scanner.mock_calls)
        self.scanner.start.assert_not_called()
        self.assertEqual([], self.finished)

    def test_pause_and_resume_between_passes_forward_without_current_machine(self):
        def between_passes():
            self.assertIsNone(self.scanner._current_sm)
            self.worker.pause()
            self.worker.resume()
        self.scanner.start.side_effect = between_passes

        self.worker.run()

        self.assertEqual([call.start(), call.pause(), call.resume()], self.scanner.mock_calls)
        self.assertEqual([0], self.finished)

    def test_stop_between_passes_wins_over_later_pause_and_resume(self):
        def between_passes():
            self.worker.pause()
            self.worker.abort()
            self.worker.resume()
            self.worker.pause()
        self.scanner.start.side_effect = between_passes

        self.worker.run()

        self.assertEqual([call.start(), call.pause(), call.abort()], self.scanner.mock_calls)
        self.assertFalse(self.worker._pause_requested)
        self.assertEqual([], self.finished)


class ScanWorkerFailureTests(unittest.TestCase):
    @patch("pokemgr.gui.workers.ensure_dirs")
    @patch("pokemgr.gui.workers.MultiPassScanner")
    def test_explicit_empty_queue_cannot_launch_default_passes(self, scanner_cls, _ensure_dirs):
        worker = ScanWorker(Mock(), SimpleNamespace(regions=Mock()), Mock())
        worker.selected_passes = []
        failures = []
        worker.failed.connect(failures.append)
        worker.run()
        scanner_cls.return_value.start.assert_not_called()
        self.assertEqual(["Explicit scan queue is empty; no default passes started"], failures)

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
