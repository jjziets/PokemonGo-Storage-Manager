"""An optional scan star removal is persisted only after matching OFF evidence."""

# TRACEWEAVER: file-role=scan-star-sync-tests; req=REQ-SCAN-003,REQ-MASS-001; trace=TRACE-SCAN-003; verifies=VER-SCAN-001

from dataclasses import replace
from contextlib import closing
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from pokemgr.data.database import PokemonDatabase
from pokemgr.indexer.snapshot import SnapshotDecision
from pokemgr.indexer.state_machine import IndexingStateMachine
from tests.test_stable_scan_loop import _ADB, _DB, accepted, profile


class ScanStarSyncTests(unittest.TestCase):
    def setUp(self):
        self.adb, self.db = _ADB(), _DB()
        self.db.flush = Mock()
        self.sm = IndexingStateMachine(self.adb, profile(), self.db)
        base = accepted()
        self.decision = replace(base, snapshot=replace(base.snapshot, favorited=True))
        self.sm.unfavorite_all = True
        self.initial = self.frame("on")
        self.sm.reader.prepare_native_ocr = Mock()
        self.sm.nav.detect_screen = Mock(side_effect=lambda image: image.info["screen"])
        self.sm.reader.are_bars_visible = Mock(return_value=True)
        self.sm.reader.appraisal_bars_stable = Mock(return_value=True)
        self.sm._read_appraisal_snapshot = Mock(side_effect=self.read_snapshot)
        self.sm._validate_appraisal_snapshot = Mock(
            side_effect=lambda _snapshot, image: image.info["decision"],
        )
        self.sm._acquire_validated_snapshot = Mock(return_value=(self.decision, self.initial, "ok", "exact"))
        self.sm._advance_from_confirmed_appraisal = Mock(side_effect=AssertionError("Single position must not advance"))
        self.sm.on_progress = Mock()
        self.enterContext(patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None))
        self.enterContext(patch("pokemgr.indexer.state_machine.human_delay"))
        self.star = self.enterContext(patch(
            "pokemgr.reader.icons.favorite_state", side_effect=lambda image, _region: image.info["star"],
        ))

    def frame(self, state, *, decision=None, screen="appraisal"):
        image = Image.new("RGB", (96, 237), "white")
        image.info.update(star=state, decision=decision or self.decision, screen=screen)
        return image

    @staticmethod
    def read_snapshot(image):
        snapshot = image.info["decision"].snapshot
        return snapshot.as_detail(), snapshot.as_appraisal()

    def scan(self, states):
        frames = [state if isinstance(state, Image.Image) else self.frame(state) for state in states]
        self.sm._fast_screencap = Mock(side_effect=frames)
        self.sm.start(expected_total=1)
        return frames

    def assert_saved_off(self):
        self.assertEqual(1, len(self.db.rows))
        self.assertFalse(self.db.rows[0][0].favorited)
        self.assertFalse(self.sm.on_progress.call_args.args[1].favorited)
        self.assertEqual((1, 1), (self.sm.count, self.sm.visited_count))
        self.sm._advance_from_confirmed_appraisal.assert_not_called()

    def test_confirmed_removal_is_saved_off_after_one_zero_jitter_tap(self):
        frames = self.scan(["on", "off"])
        self.assert_saved_off()
        self.assertEqual([("tap", self.sm.regions.favorite_star_region.center, {"jitter": 0})], self.adb.actions)
        self.assertIs(frames[-1], self.sm._last_stable_image)
        self.assertTrue(self.decision.snapshot.favorited)
        self.db.flush.assert_called_once_with()

    def test_stale_on_snapshot_with_fresh_off_is_saved_without_toggling(self):
        self.scan(["off"])
        self.assert_saved_off()
        self.assertEqual([], self.adb.actions)
        self.db.flush.assert_called_once_with()

    def test_unknown_before_input_can_recover_but_never_authorizes_a_tap(self):
        self.scan(["unknown", "on", "off"])
        self.assert_saved_off()
        self.assertEqual(1, len(self.adb.actions))

    def test_unknown_before_input_exhausts_without_tap_or_storage(self):
        with self.assertRaisesRegex(RuntimeError, "star was not confirmed before input"):
            self.scan(["unknown"] * 3)
        self.assertEqual([], self.adb.actions)
        self.assertEqual([], self.db.rows)
        self.assertEqual((0, 0), (self.sm.count, self.sm.visited_count))

    def test_unknown_or_still_on_after_one_tap_is_never_saved_or_retapped(self):
        for state in ("unknown", "on"):
            with self.subTest(state=state):
                self.adb.actions.clear()
                with self.assertRaisesRegex(RuntimeError, "after one tap"):
                    self.scan(["on", state, state, state])
                self.assertEqual(1, len(self.adb.actions))
                self.assertEqual([], self.db.rows)
                self.sm.on_progress.assert_not_called()

    def test_delayed_off_readback_stays_in_confirmation_phase(self):
        self.scan(["on", "on", "unknown", "off"])
        self.assert_saved_off()
        self.assertEqual(1, len(self.adb.actions))

    def test_identity_changes_before_or_after_tap_hold_before_storage(self):
        for after_tap in (False, True):
            for changes in ({"cp": 11}, {"hp": 13}, {"atk": 1},
                            {"detected_species": "Pidgey"}, {"display_name": "Nickname"},
                            {"gender": "female"}):
                with self.subTest(after_tap=after_tap, changes=changes):
                    self.adb.actions.clear()
                    changed = replace(self.decision, snapshot=replace(self.decision.snapshot, **changes))
                    frames = [self.frame("off", decision=changed)]
                    if after_tap:
                        frames.insert(0, self.frame("on"))
                    with self.assertRaisesRegex(RuntimeError, "identity changed"):
                        self.scan(frames)
                    self.assertEqual(int(after_tap), len(self.adb.actions))
                    self.assertEqual([], self.db.rows)

    def test_unresolved_cp_or_lost_appraisal_never_persists_a_removal(self):
        for lost_screen in (False, True):
            with self.subTest(lost_screen=lost_screen):
                self.adb.actions.clear()
                image = self.frame("off", screen="detail" if lost_screen else "appraisal")
                if not lost_screen:
                    self.sm._validate_appraisal_snapshot.side_effect = (
                        lambda _snapshot, frame: self.decision if frame.info["star"] == "on"
                        else SnapshotDecision(False, "unreadable CP")
                    )
                else:
                    self.sm._validate_appraisal_snapshot.side_effect = lambda _snapshot, frame: frame.info["decision"]
                with self.assertRaisesRegex(RuntimeError, "identity changed|appraisal not confirmed"):
                    self.scan(["on", image])
                self.assertEqual(1, len(self.adb.actions))
                self.assertEqual([], self.db.rows)

    def test_unsettled_bars_after_tap_hold_without_saving(self):
        self.sm.reader.appraisal_bars_stable.side_effect = [True, False]
        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            self.scan(["on", "off"])
        self.assertEqual(1, len(self.adb.actions))
        self.assertEqual([], self.db.rows)

    def test_pause_during_preinput_read_reacquires_before_one_tap(self):
        first, second, final = self.frame("on"), self.frame("on"), self.frame("off")
        def read(image):
            if image is first:
                self.sm.pause()
                self.sm.resume()
            return self.read_snapshot(image)
        self.sm._read_appraisal_snapshot.side_effect = read
        self.scan([first, second, final])
        self.assert_saved_off()
        self.assertEqual(1, len(self.adb.actions))

    def test_pause_during_tap_waits_for_readback_without_toggling_again(self):
        original_tap = self.adb.tap
        def tap(*args, **kwargs):
            original_tap(*args, **kwargs)
            self.sm.pause()
        self.adb.tap = tap
        with patch("pokemgr.indexer.state_machine.time.sleep", side_effect=lambda _delay: self.sm.resume()):
            self.scan(["on", "off"])
        self.assert_saved_off()
        self.assertEqual(1, len(self.adb.actions))

    def test_pause_during_off_readback_discards_that_evidence_without_retap(self):
        initial, interrupted, final = self.frame("on"), self.frame("off"), self.frame("off")
        def read(image):
            if image is interrupted:
                self.sm.pause()
                self.sm.resume()
            return self.read_snapshot(image)
        self.sm._read_appraisal_snapshot.side_effect = read
        self.scan([initial, interrupted, final])
        self.assert_saved_off()
        self.assertEqual(1, len(self.adb.actions))
        self.assertIs(final, self.sm._last_stable_image)

    def test_abort_during_tap_never_saves_an_unconfirmed_off_value(self):
        original_tap = self.adb.tap
        def tap(*args, **kwargs):
            original_tap(*args, **kwargs)
            self.sm.abort()
        self.adb.tap = tap
        self.scan(["on"])
        self.assertEqual(1, len(self.adb.actions))
        self.assertEqual([], self.db.rows)
        self.assertEqual(0, self.sm.count)

    def test_transport_error_after_possible_tap_cannot_retry_or_save(self):
        def tap(*args, **kwargs):
            self.adb.actions.append(("tap", args, kwargs))
            raise RuntimeError("uncertain input transport")
        self.adb.tap = tap
        with self.assertRaisesRegex(RuntimeError, "uncertain input transport"):
            self.scan(["on"])
        self.assertEqual(1, len(self.adb.actions))
        self.assertEqual([], self.db.rows)

    def test_confirmed_first_change_remains_saved_when_next_change_is_uncertain(self):
        second = replace(self.decision, snapshot=replace(self.decision.snapshot, cp=11))
        second_reference = self.frame("on", decision=second)
        self.sm._acquire_validated_snapshot.side_effect = [
            (self.decision, self.initial, "ok", "exact"),
            (second, second_reference, "ok", "exact"),
        ]
        self.sm._advance_from_confirmed_appraisal.side_effect = None
        self.sm._advance_from_confirmed_appraisal.return_value = True
        self.sm._fast_screencap = Mock(side_effect=[
            self.frame("on"), self.frame("off"), self.frame("on", decision=second),
            *[self.frame("unknown", decision=second) for _ in range(3)],
        ])
        with self.assertRaisesRegex(RuntimeError, "after one tap"):
            self.sm.start(expected_total=2)
        self.assertEqual(1, len(self.db.rows))
        self.assertFalse(self.db.rows[0][0].favorited)
        self.assertEqual(self.decision.snapshot.cp, self.db.rows[0][0].cp)
        self.assertEqual((1, 1), (self.sm.count, self.sm.visited_count))
        self.assertEqual(2, len(self.adb.actions))
        self.sm._advance_from_confirmed_appraisal.assert_called_once()

    def test_regular_scan_does_not_capture_or_verify_stars(self):
        self.sm.unfavorite_all = False
        self.scan([])
        self.assertEqual(1, len(self.db.rows))
        self.assertTrue(self.db.rows[0][0].favorited)
        self.sm._fast_screencap.assert_not_called()
        self.star.assert_not_called()
        self.assertEqual([], self.adb.actions)
        self.db.flush.assert_not_called()

    def test_already_unstarred_scan_retains_its_existing_fast_path(self):
        self.decision = replace(self.decision, snapshot=replace(self.decision.snapshot, favorited=False))
        self.sm._acquire_validated_snapshot.return_value = self.decision, self.initial, "ok", "exact"
        self.scan([])
        self.assert_saved_off()
        self.sm._fast_screencap.assert_not_called()
        self.star.assert_not_called()
        self.assertEqual([], self.adb.actions)
        self.db.flush.assert_not_called()

    def test_confirmed_removal_is_committed_before_next_navigation(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = f"{directory}/scan.sqlite3"
        database = PokemonDatabase(path)
        self.addCleanup(database.close)
        observer = self.enterContext(closing(sqlite3.connect(path)))
        self.sm.db = database
        self.sm._fast_screencap = Mock(side_effect=[self.frame("on"), self.frame("off")])

        def advance():
            # The session is still running and its first batch has only one
            # row. An independent connection must already see verified OFF.
            self.assertEqual(
                [(0,)], observer.execute("SELECT favorited FROM pokemon").fetchall(),
            )
            self.assertEqual(
                [(None,)], observer.execute("SELECT completed_at FROM scan_sessions").fetchall(),
            )
            self.sm.abort()
            return True

        self.sm._advance_from_confirmed_appraisal.side_effect = advance
        self.sm.start(expected_total=2)

        self.sm._advance_from_confirmed_appraisal.assert_called_once_with()
        self.assertEqual(1, len(self.adb.actions))


if __name__ == "__main__":
    unittest.main()
