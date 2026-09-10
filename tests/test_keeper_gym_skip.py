"""Keeper passes skip a confirmed gym defender and continue to a valid keeper."""

# TRACEWEAVER: file-role=keeper-gym-skip-tests; req=REQ-MASS-001,REQ-SCAN-003; trace=TRACE-MASS-001,TRACE-SCAN-003; verifies=VER-SCAN-001

from collections import Counter
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from pokemgr.execution.executor import Executor
from pokemgr.execution.favorite_sync import FavoriteSync
from pokemgr.indexer.snapshot import SnapshotDecision
from tests.test_action_preswipe_retry import sourced_frame
from tests.test_mass_action_scanning import pokemon, snapshot
from tests.test_stable_scan_loop import profile


class KeeperGymSkipTests(unittest.TestCase):
    def setUp(self):
        self.adb, self.db = Mock(), Mock()
        self.executor = Executor(self.adb, profile(), self.db)
        self.scanner = self.executor._scanner
        self.reader = self.executor.reader
        self.reader.prepare_native_ocr = Mock()
        self.reader.are_bars_visible = Mock(return_value=True)
        self.reader.appraisal_bars_stable = Mock(return_value=True)
        self.reader.read_cp = Mock(side_effect=AssertionError("No CP recovery is needed"))
        self.executor.nav.detect_screen = Mock(side_effect=lambda image: image.info["screen"])
        self.scanner._read_appraisal_snapshot = Mock(side_effect=lambda image: (
            image.info["read"].as_detail()
            | {"snapshot_read_complete": image.info["read"].read_complete},
            image.info["read"].as_appraisal(),
        ))
        self.scanner._save_failed_appraisal = Mock()
        self.executor._open_pass = Mock(return_value=2)
        self.executor._set_star = Mock(wraps=self.executor._set_star)
        self.executor._advance_action = Mock(wraps=self.executor._advance_action)
        self.executor._acquire_identity = Mock(wraps=self.executor._acquire_identity)
        self.gym = snapshot(
            display_name="Voltorb", detected_species="Voltorb", caught_species="Voltorb",
            hp=-1, cp=-1, atk=13, def_=14, sta=14, in_gym=True,
        )
        self.keeper = snapshot()
        self.stored_gym = replace(self.gym, hp=80, cp=500, in_gym=False)
        rows = [pokemon(self.stored_gym, id=1), pokemon(self.keeper, id=2)]
        self.sync = self.executor._favorite_sync = FavoriteSync(self.db, rows, Executor._keeper_key)
        self.gym_key = self.executor._snapshot_keeper_key(self.stored_gym)
        self.keeper_key = self.executor._snapshot_keeper_key(self.keeper)
        self.remaining = Counter({self.gym_key: 1, self.keeper_key: 1})
        self.gym_initial = sourced_frame(self.gym, 1)
        self.gym_pair = (sourced_frame(self.gym, 2), sourced_frame(self.gym, 3))
        self.gym_before_advance = sourced_frame(self.gym, 4)
        self.keeper_frame = sourced_frame(self.keeper, 5)
        self.keeper_before_star = sourced_frame(self.keeper, 6)
        self.keeper_after_star = sourced_frame(replace(self.keeper, favorited=True), 7)
        self.keeper_after_star.info["star"] = "on"
        self.scanner._acquire_validated_snapshot = Mock(side_effect=[
            (None, self.gym_initial, "invalid", "defending gym hides full HP and storage CP"),
            (SnapshotDecision(True, "exact", self.keeper, cp_source="calculated"),
             self.keeper_frame, "ok", "exact"),
        ])

        def settled(**_kwargs):
            self.scanner._settled_frame_pair = self.gym_pair
            return self.gym_pair[1], "stable"

        self.scanner._wait_for_stable_appraisal = Mock(side_effect=settled)
        self.scanner._fast_screencap = Mock(side_effect=[
            self.gym_before_advance, self.keeper_before_star, self.keeper_after_star,
        ])

        def advance(image, *, pause_generation):
            # Before reaching the second Pokemon, neither the gym star nor
            # any saved row or keeper allowance may have changed.
            self.assertIs(image, self.gym_before_advance)
            self.assertEqual(0, pause_generation)
            self.adb.tap.assert_not_called()
            self.assertEqual([], self.db.mock_calls)
            self.executor._set_star.assert_not_called()
            self.assertEqual(Counter({self.gym_key: 1, self.keeper_key: 1}), self.remaining)
            return True

        self.scanner._advance_appraisal = Mock(side_effect=advance)
        self.enterContext(patch("pokemgr.execution.executor.time.sleep"))
        self.enterContext(patch("pokemgr.execution.executor.favorite_state",
                                side_effect=lambda image, _region: image.info["star"]))

    def run_pass(self, *, dry_run=False):
        return self.executor._run_pass("cp500", True, keepers=self.remaining, dry_run=dry_run)

    def assert_continued_to_keeper(self, result):
        self.assertNotIn("error", result)
        self.assertEqual((2, 1, 1), (result["checked"], result["skipped"], result["favorited"]))
        self.assertEqual(Counter({self.gym_key: 1}), self.remaining)
        self.executor._acquire_identity.assert_called_once()
        self.assertIs(True, self.executor._acquire_identity.call_args.kwargs["allow_gym_skip"])
        self.executor._advance_action.assert_called_once()
        self.assertIs(True, self.executor._advance_action.call_args.kwargs["allow_gym_skip"])
        self.executor._set_star.assert_called_once()
        self.assertEqual(self.keeper, self.executor._set_star.call_args.args[0])
        self.scanner._acquire_validated_snapshot.assert_called()
        self.assertEqual(2, self.scanner._acquire_validated_snapshot.call_count)
        self.assertIs(True, self.scanner._acquire_validated_snapshot.call_args.kwargs["require_transition"])
        self.assertIs(self.gym_before_advance,
                      self.scanner._acquire_validated_snapshot.call_args.kwargs["previous_accepted"])
        self.reader.read_cp.assert_not_called()

    def test_gym_skip_continues_then_favorites_and_saves_only_valid_keeper(self):
        result = self.run_pass()

        self.assert_continued_to_keeper(result)
        self.adb.tap.assert_called_once_with(*self.executor.regions.favorite_star_region.center, jitter=0)
        self.db.update_favorited_many.assert_called_once_with([2], True)
        self.assertEqual({2}, self.sync.synced)
        self.assertEqual(Counter({self.keeper_key: 1}), self.sync.all_observations)

    def test_dry_run_skips_gym_and_reaches_keeper_without_star_or_database_input(self):
        result = self.run_pass(dry_run=True)

        self.assert_continued_to_keeper(result)
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()
        self.assertEqual([], self.db.mock_calls)
        self.assertEqual(0, self.sync.confirmed)

    def test_incomplete_non_gym_read_still_holds_before_advancing_or_consuming_keeper(self):
        ordinary = replace(self.gym, in_gym=False)
        self.gym_pair = (sourced_frame(ordinary, 2), sourced_frame(ordinary, 3))

        result = self.run_pass()

        self.assertIn("identity did not agree", result["error"])
        self.assertEqual((0, 0, 0), (result["checked"], result["skipped"], result["favorited"]))
        self.assertEqual(Counter({self.gym_key: 1, self.keeper_key: 1}), self.remaining)
        self.executor._set_star.assert_not_called()
        self.executor._advance_action.assert_not_called()
        self.scanner._advance_appraisal.assert_not_called()
        self.scanner._acquire_validated_snapshot.assert_called_once()
        self.adb.tap.assert_not_called()
        self.assertEqual([], self.db.mock_calls)


if __name__ == "__main__":
    unittest.main()
