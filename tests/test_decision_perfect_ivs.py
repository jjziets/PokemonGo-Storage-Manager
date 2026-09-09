"""Every exact perfect-IV specimen is protected, including identical copies."""

import unittest
from dataclasses import replace
from unittest.mock import Mock, call

from pokemgr.data.models import Pokemon
from pokemgr.decision.engine import DecisionEngine
from pokemgr.decision.rules import perfect_ivs


def pokemon(pid, cp=1000, ivs=(15, 15, 15), **flags):
    return Pokemon(id=pid, species="Dragonite", cp=cp, atk=ivs[0], def_=ivs[1],
                   sta=ivs[2], iv_total=sum(ivs), iv_pct=sum(ivs) / 45,
                   shiny=flags.get("shiny", False), shadow=flags.get("shadow", False),
                   lucky=flags.get("lucky", False), favorited=False,
                   is_dynamax=flags.get("is_dynamax", False), position=pid)


class PerfectIvDecisionTests(unittest.TestCase):
    def test_defaults_keep_every_duplicate_perfect_at_different_cp(self):
        db = Mock()
        group = [pokemon(1, 4287), pokemon(2), pokemon(3),
                 pokemon(4, 900, (15, 15, 14))]
        engine = DecisionEngine(db)
        self.assertEqual(engine._decide_for_species("Dragonite", group), (3, 1))
        self.assertEqual(db.update_decision.call_args_list,
                         [call(1, "KEEP", "BEST_OVERALL"),
                          call(2, "KEEP", "KEEP_PERFECT_IV"),
                          call(3, "KEEP", "KEEP_PERFECT_IV"),
                          call(4, "TRANSFER", "")])

    def test_exact_ivs_ignore_rounded_percentage_or_inconsistent_total(self):
        near_perfect = [replace(pokemon(i + 1, ivs=ivs), iv_pct=1.0, iv_total=45)
                        for i, ivs in enumerate(((14, 15, 15), (15, 14, 15),
                                                 (15, 15, 14), (16, 14, 15)))]
        exact = replace(pokemon(5), iv_pct=0.99, iv_total=44)
        self.assertEqual(perfect_ivs([*near_perfect, exact]), [exact])
        self.assertEqual(perfect_ivs([]), [])

    def test_perfect_rule_alone_keeps_every_category_and_preserves_cp_rule(self):
        for enabled in (["KEEP_PERFECT_IV"], ["KEEP_PERFECT_IV", "BEST_CP"]):
            with self.subTest(enabled=enabled):
                db = Mock()
                group = [pokemon(1), pokemon(2, shiny=True), pokemon(3, shadow=True),
                         pokemon(4, lucky=True), pokemon(5, is_dynamax=True),
                         pokemon(6, 4000, (10, 10, 10))]
                engine = DecisionEngine(db, enabled_rules=enabled)
                self.assertEqual(engine._decide_for_species("Dragonite", group),
                                 (6, 0) if "BEST_CP" in enabled else (5, 1))
                for pid in range(1, 6):
                    db.update_decision.assert_any_call(pid, "KEEP", "KEEP_PERFECT_IV")
                if "BEST_CP" in enabled:
                    db.update_decision.assert_any_call(6, "KEEP", "BEST_CP")
                else:
                    db.update_decision.assert_any_call(6, "TRANSFER", "")

    def test_rule_can_be_disabled_without_enabling_other_optional_rules(self):
        for enabled, reason in ((["BEST_OVERALL"], "BEST_OVERALL"),
                                ([], "LAST_OF_SPECIES")):
            with self.subTest(enabled=enabled):
                db = Mock()
                engine = DecisionEngine(db, enabled_rules=enabled)
                self.assertEqual(engine._decide_for_species("Dragonite",
                                 [pokemon(1), pokemon(2)]), (1, 1))
                self.assertEqual(db.update_decision.call_args_list,
                                 [call(1, "KEEP", reason), call(2, "TRANSFER", "")])


if __name__ == "__main__":
    unittest.main()
