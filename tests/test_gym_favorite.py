"""Gym defenders missing HP can be preserved for review without inventing a row."""

# TRACEWEAVER: file-role=gym-favorite-tests; req=REQ-SCAN-003; trace=TRACE-SCAN-003; verifies=VER-SCAN-001
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from tests import test_favorite_unresolved as favorite_fixture
from tests.test_favorite_unresolved import frame, snapshot
from tests.test_stable_scan_loop import _DB, accepted


# TRACEWEAVER: verifies=VER-SCAN-001; req=REQ-SCAN-003; trace=TRACE-SCAN-003
class GymFavoriteTests(unittest.TestCase):
    def setUp(self):
        fixture = favorite_fixture.FavoriteUnresolvedTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.sm, self.adb, self.db = fixture.sm, fixture.adb, fixture.db
        self.star, self.delay = fixture.star, fixture.delay
        self.original = snapshot(
            display_name="Pikachu", detected_species="Pikachu", caught_species="Pikachu",
            cp=-1, hp=-1, atk=13, def_=14, sta=15, in_gym=True,
        )
        self.initial = frame(self.original)
        self.fresh = frame(self.original)
        self.confirmed = frame(replace(self.original, favorited=True), "on")
        self.sm._fast_screencap = Mock(side_effect=[self.fresh, self.confirmed])

    def favorite(self):
        return self.sm._favorite_unresolved_snapshot(self.initial)

    def assert_no_position_or_database_changes(self):
        self.adb.swipe.assert_not_called()
        self.db.insert_pokemon.assert_not_called()
        self.assertEqual((0, 0, 0), (self.sm.count, self.sm.visited_count,
                                    self.sm.skipped_count))

    def test_missing_hp_gym_defender_gets_one_verified_favorite_without_a_record(self):
        self.assertIsNone(self.sm._cp_recovery_identity(self.original))

        result = self.favorite()

        self.assertIs(self.confirmed, result)
        self.adb.tap.assert_called_once_with(*self.sm.regions.favorite_star_region.center,
                                             jitter=0)
        self.assertEqual(2, self.sm._fast_screencap.call_count)
        self.assertEqual(3, self.sm._read_appraisal_snapshot.call_count)
        self.assert_no_position_or_database_changes()

    def test_already_favorited_gym_defender_needs_no_input(self):
        self.fresh.info["star"] = "on"

        self.assertIs(self.fresh, self.favorite())

        self.adb.tap.assert_not_called()
        self.sm._fast_screencap.assert_called_once()
        self.assert_no_position_or_database_changes()

    def test_missing_hp_without_explicit_gym_evidence_stays_held(self):
        for evidence in (False, None, 1, "true"):
            with self.subTest(in_gym=evidence):
                self.initial.info["snapshot"] = replace(self.original, in_gym=evidence)
                self.sm._fast_screencap.reset_mock()
                self.sm._fast_screencap.side_effect = [self.fresh, self.confirmed]
                self.adb.tap.reset_mock()

                with self.assertRaises(RuntimeError):
                    self.favorite()

                self.sm._fast_screencap.assert_not_called()
                self.adb.tap.assert_not_called()
                self.assert_no_position_or_database_changes()

    def test_incomplete_gym_identity_cannot_authorize_favorite(self):
        changes = (
            {"read_complete": False}, {"display_name": ""}, {"display_name": "   "},
            {"detected_species": ""}, {"caught_species": ""},
            {"caught_species": "Raichu"}, {"atk": -1}, {"def_": 16}, {"sta": -1},
        )
        for change in changes:
            with self.subTest(change=change):
                self.initial.info["snapshot"] = replace(self.original, **change)

                with self.assertRaises(RuntimeError):
                    self.favorite()

                self.sm._fast_screencap.assert_not_called()
                self.adb.tap.assert_not_called()
                self.assert_no_position_or_database_changes()

    def test_changed_gym_name_species_or_iv_before_star_prevents_input(self):
        changes = (
            {"in_gym": False}, {"display_name": "Pikacbu"},
            {"detected_species": "Raichu"}, {"caught_species": "Raichu"},
            {"caught_species": ""}, {"atk": 12}, {"def_": 13}, {"sta": 14},
        )
        for change in changes:
            with self.subTest(change=change):
                self.fresh.info["snapshot"] = replace(self.original, **change)
                self.sm._fast_screencap.side_effect = [self.fresh]

                with self.assertRaises(RuntimeError):
                    self.favorite()

                self.adb.tap.assert_not_called()
                self.assert_no_position_or_database_changes()

    def test_changed_gym_identity_after_tap_cannot_be_confirmed_by_gold_star(self):
        self.confirmed.info["snapshot"] = replace(self.original, atk=12, favorited=True)

        with self.assertRaises(RuntimeError):
            self.favorite()

        self.adb.tap.assert_called_once()
        self.assert_no_position_or_database_changes()

    def test_gym_favorite_requires_stable_pixels_before_and_after_star(self):
        for phase in ("before", "after"):
            with self.subTest(phase=phase):
                self.adb.tap.reset_mock()
                changed = Image.new("RGB", self.initial.size, "black")
                changed.info.update(snapshot=self.original, star="on", screen="appraisal")
                self.sm._fast_screencap.side_effect = (
                    [changed] if phase == "before" else [self.fresh, changed]
                )

                with self.assertRaises(RuntimeError):
                    self.favorite()

                self.assertEqual(0 if phase == "before" else 1, self.adb.tap.call_count)
                self.assert_no_position_or_database_changes()

    def test_failed_gym_favorite_readback_never_repeats_the_toggle(self):
        self.sm._fast_screencap.side_effect = [
            self.fresh, *[frame(self.original, "off") for _ in range(3)],
        ]

        with self.assertRaises(RuntimeError):
            self.favorite()

        self.adb.tap.assert_called_once()
        self.assertEqual(4, self.sm._fast_screencap.call_count)
        self.assert_no_position_or_database_changes()

    def test_pause_before_gym_review_waits_then_rechecks_without_input(self):
        self.sm.pause()
        self.fresh.info["star"] = "on"

        def resume(_seconds):
            self.adb.tap.assert_not_called()
            self.sm._fast_screencap.assert_not_called()
            self.sm.resume()

        with patch("pokemgr.indexer.state_machine.time.sleep", side_effect=resume) as wait:
            self.assertIs(self.fresh, self.favorite())

        wait.assert_called_once_with(0.25)
        self.adb.tap.assert_not_called()
        self.sm._fast_screencap.assert_called_once()
        self.assert_no_position_or_database_changes()

    def test_pause_or_resume_during_initial_or_fresh_read_retries_same_review(self):
        for phase in ("initial", "fresh"):
            for resume in (False, True):
                with self.subTest(phase=phase, resumed=resume):
                    self.sm._paused = False
                    self.adb.tap.reset_mock()
                    refreshed = frame(self.original)
                    self.sm._fast_screencap.side_effect = (
                        [self.fresh, self.confirmed] if phase == "initial"
                        else [self.fresh, refreshed, self.confirmed]
                    )
                    target = self.initial if phase == "initial" else self.fresh
                    interrupted = False

                    def read(image):
                        nonlocal interrupted
                        value = image.info["snapshot"]
                        if image is target and not interrupted:
                            interrupted = True
                            self.sm.pause()
                            if resume:
                                self.sm.resume()
                        return (value.as_detail() | {"snapshot_read_complete": value.read_complete},
                                value.as_appraisal())

                    self.sm._read_appraisal_snapshot = Mock(side_effect=read)
                    def resume_wait(_seconds):
                        self.adb.tap.assert_not_called()
                        self.sm.resume()

                    with patch("pokemgr.indexer.state_machine.time.sleep", side_effect=resume_wait):
                        self.assertIs(self.confirmed, self.favorite())

                    self.adb.tap.assert_called_once()
                    self.assertIn(self.confirmed, [call.args[0] for call in
                                                  self.sm._read_appraisal_snapshot.call_args_list])
                    self.assert_no_position_or_database_changes()

    def test_abort_during_gym_star_tap_stops_before_readback(self):
        self.adb.tap.side_effect = lambda *_args, **_kwargs: self.sm.abort()

        self.assertIsNone(self.favorite())

        self.adb.tap.assert_called_once()
        self.sm._fast_screencap.assert_called_once()
        self.assert_no_position_or_database_changes()

    def test_pause_during_star_classification_requires_fresh_state_without_retoggling(self):
        for phase, star in (("fresh", "off"), ("fresh", "on"), ("confirmed", "on")):
            with self.subTest(phase=phase, star=star):
                self.adb.tap.reset_mock()
                self.fresh.info["star"] = star if phase == "fresh" else "off"
                refreshed = frame(replace(self.original, favorited=True), "on")
                self.sm._fast_screencap.side_effect = (
                    [self.fresh, refreshed] if phase == "fresh"
                    else [self.fresh, self.confirmed, refreshed]
                )
                target = self.fresh if phase == "fresh" else self.confirmed

                def classify(image, _region):
                    if image is target:
                        self.sm.pause()
                        self.sm.resume()
                    return image.info["star"]

                self.star.side_effect = classify
                self.assertIs(refreshed, self.favorite())

                self.assertEqual(1 if phase == "confirmed" else 0, self.adb.tap.call_count)
                self.assert_no_position_or_database_changes()

    def test_pause_during_tap_keeps_bounded_readback_and_never_toggles_again(self):
        self.adb.tap.side_effect = lambda *_args, **_kwargs: self.sm.pause()
        self.sm._fast_screencap.side_effect = [
            self.fresh, *[frame(self.original, "off") for _ in range(3)],
        ]

        def resume(_seconds):
            self.adb.tap.assert_called_once()
            self.sm._fast_screencap.assert_called_once()
            self.sm.resume()

        with patch("pokemgr.indexer.state_machine.time.sleep", side_effect=resume):
            with self.assertRaisesRegex(RuntimeError, "after one tap"):
                self.favorite()

        self.adb.tap.assert_called_once()
        self.assertEqual(4, self.sm._fast_screencap.call_count)
        self.assert_no_position_or_database_changes()

    def test_abort_while_paused_before_or_after_tap_sends_no_later_input(self):
        for after_tap in (False, True):
            with self.subTest(after_tap=after_tap):
                self.sm._abort = self.sm._paused = False
                self.adb.tap.reset_mock()
                self.sm._fast_screencap.reset_mock()
                self.sm._fast_screencap.side_effect = [self.fresh]
                self.adb.tap.side_effect = lambda *_args, **_kwargs: self.sm.pause()
                if not after_tap:
                    self.sm.pause()
                with patch("pokemgr.indexer.state_machine.time.sleep",
                           side_effect=lambda _seconds: self.sm.abort()):
                    self.assertIsNone(self.favorite())

                self.assertEqual(int(after_tap), self.adb.tap.call_count)
                self.assertEqual(int(after_tap), self.sm._fast_screencap.call_count)
                self.assert_no_position_or_database_changes()

    def test_gym_acquisition_never_uses_cp_ocr_or_recovery_even_with_stray_hp(self):
        self.sm.reader._native_enabled = True
        self.sm.reader.read_cp = Mock(side_effect=AssertionError("Gym CP must not be OCR'd"))
        self.sm.reader.native_cp = Mock(side_effect=AssertionError("Gym CP must not be OCR'd"))
        self.sm._recover_cp_with_model_taps = Mock(
            side_effect=AssertionError("Gym identity must not authorize CP recovery"),
        )
        self.sm._wait_for_stable_appraisal = Mock(return_value=(self.initial, "stable"))
        self.sm._fast_screencap = Mock(side_effect=AssertionError("No extra gym CP capture"))
        for hp, cp in ((-1, -1), (60, -1), (-1, 500), (60, 500)):
            with self.subTest(hp=hp, cp=cp):
                observed = replace(self.original, hp=hp, cp=cp)
                self.initial.info["snapshot"] = observed
                self.assertIsNone(self.sm._cp_recovery_identity(observed))

                decision, image, kind, _reason = self.sm._acquire_validated_snapshot(
                    save_failure_evidence=False,
                )

                self.assertIsNone(decision)
                self.assertIs(self.initial, image)
                self.assertEqual("invalid", kind)
                self.sm.reader.read_cp.assert_not_called()
                self.sm.reader.native_cp.assert_not_called()
                self.sm._recover_cp_with_model_taps.assert_not_called()
                self.sm._fast_screencap.assert_not_called()
                self.adb.tap.assert_not_called()
                self.assert_no_position_or_database_changes()

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_scan_favorites_and_counts_gym_skip_once_then_stores_next_pokemon(self, _paddle):
        db = _DB()
        self.sm.db = db
        self.sm.reader.prepare_native_ocr = Mock()
        next_decision = accepted()
        self.sm._acquire_validated_snapshot = Mock(side_effect=[
            (None, self.initial, "invalid", "gym defender has no readable HP"),
            (next_decision, frame(next_decision.snapshot), "ok", "exact"),
        ])

        def advance():
            self.assertEqual((0, 1, 1), (self.sm.count, self.sm.visited_count,
                                        self.sm.skipped_count))
            self.assertEqual([], db.rows)
            self.assertIs(self.confirmed, self.sm._last_stable_image)
            self.assertIsNone(self.sm._last_validated_identity_key)
            self.assertIsNone(self.sm._last_accepted_image)
            return True

        self.sm._advance_from_confirmed_appraisal = Mock(side_effect=advance)
        self.sm.start(expected_total=2)

        self.assertEqual((1, 2, 1), (self.sm.count, self.sm.visited_count,
                                    self.sm.skipped_count))
        self.adb.tap.assert_called_once()
        self.sm._advance_from_confirmed_appraisal.assert_called_once()
        self.assertEqual(1, len(db.rows))
        self.assertEqual(("Zubat", 10, 1), (db.rows[0][0].species, db.rows[0][0].cp,
                                          db.rows[0][2]))


if __name__ == "__main__":
    unittest.main()
