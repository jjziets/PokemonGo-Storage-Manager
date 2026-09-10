# TRACEWEAVER: file-role=mutable-nonfavorite-carousel-tests; req=REQ-MASS-001,REQ-SCAN-003,REQ-SCAN-004; trace=TRACE-MASS-001,TRACE-SCAN-003,TRACE-SCAN-004; verifies=VER-SCAN-001
"""Confirmed removals converge without assuming a frozen appraisal carousel."""

from collections import Counter
from dataclasses import dataclass
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from pokemgr.calibration.profile import CalibrationProfile
from pokemgr.calibration.regions import ScreenRegions
from pokemgr.execution.executor import Executor, _ReacquireAction
from pokemgr.indexer.snapshot import SnapshotDecision
from tests.test_mass_action_scanning import snapshot


@dataclass
class Card:
    name: str
    read: object
    keeper: bool = True
    starred: bool = False


class NonfavoriteCarouselTests(unittest.TestCase):
    """Use real executor/tri-state star logic against an in-memory game model."""

    def setUp(self):
        profile = CalibrationProfile(
            "test", "serial", "968x2376", 420,
            ScreenRegions.default_for_resolution(968, 2376, density=420),
        )
        self.adb, self.db = Mock(), Mock()
        self.adb.has_stream_frames = True
        self.executor = Executor(self.adb, profile, self.db)
        self.scanner = self.executor._scanner
        self.executor.reader.prepare_native_ocr = Mock()
        self.executor.reader.appraisal_bars_stable = Mock(return_value=True)
        self.executor._read_identity = Mock(side_effect=lambda image: image.info["read"])
        self.scanner._fast_screencap = Mock(side_effect=self.capture)
        self.scanner._acquire_validated_snapshot = Mock(side_effect=self.acquire)
        self.scanner._recover_failed_transition = Mock(side_effect=AssertionError(
            "A mutable carousel must never replay old backtracking checkpoints"))
        self.scanner._save_failed_appraisal = Mock()
        self.executor._advance_action = Mock(side_effect=self.advance)
        self.nav = self.executor.nav
        self.nav.navigate_to_storage = Mock(return_value=True)
        self.nav.enter_search = Mock(return_value=True)
        self.nav.read_filtered_count_verified = Mock(side_effect=self.count)
        self.nav.read_filtered_count = Mock(side_effect=AssertionError("Unverified count"))
        self.nav.tap_first_pokemon = Mock(side_effect=self.open_first)
        self.nav.open_first_appraisal = Mock(return_value=True)
        self.adb.tap.side_effect = self.tap
        self.query = "!shiny&!shadow&!lucky&!dynamax&!gigantamax&cp0-9999&!favorite"
        self.cards = []
        self.mode = "frozen"
        self.current = None
        self.cursor = 0
        self.carousel = []
        self.sequence = 0
        self.tap_names = []
        self.visits = []
        self.opened = []
        self.counts = []
        self.count_hook = self.acquire_hook = self.tap_hook = self.advance_hook = None
        self.capture_hook = None
        self.enterContext(patch("pokemgr.execution.executor.time.sleep"))
        self.enterContext(patch("pokemgr.execution.executor.favorite_state",
                                side_effect=lambda image, _region: image.info["star"]))

    def add(self, name, *, keeper=True, starred=False, read=None):
        card = Card(name, read or snapshot(cp=500 + len(self.cards), hp=60 + len(self.cards)),
                    keeper, starred)
        self.cards.append(card)
        return card

    def count(self):
        value = sum(not card.starred for card in self.cards)
        if self.count_hook:
            value = self.count_hook(value)
        self.counts.append(value)
        return value

    def open_first(self):
        self.carousel = [card for card in self.cards if not card.starred]
        self.cursor = 0
        self.current = self.carousel[0]
        self.opened.append(tuple(card.name for card in self.carousel))
        return True

    def capture(self):
        self.sequence += 1
        # Pixels deliberately stay static; identity/star observations are exact
        # mocked reader outputs, with a distinct source receipt for each read.
        image = Image.new("RGB", (96, 237), "white")
        image.info.update(
            read=self.current.read, star="on" if self.current.starred else "off",
            pokemgr_capture_started_at=self.sequence,
            pokemgr_capture_finished_at=self.sequence + .01,
            pokemgr_stream_session="simulated-session",
            pokemgr_stream_sequence=self.sequence,
            pokemgr_stream_pts_us=self.sequence * 1000000,
            pokemgr_source_clock_generation=1,
        )
        if self.capture_hook:
            self.capture_hook(image)
        return image

    def acquire(self, **kwargs):
        self.visits.append((self.current.name, kwargs.copy(),
                            self.scanner._previous_validated_identity_key,
                            self.scanner._last_validated_identity_key))
        if self.acquire_hook:
            result = self.acquire_hook()
            if result is not None:
                return result
        return (SnapshotDecision(True, "exact", self.current.read, cp_source="calculated"),
                self.capture(), "ok", "exact")

    def tap(self, *_args, **_kwargs):
        self.current.starred = not self.current.starred
        self.tap_names.append(self.current.name)
        if self.tap_hook:
            self.tap_hook()

    def advance(self, *_args, **_kwargs):
        self.scanner._last_stable_image = self.capture()
        if self.advance_hook:
            result = self.advance_hook()
            if result is not None:
                return result
        if self.mode == "shrinking":
            # Removing the just-starred index and then moving right skips the
            # card that shifted into it. It must return on the next filter lap.
            self.carousel = [card for card in self.cards if not card.starred]
        self.cursor += 1
        if self.cursor >= len(self.carousel):
            return False
        self.current = self.carousel[self.cursor]
        return True

    def run_pass(self, pending=None, *, dry_run=False):
        self.pending = (pending.copy() if pending is not None else Counter(
            self.executor._snapshot_keeper_key(card.read)
            for card in self.cards if card.keeper and not card.starred))
        self.initial = self.pending.copy()
        result = self.executor._run_favorite_pass(
            self.query, self.pending, dry_run, sum(self.pending.values()))
        self.scanner._recover_failed_transition.assert_not_called()
        self.nav.read_filtered_count.assert_not_called()
        self.assertTrue(all(call.args == (self.query,) and call.kwargs == {"verify": True}
                            for call in self.nav.enter_search.call_args_list))
        self.assertEqual([], self.db.mock_calls)
        return result

    def test_frozen_carousel_favorites_once_then_verifies_empty_without_opening(self):
        for name in "abc":
            self.add(name)

        result = self.run_pass()

        self.assertNotIn("error", result)
        self.assertEqual(["a", "b", "c"], self.tap_names)
        self.assertEqual([3, 0], self.counts)
        self.assertEqual([("a", "b", "c")], self.opened)
        self.assertEqual((3, 3, 1), tuple(result[key] for key in ("favorited", "checked", "refreshes")))
        self.assertFalse(self.pending)

    def test_shrinking_carousel_recovers_skipped_neighbors_in_new_laps(self):
        self.mode = "shrinking"
        for name in "abcde":
            self.add(name)

        result = self.run_pass()

        self.assertNotIn("error", result)
        self.assertEqual(["a", "c", "e", "b", "d"], self.tap_names)
        self.assertEqual([5, 2, 1, 0], self.counts)
        self.assertEqual([("a", "b", "c", "d", "e"), ("b", "d"), ("d",)], self.opened)
        self.assertEqual(5, result["favorited"])
        self.assertFalse(self.pending)
        for _name, kwargs, prior, last in self.visits:
            if not kwargs["require_transition"]:
                self.assertEqual((None, None, None), (kwargs["previous_accepted"], prior, last))

    def test_nonkeepers_sharing_cp_remain_off_through_shrinking_laps(self):
        self.mode = "shrinking"
        base = snapshot()
        self.add("prefix", keeper=False, read=snapshot(cp=base.cp, atk=1))
        self.add("a", read=base)
        self.add("middle", keeper=False, read=snapshot(cp=base.cp, atk=2))
        self.add("b", read=snapshot(cp=base.cp, atk=3))
        self.add("tail", keeper=False, read=snapshot(cp=base.cp, atk=4))

        result = self.run_pass()

        self.assertNotIn("error", result)
        self.assertEqual(["a", "b"], self.tap_names)
        self.assertEqual([5, 3], self.counts)
        self.assertTrue(all(not card.starred for card in self.cards if not card.keeper))
        self.assertEqual(1, len(self.opened), "Final count proof must not open a nonkeeper")

    def test_same_stat_copies_keep_separate_change_budget_across_laps(self):
        self.mode = "shrinking"
        read = snapshot()
        for name in "abc":
            self.add(name, read=read)
        sync = self.executor._favorite_sync = Mock(error=None)

        result = self.run_pass()

        self.assertNotIn("error", result)
        self.assertEqual(["a", "c", "b"], self.tap_names)
        self.assertEqual([3, 1, 0], self.counts)
        self.assertEqual(3, sync.observe.call_count)
        self.assertEqual(2, sync.new_traversal.call_count)
        self.assertFalse(self.pending)

    def test_replayed_on_copy_does_not_consume_another_occurrence_or_sync(self):
        read = snapshot()
        self.add("a", read=read)
        self.add("b", read=read)
        sync = self.executor._favorite_sync = Mock(error=None)
        replayed = False

        def replay_once():
            nonlocal replayed
            if not replayed:
                replayed = True
                return True

        self.advance_hook = replay_once

        result = self.run_pass()

        self.assertNotIn("error", result)
        self.assertEqual(["a", "b"], self.tap_names)
        self.assertEqual([2, 1, 0], self.counts)
        self.assertEqual(2, sync.observe.call_count)
        self.assertFalse(self.pending)

    def test_mutation_ceiling_does_not_expand_to_extra_identical_live_copy(self):
        self.mode = "shrinking"
        read = snapshot()
        for name in "abc":
            self.add(name, read=read)

        result = self.run_pass(Counter({self.executor._snapshot_keeper_key(read): 2}))

        self.assertNotIn("error", result)
        self.assertEqual(["a", "c"], self.tap_names)
        self.assertEqual([3, 1], self.counts)
        self.assertFalse(self.cards[1].starred)
        self.assertEqual(1, len(self.opened))

    def test_full_nonmutating_lap_ends_with_missing_keeper_still_unresolved(self):
        self.add("x", keeper=False)
        self.add("y", keeper=False)
        missing = self.executor._snapshot_keeper_key(snapshot(cp=999, hp=99))

        result = self.run_pass(Counter({missing: 1}))

        self.assertNotIn("error", result)
        self.assertEqual((0, 2, 2, 0), tuple(result[k] for k in ("favorited", "checked", "skipped", "refreshes")))
        self.assertEqual(Counter({missing: 1}), self.pending)
        self.assertEqual([], self.tap_names)

    def test_incomplete_nonmutating_lap_holds_without_reopening(self):
        self.add("x", keeper=False)
        self.add("a")
        self.advance_hook = lambda: False

        result = self.run_pass()

        self.assertIn("could not advance", result["error"])
        self.assertEqual([2], self.counts)
        self.assertEqual([], self.tap_names)
        self.assertEqual(self.initial, self.pending)

    def test_navigation_failure_after_confirmed_change_refreshes_without_backtracking(self):
        self.add("a")
        self.add("b")
        self.advance_hook = lambda: False

        result = self.run_pass()

        self.assertNotIn("error", result)
        self.assertEqual(["a", "b"], self.tap_names)
        self.assertEqual([2, 1, 0], self.counts)
        self.assertFalse(self.pending)

    def test_transition_hold_after_confirmed_change_refreshes_without_backtracking(self):
        self.add("a")
        self.add("b")

        def hold_once():
            if len(self.opened) == 1 and self.current.name == "b":
                return None, self.capture(), "transition_returned_to_previous", "held"

        self.acquire_hook = hold_once

        result = self.run_pass()

        self.assertNotIn("error", result)
        self.assertEqual(["a", "b"], self.tap_names)
        self.assertEqual([2, 1, 0], self.counts)

    def test_incorrect_refreshed_count_holds_before_another_tile_input(self):
        for observed in (None, 2, 0):
            with self.subTest(observed=observed):
                self.setUp()
                self.add("a")
                self.add("b")
                self.advance_hook = lambda: False
                self.count_hook = lambda actual: observed if self.counts else actual

                result = self.run_pass()

                self.assertIn("error", result)
                self.assertEqual(["a"], self.tap_names)
                self.assertEqual(1, len(self.opened))
                self.assertEqual(1, result["favorited"])
                self.assertEqual(1, sum(self.pending.values()))

    def test_exhausted_budget_still_requires_final_count_agreement(self):
        self.add("a")
        self.count_hook = lambda actual: 1 if self.counts else actual

        result = self.run_pass()

        self.assertIn("expected 0, saw 1", result["error"])
        self.assertEqual(["a"], self.tap_names)
        self.assertEqual(1, len(self.opened))
        self.assertEqual(1, result["favorited"])
        self.assertFalse(self.pending)

    def test_unknown_post_tap_result_holds_even_after_prior_confirmed_change(self):
        self.add("a")
        self.add("b")

        def unreadable_after_second_tap(image):
            if self.tap_names == ["a", "b"]:
                image.info["star"] = "unknown"

        self.capture_hook = unreadable_after_second_tap

        result = self.run_pass()

        self.assertIn("star change was not confirmed", result["error"])
        self.assertEqual(["a", "b"], self.tap_names)
        self.assertEqual([2], self.counts)
        self.assertEqual(1, result["favorited"])
        self.assertEqual(1, sum(self.pending.values()))

    def test_changed_specimen_after_tap_holds_without_refresh_or_retap(self):
        self.add("a")
        self.add("b")
        self.add("c")

        def move_after_second_tap():
            if len(self.tap_names) == 2:
                self.current = self.cards[2]

        self.tap_hook = move_after_second_tap

        result = self.run_pass()

        self.assertIn("identity changed after star input", result["error"])
        self.assertEqual(["a", "b"], self.tap_names)
        self.assertEqual([3], self.counts)
        self.assertFalse(self.cards[2].starred)

    def test_pause_before_second_star_refreshes_without_repeating_first_star(self):
        self.add("a")
        self.add("b")
        original = self.executor._confirm_star_input
        paused = False

        def pause_once(*args):
            nonlocal paused
            if self.current.name == "b" and not paused:
                paused = True
                self.executor.pause()
                self.executor.resume()
            return original(*args)

        self.executor._confirm_star_input = Mock(side_effect=pause_once)

        result = self.run_pass()

        self.assertNotIn("error", result)
        self.assertEqual(["a", "b"], self.tap_names)
        self.assertEqual([2, 1, 0], self.counts)
        self.assertEqual(2, result["favorited"])

    def test_pause_during_refresh_count_repeats_only_the_filter_proof(self):
        self.add("a")
        self.add("b")
        self.advance_hook = lambda: False

        def pause_once(actual):
            if len(self.counts) == 1:
                self.executor.pause()
                self.executor.resume()
            return actual

        self.count_hook = pause_once

        result = self.run_pass()

        self.assertNotIn("error", result)
        self.assertEqual(["a", "b"], self.tap_names)
        self.assertEqual([2, 1, 1, 0], self.counts)
        self.assertEqual([("a", "b"), ("b",)], self.opened)
        self.assertFalse(self.pending)

    def test_abort_during_refresh_count_prevents_another_tile_or_star(self):
        self.add("a")
        self.add("b")
        self.advance_hook = lambda: False

        def abort_on_refresh(actual):
            if self.counts:
                self.executor.abort()
            return actual

        self.count_hook = abort_on_refresh

        result = self.run_pass()

        self.assertTrue(result["aborted"])
        self.assertEqual(["a"], self.tap_names)
        self.assertEqual([2, 1], self.counts)
        self.assertEqual([("a", "b")], self.opened)
        self.assertEqual(1, result["favorited"])
        self.assertEqual(1, sum(self.pending.values()))

    def test_reacquire_after_star_dispatch_never_requeries_uncertain_membership(self):
        self.add("a")
        self.add("b")
        original = self.scanner._safe_tap

        def dispatch_then_invalidate(*args, **kwargs):
            result = original(*args, **kwargs)
            if self.current.name == "b":
                self.executor.pause()
                self.executor.resume()
                raise _ReacquireAction("Star dispatch lost its observation generation")
            return result

        self.scanner._safe_tap = Mock(side_effect=dispatch_then_invalidate)

        result = self.run_pass()

        self.assertIn("error", result)
        self.assertEqual(["a", "b"], self.tap_names)
        self.assertEqual([2], self.counts, "Unknown delivered input cannot authorize a refreshed list")
        self.assertEqual(1, result["favorited"])
        self.assertEqual(1, sum(self.pending.values()))

    def test_abort_during_second_tap_preserves_only_prior_confirmation(self):
        self.add("a")
        self.add("b")
        self.tap_hook = lambda: self.executor.abort() if len(self.tap_names) == 2 else None

        result = self.run_pass()

        self.assertTrue(result["aborted"])
        self.assertEqual(["a", "b"], self.tap_names)
        self.assertEqual([2], self.counts)
        self.assertEqual(1, result["favorited"])
        self.assertEqual(1, sum(self.pending.values()))

    def test_dry_run_keeps_frozen_membership_and_never_expects_removals(self):
        self.add("a")
        self.add("b")

        result = self.run_pass(dry_run=True)

        self.assertNotIn("error", result)
        self.assertEqual((2, 2, 0), tuple(result[k] for k in ("favorited", "checked", "refreshes")))
        self.assertEqual([], self.tap_names)
        self.assertEqual([2], self.counts)
        self.assertTrue(all(not card.starred for card in self.cards))


if __name__ == "__main__":
    unittest.main()
