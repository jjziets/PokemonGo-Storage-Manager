"""Appraisal stars follow exact IV boundaries, independent of cached rounding."""

# TRACEWEAVER: file-role=appraisal-star-rating-tests; req=REQ-MASS-001; trace=TRACE-MASS-001; verifies=VER-SCAN-001

from dataclasses import replace
import unittest

from pokemgr.data.models import Pokemon


def pokemon(ivs, **changes):
    return replace(Pokemon(
        id=1, species="Pikachu", cp=500, atk=ivs[0], def_=ivs[1], sta=ivs[2],
        iv_total=0, iv_pct=0.0, shiny=False, shadow=False, lucky=False,
        favorited=False, position=1,
    ), **changes)


class ModelStarRatingTests(unittest.TestCase):
    def test_every_valid_iv_tuple_matches_appraisal_boundaries(self):
        expected_by_total = [0] * 23 + [1] * 7 + [2] * 7 + [3] * 8 + [4]
        for atk in range(16):
            for defense in range(16):
                for stamina in range(16):
                    with self.subTest(ivs=(atk, defense, stamina)):
                        row = pokemon((atk, defense, stamina))
                        self.assertEqual(row.star_rating,
                                         expected_by_total[atk + defense + stamina])

    def test_rounded_or_inconsistent_derived_fields_never_change_rating(self):
        for total, expected in ((22, 0), (23, 1), (29, 1), (30, 2),
                                (36, 2), (37, 3), (44, 3), (45, 4)):
            ivs = (min(total, 15), min(max(total - 15, 0), 15), max(total - 30, 0))
            for pct in (None, float("nan"), 0.0, 0.978, 1.0):
                with self.subTest(total=total, iv_pct=pct):
                    self.assertEqual(pokemon(ivs, iv_total=45, iv_pct=pct).star_rating,
                                     expected)

    def test_unknown_or_invalid_iv_is_not_classified_as_a_low_star(self):
        for field in ("atk", "def_", "sta"):
            for value in (-1, 16, None, True, False, 15.0, "15"):
                with self.subTest(field=field, value=value):
                    row = replace(pokemon((15, 15, 15)), **{field: value})
                    self.assertEqual(row.star_rating, -1)


if __name__ == "__main__":
    unittest.main()
