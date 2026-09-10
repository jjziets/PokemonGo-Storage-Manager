"""Caught-name quote drift may preserve a specimen, never invent its identity."""

# TRACEWEAVER: file-role=caught-name-review-tests; req=REQ-SCAN-003; trace=TRACE-SCAN-003; verifies=VER-SCAN-001
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from pokemgr.indexer.snapshot import SnapshotDecision
from pokemgr.indexer.state_machine import _AppraisalConfirmationInvalidated, _UnresolvedAppraisalName
from tests import test_favorite_unresolved as favorite_fixture
from tests.test_favorite_unresolved import frame, snapshot


class CaughtNameReviewTests(unittest.TestCase):
    def setUp(self):
        fixture = favorite_fixture.FavoriteUnresolvedTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.sm, self.adb, self.db = fixture.sm, fixture.adb, fixture.db
        self.star, self.delay = fixture.star, fixture.delay
        self.original = snapshot(
            display_name="Nidorano", detected_species="Nidorano'", caught_species="Nidorano'",
            hp=94, atk=12, def_=15, sta=15, gender="male",
        )
        self.plain = replace(self.original, detected_species="Nidorano", caught_species="Nidorano")
        self.initial = frame(self.original)
        self.sm._save_failed_appraisal = Mock()
        self.sm._validate_appraisal_snapshot = Mock()
        self.sm.reader.read_cp = Mock(side_effect=AssertionError("No CP OCR during name review"))
        self.sm.reader.read_detail_screen = Mock(side_effect=AssertionError("No detail visit"))
        self.sm._read_cp_from_powerup_preview = Mock(side_effect=AssertionError("No preview"))
        self.sm._reopen_appraisal = Mock(side_effect=AssertionError("No appraisal navigation"))

    def recover(self):
        return self.sm._recover_cp_with_model_taps(self.original, self.initial)

    def assert_no_recovery_actions(self):
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()
        self.sm.reader.read_cp.assert_not_called()
        self.sm.reader.read_detail_screen.assert_not_called()
        self.sm._read_cp_from_powerup_preview.assert_not_called()
        self.sm._reopen_appraisal.assert_not_called()
        self.db.insert_pokemon.assert_not_called()

    def test_quote_variants_match_only_for_review(self):
        for caught in ("Nidorano", "Nidorano\u2019", 'Nidorano"', " NIDORANO "):
            with self.subTest(caught=caught):
                current = replace(self.original, caught_species=caught, detected_species=caught)
                self.assertTrue(self.sm._review_name_drift(
                    self.original, current, self.initial, frame(current),
                ))
                self.assertNotEqual(self.sm._cp_recovery_identity(self.original),
                                    self.sm._cp_recovery_identity(current))

    def test_latched_review_survives_return_to_original_spelling_without_cp(self):
        captures = [frame(value) for value in (self.plain, self.original, self.original)]
        self.sm._fast_screencap = Mock(side_effect=captures)

        with self.assertRaises(_UnresolvedAppraisalName) as raised:
            self.recover()

        self.assertIs(captures[-1], raised.exception.frame)
        self.assertIs(True, captures[-1].info["pokemgr_review_caught_name_drift"])
        self.assertEqual(3, self.sm._fast_screencap.call_count)
        self.sm._validate_appraisal_snapshot.assert_not_called()
        self.assert_no_recovery_actions()

    def test_changed_non_name_evidence_does_not_enter_review(self):
        changes = (
            {"hp": 95}, {"hp": -1}, {"atk": 11}, {"def_": 14}, {"sta": 14},
            {"atk": -1}, {"sta": 16}, {"gender": "female"}, {"gender": "none"},
            {"shiny": True}, {"shadow": True}, {"lucky": True}, {"is_dynamax": True},
            {"in_gym": True}, {"candy_family": "Nidoran"}, {"read_complete": False},
            {"caught_species": ""}, {"detected_species": "Nidoran Female"},
            {"display_name": "Nidoran"},
        )
        for change in changes:
            with self.subTest(change=change):
                self.sm._fast_screencap = Mock(return_value=frame(replace(self.plain, **change)))
                with self.assertRaisesRegex(RuntimeError, "identity changed"):
                    self.recover()
                self.sm._fast_screencap.assert_called_once()
                self.assert_no_recovery_actions()

    def test_species_letters_digits_gender_and_form_words_are_not_normalized_away(self):
        pairs = (
            ("Nidorano'", "Nidorana"), ("Nidorano'", "Nidoran0"),
            ("Nidoran Male'", "Nidoran Female"), ("Nidoran\u2642'", "Nidoran\u2640"),
            ("Meowth (Alolan)'", "Meowth (Galarian)"),
            ("Farfetch'd'", "Farfetchd"), ("Mr. Mime'", "Mr Mime"),
        )
        for first, second in pairs:
            with self.subTest(first=first, second=second):
                original = replace(self.original, caught_species=first, detected_species=first)
                current = replace(self.original, caught_species=second, detected_species=second)
                self.assertFalse(self.sm._review_name_drift(original, current, frame(original), frame(current)))

    def test_unknown_screen_hidden_bars_changed_pixels_and_reused_image_are_rejected(self):
        dark = frame(self.plain)
        dark.paste("black", (0, 0, dark.width, dark.height))
        resized = Image.new("RGB", (400, 900), "white")
        resized.info.update(frame(self.plain).info)
        for bad in (frame(self.plain, screen="detail"), dark, resized):
            with self.subTest(screen=bad.info["screen"], size=bad.size):
                self.sm._fast_screencap = Mock(return_value=bad)
                with self.assertRaises(RuntimeError):
                    self.recover()
                self.assert_no_recovery_actions()
        self.assertFalse(self.sm._review_name_drift(self.original, self.plain,
                                                   self.initial, self.initial))
        self.sm.reader.are_bars_visible.return_value = False
        self.sm._fast_screencap = Mock(return_value=frame(self.plain))
        with self.assertRaisesRegex(RuntimeError, "lost appraisal"):
            self.recover()
        self.assert_no_recovery_actions()

    def test_later_exact_names_cannot_hide_changed_sex_or_pixels_in_review_mode(self):
        changed = frame(replace(self.original, gender="female"))
        moved = frame(self.original)
        moved.paste("black", (0, 0, moved.width, moved.height))
        for bad in (changed, moved):
            with self.subTest(gender=bad.info["snapshot"].gender):
                self.sm._fast_screencap = Mock(side_effect=[frame(self.plain), bad])
                with self.assertRaisesRegex(RuntimeError, "identity changed"):
                    self.recover()
                self.sm._validate_appraisal_snapshot.assert_not_called()
                self.assert_no_recovery_actions()

    def test_existing_display_only_retry_still_returns_to_exact_cp_confirmation(self):
        self.original = snapshot(display_name="Zacian", detected_species="Zacian", caught_species="Zacian")
        self.initial = frame(self.original)
        exact = frame(self.original)
        self.sm._fast_screencap = Mock(side_effect=[frame(replace(self.original, display_name="Zaclan")), exact])
        expected = SnapshotDecision(True, "exact calculation", snapshot=self.original)
        self.sm._validate_appraisal_snapshot.return_value = expected

        decision, image = self.recover()

        self.assertIs(expected, decision)
        self.assertIs(exact, image)
        self.assertNotIn("pokemgr_review_caught_name_drift", image.info)
        self.sm._validate_appraisal_snapshot.assert_called_once()
        self.assert_no_recovery_actions()

    def test_unobserved_transition_cannot_be_converted_to_review_skip(self):
        self.sm._validate_appraisal_snapshot.return_value = SnapshotDecision(False, "unknown name")
        self.sm._wait_for_stable_appraisal = Mock(return_value=(self.initial, "stable_transition_unobserved"))
        self.sm._fast_screencap = Mock(side_effect=[frame(self.plain) for _ in range(3)])

        decision, _image, status, _reason = self.sm._acquire_validated_snapshot()

        self.assertIsNone(decision)
        self.assertEqual("transition_identity_incomplete", status)
        self.assertEqual((0, 0, 0), (self.sm.count, self.sm.visited_count, self.sm.skipped_count))
        self.assert_no_recovery_actions()

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_latched_review_counts_one_skip_without_a_data_row(self, _paddle):
        self.sm.reader.prepare_native_ocr = Mock()
        self.sm._validate_appraisal_snapshot.return_value = SnapshotDecision(False, "unknown name")
        self.sm._wait_for_stable_appraisal = Mock(return_value=(self.initial, "stable"))
        self.sm._fast_screencap = Mock(side_effect=[
            frame(self.plain), frame(self.original), frame(self.original),
            frame(self.plain), frame(self.original, "on"),
        ])

        self.sm.start(expected_total=1)

        self.assertEqual((0, 1, 1), (self.sm.count, self.sm.visited_count, self.sm.skipped_count))
        self.adb.tap.assert_called_once_with(*self.sm.regions.favorite_star_region.center, jitter=0)
        self.adb.swipe.assert_not_called()
        self.db.insert_pokemon.assert_not_called()
        self.sm._validate_appraisal_snapshot.assert_called_once()
        self.sm.reader.read_cp.assert_not_called()
        self.sm._read_cp_from_powerup_preview.assert_not_called()
        self.assertIsNone(self.sm._last_validated_identity_key)
        self.assertIsNone(self.sm._previous_validated_identity_key)

    def test_recovery_pause_or_abort_discards_read_only_evidence(self):
        for abort in (False, True):
            with self.subTest(abort=abort):
                self.sm._abort = self.sm._paused = False
                self.sm._fast_screencap = Mock(side_effect=[frame(self.plain), frame(self.original)])
                self.delay.side_effect = (lambda *_args: self.sm.abort()) if abort else (
                    lambda *_args: (self.sm.pause(), self.sm.resume()))
                if abort:
                    self.assertIsNone(self.recover()[0])
                else:
                    with self.assertRaises(_AppraisalConfirmationInvalidated):
                        self.recover()
                self.assert_no_recovery_actions()

    def test_favorite_checks_caught_drift_before_and_after_one_tap(self):
        fresh, confirmed = frame(self.plain), frame(replace(self.original, favorited=True), "on")
        self.sm._fast_screencap = Mock(side_effect=[fresh, confirmed])

        self.assertIs(confirmed, self.sm._favorite_unresolved_snapshot(self.initial))

        self.adb.tap.assert_called_once_with(*self.sm.regions.favorite_star_region.center, jitter=0)
        self.adb.swipe.assert_not_called()
        self.db.insert_pokemon.assert_not_called()

    def test_already_starred_caught_drift_needs_no_input(self):
        fresh = frame(self.plain, "on")
        self.sm._fast_screencap = Mock(return_value=fresh)
        self.assertIs(fresh, self.sm._favorite_unresolved_snapshot(self.initial))
        self.assert_no_recovery_actions()

    def test_favorite_latched_mode_rejects_conflict_after_tap_without_retoggling(self):
        for change in ({"gender": "female"}, {"hp": 95}, {"caught_species": "Nidorana", "detected_species": "Nidorana"}):
            with self.subTest(change=change):
                self.adb.tap.reset_mock()
                self.sm._fast_screencap = Mock(side_effect=[frame(self.plain), frame(replace(self.original, **change), "on")])
                with self.assertRaisesRegex(RuntimeError, "identity changed"):
                    self.sm._favorite_unresolved_snapshot(self.initial)
                self.adb.tap.assert_called_once()
                self.db.insert_pokemon.assert_not_called()

    def test_marked_review_waits_for_resume_before_fresh_star_read(self):
        self.initial.info["pokemgr_review_caught_name_drift"] = True
        self.sm.pause()
        fresh = frame(self.original, "on")
        self.sm._fast_screencap = Mock(return_value=fresh)

        def resume(_seconds):
            self.sm._fast_screencap.assert_not_called()
            self.adb.tap.assert_not_called()
            self.sm.resume()

        with patch("pokemgr.indexer.state_machine.time.sleep", side_effect=resume):
            self.assertIs(fresh, self.sm._favorite_unresolved_snapshot(self.initial))
        self.assert_no_recovery_actions()

    def test_pause_during_first_caught_drift_read_discards_old_star_evidence(self):
        fresh, confirmed = frame(self.plain), frame(self.original, "on")
        self.sm._fast_screencap = Mock(side_effect=[fresh, confirmed])
        read = self.sm._read_appraisal_snapshot.side_effect

        def interrupted(image):
            result = read(image)
            if image is fresh:
                self.sm.pause()
                self.sm.resume()
            return result

        self.sm._read_appraisal_snapshot.side_effect = interrupted
        self.assertIs(confirmed, self.sm._favorite_unresolved_snapshot(self.initial))
        self.star.assert_called_once()
        self.assert_no_recovery_actions()

    def test_pause_after_tap_keeps_one_toggle_and_bounded_confirmation(self):
        self.sm._fast_screencap = Mock(side_effect=[frame(self.plain), *[frame(self.original) for _ in range(3)]])
        self.adb.tap.side_effect = lambda *_args, **_kwargs: self.sm.pause()
        with patch("pokemgr.indexer.state_machine.time.sleep", side_effect=lambda _seconds: self.sm.resume()):
            with self.assertRaisesRegex(RuntimeError, "after one tap"):
                self.sm._favorite_unresolved_snapshot(self.initial)
        self.adb.tap.assert_called_once()
        self.assertEqual(4, self.sm._fast_screencap.call_count)
        self.db.insert_pokemon.assert_not_called()

    def test_abort_while_review_paused_sends_no_input(self):
        self.initial.info["pokemgr_review_caught_name_drift"] = True
        self.sm.pause()
        self.sm._fast_screencap = Mock()
        with patch("pokemgr.indexer.state_machine.time.sleep", side_effect=lambda _seconds: self.sm.abort()):
            self.assertIsNone(self.sm._favorite_unresolved_snapshot(self.initial))
        self.sm._fast_screencap.assert_not_called()
        self.assert_no_recovery_actions()


if __name__ == "__main__":
    unittest.main()
