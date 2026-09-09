"""Highest CP is protected independently of the best IV and PvP specimens."""

import unittest
from unittest.mock import Mock, call, patch

from pokemgr.data.models import Pokemon
from pokemgr.decision.engine import DecisionEngine
from pokemgr.decision.rules import best_cp


def pokemon(pid, cp, ivs, species="Garchomp"):
    return Pokemon(id=pid, species=species, cp=cp, atk=ivs[0], def_=ivs[1],
                   sta=ivs[2], iv_total=sum(ivs), iv_pct=sum(ivs) / 45,
                   shiny=False, shadow=False, lucky=False, favorited=False,
                   position=pid)


class HighestCpTests(unittest.TestCase):
    def test_default_rules_keep_both_actual_garchomp_examples(self):
        db = Mock()
        engine = DecisionEngine(db)
        group = [pokemon(1, 4317, (15, 13, 14)), pokemon(2, 4186, (13, 15, 15))]
        self.assertEqual(engine._decide_for_species("Garchomp", group), (2, 0))
        db.update_decision.assert_has_calls([call(2, "KEEP", "BEST_OVERALL"),
                                            call(1, "KEEP", "BEST_CP")])

    def test_cp_iv_and_pvp_winners_are_all_kept(self):
        db, pvp = Mock(), Mock()
        pvp.has_rankings.return_value = True
        pvp.get_rank.side_effect = lambda sid, league, atk, defense, hp: 5 if atk == 0 else None
        engine = DecisionEngine(db, pvp, ["BEST_OVERALL", "BEST_CP", "BEST_PVP_GL"])
        group = [pokemon(1, 4317, (15, 13, 14)), pokemon(2, 4186, (15, 15, 15)),
                 pokemon(3, 1490, (0, 14, 15)), pokemon(4, 1400, (5, 5, 5))]
        with patch("pokemgr.decision.engine._default_species_map",
                   return_value={"garchomp": {"name": "Garchomp"}}):
            self.assertEqual(engine._decide_for_species("Garchomp", group), (3, 1))
        db.update_decision.assert_has_calls([call(2, "KEEP", "BEST_OVERALL"),
                                            call(1, "KEEP", "BEST_CP"),
                                            call(3, "KEEP", "BEST_PVP_GL"),
                                            call(4, "TRANSFER", "")])

    def test_unchecked_highest_cp_rule_keeps_existing_iv_policy(self):
        db = Mock()
        engine = DecisionEngine(db, enabled_rules=["BEST_OVERALL"])
        group = [pokemon(1, 4317, (15, 13, 14)), pokemon(2, 4186, (13, 15, 15))]
        self.assertEqual(engine._decide_for_species("Garchomp", group), (1, 1))
        db.update_decision.assert_has_calls([call(2, "KEEP", "BEST_OVERALL"),
                                            call(1, "TRANSFER", "")])

    def test_cp_ties_prefer_higher_iv_then_one_stable_occurrence(self):
        low_iv = pokemon(1, 3000, (10, 10, 10))
        high_iv = pokemon(2, 3000, (15, 15, 15))
        duplicate = pokemon(3, 3000, (15, 15, 15))
        self.assertIs(best_cp([low_iv, high_iv, duplicate]), high_iv)
        self.assertIsNone(best_cp([]))

    def test_one_specimen_can_win_both_rules_without_double_counting(self):
        db = Mock()
        engine = DecisionEngine(db, enabled_rules=["BEST_OVERALL", "BEST_CP"])
        group = [pokemon(1, 4317, (15, 15, 15)), pokemon(2, 4186, (13, 15, 15))]
        self.assertEqual(engine._decide_for_species("Garchomp", group), (1, 1))
        self.assertEqual(db.update_decision.call_args_list,
                         [call(1, "KEEP", "BEST_OVERALL"), call(2, "TRANSFER", "")])

    def test_highest_cp_is_selected_separately_for_each_stored_form(self):
        db = Mock()
        groups = {
            "Exeggutor": [pokemon(1, 3000, (10, 10, 10), "Exeggutor"),
                          pokemon(2, 2900, (15, 15, 15), "Exeggutor")],
            "Exeggutor (Alolan)": [pokemon(3, 2800, (10, 10, 10), "Exeggutor (Alolan)"),
                                    pokemon(4, 2700, (15, 15, 15), "Exeggutor (Alolan)")],
        }
        db.get_species_list.return_value = list(groups)
        db.get_by_species.side_effect = lambda species, session: groups[species]
        engine = DecisionEngine(db, enabled_rules=["BEST_CP"])
        self.assertEqual(engine.run("test-session"), {"keep": 2, "transfer": 2})
        self.assertEqual(db.update_decision.call_args_list,
                         [call(1, "KEEP", "BEST_CP"), call(2, "TRANSFER", ""),
                          call(3, "KEEP", "BEST_CP"), call(4, "TRANSFER", "")])


if __name__ == "__main__":
    unittest.main()
