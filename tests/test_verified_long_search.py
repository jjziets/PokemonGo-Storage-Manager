# TRACEWEAVER: file-role=verified-chunked-search-tests; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
"""Long storage searches must retain exact prefix and context before every input."""

import unittest
from unittest.mock import Mock, call, patch

from pokemgr.adb.navigator import GameNavigator
from tests.test_navigator_recovery import _tablet_regions


QUERY = "!shiny&!shadow&!dynamax&!gigantamax&!lucky&cp1-9000" + "&!cp100" * 64
QUERY = QUERY[:499]


class VerifiedLongSearchTests(unittest.TestCase):
    def setUp(self):
        self.adb = Mock()
        self.text = ""
        self.screen = "storage"
        self.cancelled = False
        self.generation = 0
        self.readings = []
        self.on_read = None
        self.on_type = None
        self.nav = GameNavigator(
            self.adb, _tablet_regions(), cancelled=lambda: self.cancelled,
            observation_generation=lambda: self.generation)
        self.nav.detect_screen = Mock(side_effect=lambda: self.screen)
        self.adb.input_text.side_effect = self.type_text
        self.adb.key_event.side_effect = self.key_event
        self.read = self.enterContext(patch(
            "pokemgr.adb.search_text.read_search_text", side_effect=self.read_text))
        self.enterContext(patch("pokemgr.adb.navigator.human_delay"))
        self.enterContext(patch("pokemgr.adb.navigator.time.sleep"))

    def type_text(self, chunk):
        self.text += chunk
        if self.on_type:
            self.on_type(chunk)

    def key_event(self, code):
        if code == 67:
            self.text = self.text[:-1]

    def read_text(self, _adb):
        value = self.text
        self.readings.append(value)
        return self.on_read(value) if self.on_read else value

    def assert_held(self, expected_chunks):
        self.assertFalse(self.nav.enter_search(QUERY, verify=True))
        self.assertEqual(expected_chunks, self.adb.input_text.call_count)
        self.assertNotIn(call(66), self.adb.key_event.call_args_list)

    def test_complete_499_character_query_checks_every_prefix_then_final_full_text(self):
        self.assertEqual(499, len(QUERY))

        self.assertTrue(self.nav.enter_search(QUERY, verify=True))

        self.assertEqual([call(QUERY[i:i + 80]) for i in range(0, 499, 80)],
                         self.adb.input_text.call_args_list)
        expected_reads = ["", ""]
        for offset in range(0, 499, 80):
            expected_reads.extend((QUERY[:offset], QUERY[:offset + 80]))
        self.assertEqual(expected_reads + [QUERY], self.readings)
        self.assertEqual(QUERY, self.text)
        self.assertEqual([call(123), call(66)], self.adb.key_event.call_args_list)

    def test_truncated_third_chunk_at_229_characters_stops_without_appending_or_enter(self):
        def truncate(_chunk):
            if self.adb.input_text.call_count == 3:
                self.text = self.text[:229]
        self.on_type = truncate

        self.assert_held(3)
        self.assertEqual(229, len(self.text))

    def test_wrong_chunk_text_stops_without_retry_or_enter(self):
        def change(_chunk):
            if self.adb.input_text.call_count == 2:
                self.text = self.text[:-1] + "X"
        self.on_type = change

        self.assert_held(2)

    def test_prefix_is_checked_again_before_appending_next_chunk(self):
        def replace_after_read(value):
            if len(self.readings) == 4:
                self.text = "different editor text"
            return value
        self.on_read = replace_after_read

        self.assert_held(1)

    def test_missing_chunk_readback_stops_without_further_input(self):
        self.on_read = lambda value: None if self.adb.input_text.call_count else value

        self.assert_held(1)

    def test_cancel_during_chunk_readback_prevents_next_chunk(self):
        def cancel(value):
            if self.adb.input_text.call_count:
                self.cancelled = True
            return value
        self.on_read = cancel

        self.assert_held(1)

    def test_pause_resume_generation_during_chunk_readback_prevents_next_chunk(self):
        def pause_resume(value):
            if self.adb.input_text.call_count:
                self.generation += 1
            return value
        self.on_read = pause_resume

        self.assert_held(1)

    def test_generation_change_before_first_chunk_prevents_typing(self):
        def pause_resume(value):
            if len(self.readings) == 3:
                self.generation += 1
            return value
        self.on_read = pause_resume

        self.assert_held(0)

    def test_lost_storage_after_matching_prefix_does_not_type_into_other_editor(self):
        def switch_editor(value):
            if len(self.readings) == 3:
                self.screen = "detail"
            return value
        self.on_read = switch_editor

        self.assert_held(0)

    def test_lost_storage_between_chunks_prevents_next_chunk(self):
        def switch_editor(value):
            if len(self.readings) == 4:
                self.screen = "detail"
            return value
        self.on_read = switch_editor

        self.assert_held(1)

    def test_final_full_query_readback_is_still_required(self):
        def change_final(value):
            if value == QUERY and self.readings.count(QUERY) == 2:
                return QUERY[:-1]
            return value
        self.on_read = change_final

        self.assert_held(7)

    def test_generation_change_during_final_readback_prevents_enter(self):
        def pause_resume(value):
            if value == QUERY and self.readings.count(QUERY) == 2:
                self.generation += 1
            return value
        self.on_read = pause_resume

        self.assert_held(7)

    def test_query_above_native_complete_read_limit_has_no_navigation_or_text_input(self):
        self.assertFalse(self.nav.enter_search("x" * 513, verify=True))

        self.adb.tap.assert_not_called()
        self.adb.input_text.assert_not_called()
        self.adb.key_event.assert_not_called()
        self.read.assert_not_called()

    def test_80_character_query_keeps_existing_short_fast_path(self):
        query = QUERY[:80]

        self.assertTrue(self.nav.enter_search(query, verify=True))

        self.adb.input_text.assert_called_once_with(query)
        self.assertEqual(["", "", query], self.readings)
        self.assertEqual(5, self.nav.detect_screen.call_count)

    def test_unverified_long_query_keeps_legacy_input_path(self):
        self.assertTrue(self.nav.enter_search(QUERY))

        self.adb.input_text.assert_called_once_with(QUERY)
        self.read.assert_not_called()
        self.assertEqual([call(123), *[call(67)] * 40, call(66)],
                         self.adb.key_event.call_args_list)


if __name__ == "__main__":
    unittest.main()
