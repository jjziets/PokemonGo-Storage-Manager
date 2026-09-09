import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pokemgr.indexer.multi_pass import MultiPassScanner, ScanPass


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

        scanner.nav.enter_search.assert_called_once_with("!shiny&!shadow")

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
        scanner.nav.detect_screen.return_value = "game_map"
        scanner.nav.navigate_to_appraisal.return_value = True

        self.assertTrue(scanner._prepare_first_appraisal("!shiny"))

        scanner.nav.open_first_appraisal.assert_not_called()
        scanner.nav.navigate_to_appraisal.assert_called_once_with("!shiny")

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
        scanner.nav.read_filtered_count.return_value = 2
        scanner._clear_and_search = Mock()
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
        scanner.nav.read_filtered_count.return_value = 10
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


if __name__ == "__main__":
    unittest.main()
