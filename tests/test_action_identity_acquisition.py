"""Gym skips use explicit independent appraisal evidence without star input."""

# TRACEWEAVER: file-role=action-identity-acquisition-tests; req=REQ-MASS-001,REQ-SCAN-003; trace=TRACE-MASS-001,TRACE-SCAN-003; verifies=VER-SCAN-001

from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from pokemgr.execution.executor import Executor, _ReacquireAction
from tests.test_action_preswipe_retry import sourced_frame
from tests.test_mass_action_scanning import snapshot
from tests.test_stable_scan_loop import profile


class ActionIdentityAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.adb, self.db = Mock(), Mock()
        self.executor = Executor(self.adb, profile(), self.db)
        self.scanner = self.executor._scanner
        self.reader = self.executor.reader
        self.reader.are_bars_visible = Mock(return_value=True)
        self.reader.appraisal_bars_stable = Mock(return_value=True)
        self.reader.read_cp = Mock(side_effect=AssertionError("Gym skip must not OCR CP"))
        self.scanner._recover_cp_with_model_taps = Mock(side_effect=AssertionError("Gym skip must not recover CP"))
        self.executor.nav.detect_screen = Mock(side_effect=lambda image: image.info["screen"])
        self.scanner._read_appraisal_snapshot = Mock(side_effect=self.read)
        self.scanner._save_failed_appraisal = Mock()
        self.scanner._advance_appraisal = Mock(return_value=True)
        self.scanner._fast_screencap = Mock(side_effect=AssertionError("Unexpected capture"))
        self.gym = snapshot(
            detected_species="Voltorb", caught_species="Voltorb", display_name="Voltorb",
            hp=-1, cp=-1, atk=13, def_=14, sta=14, in_gym=True,
        )
        self.older, self.newer = sourced_frame(self.gym, 1), sourced_frame(self.gym, 2)
        self.enterContext(patch("pokemgr.execution.executor.time.sleep"))
        self.settle()

    @staticmethod
    def read(image):
        observed = image.info["read"]
        return (observed.as_detail() | {"snapshot_read_complete": observed.read_complete},
                observed.as_appraisal())

    def settle(self, older=None, newer=None, *, status="stable", pair=True):
        self.older, self.newer = older or self.older, newer or self.newer
        def acquire(**_kwargs):
            self.scanner._settled_frame_pair = (self.older, self.newer) if pair else None
            return self.newer, status
        self.scanner._wait_for_stable_appraisal = Mock(side_effect=acquire)

    def acquire(self, *, allow=True, previous=None, transition=False):
        return self.executor._acquire_identity(
            sourced_frame(previous, 0) if previous is not None else None,
            previous, transition, allow_gym_skip=allow,
        )

    def assert_no_actions_or_writes(self):
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()
        self.adb.key_event.assert_not_called()
        self.assertEqual([], self.db.mock_calls)
        self.reader.read_cp.assert_not_called()
        self.scanner._recover_cp_with_model_taps.assert_not_called()

    def test_explicit_gym_pair_returns_missing_hp_only_for_skip(self):
        observed, image = self.acquire()
        self.assertEqual(self.gym, observed)
        self.assertEqual((-1, -1), (observed.hp, observed.cp))
        self.assertIs(image, self.newer)
        self.assertIsNone(self.executor._identity(observed))
        self.scanner._fast_screencap.assert_not_called()
        self.scanner._wait_for_stable_appraisal.assert_called_once()
        self.assert_no_actions_or_writes()

    def test_default_acquisition_still_rejects_gym_even_with_stray_positive_hp(self):
        for hp in (-1, 80):
            with self.subTest(hp=hp):
                observed = replace(self.gym, hp=hp)
                self.settle(sourced_frame(observed, 1), sourced_frame(observed, 2))
                with self.assertRaisesRegex(RuntimeError, "3 paired reads.*gym=True"):
                    self.acquire(allow=False)
                self.assertEqual(3, self.scanner._wait_for_stable_appraisal.call_count)
                self.assert_no_actions_or_writes()

    def test_missing_hp_without_explicit_gym_evidence_cannot_skip(self):
        for value in (False, None, 1, "true"):
            with self.subTest(in_gym=value):
                observed = replace(self.gym, in_gym=value)
                self.settle(sourced_frame(observed, 1), sourced_frame(observed, 2))
                with self.assertRaisesRegex(RuntimeError, "identity did not agree"):
                    self.acquire()
                self.assert_no_actions_or_writes()

    def test_incomplete_or_mismatched_gym_identity_holds(self):
        for changes in (
            {"read_complete": False}, {"caught_species": ""}, {"display_name": ""},
            {"detected_species": "Electrode"}, {"caught_species": "Electrode"},
            {"display_name": "Voltorb2"}, {"atk": -1}, {"def_": 16}, {"sta": 13},
            {"shiny": True}, {"shadow": True}, {"lucky": True}, {"is_dynamax": True},
        ):
            with self.subTest(changes=changes):
                self.settle(sourced_frame(self.gym, 1), sourced_frame(replace(self.gym, **changes), 2))
                with self.assertRaisesRegex(RuntimeError, "identity did not agree"):
                    self.acquire()
                self.assert_no_actions_or_writes()

    def test_gym_requires_independent_captures_and_compatible_source_clocks(self):
        for changes in (
            {"pokemgr_capture_started_at": float("nan")},
            {"pokemgr_capture_started_at": 1.0},
            {"pokemgr_capture_finished_at": 1.9},
            {"pokemgr_stream_sequence": 1}, {"pokemgr_stream_pts_us": 100_000},
            {"pokemgr_stream_session": "different"},
            {"pokemgr_source_clock_continuity": "b" * 32},
        ):
            with self.subTest(changes=changes):
                changed = sourced_frame(self.gym, 2)
                changed.info.update(changes)
                self.assertFalse(self.executor._same_gym_skip_identity(
                    self.gym, self.gym, self.older, changed,
                ))
        copied = self.older.copy()
        self.assertFalse(self.executor._same_gym_skip_identity(self.gym, self.gym, self.older, copied))
        self.assertFalse(self.executor._same_gym_skip_identity(self.gym, self.gym, self.older, self.older))
        self.assertFalse(self.executor._same_gym_skip_identity(self.gym, self.gym, self.newer, self.older))
        bare = self.newer.copy()
        bare.info.clear()
        self.assertFalse(self.executor._same_gym_skip_identity(self.gym, self.gym, self.older, bare))

    def test_clock_refresh_with_same_continuity_can_confirm_gym(self):
        self.newer.info["pokemgr_source_clock_generation"] = 2
        self.assertTrue(self.executor._same_gym_skip_identity(self.gym, self.gym, self.older, self.newer))

    def test_no_settled_pair_uses_one_new_independent_capture(self):
        self.settle(pair=False)
        fresh = sourced_frame(self.gym, 3)
        self.scanner._fast_screencap = Mock(return_value=fresh)
        observed, _image = self.acquire()
        self.assertEqual(self.gym, observed)
        self.scanner._fast_screencap.assert_called_once()
        self.assert_no_actions_or_writes()

    def test_pixel_motion_or_unsettled_iv_bars_cannot_confirm_skip(self):
        for pixels in (False, True):
            with self.subTest(pixels=pixels):
                self.settle(sourced_frame(self.gym, 1), sourced_frame(self.gym, 2, shade=0 if pixels else 255))
                self.reader.appraisal_bars_stable.return_value = pixels
                with self.assertRaisesRegex(RuntimeError, "identity did not agree"):
                    self.acquire()
                self.assert_no_actions_or_writes()

    def test_unobserved_transition_from_different_species_can_confirm_gym_skip(self):
        previous = replace(self.gym, detected_species="Snivy", caught_species="Snivy", display_name="Snivy", hp=91, in_gym=False)
        self.settle(status="stable_transition_unobserved")
        self.assertEqual(self.gym, self.acquire(previous=previous, transition=True)[0])
        self.assert_no_actions_or_writes()

    def test_gym_status_missing_hp_or_name_alone_cannot_prove_new_position(self):
        self.settle(status="stable_transition_unobserved")
        for previous in (self.gym, replace(self.gym, hp=91, in_gym=False),
                         replace(self.gym, display_name="My Voltorb", hp=91, in_gym=False)):
            with self.subTest(previous=previous):
                with self.assertRaisesRegex(RuntimeError, "next storage position"):
                    self.acquire(previous=previous, transition=True)
                self.assert_no_actions_or_writes()

    def test_other_transition_failure_statuses_remain_held(self):
        self.settle(status="stable_raw_identity_unchanged")
        with self.assertRaisesRegex(RuntimeError, "next storage position"):
            self.acquire(previous=snapshot(), transition=True)
        self.assert_no_actions_or_writes()

    def test_pause_during_first_read_invalidates_pair_before_confirmation(self):
        def read(image):
            self.executor.pause()
            self.executor.resume()
            return self.read(image)
        self.scanner._read_appraisal_snapshot.side_effect = read
        with self.assertRaises(_ReacquireAction):
            self.acquire()
        self.scanner._read_appraisal_snapshot.assert_called_once()
        self.assert_no_actions_or_writes()

    def test_abort_during_first_read_stops_before_confirmation(self):
        def read(image):
            self.executor.abort()
            return self.read(image)
        self.scanner._read_appraisal_snapshot.side_effect = read
        self.assertIsNone(self.acquire()[0])
        self.scanner._read_appraisal_snapshot.assert_called_once()
        self.assert_no_actions_or_writes()

    def test_mismatch_diagnostics_preserve_last_pair_and_missing_hp_reason(self):
        with self.assertLogs("pokemgr.execution.executor", level="WARNING") as logs:
            with self.assertRaisesRegex(RuntimeError, "HP=-1.*gym=True"):
                self.acquire(allow=False)
        self.assertIn("HP=(-1,-1)", logs.output[-1])
        self.assertIn("region_diffs=", logs.output[-1])
        self.assertEqual(2, self.scanner._save_failed_appraisal.call_count)
        self.assert_no_actions_or_writes()

    def test_confirmed_gym_skip_advances_once_without_star_or_database_action(self):
        fresh = sourced_frame(self.gym, 3)
        self.scanner._fast_screencap = Mock(return_value=fresh)
        self.assertTrue(self.executor._advance_action(self.gym, self.newer, allow_gym_skip=True))
        self.scanner._advance_appraisal.assert_called_once_with(fresh, pause_generation=0)
        self.assert_no_actions_or_writes()

    def test_default_advance_does_not_allow_missing_hp_gym(self):
        self.scanner._fast_screencap = Mock(side_effect=[sourced_frame(self.gym, seq) for seq in (3, 4, 5)])
        with self.assertRaisesRegex(RuntimeError, "changed before swipe"):
            self.executor._advance_action(self.gym, self.newer)
        self.scanner._advance_appraisal.assert_not_called()
        self.assert_no_actions_or_writes()

    def test_gym_advance_retry_requires_two_matching_reads_after_transient_mismatch(self):
        last = sourced_frame(self.gym, 5)
        self.scanner._fast_screencap = Mock(side_effect=[
            sourced_frame(replace(self.gym, atk=12), 3), sourced_frame(self.gym, 4), last,
        ])
        self.assertTrue(self.executor._advance_action(self.gym, self.newer, allow_gym_skip=True))
        self.scanner._advance_appraisal.assert_called_once_with(last, pause_generation=0)
        self.assert_no_actions_or_writes()

    def test_gym_advance_cannot_accept_departed_or_unreadable_gym(self):
        for changes in ({"in_gym": False}, {"atk": 12}, {"read_complete": False}):
            with self.subTest(changes=changes):
                self.scanner._fast_screencap = Mock(side_effect=[
                    sourced_frame(replace(self.gym, **changes), seq) for seq in (3, 4, 5)
                ])
                with self.assertRaisesRegex(RuntimeError, "changed before swipe"):
                    self.executor._advance_action(self.gym, self.newer, allow_gym_skip=True)
                self.scanner._advance_appraisal.assert_not_called()
                self.assert_no_actions_or_writes()

    def test_gym_advance_cannot_use_unseen_but_older_frames(self):
        completed = sourced_frame(self.gym, 6)
        self.scanner._fast_screencap = Mock(side_effect=[
            sourced_frame(self.gym, seq) for seq in (3, 4, 5)
        ])
        with self.assertRaisesRegex(RuntimeError, "changed before swipe"):
            self.executor._advance_action(self.gym, completed, allow_gym_skip=True)
        self.scanner._advance_appraisal.assert_not_called()
        self.assert_no_actions_or_writes()


if __name__ == "__main__":
    unittest.main()
