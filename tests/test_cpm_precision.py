"""Golden CP boundaries keep multiplier errors out of exact scan validation."""

import math
from decimal import Decimal, localcontext
import unittest

from pokemgr.pvp.calculator import compute_cp
from pokemgr.pvp.cpm_table import CPM_TABLE, LEVELS_ASCENDING
from pokemgr.pvp.resolver import resolve_candidates


TYRANITAR = {
    "tyranitar": {
        "name": "Tyranitar", "base_atk": 251, "base_def": 207, "base_sta": 225,
    },
    "tyranitar_mega": {
        "name": "Tyranitar (Mega)", "base_atk": 309, "base_def": 276, "base_sta": 225,
    },
    "tyranitar_shadow": {
        "name": "Tyranitar (Shadow)", "base_atk": 251, "base_def": 207, "base_sta": 225,
    },
}


class CpmPrecisionTests(unittest.TestCase):
    def test_near_integer_cp_is_floored_without_a_global_rounding_tolerance(self):
        # This is a real Tyranitar stat combination, not float cancellation:
        # an independent 60-digit calculation stays clearly below CP208.
        with localcontext() as context:
            context.prec = 60
            cpm = Decimal(str(CPM_TABLE[2.5]))
            raw = Decimal(256) * Decimal(213).sqrt() * Decimal(225).sqrt() * cpm ** 2 / 10
            self.assertGreater(Decimal(208) - raw, Decimal("0.00007"))
            self.assertLess(Decimal(208) - raw, Decimal("0.00008"))
            self.assertEqual(207, int(raw))

        self.assertEqual(207, compute_cp(251, 207, 225, 5, 6, 0, CPM_TABLE[2.5]))
        self.assertEqual(10, compute_cp(1, 1, 1, 0, 0, 0, CPM_TABLE[1.0]))

    def test_live_tyranitar_has_cp4099_and_hp195_at_level46_half(self):
        # Directly visible in cache/skipped-replay/tyranitar-0-before.png:
        # CP4099, HP195, IV13/14/13. The old arithmetic-midpoint multiplier
        # produced raw4098.9925, incorrectly rejecting a perfectly clear CP.
        cpm = CPM_TABLE[46.5]

        self.assertEqual(4099, compute_cp(251, 207, 225, 13, 14, 13, cpm))
        self.assertEqual(195, math.floor((225 + 13) * cpm))

    def test_live_tyranitar_exact_resolver_accepts4099_without_cp_tolerance(self):
        result = resolve_candidates(
            13, 14, 13, hp=195, cp=4099, caught_family="Tyranitar",
            species_map=TYRANITAR,
        )

        self.assertEqual("resolved", result.status)
        self.assertEqual("tyranitar", result.resolved.species_id)
        self.assertEqual(46.5, result.resolved.level)
        self.assertEqual(4099, result.resolved.expected_cp)
        self.assertEqual(195, result.resolved.expected_hp)

        for wrong_cp in (4098, 4100):
            with self.subTest(cp=wrong_cp):
                rejected = resolve_candidates(
                    13, 14, 13, hp=195, cp=wrong_cp, caught_family="Tyranitar",
                    species_map=TYRANITAR,
                )
                self.assertEqual("no_match", rejected.status)
                self.assertEqual((), rejected.candidates)

    def test_documented_machoke_half_level_boundary_is1501_not1500(self):
        # Independent in-game boundary reported and checked by Pokebattler:
        # https://www.reddit.com/r/TheSilphRoad/comments/jwjbw4/
        # PvPoke uses the same precise L28.5 multiplier. This also guards the
        # stale 20.5–29.5 entries, where errors were larger than display rounding.
        self.assertEqual(
            1501, compute_cp(177, 125, 190, 0, 14, 10, CPM_TABLE[28.5]),
        )

    def test_previously_verified_level50_cp_values_are_preserved(self):
        for base_stats, ivs, expected_cp, expected_hp in (
            ((332, 240, 192), (13, 13, 14), 5561, 173),  # Zacian Crowned Sword
            ((263, 198, 209), (15, 15, 15), 4287, 188),  # Dragonite
        ):
            with self.subTest(base_stats=base_stats):
                self.assertEqual(
                    expected_cp, compute_cp(*base_stats, *ivs, CPM_TABLE[50.0]),
                )
                self.assertEqual(
                    expected_hp, math.floor((base_stats[2] + ivs[2]) * CPM_TABLE[50.0]),
                )

    def test_precise_high_half_levels_and_supported_level_range(self):
        self.assertEqual(list(range(2, 103)), [int(level * 2) for level in LEVELS_ASCENDING])
        # These reference values preserve the original float32 whole-level
        # inputs and quadratic interpolation, including the Best Buddy range.
        for level, expected_cpm in (
            (41.5, 0.797803921486970),
            (46.5, 0.822803778631297),
            (50.5, 0.842803729034748),
        ):
            with self.subTest(level=level):
                self.assertAlmostEqual(expected_cpm, CPM_TABLE[level], places=14)


if __name__ == "__main__":
    unittest.main()
