# TRACEWEAVER: file-role=checkpoint-recovery-tests; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
import unittest
from unittest.mock import Mock, call, patch

from PIL import Image

from pokemgr.indexer.snapshot import SnapshotDecision
from pokemgr.indexer.state_machine import IndexingStateMachine
from tests.test_stable_scan_loop import _ADB, _DB, accepted, profile


def exact(decision, frame):
    return decision, frame, "ok", "exact"


# TRACEWEAVER: verifies=VER-SCAN-001; req=REQ-SCAN-003; trace=TRACE-SCAN-003
class TransitionRetryTests(unittest.TestCase):
    def setUp(self):
        self.adb = _ADB()
        self.db = _DB()
        self.sm = IndexingStateMachine(self.adb, profile(), self.db)
        self.previous = accepted("Clawitzer", 2092, 128, (15, 12, 11))
        self.last = accepted("Necrozma", 2082, 139, (14, 13, 14))
        self.next = accepted("Zubat", 10, 12, (0, 12, 15))
        self.previous_frame = Image.new("RGB", (968, 2376), "gray")
        self.last_frame = Image.new("RGB", (968, 2376), "white")
        self.restored_capture = self.last_frame.copy()
        self.next_frame = Image.new("RGB", (968, 2376), "blue")
        self.sm.nav.detect_screen = Mock(return_value="appraisal")
        self.sm._fast_screencap = Mock(side_effect=[
            self.last_frame, self.previous_frame, self.restored_capture,
        ])
        self.sm._previous_validated_identity_key = self.previous.snapshot.identity_key
        self.sm._last_validated_identity_key = self.last.snapshot.identity_key
        self.sm._last_stable_image = self.last_frame
        self.sm.count = self.sm.visited_count = 2

    def checkpoint_reads(self):
        return [
            exact(self.previous, self.previous_frame),
            exact(self.last, self.last_frame),
            exact(self.next, self.next_frame),
        ]

    def test_reverse_and_restore_prove_position_before_one_forward_retry(self):
        self.sm._acquire_validated_snapshot = Mock(side_effect=self.checkpoint_reads())

        decision, frame, kind, _reason = self.sm._recover_failed_transition(self.last_frame)

        self.assertIs(decision, self.next)
        self.assertIs(frame, self.next_frame)
        self.assertEqual("ok", kind)
        r = self.sm.regions
        reverse = (*r.swipe_end, *r.swipe_start, r.swipe_duration_ms)
        forward = (*r.swipe_start, *r.swipe_end, r.swipe_duration_ms)
        self.assertEqual([reverse, forward, forward], [action[1] for action in self.adb.actions])
        self.assertTrue(all(action[0] == "swipe" and action[2] == {"jitter": 0}
                            for action in self.adb.actions))
        self.assertEqual([
            call(require_transition=False, save_failure_evidence=False),
            call(require_transition=False, save_failure_evidence=False),
            call(previous_accepted=self.restored_capture, require_transition=True),
        ], self.sm._acquire_validated_snapshot.call_args_list)
        self.assertIs(self.sm._last_stable_image, self.restored_capture)
        self.assertEqual(self.previous.snapshot.identity_key,
                         self.sm._previous_validated_identity_key)
        self.assertEqual(self.last.snapshot.identity_key, self.sm._last_validated_identity_key)
        self.assertEqual((2, 2, 0), (self.sm.count, self.sm.visited_count, self.sm.skipped_count))
        self.assertEqual([], self.db.rows)

    def test_unseen_identical_neighbour_fails_reverse_checkpoint_without_forward(self):
        self.sm._acquire_validated_snapshot = Mock(
            return_value=exact(self.last, self.last_frame),
        )

        decision, _frame, kind, reason = self.sm._recover_failed_transition(self.last_frame)

        self.assertIsNone(decision)
        self.assertEqual("transition_retry_failed", kind)
        self.assertIn("previous checkpoint did not match", reason)
        self.assertEqual(1, len(self.adb.actions))
        self.assertEqual(
            [call(require_transition=False, save_failure_evidence=False)] * 3,
            self.sm._acquire_validated_snapshot.call_args_list,
        )
        self.assertEqual((2, 2, 0), (self.sm.count, self.sm.visited_count,
                                    self.sm.skipped_count))
        self.assertEqual([], self.db.rows)

    def test_delayed_expected_checkpoint_retries_reads_without_replaying_swipe(self):
        for phase in (0, 1):
            for wrong_reads in (1, 2):
                with self.subTest(phase=phase, wrong_reads=wrong_reads):
                    self.setUp()
                    reads = self.checkpoint_reads()
                    wrong = reads[1 - phase]
                    reads[phase:phase] = [wrong] * wrong_reads
                    self.sm._acquire_validated_snapshot = Mock(side_effect=reads)

                    decision, frame, kind, _reason = (
                        self.sm._recover_failed_transition(self.last_frame)
                    )

                    self.assertIs(decision, self.next)
                    self.assertIs(frame, self.next_frame)
                    self.assertEqual("ok", kind)
                    r = self.sm.regions
                    reverse = (*r.swipe_end, *r.swipe_start, r.swipe_duration_ms)
                    forward = (*r.swipe_start, *r.swipe_end, r.swipe_duration_ms)
                    self.assertEqual([reverse, forward, forward],
                                     [action[1] for action in self.adb.actions])
                    self.assertEqual(
                        [call(require_transition=False, save_failure_evidence=False)]
                        * (2 + wrong_reads)
                        + [call(previous_accepted=self.restored_capture,
                                require_transition=True)],
                        self.sm._acquire_validated_snapshot.call_args_list,
                    )
                    self.assertEqual((2, 2, 0), (self.sm.count, self.sm.visited_count,
                                                self.sm.skipped_count))
                    self.assertEqual([], self.db.rows)
                    self.assertEqual(self.previous.snapshot.identity_key,
                                     self.sm._previous_validated_identity_key)
                    self.assertEqual(self.last.snapshot.identity_key,
                                     self.sm._last_validated_identity_key)

    def test_checkpoint_failure_does_not_save_evidence_as_pending_position(self):
        snapshot = self.previous.snapshot
        self.sm._wait_for_stable_appraisal = Mock(
            return_value=(self.previous_frame, "stable"),
        )
        self.sm._read_appraisal_snapshot = Mock(
            return_value=(snapshot.as_detail(), snapshot.as_appraisal()),
        )
        self.sm._validate_appraisal_snapshot = Mock(
            return_value=SnapshotDecision(False, "ambiguous checkpoint"),
        )
        self.sm._cp_recovery_identity = Mock(return_value=None)
        self.sm._save_failed_appraisal = Mock()

        for save_evidence in (False, True):
            with self.subTest(save_failure_evidence=save_evidence):
                self.sm._save_failed_appraisal.reset_mock()
                _decision, _frame, kind, _reason = self.sm._acquire_validated_snapshot(
                    save_failure_evidence=save_evidence,
                )

                self.assertEqual("invalid", kind)
                if save_evidence:
                    self.sm._save_failed_appraisal.assert_called_once_with(
                        self.previous_frame, "ambiguous checkpoint",
                    )
                else:
                    self.sm._save_failed_appraisal.assert_not_called()

    def test_changed_restore_checkpoint_stops_before_forward_retry(self):
        reads = [exact(self.previous, self.previous_frame)] + [
            exact(self.next, self.next_frame),
        ] * 3
        self.sm._acquire_validated_snapshot = Mock(side_effect=reads)

        decision, _frame, kind, reason = self.sm._recover_failed_transition(self.last_frame)

        self.assertIsNone(decision)
        self.assertEqual("transition_retry_failed", kind)
        self.assertIn("restored checkpoint did not match", reason)
        self.assertEqual(2, len(self.adb.actions))
        self.assertEqual(4, self.sm._acquire_validated_snapshot.call_count)
        self.assertEqual((2, 2, 0), (self.sm.count, self.sm.visited_count,
                                    self.sm.skipped_count))
        self.assertEqual([], self.db.rows)

    def test_reacquire_retries_only_the_current_phase_after_resume(self):
        for phase in range(3):
            with self.subTest(phase=phase):
                self.setUp()
                reads = self.checkpoint_reads()
                reads.insert(phase, (None, None, "reacquire", "pause invalidated read"))
                gestures_during_read = []

                def acquire(**_kwargs):
                    index = len(gestures_during_read)
                    gestures_during_read.append(len(self.adb.actions))
                    if index == phase:
                        self.sm.pause()
                    return reads[index]

                sleeps = []

                def wait_for_resume(_seconds):
                    self.assertTrue(self.sm._paused)
                    self.assertEqual(phase + 1, len(self.adb.actions))
                    self.assertEqual(phase + 1, len(gestures_during_read))
                    sleeps.append(_seconds)
                    if len(sleeps) == 2:
                        self.sm.resume()

                self.sm._acquire_validated_snapshot = Mock(side_effect=acquire)
                with patch("pokemgr.indexer.state_machine.time.sleep",
                           side_effect=wait_for_resume):
                    decision, frame, kind, _reason = (
                        self.sm._recover_failed_transition(self.last_frame)
                    )

                self.assertIs(decision, self.next)
                self.assertIs(frame, self.next_frame)
                self.assertEqual("ok", kind)
                self.assertEqual(2, len(sleeps))
                expected_gestures = [1, 2, 3]
                expected_gestures.insert(phase, phase + 1)
                self.assertEqual(expected_gestures, gestures_during_read)
                expected_calls = [
                    call(require_transition=False, save_failure_evidence=False),
                    call(require_transition=False, save_failure_evidence=False),
                    call(previous_accepted=self.restored_capture, require_transition=True),
                ]
                expected_calls.insert(phase, expected_calls[phase])
                self.assertEqual(expected_calls,
                                 self.sm._acquire_validated_snapshot.call_args_list)
                self.assertEqual(3, len(self.adb.actions))
                self.assertEqual([], self.db.rows)

    def test_reacquire_exhaustion_never_replays_a_phase_gesture(self):
        for phase in range(3):
            with self.subTest(phase=phase):
                self.setUp()
                reads = self.checkpoint_reads()[:phase] + [
                    (None, None, "reacquire", "read invalidated"),
                ] * 3
                self.sm._acquire_validated_snapshot = Mock(side_effect=reads)

                decision, _frame, kind, _reason = (
                    self.sm._recover_failed_transition(self.last_frame)
                )

                self.assertIsNone(decision)
                self.assertEqual("transition_retry_failed", kind)
                self.assertEqual(phase + 1, len(self.adb.actions))
                self.assertEqual(phase + 3,
                                 self.sm._acquire_validated_snapshot.call_count)
                self.assertEqual([], self.db.rows)
                self.assertEqual((2, 2, 0), (self.sm.count, self.sm.visited_count,
                                            self.sm.skipped_count))

    def test_pause_resume_during_nominal_success_discards_that_read_in_every_phase(self):
        for phase in range(3):
            with self.subTest(phase=phase):
                self.setUp()
                reads = self.checkpoint_reads()
                # For checkpoints, even an exact expected tuple is invalidated.
                # For the final read, stale prior-row evidence must not be
                # returned as the next row simply because acquisition said ok.
                interrupted_result = reads[phase] if phase < 2 else reads[1]
                reads.insert(phase, interrupted_result)
                gestures_during_read = []

                def acquire(**_kwargs):
                    index = len(gestures_during_read)
                    gestures_during_read.append(len(self.adb.actions))
                    if index == phase:
                        self.sm.pause()
                        self.sm.resume()
                    return reads[index]

                self.sm._acquire_validated_snapshot = Mock(side_effect=acquire)

                decision, frame, kind, _reason = (
                    self.sm._recover_failed_transition(self.last_frame)
                )

                self.assertIs(decision, self.next)
                self.assertIs(frame, self.next_frame)
                self.assertEqual("ok", kind)
                expected_gestures = [1, 2, 3]
                expected_gestures.insert(phase, phase + 1)
                self.assertEqual(expected_gestures, gestures_during_read)
                expected_calls = [
                    call(require_transition=False, save_failure_evidence=False),
                    call(require_transition=False, save_failure_evidence=False),
                    call(previous_accepted=self.restored_capture, require_transition=True),
                ]
                expected_calls.insert(phase, expected_calls[phase])
                self.assertEqual(expected_calls,
                                 self.sm._acquire_validated_snapshot.call_args_list)
                self.assertEqual(3, len(self.adb.actions))
                self.assertEqual([], self.db.rows)

    def test_mismatch_and_reacquire_share_the_three_read_checkpoint_budget(self):
        self.sm._acquire_validated_snapshot = Mock(side_effect=[
            exact(self.last, self.last_frame),
            (None, None, "reacquire", "read invalidated"),
            exact(self.last, self.last_frame),
            exact(self.previous, self.previous_frame),
        ])

        decision, _frame, kind, _reason = (
            self.sm._recover_failed_transition(self.last_frame)
        )

        self.assertIsNone(decision)
        self.assertEqual("transition_retry_failed", kind)
        self.assertEqual(3, self.sm._acquire_validated_snapshot.call_count)
        self.assertEqual(1, len(self.adb.actions))
        self.assertEqual([], self.db.rows)

    def test_abort_while_waiting_to_reacquire_sends_no_later_input(self):
        for phase in range(3):
            with self.subTest(phase=phase):
                self.setUp()
                reads = self.checkpoint_reads()

                def acquire(**_kwargs):
                    index = self.sm._acquire_validated_snapshot.call_count - 1
                    if index == phase:
                        self.sm.pause()
                        return None, None, "reacquire", "pause invalidated read"
                    return reads[index]

                self.sm._acquire_validated_snapshot = Mock(side_effect=acquire)
                with patch("pokemgr.indexer.state_machine.time.sleep",
                           side_effect=lambda _seconds: self.sm.abort()) as sleep:
                    decision, _frame, kind, _reason = (
                        self.sm._recover_failed_transition(self.last_frame)
                    )

                self.assertIsNone(decision)
                self.assertEqual("aborted", kind)
                sleep.assert_called_once()
                self.assertEqual(phase + 1, len(self.adb.actions))
                self.assertEqual(phase + 1,
                                 self.sm._acquire_validated_snapshot.call_count)
                self.assertEqual([], self.db.rows)

    def test_abort_during_mismatch_reread_sends_no_later_input(self):
        for phase in (0, 1):
            with self.subTest(phase=phase):
                self.setUp()
                reads = self.checkpoint_reads()
                reads.insert(phase, reads[1 - phase])

                def acquire(**_kwargs):
                    index = self.sm._acquire_validated_snapshot.call_count - 1
                    if index == phase + 1:
                        self.sm.abort()
                    return reads[index]

                self.sm._acquire_validated_snapshot = Mock(side_effect=acquire)

                decision, _frame, kind, _reason = (
                    self.sm._recover_failed_transition(self.last_frame)
                )

                self.assertIsNone(decision)
                self.assertEqual("aborted", kind)
                self.assertEqual(phase + 1, len(self.adb.actions))
                self.assertEqual(phase + 2,
                                 self.sm._acquire_validated_snapshot.call_count)
                self.assertEqual([], self.db.rows)

    def test_missing_equal_or_incomplete_history_never_sends_input(self):
        previous_key = self.previous.snapshot.identity_key
        last_key = self.last.snapshot.identity_key
        for earlier, latest in (
            (None, last_key), (last_key, last_key), (previous_key, None),
            (("Clawitzer", 2092, -1, 15, 12, 11), last_key),
            (("Clawitzer", 2092, 128, 16, 12, 11), last_key),
        ):
            with self.subTest(previous=earlier, last=latest):
                self.sm._previous_validated_identity_key = earlier
                self.sm._last_validated_identity_key = latest
                self.sm._acquire_validated_snapshot = Mock()

                decision, _frame, kind, reason = self.sm._recover_failed_transition(self.last_frame)

                self.assertIsNone(decision)
                self.assertEqual("transition_retry_failed", kind)
                self.assertIn("could not confirm advance", reason)
                self.assertIn("previous checkpoint", reason)
                self.assertEqual([], self.adb.actions)
                self.sm._fast_screencap.assert_not_called()
                self.sm._acquire_validated_snapshot.assert_not_called()

    def test_abort_during_each_checkpoint_or_final_read_sends_no_later_gesture(self):
        for abort_at in range(3):
            with self.subTest(abort_at=abort_at):
                self.setUp()
                reads = self.checkpoint_reads()
                call_index = 0

                def acquire(**_kwargs):
                    nonlocal call_index
                    index = call_index
                    call_index += 1
                    if index == abort_at:
                        self.sm.abort()
                    return reads[index]

                self.sm._acquire_validated_snapshot = Mock(side_effect=acquire)

                decision, _frame, kind, _reason = self.sm._recover_failed_transition(self.last_frame)

                self.assertIsNone(decision)
                self.assertEqual("aborted", kind)
                self.assertEqual(abort_at + 1, len(self.adb.actions))
                self.assertEqual(abort_at + 1, call_index)
                self.assertEqual([], self.db.rows)

    def test_abort_before_recovery_sends_no_gestures(self):
        self.sm.abort()

        decision, _frame, kind, _reason = self.sm._recover_failed_transition(self.last_frame)

        self.assertIsNone(decision)
        self.assertEqual("aborted", kind)
        self.assertEqual([], self.adb.actions)
        self.sm._fast_screencap.assert_not_called()

    def test_lost_screen_before_each_gesture_stops_without_that_gesture(self):
        for lost_at in range(3):
            with self.subTest(lost_at=lost_at):
                self.setUp()
                self.sm.nav.detect_screen = Mock(
                    side_effect=["appraisal"] * lost_at + ["game_map"],
                )
                self.sm._acquire_validated_snapshot = Mock(side_effect=self.checkpoint_reads())

                decision, _frame, kind, _reason = self.sm._recover_failed_transition(self.last_frame)

                self.assertIsNone(decision)
                self.assertEqual("transition_retry_failed", kind)
                self.assertEqual(lost_at, len(self.adb.actions))
                self.assertEqual(lost_at, self.sm._acquire_validated_snapshot.call_count)

    def test_inexact_checkpoint_stops_and_final_failure_never_recurses(self):
        for failed_at in range(3):
            with self.subTest(failed_at=failed_at):
                self.setUp()
                reads = self.checkpoint_reads()
                reads[failed_at] = (
                    None, self.last_frame, "transition_returned_to_previous", "same tuple",
                )
                self.sm._acquire_validated_snapshot = Mock(side_effect=reads)

                decision, _frame, kind, _reason = self.sm._recover_failed_transition(self.last_frame)

                self.assertIsNone(decision)
                self.assertEqual("transition_retry_failed", kind)
                self.assertEqual(failed_at + 1, len(self.adb.actions))
                self.assertEqual(failed_at + 1, self.sm._acquire_validated_snapshot.call_count)
                self.assertEqual([], self.db.rows)

    def test_invalid_reread_stops_without_using_remaining_checkpoint_budget(self):
        for failure_kind in ("invalid", "lost_appraisal_game_map"):
            with self.subTest(failure_kind=failure_kind):
                self.setUp()
                self.sm._acquire_validated_snapshot = Mock(side_effect=[
                    exact(self.last, self.last_frame),
                    (None, self.last_frame, failure_kind, "unconfirmed appraisal"),
                    exact(self.previous, self.previous_frame),
                ])

                decision, _frame, kind, _reason = (
                    self.sm._recover_failed_transition(self.last_frame)
                )

                self.assertIsNone(decision)
                self.assertEqual("transition_retry_failed", kind)
                self.assertEqual(2, self.sm._acquire_validated_snapshot.call_count)
                self.assertEqual(1, len(self.adb.actions))
                self.assertEqual([], self.db.rows)

    def test_accepted_visible_cp_without_complete_checkpoint_identity_stops(self):
        incomplete = accepted("Clawitzer", 2092, -1, (15, 12, 11))
        self.sm._acquire_validated_snapshot = Mock(
            return_value=exact(incomplete, self.previous_frame),
        )

        decision, _frame, kind, _reason = (
            self.sm._recover_failed_transition(self.last_frame)
        )

        self.assertIsNone(decision)
        self.assertEqual("transition_retry_failed", kind)
        self.assertEqual(1, self.sm._acquire_validated_snapshot.call_count)
        self.assertEqual(1, len(self.adb.actions))
        self.assertEqual([], self.db.rows)

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_scan_loop_stores_recovered_next_row_once_without_counting_checkpoints(self, _paddle):
        self.sm.count = self.sm.visited_count = 0
        self.sm._previous_validated_identity_key = self.sm._last_validated_identity_key = None
        self.sm._fast_screencap = Mock(return_value=self.last_frame)
        self.sm._acquire_validated_snapshot = Mock(side_effect=[
            exact(self.previous, self.previous_frame),
            exact(self.last, self.last_frame),
            (None, self.last_frame, "transition_returned_to_previous", "same tuple"),
            *self.checkpoint_reads(),
        ])

        self.sm.start(expected_total=3)

        self.assertEqual([2092, 2082, 10], [row[0].cp for row in self.db.rows])
        self.assertEqual([0, 1, 2], [row[2] for row in self.db.rows])
        self.assertEqual((3, 3, 0), (self.sm.count, self.sm.visited_count, self.sm.skipped_count))
        self.assertEqual(6, self.sm._acquire_validated_snapshot.call_count)
        self.assertEqual(5, len(self.adb.actions))
        self.assertEqual(self.last.snapshot.identity_key, self.sm._previous_validated_identity_key)
        self.assertEqual(self.next.snapshot.identity_key, self.sm._last_validated_identity_key)

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_quagsire_larvesta_delayed_checkpoints_preserve_scan_positions(self, _paddle):
        # Same CP is not the checkpoint identity: both HP and IVs differ.
        quagsire = accepted("Quagsire", 832, 116, (13, 14, 10))
        larvesta = accepted("Larvesta", 832, 94, (12, 15, 12))
        self.sm.count = self.sm.visited_count = 0
        self.sm._previous_validated_identity_key = self.sm._last_validated_identity_key = None
        self.sm._fast_screencap = Mock(return_value=self.last_frame)
        self.sm._acquire_validated_snapshot = Mock(side_effect=[
            exact(quagsire, self.previous_frame),
            exact(larvesta, self.last_frame),
            (None, self.last_frame, "transition_returned_to_previous", "same tuple"),
            exact(larvesta, self.last_frame),  # Reverse has not settled yet.
            exact(quagsire, self.previous_frame),
            exact(quagsire, self.previous_frame),  # Restore also arrives late.
            exact(larvesta, self.last_frame),
            exact(self.next, self.next_frame),
        ])

        self.sm.start(expected_total=3)

        self.assertEqual([("Quagsire", 832), ("Larvesta", 832), ("Zubat", 10)],
                         [(row[0].species, row[0].cp) for row in self.db.rows])
        self.assertEqual([0, 1, 2], [row[2] for row in self.db.rows])
        self.assertEqual((3, 3, 0), (self.sm.count, self.sm.visited_count,
                                    self.sm.skipped_count))
        self.assertEqual(8, self.sm._acquire_validated_snapshot.call_count)
        r = self.sm.regions
        reverse = (*r.swipe_end, *r.swipe_start, r.swipe_duration_ms)
        forward = (*r.swipe_start, *r.swipe_end, r.swipe_duration_ms)
        self.assertEqual([forward, forward, reverse, forward, forward],
                         [action[1] for action in self.adb.actions])
        self.assertEqual(larvesta.snapshot.identity_key,
                         self.sm._previous_validated_identity_key)
        self.assertEqual(self.next.snapshot.identity_key,
                         self.sm._last_validated_identity_key)

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_scan_loop_stops_after_failed_retry_without_duplicate_rows(self, _paddle):
        self.sm.count = self.sm.visited_count = 0
        self.sm._previous_validated_identity_key = self.sm._last_validated_identity_key = None
        self.sm._fast_screencap = Mock(return_value=self.last_frame)
        bounce = None, self.last_frame, "transition_returned_to_previous", "same tuple"
        self.sm._acquire_validated_snapshot = Mock(side_effect=[
            exact(self.previous, self.previous_frame), exact(self.last, self.last_frame),
            bounce, *self.checkpoint_reads()[:2], bounce,
        ])

        with self.assertRaisesRegex(RuntimeError, "single failed-swipe retry"):
            self.sm.start(expected_total=3)

        self.assertEqual([2092, 2082], [row[0].cp for row in self.db.rows])
        self.assertEqual((2, 2, 0), (self.sm.count, self.sm.visited_count, self.sm.skipped_count))
        self.assertEqual(6, self.sm._acquire_validated_snapshot.call_count)
        self.assertEqual(5, len(self.adb.actions))

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_skip_invalidates_both_checkpoints_before_the_next_accepted_row(self, _paddle):
        self.sm._favorite_unresolved_snapshot = Mock(side_effect=lambda frame: frame)
        self.sm.count = self.sm.visited_count = 0
        self.sm._previous_validated_identity_key = self.sm._last_validated_identity_key = None
        self.sm._acquire_validated_snapshot = Mock(side_effect=[
            exact(self.previous, self.previous_frame), exact(self.last, self.last_frame),
            (None, self.last_frame, "invalid", "ambiguous"), exact(self.next, self.next_frame),
        ])
        histories = []

        def advance():
            histories.append((self.sm._previous_validated_identity_key,
                              self.sm._last_validated_identity_key))
            return True

        self.sm._advance_from_confirmed_appraisal = Mock(side_effect=advance)

        self.sm.start(expected_total=4)

        self.assertEqual((None, None), histories[-1])
        self.assertIsNone(self.sm._previous_validated_identity_key)
        self.assertEqual(self.next.snapshot.identity_key, self.sm._last_validated_identity_key)
        self.assertEqual([0, 1, 3], [row[2] for row in self.db.rows])


if __name__ == "__main__":
    unittest.main()
