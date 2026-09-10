"""A paused setup cannot lend filter/count authority to a different list."""

# TRACEWEAVER: file-role=pass-setup-epoch-tests; req=REQ-SCAN-003,REQ-DATA-001; trace=TRACE-SCAN-003,TRACE-DATA-001; verifies=VER-SCAN-001
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from pokemgr.indexer.multi_pass import MultiPassScanner, ScanPass
from tests.test_stable_scan_loop import profile
from tests.test_verified_filtered_count import capture


class MultiPassSetupEpochTests(unittest.TestCase):
    def setUp(self):
        self.adb, self.db = Mock(), Mock()
        self.scanner = MultiPassScanner(self.adb, profile(), self.db)
        self.scanner.nav = Mock()
        self.scanner.nav.navigate_to_storage.return_value = True
        self.scanner.nav.enter_search.return_value = True
        self.scanner.nav.read_filtered_count_verified.return_value = 2
        self.scanner._prepare_first_appraisal = Mock(return_value=True)
        self.scanner._apply_tags_to_session = Mock()
        self.pass_ = ScanPass("Shiny", "shiny", {"shiny": True})
        self.sm = Mock(count=2, visited_count=2, skipped_count=0, session_id="fresh")
        self.construct = self.enterContext(patch(
            "pokemgr.indexer.multi_pass.IndexingStateMachine", return_value=self.sm))

    def pause_resume(self):
        self.scanner.pause()
        self.scanner.resume()

    def assert_reapplied(self):
        self.assertEqual([call("shiny", verify=True)] * 2,
                         self.scanner.nav.enter_search.call_args_list)
        self.sm.start.assert_called_once_with(expected_total=2)
        self.scanner._apply_tags_to_session.assert_called_once_with("fresh", {"shiny": True})
        self.assertIsNone(self.scanner._setup_generation)
        self.assertIsNone(self.scanner._current_sm)
        self.assertEqual([], self.db.mock_calls)

    def test_same_count_after_pause_reapplies_query_before_any_card(self):
        events = []
        self.scanner.nav.enter_search.side_effect = lambda *_args, **_kwargs: events.append("query") or True
        def count():
            events.append("count")
            if len(events) == 2:
                self.pause_resume()
            return 2
        self.scanner.nav.read_filtered_count_verified.side_effect = count
        self.scanner._prepare_first_appraisal.side_effect = lambda *_args: events.append("open") or True
        self.sm.start.side_effect = lambda **_kwargs: events.append("start")

        self.assertEqual(2, self.scanner._run_pass(self.pass_))

        self.assertEqual(["query", "count", "query", "count", "open", "start"], events)
        self.assert_reapplied()

    def test_paused_zero_count_cannot_finish_an_empty_partition(self):
        def count():
            if self.scanner.nav.read_filtered_count_verified.call_count == 1:
                self.pause_resume()
                return 0
            return 2
        self.scanner.nav.read_filtered_count_verified.side_effect = count
        self.scanner.on_pass_count = Mock()

        self.assertEqual(2, self.scanner._run_pass(self.pass_))

        self.scanner.on_pass_count.assert_called_once_with(2)
        self.assert_reapplied()

    def test_pause_after_filter_count_or_first_appraisal_repeats_setup(self):
        for stage in ("query", "count_callback", "appraisal"):
            with self.subTest(stage=stage):
                fixture = MultiPassSetupEpochTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                callback = {"query": fixture.scanner.nav.enter_search,
                            "count_callback": Mock(),
                            "appraisal": fixture.scanner._prepare_first_appraisal}[stage]
                if stage == "count_callback":
                    fixture.scanner.on_pass_count = callback
                def once(*_args, **_kwargs):
                    if callback.call_count == 1:
                        fixture.pause_resume()
                    return True
                callback.side_effect = once

                self.assertEqual(2, fixture.scanner._run_pass(fixture.pass_))

                fixture.assert_reapplied()
                fixture.construct.assert_called_once()

    def test_paused_reader_construction_is_closed_and_resume_offset_preserved(self):
        stale = Mock(count=0, visited_count=0, skipped_count=0)
        self.scanner.skip_first_n = 3
        self.scanner.resume_target_species = "Pikachu"
        self.scanner.resume_target_cp = 500
        self.scanner.nav.read_filtered_count_verified.return_value = 5
        def construct(*_args):
            if self.construct.call_count == 1:
                self.pause_resume()
                return stale
            return self.sm
        self.construct.side_effect = construct

        self.assertEqual(2, self.scanner._run_pass(self.pass_))

        stale._close_reader.assert_called_once()
        stale.start.assert_not_called()
        self.assertEqual((3, "Pikachu", 500),
                         (self.sm.skip_first_n, self.sm.resume_target_species, self.sm.resume_target_cp))
        self.assertEqual(0, self.scanner.skip_first_n)
        self.assert_reapplied()

    def test_pause_after_machine_attachment_closes_it_without_tags_or_skip_counts(self):
        stale = Mock(count=0, visited_count=0, skipped_count=0)
        self.construct.side_effect = [stale, self.sm]
        original_cancelled = self.scanner._navigation_cancelled
        def invalidated():
            if self.scanner._current_sm is stale:
                self.pause_resume()
            return original_cancelled()
        self.scanner._navigation_cancelled = invalidated

        self.assertEqual(2, self.scanner._run_pass(self.pass_))

        stale.start.assert_not_called()
        stale._close_reader.assert_called_once()
        self.assertEqual(0, self.scanner._total_skipped)
        self.assert_reapplied()

    def test_pause_waits_for_resume_before_reapplying_filter(self):
        def count():
            if self.scanner.nav.read_filtered_count_verified.call_count == 1:
                self.scanner.pause()
            return 2
        self.scanner.nav.read_filtered_count_verified.side_effect = count
        def resume(_seconds):
            self.assertEqual(1, self.scanner.nav.enter_search.call_count)
            self.construct.assert_not_called()
            self.scanner.resume()

        with patch("pokemgr.indexer.multi_pass.time.sleep", side_effect=resume) as wait:
            self.assertEqual(2, self.scanner._run_pass(self.pass_))

        wait.assert_called_once()
        self.assert_reapplied()

    def test_abort_after_invalidated_count_never_reopens_or_tags(self):
        def count():
            self.scanner.pause()
            return 2
        self.scanner.nav.read_filtered_count_verified.side_effect = count
        with patch("pokemgr.indexer.multi_pass.time.sleep", side_effect=lambda _seconds: self.scanner.abort()):
            self.assertEqual(0, self.scanner._run_pass(self.pass_))
        self.scanner.nav.enter_search.assert_called_once_with("shiny", verify=True)
        self.scanner._prepare_first_appraisal.assert_not_called()
        self.construct.assert_not_called()
        self.scanner._apply_tags_to_session.assert_not_called()

    def test_unrelated_setup_error_is_not_retried(self):
        self.scanner.nav.read_filtered_count_verified.return_value = None
        with self.assertRaisesRegex(RuntimeError, "count could not be verified"):
            self.scanner._run_pass(self.pass_)
        self.scanner.nav.enter_search.assert_called_once_with("shiny", verify=True)
        self.construct.assert_not_called()

    def test_pause_then_failure_after_scan_starts_preserves_partial_rows_without_replay(self):
        def failed_start(**_kwargs):
            self.pause_resume()
            raise RuntimeError("reader failed after stored rows")
        self.sm.start.side_effect = failed_start

        with self.assertRaisesRegex(RuntimeError, "reader failed after stored rows"):
            self.scanner._run_pass(self.pass_)

        self.scanner.nav.enter_search.assert_called_once_with("shiny", verify=True)
        self.sm.start.assert_called_once_with(expected_total=2)
        self.scanner._apply_tags_to_session.assert_called_once_with("fresh", {"shiny": True})


class SetupNavigationEpochTests(unittest.TestCase):
    def setUp(self):
        self.adb = Mock()
        self.scanner = MultiPassScanner(self.adb, profile(), Mock())
        self.scanner._setup_generation = self.scanner._pause_generation
        self.scanner.nav.detect_screen = Mock(return_value="storage")

    def test_short_query_navigation_unwinds_at_pause_without_typing_or_enter(self):
        self.adb.tap.side_effect = lambda *_args, **_kwargs: self.scanner.pause()
        with patch("pokemgr.adb.navigator.human_delay"), patch("pokemgr.indexer.multi_pass.time.sleep") as wait:
            self.assertFalse(self.scanner.nav.enter_search("shiny", verify=True))
        self.adb.tap.assert_called_once()
        self.adb.input_text.assert_not_called()
        self.adb.key_event.assert_not_called()
        wait.assert_not_called()

    def test_count_pair_across_pause_epoch_is_not_accepted(self):
        self.adb.screencap.side_effect = [capture(1), capture(2)]
        def ocr(*_args, **_kwargs):
            self.scanner.pause()
            self.scanner.resume()
            return "Q(2)"
        with patch("pytesseract.image_to_string", side_effect=ocr), patch("pokemgr.adb.navigator.time.sleep"):
            self.assertIsNone(self.scanner.nav.read_filtered_count_verified())
        self.adb.screencap.assert_called_once()
        self.adb.tap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
