"""Cleanup authority comes from complete groups with unanimous reviewed decisions."""

from collections import Counter
from dataclasses import replace
import unittest

from pokemgr.data.models import Pokemon
from pokemgr.execution.pvp_cleanup import (
    PvpCleanupPlan, cleanup_group_key, plan_pvp_cleanup,
)

# TRACEWEAVER: file-role=reviewed-pvp-cleanup-plan-tests; req=REQ-MASS-001,REQ-DATA-001; verifies=VER-SCAN-001


def pokemon(pid=1, **changes):
    row = Pokemon(
        id=pid, species="Swampert", cp=1400 + pid, hp=120,
        atk=0, def_=15, sta=15, iv_total=30, iv_pct=30 / 45,
        shiny=False, shadow=False, lucky=False, favorited=True,
        position=pid, scan_session_id="scan-a", decision="TRANSFER",
    )
    return replace(row, **changes)


def key_for(row):
    # Same full signature used by the live keeper action, without native reader
    # imports or any database/device construction in these planner tests.
    if (not row.species.strip() or row.cp <= 0 or row.hp <= 0
            or any(value < 0 or value > 15
                   for value in (row.atk, row.def_, row.sta))):
        return None
    return (row.species.strip().casefold(), row.cp, row.hp,
            row.atk, row.def_, row.sta,
            row.shiny, row.shadow, row.lucky, row.is_dynamax)


class PvpCleanupPlanTests(unittest.TestCase):
    def test_empty_plan(self):
        plan = plan_pvp_cleanup([], key_for)
        self.assertIsInstance(plan, PvpCleanupPlan)
        self.assertEqual(plan.candidates, ())
        self.assertEqual(plan.remaining, Counter())
        self.assertEqual((plan.total_favorited, plan.eligible,
                          plan.protected, plan.ambiguous), (0, 0, 0, 0))

    def test_counts_partition_favorites_and_candidates_are_detached(self):
        eligible = pokemon(1)
        rows = [eligible, pokemon(2, decision="KEEP"),
                pokemon(3, atk=7), pokemon(4, cp=-1, hp=119),
                pokemon(5, favorited=False), pokemon(6, decision=None)]
        plan = plan_pvp_cleanup(iter(rows), key_for)
        self.assertEqual((plan.total_favorited, plan.eligible,
                          plan.protected, plan.ambiguous), (5, 1, 3, 1))
        self.assertEqual(plan.candidates, (eligible,))
        self.assertIsNot(plan.candidates[0], eligible)
        self.assertEqual(plan.remaining, Counter({key_for(eligible): 1}))
        eligible.decision = "KEEP"
        eligible.favorited = False
        eligible.cp = 900
        self.assertEqual(plan.candidates[0].decision, "TRANSFER")
        self.assertTrue(plan.candidates[0].favorited)
        self.assertEqual(plan.candidates[0].cp, 1401)
        self.assertEqual(rows[1].decision, "KEEP")

    def test_all_valid_iv_combinations_use_actual_sum_not_cached_summaries(self):
        for atk in range(16):
            for defense in range(16):
                for stamina in range(16):
                    with self.subTest(ivs=(atk, defense, stamina)):
                        row = pokemon(atk=atk, def_=defense, sta=stamina,
                                      iv_total=45, iv_pct=1.0)
                        plan = plan_pvp_cleanup([row], key_for)
                        eligible = int(atk + defense + stamina <= 36)
                        self.assertEqual(plan.eligible, eligible)
                        self.assertEqual(plan.protected, 1 - eligible)
                        self.assertEqual(plan.ambiguous, 0)

    def test_only_explicit_transfer_and_affirmative_star_qualify(self):
        for decision in ("KEEP", None, "", "transfer", "UNKNOWN", " TRANSFER "):
            with self.subTest(decision=decision):
                plan = plan_pvp_cleanup([pokemon(decision=decision)], key_for)
                self.assertEqual((plan.eligible, plan.protected), (0, 1))
        for favorited in (False, None, 1, "true"):
            with self.subTest(favorited=favorited):
                plan = plan_pvp_cleanup([pokemon(favorited=favorited)], key_for)
                self.assertEqual((plan.eligible, plan.total_favorited), (0, 0))

    def test_malformed_or_incomplete_identity_is_held(self):
        cases = {
            "id": [0, -1, None, True, 1.0, "1", []],
            "position": [-1, None, True, False, 1.0, "1", []],
            "species": [None, "", "  "],
            "cp": [0, -1, None, True, 1401.0, "1401"],
            "hp": [0, -1, None, True, 120.0, "120"],
            "atk": [-1, 16, None, True, 0.0, "0"],
            "def_": [-1, 16, None, True, 15.0, "15"],
            "sta": [-1, 16, None, True, 15.0, "15"],
            "scan_session_id": [None, "", "  ", 1],
            "shiny": [None, 0, "false"],
            "shadow": [None, 0, "false"],
            "lucky": [None, 0, "false"],
            "is_dynamax": [None, 0, "false"],
        }
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    row = replace(pokemon(), **{field: value})
                    for selected in (None, [1]):
                        plan = plan_pvp_cleanup([row], key_for, selected_ids=selected)
                        self.assertEqual(plan.eligible, 0)
                        self.assertEqual(plan.total_favorited, 1)

    def test_uniform_same_session_duplicate_group_is_reviewable_in_full(self):
        row = pokemon()
        for changes in ({}, {"display_name": "Personal favorite"},
                        {"gender": "female", "weight_tag": "HEAVIEST"}):
            with self.subTest(changes=changes):
                companion = replace(row, id=2, position=2, **changes)
                for selection in (None, [1, 2]):
                    plan = plan_pvp_cleanup([row, companion], key_for,
                                            selected_ids=selection)
                    self.assertEqual(plan.candidates, (row, companion))
                    self.assertEqual(plan.remaining, Counter({key_for(row): 2}))
                    self.assertEqual((plan.protected, plan.ambiguous), (0, 0))
                    self.assertIsNot(plan.candidates[1], companion)

    def test_duplicate_group_is_held_for_protected_unstarred_or_cross_session_companion(self):
        row = pokemon()
        companions = [
            replace(row, id=2, position=2, decision="KEEP"),
            replace(row, id=2, position=2, favorited=False),
            replace(row, id=2, favorited=None),
            replace(row, id=2, scan_session_id="scan-b"),
            replace(row, id=2, scan_session_id=""),
            replace(row, id=None),
        ]
        for companion in companions:
            with self.subTest(companion=companion):
                plan = plan_pvp_cleanup([row, companion], key_for)
                self.assertEqual(plan.eligible, 0)
                self.assertGreaterEqual(plan.ambiguous, 1)
                self.assertEqual(plan.remaining, Counter())

    def test_duplicate_ids_in_different_signatures_are_held(self):
        row = pokemon()
        plan = plan_pvp_cleanup([row, replace(row, cp=1402)], key_for)
        self.assertEqual((plan.eligible, plan.ambiguous), (0, 2))
        plan = plan_pvp_cleanup([row, replace(row, cp=1402, favorited=False)], key_for)
        self.assertEqual((plan.eligible, plan.ambiguous), (0, 1))

    def test_repeated_same_session_position_cannot_authorize_duplicate_group(self):
        first = pokemon()
        second = replace(first, id=2)
        plan = plan_pvp_cleanup([first, second], key_for)
        self.assertEqual((plan.eligible, plan.ambiguous), (0, 2))
        plan = plan_pvp_cleanup([first, replace(second, species="Another form")], key_for)
        self.assertEqual((plan.eligible, plan.ambiguous), (0, 2))

    def test_zero_based_scan_positions_are_valid_for_complete_groups(self):
        first = pokemon(position=0)
        second = replace(first, id=2, position=1)
        self.assertEqual(plan_pvp_cleanup([first], key_for).candidates, (first,))
        self.assertEqual(plan_pvp_cleanup([first, second], key_for).candidates, (first, second))

    def test_position_reuse_does_not_merge_distinct_numeric_groups(self):
        first = pokemon()
        second = replace(first, id=2, hp=121)
        plan = plan_pvp_cleanup([first, second], key_for)
        self.assertEqual(plan.candidates, (first, second))

    def test_compatible_partial_companion_holds_even_when_unstarred_or_deselected(self):
        first = pokemon()
        for field, missing in (("hp", -1), ("cp", None), ("atk", -1),
                               ("species", ""), ("shiny", "unknown")):
            for favorite in (True, False):
                with self.subTest(field=field, favorited=favorite):
                    companion = replace(first, id=2, decision="KEEP", favorited=favorite,
                                        **{field: missing})
                    plan = plan_pvp_cleanup([first, companion], key_for, selected_ids=[1])
                    self.assertEqual((plan.eligible, plan.ambiguous), (0, 1))
                    self.assertEqual(plan.remaining, Counter())

    def test_known_disagreement_can_distinguish_a_partial_companion(self):
        first = pokemon()
        for changes in ({"cp": 1999}, {"atk": 1}, {"shiny": True}):
            with self.subTest(changes=changes):
                companion = replace(first, id=2, hp=-1, decision="KEEP", **changes)
                plan = plan_pvp_cleanup([first, companion], key_for)
                self.assertEqual(plan.candidates, (first,))

    def test_partial_different_species_companion_still_holds_numeric_group(self):
        first = pokemon()
        for changes in ({"hp": -1}, {"shiny": "unknown"}, {"id": None}):
            with self.subTest(changes=changes):
                companion = replace(first, **dict(
                    {"id": 2, "species": "Pikachu", "decision": "KEEP", "favorited": False},
                    **changes))
                plan = plan_pvp_cleanup([first, companion], key_for)
                self.assertEqual((plan.eligible, plan.ambiguous), (0, 1))

    def test_all_transfer_forms_and_species_share_group_but_keep_exact_budgets(self):
        first = pokemon(species="Oricorio (Baile)")
        second = replace(first, id=2, position=2, species="Oricorio (Pom-Pom)")
        third = replace(first, id=3, position=3, species="Another species")
        plan = plan_pvp_cleanup([first, second, third], key_for, selected_ids=[1, 2, 3])
        self.assertEqual(plan.candidates, (first, second, third))
        self.assertEqual(plan.remaining,
                         Counter({key_for(first): 1, key_for(second): 1, key_for(third): 1}))
        self.assertEqual(cleanup_group_key(key_for(first)), cleanup_group_key(key_for(second)))

    def test_protected_or_unselected_form_holds_other_same_stat_forms(self):
        first = pokemon(species="Oricorio (Baile)")
        for companion, selected in (
            (replace(first, id=2, species="Oricorio (Sensu)", decision="KEEP"), None),
            (replace(first, id=2, species="Oricorio (Sensu)"), [1]),
            (replace(first, id=2, species="Another species", favorited=False), None),
            (replace(first, id=2, species="Oricorio (Sensu)", scan_session_id="scan-b"), None),
        ):
            with self.subTest(companion=companion, selected=selected):
                plan = plan_pvp_cleanup([first, companion], key_for, selected_ids=selected)
                self.assertEqual(plan.eligible, 0)
                self.assertGreaterEqual(plan.ambiguous, 1)
                self.assertEqual(plan.total_favorited, plan.protected + plan.ambiguous)

    def test_three_and_four_star_groups_stay_protected_even_if_all_reviewed(self):
        for atk in (7, 15):
            with self.subTest(atk=atk):
                first = pokemon(atk=atk)
                second = replace(first, id=2, species="Other form")
                plan = plan_pvp_cleanup([first, second], key_for, selected_ids=[1, 2])
                self.assertEqual((plan.eligible, plan.protected, plan.ambiguous), (0, 2, 0))

    def test_selection_only_narrows_and_deselected_companions_still_block(self):
        first, second = pokemon(1), pokemon(2)
        plan = plan_pvp_cleanup([first, second], key_for, selected_ids=[2, 2, 999])
        self.assertEqual(plan.candidates, (second,))
        self.assertEqual((plan.eligible, plan.protected), (1, 1))
        plan = plan_pvp_cleanup([first, second], key_for, selected_ids=[])
        self.assertEqual((plan.eligible, plan.protected), (0, 2))
        companion = replace(first, id=3)
        plan = plan_pvp_cleanup([first, companion], key_for, selected_ids=[1])
        self.assertEqual((plan.eligible, plan.protected, plan.ambiguous), (0, 1, 1))

    def test_malformed_selection_cannot_select_an_id_via_numeric_coercion(self):
        for selected in ([True], [1.0], ["1"], [0], [-1], [None]):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                plan_pvp_cleanup([pokemon()], key_for, selected_ids=selected)

    def test_invalid_keys_hold_and_distinct_exact_signatures_remain_separate(self):
        for key in (None, [], (), ("short",), ("x",) + (None,) * 9):
            with self.subTest(key=key):
                plan = plan_pvp_cleanup([pokemon()], lambda row: key)
                self.assertEqual((plan.eligible, plan.ambiguous), (0, 1))
        row = pokemon()
        rows = [row, replace(row, id=2, hp=121),
                replace(row, id=3, shiny=True), replace(row, id=4, shadow=True),
                replace(row, id=5, lucky=True), replace(row, id=6, is_dynamax=True),
                replace(row, id=7, species="Swampert (Mega)"),
                replace(row, id=8, def_=14)]
        rows = [replace(item, position=item.id) for item in rows]
        plan = plan_pvp_cleanup(rows, key_for)
        self.assertEqual(plan.eligible, len(rows))
        self.assertEqual(len(plan.remaining), len(rows))
        self.assertTrue(all(count == 1 for count in plan.remaining.values()))

    def test_cleanup_group_key_refuses_malformed_native_types(self):
        key = key_for(pokemon())
        self.assertEqual(cleanup_group_key(key), key[1:])
        for index, values in ((1, (True, 0, 1401.0)), (2, (False, -1, "120")),
                              (3, (False, 16, "0")), (6, (0, 1, "false")),
                              (7, (0, 1, None)), (8, (0, 1, None)),
                              (9, (0, 1, None))):
            for value in values:
                with self.subTest(index=index, value=value):
                    modified = key[:index] + (value,) + key[index + 1:]
                    self.assertIsNone(cleanup_group_key(modified))

    def test_rank_or_reason_fields_do_not_expand_or_replace_stored_decisions(self):
        keep = pokemon(1, decision="KEEP", decision_reason="BEST_PVP_GL",
                       pvp_rank_gl=4000, pvp_rank_ul=4000)
        transfer = pokemon(2, pvp_rank_gl=1, pvp_rank_ul=1)
        plan = plan_pvp_cleanup([keep, transfer], key_for)
        self.assertEqual(plan.candidates, (transfer,))
        self.assertEqual(plan.protected, 1)


if __name__ == "__main__":
    unittest.main()
