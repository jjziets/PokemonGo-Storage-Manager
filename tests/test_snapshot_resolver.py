import math
import unittest

from pokemgr.pvp.calculator import compute_cp
from pokemgr.pvp.cpm_table import CPM_TABLE
from pokemgr.pvp.resolver import resolve_candidates, resolve_exact


ZORUA_FORMS = {
    "zorua": {
        "id": "zorua",
        "name": "Zorua",
        "base_atk": 153,
        "base_def": 78,
        "base_sta": 120,
    },
    "zorua_hisuian": {
        "id": "zorua_hisuian",
        "name": "Zorua (Hisuian)",
        "base_atk": 162,
        "base_def": 79,
        "base_sta": 111,
    },
}

ZYGARDE_FORMS = {
    "zygarde_10": {
        "id": "zygarde_10",
        "name": "Zygarde (10% Forme)",
        "base_atk": 205,
        "base_def": 173,
        "base_sta": 144,
    },
    "zygarde": {
        "id": "zygarde",
        "name": "Zygarde (50% Forme)",
        "base_atk": 203,
        "base_def": 232,
        "base_sta": 239,
    },
    "zygarde_complete": {
        "id": "zygarde_complete",
        "name": "Zygarde (Complete Forme)",
        "base_atk": 184,
        "base_def": 207,
        "base_sta": 389,
    },
}


def exact_values(species, ivs=(3, 12, 15), level=28.0):
    cpm = CPM_TABLE[level]
    atk, def_, sta = ivs
    hp = max(10, math.floor((species["base_sta"] + sta) * cpm))
    cp = compute_cp(
        species["base_atk"],
        species["base_def"],
        species["base_sta"],
        atk,
        def_,
        sta,
        cpm,
    )
    return hp, cp


class ExactSnapshotResolverTests(unittest.TestCase):
    def test_positive_zorua_resolves_hisuian_level_28_exactly(self):
        result = resolve_exact(
            3,
            12,
            15,
            hp=89,
            cp=882,
            caught_family="Zorua",
            species_map=ZORUA_FORMS,
        )

        self.assertIsNotNone(result)
        self.assertEqual("zorua_hisuian", result.species_id)
        self.assertEqual("Zorua (Hisuian)", result.species)
        self.assertEqual(28.0, result.level)
        self.assertEqual(882, result.expected_cp)
        self.assertEqual(89, result.expected_hp)

    def test_positive_zorua_resolution_is_independent_of_gamemaster_order(self):
        reversed_forms = dict(reversed(tuple(ZORUA_FORMS.items())))

        forward = resolve_exact(
            3,
            12,
            15,
            hp=89,
            cp=882,
            caught_family="Zorua",
            species_map=ZORUA_FORMS,
        )
        reverse = resolve_exact(
            3,
            12,
            15,
            hp=89,
            cp=882,
            caught_family="Zorua",
            species_map=reversed_forms,
        )

        self.assertEqual(forward, reverse)

    def test_hidden_zorua_is_ambiguous_in_its_authoritative_family(self):
        result = resolve_candidates(
            3,
            12,
            15,
            hp=89,
            cp=-1,
            caught_family="Zorua",
            species_map=ZORUA_FORMS,
        )

        self.assertEqual("ambiguous", result.status)
        self.assertIsNone(result.resolved)
        self.assertEqual(
            {("zorua", 24.5, 751),
             ("zorua_hisuian", 28.0, 882),
             ("zorua_hisuian", 28.5, 898)},
            {(item.species_id, item.level, item.expected_cp)
             for item in result.candidates},
        )

    def test_hidden_zygarde_resolves_only_complete_forme(self):
        result = resolve_exact(
            15,
            12,
            11,
            hp=264,
            cp=None,
            caught_family="Zygarde",
            species_map=ZYGARDE_FORMS,
        )

        self.assertIsNotNone(result)
        self.assertEqual("zygarde_complete", result.species_id)
        self.assertEqual("Zygarde (Complete Forme)", result.species)
        self.assertEqual(24.5, result.level)
        self.assertEqual(2575, result.expected_cp)
        self.assertEqual(264, result.expected_hp)

    def test_inexact_positive_cp_is_rejected_not_corrected(self):
        result = resolve_candidates(
            3,
            12,
            15,
            hp=89,
            cp=881,
            caught_family="Zorua",
            species_map=ZORUA_FORMS,
        )

        self.assertEqual("no_match", result.status)
        self.assertIsNone(result.resolved)
        self.assertEqual((), result.candidates)

    def test_no_family_requires_global_uniqueness(self):
        species_map = {
            "alpha": {
                "id": "alpha",
                "name": "Alpha",
                "base_atk": 150,
                "base_def": 100,
                "base_sta": 130,
            },
            "beta": {
                "id": "beta",
                "name": "Beta",
                "base_atk": 150,
                "base_def": 100,
                "base_sta": 130,
            },
        }
        hp, cp = exact_values(species_map["alpha"])

        global_result = resolve_candidates(
            3, 12, 15, hp=hp, cp=cp, species_map=species_map
        )
        family_result = resolve_exact(
            3,
            12,
            15,
            hp=hp,
            cp=cp,
            caught_family="Alpha",
            species_map=species_map,
        )

        self.assertEqual("ambiguous", global_result.status)
        self.assertEqual(2, len(global_result.candidates))
        self.assertIsNotNone(family_result)
        self.assertEqual("alpha", family_result.species_id)

    def test_authoritative_family_matching_is_unicode_and_punctuation_safe(self):
        examples = (
            (
                "Nidoran♀",
                "nidoran_female",
                "Nidoran Female",
            ),
            (
                "Flabébé",
                "flabebe",
                "Flabebe",
            ),
            (
                "Type: Null",
                "type_null",
                "Type (Null)",
            ),
        )
        for caught_family, species_id, species_name in examples:
            with self.subTest(caught_family=caught_family):
                chosen = {
                    "id": species_id,
                    "name": species_name,
                    "base_atk": 150,
                    "base_def": 100,
                    "base_sta": 130,
                }
                other = {
                    "id": "nidoran_male",
                    "name": "Nidoran Male",
                    "base_atk": 150,
                    "base_def": 100,
                    "base_sta": 130,
                }
                hp, cp = exact_values(chosen)

                result = resolve_exact(
                    3,
                    12,
                    15,
                    hp=hp,
                    cp=cp,
                    caught_family=caught_family,
                    species_map={species_id: chosen, "nidoran_male": other},
                )

                self.assertIsNotNone(result)
                self.assertEqual(species_id, result.species_id)

    def test_gender_signs_do_not_collapse_to_the_same_family(self):
        female = {
            "id": "nidoran_female",
            "name": "Nidoran Female",
            "base_atk": 86,
            "base_def": 89,
            "base_sta": 146,
        }
        male = {
            "id": "nidoran_male",
            "name": "Nidoran Male",
            "base_atk": 86,
            "base_def": 89,
            "base_sta": 146,
        }
        hp, cp = exact_values(female)

        result = resolve_exact(
            3,
            12,
            15,
            hp=hp,
            cp=cp,
            caught_family="Nidoran♀",
            species_map={"nidoran_male": male, "nidoran_female": female},
        )

        self.assertIsNotNone(result)
        self.assertEqual("nidoran_female", result.species_id)

    def test_cosmetic_shadow_duplicate_is_one_order_independent_class(self):
        normal = {
            "id": "nidoran_female",
            "name": "Nidoran Female",
            "base_atk": 86,
            "base_def": 89,
            "base_sta": 146,
        }
        shadow = {
            "id": "nidoran_female_shadow",
            "name": "Nidoran Female (Shadow)",
            "base_atk": 86,
            "base_def": 89,
            "base_sta": 146,
        }
        hp, cp = exact_values(normal)

        forward = resolve_exact(
            3,
            12,
            15,
            hp=hp,
            cp=cp,
            caught_family="Nidoran♀",
            species_map={"normal": normal, "shadow": shadow},
        )
        reverse = resolve_exact(
            3,
            12,
            15,
            hp=hp,
            cp=cp,
            caught_family="Nidoran♀",
            species_map={"shadow": shadow, "normal": normal},
        )

        self.assertEqual(forward, reverse)
        self.assertIsNotNone(forward)
        self.assertEqual("nidoran_female", forward.species_id)
        self.assertEqual("Nidoran Female", forward.species)

    def test_invalid_iv_range_never_produces_a_candidate(self):
        for ivs in ((-1, 12, 15), (16, 12, 15), (3, 12.5, 15), (True, 12, 15)):
            with self.subTest(ivs=ivs):
                result = resolve_candidates(
                    *ivs,
                    hp=89,
                    cp=882,
                    caught_family="Zorua",
                    species_map=ZORUA_FORMS,
                )
                self.assertEqual("invalid_input", result.status)
                self.assertIsNone(result.resolved)


if __name__ == "__main__":
    unittest.main()
