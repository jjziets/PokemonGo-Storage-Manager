"""Retry only exhausted settling; failed pixels never authorize a scan or input."""

# TRACEWEAVER: file-role=appraisal-settle-retry-regression; req=REQ-SCAN-001,REQ-SCAN-002,REQ-SCAN-003; trace=TRACE-SCAN-003; verifies=VER-SCAN-001

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from pokemgr.adb.controller import ADBError, StreamCaptureInvalidated
from pokemgr.indexer.snapshot import SnapshotDecision
from tests import test_settled_pair_reuse as fixture_module
from tests import test_bar_reacquisition as bar_fixture
from tests import test_stream_appraisal_settle as stream_fixture


class AppraisalSettleRetryTests(unittest.TestCase):
    def setUp(self):
        fixture = fixture_module.SettledPairReuseTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.sm, self.adb = fixture.sm, fixture.adb
        self.sm._recover_cp_with_model_taps = Mock(side_effect=AssertionError("Unexpected recovery input"))
        self.cache = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch("pokemgr.indexer.state_machine.config.CACHE_DIR", self.cache))
        self.time = 1.0

    def frame(self, read=None):
        image = fixture_module.frame(read)
        image.info.update(pokemgr_capture_started_at=self.time,
                          pokemgr_capture_finished_at=self.time + .01)
        self.time += 1
        return image

    def assert_no_input(self):
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()
        self.sm.db.insert_pokemon.assert_not_called()
        self.assertEqual((self.sm.count, self.sm.visited_count, self.sm.skipped_count), (0, 0, 0))

    def test_six_failed_pairs_then_fresh_matching_pair_recovers_without_input(self):
        frames = [self.frame() for _ in range(9)]
        self.sm._fast_screencap = Mock(side_effect=frames)
        self.sm.reader.appraisal_bars_stable.side_effect = [False] * 6 + [True, True]

        result = self.sm._acquire_validated_snapshot()

        self.assertEqual(result[2], "ok")
        self.assertIs(result[1], frames[-1])
        self.assertEqual([call.args[0] for call in self.sm._read_appraisal_snapshot.call_args_list],
                         [frames[-1], frames[-2]])
        self.assertEqual(self.sm._fast_screencap.call_count, 9)
        self.assertIsNone(self.sm._settled_frame_pair)
        self.assertIsNone(self.sm._appraisal_settle_failure)
        self.assertFalse((self.cache / "scan_failures").exists())
        self.sm._recover_cp_with_model_taps.assert_not_called()
        self.assert_no_input()

    def test_exhaustion_has_three_attempts_and_saves_only_final_pair_as_diagnostics(self):
        frames = [self.frame() for _ in range(21)]
        self.sm._fast_screencap = Mock(side_effect=frames)
        self.sm.reader.appraisal_bars_stable.return_value = False
        self.sm.skip_first_n, self.sm.visited_count = 200, 311

        with self.assertLogs("pokemgr.indexer.state_machine", level="DEBUG") as logs:
            result = self.sm._acquire_validated_snapshot()

        self.assertEqual(result[:3], (None, None, "appraisal_not_stable"))
        self.assertIn("3 acquisition attempts", result[3])
        self.assertEqual(self.sm._fast_screencap.call_count, 21)
        self.assertIsNone(self.sm._settled_frame_pair)
        self.sm._read_appraisal_snapshot.assert_not_called()
        destination = self.cache / "scan_failures" / self.sm.session_id
        metadata = json.loads((destination / "position_00512_settle.json").read_text())
        self.assertTrue(metadata["diagnostic_only"])
        self.assertFalse(metadata["narrow_bars_stable"])
        self.assertEqual(metadata["comparison"], 6)
        self.assertEqual(metadata["sources"][1]["pokemgr_capture_started_at"], 21)
        for phase, original in zip(("settle_before", "settle_after"), frames[-2:]):
            with Image.open(destination / f"position_00512_{phase}.png") as saved:
                self.assertEqual(saved.tobytes(), original.tobytes())
        self.assertTrue(any("narrow_bars_stable=False" in line for line in logs.output))
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()
        self.sm.db.insert_pokemon.assert_not_called()
        self.assertEqual((self.sm.visited_count, self.sm.skipped_count), (311, 0))

    def test_transition_reference_and_requirement_are_retained_on_every_retry(self):
        reference = self.frame()
        self.sm._wait_for_stable_appraisal = Mock(return_value=(None, "appraisal_not_stable"))
        result = self.sm._acquire_validated_snapshot(previous_accepted=reference, require_transition=True)
        self.assertEqual(result[2], "appraisal_not_stable")
        self.assertEqual(self.sm._wait_for_stable_appraisal.call_count, 3)
        for call in self.sm._wait_for_stable_appraisal.call_args_list:
            self.assertIs(call.kwargs["previous_accepted"], reference)
            self.assertIs(call.kwargs["require_transition"], True)
            self.assertIs(call.kwargs["allow_structured_fallback"], True)
        self.assert_no_input()

    def test_other_typed_failures_do_not_gain_retry(self):
        for status in ("lost_appraisal_detail", "lost_appraisal_other", "transition_not_observed",
                       "transition_reference_missing", "transition_returned_to_previous"):
            with self.subTest(status=status):
                self.sm._wait_for_stable_appraisal = Mock(return_value=(None, status))
                self.assertEqual(self.sm._acquire_validated_snapshot()[2], status)
                self.sm._wait_for_stable_appraisal.assert_called_once()
                self.assert_no_input()

    def test_abort_pause_and_completed_pause_epoch_do_not_retry_or_save(self):
        for mode in ("abort", "pause", "pause_resume"):
            with self.subTest(mode=mode):
                self.sm._abort = self.sm._paused = False
                save = self.sm._save_appraisal_settle_failure = Mock()

                def interrupted(**kwargs):
                    if mode == "abort":
                        self.sm.abort()
                    else:
                        self.sm.pause()
                        if mode == "pause_resume":
                            self.sm.resume()
                    return None, "appraisal_not_stable"

                self.sm._wait_for_stable_appraisal = Mock(side_effect=interrupted)
                result = self.sm._acquire_validated_snapshot()
                self.assertEqual(result[2], "aborted" if mode == "abort" else "reacquire")
                self.sm._wait_for_stable_appraisal.assert_called_once()
                save.assert_not_called()
                self.assert_no_input()

    def test_source_error_is_not_retried_or_relabelled_as_instability(self):
        self.sm._wait_for_stable_appraisal = Mock(side_effect=ADBError("display changed"))
        with self.assertRaisesRegex(ADBError, "display changed"):
            self.sm._acquire_validated_snapshot()
        self.sm._wait_for_stable_appraisal.assert_called_once()
        self.assert_no_input()

    def test_prior_invalid_read_then_two_unstable_attempts_cannot_become_skippable(self):
        invalid = self.frame(replace(fixture_module.snapshot(), atk=-1))
        self.sm._wait_for_stable_appraisal = Mock(side_effect=[
            (invalid, "stable"), (None, "appraisal_not_stable"), (None, "appraisal_not_stable"),
        ])
        result = self.sm._acquire_validated_snapshot()
        self.assertEqual(result[2], "appraisal_not_stable")
        self.assertIs(result[1], invalid)
        self.assertEqual(self.sm._read_appraisal_snapshot.call_count, 1)
        self.sm._recover_cp_with_model_taps.assert_not_called()
        self.assert_no_input()

    def test_failed_settled_pair_is_cleared_before_retry_and_never_read(self):
        discarded = self.frame()
        pair = (self.frame(), self.frame())
        count = 0

        def settle(**kwargs):
            nonlocal count
            self.assertIsNone(self.sm._settled_frame_pair)
            count += 1
            self.sm._settled_frame_pair = (discarded, discarded) if count == 1 else pair
            return (None, "appraisal_not_stable") if count == 1 else (pair[1], "stable")

        self.sm._wait_for_stable_appraisal = Mock(side_effect=settle)
        result = self.sm._acquire_validated_snapshot()
        self.assertEqual(result[2], "ok")
        self.assertTrue(all(call.args[0] is not discarded
                            for call in self.sm._read_appraisal_snapshot.call_args_list))
        self.assert_no_input()

    def test_evidence_opt_out_and_disk_failure_do_not_change_terminal_status(self):
        for save_evidence in (False, True):
            with self.subTest(save=save_evidence):
                self.sm._fast_screencap = Mock(side_effect=[self.frame() for _ in range(21)])
                self.sm.reader.appraisal_bars_stable.return_value = False
                with patch("pathlib.Path.mkdir", side_effect=OSError("disk full")) as mkdir:
                    result = self.sm._acquire_validated_snapshot(save_failure_evidence=save_evidence)
                self.assertEqual(result[:3], (None, None, "appraisal_not_stable"))
                self.assertEqual(mkdir.call_count, 3 if save_evidence else 0)
                self.assert_no_input()

    def test_recovery_invalid_then_instability_cannot_accept_unconfirmed_calculated_cp(self):
        first, third = self.frame(), self.frame()
        self.sm._wait_for_stable_appraisal = Mock(side_effect=[
            (first, "stable"), (None, "appraisal_not_stable"), (third, "stable"),
        ])
        invalid = SnapshotDecision(False, "CP recovery inconclusive", snapshot=first.info["snapshot"])
        self.sm._recover_cp_with_model_taps = Mock(return_value=(invalid, first))

        result = self.sm._acquire_validated_snapshot()

        self.assertEqual(result[2], "cp_recovery_failed")
        self.assertIn("independent confirmation", result[3])
        self.sm._recover_cp_with_model_taps.assert_called_once()
        self.assert_no_input()

    def test_recovery_input_is_not_replayed_when_later_fresh_pair_confirms_cp(self):
        first = self.frame()
        pair = (self.frame(), self.frame())
        count = 0

        def settle(**kwargs):
            nonlocal count
            count += 1
            if count == 3:
                self.sm._settled_frame_pair = pair
            return [(first, "stable"), (None, "appraisal_not_stable"), (pair[1], "stable")][count - 1]

        self.sm._wait_for_stable_appraisal = Mock(side_effect=settle)
        invalid = SnapshotDecision(False, "CP recovery inconclusive", snapshot=first.info["snapshot"])
        self.sm._recover_cp_with_model_taps = Mock(return_value=(invalid, first))
        result = self.sm._acquire_validated_snapshot()
        self.assertEqual(result[2], "ok")
        self.assertIs(result[1], pair[1])
        self.sm._recover_cp_with_model_taps.assert_called_once()
        self.assert_no_input()

    def test_visible_cp_can_validate_after_prior_recovery_without_replaying_input(self):
        first = self.frame()
        visible = self.frame(replace(fixture_module.snapshot(), cp=5561))
        self.sm._wait_for_stable_appraisal = Mock(side_effect=[
            (first, "stable"), (None, "appraisal_not_stable"), (visible, "stable"),
        ])
        invalid = SnapshotDecision(False, "CP recovery inconclusive", snapshot=first.info["snapshot"])
        self.sm._recover_cp_with_model_taps = Mock(return_value=(invalid, first))
        result = self.sm._acquire_validated_snapshot()
        self.assertEqual(result[2], "ok")
        self.assertIs(result[1], visible)
        self.assertFalse(result[0].cp_source.startswith("calculated"))
        self.sm._recover_cp_with_model_taps.assert_called_once()
        self.assert_no_input()

    def test_broad_region_failure_keeps_narrow_bar_work_short_circuited(self):
        frames = [self.frame() for _ in range(7)]
        for index, image in enumerate(frames):
            image.paste("black" if index % 2 else "white", (0, 0, *image.size))
        self.sm._fast_screencap = Mock(side_effect=frames)
        result = self.sm._wait_for_stable_appraisal()
        self.assertEqual(result, (None, "appraisal_not_stable"))
        self.sm.reader.appraisal_bars_stable.assert_not_called()
        self.assertIsNone(self.sm._appraisal_settle_failure["narrow_bars_stable"])
        self.assert_no_input()

    def test_iv_conflict_anchor_survives_unstable_attempt_until_new_independent_pair(self):
        fixture = bar_fixture.BarReacquisitionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        sm = fixture.sm
        pair = (fixture.frame(), fixture.frame())
        count = 0

        def settle(**kwargs):
            nonlocal count
            count += 1
            if count == 3:
                sm._settled_frame_pair = pair
            return [(fixture.initial, "stable"), (None, "appraisal_not_stable"), (pair[1], "stable")][count - 1]

        sm._wait_for_stable_appraisal = Mock(side_effect=settle)
        result = sm._acquire_validated_snapshot()
        self.assertEqual(result[2], "ok")
        self.assertEqual(result[0].snapshot.ivs, fixture.changed.ivs)
        self.assertIs(result[1], pair[1])
        sm._fast_screencap.assert_called_once()
        fixture.assert_no_mutations()

    def test_invalidated_final_stream_window_does_not_publish_stale_diagnostic_pair(self):
        fixture = stream_fixture.StreamAppraisalSettleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        frames = [stream_fixture.frame(i + 1, 10_000 + i * 150_000) for i in range(7)]
        fixture.sm.reader.appraisal_bars_stable.return_value = False
        fixture.windows([frames], on_close=Mock(side_effect=StreamCaptureInvalidated("clock changed")))
        fixture.fallback([stream_fixture.frame(streamed=False)])
        result = fixture.sm._wait_for_stable_appraisal()
        self.assertEqual(result, (None, "appraisal_not_stable"))
        self.assertIsNone(fixture.sm._settled_frame_pair)
        self.assertIsNone(fixture.sm._appraisal_settle_failure)


if __name__ == "__main__":
    unittest.main()
