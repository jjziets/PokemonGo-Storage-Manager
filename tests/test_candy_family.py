"""Candy constrains evolution membership without assigning the active evolution."""

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from pokemgr.calibration.regions import ScreenRegions
from pokemgr.indexer.snapshot import AppraisalSnapshot, validate_snapshot
from pokemgr.pvp.gamemaster import parse_species
from pokemgr.pvp.resolver import (
    _build_candy_index, candy_family_species_ids, resolve_candidates,
)
from pokemgr.reader.candy import parse_candy_observations, read_candy_family
from pokemgr.reader.native_ocr import NativeFrameText, parse_appraisal_fields, TextObservation
from pokemgr.reader.screen import ScreenReader


def species(name, dex, family="", stats=(200, 150, 150)):
    return dict(name=name, dex=dex, family_id=family, base_atk=stats[0], base_def=stats[1], base_sta=stats[2])


SPECIES = {
    "exeggcute": species("Exeggcute", 102, "FAMILY_EXEGGCUTE", (100, 100, 120)),
    "exeggutor": species("Exeggutor", 103, "FAMILY_EXEGGCUTE", (233, 149, 216)),
    "exeggutor_alolan": species("Exeggutor (Alolan)", 103, "FAMILY_EXEGGCUTE", (230, 153, 216)),
    "other": species("Other", 999, "FAMILY_OTHER", (233, 149, 216)),
}


class CandyFamilyResolverTests(unittest.TestCase):
    def test_game_master_preserves_family_and_dex_without_changing_legacy_evolutions(self):
        parsed = parse_species({"pokemon": [{"speciesId": "exeggutor", "speciesName": "Exeggutor",
            "dex": 103, "baseStats": {"atk": 233, "def": 149, "hp": 216},
            "family": {"id": "FAMILY_EXEGGCUTE", "parent": "exeggcute", "evolutions": []}}]})
        self.assertEqual(parsed["exeggutor"]["family_id"], "FAMILY_EXEGGCUTE")
        self.assertEqual(parsed["exeggutor"]["dex"], 103)
        self.assertEqual(parsed["exeggutor"]["evolutions"], [])

    def test_candy_members_include_evolutions_but_caught_species_does_not(self):
        self.assertEqual(candy_family_species_ids("EXEGGCUTE", SPECIES),
                         {"exeggcute", "exeggutor", "exeggutor_alolan"})
        candy = resolve_candidates(15, 15, 15, candy_family="EXEGGCUTE", species_map=SPECIES)
        self.assertEqual({candidate.species_id for candidate in candy.candidates},
                         {"exeggcute", "exeggutor", "exeggutor_alolan"})
        caught = resolve_candidates(15, 15, 15, caught_family="Exeggutor", species_map=SPECIES)
        self.assertEqual({candidate.species_id for candidate in caught.candidates},
                         {"exeggutor", "exeggutor_alolan"})

    def test_same_dex_bridge_keeps_familyless_forms_and_regional_evolutions(self):
        mapping = {
            "larvitar": species("Larvitar", 246, "FAMILY_LARVITAR"),
            "tyranitar": species("Tyranitar", 248, "FAMILY_LARVITAR"),
            "tyranitar_mega": species("Tyranitar (Mega)", 248),
            "wooper": species("Wooper", 194, "FAMILY_WOOPER"),
            "wooper_paldean": species("Wooper (Paldean)", 194, "FAMILY_WOOPER_PALDEAN"),
            "clodsire": species("Clodsire", 980, "FAMILY_WOOPER_PALDEAN"),
            "necrozma": species("Necrozma", 800),
            "necrozma_dusk_mane": species("Necrozma (Dusk Mane)", 800),
        }
        self.assertEqual(candy_family_species_ids("LARVITAR", mapping),
                         {"larvitar", "tyranitar", "tyranitar_mega"})
        self.assertEqual(candy_family_species_ids("WOOPER", mapping),
                         {"wooper", "wooper_paldean", "clodsire"})
        self.assertEqual(candy_family_species_ids("NECROZMA", mapping),
                         {"necrozma", "necrozma_dusk_mane"})

    def test_unknown_candy_does_not_establish_identity_and_conflicting_known_evidence_holds(self):
        self.assertEqual(resolve_candidates(15, 15, 15, candy_family="EXEGGCVTE", species_map=SPECIES).status,
                         "no_match")
        result = resolve_candidates(15, 15, 15, caught_family="Other", candy_family="EXEGGCUTE", species_map=SPECIES)
        self.assertEqual(result.status, "no_match")

    def snapshot(self, **kwargs):
        detail = dict(display_name="My nickname", species="My nickname", caught_species="",
                      candy_family="EXEGGCUTE", cp=-1, hp=192, confidence=.95)
        detail.update(kwargs)
        return AppraisalSnapshot.from_reads(detail, dict(atk=15, def_=15, sta=15, confidence=.95))

    def test_candy_restriction_resolves_actual_evolution_instead_of_nickname_or_candy_root(self):
        # Select an exact Exeggutor tuple that is duplicated by an unrelated
        # species, proving the candy family really narrows the resolver.
        all_matches = resolve_candidates(15, 15, 15, caught_family="Exeggutor", species_map=SPECIES).candidates
        exact = next(candidate for candidate in all_matches if candidate.species_id == "exeggutor" and candidate.level == 30.)
        snapshot = self.snapshot(cp=exact.expected_cp, hp=exact.expected_hp)
        with patch("pokemgr.pvp.resolver._default_species_map", return_value=SPECIES), \
             patch("pokemgr.pvp.resolver._default_candy_index", return_value=_build_candy_index(SPECIES)):
            decision = validate_snapshot(snapshot, True)
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.snapshot.detected_species, "Exeggutor")
        self.assertEqual(decision.snapshot.display_name, "My nickname")
        self.assertEqual(decision.snapshot.as_detail()["candy_family"], "EXEGGCUTE")

    def test_shared_cp_across_distinct_evolutions_cannot_be_accepted(self):
        mapping = {"a": species("First Evolution", 1, "FAMILY_ROOT"),
                   "b": species("Second Evolution", 2, "FAMILY_ROOT")}
        candidates = resolve_candidates(15, 15, 15, hp=120, candy_family="ROOT", species_map=mapping).candidates
        self.assertTrue(candidates)
        for cp in (-1, candidates[0].expected_cp):
            with self.subTest(cp=cp), patch("pokemgr.pvp.resolver._default_species_map", return_value=mapping), \
                 patch("pokemgr.pvp.resolver._default_candy_index", return_value=_build_candy_index(mapping)):
                self.assertFalse(validate_snapshot(self.snapshot(candy_family="ROOT", hp=120, cp=cp), True).accepted)


    def test_candy_only_equivalent_forms_keep_the_actual_species_name(self):
        mapping = {
            "root": species("Root", 1, "FAMILY_ROOT", (100, 100, 100)),
            "active": species("Active", 2, "FAMILY_ROOT", (200, 150, 216)),
            "active_form": species("Active (Form)", 2, "FAMILY_ROOT", (200, 150, 216)),
        }
        with patch("pokemgr.pvp.resolver._default_species_map", return_value=mapping), \
             patch("pokemgr.pvp.resolver._default_candy_index", return_value=_build_candy_index(mapping)):
            decision = validate_snapshot(self.snapshot(candy_family="ROOT", hp=151), True)
        self.assertTrue(decision.accepted)
        self.assertEqual((decision.snapshot.detected_species, decision.snapshot.cp), ("Active", 1797))


class CandyReaderTests(unittest.TestCase):
    def setUp(self):
        self.regions = ScreenRegions.default_for_resolution(968, 2376)
        self.image = Image.new("RGB", (968, 2376), "white")

    def parse(self, *labels):
        return parse_candy_observations(((label, .9, (390, 1393, 240, 20)) for label in labels), 968, 2376)

    def test_whole_normal_and_xl_labels_agree_without_changing_active_species(self):
        self.assertEqual(self.parse("EXEGGCUTE CANDY", "Exeggcute Candy XL"), ("EXEGGCUTE", .9, False))
        raw = NativeFrameText("one", 968, 2376, (
            TextObservation("My nickname", .5, (350, 855, 250, 55)),
            TextObservation("EXEGGCUTE CANDY", .5, (390, 1393, 240, 20)),
            TextObservation("This Exeggutor was caught", .5, (60, 2150, 800, 40)),
        ), 12.)
        fields = parse_appraisal_fields(raw, self.regions)
        self.assertEqual((fields.display_name, fields.caught_species, fields.candy_family),
                         ("My nickname", "Exeggutor", "EXEGGCUTE"))

    def test_incomplete_generic_outside_and_conflicting_labels_do_not_supply_family(self):
        for label in ("EXEGGCUTE", "CANDY", "RARE CANDY", "EXEGGCUTE CANDY extra", "EXEGGCUTE CANDY-X"):
            with self.subTest(label=label):
                self.assertEqual(self.parse(label), ("", 0., False))
        self.assertEqual(self.parse("EXEGGCUTE CANDY", "EEVEE CANDY"), ("", 0., True))
        self.assertEqual(parse_candy_observations([("EXEGGCUTE CANDY", .9, (390, 120, 240, 20))], 968, 2376),
                         ("", 0., False))

    def test_native_detail_adds_candy_without_extra_ocr_and_conflict_blocks_legacy(self):
        profile = SimpleNamespace(regions=self.regions, density=420)
        reader = ScreenReader(profile, read_size_tags=False)
        raw = NativeFrameText("one", 968, 2376, (TextObservation("EXEGGCUTE CANDY", .5, (390, 1393, 240, 20)),), 12.)
        native = parse_appraisal_fields(raw, self.regions)
        with patch.object(reader, "native_fields", return_value=native), \
             patch("pokemgr.reader.candy.read_candy_family") as legacy:
            self.assertEqual(reader.read_candy_family(self.image), ("EXEGGCUTE", .5))
            legacy.assert_not_called()
        with patch.object(reader, "native_fields", return_value=replace(native, candy_family="", candy_conflict=True)), \
             patch("pokemgr.reader.candy.read_candy_family") as legacy:
            self.assertEqual(reader.read_candy_family(self.image), ("", -1.))
            legacy.assert_not_called()

    def test_legacy_pass_requires_complete_confident_lines_and_holds_conflicts(self):
        # Tesseract returns independent physical lines; neither line can borrow
        # a label or confidence from its neighbor.
        data = {
            "text": ["EXEGGCUTE", "CANDY", "EEVEE", "CANDY"],
            "conf": [92, 91, 94, 93], "block_num": [1, 1, 2, 2],
            "par_num": [1, 1, 1, 1], "line_num": [1, 1, 1, 1],
            "left": [380, 520, 680, 770], "top": [210, 210, 210, 210],
            "width": [130, 90, 80, 90], "height": [20, 20, 20, 20],
        }
        with patch("pytesseract.image_to_data", return_value=data) as recognize:
            self.assertEqual(read_candy_family(self.image), ("", -1.))
            recognize.assert_called_once()
        data["conf"][2:] = [49, 49]
        with patch("pytesseract.image_to_data", return_value=data):
            self.assertEqual(read_candy_family(self.image), ("EXEGGCUTE", .91))

    def test_legacy_candy_result_is_bound_to_exact_image_and_copy_needs_new_read(self):
        reader = ScreenReader(SimpleNamespace(regions=self.regions, density=420))
        with patch.object(reader, "native_fields", return_value=None), \
             patch("pokemgr.reader.candy.read_candy_family", side_effect=[("EXEGGCUTE", .9), ("EEVEE", .8)]) as legacy:
            self.assertEqual(reader.read_candy_family(self.image), ("EXEGGCUTE", .9))
            self.assertEqual(reader.read_candy_family(self.image), ("EXEGGCUTE", .9))
            copied = self.image.copy()
            copied.putpixel((400, 1393), (0, 0, 0))
            self.assertEqual(reader.read_candy_family(copied), ("EEVEE", .8))
            self.assertEqual(legacy.call_count, 2)

    def test_actual_split_normal_and_xl_columns_agree(self):
        observations = (
            ("EXEGGCUTE", .8, (430, 1393, 163, 20)),
            ("CANDY", .9, (462, 1425, 98, 20)),
            ("EXEGGCUTE", .7, (725, 1393, 164, 20)),
            ("CANDY XL", .6, (738, 1425, 139, 20)),
        )
        self.assertEqual(parse_candy_observations(observations, 968, 2376), ("EXEGGCUTE", .8, False))
        raw = NativeFrameText("same-frame", 968, 2376,
                              tuple(TextObservation(*item) for item in observations), 12.)
        self.assertEqual(parse_appraisal_fields(raw, self.regions).candy_family, "EXEGGCUTE")
        scaled = [(text, conf, tuple(value*2 for value in box)) for text, conf, box in observations]
        self.assertEqual(parse_candy_observations(scaled, 1936, 4752), ("EXEGGCUTE", .8, False))

    def test_split_labels_require_nearby_centered_complete_lines(self):
        anchor = ("CANDY", .9, (462, 1425, 98, 20))
        for label in (
            ("EXEGGCUTE", .9, (725, 1393, 164, 20)),  # adjacent column
            ("EXEGGCUTE", .9, (430, 1340, 163, 20)),  # far above
            ("EXEGGCUTE", .9, (430, 1447, 163, 20)),  # below anchor
            ("EXEGGCUTE", .9, (430, 1393, 163, 50)),  # wrong font/overlap
            ("EXEGGCUTE", .49, (430, 1393, 163, 20)), # unreadable label
            ("1535", .9, (475, 1393, 70, 20)),       # resource count
            ("RARE", .9, (475, 1393, 70, 20)),       # generic item
        ):
            with self.subTest(label=label):
                self.assertEqual(parse_candy_observations((label, anchor), 968, 2376), ("", 0., False))
        # Two separated name fragments cannot become EXEGGCUTE.
        fragments = (("EXEGG", .9, (390, 1393, 80, 20)),
                     ("CUTE", .9, (550, 1393, 70, 20)), anchor)
        self.assertEqual(parse_candy_observations(fragments, 968, 2376), ("", 0., False))

    def test_split_conflicts_are_held_and_intervening_text_blocks_pair(self):
        anchor = ("CANDY", .9, (462, 1425, 98, 20))
        label = ("EXEGGCUTE", .9, (430, 1393, 163, 20))
        competing = ("EEVEE", .9, (470, 1393, 82, 20))
        self.assertEqual(parse_candy_observations((label, competing, anchor), 968, 2376), ("", 0., True))
        other_column = (("EEVEE", .9, (766, 1393, 82, 20)),
                        ("CANDY XL", .9, (738, 1425, 139, 20)))
        self.assertEqual(parse_candy_observations((label, anchor, *other_column), 968, 2376), ("", 0., True))
        between = ("?", .2, (500, 1415, 20, 5))
        self.assertEqual(parse_candy_observations((label, between, anchor), 968, 2376), ("", 0., False))

    def test_legacy_split_lines_use_the_same_geometry_rules(self):
        data = {
            "text": ["EXEGGCUTE", "CANDY", "XL"], "conf": [92, 95, 94],
            "block_num": [1, 1, 1], "par_num": [1, 1, 1], "line_num": [1, 2, 2],
            "left": [411, 418, 518], "top": [229, 261, 261],
            "width": [163, 90, 30], "height": [20, 20, 20],
        }
        with patch("pytesseract.image_to_data", return_value=data) as recognize:
            self.assertEqual(read_candy_family(self.image), ("EXEGGCUTE", .92))
        recognize.assert_called_once()


if __name__ == "__main__":
    unittest.main()
