"""Decisions use exact stored forms and candidates eligible for each league."""

import unittest
from unittest.mock import Mock, call, patch

from pokemgr.data.models import Pokemon
from pokemgr.decision.engine import ALL_RULES, DecisionEngine
from pokemgr.decision.rules import best_pvp


SPECIES = {
    "exeggutor": {"name": "Exeggutor"},
    "exeggutor_alolan": {"name": "Exeggutor (Alolan)"},
    "exeggutor_alolan_shadow": {"name": "Exeggutor (Alolan) (Shadow)"},
    "weezing_galarian": {"name": "Weezing (Galarian)"},
    "giratina_altered": {"name": "Giratina (Altered)"},
    "giratina_origin": {"name": "Giratina (Origin)"},
    "zacian_crowned_sword": {"name": "Zacian (Crowned Sword)"},
    "zacian_hero": {"name": "Zacian (Hero)"},
    "nidoran_female": {"name": "Nidoran Female"},
    "nidoran_male": {"name": "Nidoran Male"},
    "flabebe": {"name": "Flabebe"},
}


def pokemon(pid, *, species="Exeggutor (Alolan)", cp=400, atk=10,
            display_name="My nickname"):
    return Pokemon(id=pid, species=species, cp=cp, atk=atk, def_=10, sta=10,
                   iv_total=atk + 20, iv_pct=(atk + 20) / 45,
                   shiny=False, shadow=False, lucky=False, favorited=False,
                   position=pid, display_name=display_name)


def rankings():
    db = Mock()
    db.has_rankings.return_value = True
    db.get_rank.side_effect = lambda sid, league, atk, defense, hp: 20 - atk
    return db


class DecisionIdentityTests(unittest.TestCase):
    def test_full_stored_forms_use_actual_ranking_ids_in_every_league(self):
        for name, species_id in (("Exeggutor (Alolan)", "exeggutor_alolan"),
                                 ("Weezing (Galarian)", "weezing_galarian"),
                                 ("Giratina (Origin)", "giratina_origin"),
                                 ("Zacian (Crowned Sword)", "zacian_crowned_sword")):
            with self.subTest(name=name):
                db, pvp = Mock(), rankings()
                engine = DecisionEngine(db, pvp, ["BEST_PVP_LL", "BEST_PVP_GL", "BEST_PVP_UL"])
                group = [pokemon(1, species=name, atk=5), pokemon(2, species=name, atk=15)]
                with patch("pokemgr.decision.engine._default_species_map", return_value=SPECIES) as catalog:
                    self.assertEqual(engine._decide_for_species(name, group), (1, 1))
                    self.assertEqual(engine._pvp_species_id(name), species_id)
                catalog.assert_called_once()
                self.assertEqual(pvp.has_rankings.call_args_list,
                                 [call(species_id, league) for league in ("little", "great", "ultra")])
                db.update_decision.assert_has_calls([call(2, "KEEP", "BEST_PVP_LL"),
                                                    call(1, "TRANSFER", "")])

    def test_shadow_ids_and_unicode_spelling_preserve_actual_form_and_gender(self):
        engine = DecisionEngine(Mock(), rankings())
        with patch("pokemgr.decision.engine._default_species_map", return_value=SPECIES):
            for name, expected in (("exeggutor_alolan", "exeggutor_alolan"),
                                   ("Exeggutor (Alolan) (Shadow)", "exeggutor_alolan"),
                                   ("exeggutor_alolan_shadow", "exeggutor_alolan"),
                                   ("Nidoran ♀", "nidoran_female"),
                                   ("Nidoran ♂", "nidoran_male"),
                                   ("Flabébé", "flabebe")):
                with self.subTest(name=name):
                    self.assertEqual(engine._pvp_species_id(name), expected)

    def test_unknown_nickname_and_incomplete_form_do_not_guess_rankings(self):
        for species in ("My nickname", "Exeggeutor", "Giratina", "Zacian (Crowned)"):
            with self.subTest(species=species):
                db, pvp = Mock(), rankings()
                engine = DecisionEngine(db, pvp, ["BEST_PVP_GL"])
                group = [pokemon(1, species=species, display_name="Exeggutor (Alolan)"),
                         pokemon(2, species=species, atk=15)]
                with patch("pokemgr.decision.engine._default_species_map", return_value=SPECIES), \
                     self.assertLogs("pokemgr.decision.engine", level="WARNING"):
                    self.assertEqual(engine._decide_for_species(species, group), (1, 1))
                pvp.has_rankings.assert_not_called()
                pvp.get_rank.assert_not_called()
                db.update_decision.assert_has_calls([call(1, "KEEP", "LAST_OF_SPECIES"),
                                                    call(2, "TRANSFER", "")])

    def test_ambiguous_full_name_is_held_but_other_selected_rules_still_apply(self):
        mapping = {"form_a": {"name": "Same (Form)"}, "form_b": {"name": "Same (Form)"}}
        db, pvp = Mock(), rankings()
        engine = DecisionEngine(db, pvp, ["BEST_OVERALL", "BEST_PVP_GL"])
        group = [pokemon(1, species="Same (Form)", atk=5),
                 pokemon(2, species="Same (Form)", atk=15)]
        with patch("pokemgr.decision.engine._default_species_map", return_value=mapping), \
             self.assertLogs("pokemgr.decision.engine", level="WARNING"):
            self.assertEqual(engine._decide_for_species("Same (Form)", group), (1, 1))
        pvp.has_rankings.assert_not_called()
        db.update_decision.assert_has_calls([call(2, "KEEP", "BEST_OVERALL"),
                                            call(1, "TRANSFER", "")])

    def test_none_uses_all_rules_and_empty_uses_only_existing_last_species_safety(self):
        for enabled, expected_rules, expected_keeper, expected_reason in (
                (None, set(ALL_RULES), 2, "BEST_OVERALL"),
                ([], set(), 1, "LAST_OF_SPECIES")):
            with self.subTest(enabled=enabled):
                db = Mock()
                db.get_species_list.return_value = ["Exeggutor (Alolan)"]
                db.get_by_species.return_value = [pokemon(1, atk=5), pokemon(2, atk=15)]
                engine = DecisionEngine(db, enabled_rules=enabled)
                with patch("pokemgr.decision.engine._default_species_map") as catalog:
                    self.assertEqual(engine.run("test-session"), {"keep": 1, "transfer": 1})
                self.assertEqual(engine.enabled_rules, expected_rules)
                catalog.assert_not_called()
                db.clear_decisions.assert_called_once_with("test-session")
                db.update_decision.assert_any_call(expected_keeper, "KEEP", expected_reason)

    def test_no_pvp_rule_does_not_load_catalog_even_with_rankings_db(self):
        for enabled in ([], ["BEST_OVERALL"]):
            with self.subTest(enabled=enabled):
                pvp = rankings()
                engine = DecisionEngine(Mock(), pvp, enabled)
                with patch("pokemgr.decision.engine._default_species_map") as catalog:
                    engine._decide_for_species("Exeggutor (Alolan)", [pokemon(1)])
                catalog.assert_not_called()
                pvp.has_rankings.assert_not_called()

    def test_unavailable_form_catalog_preserves_existing_decisions(self):
        db = Mock()
        db.get_species_list.return_value = ["Exeggutor (Alolan)"]
        engine = DecisionEngine(db, rankings(), ["BEST_PVP_GL"])
        with patch("pokemgr.decision.engine._default_species_map", side_effect=RuntimeError("catalog unavailable")):
            with self.assertRaisesRegex(RuntimeError, "catalog unavailable"):
                engine.run()
        db.clear_decisions.assert_not_called()
        db.update_decision.assert_not_called()

    def test_empty_form_catalog_preserves_existing_decisions(self):
        db = Mock()
        db.get_species_list.return_value = ["Exeggutor (Alolan)"]
        engine = DecisionEngine(db, rankings(), ["BEST_PVP_GL"])
        with patch("pokemgr.decision.engine._default_species_map", return_value={}):
            with self.assertRaisesRegex(ValueError, "catalog is empty"):
                engine.run()
        db.clear_decisions.assert_not_called()
        db.update_decision.assert_not_called()
        self.assertIsNone(engine._pvp_species_index)


class DecisionPvpEligibilityTests(unittest.TestCase):
    def test_better_rank_above_cap_cannot_beat_eligible_boundary_candidate(self):
        for league, cap in (("little", 500), ("great", 1500), ("ultra", 2500)):
            with self.subTest(league=league):
                pvp = rankings()
                over_cap, at_cap = pokemon(1, cp=cap + 1, atk=15), pokemon(2, cp=cap, atk=10)
                self.assertIs(best_pvp([over_cap, at_cap], "exeggutor_alolan", league, pvp), at_cap)
                pvp.get_rank.assert_called_once_with("exeggutor_alolan", league, 10, 10, 10)

    def test_all_over_cap_returns_no_pvp_keeper_and_retains_last_species_safety(self):
        db, pvp = Mock(), rankings()
        engine = DecisionEngine(db, pvp, ["BEST_PVP_GL"])
        with patch("pokemgr.decision.engine._default_species_map", return_value=SPECIES):
            self.assertEqual(engine._decide_for_species("Exeggutor (Alolan)",
                             [pokemon(1, cp=1501), pokemon(2, cp=2500, atk=15)]), (1, 1))
        pvp.get_rank.assert_not_called()
        db.update_decision.assert_any_call(1, "KEEP", "LAST_OF_SPECIES")

    def test_existing_rank_cutoff_and_missing_rankings_still_hold(self):
        pvp = rankings()
        pvp.get_rank.side_effect = None
        pvp.get_rank.return_value = 201
        self.assertIsNone(best_pvp([pokemon(1)], "exeggutor_alolan", "great", pvp))
        pvp.get_rank.return_value = 200
        self.assertEqual(best_pvp([pokemon(1)], "exeggutor_alolan", "great", pvp).id, 1)
        pvp.has_rankings.return_value = False
        self.assertIsNone(best_pvp([pokemon(1)], "exeggutor_alolan", "great", pvp))


if __name__ == "__main__":
    unittest.main()
