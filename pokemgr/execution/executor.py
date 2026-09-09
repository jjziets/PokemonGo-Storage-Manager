"""Verified appraisal actions using the scanner's stream and native reader.

Category actions need stable identity and a confirmed star state, not CP.
Keeper actions additionally require an exact validated database signature.
Searches never depend on favorite state, so changing a star leaves carousel
membership unchanged. Occurrences are counted; identical stats are not an
end-of-list signal or a reason to merge database rows.
"""

from collections import Counter, defaultdict
from dataclasses import replace
import json
import logging
import re
import time
from typing import Callable

from .. import timing
from ..adb.controller import ADBController
from ..calibration.profile import CalibrationProfile
from ..data.database import PokemonDatabase
from ..data.models import Pokemon
from ..indexer.snapshot import AppraisalSnapshot, appraisal_frames_stable, validate_snapshot
from ..indexer.state_machine import IndexingStateMachine
from ..reader.icons import favorite_state
from ..config import LOGS_DIR

log = logging.getLogger(__name__)


class _ReacquireAction(Exception):
    """No star input was sent; discard a paused read and try again."""


class Executor:
    """Set stars only on independently confirmed storage positions."""

    ALL_FAV_PASSES = [
        ("Normal", "!shiny&!shadow&!dynamax&!gigantamax"),
        ("Shiny", "shiny"), ("Shadow", "shadow"),
        ("Dynamax", "dynamax"), ("Gigantamax", "gigantamax"),
    ]

    def __init__(self, adb: ADBController, profile: CalibrationProfile,
                 db: PokemonDatabase):
        self.adb, self.profile, self.db = adb, profile, db
        self._scanner = IndexingStateMachine(adb, profile, db)
        self.reader, self.nav = self._scanner.reader, self._scanner.nav
        self.regions = profile.regions
        self._reader_threads = self._scanner._reader_threads
        self.on_progress: Callable | None = None
        self.on_error: Callable | None = None
        self._abort = self._paused = False
        self._current_flags = None
        self.nav.is_cancelled = lambda: self._abort

    def _active(self):
        while self._paused and not self._abort:
            time.sleep(0.05)
        return not self._abort

    @staticmethod
    def _keeper_key(pokemon):
        """Nicknames are display text, never a keeper lookup key."""
        if (not pokemon.species.strip() or pokemon.cp <= 0 or pokemon.hp <= 0
                or any(value < 0 or value > 15 for value in (pokemon.atk, pokemon.def_, pokemon.sta))):
            return None
        return (pokemon.species.strip().casefold(), pokemon.cp, pokemon.hp,
                pokemon.atk, pokemon.def_, pokemon.sta,
                pokemon.shiny, pokemon.shadow, pokemon.lucky, pokemon.is_dynamax)

    @classmethod
    def _snapshot_keeper_key(cls, snapshot):
        from types import SimpleNamespace
        return cls._keeper_key(SimpleNamespace(
            species=snapshot.detected_species, cp=snapshot.cp, hp=snapshot.hp,
            atk=snapshot.atk, def_=snapshot.def_, sta=snapshot.sta,
            shiny=snapshot.shiny, shadow=snapshot.shadow, lucky=snapshot.lucky,
            is_dynamax=snapshot.is_dynamax,
        ))

    @classmethod
    def _selected_queries(cls, selected):
        if selected is not None and not set(selected) <= {name for name, _ in cls.ALL_FAV_PASSES}:
            raise ValueError("Unknown favorite pass")
        excluded = []
        for name, query in cls.ALL_FAV_PASSES:
            if selected is not None and name not in selected:
                continue
            if name != "Normal":
                query = "&".join([query, *excluded])
                excluded.append("!" + dict(cls.ALL_FAV_PASSES)[name])
            yield name, query

    @classmethod
    def _keeper_queries(cls, selected, required):
        """Make every placeholder flag authoritative through the game filter.

        ScreenReader defers shiny/shadow/Dynamax detection to pass filters.
        Split only the combinations required by actual keeper rows. The two
        Dynamax variants are disjoint even if the game's dynamax term also
        includes Gigantamax; the existing selected-pass exclusions still apply.
        """
        groups = sorted({key[6:] for key in required})
        for name, base in cls._selected_queries(selected):
            for shiny, shadow, lucky, dynamax in groups:
                flags = dict(shiny=shiny, shadow=shadow, lucky=lucky, is_dynamax=dynamax)
                common = [("" if value else "!") + term for term, value in
                          (("shiny", shiny), ("shadow", shadow), ("lucky", lucky))]
                variants = (("dynamax", "!gigantamax"), ("gigantamax",)) if dynamax else (("!dynamax", "!gigantamax"),)
                for variant in variants:
                    terms = list(dict.fromkeys([*base.split("&"), *common, *variant]))
                    if any("!" + term in terms for term in terms if not term.startswith("!")):
                        continue
                    yield name, "&".join(terms), flags

    def favorite_keepers(self, dry_run=False, selected_passes=None):
        try:
            return self._favorite_keepers(dry_run, selected_passes)
        finally:
            self._close_reader()

    def _favorite_keepers(self, dry_run, selected_passes):
        all_pokemon = self.db.get_all()
        need_fav = [p for p in all_pokemon if p.decision == "KEEP" and not p.favorited]
        if not need_fav:
            return {"favorited": 0, "checked": 0}
        if self._abort:
            return {"favorited": 0, "checked": 0, "dry_run": dry_run, "aborted": True}
        # Validate [] separately from None before inspecting the inventory.
        list(self._selected_queries(selected_passes))
        groups = defaultdict(list)
        for pokemon in all_pokemon:
            groups[self._keeper_key(pokemon)].append(pokemon)
        ambiguous = {key for key, rows in groups.items()
                     if key is None or any(row.decision != "KEEP" for row in rows)
                     or len({getattr(row, "scan_session_id", "") for row in rows}) > 1}
        required = {self._keeper_key(p) for p in need_fav if self._keeper_key(p) not in ambiguous}
        # Include already-starred occurrences of these same signatures. Seeing
        # one must not consume the allowance for an identical unstarred keeper.
        remaining = Counter(self._keeper_key(p) for p in all_pokemon
                            if p.decision == "KEEP" and self._keeper_key(p) in required)
        queries = list(self._keeper_queries(selected_passes, required))
        blocked = sum(self._keeper_key(p) in ambiguous for p in need_fav)
        self._write_log(all_pokemon)
        totals = dict(favorited=0, checked=0, skipped=0, dry_run=dry_run)
        for name, query, flags in queries:
            if not remaining or not self._active():
                break
            if not any(key[6:] == tuple(flags.values()) for key in remaining):
                continue
            if self.on_progress:
                self.on_progress(0, 0, f"Pass: {name}")
            result = self._run_favorite_pass(query, remaining, dry_run, len(need_fav), flags=flags)
            for key in ("favorited", "checked", "skipped"):
                totals[key] += result.get(key, 0)
            if result.get("error"):
                totals["error"] = result["error"]
                break
        totals.update(unmatched=sum(remaining.values()) + blocked, ambiguous=blocked,
                      aborted=self._abort)
        if blocked:
            totals["note"] = "Some keeper records have incomplete stats, conflicting decisions, or indistinguishable occurrences across scan sessions. These matches need review."
        return totals

    def _run_favorite_pass(self, search_query, keeper_set, dry_run, total_keepers, *, flags=None):
        return self._run_pass(search_query, True, dry_run=dry_run, keepers=keeper_set, flags=flags)

    def favorite_by_filter(self, search_query, label=""):
        try:
            return self._favorite_by_filter(search_query, label)
        finally:
            self._close_reader()

    def _favorite_by_filter(self, search_query, label):
        result = self._run_pass(search_query, True)
        result["label"] = label
        return result

    def unfavorite_all(self):
        try:
            return self._unfavorite_all()
        finally:
            self._close_reader()

    def _unfavorite_all(self):
        # Empty search stays on the game's suggestion page. Every Pokemon has
        # nonnegative CP; this stable all-storage query opens an actual grid.
        return self._run_pass("cp0-", False)

    def _open_pass(self, query):
        while self._active():
            try:
                return self._open_pass_once(query)
            except _ReacquireAction:
                # A pause during navigation invalidates the filter proof too.
                continue
        return 0

    def _open_pass_once(self, query):
        generation = self._scanner._pause_generation
        if not query.strip():
            raise ValueError("Action filter must open a Pokemon list; empty search shows suggestions")
        if re.search(r"(?:^|[&,;|])!?favorite(?:$|[&,;|])", query, re.IGNORECASE):
            raise ValueError("Action filter must not depend on favorite state")
        for action in (self.nav.navigate_to_storage, lambda: self.nav.enter_search(query, verify=True)):
            if not self._active():
                return 0
            self._check_generation(generation)
            outcome = action()
            self._check_generation(generation)
            if outcome is False:
                if self._abort:
                    return 0
                raise RuntimeError("Could not verify the complete storage search filter; no Pokemon opened")
        if not self._active():
            return 0
        self._check_generation(generation)
        total = self.nav.read_filtered_count()
        self._check_generation(generation)
        if not total:
            # Count OCR cannot distinguish an empty result from unreadable.
            # Never tap the first tile speculatively after a zero result.
            log.info("Filter is empty or count unreadable; no Pokemon opened")
            return 0
        for action in (self.nav.tap_first_pokemon, self.nav.open_first_appraisal):
            if not self._active():
                return 0
            self._check_generation(generation)
            outcome = action()
            self._check_generation(generation)
            if outcome is False:
                if self._abort:
                    return 0
                raise RuntimeError("Could not open the filtered appraisal")
        return total

    @staticmethod
    def _identity(snapshot):
        authority = snapshot.caught_species or getattr(snapshot, "candy_family", "")
        if (not snapshot.read_complete or not authority or not snapshot.display_name
                or snapshot.hp <= 0 or any(iv < 0 or iv > 15 for iv in snapshot.ivs)):
            return None
        return (snapshot.caught_species, getattr(snapshot, "candy_family", "") if not snapshot.caught_species else "",
                snapshot.display_name, snapshot.hp, *snapshot.ivs,
                snapshot.shiny, snapshot.shadow, snapshot.lucky, snapshot.is_dynamax)

    def _same_identity(self, before, after, first, second):
        left, right = self._identity(before), self._identity(after)
        return (left is not None and right is not None
                and (left == right or (left[:2] == right[:2] and left[3:] == right[3:]
                     and self._scanner._review_name_drift(before, after, first, second)))
                and appraisal_frames_stable(first, second))

    def _read_identity(self, image):
        if self.nav.detect_screen(image) != "appraisal" or not self.reader.are_bars_visible(image):
            raise RuntimeError("Action held: appraisal is not confirmed")
        detail, appraisal = self._scanner._read_appraisal_snapshot(image)
        if self._current_flags is not None:
            detail.update(self._current_flags)
        # A missing classifier result is not evidence that its flag is false.
        if any(type(detail.get(key)) is not bool for key in ("shiny", "shadow", "lucky", "is_dynamax")):
            detail["snapshot_read_complete"] = False
        return AppraisalSnapshot.from_reads(detail, appraisal)

    def _check_generation(self, generation):
        if self._paused or self._scanner._pause_generation != generation:
            raise _ReacquireAction()

    @timing.timed("action.acquire_identity")
    def _acquire_identity(self, previous_frame, previous_snapshot, require_transition):
        """Share native frame/pair reading without invoking CP recovery."""
        scanner = self._scanner
        for _attempt in range(3):
            generation = scanner._pause_generation
            scanner._settled_frame_pair = None
            frame, status = scanner._wait_for_stable_appraisal(
                previous_accepted=previous_frame, require_transition=require_transition,
                allow_structured_fallback=True,
            )
            pair, scanner._settled_frame_pair = scanner._settled_frame_pair, None
            if self._abort:
                return None, frame
            self._check_generation(generation)
            if frame is None:
                raise RuntimeError(f"Action held: {status.replace('_', ' ')}")
            snapshot = self._read_identity(frame)
            if self._abort:
                return None, frame
            if (isinstance(pair, tuple) and len(pair) == 2 and pair[1] is frame
                    and pair[0] is not frame and scanner._independent_frame_sources(*pair)):
                confirmation_frame = pair[0]
            else:
                confirmation_frame = scanner._fast_screencap()
            if self._abort:
                return None, frame
            confirmation = self._read_identity(confirmation_frame)
            self._check_generation(generation)
            if not self._same_identity(snapshot, confirmation, frame, confirmation_frame):
                continue
            if status != "stable":
                # Two full agreeing reads may prove an unobserved move only
                # when species/HP/IV/flags differ. Name OCR alone proves none.
                before, after = self._identity(previous_snapshot) if previous_snapshot else None, self._identity(snapshot)
                if (status != "stable_transition_unobserved" or before is None
                        or (before[:2], before[3:]) == (after[:2], after[3:])):
                    raise RuntimeError("Action held: next storage position could not be verified")
            return snapshot, frame
        raise RuntimeError("Action held: complete appraisal identity did not agree after 3 reads")

    @timing.timed("action.star")
    def _set_star(self, snapshot, frame, target, *, dry_run=False, cp_decision=None):
        """Check identity and tri-state star before one tap, then verify it."""
        desired = "on" if target else "off"
        scanner = self._scanner
        generation = scanner._pause_generation
        initial = frame
        for _attempt in range(3):
            frame = scanner._fast_screencap()
            if self._abort:
                return False, frame
            fresh = self._read_identity(frame)
            self._check_generation(generation)
            if not self._same_identity(snapshot, fresh, initial, frame):
                raise RuntimeError("Action held: appraisal identity changed before star input")
            state = favorite_state(frame, self.regions.favorite_star_region)
            if state in ("on", "off"):
                break
        else:
            raise RuntimeError("Action held: favorite star is unreadable")
        if cp_decision is not None and not cp_decision.cp_source.startswith("calculated"):
            # A newly visible, valid contradictory CP must not inherit an old
            # recovery result, even when rounded HP and all IVs are identical.
            cp, _confidence = self.reader.read_cp(frame)
            visible = validate_snapshot(replace(fresh, cp=cp), allow_calculated_cp=False)
            if visible.accepted and visible.snapshot.cp != snapshot.cp:
                raise RuntimeError("Action held: visible CP changed before star input")
        self._check_generation(generation)
        if self._abort:
            return False, frame
        if state == desired:
            return False, frame
        if dry_run:
            return True, frame
        if not scanner._safe_tap(*self.regions.favorite_star_region.center, jitter=0):
            return False, frame
        for _attempt in range(3):
            if self._abort:
                return False, frame
            time.sleep(0.1)
            frame = scanner._fast_screencap()
            if self._abort:
                return False, frame
            fresh = self._read_identity(frame)
            if self._abort:
                return False, frame
            if not self._same_identity(snapshot, fresh, initial, frame):
                raise RuntimeError("Action held: appraisal identity changed after star input")
            if favorite_state(frame, self.regions.favorite_star_region) == desired:
                return True, frame
        raise RuntimeError("Action held: star change was not confirmed after one tap")

    def _advance_action(self, snapshot, frame):
        """The pre-swipe frame must still be the position just completed."""
        while self._active():
            generation = self._scanner._pause_generation
            fresh = self._scanner._fast_screencap()
            if self._abort:
                return False
            current = self._read_identity(fresh)
            if self._abort:
                return False
            try:
                self._check_generation(generation)
            except _ReacquireAction:
                continue
            if not self._same_identity(snapshot, current, frame, fresh):
                raise RuntimeError("Action held: completed position changed before swipe")
            self._scanner._last_stable_image = fresh
            return self._scanner._fast_swipe()
        return False

    def _run_pass(self, query, target, *, dry_run=False, keepers=None, flags=None):
        count_key = "favorited" if target else "unfavorited"
        result = {count_key: 0, "checked": 0, "skipped": 0}
        scanner = self._scanner
        self._current_flags = None
        scanner._last_validated_identity_key = scanner._previous_validated_identity_key = None
        previous_frame = previous_snapshot = None
        transition = False
        try:
            if not self._active():
                return result | {"aborted": True}
            self.reader.prepare_native_ocr()
            total = self._open_pass(query)
            self._current_flags = flags
            if total == 0 and not self._abort:
                result["note"] = "Filter empty or count unreadable; no Pokemon opened"
            while result["checked"] < total and self._active():
                try:
                    generation = scanner._pause_generation
                    decision = None
                    if keepers is None:
                        snapshot, frame = self._acquire_identity(previous_frame, previous_snapshot, transition)
                    else:
                        decision, frame, kind, reason = scanner._acquire_validated_snapshot(
                            previous_accepted=previous_frame, require_transition=transition,
                        )
                        if kind == "reacquire":
                            continue
                        if kind == "transition_returned_to_previous":
                            decision, frame, kind, reason = scanner._recover_failed_transition(frame)
                        if self._abort:
                            break
                        if decision is None:
                            if kind != "invalid" or frame is None:
                                raise RuntimeError(f"Action held: {reason}")
                            # Unresolved CP cannot authorize a keeper star, but
                            # a confirmed position may be skipped without input.
                            snapshot, frame = self._acquire_identity(previous_frame, previous_snapshot, transition)
                        else:
                            snapshot = decision.snapshot
                            if flags is not None:
                                snapshot = replace(snapshot, **flags)
                                decision = replace(decision, snapshot=snapshot)
                    if self._abort or snapshot is None:
                        break
                    self._check_generation(generation)
                    # A generic family is sufficient for collection scanning,
                    # but cannot authorize an exact-form keeper action.
                    match_key = self._snapshot_keeper_key(snapshot) if decision and decision.exact_form else None
                    matches = keepers is None or (match_key is not None and keepers[match_key] > 0)
                    if matches:
                        changed, frame = self._set_star(snapshot, frame, target, dry_run=dry_run, cp_decision=decision)
                        if self._abort:
                            break
                        result[count_key] += int(changed)
                        if keepers is not None:
                            keepers[match_key] -= 1
                            if not keepers[match_key]:
                                del keepers[match_key]
                    else:
                        result["skipped"] += 1
                except _ReacquireAction:
                    continue
                result["checked"] += 1
                if self.on_progress:
                    summary = snapshot.caught_species or snapshot.detected_species or "Unknown species"
                    self.on_progress(result["checked"], total,
                                     f"#{result['checked']} {summary} HP{snapshot.hp} "
                                     f"{snapshot.atk}/{snapshot.def_}/{snapshot.sta} · {count_key}: {result[count_key]}"
                                     + (" (dry run)" if dry_run else ""))
                scanner._previous_validated_identity_key = scanner._last_validated_identity_key
                scanner._last_validated_identity_key = scanner._complete_identity_key(snapshot) if decision else None
                previous_frame, previous_snapshot = frame, snapshot
                if result["checked"] >= total or (keepers is not None and not keepers) or not self._active():
                    break
                scanner._last_stable_image = frame
                if not self._advance_action(snapshot, frame):
                    if self._abort:
                        break
                    raise RuntimeError("Action held: could not advance from confirmed appraisal")
                previous_frame = scanner._last_stable_image
                transition = True
        except Exception as exc:
            result["error"] = str(exc)
            log.exception("Mass action held")
            if self.on_error:
                self.on_error(str(exc))
        finally:
            self._current_flags = None
        result["aborted"] = self._abort
        return result

    def _close_reader(self):
        self._scanner._close_reader()

    def _write_log(self, all_pokemon: list[Pokemon]):
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        path = LOGS_DIR / f"execution_{time.strftime('%Y%m%d_%H%M%S')}.json"
        data = {"keep": [], "transfer": []}
        for pokemon in all_pokemon:
            data["keep" if pokemon.decision == "KEEP" else "transfer"].append({
                "species": pokemon.species, "cp": pokemon.cp,
                "atk": pokemon.atk, "def": pokemon.def_, "sta": pokemon.sta,
                "iv_pct": round(pokemon.iv_pct, 4),
                "decision": pokemon.decision, "reason": pokemon.decision_reason,
            })
        path.write_text(json.dumps(data, indent=2))

    def pause(self):
        self._paused = True
        self._scanner.pause()

    def resume(self):
        self._scanner.resume()
        self._paused = False

    def abort(self):
        self._abort = True
        self._paused = False
        self._scanner.abort()
