# TRACEWEAVER: file-role=stable-keeper-query-tests; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
"""CP query chunks preserve exact candidate coverage and occurrence ownership."""

from collections import Counter
from itertools import product
import random
import re
import unittest

from pokemgr.execution.keeper_queries import KeeperCPBatch, pending_cp_batches, pending_cp_queries


FLAGS = dict(shiny=False, shadow=False, lucky=False, is_dynamax=False)
BASE = "!shiny&!shadow&!dynamax&!gigantamax&!lucky"


def keeper_key(cp, *, species="Pikachu", hp=60, **changes):
    flags = FLAGS | changes
    return (species, cp, hp, 12, 13, 14,
            *(flags[name] for name in FLAGS))


def cp_values(query, base=BASE):
    prefix = base + "&"
    if not query.startswith(prefix):
        raise AssertionError("The established base query changed")
    values = None
    excluded = set()
    for index, term in enumerate(query[len(prefix):].split("&")):
        match = re.fullmatch(r"(!?)cp(\d+)(?:-(\d+))?", term)
        if match is None:
            raise AssertionError(f"Not an exact numeric CP term: {term}")
        if bool(match[1]) != (index > 0):
            raise AssertionError("Expected one enclosing range followed by excluded gaps")
        low = int(match[2])
        high = int(match[3]) if match[3] is not None else low
        if low > high:
            raise AssertionError("A CP range is reversed")
        covered = set(range(low, high + 1))
        if index == 0:
            values = covered
        else:
            if not covered <= values or covered.intersection(excluded):
                raise AssertionError("Excluded gaps must be distinct and inside the enclosing range")
            excluded.update(covered)
    return values - excluded


class KeeperQueriesTests(unittest.TestCase):
    def assert_coverage(self, queries, expected, *, base=BASE, limit=500):
        seen = set()
        for query in queries:
            self.assertLessEqual(len(query), limit)
            values = cp_values(query, base)
            self.assertFalse(seen.intersection(values), "A CP appears in multiple chunks")
            seen.update(values)
        self.assertEqual(set(expected), seen)

    def test_single_cp_and_empty_matching_set(self):
        self.assertEqual([BASE + "&cp500"], pending_cp_queries(BASE, [keeper_key(500)], FLAGS))
        self.assertEqual([], pending_cp_queries(BASE, [], FLAGS))
        self.assertEqual([], pending_cp_queries(BASE, [keeper_key(500, shiny=True)], FLAGS))

    def test_only_adjacent_integer_values_form_ranges(self):
        values = [11, 12, 10, 14, 99, 100, 101, 104]
        queries = pending_cp_queries(BASE, [keeper_key(cp) for cp in values], FLAGS)

        self.assertEqual([BASE + "&cp10-104&!cp13&!cp15-98&!cp102-103"], queries)
        self.assert_coverage(queries, values)

    def test_query_flag_filter_uses_every_proven_flag(self):
        keys = [keeper_key(500)] + [keeper_key(501 + i, **{name: True})
                                   for i, name in enumerate(FLAGS)]

        self.assertEqual([BASE + "&cp500"], pending_cp_queries(BASE, keys, FLAGS))
        for i, name in enumerate(FLAGS):
            with self.subTest(flag=name):
                flags = FLAGS | {name: True}
                query = pending_cp_queries("established-filter", keys, flags)
                self.assertEqual([f"established-filter&cp{501 + i}"], query)

    def test_numeric_boolean_flags_match_database_values_independent_of_mapping_order(self):
        flags = {name: 0 for name in reversed(FLAGS)}
        self.assertEqual([BASE + "&cp500"], pending_cp_queries(BASE, [keeper_key(500)], flags))

    def test_same_cp_occurrences_are_kept_in_one_chunk_without_consumption(self):
        remaining = Counter({keeper_key(500): 3, keeper_key(500, species="Raichu", hp=70): 2,
                             keeper_key(502): 1})
        before = remaining.copy()
        queries = pending_cp_queries(BASE, remaining, FLAGS)

        self.assertEqual([BASE + "&cp500-502&!cp501"], queries)
        self.assertEqual(before, remaining)
        self.assertEqual(1, sum(500 in cp_values(query) for query in queries))

    def test_chunks_are_deterministic_disjoint_and_bounded(self):
        values = list(range(1, 10001, 3))
        keys = [keeper_key(cp) for cp in values]
        shuffled = list(keys)
        random.Random(42).shuffle(shuffled)
        queries = pending_cp_queries(BASE, keys, FLAGS)

        self.assertGreater(len(queries), 1)
        self.assertEqual(queries, pending_cp_queries(BASE, shuffled + shuffled[:10], FLAGS))
        self.assert_coverage(queries, values)
        self.assertEqual(sorted(min(cp_values(query)) for query in queries),
                         [min(cp_values(query)) for query in queries])

    def test_complete_consecutive_cp_domain_is_one_exact_range(self):
        values = range(1, 10001)
        queries = pending_cp_queries(BASE, (keeper_key(cp) for cp in values), FLAGS)

        self.assertEqual([BASE + "&cp1-10000"], queries)
        self.assert_coverage(queries, values)

    def test_range_can_split_at_chunk_boundary_without_losing_values(self):
        base = "shiny"
        values = [998, 999, 1000, 1001]
        queries = pending_cp_queries(base, [keeper_key(cp, shiny=True) for cp in values],
                                     FLAGS | {"shiny": True}, max_query_length=15)

        self.assert_coverage(queries, values, base=base, limit=15)
        self.assertGreater(len(queries), 1)

    def test_full_query_exactly_at_limit_is_allowed(self):
        base = "a" * 493
        queries = pending_cp_queries(base, [keeper_key(9999)], FLAGS)

        self.assertEqual(500, len(queries[0]))
        self.assert_coverage(queries, [9999], base=base)

    def test_base_query_with_no_room_for_a_cp_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "no room"):
            pending_cp_queries("a" * 494, [keeper_key(10000)], FLAGS)

    def test_malformed_matching_cp_is_rejected_instead_of_dropped(self):
        for cp in (None, True, False, 0, -1, 500.0, "500", "500,cp600"):
            with self.subTest(cp=cp), self.assertRaises(ValueError):
                pending_cp_queries(BASE, [keeper_key(500), keeper_key(cp)], FLAGS)

    def test_positive_cp_above_current_game_values_needs_no_arbitrary_upper_limit(self):
        self.assertEqual([BASE + "&cp10001"],
                         pending_cp_queries(BASE, [keeper_key(10001)], FLAGS))

    def test_batch_keys_keep_exact_chunk_ownership_and_counter_multiplicity(self):
        keys = [keeper_key(cp) for cp in range(100, 201, 2)]
        same_cp_other = keeper_key(100, species="Raichu", hp=70)
        remaining = Counter({key: 2 for key in keys})
        remaining[same_cp_other] = 3
        ignored = keeper_key(150, shiny=True)
        remaining[ignored] = 4
        before = remaining.copy()
        batches = pending_cp_batches(BASE, remaining, FLAGS, max_query_length=65)

        self.assertGreater(len(batches), 1)
        self.assertTrue(all(isinstance(batch, KeeperCPBatch) for batch in batches))
        self.assert_coverage([batch.query for batch in batches], range(100, 201, 2), limit=65)
        seen = set()
        for batch in batches:
            self.assertFalse(seen.intersection(batch.keys))
            seen.update(batch.keys)
            self.assertEqual(cp_values(batch.query), {key[1] for key in batch.keys})
        self.assertEqual(set(keys + [same_cp_other]), seen)
        owning_batch = next(batch for batch in batches if keys[0] in batch.keys)
        self.assertIn(same_cp_other, owning_batch.keys)
        self.assertEqual(5, sum(remaining[key] for key in owning_batch.keys if key[1] == 100))
        self.assertEqual(before, remaining)

        shuffled = list(remaining)
        random.Random(53).shuffle(shuffled)
        self.assertEqual(batches, pending_cp_batches(BASE, shuffled + shuffled, FLAGS,
                                                    max_query_length=65))

    def test_nonmatching_cp_does_not_enter_an_unrelated_flag_query(self):
        queries = pending_cp_queries(BASE, [keeper_key(500), keeper_key(None, shiny=True)], FLAGS)
        self.assertEqual([BASE + "&cp500"], queries)

    def test_invalid_flag_authority_or_signature_is_rejected(self):
        for flags in ({}, FLAGS | {"favorite": False}, FLAGS | {"shiny": "false"},
                      FLAGS | {"shadow": 2}, None):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                pending_cp_queries(BASE, [keeper_key(500)], flags)
        for key in (None, (), keeper_key(500)[:-1], keeper_key(500, lucky="false")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                pending_cp_queries(BASE, [key], FLAGS)

    def test_invalid_base_or_length_budget_is_rejected(self):
        for base in (None, "", "   "):
            with self.subTest(base=base), self.assertRaises(ValueError):
                pending_cp_queries(base, [keeper_key(500)], FLAGS)
        for limit in (None, True, 0, -1, 501, 500.0):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                pending_cp_queries(BASE, [keeper_key(500)], FLAGS, max_query_length=limit)

    def test_builder_adds_no_favorite_or_species_terms(self):
        queries = pending_cp_queries(BASE, [keeper_key(500, species="Renamed Pokémon")], FLAGS)

        self.assertEqual([BASE + "&cp500"], queries)
        self.assertNotIn("favorite", queries[0])
        self.assertNotIn("Renamed", queries[0])

    def test_cp_union_never_introduces_or_that_can_bypass_flag_authority(self):
        cps = [10, 12, 19, 20]
        queries = pending_cp_queries(BASE, [keeper_key(cp) for cp in cps], FLAGS)

        for query in queries:
            self.assertNotIn(",", query)
            self.assertNotIn(";", query)
            terms = query.split("&")
            self.assertEqual(BASE.split("&"), terms[:len(BASE.split("&"))])
            accepted_cps = cp_values(query)
            for shiny, shadow, dynamax, gigantamax, lucky in product((False, True), repeat=5):
                traits = dict(shiny=shiny, shadow=shadow, dynamax=dynamax,
                              gigantamax=gigantamax, lucky=lucky)
                flags_match = all(not traits[term[1:]] for term in BASE.split("&"))
                observed = {cp for cp in range(1, 25) if flags_match and cp in accepted_cps}
                self.assertEqual(set(cps) if not any(traits.values()) else set(), observed)


if __name__ == "__main__":
    unittest.main()
