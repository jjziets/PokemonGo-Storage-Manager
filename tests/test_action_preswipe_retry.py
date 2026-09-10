# TRACEWEAVER: file-role=action-preswipe-retry-tests; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
"""A completed action can retry its read without repeating the action."""

from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, call, patch

from pokemgr.calibration.profile import CalibrationProfile
from pokemgr.calibration.regions import ScreenRegions
from pokemgr.execution.executor import Executor
from pokemgr.indexer.snapshot import SnapshotDecision
from tests.test_mass_action_scanning import frame, snapshot


def sourced_frame(read, sequence, *, shade=255):
    image = frame(read)
    if shade != 255:
        image.paste((shade, shade, shade), (0, 0, *image.size))
    image.info.update(
        pokemgr_stream_session="action-test-stream",
        pokemgr_stream_sequence=sequence,
        pokemgr_stream_pts_us=sequence * 100_000,
        pokemgr_source_clock_generation=1,
        pokemgr_source_clock_continuity="a" * 32,
        pokemgr_capture_started_at=float(sequence),
        pokemgr_capture_finished_at=sequence + .01,
    )
    return image


class ActionPreswipeRetryTests(unittest.TestCase):
    def setUp(self):
        profile = CalibrationProfile(
            "test", "serial", "968x2376", 420,
            ScreenRegions.default_for_resolution(968, 2376, density=420),
        )
        self.adb, self.db = Mock(), Mock()
        self.executor = Executor(self.adb, profile, self.db)
        self.scanner = self.executor._scanner
        self.executor.reader.prepare_native_ocr = Mock()
        self.executor.reader.are_bars_visible = Mock(return_value=True)
        self.executor.reader.appraisal_bars_stable = Mock(return_value=True)
        self.executor.nav.detect_screen = Mock(side_effect=lambda image: image.info["screen"])
        self.scanner._read_appraisal_snapshot = Mock(side_effect=lambda image: (
            image.info["read"].as_detail()
            | {"snapshot_read_complete": image.info["read"].read_complete},
            image.info["read"].as_appraisal(),
        ))
        self.scanner._fast_swipe = Mock(return_value=True)
        self.scanner._save_failed_appraisal = Mock()
        self.sleep = self.enterContext(patch("pokemgr.execution.executor.time.sleep"))
        self.original = snapshot()
        self.completed = sourced_frame(self.original, 1)

    def captures(self, *images):
        self.scanner._fast_screencap = Mock(side_effect=images)

    def assert_no_action_repeated(self):
        self.adb.tap.assert_not_called()
        self.adb.key_event.assert_not_called()
        self.assertEqual([], self.db.mock_calls)

    def assert_held(self):
        with self.assertRaisesRegex(RuntimeError, "changed before swipe"):
            self.executor._advance_action(self.original, self.completed)
        self.scanner._fast_swipe.assert_not_called()
        self.assert_no_action_repeated()

    def test_matching_first_read_keeps_one_capture_one_swipe_fast_path(self):
        fresh = sourced_frame(self.original, 2)
        self.captures(fresh)

        self.assertTrue(self.executor._advance_action(self.original, self.completed))

        self.scanner._fast_screencap.assert_called_once()
        self.scanner._fast_swipe.assert_called_once_with()
        self.assertIs(fresh, self.scanner._last_stable_image)
        self.sleep.assert_not_called()
        self.scanner._save_failed_appraisal.assert_not_called()
        self.assert_no_action_repeated()

    def test_transient_mismatch_requires_two_new_confirmations_before_one_swipe(self):
        wrong = sourced_frame(replace(self.original, hp=61), 2)
        first, second = (sourced_frame(self.original, seq) for seq in (3, 4))
        self.captures(wrong, first, second)

        self.assertTrue(self.executor._advance_action(self.original, self.completed))

        self.assertEqual(3, self.scanner._fast_screencap.call_count)
        self.assertEqual([call(.1), call(.1)], self.sleep.call_args_list)
        self.scanner._fast_swipe.assert_called_once_with()
        self.assertIs(second, self.scanner._last_stable_image)
        self.scanner._save_failed_appraisal.assert_not_called()
        self.assert_no_action_repeated()

    def test_single_matching_read_after_two_mismatches_cannot_swipe(self):
        wrong = replace(self.original, hp=61)
        self.captures(sourced_frame(wrong, 2), sourced_frame(wrong, 3),
                      sourced_frame(self.original, 4))

        self.assert_held()

        self.assertEqual(3, self.scanner._fast_screencap.call_count)

    def test_persistent_hp_iv_species_or_incomplete_identity_remains_held(self):
        for changes in ({"hp": 61}, {"atk": 11},
                        {"caught_species": "Raichu", "detected_species": "Raichu"},
                        {"read_complete": False}):
            with self.subTest(changes=changes):
                changed = replace(self.original, **changes)
                self.captures(*(sourced_frame(changed, seq) for seq in (2, 3, 4)))
                self.assert_held()
                self.assertEqual(3, self.scanner._fast_screencap.call_count)

    def test_non_appraisal_error_is_held_without_navigation_or_swipe(self):
        fresh = sourced_frame(self.original, 2)
        fresh.info["screen"] = "storage"
        self.captures(fresh)

        with self.assertRaisesRegex(RuntimeError, "appraisal is not confirmed"):
            self.executor._advance_action(self.original, self.completed)

        self.scanner._fast_screencap.assert_called_once()
        self.scanner._fast_swipe.assert_not_called()
        self.assert_no_action_repeated()

    def test_repeated_frame_object_cannot_supply_both_retry_confirmations(self):
        candidate = sourced_frame(self.original, 3)
        self.captures(sourced_frame(replace(self.original, hp=61), 2),
                      candidate, candidate)
        # Identity of the objects must still be checked if the source helper
        # is permissive (as it is for captures without stream metadata).
        self.scanner._specimen_frame_sources_ordered = Mock(return_value=True)

        self.assert_held()

    def test_copied_repeated_source_frame_cannot_confirm_a_retry(self):
        candidate = sourced_frame(self.original, 3)
        self.captures(sourced_frame(replace(self.original, hp=61), 2),
                      candidate, candidate.copy())

        self.assert_held()

    def test_original_completed_frame_cannot_be_reused_as_retry_evidence(self):
        self.captures(sourced_frame(replace(self.original, hp=61), 2),
                      self.completed, sourced_frame(self.original, 4))
        self.scanner._specimen_frame_sources_ordered = Mock(return_value=True)

        self.assert_held()

    def test_failed_frame_cannot_be_reused_when_ocr_changes_its_answer(self):
        failed = sourced_frame(replace(self.original, hp=61), 2)
        self.captures(failed, failed, sourced_frame(self.original, 4))
        self.executor._read_identity = Mock(side_effect=[
            replace(self.original, hp=61), self.original, self.original,
        ])
        self.scanner._specimen_frame_sources_ordered = Mock(return_value=True)

        self.assert_held()

    def test_copied_failed_source_cannot_be_reused_when_ocr_changes_its_answer(self):
        failed = sourced_frame(replace(self.original, hp=61), 2)
        self.captures(failed, failed.copy(), sourced_frame(self.original, 4))
        self.executor._read_identity = Mock(side_effect=[
            replace(self.original, hp=61), self.original, self.original,
        ])

        self.assert_held()

    def test_first_retry_source_must_be_newer_than_failed_observation(self):
        self.captures(sourced_frame(replace(self.original, hp=61), 5),
                      sourced_frame(self.original, 3), sourced_frame(self.original, 6))

        self.assert_held()

    def test_retry_pair_requires_stable_pixels_between_its_two_frames(self):
        self.completed = sourced_frame(self.original, 1, shade=128)
        self.captures(sourced_frame(self.original, 2, shade=124),
                      sourced_frame(self.original, 3, shade=127),
                      sourced_frame(self.original, 4, shade=129))
        # Each final frame differs from the original by one intensity unit;
        # the retry pair differs by two, exceeding the real stability limit.
        self.assert_held()

    def test_compatible_clock_refresh_can_confirm_a_retry(self):
        first, second = (sourced_frame(self.original, seq) for seq in (3, 4))
        second.info["pokemgr_source_clock_generation"] = 2
        self.captures(sourced_frame(replace(self.original, hp=61), 2), first, second)

        self.assertTrue(self.executor._advance_action(self.original, self.completed))

        self.scanner._fast_swipe.assert_called_once_with()

    def test_moving_narrow_iv_bars_cannot_confirm_same_rounded_iv_reads(self):
        first, second = (sourced_frame(self.original, seq) for seq in (3, 4))
        self.captures(sourced_frame(replace(self.original, hp=61), 2), first, second)
        self.executor.reader.appraisal_bars_stable.return_value = False

        self.assert_held()

        self.executor.reader.appraisal_bars_stable.assert_called_once_with(first, second)

    def test_changed_stream_or_clock_continuity_cannot_confirm_a_retry(self):
        for field, value in (("pokemgr_stream_session", "reconnected"),
                             ("pokemgr_source_clock_continuity", "b" * 32)):
            with self.subTest(field=field):
                first, second = (sourced_frame(self.original, seq) for seq in (3, 4))
                second.info[field] = value
                self.captures(sourced_frame(replace(self.original, hp=61), 2), first, second)
                self.assert_held()

    def test_pause_resume_discards_pending_frame_and_needs_two_new_frames(self):
        wrong = sourced_frame(replace(self.original, hp=61), 2)
        before_pause, after_pause, confirmation = (
            sourced_frame(self.original, seq) for seq in (3, 4, 5)
        )
        self.captures(wrong, before_pause, after_pause, confirmation)

        def pause_between_confirmations(_delay):
            if self.sleep.call_count == 2:
                self.executor.pause()
                self.executor.resume()

        self.sleep.side_effect = pause_between_confirmations

        self.assertTrue(self.executor._advance_action(self.original, self.completed))

        self.assertEqual(4, self.scanner._fast_screencap.call_count)
        self.assertIs(confirmation, self.scanner._last_stable_image)
        self.scanner._fast_swipe.assert_called_once_with()
        self.assert_no_action_repeated()

    def test_reused_pre_pause_candidate_cannot_count_as_a_new_post_resume_read(self):
        candidate = sourced_frame(self.original, 3)
        after_pause, confirmation = (sourced_frame(self.original, seq) for seq in (4, 5))
        self.captures(sourced_frame(replace(self.original, hp=61), 2),
                      candidate, candidate, after_pause, confirmation)

        def pause_between_confirmations(_delay):
            if self.sleep.call_count == 2:
                self.executor.pause()
                self.executor.resume()

        self.sleep.side_effect = pause_between_confirmations

        self.assertTrue(self.executor._advance_action(self.original, self.completed))

        self.assertEqual(5, self.scanner._fast_screencap.call_count)
        self.assertIs(confirmation, self.scanner._last_stable_image)
        self.scanner._fast_swipe.assert_called_once_with()

    def test_copied_pre_pause_source_cannot_count_as_a_new_post_resume_read(self):
        candidate = sourced_frame(self.original, 3)
        after_pause, confirmation = (sourced_frame(self.original, seq) for seq in (4, 5))
        self.captures(sourced_frame(replace(self.original, hp=61), 2),
                      candidate, candidate.copy(), after_pause, confirmation)

        def pause_between_confirmations(_delay):
            if self.sleep.call_count == 2:
                self.executor.pause()
                self.executor.resume()

        self.sleep.side_effect = pause_between_confirmations

        self.assertTrue(self.executor._advance_action(self.original, self.completed))

        self.assertEqual(5, self.scanner._fast_screencap.call_count)
        self.assertIs(confirmation, self.scanner._last_stable_image)
        self.scanner._fast_swipe.assert_called_once_with()

    def test_abort_during_retry_delay_prevents_capture_evidence_and_swipe(self):
        self.captures(sourced_frame(replace(self.original, hp=61), 2))
        self.sleep.side_effect = lambda _delay: self.executor.abort()

        self.assertFalse(self.executor._advance_action(self.original, self.completed))

        self.scanner._fast_screencap.assert_called_once()
        self.scanner._fast_swipe.assert_not_called()
        self.scanner._save_failed_appraisal.assert_not_called()
        self.assert_no_action_repeated()

    def test_abort_during_fresh_read_prevents_swipe(self):
        self.captures(sourced_frame(self.original, 2))
        read_identity = self.executor._read_identity

        def interrupted_read(image):
            result = read_identity(image)
            self.executor.abort()
            return result

        self.executor._read_identity = Mock(side_effect=interrupted_read)

        self.assertFalse(self.executor._advance_action(self.original, self.completed))

        self.scanner._fast_swipe.assert_not_called()
        self.scanner._save_failed_appraisal.assert_not_called()
        self.assert_no_action_repeated()

    def test_abort_during_final_identity_check_prevents_evidence_and_swipe(self):
        first, second = (sourced_frame(self.original, seq) for seq in (3, 4))
        self.captures(sourced_frame(replace(self.original, hp=61), 2), first, second)
        same_identity = self.executor._same_identity

        def interrupted_comparison(before, after, left, right):
            result = same_identity(before, after, left, right)
            if left is first and right is second:
                self.executor.abort()
            return result

        self.executor._same_identity = Mock(side_effect=interrupted_comparison)

        self.assertFalse(self.executor._advance_action(self.original, self.completed))

        self.scanner._fast_swipe.assert_not_called()
        self.scanner._save_failed_appraisal.assert_not_called()
        self.assert_no_action_repeated()

    def test_exhaustion_preserves_original_and_final_comparison_evidence(self):
        changed = replace(self.original, hp=61)
        images = [sourced_frame(changed, seq) for seq in (2, 3, 4)]
        self.captures(*images)
        self.executor._action_position = 20

        self.assert_held()

        calls = self.scanner._save_failed_appraisal.call_args_list
        self.assertEqual(2, len(calls))
        self.assertIs(self.completed, calls[0].args[0])
        self.assertIs(images[-1], calls[1].args[0])
        self.assertEqual("action_before_swipe", calls[0].kwargs["phase"])
        self.assertEqual("action_before_swipe_reread", calls[1].kwargs["phase"])
        self.assertEqual([20, 20], [saved.kwargs["position"] for saved in calls])

    def test_retry_does_not_repeat_completed_star_or_occurrence_count(self):
        following = snapshot(cp=600, hp=70)
        final_frame = sourced_frame(following, 5)
        self.executor._open_pass = Mock(return_value=2)
        decisions = [SnapshotDecision(True, "exact", read, cp_source="calculated")
                     for read in (self.original, following)]
        self.scanner._acquire_validated_snapshot = Mock(side_effect=[
            (decisions[0], self.completed, "ok", "exact"),
            (decisions[1], final_frame, "ok", "exact"),
        ])
        self.executor._set_star = Mock(side_effect=[(True, self.completed), (True, final_frame)])
        self.captures(sourced_frame(replace(self.original, hp=61), 2),
                      sourced_frame(self.original, 3), sourced_frame(self.original, 4))
        remaining = Counter({self.executor._snapshot_keeper_key(read): 1
                             for read in (self.original, following)})

        result = self.executor._run_favorite_pass("4*", remaining, False, 2)

        self.assertNotIn("error", result)
        self.assertEqual((2, 2, 0), (result["checked"], result["favorited"], result["skipped"]))
        self.assertEqual(2, self.executor._set_star.call_count)
        self.assertEqual(2, self.scanner._acquire_validated_snapshot.call_count)
        self.assertEqual({}, remaining)
        self.scanner._fast_swipe.assert_called_once_with()
        self.assert_no_action_repeated()

    def test_saved_evidence_uses_action_ordinal_and_preserves_default_scan_position(self):
        from pokemgr.indexer.state_machine import IndexingStateMachine

        self.scanner.visited_count = 6
        with TemporaryDirectory() as directory, \
             patch('pokemgr.indexer.state_machine.config.CACHE_DIR', Path(directory)):
            save = IndexingStateMachine._save_failed_appraisal
            save(self.scanner, self.completed, 'action held', phase='action_before_swipe', position=20)
            save(self.scanner, self.completed, 'scan held')
            destination = Path(directory) / 'scan_failures' / self.scanner.session_id
            self.assertTrue((destination / 'position_00020_action_before_swipe.png').exists())
            self.assertEqual(20, json.loads((destination / 'position_00020.json').read_text())['position'])
            self.assertEqual(7, json.loads((destination / 'position_00007.json').read_text())['position'])

    def test_retry_does_not_count_a_completed_nonmatching_position_twice(self):
        following = snapshot(cp=600, hp=70)
        final_frame = sourced_frame(following, 5)
        self.executor._open_pass = Mock(return_value=2)
        self.scanner._acquire_validated_snapshot = Mock(side_effect=[
            (SnapshotDecision(True, "exact", self.original), self.completed, "ok", "exact"),
            (SnapshotDecision(True, "exact", following), final_frame, "ok", "exact"),
        ])
        self.executor._set_star = Mock(return_value=(True, final_frame))
        self.captures(sourced_frame(replace(self.original, hp=61), 2),
                      sourced_frame(self.original, 3), sourced_frame(self.original, 4))
        remaining = Counter({self.executor._snapshot_keeper_key(following): 1})

        result = self.executor._run_favorite_pass("4*", remaining, False, 1)

        self.assertNotIn("error", result)
        self.assertEqual((2, 1, 1), (result["checked"], result["favorited"], result["skipped"]))
        self.executor._set_star.assert_called_once()
        self.assertEqual({}, remaining)
        self.scanner._fast_swipe.assert_called_once_with()
        self.assert_no_action_repeated()


if __name__ == "__main__":
    unittest.main()
