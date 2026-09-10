import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pokemgr.indexer.multi_pass import MultiPassScanner, ScanPass

# TRACEWEAVER: file-role=verified-pass-coverage-tests; req=REQ-SCAN-003; trace=TRACE-SCAN-003; verifies=VER-SCAN-001


class MultiPassFailureTests(unittest.TestCase):
    def test_filter_entry_routes_through_calibrated_navigator(self):
        scanner = MultiPassScanner(
            Mock(),
            SimpleNamespace(regions=SimpleNamespace(
                screen_width=1440,
                screen_height=2304,
            )),
            Mock(),
        )
        scanner.nav.enter_search = Mock()

        scanner._clear_and_search("!shiny&!shadow")

        scanner.nav.enter_search.assert_called_once_with("!shiny&!shadow", verify=True)

    def test_first_appraisal_uses_menu_only_from_confirmed_detail(self):
        scanner = MultiPassScanner(
            Mock(),
            SimpleNamespace(regions=SimpleNamespace(
                screen_width=968,
                screen_height=2376,
            )),
            Mock(),
        )
        scanner.nav = Mock()
        scanner.nav.detect_screen.side_effect = ["detail", "appraisal"]

        self.assertTrue(scanner._prepare_first_appraisal("!shiny"))

        scanner.nav.tap_first_pokemon.assert_called_once_with()
        scanner.nav.open_first_appraisal.assert_called_once_with()
        scanner.nav.navigate_to_appraisal.assert_not_called()

    def test_first_appraisal_recovers_map_without_blind_menu_taps(self):
        scanner = MultiPassScanner(
            Mock(),
            SimpleNamespace(regions=SimpleNamespace(
                screen_width=968,
                screen_height=2376,
            )),
            Mock(),
        )
        scanner.nav = Mock()
        scanner.nav.detect_screen.side_effect = ["game_map", "detail", "appraisal"]
        scanner.nav.read_filtered_count_verified.return_value = 2
        scanner._pass_filtered_count = 2

        self.assertTrue(scanner._prepare_first_appraisal("!shiny"))

        scanner.nav.open_first_appraisal.assert_called_once_with()
        scanner.nav.navigate_to_storage.assert_called_once_with()
        scanner.nav.enter_search.assert_called_once_with("!shiny", verify=True)
        scanner.nav.navigate_to_appraisal.assert_not_called()

    @patch("pokemgr.scan_logger.stop_scan_log")
    @patch("pokemgr.scan_logger.start_scan_log", return_value="test.log")
    def test_unexpected_pass_failure_stops_before_next_filter(
            self, _start_log, stop_log):
        scanner = MultiPassScanner(
            Mock(),
            SimpleNamespace(regions=SimpleNamespace(
                screen_width=1440,
                screen_height=2304,
            )),
            Mock(),
        )
        scanner.passes = [
            ScanPass("Normal", "!shiny", {}),
            ScanPass("Shiny", "shiny", {"shiny": True}),
        ]
        scanner._run_pass = Mock(side_effect=RuntimeError("PNG decode race"))
        scanner.on_error = Mock()
        scanner.on_pass_end = Mock()
        scanner.on_finished = Mock()

        with self.assertRaisesRegex(
                RuntimeError,
                "Pass Normal failed; scan stopped: PNG decode race"):
            scanner.start()

        scanner._run_pass.assert_called_once_with(scanner.passes[0])
        scanner.on_error.assert_not_called()
        scanner.on_pass_end.assert_not_called()
        scanner.on_finished.assert_not_called()
        stop_log.assert_called_once_with()

    @patch("pokemgr.indexer.multi_pass.IndexingStateMachine")
    def test_failed_tagged_pass_tags_partial_rows_before_raising(
            self, state_machine_cls):
        scanner = MultiPassScanner(
            Mock(),
            SimpleNamespace(regions=SimpleNamespace(
                screen_width=1440,
                screen_height=2304,
            )),
            Mock(),
        )
        scanner.nav = Mock()
        scanner.nav.navigate_to_storage.return_value = True
        scanner.nav.read_filtered_count_verified.return_value = 2
        scanner._clear_and_search = Mock()
        scanner._prepare_first_appraisal = Mock(return_value=True)
        scanner._apply_tags_to_session = Mock()

        sm = state_machine_cls.return_value
        sm.count = 2
        sm.skipped_count = 0
        sm.session_id = "partial-session"
        sm.start.side_effect = RuntimeError("OCR crashed")
        tags = {"shiny": True}

        with self.assertRaisesRegex(RuntimeError, "OCR crashed"):
            scanner._run_pass(ScanPass("Shiny", "shiny", tags))

        scanner._apply_tags_to_session.assert_called_once_with(
            "partial-session", tags)
        self.assertIsNone(scanner._current_sm)

    @patch("pokemgr.indexer.multi_pass.IndexingStateMachine")
    def test_resume_count_targets_new_positions_after_skipped_prefix(
            self, state_machine_cls):
        scanner = MultiPassScanner(
            Mock(),
            SimpleNamespace(regions=SimpleNamespace(
                screen_width=968,
                screen_height=2376,
            )),
            Mock(),
        )
        scanner.nav = Mock()
        scanner.nav.navigate_to_storage.return_value = True
        scanner.nav.read_filtered_count_verified.return_value = 10
        scanner._clear_and_search = Mock()
        scanner._prepare_first_appraisal = Mock(return_value=True)
        scanner.skip_first_n = 3
        scanner.skip_delay = 0.27
        scanner.resume_target_species = "Zorua"
        scanner.resume_target_cp = 882
        scanner.max_per_pass = 4
        scanner.on_pass_count = Mock()

        sm = state_machine_cls.return_value
        sm.count = 4
        sm.visited_count = 4
        sm.skipped_count = 0
        sm.session_id = "resume-session"

        count = scanner._run_pass(ScanPass("Normal", "!shiny", {}))

        self.assertEqual(4, count)
        scanner.on_pass_count.assert_called_once_with(4)
        self.assertEqual(3, sm.skip_first_n)
        self.assertEqual(0.27, sm.skip_delay)
        self.assertEqual("Zorua", sm.resume_target_species)
        self.assertEqual(882, sm.resume_target_cp)
        sm.start.assert_called_once_with(expected_total=4)
        self.assertEqual(0, scanner.skip_first_n)


class VerifiedPassCoverageTests(unittest.TestCase):
    def setUp(self):
        self.scanner = MultiPassScanner(
            Mock(), SimpleNamespace(regions=SimpleNamespace(
                screen_width=968, screen_height=2376)), Mock())
        self.scanner.nav = Mock()
        self.scanner.nav.enter_search.return_value = True
        self.scanner.nav.navigate_to_storage.return_value = True
        self.scanner._prepare_first_appraisal = Mock(return_value=True)
        self.scanner.on_pass_count = Mock()
        self.scan_pass = ScanPass('Shiny', 'shiny', {'shiny': True})

    @patch('pokemgr.indexer.multi_pass.IndexingStateMachine')
    def test_unknown_count_holds_without_card_or_state_machine(self, sm_cls):
        for count in (None, False, -1, 10001):
            with self.subTest(count=count):
                self.scanner.nav.read_filtered_count_verified.return_value = count
                with self.assertRaisesRegex(RuntimeError, 'count could not be verified'):
                    self.scanner._run_pass(self.scan_pass)
        self.scanner._prepare_first_appraisal.assert_not_called()
        sm_cls.assert_not_called()

    @patch('pokemgr.indexer.multi_pass.IndexingStateMachine')
    def test_verified_zero_skips_even_with_cap_without_opening_card(self, sm_cls):
        self.scanner.nav.read_filtered_count_verified.return_value = 0
        self.scanner.max_per_pass = 5
        self.assertEqual(0, self.scanner._run_pass(self.scan_pass))
        self.scanner.on_pass_count.assert_called_once_with(0)
        self.scanner._prepare_first_appraisal.assert_not_called()
        sm_cls.assert_not_called()

    @patch('pokemgr.indexer.multi_pass.IndexingStateMachine')
    def test_failed_full_search_does_not_read_count_or_open_card(self, sm_cls):
        self.scanner.nav.enter_search.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'search text could not be verified'):
            self.scanner._run_pass(self.scan_pass)
        self.scanner.nav.read_filtered_count_verified.assert_not_called()
        self.scanner._prepare_first_appraisal.assert_not_called()
        sm_cls.assert_not_called()

    @patch('pokemgr.indexer.multi_pass.IndexingStateMachine')
    def test_early_return_preserves_tags_but_cannot_claim_completion(self, sm_cls):
        self.scanner.nav.read_filtered_count_verified.return_value = 5
        sm = sm_cls.return_value
        sm.count = sm.visited_count = 3
        sm.skipped_count = 0
        sm.session_id = 'partial'
        self.scanner._apply_tags_to_session = Mock()
        with self.assertRaisesRegex(RuntimeError, 'verified 3 of 5 positions'):
            self.scanner._run_pass(self.scan_pass)
        self.scanner._apply_tags_to_session.assert_called_once_with('partial', {'shiny': True})
        self.assertEqual(1, self.scanner.nav.navigate_to_storage.call_count)

    @patch('pokemgr.indexer.multi_pass.IndexingStateMachine')
    def test_full_coverage_includes_verified_skips_and_keeps_counts_separate(self, sm_cls):
        self.scanner.nav.read_filtered_count_verified.return_value = 5
        sm = sm_cls.return_value
        sm.count, sm.visited_count, sm.skipped_count = 3, 5, 2
        sm.session_id = 'covered'
        self.assertEqual(3, self.scanner._run_pass(self.scan_pass))
        sm.start.assert_called_once_with(expected_total=5)
        self.assertEqual(2, self.scanner._total_skipped)

    def test_recovered_filter_count_change_never_opens_another_card(self):
        del self.scanner._prepare_first_appraisal
        self.scanner._pass_filtered_count = 5
        self.scanner.nav.read_filtered_count_verified.return_value = 4
        self.scanner.nav.detect_screen.return_value = 'game_map'
        self.assertFalse(self.scanner._prepare_first_appraisal('shiny'))
        self.scanner.nav.tap_first_pokemon.assert_called_once_with()
        self.scanner.nav.open_first_appraisal.assert_not_called()

    @patch('pokemgr.indexer.multi_pass.IndexingStateMachine')
    def test_stop_during_final_appraisal_detection_never_starts_scan(self, sm_cls):
        del self.scanner._prepare_first_appraisal
        self.scanner.nav.read_filtered_count_verified.return_value = 2
        def last_detection():
            self.scanner.abort()
            return 'appraisal'
        self.scanner.nav.detect_screen.side_effect = last_detection
        self.assertEqual(0, self.scanner._run_pass(self.scan_pass))
        sm_cls.assert_not_called()

    @patch('pokemgr.indexer.multi_pass.IndexingStateMachine')
    def test_stop_during_machine_construction_transfers_abort_and_closes_reader(self, sm_cls):
        self.scanner.nav.read_filtered_count_verified.return_value = 2
        sm = Mock(count=0, visited_count=0, skipped_count=0)
        def construct(*_args):
            self.scanner.abort()
            return sm
        sm_cls.side_effect = construct
        self.assertEqual(0, self.scanner._run_pass(self.scan_pass))
        sm.start.assert_not_called()
        sm.abort.assert_called_once_with()
        sm._close_reader.assert_called_once_with()
        self.assertIsNone(self.scanner._current_sm)

    @patch('pokemgr.indexer.multi_pass.IndexingStateMachine')
    def test_stop_during_return_to_storage_remains_cancellation(self, sm_cls):
        self.scanner.nav.read_filtered_count_verified.return_value = 2
        sm = sm_cls.return_value
        sm.count = sm.visited_count = 2
        sm.skipped_count = 0
        calls = []
        def navigate():
            calls.append(True)
            if len(calls) == 2:
                self.scanner.abort()
                return False
            return True
        self.scanner.nav.navigate_to_storage.side_effect = navigate
        self.assertEqual(2, self.scanner._run_pass(self.scan_pass))
        self.assertTrue(self.scanner._abort)

    def test_navigation_pause_holds_input_until_resume(self):
        scanner = MultiPassScanner(Mock(), SimpleNamespace(regions=SimpleNamespace(
            screen_width=968, screen_height=2376, storage_first_item=None)), Mock())
        scanner.pause()
        def resume(_seconds):
            scanner.adb.tap.assert_not_called()
            scanner.resume()
        with patch('pokemgr.indexer.multi_pass.time.sleep', side_effect=resume), patch(
                'pokemgr.adb.navigator.human_delay'):
            self.assertTrue(scanner.nav.tap_first_pokemon())
        scanner.adb.tap.assert_called_once()

    def test_stop_releases_navigation_pause_without_sending_input(self):
        scanner = MultiPassScanner(Mock(), SimpleNamespace(regions=SimpleNamespace(
            screen_width=968, screen_height=2376)), Mock())
        scanner.pause()
        with patch('pokemgr.indexer.multi_pass.time.sleep', side_effect=lambda _seconds: scanner.abort()):
            self.assertFalse(scanner.nav.tap_first_pokemon())
        scanner.adb.tap.assert_not_called()
        scanner.resume()
        self.assertTrue(scanner._navigation_cancelled())

    def test_pause_during_storage_detection_requires_a_fresh_screen_after_resume(self):
        scanner = MultiPassScanner(Mock(), SimpleNamespace(regions=SimpleNamespace(
            screen_width=968, screen_height=2376)), Mock())
        screens = iter(('detail', 'storage'))
        def detect():
            screen = next(screens)
            if screen == 'detail':
                scanner.pause()
            return screen
        scanner.nav.detect_screen = Mock(side_effect=detect)
        def resume(_seconds):
            scanner.adb.key_event.assert_not_called()
            scanner.adb.tap.assert_not_called()
            scanner.resume()
        with patch('pokemgr.indexer.multi_pass.time.sleep', side_effect=resume):
            self.assertTrue(scanner.nav.navigate_to_storage())
        self.assertEqual(2, scanner.nav.detect_screen.call_count)
        scanner.adb.key_event.assert_not_called()
        scanner.adb.tap.assert_not_called()

    def test_stop_after_paused_storage_detection_prevents_branch_input(self):
        scanner = MultiPassScanner(Mock(), SimpleNamespace(regions=SimpleNamespace(
            screen_width=968, screen_height=2376)), Mock())
        def detect():
            scanner.pause()
            return 'detail'
        scanner.nav.detect_screen = Mock(side_effect=detect)
        with patch('pokemgr.indexer.multi_pass.time.sleep', side_effect=lambda _seconds: scanner.abort()):
            self.assertFalse(scanner.nav.navigate_to_storage())
        scanner.adb.key_event.assert_not_called()
        scanner.adb.tap.assert_not_called()

    @patch('pokemgr.indexer.multi_pass.IndexingStateMachine')
    def test_pause_during_construction_reopens_setup_after_resume(self, sm_cls):
        self.scanner.nav.read_filtered_count_verified.return_value = 2
        stale = Mock(count=0, visited_count=0, skipped_count=0)
        sm = Mock(count=2, visited_count=2, skipped_count=0)
        def construct(*_args):
            if sm_cls.call_count == 1:
                self.scanner.pause()
                return stale
            return sm
        sm_cls.side_effect = construct
        def resume(_seconds):
            sm.start.assert_not_called()
            stale.start.assert_not_called()
            stale._close_reader.assert_called_once_with()
            self.scanner.resume()
        with patch('pokemgr.indexer.multi_pass.time.sleep', side_effect=resume):
            self.assertEqual(2, self.scanner._run_pass(self.scan_pass))
        self.assertEqual(2, self.scanner.nav.enter_search.call_count)
        stale.start.assert_not_called()
        sm.start.assert_called_once_with(expected_total=2)

    @patch('pokemgr.scan_logger.stop_scan_log')
    @patch('pokemgr.scan_logger.start_scan_log', return_value='test.log')
    def test_pause_between_empty_partitions_holds_next_pass(self, *_mocks):
        self.scanner.passes = [self.scan_pass, self.scan_pass]
        self.scanner._run_pass = Mock(return_value=0)
        self.scanner.on_pass_end = lambda i, _count: self.scanner.pause() if i == 0 else None
        waits = []
        def resume_if_paused(_seconds):
            if self.scanner._paused:
                self.assertEqual(1, self.scanner._run_pass.call_count)
                waits.append(True)
                self.scanner.resume()
        with patch('pokemgr.indexer.multi_pass.time.sleep', side_effect=resume_if_paused):
            self.scanner.start()
        self.assertEqual([True], waits)
        self.assertEqual(2, self.scanner._run_pass.call_count)

    @patch('pokemgr.scan_logger.stop_scan_log')
    @patch('pokemgr.scan_logger.start_scan_log', return_value='test.log')
    @patch('pokemgr.indexer.multi_pass.time.sleep')
    @patch('pokemgr.indexer.multi_pass.IndexingStateMachine')
    def test_empty_partition_continues_to_nonempty_with_exact_target(self, sm_cls, *_mocks):
        self.scanner.passes = [ScanPass('Empty', 'shadow', {'shadow': True}), self.scan_pass]
        self.scanner.nav.read_filtered_count_verified.side_effect = [0, 2]
        sm = sm_cls.return_value
        sm.count = sm.visited_count = 2
        sm.skipped_count = 0
        self.scanner.on_pass_end = Mock()
        self.scanner.start()
        sm_cls.assert_called_once()
        sm.start.assert_called_once_with(expected_total=2)
        self.assertEqual([(0, 0), (1, 2)],
                         [c.args for c in self.scanner.on_pass_end.call_args_list])


if __name__ == "__main__":
    unittest.main()
