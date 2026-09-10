"""Only independent rereads can recover a pre-input identity mismatch."""

from dataclasses import replace
import unittest
from unittest.mock import Mock, call, patch

from pokemgr.execution.executor import _ReacquireAction
from pokemgr.indexer.snapshot import SnapshotDecision
from tests import test_action_preswipe_retry as fixtures


class ActionPrestarRetryTests(unittest.TestCase):
    def setUp(self):
        fixtures.ActionPreswipeRetryTests.setUp(self)
        self.reader = self.executor.reader
        self.adb.has_stream_frames = True
        self.executor._record_star = Mock(wraps=self.executor._record_star)
        self.enterContext(patch("pokemgr.execution.executor.favorite_state",
                                side_effect=lambda image, _region: image.info["star"]))

    def image(self, sequence, *, read=None, star="off", **kwargs):
        image = fixtures.sourced_frame(read or self.original, sequence, **kwargs)
        image.info["star"] = star
        return image

    def run_star(self, *images, **kwargs):
        self.scanner._fast_screencap = Mock(side_effect=images)
        return self.executor._set_star(self.original, self.completed, True, **kwargs)

    def assert_no_input(self):
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()
        self.executor._record_star.assert_not_called()
        self.assertEqual([], self.db.mock_calls)

    def test_matching_first_read_keeps_fast_path_and_one_readback(self):
        changed, final = self.run_star(self.image(2), self.image(3, star="on"))
        self.assertTrue(changed)
        self.assertEqual(2, self.scanner._fast_screencap.call_count)
        self.adb.tap.assert_called_once()
        self.executor._record_star.assert_called_once()
        self.sleep.assert_not_called()

    def test_transient_bad_hp_recovers_with_two_new_reads_and_one_tap(self):
        wrong = replace(self.original, hp=self.original.hp + 1)
        with self.assertLogs("pokemgr.execution.executor", level="INFO") as captured:
            changed, _ = self.run_star(self.image(2, read=wrong), self.image(3),
                                       self.image(4), self.image(5, star="on"))
        self.assertTrue(changed)
        self.adb.tap.assert_called_once()
        self.executor._record_star.assert_called_once()
        self.assertEqual([call(.1), call(.1)], self.sleep.call_args_list)
        self.assertIn("hp", " ".join(captured.output))
        self.scanner._save_failed_appraisal.assert_not_called()

    def test_one_good_read_after_two_bad_reads_cannot_authorize_star(self):
        wrong = replace(self.original, hp=self.original.hp + 1)
        with self.assertRaisesRegex(RuntimeError, "no two fresh matching reads"):
            self.run_star(self.image(2, read=wrong), self.image(3, read=wrong), self.image(4))
        self.assert_no_input()
        self.assertEqual(3, self.scanner._save_failed_appraisal.call_count)
        self.assertEqual("action_before_star_mismatch",
                         self.scanner._save_failed_appraisal.call_args.kwargs["phase"])

    def test_persistent_field_changes_remain_held_and_diagnosed(self):
        for changes in ({"hp": 99}, {"atk": 1}, {"caught_species": "Raichu"},
                        {"shiny": True}, {"read_complete": False}):
            with self.subTest(changes=changes):
                wrong = replace(self.original, **changes)
                with self.assertRaisesRegex(RuntimeError, "appraisal identity changed"), \
                        self.assertLogs("pokemgr.execution.executor", level="WARNING") as logs:
                    self.run_star(*(self.image(seq, read=wrong) for seq in (2, 3, 4)))
                self.assertIn(next(iter(changes)), " ".join(logs.output))
                self.assertIn("region_diffs", " ".join(logs.output))
                self.assert_no_input()

    def test_reused_or_copied_source_cannot_form_recovery_pair(self):
        for copy in (False, True):
            with self.subTest(copy=copy):
                match = self.image(3)
                with self.assertRaisesRegex(RuntimeError, "appraisal identity changed"):
                    self.run_star(self.image(2, read=replace(self.original, hp=99)),
                                  match, match.copy() if copy else match)
                self.assert_no_input()

    def test_failed_source_cannot_become_new_evidence_when_ocr_changes(self):
        failure = self.image(2, read=replace(self.original, hp=99))
        same_source = self.image(2)
        with self.assertRaisesRegex(RuntimeError, "appraisal identity changed"):
            self.run_star(failure, same_source, self.image(3))
        self.assert_no_input()

    def test_backward_or_different_clock_source_cannot_form_recovery_pair(self):
        for key, value in (("pokemgr_stream_pts_us", 250_000),
                           ("pokemgr_source_clock_generation", 0),
                           ("pokemgr_source_clock_continuity", "b" * 32),
                           ("pokemgr_stream_sequence", 2)):
            with self.subTest(key=key):
                last = self.image(4)
                last.info[key] = value
                with self.assertRaisesRegex(RuntimeError, "appraisal identity changed"):
                    self.run_star(self.image(2, read=replace(self.original, hp=99)),
                                  self.image(3), last)
                self.assert_no_input()

    def test_compatible_clock_refresh_can_confirm_new_frames(self):
        last = self.image(4)
        last.info["pokemgr_source_clock_generation"] = 2
        changed, _ = self.run_star(self.image(2, read=replace(self.original, hp=99)),
                                  self.image(3), last, self.image(5, star="on"))
        self.assertTrue(changed)
        self.adb.tap.assert_called_once()

    def test_moving_bars_cannot_confirm_identical_rounded_ivs(self):
        self.reader.appraisal_bars_stable = Mock(return_value=False)
        with self.assertRaisesRegex(RuntimeError, "appraisal identity changed"):
            self.run_star(self.image(2, read=replace(self.original, hp=99)),
                          self.image(3), self.image(4))
        self.assert_no_input()

    def test_newest_unknown_star_cannot_inherit_earlier_on_state(self):
        with self.assertRaisesRegex(RuntimeError, "star is unreadable"):
            self.run_star(self.image(2, read=replace(self.original, hp=99)),
                          self.image(3, star="on"), self.image(4, star="unknown"))
        self.assert_no_input()

    def test_legacy_copied_or_overlapping_capture_cannot_form_retry_pair(self):
        for kind in ("copied", "overlap", "missing", "nan", "initial-copy"):
            with self.subTest(kind=kind):
                initial = self.completed.copy()
                failure = self.image(2, read=replace(self.original, hp=99))
                first, last = self.image(3), self.image(4)
                for image in (initial, failure, first, last):
                    for key in list(image.info):
                        if key.startswith("pokemgr_stream_") or key.startswith("pokemgr_source_"):
                            del image.info[key]
                if kind == "copied":
                    last = first.copy()
                elif kind == "overlap":
                    last.info["pokemgr_capture_started_at"] = 3.005
                elif kind == "missing":
                    del last.info["pokemgr_capture_started_at"]
                elif kind == "nan":
                    last.info["pokemgr_capture_finished_at"] = float("nan")
                else:
                    failure, first = initial, initial.copy()
                with self.assertRaisesRegex(RuntimeError, "appraisal identity changed"):
                    self.scanner._fast_screencap = Mock(side_effect=[failure, first, last])
                    self.executor._set_star(self.original, initial, True)
                self.assert_no_input()

    def test_legacy_independent_capture_intervals_can_recover(self):
        images = [self.image(2, read=replace(self.original, hp=99)), self.image(3),
                  self.image(4), self.image(5, star="on")]
        initial = self.completed.copy()
        for image in [initial, *images]:
            for key in list(image.info):
                if key.startswith("pokemgr_stream_") or key.startswith("pokemgr_source_"):
                    del image.info[key]
        self.scanner._fast_screencap = Mock(side_effect=images)
        changed, _ = self.executor._set_star(self.original, initial, True)
        self.assertTrue(changed)
        self.adb.tap.assert_called_once()

    def test_pause_during_retry_delay_reacquires_without_input(self):
        self.sleep.side_effect = lambda *_: self.executor.pause()
        with self.assertRaises(_ReacquireAction):
            self.run_star(self.image(2, read=replace(self.original, hp=99)), self.image(3))
        self.assertEqual(1, self.scanner._fast_screencap.call_count)
        self.assert_no_input()

    def test_abort_during_retry_delay_stops_without_more_frames(self):
        self.sleep.side_effect = lambda *_: self.executor.abort()
        changed, _ = self.run_star(self.image(2, read=replace(self.original, hp=99)), self.image(3))
        self.assertFalse(changed)
        self.assertEqual(1, self.scanner._fast_screencap.call_count)
        self.assert_no_input()

    def test_cp_conflict_is_still_checked_after_recovery_even_for_on_star(self):
        decision = SnapshotDecision(True, "exact", self.original, cp_source="screen")
        contradiction = replace(decision, snapshot=replace(self.original, cp=501))
        self.reader.read_cp = Mock(return_value=(501, .99))
        with patch("pokemgr.execution.executor.validate_snapshot", return_value=contradiction), \
                self.assertRaisesRegex(RuntimeError, "visible CP changed"):
            self.run_star(self.image(2, read=replace(self.original, hp=99)),
                          self.image(3, star="on"), self.image(4, star="on"), cp_decision=decision)
        self.reader.read_cp.assert_called_once()
        self.assert_no_input()

    def test_pause_in_cp_guard_cannot_tap_or_save(self):
        decision = SnapshotDecision(True, "exact", self.original, cp_source="screen")
        self.reader.read_cp = Mock(side_effect=lambda _: (self.executor.pause() or -1, 0))
        with self.assertRaises(_ReacquireAction):
            self.run_star(self.image(2), cp_decision=decision)
        self.assert_no_input()


if __name__ == "__main__":
    unittest.main()
