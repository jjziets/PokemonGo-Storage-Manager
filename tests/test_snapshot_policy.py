import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from pokemgr.indexer.snapshot import (
    AppraisalSnapshot,
    appraisal_frames_stable,
    appraisal_transition_observed,
    validate_snapshot,
)


def snapshot(**overrides):
    values = {
        "display_name": "Zorua",
        "detected_species": "Zorua",
        "caught_species": "Zorua",
        "cp": 882,
        "hp": 89,
        "atk": 3,
        "def_": 12,
        "sta": 15,
        "shiny": False,
        "shadow": False,
        "favorited": False,
        "lucky": False,
        "gender": "none",
        "weight_tag": "",
        "height_tag": "",
        "is_dynamax": False,
        "detail_confidence": 0.9,
        "appraisal_confidence": 0.95,
    }
    values.update(overrides)
    return AppraisalSnapshot(**values)


class SnapshotAcceptanceTests(unittest.TestCase):
    def test_visible_zorua_cp_is_preserved_and_form_is_resolved(self):
        decision = validate_snapshot(snapshot(), allow_calculated_cp=True)

        self.assertTrue(decision.accepted)
        self.assertEqual("Zorua (Hisuian)", decision.snapshot.detected_species)
        self.assertTrue(decision.exact_form)
        self.assertEqual(882, decision.snapshot.cp)
        self.assertEqual("screen", decision.cp_source)

    def test_visible_equivalent_zigzagoon_forms_store_generic_family(self):
        decision = validate_snapshot(
            snapshot(
                display_name="Zigzagoon",
                detected_species="Zigzagoon",
                caught_species="Zigzagoon",
                cp=272,
                hp=83,
                atk=0,
                def_=15,
                sta=11,
            ),
            allow_calculated_cp=True,
        )

        self.assertTrue(decision.accepted)
        self.assertEqual("Zigzagoon", decision.snapshot.detected_species)
        self.assertEqual(272, decision.snapshot.cp)
        self.assertEqual("screen", decision.cp_source)
        self.assertIn("equivalent form", decision.reason)
        self.assertFalse(decision.exact_form)

    def test_impossible_visible_cp_is_rejected_not_rewritten(self):
        decision = validate_snapshot(
            snapshot(cp=881),
            allow_calculated_cp=True,
        )

        self.assertFalse(decision.accepted)
        self.assertIsNone(decision.snapshot)

    def test_impossible_yungoos_ocr_recovers_only_unique_exact_cp(self):
        decision = validate_snapshot(
            snapshot(
                display_name="Yungoos",
                detected_species="Yungoos",
                caught_species="Yungoos",
                cp=3869,
                hp=82,
                atk=0,
                def_=11,
                sta=15,
            ),
            allow_calculated_cp=True,
        )

        self.assertTrue(decision.accepted)
        self.assertEqual("Yungoos", decision.snapshot.detected_species)
        self.assertEqual(369, decision.snapshot.cp)
        self.assertEqual("calculated_after_invalid_ocr", decision.cp_source)

    def test_hidden_zorua_is_ambiguous(self):
        decision = validate_snapshot(
            snapshot(cp=-1),
            allow_calculated_cp=True,
        )

        self.assertFalse(decision.accepted)
        self.assertIn("ambiguous", decision.reason)

    def test_hidden_zygarde_is_uniquely_calculated(self):
        decision = validate_snapshot(
            snapshot(
                display_name="Zygarde (10% Forme)",
                detected_species="Zygarde (10% Forme)",
                caught_species="Zygarde",
                cp=-1,
                hp=264,
                atk=15,
                def_=12,
                sta=11,
            ),
            allow_calculated_cp=True,
        )

        self.assertTrue(decision.accepted)
        self.assertEqual("Zygarde (Complete Forme)", decision.snapshot.detected_species)
        self.assertEqual(2575, decision.snapshot.cp)
        self.assertEqual("calculated", decision.cp_source)

    def test_all_zero_ivs_are_not_treated_as_a_transition_sentinel(self):
        decision = validate_snapshot(
            snapshot(cp=10, hp=10, atk=0, def_=0, sta=0),
            allow_calculated_cp=True,
        )

        # The tuple may or may not identify a unique species globally, but it is
        # not rejected as an invalid IV range or a stale-frame marker.
        self.assertNotIn("invalid IVs", decision.reason)


class EquivalentFormCpRecoveryTests(unittest.TestCase):
    # Fixed GameMaster entries keep the live-skip regressions offline and use
    # the real resolver. Exeggutor forms have distinct base stats but can still
    # yield exactly the same CP/HP at the supplied IVs and level.
    SPECIES = {
        "exeggutor": {
            "name": "Exeggutor", "base_atk": 233, "base_def": 149, "base_sta": 216,
        },
        "exeggutor_alolan": {
            "name": "Exeggutor (Alolan)", "base_atk": 230, "base_def": 153, "base_sta": 216,
        },
        "tyranitar": {
            "name": "Tyranitar", "base_atk": 251, "base_def": 207, "base_sta": 225,
        },
        "tyranitar_mega": {
            "name": "Tyranitar (Mega)", "base_atk": 309, "base_def": 276, "base_sta": 225,
        },
        "weezing": {
            "name": "Weezing", "base_atk": 174, "base_def": 197, "base_sta": 163,
        },
        "weezing_galarian": {
            "name": "Weezing (Galarian)", "base_atk": 174, "base_def": 197, "base_sta": 163,
        },
    }

    def setUp(self):
        resolver_patch = patch(
            "pokemgr.pvp.resolver._default_species_map", return_value=self.SPECIES,
        )
        resolver_patch.start()
        self.addCleanup(resolver_patch.stop)

    @staticmethod
    def _exeggutor(**overrides):
        values = dict(
            display_name="Exeggutor", detected_species="Exeggutor",
            caught_species="Exeggutor", hp=158, atk=8, def_=9, sta=4, cp=2385,
        )
        values.update(overrides)
        return snapshot(**values)

    def test_live_impossible_exeggutor_cp_recovers_unique_number_without_guessing_form(self):
        for cp in (2385, 2825, 25):
            with self.subTest(cp=cp):
                original = self._exeggutor(cp=cp)

                decision = validate_snapshot(original, allow_calculated_cp=True)

                self.assertTrue(decision.accepted)
                self.assertEqual(2325, decision.snapshot.cp)
                self.assertEqual("Exeggutor", decision.snapshot.detected_species)
                self.assertEqual("Exeggutor", decision.snapshot.caught_species)
                self.assertEqual(original.hp, decision.snapshot.hp)
                self.assertEqual(original.ivs, decision.snapshot.ivs)
                self.assertEqual(29.0, decision.level)
                self.assertEqual("calculated_after_invalid_ocr", decision.cp_source)
                self.assertFalse(decision.exact_form)
                self.assertEqual(cp, original.cp)

    def test_hidden_and_impossible_cp_recover_the_same_equivalent_identity(self):
        hidden = validate_snapshot(self._exeggutor(cp=-1), allow_calculated_cp=True)
        invalid = validate_snapshot(self._exeggutor(), allow_calculated_cp=True)
        self.assertFalse(hidden.exact_form)
        self.assertFalse(invalid.exact_form)

        self.assertTrue(hidden.accepted)
        self.assertTrue(invalid.accepted)
        self.assertEqual(hidden.snapshot, invalid.snapshot)
        self.assertEqual(hidden.level, invalid.level)
        self.assertEqual("calculated", hidden.cp_source)
        self.assertEqual("calculated_after_invalid_ocr", invalid.cp_source)

    def test_equivalent_form_recovery_respects_disabled_calculated_cp(self):
        decision = validate_snapshot(self._exeggutor(), allow_calculated_cp=False)

        self.assertFalse(decision.accepted)
        self.assertIsNone(decision.snapshot)

    def test_equivalent_form_recovery_requires_complete_exact_hp_iv_and_caught_evidence(self):
        for overrides in (
            {"caught_species": ""}, {"hp": -1}, {"hp": 999},
            {"atk": -1}, {"sta": 16}, {"read_complete": False},
        ):
            with self.subTest(overrides=overrides):
                decision = validate_snapshot(
                    self._exeggutor(**overrides), allow_calculated_cp=True,
                )

                self.assertFalse(decision.accepted)
                self.assertIsNone(decision.snapshot)

    def test_other_live_skips_with_multiple_possible_cps_remain_rejected(self):
        for species, hp, ivs, cp in (
            ("Tyranitar", 195, (13, 14, 13), 4098),
            ("Exeggutor", 168, (15, 12, 12), 236),
            ("Weezing", 137, (14, 15, 13), 903),
            ("Weezing", 137, (14, 14, 15), 7210),
        ):
            with self.subTest(species=species, ivs=ivs):
                decision = validate_snapshot(
                    snapshot(
                        display_name=species, detected_species=species,
                        caught_species=species, hp=hp, atk=ivs[0], def_=ivs[1],
                        sta=ivs[2], cp=cp,
                    ),
                    allow_calculated_cp=True,
                )

                self.assertFalse(decision.accepted)
                self.assertIsNone(decision.snapshot)

    def test_exact_visible_cp_is_preserved_even_when_hp_allows_another_cp(self):
        for cp in (2586, 2607):
            with self.subTest(cp=cp):
                decision = validate_snapshot(
                    self._exeggutor(cp=cp, hp=168, atk=15, def_=12, sta=12),
                    allow_calculated_cp=True,
                )

                self.assertTrue(decision.accepted)
                self.assertEqual(cp, decision.snapshot.cp)
                self.assertEqual("Exeggutor", decision.snapshot.detected_species)
                self.assertEqual("screen", decision.cp_source)


class AppraisalStabilityTests(unittest.TestCase):
    def test_static_identity_regions_ignore_unchanged_frame(self):
        image = Image.new("RGB", (100, 200), "white")
        self.assertTrue(appraisal_frames_stable(image, image.copy()))
        self.assertFalse(appraisal_transition_observed(image, image.copy()))

    def test_transition_requires_material_change_in_identity_region(self):
        before = Image.new("RGB", (100, 200), "white")
        after_pixels = np.full((200, 100, 3), 255, dtype=np.uint8)
        after_pixels[142:180, 5:54] = 40
        after = Image.fromarray(after_pixels)

        self.assertTrue(appraisal_transition_observed(before, after))
        self.assertFalse(appraisal_frames_stable(before, after))


if __name__ == "__main__":
    unittest.main()
