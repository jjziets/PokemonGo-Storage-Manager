# TRACEWEAVER: file-role=scan-recovery-verification; req=REQ-SCAN-003; trace=TRACE-SCAN-005; ver=VER-SCAN-001
# TRACEWEAVER: verifies=VER-SCAN-001; req=REQ-SCAN-003; trace=TRACE-SCAN-005
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication

from pokemgr import config
from pokemgr.calibration.profile import CalibrationProfile
from pokemgr.calibration.regions import ScreenRegions
from pokemgr.gui.widgets.scan_control import ScanControl
from pokemgr.indexer.snapshot import AppraisalSnapshot, SnapshotDecision
from pokemgr.indexer.state_machine import IndexingStateMachine


def _profile():
    return CalibrationProfile(
        device_model="test",
        serial="serial",
        resolution="968x2376",
        density=420,
        regions=ScreenRegions.default_for_resolution(968, 2376, density=420),
    )


def _decision(species="Zweilous", cp=783, hp=98, ivs=(15, 14, 14)):
    snapshot = AppraisalSnapshot(
        display_name=species,
        detected_species=species,
        caught_species=species.split(" (", 1)[0],
        cp=cp,
        hp=hp,
        atk=ivs[0],
        def_=ivs[1],
        sta=ivs[2],
        shiny=False,
        shadow=False,
        favorited=False,
        lucky=False,
        gender="none",
        weight_tag="",
        height_tag="",
        is_dynamax=False,
        detail_confidence=1.0,
        appraisal_confidence=1.0,
    )
    return SnapshotDecision(True, "exact", snapshot, level=20.0)


class _ADB:
    def __init__(self):
        self.actions = []

    def screencap(self):
        return Image.new("RGB", (968, 2376), "white")

    def get_device_info(self):
        return SimpleNamespace(fingerprint="test-device")

    def get_battery_level(self):
        return 100

    def tap(self, *args, **kwargs):
        self.actions.append(("tap", args, kwargs))

    def swipe(self, *args, **kwargs):
        self.actions.append(("swipe", args, kwargs))


class _DB:
    def __init__(self):
        self.rows = []
        self.sessions = []

    def create_session(self, session_id, fingerprint):
        self.sessions.append((session_id, fingerprint))

    def complete_session(self, session_id, total):
        self.completed = (session_id, total)

    def insert_pokemon(self, pokemon, session_id, position):
        self.rows.append((pokemon, session_id, position))
        return len(self.rows)


class ExactCpDefaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_exact_cp_recovery_is_the_no_settings_file_default(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "pokemgr.config.DATA_DIR", Path(temp_dir)
        ):
            widget = ScanControl()

        self.assertTrue(config.USE_CALCULATED_CP)
        self.assertTrue(config.calculated_cp_recovery_enabled())
        self.assertTrue(ScanControl.SPEED_DEFAULTS["calc_cp"])
        self.assertTrue(widget.calc_cp_check.isChecked())
        self.assertIn("exact", widget.calc_cp_check.toolTip().casefold())
        self.assertIn("visible cp", widget.calc_cp_check.toolTip().casefold())
        widget.deleteLater()


class StructuredTransitionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.adb = _ADB()
        self.sm = IndexingStateMachine(self.adb, _profile(), _DB())
        self.frame = self.adb.screencap()
        self.sm._last_validated_identity_key = _decision().snapshot.identity_key
        self.sm._wait_for_stable_appraisal = Mock(
            return_value=(self.frame, "stable_raw_identity_unchanged")
        )
        self.sm._read_appraisal_snapshot = Mock(return_value=({}, {}))

    def _acquire_with(self, decision):
        with patch(
            "pokemgr.indexer.snapshot.validate_snapshot",
            return_value=decision,
        ):
            return self.sm._acquire_validated_snapshot(
                previous_accepted=self.frame,
                require_transition=True,
            )

    def test_different_complete_tuple_overrides_inconclusive_raw_identity(self):
        current = _decision(cp=775, hp=98, ivs=(15, 11, 14))

        decision, frame, failure_kind, _reason = self._acquire_with(current)

        self.assertIs(decision, current)
        self.assertIs(frame, self.frame)
        self.assertEqual("ok", failure_kind)
        self.assertEqual([], self.adb.actions)

    def test_equal_complete_tuple_remains_a_returned_transition(self):
        current = _decision()

        decision, frame, failure_kind, reason = self._acquire_with(current)

        self.assertIsNone(decision)
        self.assertIs(frame, self.frame)
        self.assertEqual("transition_returned_to_previous", failure_kind)
        self.assertIn("same validated", reason)
        self.assertEqual([], self.adb.actions)

    def test_incomplete_tuple_cannot_override_inconclusive_raw_identity(self):
        current = _decision(cp=775, hp=-1, ivs=(15, 11, 14))

        decision, frame, failure_kind, reason = self._acquire_with(current)

        self.assertIsNone(decision)
        self.assertIs(frame, self.frame)
        self.assertEqual("transition_identity_incomplete", failure_kind)
        self.assertIn("complete", reason)
        self.assertEqual([], self.adb.actions)

    def test_missing_prior_validated_tuple_cannot_override_raw_identity(self):
        self.sm._last_validated_identity_key = None
        current = _decision(cp=775, hp=98, ivs=(15, 11, 14))

        decision, frame, failure_kind, reason = self._acquire_with(current)

        self.assertIsNone(decision)
        self.assertIs(frame, self.frame)
        self.assertEqual("transition_identity_incomplete", failure_kind)
        self.assertIn("prior", reason)
        self.assertEqual([], self.adb.actions)

    @patch("pokemgr.indexer.state_machine.human_delay", return_value=None)
    def test_acquisition_wires_motion_settling_to_different_tuple(self, _delay):
        sm = IndexingStateMachine(self.adb, _profile(), _DB())
        previous = _decision()
        current = _decision(cp=775, hp=98, ivs=(15, 11, 14))
        pokemon_a = Image.new("RGB", (968, 2376), "white")
        transient = Image.new("RGB", (968, 2376), "black")
        sm._last_validated_identity_key = previous.snapshot.identity_key
        sm._fast_screencap = Mock(
            side_effect=[transient, pokemon_a, pokemon_a]
        )
        sm.reader.are_bars_visible = Mock(return_value=True)
        sm.reader.appraisal_bars_stable = Mock(return_value=True)
        sm.nav.detect_screen = Mock(return_value="appraisal")
        sm._read_appraisal_snapshot = Mock(return_value=({}, {}))

        with patch(
            "pokemgr.indexer.snapshot.validate_snapshot",
            return_value=current,
        ):
            decision, frame, failure_kind, _reason = (
                sm._acquire_validated_snapshot(
                    previous_accepted=pokemon_a,
                    require_transition=True,
                )
            )

        self.assertIs(decision, current)
        self.assertEqual(pokemon_a.tobytes(), frame.tobytes())
        self.assertEqual("ok", failure_kind)
        self.assertEqual([], self.adb.actions)

    @patch("pokemgr.indexer.state_machine.human_delay", return_value=None)
    def test_fallback_status_requires_motion_and_two_stable_frames(self, _delay):
        sm = IndexingStateMachine(self.adb, _profile(), _DB())
        pokemon_a = Image.new("RGB", (968, 2376), "white")
        transient = Image.new("RGB", (968, 2376), "black")
        sm._fast_screencap = Mock(
            side_effect=[transient, pokemon_a, pokemon_a]
        )
        sm.reader.are_bars_visible = Mock(return_value=True)
        sm.reader.appraisal_bars_stable = Mock(return_value=True)
        sm.nav.detect_screen = Mock(return_value="appraisal")

        frame, status = sm._wait_for_stable_appraisal(
            previous_accepted=pokemon_a,
            require_transition=True,
            allow_structured_fallback=True,
        )

        self.assertEqual(pokemon_a.tobytes(), frame.tobytes())
        self.assertEqual("stable_raw_identity_unchanged", status)

    @patch("pokemgr.indexer.state_machine.human_delay", return_value=None)
    def test_stable_frames_offer_structured_fallback_when_motion_was_missed(
            self, _delay):
        sm = IndexingStateMachine(self.adb, _profile(), _DB())
        pokemon_a = Image.new("RGB", (968, 2376), "white")
        sm._fast_screencap = Mock(side_effect=[pokemon_a] * 7)
        sm.reader.are_bars_visible = Mock(return_value=True)
        sm.reader.appraisal_bars_stable = Mock(return_value=True)
        sm.nav.detect_screen = Mock(return_value="appraisal")

        frame, status = sm._wait_for_stable_appraisal(
            previous_accepted=pokemon_a,
            require_transition=True,
            allow_structured_fallback=True,
        )

        self.assertIsNotNone(frame)
        self.assertEqual("stable_transition_unobserved", status)

    @patch("pokemgr.indexer.state_machine.human_delay", return_value=None)
    def test_strict_transition_still_rejects_without_witnessed_motion(
            self, _delay):
        sm = IndexingStateMachine(self.adb, _profile(), _DB())
        pokemon_a = Image.new("RGB", (968, 2376), "white")
        sm._fast_screencap = Mock(side_effect=[pokemon_a] * 7)
        sm.reader.are_bars_visible = Mock(return_value=True)
        sm.reader.appraisal_bars_stable = Mock(return_value=True)
        sm.nav.detect_screen = Mock(return_value="appraisal")

        frame, status = sm._wait_for_stable_appraisal(
            previous_accepted=pokemon_a,
            require_transition=True,
            allow_structured_fallback=False,
        )

        self.assertIsNone(frame)
        self.assertEqual("transition_not_observed", status)

    @patch("pokemgr.indexer.state_machine.human_delay", return_value=None)
    def test_missed_visual_motion_accepts_two_matching_exact_new_tuples(
            self, _delay):
        sm = IndexingStateMachine(self.adb, _profile(), _DB())
        previous = _decision(species="Spearow", cp=579, hp=90,
                             ivs=(14, 14, 13))
        current = _decision(species="Spearow", cp=507, hp=85,
                            ivs=(13, 13, 14))
        first = Image.new("RGB", (968, 2376), "white")
        confirmation = first.copy()
        sm._last_validated_identity_key = previous.snapshot.identity_key
        sm._wait_for_stable_appraisal = Mock(
            return_value=(first, "stable_transition_unobserved")
        )
        sm._fast_screencap = Mock(return_value=confirmation)
        sm.reader.are_bars_visible = Mock(return_value=True)
        sm.reader.appraisal_bars_stable = Mock(return_value=True)
        sm.nav.detect_screen = Mock(return_value="appraisal")
        sm._read_appraisal_snapshot = Mock(side_effect=[({}, {}), ({}, {})])

        with patch(
            "pokemgr.indexer.snapshot.validate_snapshot",
            side_effect=[current, current],
        ):
            decision, frame, failure_kind, _reason = (
                sm._acquire_validated_snapshot(
                    previous_accepted=first,
                    require_transition=True,
                )
            )

        self.assertIs(decision, current)
        self.assertIs(frame, confirmation)
        self.assertEqual("ok", failure_kind)
        self.assertEqual(2, sm._read_appraisal_snapshot.call_count)
        self.assertEqual([], self.adb.actions)

    @patch("pokemgr.indexer.state_machine.human_delay", return_value=None)
    def test_missed_visual_motion_rejects_inconsistent_exact_tuples(
            self, _delay):
        sm = IndexingStateMachine(self.adb, _profile(), _DB())
        previous = _decision(species="Spearow", cp=579, hp=90,
                             ivs=(14, 14, 13))
        first_read = _decision(species="Spearow", cp=507, hp=85,
                               ivs=(13, 13, 14))
        second_read = _decision(species="Spearow", cp=506, hp=85,
                                ivs=(13, 13, 14))
        frame = Image.new("RGB", (968, 2376), "white")
        sm._last_validated_identity_key = previous.snapshot.identity_key
        sm._wait_for_stable_appraisal = Mock(
            return_value=(frame, "stable_transition_unobserved")
        )
        sm._fast_screencap = Mock(return_value=frame.copy())
        sm.reader.are_bars_visible = Mock(return_value=True)
        sm.reader.appraisal_bars_stable = Mock(return_value=True)
        sm.nav.detect_screen = Mock(return_value="appraisal")
        sm._read_appraisal_snapshot = Mock(side_effect=[({}, {}), ({}, {})])

        with patch(
            "pokemgr.indexer.snapshot.validate_snapshot",
            side_effect=[first_read, second_read],
        ):
            decision, _frame, failure_kind, reason = (
                sm._acquire_validated_snapshot(
                    previous_accepted=frame,
                    require_transition=True,
                )
            )

        self.assertIsNone(decision)
        self.assertEqual("transition_identity_inconsistent", failure_kind)
        self.assertIn("did not agree", reason)
        self.assertEqual([], self.adb.actions)

    @patch("pokemgr.indexer.state_machine.human_delay", return_value=None)
    def test_fallback_is_not_offered_until_two_frames_settle(self, _delay):
        sm = IndexingStateMachine(self.adb, _profile(), _DB())
        pokemon_a = Image.new("RGB", (968, 2376), "white")
        black = Image.new("RGB", (968, 2376), "black")
        red = Image.new("RGB", (968, 2376), "red")
        sm._fast_screencap = Mock(
            side_effect=[black, red, black, red, black, red, black]
        )
        sm.reader.are_bars_visible = Mock(return_value=True)
        sm.reader.appraisal_bars_stable = Mock(return_value=True)
        sm.nav.detect_screen = Mock(return_value="appraisal")

        frame, status = sm._wait_for_stable_appraisal(
            previous_accepted=pokemon_a,
            require_transition=True,
            allow_structured_fallback=True,
        )

        self.assertIsNone(frame)
        self.assertEqual("appraisal_not_stable", status)

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_scan_loop_records_accepted_key_and_clears_it_after_skip(self, _paddle):
        db = _DB()
        sm = IndexingStateMachine(self.adb, _profile(), db)
        sm._favorite_unresolved_snapshot = Mock(side_effect=lambda frame: frame)
        first = _decision()
        sm._acquire_validated_snapshot = Mock(side_effect=[
            (first, self.frame, "ok", "exact"),
            (None, self.frame, "invalid", "ambiguous evidence"),
        ])
        key_at_advance = []

        def _advance():
            key_at_advance.append(sm._last_validated_identity_key)
            return True

        sm._advance_from_confirmed_appraisal = Mock(side_effect=_advance)

        sm.start(expected_total=2)

        self.assertEqual([first.snapshot.identity_key], key_at_advance)
        self.assertIsNone(sm._last_validated_identity_key)
        self.assertEqual(1, len(db.rows))
        self.assertEqual([], self.adb.actions)

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_accepted_incomplete_identity_cannot_authorize_next_fallback(self, _paddle):
        db = _DB()
        sm = IndexingStateMachine(self.adb, _profile(), db)
        incomplete = _decision(hp=-1)
        sm._acquire_validated_snapshot = Mock(
            return_value=(incomplete, self.frame, "ok", "visible CP")
        )

        sm.start(expected_total=1)

        self.assertIsNone(sm._last_validated_identity_key)

        sm._acquire_validated_snapshot = (
            IndexingStateMachine._acquire_validated_snapshot.__get__(sm)
        )
        sm._wait_for_stable_appraisal = Mock(
            return_value=(self.frame, "stable_raw_identity_unchanged")
        )
        sm._read_appraisal_snapshot = Mock(return_value=({}, {}))
        next_pokemon = _decision(cp=775, hp=98, ivs=(15, 11, 14))
        with patch(
            "pokemgr.indexer.snapshot.validate_snapshot",
            return_value=next_pokemon,
        ):
            decision, _frame, failure_kind, reason = (
                sm._acquire_validated_snapshot(
                    previous_accepted=self.frame,
                    require_transition=True,
                )
            )

        self.assertIsNone(decision)
        self.assertEqual("transition_identity_incomplete", failure_kind)
        self.assertIn("prior", reason)
        self.assertEqual([], self.adb.actions)


if __name__ == "__main__":
    unittest.main()
