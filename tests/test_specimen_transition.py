"""Independent specimen markers distinguish neighbours with identical battle stats."""

# TRACEWEAVER: file-role=specimen-transition-tests; req=REQ-SCAN-003; trace=TRACE-SCAN-003; verifies=VER-SCAN-001
from dataclasses import replace
from collections import Counter
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from pokemgr.indexer.snapshot import SnapshotDecision
from pokemgr.indexer.state_machine import IndexingStateMachine
from pokemgr.execution.executor import Executor
from tests.test_stable_scan_loop import _ADB, _DB, accepted, profile


STREAM_KEYS = (
    "pokemgr_stream_session", "pokemgr_stream_sequence",
    "pokemgr_stream_pts_us", "pokemgr_source_clock_generation",
)


def specimen_frame(sequence, markers, decision):
    image = Image.new("RGB", (96, 237), "white")
    image.info.update(
        pokemgr_stream_session="a" * 32,
        pokemgr_stream_sequence=sequence,
        pokemgr_stream_pts_us=sequence * 100_000,
        pokemgr_source_clock_generation=1,
        pokemgr_capture_started_at=sequence * 0.1,
        pokemgr_capture_finished_at=sequence * 0.1 + 0.01,
        markers=markers, decision=decision, screen="appraisal", bars=True,
    )
    return image


# TRACEWEAVER: verifies=VER-SCAN-001; req=REQ-SCAN-003; trace=TRACE-SCAN-003
class SpecimenTransitionTests(unittest.TestCase):
    def setUp(self):
        self.adb, self.db = _ADB(), _DB()
        self.sm = IndexingStateMachine(self.adb, profile(), self.db)
        self.decision = accepted("Plusle", 755, 87, (14, 15, 14))
        self.key = self.decision.snapshot.identity_key
        before = ("3.12", "0.39", "2025/09/12")
        after = ("3.80", "0.39", "2025/09/12")
        self.frames = [specimen_frame(i + 1, markers, self.decision)
                       for i, markers in enumerate((before, before, after, after))]
        self.sm._last_accepted_image = self.frames[0]
        self.sm._last_accepted_pause_generation = self.sm._pause_generation
        self.sm._last_validated_identity_key = self.key
        self.sm.nav.detect_screen = Mock(side_effect=lambda image: image.info["screen"])
        self.sm.reader.are_bars_visible = Mock(side_effect=lambda image: image.info["bars"])
        self.sm.reader.specimen_markers = Mock(side_effect=lambda image: image.info["markers"])
        self.sm.reader.specimen_gender = Mock(
            side_effect=lambda image: image.info.get("specimen_gender", ""),
        )
        self.sm._read_appraisal_snapshot = Mock(side_effect=self.read_snapshot)
        self.sm._validate_appraisal_snapshot = Mock(
            side_effect=lambda _snapshot, image: image.info["decision"],
        )

    @staticmethod
    def read_snapshot(image):
        snapshot = image.info["decision"].snapshot
        return snapshot.as_detail(), snapshot.as_appraisal()

    def proof(self):
        result = self.sm._same_stats_specimen_advanced(
            self.frames[1], tuple(self.frames[2:]), self.key,
            self.sm._pause_generation,
        )
        self.assertEqual([], self.adb.actions)
        self.assertEqual([], self.db.rows)
        self.assertEqual((0, 0, 0), (self.sm.count, self.sm.visited_count,
                                    self.sm.skipped_count))
        return result

    def acquire(self, *, recovered=False, status="stable_raw_identity_unchanged"):
        def settle(**_kwargs):
            self.sm._settled_frame_pair = tuple(self.frames[2:])
            return self.frames[3], status

        self.sm._wait_for_stable_appraisal = Mock(side_effect=settle)
        self.sm._cp_recovery_identity = Mock(return_value=self.key if recovered else None)
        self.sm._calculated_cp_confirmed_by_pair = Mock(return_value=False)
        return self.sm._acquire_validated_snapshot(
            previous_accepted=self.frames[1], require_transition=True,
        )

    def test_repeated_changed_weight_proves_same_stats_neighbour(self):
        self.assertTrue(self.proof())
        read_ids = {id(call.args[0]) for call in
                    self.sm._read_appraisal_snapshot.call_args_list}
        validated_ids = {id(call.args[1]) for call in
                         self.sm._validate_appraisal_snapshot.call_args_list}
        marker_ids = {id(call.args[0]) for call in
                      self.sm.reader.specimen_markers.call_args_list}
        expected_ids = {id(image) for image in self.frames}
        self.assertEqual(expected_ids, read_ids)
        self.assertEqual(expected_ids, validated_ids)
        self.assertEqual(expected_ids, marker_ids)

    def test_each_marker_can_prove_advance_without_other_fields(self):
        for field in range(3):
            with self.subTest(field=field):
                self.setUp()
                for side, value in ((self.frames[:2], "old"),
                                    (self.frames[2:], "new")):
                    markers = ["", "", ""]
                    markers[field] = value
                    for image in side:
                        image.info["markers"] = tuple(markers)
                self.assertTrue(self.proof())

    def test_unreliable_other_marker_does_not_discard_repeated_weight(self):
        self.frames[0].info["markers"] = ("3.12", "", "2025/09/12")
        self.frames[1].info["markers"] = ("3.12", "", "2025/09/17")
        self.frames[2].info["markers"] = ("3.80", "", "")
        self.frames[3].info["markers"] = ("3.80", "", "")
        self.assertTrue(self.proof())

    def test_repeated_male_and_female_icons_can_be_the_only_distinct_marker(self):
        for genders in (("male", "male", "female", "female"),
                        ("female", "female", "male", "male")):
            with self.subTest(genders=genders):
                self.setUp()
                for image, gender in zip(self.frames, genders):
                    image.info["markers"] = ("", "", "")
                    image.info["specimen_gender"] = gender
                self.assertTrue(self.proof())

    def test_missing_unknown_equal_or_noisy_gender_does_not_prove_a_neighbour(self):
        cases = (
            ("male", "male", "", ""),
            ("", "", "female", "female"),
            ("male", "male", "none", "none"),
            ("none", "none", "female", "female"),
            ("male", "male", "unknown", "unknown"),
            ("male", "male", "female", ""),
            ("male", "", "female", "female"),
            ("male", "female", "female", "female"),
            ("male", "male", "male", "female"),
            ("male", "male", "male", "male"),
        )
        for genders in cases:
            with self.subTest(genders=genders):
                self.setUp()
                for image, gender in zip(self.frames, genders):
                    image.info["markers"] = ("", "", "")
                    image.info["specimen_gender"] = gender
                self.assertFalse(self.proof())

    def test_pause_during_any_gender_read_invalidates_specimen_proof(self):
        for interrupt_at in range(1, 5):
            with self.subTest(at=interrupt_at):
                self.setUp()
                for index, image in enumerate(self.frames):
                    image.info["markers"] = ("", "", "")
                    image.info["specimen_gender"] = "male" if index < 2 else "female"
                reads = 0

                def gender(image):
                    nonlocal reads
                    reads += 1
                    if reads == interrupt_at:
                        self.sm._pause_generation += 1
                    return image.info["specimen_gender"]

                self.sm.reader.specimen_gender = Mock(side_effect=gender)
                self.assertFalse(self.proof())

    def test_missing_equal_or_unrepeated_marker_cannot_prove_advance(self):
        cases = {
            "all_missing": [("", "", "")] * 4,
            "all_equal": [("3.12", "0.39", "2025/09/12")] * 4,
            "only_after": [("", "", "")] * 2 + [("3.80", "", "")] * 2,
            "only_before": [("3.12", "", "")] * 2 + [("", "", "")] * 2,
            "before_noise": [("3.12", "", ""), ("3.17", "", ""),
                             ("3.80", "", ""), ("3.80", "", "")],
            "after_noise": [("3.12", "", ""), ("3.12", "", ""),
                            ("3.80", "", ""), ("3.88", "", "")],
        }
        for name, markers in cases.items():
            with self.subTest(case=name):
                self.setUp()
                for image, values in zip(self.frames, markers):
                    image.info["markers"] = values
                self.assertFalse(self.proof())

    def test_each_frame_must_validate_the_same_complete_tuple(self):
        for index in range(4):
            for fault in ("changed", "incomplete", "rejected"):
                with self.subTest(frame=index, fault=fault):
                    self.setUp()
                    if fault == "changed":
                        value = accepted("Plusle", 755, 87, (14, 15, 13))
                    elif fault == "incomplete":
                        value = accepted("Plusle", -1, 87, (14, 15, 14))
                    else:
                        value = SnapshotDecision(False, "uncertain", self.decision.snapshot)
                    self.frames[index].info["decision"] = value
                    self.assertFalse(self.proof())

    def test_each_frame_requires_appraisal_and_visible_bars(self):
        for index in range(4):
            for field, value in (("screen", "detail"), ("bars", False)):
                with self.subTest(frame=index, field=field):
                    self.setUp()
                    self.frames[index].info[field] = value
                    self.assertFalse(self.proof())

    def test_same_side_frames_must_stay_settled(self):
        for unstable_side in (0, 1):
            with self.subTest(side=unstable_side):
                self.setUp()
                bad_ids = {id(image) for image in
                           self.frames[unstable_side * 2:unstable_side * 2 + 2]}
                with patch("pokemgr.indexer.snapshot.appraisal_frames_stable",
                           side_effect=lambda a, b: {id(a), id(b)} != bad_ids):
                    self.assertFalse(self.proof())

    def test_repeated_image_object_cannot_be_independent_evidence(self):
        for index in range(1, 4):
            with self.subTest(boundary=index):
                self.setUp()
                self.frames[index] = self.frames[index - 1]
                self.assertFalse(self.proof())

    def test_every_stream_boundary_requires_ordered_same_session_sources(self):
        for index in range(1, 4):
            for fault in ("sequence", "pts", "session", "clock", "missing"):
                with self.subTest(boundary=index, fault=fault):
                    self.setUp()
                    info = self.frames[index].info
                    if fault == "sequence":
                        info["pokemgr_stream_sequence"] = index
                    elif fault == "pts":
                        info["pokemgr_stream_pts_us"] = index * 100_000
                    elif fault == "session":
                        info["pokemgr_stream_session"] = "b" * 32
                    elif fault == "clock":
                        info["pokemgr_source_clock_generation"] = 2
                    else:
                        info.pop("pokemgr_source_clock_generation")
                    self.assertFalse(self.proof())

    def test_compatible_clock_refresh_at_each_boundary_preserves_specimen_proof(self):
        generations = [(1, 1, 1, 1), (1, 2, 2, 2), (1, 1, 2, 2),
                       (1, 1, 1, 2), (1, 2, 3, 4)]
        for values in generations:
            with self.subTest(generations=values):
                self.setUp()
                for image, generation in zip(self.frames, values):
                    image.info["pokemgr_source_clock_continuity"] = "c" * 32
                    image.info["pokemgr_source_clock_generation"] = generation
                self.assertTrue(self.proof())

    def test_changed_continuity_token_invalidates_even_equal_clock_generations(self):
        for boundary in range(1, 4):
            with self.subTest(boundary=boundary):
                self.setUp()
                for index, image in enumerate(self.frames):
                    image.info["pokemgr_source_clock_continuity"] = (
                        "c" if index < boundary else "d"
                    ) * 32
                self.assertFalse(self.proof())

    def test_partial_or_invalid_continuity_tokens_cannot_authorize_source_reuse(self):
        invalid_tokens = (None, "", "c" * 31, "c" * 33, "g" * 32, 123, True)
        for index in range(4):
            for token in invalid_tokens:
                with self.subTest(frame=index, token=token):
                    self.setUp()
                    for image in self.frames:
                        image.info["pokemgr_source_clock_continuity"] = "c" * 32
                    if token is None:
                        self.frames[index].info.pop("pokemgr_source_clock_continuity")
                    else:
                        self.frames[index].info["pokemgr_source_clock_continuity"] = token
                    self.assertFalse(self.proof())
        for token in ("", "not-a-continuity-token", None):
            with self.subTest(all_frames=token):
                self.setUp()
                for image in self.frames:
                    image.info["pokemgr_source_clock_continuity"] = token
                self.assertFalse(self.proof())

    def test_continuity_token_never_allows_clock_generations_to_go_backward(self):
        for boundary in range(1, 4):
            with self.subTest(boundary=boundary):
                self.setUp()
                for index, image in enumerate(self.frames):
                    image.info["pokemgr_source_clock_continuity"] = "c" * 32
                    image.info["pokemgr_source_clock_generation"] = 2 if index < boundary else 1
                self.assertFalse(self.proof())

    def test_continuity_still_requires_ordered_sequences_pts_and_same_session(self):
        mutations = (
            ("pokemgr_stream_sequence", 2),
            ("pokemgr_stream_pts_us", 200_000),
            ("pokemgr_stream_session", "b" * 32),
            ("pokemgr_source_clock_generation", 0),
            ("pokemgr_source_clock_generation", -1),
            ("pokemgr_source_clock_generation", True),
            ("pokemgr_source_clock_generation", "1"),
        )
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                self.setUp()
                for image in self.frames:
                    image.info["pokemgr_source_clock_continuity"] = "c" * 32
                self.frames[2].info[key] = value
                self.assertFalse(self.proof())

    def test_continuity_requires_finite_nonoverlapping_capture_bounds(self):
        for boundary in range(1, 4):
            for fault in ("overlap", "missing_start", "missing_finish", "nan",
                          "infinite", "backward", "boolean"):
                with self.subTest(boundary=boundary, fault=fault):
                    self.setUp()
                    for index, image in enumerate(self.frames):
                        image.info["pokemgr_source_clock_continuity"] = "c" * 32
                        image.info["pokemgr_source_clock_generation"] = index + 1
                    info = self.frames[boundary].info
                    if fault == "overlap":
                        info["pokemgr_capture_started_at"] = (
                            self.frames[boundary - 1].info["pokemgr_capture_finished_at"] - 0.005
                        )
                    elif fault == "missing_start":
                        info.pop("pokemgr_capture_started_at")
                    elif fault == "missing_finish":
                        info.pop("pokemgr_capture_finished_at")
                    elif fault == "nan":
                        info["pokemgr_capture_started_at"] = float("nan")
                    elif fault == "infinite":
                        info["pokemgr_capture_finished_at"] = float("inf")
                    elif fault == "backward":
                        info["pokemgr_capture_finished_at"] = info["pokemgr_capture_started_at"] - 0.01
                    else:
                        info["pokemgr_capture_started_at"] = True
                    self.assertFalse(self.proof())

    def test_bare_images_or_mixed_capture_sources_cannot_prove_advance(self):
        for stripped in ((0, 1, 2, 3), (0,), (1,), (2,), (3,)):
            with self.subTest(frames=stripped):
                self.setUp()
                for index in stripped:
                    self.frames[index].info.clear()
                    self.frames[index].info.update(
                        markers=("old" if index < 2 else "new", "", ""),
                        decision=self.decision, screen="appraisal", bars=True,
                    )
                self.assertFalse(self.proof())

    def test_legacy_captures_need_ordered_nonoverlapping_finite_timestamps(self):
        for fault in (None, "missing", "overlap", "nan", "infinite", "backward"):
            with self.subTest(fault=fault):
                self.setUp()
                for image in self.frames:
                    for key in STREAM_KEYS:
                        image.info.pop(key)
                info = self.frames[2].info
                if fault == "missing":
                    info.pop("pokemgr_capture_finished_at")
                elif fault == "overlap":
                    info["pokemgr_capture_started_at"] = 0.205
                elif fault == "nan":
                    info["pokemgr_capture_started_at"] = float("nan")
                elif fault == "infinite":
                    info["pokemgr_capture_finished_at"] = float("inf")
                elif fault == "backward":
                    info["pokemgr_capture_finished_at"] = 0.29
                self.assertEqual(fault is None, self.proof())

    def test_pause_or_abort_before_or_during_any_marker_read_rejects(self):
        for interrupt_at in range(5):
            for fault in ("abort", "pause", "resumed"):
                with self.subTest(at=interrupt_at, fault=fault):
                    self.setUp()
                    generation = self.sm._pause_generation

                    def interrupt():
                        if fault == "abort":
                            self.sm._abort = True
                        elif fault == "pause":
                            self.sm._paused = True
                        else:
                            self.sm._pause_generation += 1

                    count = 0

                    def markers(image):
                        nonlocal count
                        count += 1
                        if count == interrupt_at:
                            interrupt()
                        return image.info["markers"]

                    self.sm.reader.specimen_markers = Mock(side_effect=markers)
                    if interrupt_at == 0:
                        interrupt()
                    self.assertFalse(self.sm._same_stats_specimen_advanced(
                        self.frames[1], tuple(self.frames[2:]), self.key, generation,
                    ))
                    self.assertEqual([], self.adb.actions)
                    self.assertEqual([], self.db.rows)

    def test_pause_since_last_accepted_frame_invalidates_its_evidence(self):
        self.sm._pause_generation += 1
        self.assertFalse(self.proof())

    def test_pause_during_any_tuple_read_or_validation_invalidates_proof(self):
        for stage in ("read", "validate"):
            for interrupt_at in range(1, 5):
                with self.subTest(stage=stage, at=interrupt_at):
                    self.setUp()
                    count = 0

                    def interrupted_read(*args):
                        nonlocal count
                        count += 1
                        if count == interrupt_at:
                            self.sm._pause_generation += 1
                        if stage == "read":
                            return self.read_snapshot(args[0])
                        return args[1].info["decision"]

                    target = ("_read_appraisal_snapshot" if stage == "read"
                              else "_validate_appraisal_snapshot")
                    setattr(self.sm, target, Mock(side_effect=interrupted_read))
                    self.assertFalse(self.proof())

    def test_acquisition_accepts_changed_specimen_without_recovery_input(self):
        decision, image, kind, _reason = self.acquire()
        self.assertEqual("ok", kind)
        self.assertEqual(self.key, decision.snapshot.identity_key)
        self.assertIs(self.frames[3], image)
        self.assertEqual([], self.adb.actions)
        self.assertEqual([], self.db.rows)

    def test_unobserved_motion_accepts_specimen_proof_without_an_extra_capture(self):
        self.sm._fast_screencap = Mock(
            side_effect=AssertionError("Four-frame proof already confirms the neighbour"),
        )
        decision, image, kind, _reason = self.acquire(status="stable_transition_unobserved")
        self.assertEqual("ok", kind)
        self.assertEqual(self.key, decision.snapshot.identity_key)
        self.assertIs(self.frames[3], image)
        self.sm._fast_screencap.assert_not_called()
        self.assertEqual([], self.adb.actions)
        self.assertEqual([], self.db.rows)

    def test_unobserved_motion_without_changed_markers_still_holds_position(self):
        for image in self.frames:
            image.info["markers"] = ("3.12", "0.39", "2025/09/12")
        confirmation = specimen_frame(5, self.frames[3].info["markers"], self.decision)
        self.sm._fast_screencap = Mock(return_value=confirmation)
        with patch("pokemgr.indexer.state_machine.human_delay"):
            decision, _image, kind, _reason = self.acquire(
                status="stable_transition_unobserved",
            )
        self.assertIsNone(decision)
        self.assertEqual("transition_returned_to_previous", kind)
        self.sm._fast_screencap.assert_called_once()
        self.assertEqual([], self.adb.actions)
        self.assertEqual([], self.db.rows)

    def test_acquisition_keeps_checkpoint_failure_when_markers_do_not_advance(self):
        for image in self.frames:
            image.info["markers"] = ("3.12", "0.39", "2025/09/12")
        decision, _image, kind, _reason = self.acquire()
        self.assertIsNone(decision)
        self.assertEqual("transition_returned_to_previous", kind)
        self.assertEqual([], self.adb.actions)
        self.assertEqual([], self.db.rows)

    def test_cp_recovery_invalidates_pair_even_if_it_returns_same_image_object(self):
        recovered = replace(self.decision, cp_source="calculated")
        for image in self.frames:
            image.info["decision"] = recovered
        self.sm._recover_cp_with_model_taps = Mock(return_value=(recovered, self.frames[3]))
        decision, _image, kind, _reason = self.acquire(recovered=True)
        self.sm._recover_cp_with_model_taps.assert_called_once()
        self.assertIsNone(decision)
        self.assertEqual("transition_returned_to_previous", kind)
        self.sm.reader.specimen_markers.assert_not_called()
        self.assertEqual([], self.adb.actions)
        self.assertEqual([], self.db.rows)

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_scan_stores_proven_identical_neighbours_at_separate_positions(self, _paddle):
        self.sm._last_accepted_image = None
        self.sm._last_accepted_pause_generation = None
        self.sm._last_validated_identity_key = None
        self.sm.reader.prepare_native_ocr = Mock()
        self.sm._fast_screencap = Mock(return_value=self.frames[1])
        self.sm._cp_recovery_identity = Mock(return_value=None)
        self.sm._calculated_cp_confirmed_by_pair = Mock(return_value=False)
        self.sm._recover_failed_transition = Mock(
            side_effect=AssertionError("Proven neighbour should not backtrack"),
        )
        acquisitions = 0

        def settle(**_kwargs):
            nonlocal acquisitions
            acquisitions += 1
            if acquisitions == 1:
                return self.frames[0], "stable"
            self.sm._settled_frame_pair = tuple(self.frames[2:])
            return self.frames[3], "stable_raw_identity_unchanged"

        self.sm._wait_for_stable_appraisal = Mock(side_effect=settle)
        self.sm.start(expected_total=2)

        self.assertEqual((2, 2, 0), (self.sm.count, self.sm.visited_count,
                                    self.sm.skipped_count))
        self.assertEqual([0, 1], [row[2] for row in self.db.rows])
        self.assertEqual(["Plusle", "Plusle"], [row[0].species for row in self.db.rows])
        self.assertEqual([755, 755], [row[0].cp for row in self.db.rows])
        self.assertEqual(["swipe"], [action[0] for action in self.adb.actions])
        self.sm._recover_failed_transition.assert_not_called()

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_pause_in_saved_row_callback_does_not_bless_old_frame_with_new_generation(self, _paddle):
        for resume in (False, True):
            with self.subTest(resumed=resume):
                self.setUp()
                generation = self.sm._pause_generation
                self.sm.reader.prepare_native_ocr = Mock()
                self.sm._acquire_validated_snapshot = Mock(return_value=(
                    self.decision, self.frames[0], "ok", "exact",
                ))

                def on_progress(_count, _pokemon):
                    self.sm.pause()
                    if resume:
                        self.sm.resume()

                self.sm.on_progress = on_progress
                self.sm.start(expected_total=1)

                self.assertEqual(1, len(self.db.rows))
                self.assertEqual((1, 1), (self.sm.count, self.sm.visited_count))
                self.assertIsNone(self.sm._last_accepted_image)
                self.assertEqual(generation, self.sm._last_accepted_pause_generation)
                self.assertGreater(self.sm._pause_generation, generation)
                self.assertEqual([], self.adb.actions)

    def test_checkpoint_retry_uses_restored_specimen_reference_for_final_transition(self):
        previous = accepted("Tropius", 756, 120, (13, 11, 14))
        self.sm._previous_validated_identity_key = previous.snapshot.identity_key
        before, after = self.frames[0].info["markers"], self.frames[3].info["markers"]
        restored = specimen_frame(5, before, self.decision)
        pre_retry = specimen_frame(6, before, self.decision)
        post_pair = (specimen_frame(7, after, self.decision),
                     specimen_frame(8, after, self.decision))
        previous_frame = specimen_frame(3, ("70.0", "2.00", ""), previous)
        self.sm._fast_screencap = Mock(side_effect=[
            specimen_frame(2, before, self.decision), previous_frame, pre_retry,
        ])
        self.sm._cp_recovery_identity = Mock(return_value=None)
        self.sm._calculated_cp_confirmed_by_pair = Mock(return_value=False)

        def settle(**_kwargs):
            self.sm._settled_frame_pair = post_pair
            return post_pair[1], "stable_raw_identity_unchanged"

        self.sm._wait_for_stable_appraisal = Mock(side_effect=settle)
        acquire = self.sm._acquire_validated_snapshot
        reads = 0

        def checkpoint_or_acquire(**kwargs):
            nonlocal reads
            reads += 1
            if reads == 1:
                return previous, previous_frame, "ok", "exact"
            if reads == 2:
                return self.decision, restored, "ok", "exact"
            self.assertIs(restored, self.sm._last_accepted_image)
            self.assertEqual(self.sm._pause_generation,
                             self.sm._last_accepted_pause_generation)
            self.assertIs(pre_retry, kwargs["previous_accepted"])
            return acquire(**kwargs)

        self.sm._acquire_validated_snapshot = Mock(side_effect=checkpoint_or_acquire)
        decision, image, kind, _reason = self.sm._recover_failed_transition(self.frames[0])

        self.assertEqual("ok", kind)
        self.assertEqual(self.key, decision.snapshot.identity_key)
        self.assertIs(post_pair[1], image)
        self.assertIs(restored, self.sm._last_accepted_image)
        self.assertEqual(3, len(self.adb.actions))
        self.assertEqual([], self.db.rows)

    def test_failed_checkpoint_does_not_replace_accepted_specimen_with_probe(self):
        for phase in (0, 1):
            with self.subTest(phase=phase):
                self.setUp()
                previous = accepted("Tropius", 756, 120, (13, 11, 14))
                self.sm._previous_validated_identity_key = previous.snapshot.identity_key
                probe = specimen_frame(5, ("70.0", "2.00", ""), previous)
                self.sm._fast_screencap = Mock(return_value=self.frames[1])
                wrong = self.decision if phase == 0 else previous
                reads = ([(previous, probe, "ok", "exact")] if phase else [])
                reads += [(wrong, probe, "ok", "exact")] * 3
                self.sm._acquire_validated_snapshot = Mock(side_effect=reads)

                decision, _image, kind, _reason = self.sm._recover_failed_transition(probe)

                self.assertIsNone(decision)
                self.assertEqual("transition_retry_failed", kind)
                self.assertIs(self.frames[0], self.sm._last_accepted_image)
                self.assertEqual(self.sm._pause_generation,
                                 self.sm._last_accepted_pause_generation)
                self.assertEqual(phase + 1, len(self.adb.actions))
                self.assertEqual([], self.db.rows)

    def test_keeper_pass_retains_specimen_reference_and_consumes_both_occurrences(self):
        executor = Executor(self.adb, profile(), self.db)
        executor._scanner = self.sm
        executor.reader, executor.nav = self.sm.reader, self.sm.nav
        executor._open_pass = Mock(return_value=2)
        executor._set_star = Mock(side_effect=lambda _snapshot, frame, *_args, **_kwargs:
                                  (True, frame))
        self.sm.reader.prepare_native_ocr = Mock()
        self.sm._fast_screencap = Mock(return_value=self.frames[1])
        self.sm._cp_recovery_identity = Mock(return_value=None)
        self.sm._calculated_cp_confirmed_by_pair = Mock(return_value=False)
        self.sm._recover_failed_transition = Mock(
            side_effect=AssertionError("Proven keeper neighbour should not backtrack"),
        )
        acquisitions = 0

        def settle(**_kwargs):
            nonlocal acquisitions
            acquisitions += 1
            if acquisitions == 1:
                self.assertIsNone(self.sm._last_accepted_image)
                self.assertIsNone(self.sm._last_accepted_pause_generation)
                return self.frames[0], "stable"
            self.assertIs(self.frames[0], self.sm._last_accepted_image)
            self.assertEqual(self.sm._pause_generation,
                             self.sm._last_accepted_pause_generation)
            self.sm._settled_frame_pair = tuple(self.frames[2:])
            return self.frames[3], "stable_raw_identity_unchanged"

        self.sm._wait_for_stable_appraisal = Mock(side_effect=settle)
        remaining = Counter({executor._snapshot_keeper_key(self.decision.snapshot): 2})
        result = executor._run_favorite_pass("4*", remaining, True, 2)

        self.assertNotIn("error", result)
        self.assertEqual((2, 2), (result["checked"], result["favorited"]))
        self.assertEqual({}, remaining)
        self.assertIs(self.frames[3], self.sm._last_accepted_image)
        self.assertEqual(["swipe"], [action[0] for action in self.adb.actions])
        self.assertEqual([], self.db.rows)
        self.sm._recover_failed_transition.assert_not_called()

        executor._open_pass.return_value = 0
        executor._run_favorite_pass("shiny", Counter(), True, 0)
        self.assertIsNone(self.sm._last_accepted_image)
        self.assertIsNone(self.sm._last_accepted_pause_generation)

    def test_pause_in_keeper_completion_callback_drops_old_specimen_reference(self):
        for resume in (False, True):
            with self.subTest(resumed=resume):
                self.setUp()
                generation = self.sm._pause_generation
                executor = Executor(self.adb, profile(), self.db)
                executor._scanner = self.sm
                executor.reader, executor.nav = self.sm.reader, self.sm.nav
                executor._open_pass = Mock(return_value=1)
                executor._set_star = Mock(return_value=(True, self.frames[0]))
                self.sm.reader.prepare_native_ocr = Mock()
                self.sm._acquire_validated_snapshot = Mock(return_value=(
                    self.decision, self.frames[0], "ok", "exact",
                ))

                def on_progress(_count, _total, _message):
                    executor.pause()
                    if resume:
                        executor.resume()

                executor.on_progress = on_progress
                remaining = Counter({executor._snapshot_keeper_key(self.decision.snapshot): 1})
                result = executor._run_favorite_pass("4*", remaining, True, 1)

                self.assertNotIn("error", result)
                self.assertEqual((1, 1), (result["checked"], result["favorited"]))
                self.assertEqual({}, remaining)
                self.assertIsNone(self.sm._last_accepted_image)
                self.assertEqual(generation, self.sm._last_accepted_pause_generation)
                self.assertGreater(self.sm._pause_generation, generation)
                self.assertEqual([], self.adb.actions)
                self.assertEqual([], self.db.rows)


if __name__ == "__main__":
    unittest.main()
