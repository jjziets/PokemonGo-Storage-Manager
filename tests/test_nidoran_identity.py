"""Preserve Nidoran sex when the caught-text glyph is incomplete or conflicts."""

# TRACEWEAVER: file-role=caught-nidoran-tests; verifies=VER-SCAN-001; req=REQ-IDENTITY-001; trace=TRACE-IDENTITY-001
# TRACEWEAVER: file-role=caught-nidoran-cp-tests; verifies=VER-SCAN-001; req=REQ-SCAN-001; trace=TRACE-SCAN-001
# TRACEWEAVER: file-role=caught-nidoran-review-tests; verifies=VER-SCAN-001; req=REQ-SCAN-004; trace=TRACE-SCAN-004
import unittest

from pokemgr.reader.nidoran import is_nidoran_read, resolve_caught_nidoran


class NidoranIdentityTests(unittest.TestCase):
    def test_literal_sex_symbols_and_words_keep_distinct_canonical_species(self):
        for sex, names in (
            ("male", ("Nidoran♂", "Nidoran ♂", "Nidoran Male", " nidoran  MALE ")),
            ("female", ("Nidoran♀", "Nidoran ♀", "Nidoran Female", " NIDORAN  female ")),
        ):
            for name in names:
                for observed in (sex, "", None, False):
                    with self.subTest(name=name, observed=observed):
                        self.assertTrue(is_nidoran_read(name))
                        self.assertEqual(f"Nidoran {sex.title()}", resolve_caught_nidoran(name, observed))

    def test_bounded_incomplete_forms_use_strict_same_frame_gender(self):
        for name in ("Nidoran", "Nidoran'", "Nidoran’", "Nidorano", "Nidorano'", "Nidorano’", " NIDORANO’ ", "Nidoran o"):
            for sex in ("male", "female"):
                with self.subTest(name=name, sex=sex):
                    self.assertTrue(is_nidoran_read(name))
                    self.assertEqual(f"Nidoran {sex.title()}", resolve_caught_nidoran(name, sex))

    def test_single_ascii_digit_artifacts_never_determine_sex(self):
        for digit in "0123456789":
            for separator in ("", " "):
                for quote in ("", "'", "’"):
                    name=f"Nidoran{separator}{digit}{quote}"
                    for observed in ("male", "female", "", None, False, "Female", "♀"):
                        with self.subTest(name=name, observed=observed):
                            expected=(f"Nidoran {observed.title()}"
                                      if observed in ("male", "female") else None)
                            self.assertEqual(expected, resolve_caught_nidoran(name, observed))

    def test_missing_or_nonstrict_gender_does_not_fill_the_lost_sex(self):
        for name in ("Nidoran", "Nidoran'", "Nidoran’", "Nidorano", "Nidorano'", "Nidorano’"):
            for observed in ("", None, False, True, 0, "none", "Male", "FEMALE", "male ", "♂", "female?"):
                with self.subTest(name=name, observed=observed):
                    self.assertIsNone(resolve_caught_nidoran(name, observed))

    def test_explicit_contradiction_is_held_in_both_directions(self):
        for name, opposite in (("Nidoran♂", "female"), ("Nidoran Male", "female"),
                               ("Nidoran♀", "male"), ("Nidoran Female", "male")):
            with self.subTest(name=name, observed=opposite):
                self.assertIsNone(resolve_caught_nidoran(name, opposite))

    def test_mixed_or_unknown_suffixes_are_recognized_but_never_repaired(self):
        for name in ("Nidoran♂♀", "Nidoran Female Male", "Nidoran Male ♀",
                     "NidoranFemale", "Nidoran 44", "Nidoranc", "Nidorano''",
                     "Nidoran4''", "Nidoran４", "Nidoran٤", "Nidoran 4♂", "Nidoran4 Female",
                     "Nidoran''", "Nidoran’♂", "Nidoran (Shadow)", "Nidoranwhatever"):
            for sex in ("male", "female", ""):
                with self.subTest(name=name, sex=sex):
                    self.assertTrue(is_nidoran_read(name))
                    self.assertIsNone(resolve_caught_nidoran(name, sex))

    def test_other_species_and_nicknames_are_never_fuzzy_matched(self):
        for name in ("Nidorina", "Nidorino", "Nidoking", "Nidoqueen", " Nidoking ",
                     "My Nidoran", "Buddy", "Pikachu", ""):
            for sex in ("male", "female", ""):
                with self.subTest(name=name, sex=sex):
                    self.assertFalse(is_nidoran_read(name))
                    self.assertEqual(name, resolve_caught_nidoran(name, sex))

    def test_nontext_caught_evidence_is_unavailable(self):
        for name in (None, False, 29):
            with self.subTest(name=name):
                self.assertFalse(is_nidoran_read(name))
                self.assertIsNone(resolve_caught_nidoran(name, "female"))


if __name__ == "__main__":
    unittest.main()
