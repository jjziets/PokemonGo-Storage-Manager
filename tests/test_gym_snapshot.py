"""Gym motivation must never be accepted as a stored Pokemon's CP."""

# TRACEWEAVER: file-role=gym-snapshot-tests; verifies=VER-SCAN-001; req=REQ-SCAN-001; trace=TRACE-SCAN-001
from dataclasses import fields, replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from pokemgr.calibration.profile import CalibrationProfile
from pokemgr.calibration.regions import ScreenRegions
from pokemgr.indexer.state_machine import IndexingStateMachine

from pokemgr.indexer.snapshot import AppraisalSnapshot, validate_snapshot


def snapshot(**changes):
    detail = {
        "species": "pikachu", "display_name": "Pikachu",
        "caught_species": "Pikachu", "cp": 142, "hp": -1,
        "confidence": 1.0, **changes,
    }
    appraisal = {"atk": 12, "def_": 13, "sta": 14, "confidence": 1.0}
    return AppraisalSnapshot.from_reads(detail, appraisal)


class GymSnapshotTests(unittest.TestCase):
    def test_gym_flag_round_trips_and_survives_recovery_replacement(self):
        original = snapshot(in_gym=True)
        self.assertIs(original.in_gym, True)
        self.assertIs(original.as_detail()["in_gym"], True)
        reread = AppraisalSnapshot.from_reads(original.as_detail(), original.as_appraisal())
        self.assertEqual(original, reread)
        self.assertIs(replace(original, cp=500, hp=100).in_gym, True)

    def test_legacy_and_explicit_non_gym_reads_default_to_false(self):
        self.assertEqual("in_gym", fields(AppraisalSnapshot)[-1].name)
        self.assertIs(fields(AppraisalSnapshot)[-1].default, False)
        for observed in (snapshot(), snapshot(in_gym=False)):
            self.assertIs(observed.in_gym, False)
            self.assertIs(observed.as_detail()["in_gym"], False)

    def test_visible_or_hidden_gym_cp_is_rejected_before_resolving_even_with_hp(self):
        with patch("pokemgr.pvp.resolver.resolve_candidates") as resolve:
            for cp in (142, -1):
                for hp in (-1, 100):
                    for calculated in (False, True):
                        with self.subTest(cp=cp, hp=hp, calculated=calculated):
                            original = snapshot(in_gym=True, cp=cp, hp=hp)
                            decision = validate_snapshot(original, calculated)
                            self.assertFalse(decision.accepted)
                            self.assertEqual(
                                "defending gym hides full HP and storage CP",
                                decision.reason,
                            )
                            self.assertIsNone(decision.snapshot)
                            self.assertIsNone(decision.level)
                            self.assertEqual(cp, original.cp)
                            resolve.assert_not_called()

    def test_gym_reason_precedes_incomplete_or_invalid_read(self):
        with patch("pokemgr.pvp.resolver.resolve_candidates") as resolve:
            original = replace(snapshot(in_gym=True), read_complete=False, atk=-1)
            decision = validate_snapshot(original, True)
            self.assertFalse(decision.accepted)
            self.assertEqual("defending gym hides full HP and storage CP", decision.reason)
            resolve.assert_not_called()

    def test_non_gym_visible_and_hidden_cp_keep_existing_resolution(self):
        candidate = SimpleNamespace(species="pikachu", level=10.0, expected_cp=142)
        result = SimpleNamespace(resolved=candidate, candidates=[candidate])
        for cp, source in ((142, "screen"), (-1, "calculated")):
            with self.subTest(cp=cp):
                with patch("pokemgr.pvp.resolver.resolve_candidates", return_value=result) as resolve:
                    decision = validate_snapshot(snapshot(cp=cp, hp=100), True)
                self.assertTrue(decision.accepted)
                self.assertEqual(142, decision.snapshot.cp)
                self.assertEqual("pikachu", decision.snapshot.detected_species)
                self.assertIs(decision.snapshot.in_gym, False)
                self.assertEqual(source, decision.cp_source)
                resolve.assert_called_once_with(
                    12, 13, 14, hp=100, cp=142 if cp > 0 else None,
                    caught_family="Pikachu",
                )


class GymAcquisitionTests(unittest.TestCase):
    def setUp(self):
        regions = ScreenRegions.default_for_resolution(968, 2376, density=420)
        profile = CalibrationProfile(device_model="test", serial="test",
                                     resolution="968x2376", density=420, regions=regions)
        self.sm = IndexingStateMachine(Mock(), profile, Mock())
        self.addCleanup(self.sm._close_reader)
        self.image = Image.new("RGB", (968, 2376), "white")
        self.sm.reader.read_detail_screen = Mock(side_effect=lambda *_a, **_k: snapshot().as_detail())
        self.sm.reader.read_appraisal_screen = Mock(return_value=snapshot().as_appraisal())
        self.sm.reader.read_hp = Mock(return_value=100)
        self.sm.reader.read_cp = Mock()

    def test_raid_evidence_skips_hp_and_cp_without_another_native_read(self):
        self.sm.reader.native_fields = Mock(return_value=SimpleNamespace(
            caught_species="Pikachu", in_gym=True,
        ))
        with patch("pokemgr.reader.ocr.is_in_gym") as template:
            detail, appraisal = self.sm._read_appraisal_snapshot(self.image)
        observed = AppraisalSnapshot.from_reads(detail, appraisal)

        self.assertIs(observed.in_gym, True)
        self.assertEqual(-1, observed.hp)
        self.assertTrue(observed.read_complete)
        decision = self.sm._validate_appraisal_snapshot(observed, self.image)
        self.assertFalse(decision.accepted)
        self.assertEqual("defending gym hides full HP and storage CP", decision.reason)
        self.sm.reader.native_fields.assert_called_once_with(self.image)
        template.assert_not_called()
        self.sm.reader.read_hp.assert_not_called()
        self.sm.reader.read_cp.assert_not_called()
        self.sm.adb.tap.assert_not_called()

    def test_absent_or_non_boolean_raid_evidence_keeps_template_and_hp_paths(self):
        for native_flag in (False, None, 1, "true"):
            for template_flag in (False, True):
                with self.subTest(native=native_flag, template=template_flag):
                    self.sm.reader.native_fields = Mock(return_value=SimpleNamespace(
                        caught_species="Pikachu", in_gym=native_flag,
                    ))
                    self.sm.reader.read_hp.reset_mock()
                    with patch("pokemgr.reader.ocr.is_in_gym", return_value=template_flag) as template:
                        detail, _appraisal = self.sm._read_appraisal_snapshot(self.image)
                    template.assert_called_once_with(self.image, 968, 2376)
                    self.assertIs(detail["in_gym"], template_flag)
                    self.assertEqual(-1 if template_flag else 100, detail["hp"])
                    self.assertEqual(0 if template_flag else 1, self.sm.reader.read_hp.call_count)


if __name__ == "__main__":
    unittest.main()
