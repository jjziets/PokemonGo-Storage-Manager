"""Default-name accent tolerance preserves action identity and pixel guards."""

# TRACEWEAVER: file-role=action-name-identity-tests; req=REQ-MASS-001; trace=TRACE-MASS-001; verifies=VER-SCAN-001

from dataclasses import replace
from itertools import permutations
import unittest

from PIL import Image, ImageDraw

from pokemgr.execution.executor import Executor
from pokemgr.indexer.state_machine import IndexingStateMachine
from tests.test_stable_scan_loop import accepted


class ActionNameIdentityTests(unittest.TestCase):
    def setUp(self):
        # These comparison methods are pure: no controller, OCR or database.
        self.executor = Executor.__new__(Executor)
        self.executor._scanner = IndexingStateMachine.__new__(IndexingStateMachine)
        self.before = replace(
            accepted().snapshot, detected_species="Flabebe", caught_species="Flabébé",
            display_name="Flabebé", cp=595, hp=84, atk=13, def_=14, sta=15,
        )
        self.after = replace(
            self.before, detected_species="Flabébé", display_name="Flabébé", cp=-1,
        )
        self.first = Image.new("RGB", (320, 640), "white")
        self.second = self.first.copy()

    def same(self, before=None, after=None, first=None, second=None):
        return self.executor._same_identity(
            before or self.before, after or self.after,
            first or self.first, second or self.second,
        )

    def test_default_accents_match_across_resolved_and_raw_species(self):
        labels = ("Flabebe", "Flabébé", "Flabebé", "Flabébé", " FLABÉBÉ ")
        for old, new in permutations(labels, 2):
            with self.subTest(old=old, new=new):
                self.assertTrue(self.same(
                    replace(self.before, display_name=old),
                    replace(self.after, display_name=new),
                ))

    def test_comparison_does_not_rewrite_keeper_species_cp_or_snapshots(self):
        before, after = replace(self.before), replace(self.after)
        key = self.executor._snapshot_keeper_key(self.before)

        self.assertTrue(self.same())

        self.assertEqual(before, self.before)
        self.assertEqual(after, self.after)
        self.assertEqual(key, self.executor._snapshot_keeper_key(self.before))
        self.assertEqual(("flabebe", 595, 84, 13, 14, 15), key[:6])

    def test_caught_authority_must_remain_exact(self):
        for changes in (
            {"caught_species": "Flabebe"}, {"caught_species": "Floette"},
            {"caught_species": "Flabébé"},
            {"caught_species": "", "candy_family": "Flabébé"},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(self.same(after=replace(self.after, **changes)))

    def test_candy_family_alone_cannot_establish_a_default_species_label(self):
        before = replace(self.before, caught_species="", candy_family="Flabébé")
        after = replace(self.after, caught_species="", candy_family="Flabébé")
        self.assertFalse(self.same(before, after))

    def test_nicknames_and_other_species_are_not_normalized_to_a_default(self):
        for old, new in (
            ("Fleur", "Fleúr"), ("Flabebe1", "Flabébé1"),
            ("Flabebe 1", "Flabebe 2"), ("Flabebé", "Floette"),
            ("Flabebé", "Flower"), ("Flabebe!", "Flabébé!"),
            ("Flabe-be", "Flabé-bé"), ("Flabebe♀", "Flabébé♂"),
        ):
            with self.subTest(old=old, new=new):
                self.assertFalse(self.same(
                    replace(self.before, display_name=old),
                    replace(self.after, display_name=new),
                ))

    def test_unchanged_nickname_keeps_existing_exact_match(self):
        self.assertTrue(self.same(
            replace(self.before, display_name="Flower 2"),
            replace(self.after, display_name="Flower 2"),
        ))

    def test_form_and_species_suffixes_are_not_removed(self):
        for species in ("Flabebe (Blue)", "Flabebe Shadow", "Floette", "Flabebe♀"):
            with self.subTest(species=species):
                self.assertFalse(self.same(before=replace(self.before, detected_species=species)))
                self.assertFalse(self.same(after=replace(self.after, detected_species=species)))
        self.assertFalse(self.same(
            replace(self.before, display_name="Flabebe (Blue)"),
            replace(self.after, display_name="Flabébé (Blue)"),
        ))

    def test_numeric_flags_and_completeness_still_gate_accent_drift(self):
        for changes in (
            {"hp": 85}, {"hp": -1}, {"atk": 12}, {"def_": 13}, {"sta": 14},
            {"atk": -1}, {"sta": 16}, {"shiny": True}, {"shadow": True},
            {"lucky": True}, {"is_dynamax": True}, {"read_complete": False},
            {"display_name": ""}, {"caught_species": ""},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(self.same(after=replace(self.after, **changes)))
                self.assertFalse(self.same(before=replace(self.before, **changes)))

    def test_each_static_region_still_rejects_motion(self):
        for box in ((.18, .35, .82, .43), (.05, .71, .54, .90), (.02, .84, .98, .985)):
            with self.subTest(box=box):
                image = self.second.copy()
                ImageDraw.Draw(image).rectangle(
                    tuple(int(value * scale) for value, scale in zip(box, (320, 640, 320, 640))),
                    fill="black",
                )
                self.assertFalse(self.same(second=image))

    def test_changed_frame_size_still_rejects_accent_drift(self):
        self.assertFalse(self.same(second=Image.new("RGB", (321, 640), "white")))

    def test_existing_bounded_default_name_ocr_drift_is_unchanged(self):
        before = replace(self.before, detected_species="Pikachu", caught_species="Pikachu", display_name="Pikachu")
        after = replace(before, display_name="Pikacnu", cp=-1)
        self.assertTrue(self.same(before, after))


if __name__ == "__main__":
    unittest.main()
