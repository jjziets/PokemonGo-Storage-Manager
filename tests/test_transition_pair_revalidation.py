"""Unsettled transition proof can be replaced without replaying phone input."""

# TRACEWEAVER: file-role=transition-pair-revalidation-tests; req=REQ-SCAN-003,REQ-SCAN-004,REQ-STREAM-001; trace=TRACE-SCAN-003; verifies=VER-SCAN-001

from dataclasses import replace
import unittest
from unittest.mock import Mock, call, patch

from PIL import Image

from pokemgr.indexer.snapshot import SnapshotDecision
from pokemgr.indexer.state_machine import IndexingStateMachine
from tests.test_stable_scan_loop import _ADB, _DB, accepted, profile


class TransitionPairRevalidationTests(unittest.TestCase):
    def setUp(self):
        self.adb, self.db = _ADB(), _DB()
        self.sm = IndexingStateMachine(self.adb, profile(), self.db)
        self.expected = replace(accepted("Kabuto", 763, 83, (14, 13, 12)), cp_source="screen")
        self.previous_key = accepted("Kabuto", 780, 84, (15, 13, 12)).snapshot.identity_key
        self.sm._last_validated_identity_key = self.previous_key
        self.sm._previous_validated_identity_key = accepted().snapshot.identity_key
        self.initial = self.frame(5)
        self.confirmation = self.frame(6, color="gray")
        self.previous_frame = self.frame(1)
        self.sm._last_accepted_image = self.previous_frame
        self.sm._last_stable_image = self.previous_frame
        self.sm.count = self.sm.visited_count = 32
        self.sm.nav.detect_screen = Mock(side_effect=lambda image: image.info.get("screen", "appraisal"))
        self.sm.reader.are_bars_visible = Mock(side_effect=lambda image: image.info.get("bars", True))
        self.sm.reader.appraisal_bars_stable = Mock(return_value=True)
        self.sm._read_appraisal_snapshot = Mock(side_effect=self.read)
        self.sm._validate_appraisal_snapshot = Mock(side_effect=lambda _snapshot, image: image.info["decision"])
        self.sm._recover_cp_with_model_taps = Mock(side_effect=AssertionError("replayed recovery input"))
        self.sm._wait_for_stable_appraisal = Mock(return_value=(self.initial, "stable_transition_unobserved"))
        self.sm._save_failed_appraisal = Mock()
        self.enterContext(patch("pokemgr.indexer.state_machine.human_delay"))
        self.enterContext(patch("pokemgr.indexer.state_machine.time.monotonic", return_value=10.0))

    def frame(self, start, *, color="white", stream=None, **info):
        image = Image.new("RGB", (96, 237), color)
        image.info.update(pokemgr_capture_started_at=start, pokemgr_capture_finished_at=start + .1,
                          decision=self.expected)
        if stream:
            session, sequence, generation = stream
            image.info.update(pokemgr_stream_session=session, pokemgr_stream_sequence=sequence,
                              pokemgr_stream_pts_us=sequence * 1000,
                              pokemgr_source_clock_generation=generation)
        image.info.update(info)
        return image

    def read(self, image):
        snapshot = image.info["decision"].snapshot or self.expected.snapshot
        return snapshot.as_detail(), snapshot.as_appraisal()

    def acquire(self, pairs):
        self.sm._fast_screencap = Mock(side_effect=[self.confirmation, *pairs])
        result = self.sm._acquire_validated_snapshot(previous_accepted=self.previous_frame,
                                                    require_transition=True)
        self.assertEqual([], self.adb.actions)
        self.assertEqual([], self.db.rows)
        self.assertEqual((32, 32, 0), (self.sm.count, self.sm.visited_count, self.sm.skipped_count))
        self.assertEqual(self.previous_key, self.sm._last_validated_identity_key)
        self.assertIs(self.previous_frame, self.sm._last_stable_image)
        self.assertIs(self.previous_frame, self.sm._last_accepted_image)
        self.sm._recover_cp_with_model_taps.assert_not_called()
        self.sm._wait_for_stable_appraisal.assert_called_once_with(
            previous_accepted=self.previous_frame, require_transition=True, allow_structured_fallback=True,
        )
        self.sm._save_failed_appraisal.assert_not_called()
        return result

    def test_unsettled_pair_is_discarded_and_two_new_exact_reads_confirm(self):
        pair = [self.frame(20), self.frame(21)]
        result = self.acquire(pair)
        self.assertEqual("ok", result[2])
        self.assertIs(pair[-1], result[1])
        self.assertEqual([call(self.initial), *map(call, pair)], self.sm._read_appraisal_snapshot.call_args_list)
        self.assertEqual(3, self.sm._fast_screencap.call_count)
        self.assertIsNone(self.sm._settled_frame_pair)

    def test_native_to_jpeg_pair_is_replaced_even_when_pixels_match(self):
        self.initial.info.update(self.frame(5, stream=("native", 1, 1)).info)
        self.confirmation = self.frame(6)
        pair = [self.frame(20), self.frame(21)]
        result = self.acquire(pair)
        self.assertEqual("ok", result[2])
        self.assertEqual(3, self.sm._fast_screencap.call_count)
        self.assertEqual([call(self.initial), *map(call, pair)], self.sm._read_appraisal_snapshot.call_args_list)

    def test_fresh_internal_native_pair_and_continuous_clock_refresh_are_supported(self):
        for refresh in (False, True):
            with self.subTest(refresh=refresh):
                self.setUp()
                first = self.frame(20, stream=("new-session", 20, 3))
                second = self.frame(21, stream=("new-session", 21, 4 if refresh else 3))
                if refresh:
                    for image in (first, second):
                        image.info["pokemgr_source_clock_continuity"] = "a" * 32
                result = self.acquire([first, second])
                self.assertEqual("ok", result[2])

    def test_three_full_pair_attempts_are_bounded_and_do_not_reuse_a_failed_image(self):
        pairs = [self.frame(20), self.frame(21, color="gray"),
                 self.frame(22, stream=("s", 1, 1)), self.frame(23),
                 self.frame(24), self.frame(25)]
        result = self.acquire(pairs)
        self.assertEqual("ok", result[2])
        self.assertIs(pairs[-1], result[1])
        self.assertEqual(7, self.sm._fast_screencap.call_count)
        self.assertEqual([call(self.initial), call(pairs[-2]), call(pairs[-1])],
                         self.sm._read_appraisal_snapshot.call_args_list)

    def test_unsettled_after_three_pairs_holds_without_ocr_or_input(self):
        pairs = [self.frame(20 + index, color="gray" if index % 2 else "white") for index in range(6)]
        result = self.acquire(pairs)
        self.assertEqual("transition_identity_inconsistent", result[2])
        self.assertIn("after 3 read attempts", result[3])
        self.assertEqual(7, self.sm._fast_screencap.call_count)
        self.sm._read_appraisal_snapshot.assert_called_once_with(self.initial)

    def test_moving_iv_bars_discard_pair_before_a_fully_settled_pair_is_read(self):
        self.sm.reader.appraisal_bars_stable.side_effect = [False, True]
        pairs = [self.frame(20 + index) for index in range(4)]
        result = self.acquire(pairs)
        self.assertEqual("ok", result[2])
        self.assertIs(pairs[-1], result[1])
        self.assertEqual([call(self.initial), call(pairs[-2]), call(pairs[-1])],
                         self.sm._read_appraisal_snapshot.call_args_list)

    def test_persistent_iv_bar_motion_cannot_confirm_even_with_unchanged_rounded_ivs(self):
        self.sm.reader.appraisal_bars_stable.return_value = False
        result = self.acquire([self.frame(20 + index) for index in range(6)])
        self.assertEqual("transition_identity_inconsistent", result[2])
        self.assertEqual(3, self.sm.reader.appraisal_bars_stable.call_count)
        self.sm._read_appraisal_snapshot.assert_called_once_with(self.initial)

    def test_invalid_source_or_capture_receipts_cannot_confirm(self):
        cases = ("duplicate_object", "discarded_object", "before_request", "overlap", "nan",
                 "missing_receipt", "mixed_sources", "replayed_sequence", "changed_session",
                 "changed_generation", "invalid_continuity", "geometry")
        for case in cases:
            with self.subTest(case=case):
                self.setUp()
                first, second = self.frame(20), self.frame(21)
                if case == "duplicate_object": second = first
                elif case == "discarded_object": first = self.initial
                elif case == "before_request": first.info["pokemgr_capture_started_at"] = 9
                elif case == "overlap": second.info["pokemgr_capture_started_at"] = 20.05
                elif case == "nan": first.info["pokemgr_capture_started_at"] = float("nan")
                elif case == "missing_receipt": del second.info["pokemgr_capture_finished_at"]
                elif case == "geometry": second = second.resize((97, 237))
                else:
                    first = self.frame(20, stream=("s", 10, 1))
                    second = self.frame(21, stream=("s", 11, 1))
                    if case == "mixed_sources": second = self.frame(21)
                    elif case == "replayed_sequence": second.info["pokemgr_stream_sequence"] = 10
                    elif case == "changed_session": second.info["pokemgr_stream_session"] = "other"
                    elif case == "changed_generation": second.info["pokemgr_source_clock_generation"] = 2
                    elif case == "invalid_continuity": first.info["pokemgr_source_clock_continuity"] = "invalid"
                result = self.acquire([first, second] * 3)
                self.assertEqual("transition_identity_inconsistent", result[2])
                self.assertIn("after 3 read attempts", result[3])
                self.sm._read_appraisal_snapshot.assert_called_once_with(self.initial)

    def test_complete_tuple_conflict_holds_immediately_without_searching_for_agreement(self):
        for field in ("detected_species", "cp", "hp", "atk", "def_", "sta"):
            for index in (0, 1):
                with self.subTest(field=field, index=index):
                    self.setUp()
                    pair = [self.frame(20), self.frame(21)]
                    value = "Omanyte" if field == "detected_species" else getattr(self.expected.snapshot, field) + 1
                    pair[index].info["decision"] = replace(self.expected, snapshot=replace(self.expected.snapshot, **{field: value}))
                    result = self.acquire(pair)
                    self.assertEqual("transition_identity_inconsistent", result[2])
                    self.assertIn("did not agree", result[3])
                    self.assertEqual(3, self.sm._fast_screencap.call_count)

    def test_incomplete_new_tuple_is_held(self):
        pair = [self.frame(20), self.frame(21, decision=SnapshotDecision(False, "missing HP"))]
        result = self.acquire(pair)
        self.assertEqual("transition_identity_incomplete", result[2])
        self.assertIn("missing HP", result[3])

    def test_exact_same_committed_tuple_remains_a_failed_transition(self):
        self.previous_key = self.expected.snapshot.identity_key
        self.sm._last_validated_identity_key = self.previous_key
        result = self.acquire([self.frame(20), self.frame(21)])
        self.assertEqual("transition_returned_to_previous", result[2])
        self.assertIsNone(result[0])

    def test_pause_resume_or_abort_during_replacement_capture_discards_proof(self):
        for event in ("pause", "pause_resume", "abort"):
            with self.subTest(event=event):
                self.setUp()
                fresh = self.frame(20)
                count = 0
                def capture():
                    nonlocal count
                    count += 1
                    if count == 1:
                        return self.confirmation
                    if event == "abort": self.sm.abort()
                    else:
                        self.sm.pause()
                        if event == "pause_resume": self.sm.resume()
                    return fresh
                self.sm._fast_screencap = Mock(side_effect=capture)
                result = self.sm._acquire_validated_snapshot(previous_accepted=self.previous_frame, require_transition=True)
                self.assertEqual("aborted" if event == "abort" else "reacquire", result[2])
                self.assertEqual(2, count)
                self.sm._read_appraisal_snapshot.assert_called_once_with(self.initial)
                self.assertEqual([], self.adb.actions)

    def test_pause_resume_during_last_exact_read_cannot_accept_the_pair(self):
        pair = [self.frame(20), self.frame(21)]
        def read(image):
            if image is pair[-1]:
                self.sm.pause()
                self.sm.resume()
            return self.read(image)
        self.sm._read_appraisal_snapshot.side_effect = read
        result = self.acquire(pair)
        self.assertEqual("reacquire", result[2])

    def test_recovered_cp_is_rechecked_on_both_new_frames_without_recovery_input(self):
        for source in ("screen_after_animation", "screen_after_powerup_preview"):
            with self.subTest(source=source):
                self.setUp()
                self.expected = replace(self.expected, cp_source=source)
                self.initial.info["decision"] = self.expected
                pair = [self.frame(20), self.frame(21)]
                self.sm._apply_animation_cp = Mock(return_value=self.expected)
                result = self.acquire(pair)
                self.assertEqual("ok", result[2])
                self.assertEqual(source, result[0].cp_source)
                self.assertEqual([pair[0], pair[1]], [item.kwargs["frame"] for item in self.sm._apply_animation_cp.call_args_list])

    def test_recovered_cp_conflict_is_held_without_recovery_input(self):
        self.expected = replace(self.expected, cp_source="screen_after_animation")
        self.initial.info["decision"] = self.expected
        self.sm._apply_animation_cp = Mock(side_effect=RuntimeError("valid final visible CP conflicts"))
        result = self.acquire([self.frame(20), self.frame(21)])
        self.assertEqual("transition_identity_inconsistent", result[2])
        self.assertIn("visible CP conflicts", result[3])
