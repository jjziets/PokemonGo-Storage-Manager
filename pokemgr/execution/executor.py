# TRACEWEAVER: file-role=verified-appraisal-actions; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
"""Verified appraisal actions using the scanner's stream and native reader.

Category actions need stable identity and a confirmed star state, not CP.
Keeper actions additionally require an exact validated database signature.
Keeper searches exclude existing favorites. Their bounded traversals refresh
and verify the shrinking result count after confirmed changes. Category actions
retain stable membership. Identical stats never merge database rows.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
import json
import logging
import re
import time
import weakref
from typing import Callable

from .. import timing
from ..adb.controller import ADBController
from ..calibration.profile import CalibrationProfile
from ..data.database import PokemonDatabase
from ..data.models import Pokemon
from ..indexer.snapshot import (
    AppraisalSnapshot, appraisal_frames_stable, appraisal_region_diffs, validate_snapshot,
)
from ..indexer.state_machine import IndexingStateMachine
from ..reader.icons import favorite_state
from ..config import LOGS_DIR
from .keeper_queries import pending_cp_batches
from .favorite_sync import FavoriteSync

log = logging.getLogger(__name__)


@dataclass
class KeeperFavoritePlan:
    """Recorded counts before pass selection and live identity/star checks."""

    total: int
    already_favorited: int
    ambiguous: int
    remaining: Counter

    @property
    def unstarred(self):
        return self.total - self.already_favorited

    @property
    def eligible(self):
        return self.unstarred - self.ambiguous


class _ReacquireAction(Exception):
    """No star input was sent; discard a paused read and try again."""


class _RefreshFavorites(Exception):
    """Discard carousel evidence and verify the remaining nonfavorite list."""


class Executor:
    """Set stars only on independently confirmed storage positions."""

    ALL_FAV_PASSES = [
        ("Normal", "!shiny&!shadow&!dynamax&!gigantamax"),
        ("Shiny", "shiny"), ("Shadow", "shadow"),
        ("Dynamax", "dynamax"), ("Gigantamax", "gigantamax"),
    ]

    # TRACEWEAVER: entrypoint=__init__; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
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
        self._favorite_sync = None
        self._star_confirmed = False
        self.nav.is_cancelled = lambda: self._abort or self._paused
        self.nav._observation_generation = lambda: self._scanner._pause_generation

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

    # TRACEWEAVER: entrypoint=plan_keeper_favorites; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
    @classmethod
    def plan_keeper_favorites(cls, all_pokemon):
        """Share the exact occurrence guards with the confirmation, without I/O."""
        keepers = [p for p in all_pokemon if p.decision == "KEEP"]
        need_fav = [p for p in keepers if not p.favorited]
        groups = defaultdict(list)
        for pokemon in all_pokemon:
            groups[cls._keeper_key(pokemon)].append(pokemon)
        ambiguous = {key for key, rows in groups.items()
                     if key is None or any(row.decision != "KEEP" for row in rows)
                     or len({getattr(row, "scan_session_id", "") for row in rows}) > 1}
        required = {cls._keeper_key(p) for p in need_fav if cls._keeper_key(p) not in ambiguous}
        # The live query excludes stars. Only initially unstarred eligible rows
        # authorize OFF-to-ON changes; starred duplicate companions add no budget.
        remaining = Counter(cls._keeper_key(p) for p in need_fav if cls._keeper_key(p) in required)
        blocked = sum(cls._keeper_key(p) in ambiguous for p in need_fav)
        return KeeperFavoritePlan(len(keepers), len(keepers) - len(need_fav), blocked, remaining)

    def _favorite_keepers(self, dry_run, selected_passes):
        all_pokemon = self.db.get_all()
        plan = self.plan_keeper_favorites(all_pokemon)
        if not plan.unstarred:
            return {"favorited": 0, "checked": 0}
        if self._abort:
            return {"favorited": 0, "checked": 0, "dry_run": dry_run, "aborted": True}
        # Validate [] separately from None before inspecting the inventory.
        list(self._selected_queries(selected_passes))
        remaining = plan.remaining
        self._favorite_sync = None if dry_run else FavoriteSync(
            self.db, all_pokemon, self._keeper_key, changes_only=True)
        queries = list(self._keeper_queries(selected_passes, remaining))
        blocked = plan.ambiguous
        self._write_log(all_pokemon)
        totals = dict(favorited=0, checked=0, skipped=0, dry_run=dry_run)
        # Include !favorite before batching so the complete search stays inside
        # the native editor's length limit. Build only still-pending CP groups.
        batches = ((name, batch, flags) for name, query, flags in queries
                   for batch in pending_cp_batches(query + "&!favorite", remaining, flags))
        for name, batch, flags in batches:
            if not remaining or not self._active():
                break
            pending = Counter({key: remaining[key] for key in batch.keys if remaining[key] > 0})
            if not pending:
                continue
            before = pending.copy()
            if self.on_progress:
                self.on_progress(0, 0, f"Pass: {name} · pending keeper CPs · excluding favorites")
            log.info("Keeper CP batch: %d known occurrences, filter=%s", sum(pending.values()), batch.query)
            result = self._run_favorite_pass(batch.query, pending, dry_run, plan.unstarred, flags=flags)
            # Keep completed work even when a later position holds the pass.
            # A local allowance also ends this batch as soon as it is exhausted.
            for key, consumed in (before - pending).items():
                remaining[key] -= consumed
                if not remaining[key]:
                    del remaining[key]
            for key in ("favorited", "checked", "skipped", "restarts", "refreshes"):
                totals[key] = totals.get(key, 0) + result.get(key, 0)
            if result.get("error"):
                totals["error"] = result["error"]
                break
        totals.update(unmatched=sum(remaining.values()) + blocked, ambiguous=blocked,
                      aborted=self._abort)
        if blocked:
            totals["note"] = "Some keeper records have incomplete stats, conflicting decisions, or indistinguishable occurrences across scan sessions. These matches need review."
        self._add_sync_result(totals)
        return totals

    def _run_favorite_pass(self, search_query, keeper_set, dry_run, total_keepers, *, flags=None):
        """Converge across shrinking lists without assuming carousel positions.

        Each changed lap removes at least one member. Re-enter the full verified
        query from the top and require exactly that many fewer results, even if
        the final keeper was just changed. A full nonmutating lap ends the batch;
        an incomplete one holds. Unknown post-input outcomes never refresh.
        """
        if "!favorite" not in search_query.casefold().split("&"):
            return self._run_pass(search_query, True, dry_run=dry_run,
                                  keepers=keeper_set, flags=flags)
        totals = dict(favorited=0, checked=0, skipped=0, restarts=0, refreshes=0)
        expected_count = None
        try:
            self.reader.prepare_native_ocr()
            while self._active():
                total = self._open_pass(
                    search_query, verify_count=True, nonfavorite=True,
                    expected_count=expected_count, open_appraisal=bool(keeper_set))
                if self._abort or not total or not keeper_set:
                    break
                result = self._run_pass(search_query, True, dry_run=dry_run,
                                        keepers=keeper_set, flags=flags,
                                        nonfavorite=True, opened_total=total)
                for key in ("favorited", "checked", "skipped", "restarts"):
                    totals[key] += result.get(key, 0)
                if result.get("error"):
                    totals["error"] = result["error"]
                    break
                if self._abort or dry_run:
                    break
                changes = result["favorited"]
                if not changes:
                    if result["checked"] != total:
                        raise RuntimeError("Action held: nonfavorite traversal was incomplete without confirmed changes")
                    break
                expected_count = total - changes
                totals["refreshes"] += 1
                log.info("Refreshing nonfavorite filter after %d changes; expected %d remaining",
                         changes, expected_count)
                if self.on_progress:
                    self.on_progress(totals["checked"], 0,
                                     f"Refreshing remaining nonfavorites after {changes} confirmed favorites")
        except Exception as exc:
            totals["error"] = str(exc)
            log.exception("Nonfavorite action held")
            if self.on_error:
                self.on_error(str(exc))
        totals["aborted"] = self._abort
        return totals

    def favorite_by_filter(self, search_query, label=""):
        try:
            return self._favorite_by_filter(search_query, label)
        finally:
            self._close_reader()

    def _favorite_by_filter(self, search_query, label):
        self._favorite_sync = FavoriteSync(self.db, self.db.get_all(), self._keeper_key)
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
        self._favorite_sync = FavoriteSync(self.db, self.db.get_all(), self._keeper_key)
        return self._run_pass("cp0-", False)

    def _record_star(self, snapshot, target, cp_decision):
        self._star_confirmed = True
        sync = getattr(self, "_favorite_sync", None)
        if sync is not None:
            key = (self._snapshot_keeper_key(snapshot)
                   if cp_decision is not None and cp_decision.exact_form else None)
            sync.observe(key, target)

    def _add_sync_result(self, result):
        sync = getattr(self, "_favorite_sync", None)
        if sync is None:
            return
        details = sync.result()
        if result.get("note") and details.get("note"):
            details["note"] = result["note"] + "\n" + details["note"]
        if result.get("error") and details.get("error") and result["error"] != details["error"]:
            details["error"] = result["error"] + "\n" + details["error"]
        result.update(details)

    def _open_pass(self, query, *, verify_count=False, nonfavorite=False,
                   expected_count=None, open_appraisal=True):
        while self._active():
            try:
                return self._open_pass_once(query, verify_count=verify_count,
                                            nonfavorite=nonfavorite, expected_count=expected_count,
                                            open_appraisal=open_appraisal)
            except _ReacquireAction:
                # A pause during navigation invalidates the filter proof too.
                continue
        return 0

    def _open_pass_once(self, query, *, verify_count=False, nonfavorite=False,
                        expected_count=None, open_appraisal=True):
        generation = self._scanner._pause_generation
        if not query.strip():
            raise ValueError("Action filter must open a Pokemon list; empty search shows suggestions")
        favorite_terms = re.findall(r"(?:^|[&,;|])(!?favorite)(?=$|[&,;|])", query, re.IGNORECASE)
        if nonfavorite and (not verify_count or favorite_terms != ["!favorite"]
                            or any(char in query for char in ",;|")):
            raise ValueError("Nonfavorite traversal requires one conjunctive !favorite filter and verified count")
        if favorite_terms and not nonfavorite:
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
        total = (self.nav.read_filtered_count_verified() if verify_count
                 else self.nav.read_filtered_count())
        self._check_generation(generation)
        if self._abort:
            return 0
        if verify_count and (type(total) is not int or not 0 <= total <= 10000):
            raise RuntimeError("Action held: filtered count was not independently confirmed; no Pokemon opened")
        if expected_count is not None and total != expected_count:
            raise RuntimeError("Action held: nonfavorite count did not match confirmed changes "
                               f"(expected {expected_count}, saw {total}); no Pokemon opened")
        if not open_appraisal:
            return total
        if not total:
            # Legacy category counts conflate empty and unreadable. A keeper
            # zero is independently confirmed; neither permits a tile input.
            log.info("Filter is empty%s; no Pokemon opened",
                     "" if verify_count else " or count unreadable")
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

    @staticmethod
    def _same_default_species_label(before, after):
        """Compare accent-only default-label drift without rewriting identity.

        A resolved GameMaster name and its raw caught label may use different
        accents. Both display names and detected species must still equal the
        unchanged caught label; retain letters, digits, punctuation and forms.
        """
        import unicodedata

        def label(value):
            return " ".join("".join(
                char for char in unicodedata.normalize("NFD", value)
                if not unicodedata.combining(char)
            ).casefold().split())

        family = label(before.caught_species)
        return (bool(family) and before.caught_species == after.caught_species
                and all(label(value) == family for value in (
                    before.display_name, after.display_name,
                    before.detected_species, after.detected_species,
                )))

    def _same_identity(self, before, after, first, second):
        left, right = self._identity(before), self._identity(after)
        return (left is not None and right is not None
                and (left == right or (left[:2] == right[:2] and left[3:] == right[3:]
                     and (self._same_default_species_label(before, after)
                          or self._scanner._review_name_drift(before, after, first, second))))
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

    def _gym_skip_identity(self, snapshot):
        """A visible gym defender can authorize a skip, never a keeper match."""
        if snapshot.in_gym is not True:
            return None
        identity = self._scanner._review_identity(snapshot)
        flags = (snapshot.shiny, snapshot.shadow, snapshot.lucky, snapshot.is_dynamax)
        if identity is None or any(type(value) is not bool for value in flags):
            return None
        return (*identity, *flags)

    def _same_gym_skip_identity(self, before, after, first, second):
        """Require independent, settled gym observations with matching IVs."""
        import math

        left, right = self._gym_skip_identity(before), self._gym_skip_identity(after)
        if left is None or left != right or first is second:
            return False

        def ordered(older, newer):
            bounds = tuple(image.info.get(key) for image in (older, newer)
                           for key in ("pokemgr_capture_started_at", "pokemgr_capture_finished_at"))
            return (all(type(value) in (int, float) and math.isfinite(value) for value in bounds)
                    and 0 < bounds[0] <= bounds[1] < bounds[2] <= bounds[3]
                    and self._scanner._specimen_frame_sources_ordered(older, newer))

        return (ordered(first, second)
                and appraisal_frames_stable(first, second)
                and self.reader.appraisal_bars_stable(first, second))

    @timing.timed("action.acquire_identity")
    def _acquire_identity(self, previous_frame, previous_snapshot, require_transition,
                          *, allow_gym_skip=False):
        """Share native frame/pair reading without invoking CP recovery."""
        scanner = self._scanner
        for _attempt in range(3):
            generation = scanner._pause_generation
            if self._abort:
                return None, previous_frame
            self._check_generation(generation)
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
            self._check_generation(generation)
            reuse_pair = (isinstance(pair, tuple) and len(pair) == 2 and pair[1] is frame
                          and pair[0] is not frame and scanner._independent_frame_sources(*pair))
            if reuse_pair:
                confirmation_frame = pair[0]
            else:
                confirmation_frame = scanner._fast_screencap()
            if self._abort:
                return None, frame
            confirmation = self._read_identity(confirmation_frame)
            if self._abort:
                return None, frame
            self._check_generation(generation)
            gym = snapshot.in_gym or confirmation.in_gym
            if gym:
                gym_pair = ((confirmation, snapshot, confirmation_frame, frame) if reuse_pair
                            else (snapshot, confirmation, frame, confirmation_frame))
                matches = allow_gym_skip and self._same_gym_skip_identity(*gym_pair)
            else:
                matches = self._same_identity(snapshot, confirmation, frame, confirmation_frame)
            if self._abort:
                return None, frame
            self._check_generation(generation)
            if not matches:
                fields = ("display_name", "detected_species", "caught_species", "candy_family",
                          "hp", "atk", "def_", "sta", "read_complete", "in_gym",
                          "shiny", "shadow", "lucky", "is_dynamax")
                changes = {field: (getattr(snapshot, field), getattr(confirmation, field))
                           for field in fields if getattr(snapshot, field) != getattr(confirmation, field)}
                log.warning("Action identity pair %d/3 disagreed for %s: "
                            "HP=(%s,%s); gym=(%s,%s); allow_gym_skip=%s; fields=%s; region_diffs=%s",
                            _attempt + 1, snapshot.caught_species, snapshot.hp, confirmation.hp,
                            snapshot.in_gym, confirmation.in_gym, allow_gym_skip,
                            changes, appraisal_region_diffs(frame, confirmation_frame))
                continue
            if status != "stable":
                # Two full agreeing reads may prove an unobserved move only
                # when species/HP/IV/flags differ. Name OCR alone proves none.
                before, after = self._identity(previous_snapshot) if previous_snapshot else None, self._identity(snapshot)
                if allow_gym_skip and (gym or (previous_snapshot is not None and previous_snapshot.in_gym)):
                    def transition_key(value):
                        if value is None or (self._identity(value) is None
                                             and self._gym_skip_identity(value) is None):
                            return None
                        # A missing HP or changing gym status cannot establish
                        # a new position. Require species, IVs or flags to differ.
                        return (value.caught_species, value.candy_family if not value.caught_species else "",
                                *value.ivs, value.shiny, value.shadow, value.lucky, value.is_dynamax)
                    before, after = transition_key(previous_snapshot), transition_key(snapshot)
                    if (status != "stable_transition_unobserved" or before is None
                            or after is None or before == after):
                        raise RuntimeError("Action held: next storage position could not be verified")
                    return snapshot, frame
                if (status != "stable_transition_unobserved" or before is None
                        or (before[:2], before[3:]) == (after[:2], after[3:])):
                    raise RuntimeError("Action held: next storage position could not be verified")
            return snapshot, frame
        reason = ("Action held: complete appraisal identity did not agree after 3 paired reads "
                  f"(species={snapshot.caught_species!r}, HP={snapshot.hp}, "
                  f"gym={snapshot.in_gym}, fields={changes})")
        position = getattr(self, "_action_position", None)
        scanner._save_failed_appraisal(frame, reason, phase="action_identity", position=position)
        scanner._save_failed_appraisal(confirmation_frame, reason,
                                      phase="action_identity_confirmation", position=position)
        raise RuntimeError(reason)

    def _confirm_star_input(self, snapshot, initial, generation):
        """Reread a transient mismatch without changing the current position."""
        import math

        scanner = self._scanner
        require_pair = False
        candidate_frame = candidate_snapshot = failed_frame = None
        seen_frames = weakref.WeakValueDictionary()
        seen_sources = set()
        last_changes = {}
        last_star_unknown = False

        def ordered(older, newer):
            # JPEG/fallback captures have no stream sequence. They still need
            # independent capture intervals; copied pixels are not new reads.
            bounds = tuple(image.info.get(key) for image in (older, newer)
                           for key in ("pokemgr_capture_started_at", "pokemgr_capture_finished_at"))
            return (all(type(value) in (int, float) and math.isfinite(value) for value in bounds)
                    and 0 < bounds[0] <= bounds[1] < bounds[2] <= bounds[3]
                    and scanner._specimen_frame_sources_ordered(older, newer))

        def new_observation(image):
            unseen = seen_frames.get(id(image)) is not image
            seen_frames[id(image)] = image
            source = tuple(image.info.get(key) for key in (
                "pokemgr_stream_session", "pokemgr_stream_sequence", "pokemgr_stream_pts_us",
            ))
            if (isinstance(source[0], str) and source[0]
                    and all(type(value) is int and value > 0 for value in source[1:])):
                unseen = unseen and source not in seen_sources
                seen_sources.add(source)
            return unseen

        new_observation(initial)
        for attempt in range(3):
            last_star_unknown = False
            if attempt:
                time.sleep(.1)
            if self._abort:
                return None
            self._check_generation(generation)
            frame = scanner._fast_screencap()
            if self._abort:
                return None
            unseen = new_observation(frame)
            fresh = self._read_identity(frame)
            if self._abort:
                return None
            self._check_generation(generation)
            matches = unseen and self._same_identity(snapshot, fresh, initial, frame)
            confirmed = matches and not require_pair
            if not matches:
                require_pair = True
                candidate_frame = candidate_snapshot = None
                failed_frame = frame
                fields = ("display_name", "detected_species", "caught_species", "candy_family",
                          "hp", "atk", "def_", "sta", "read_complete",
                          "shiny", "shadow", "lucky", "is_dynamax")
                last_changes = {field: (getattr(snapshot, field), getattr(fresh, field))
                                for field in fields if getattr(snapshot, field) != getattr(fresh, field)}
                log.warning("Pre-star check %d/3 disagreed for %s at action position %s: "
                            "fresh=%s; fields=%s; region_diffs=%s",
                            attempt + 1, snapshot.caught_species, getattr(self, "_action_position", None),
                            unseen, last_changes, appraisal_region_diffs(initial, frame))
            elif require_pair:
                newer = (frame is not initial and ordered(initial, frame)
                         and (failed_frame is None or ordered(failed_frame, frame)))
                confirmed = (newer and candidate_frame is not None
                             and candidate_frame is not frame
                             and ordered(candidate_frame, frame)
                             and self._same_identity(candidate_snapshot, fresh, candidate_frame, frame)
                             and self.reader.appraisal_bars_stable(candidate_frame, frame))
                candidate_frame, candidate_snapshot = (frame, fresh) if newer else (None, None)
            if self._abort:
                return None
            self._check_generation(generation)
            if confirmed:
                state = favorite_state(frame, self.regions.favorite_star_region)
                if self._abort:
                    return None
                self._check_generation(generation)
                if state in ("on", "off"):
                    if require_pair:
                        log.info("Pre-star identity confirmed by two fresh reads for %s",
                                 snapshot.caught_species)
                    return fresh, frame, state
                last_star_unknown = True
        if require_pair and not last_star_unknown:
            reason = ("Action held: appraisal identity changed before star input; "
                      f"no two fresh matching reads after 3 attempts (fields={last_changes})")
        else:
            reason = "Action held: favorite star is unreadable after 3 attempts"
        position = getattr(self, "_action_position", None)
        scanner._save_failed_appraisal(initial, reason, phase="action_before_star", position=position)
        scanner._save_failed_appraisal(frame, reason, phase="action_before_star_reread", position=position)
        if failed_frame is not None and failed_frame is not frame:
            scanner._save_failed_appraisal(failed_frame, reason,
                                          phase="action_before_star_mismatch", position=position)
        raise RuntimeError(reason)

    @timing.timed("action.star")
    def _set_star(self, snapshot, frame, target, *, dry_run=False, cp_decision=None,
                  allow_change=True, nonfavorite=False):
        """Check identity and tri-state star before one tap, then verify it."""
        desired = "on" if target else "off"
        self._star_confirmed = False
        self._star_input_sent = False
        scanner = self._scanner
        generation = scanner._pause_generation
        initial = frame
        confirmed = self._confirm_star_input(snapshot, frame, generation)
        if confirmed is None:
            return False, frame
        fresh, frame, state = confirmed
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
            if nonfavorite:
                # This may be a retained carousel card after its list member
                # disappeared. It supplies neither an occurrence nor DB proof.
                raise _RefreshFavorites("Already-favorited card remains in nonfavorite carousel")
            if not dry_run:
                self._record_star(snapshot, target, cp_decision)
            return False, frame
        if dry_run:
            return True, frame
        if not allow_change:
            raise RuntimeError("Action held: keeper change allowance exhausted after pass restart")
        # Once dispatch begins, even an exception may represent delivered
        # input. Such uncertainty must never become an automatic refresh.
        self._star_input_sent = True
        if not scanner._safe_tap(*self.regions.favorite_star_region.center, jitter=0):
            if self._abort:
                return False, frame
            self._check_generation(generation)
            raise RuntimeError("Action held: star input was not sent")
        # Paired captures already wait for a fresh post-input source frame.
        # Try that frame immediately, then retain all three delayed attempts
        # so an early observation cannot shorten the existing update window.
        stream_readback = getattr(self.adb, "has_stream_frames", False) is True
        for _attempt in range(4 if stream_readback else 3):
            if self._abort:
                return False, frame
            if _attempt or not stream_readback:
                time.sleep(0.1)
            if self._abort:
                return False, frame
            frame = scanner._fast_screencap()
            if self._abort:
                return False, frame
            fresh = self._read_identity(frame)
            if self._abort:
                return False, frame
            if not self._same_identity(snapshot, fresh, initial, frame):
                raise RuntimeError("Action held: appraisal identity changed after star input")
            if favorite_state(frame, self.regions.favorite_star_region) == desired:
                self._record_star(snapshot, target, cp_decision)
                return True, frame
        raise RuntimeError("Action held: star change was not confirmed after one tap")

    # TRACEWEAVER: entrypoint=_advance_action; req=REQ-MASS-001,REQ-SCAN-003; trace=TRACE-MASS-001,TRACE-SCAN-003; ver=VER-SCAN-001
    def _advance_action(self, snapshot, frame, *, allow_gym_skip=False):
        """The pre-swipe frame must still be the position just completed."""
        scanner = self._scanner
        generation = scanner._pause_generation
        attempts = 0
        require_pair = False
        candidate_frame = candidate_snapshot = None
        failed_frame = None
        seen_frames = weakref.WeakValueDictionary()
        seen_sources = set()

        def same_position(before, after, first, second):
            if before.in_gym or after.in_gym:
                return allow_gym_skip and self._same_gym_skip_identity(before, after, first, second)
            return self._same_identity(before, after, first, second)

        def new_observation(image):
            # Keep receipts across pause/resume without retaining image buffers.
            unseen = seen_frames.get(id(image)) is not image
            seen_frames[id(image)] = image
            source = tuple(image.info.get(key) for key in (
                "pokemgr_stream_session", "pokemgr_stream_sequence", "pokemgr_stream_pts_us",
            ))
            if (isinstance(source[0], str) and source[0]
                    and all(type(value) is int and value > 0 for value in source[1:])):
                unseen = unseen and source not in seen_sources
                seen_sources.add(source)
            return unseen

        new_observation(frame)
        while self._active():
            if scanner._pause_generation != generation:
                generation = scanner._pause_generation
                attempts = 0
                require_pair = True
                candidate_frame = candidate_snapshot = None
                failed_frame = None
            fresh = scanner._fast_screencap()
            if self._abort:
                return False
            unseen = new_observation(fresh)
            current = self._read_identity(fresh)
            if self._abort:
                return False
            try:
                self._check_generation(generation)
            except _ReacquireAction:
                require_pair = True
                candidate_frame = candidate_snapshot = None
                failed_frame = None
                continue
            attempts += 1
            matches = unseen and same_position(snapshot, current, frame, fresh)
            confirmed = matches and not require_pair
            if not matches:
                require_pair = True
                candidate_frame = candidate_snapshot = None
                if unseen:
                    failed_frame = fresh
                fields = ("caught_species", "candy_family", "display_name", "hp",
                          "atk", "def_", "sta", "read_complete",
                          "shiny", "shadow", "lucky", "is_dynamax")
                changes = {field: (getattr(snapshot, field), getattr(current, field))
                           for field in fields
                           if getattr(snapshot, field) != getattr(current, field)}
                log.warning("Pre-swipe check %d/3 disagreed for %s at action position %s: "
                            "fresh=%s; fields=%s; region_diffs=%s",
                            attempts, snapshot.caught_species, getattr(self, "_action_position", None), unseen, changes,
                            appraisal_region_diffs(frame, fresh))
            elif require_pair:
                # Retry observations alone: no repeated star, keeper consumption
                # or checked-count updates. A pause discards this proof in full.
                newly_observed = fresh is not frame and (
                    failed_frame is None or (fresh is not failed_frame
                    and scanner._specimen_frame_sources_ordered(failed_frame, fresh))
                )
                confirmed = newly_observed and (
                    candidate_frame is not None and candidate_frame is not fresh
                    and scanner._specimen_frame_sources_ordered(candidate_frame, fresh)
                    and same_position(candidate_snapshot, current, candidate_frame, fresh)
                    and self.reader.appraisal_bars_stable(candidate_frame, fresh)
                )
                candidate_frame, candidate_snapshot = (fresh, current) if newly_observed else (None, None)
            if self._abort:
                return False
            try:
                self._check_generation(generation)
            except _ReacquireAction:
                require_pair = True
                candidate_frame = candidate_snapshot = None
                failed_frame = None
                continue
            if confirmed:
                if require_pair:
                    log.info("Pre-swipe identity confirmed by two fresh reads for %s",
                             snapshot.caught_species)
                scanner._last_stable_image = fresh
                advanced = scanner._advance_appraisal(fresh, pause_generation=generation)
                if (not advanced and not self._abort
                        and (self._paused or scanner._pause_generation != generation)):
                    # The helper sends no input when its observation was
                    # invalidated. Retain this completed-action phase and
                    # reacquire its position, never its star or allowance.
                    require_pair = True
                    candidate_frame = candidate_snapshot = None
                    failed_frame = None
                    continue
                return advanced
            if attempts >= 3:
                reason = ("Action held: completed position changed before swipe; "
                          "could not confirm two fresh matching reads after 3 attempts")
                position = getattr(self, "_action_position", None)
                scanner._save_failed_appraisal(frame, reason, phase="action_before_swipe", position=position)
                scanner._save_failed_appraisal(fresh, reason, phase="action_before_swipe_reread", position=position)
                raise RuntimeError(reason)
            time.sleep(0.1)
        return False

    def _run_pass(self, query, target, *, dry_run=False, keepers=None, flags=None,
                  nonfavorite=False, opened_total=None):
        count_key = "favorited" if target else "unfavorited"
        result = {count_key: 0, "checked": 0, "skipped": 0, "restarts": 0}
        scanner = self._scanner
        self._current_flags = None
        previous_frame = previous_snapshot = None
        transition = False
        initial_keepers = keepers.copy() if keepers is not None else None
        changed_occurrences = Counter()
        restart_attempted = False
        sync = getattr(self, "_favorite_sync", None) if not dry_run else None
        phase = "open"

        def clear_traversal():
            nonlocal previous_frame, previous_snapshot, transition
            self._action_position = None
            scanner._last_validated_identity_key = scanner._previous_validated_identity_key = None
            scanner._last_accepted_image = scanner._last_accepted_pause_generation = None
            scanner._last_stable_image = scanner._settled_frame_pair = None
            scanner._transition_required = False
            previous_frame = previous_snapshot = None
            transition = False

        clear_traversal()
        if sync is not None:
            sync.new_traversal()
        try:
            if not self._active():
                return result | {"aborted": True}
            self.reader.prepare_native_ocr()
            if nonfavorite and (keepers is None or target is not True or opened_total is None):
                raise ValueError("Nonfavorite lap requires a verified keeper filter")
            total = opened_total if nonfavorite else (
                self._open_pass(query, verify_count=True) if keepers is not None or sync is not None
                else self._open_pass(query))
            self._current_flags = flags
            if total == 0 and not self._abort:
                result["note"] = "Filter empty or count unreadable; no Pokemon opened"
            while result["checked"] < total and self._active():
                try:
                    phase = "read"
                    gym_skip = False
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
                            self._check_generation(generation)
                            if self._abort:
                                break
                            if nonfavorite:
                                # A changed membership invalidates every old
                                # ordinal/backtracking checkpoint in this lap.
                                raise _RefreshFavorites("Nonfavorite advance was not confirmed")
                            previous_key = scanner._previous_validated_identity_key
                            last_key = scanner._last_validated_identity_key
                            no_previous_checkpoint = (
                                transition and result["checked"] > 0 and last_key is not None
                                and (previous_key is None or previous_key == last_key)
                            )
                            if no_previous_checkpoint:
                                if restart_attempted:
                                    raise RuntimeError("Action held: advance remained unconfirmed after one pass restart")
                                restart_attempted = True
                                result["restarts"] += 1
                                log.info("Unconfirmed keeper advance has no distinct checkpoint; "
                                         "reopening the complete verified filter once")
                                if self.on_progress:
                                    self.on_progress(result["checked"], total,
                                                     "Unconfirmed advance; restarting this filter once. "
                                                     "Completed favorites will be checked without another tap.")
                                self._current_flags = None
                                reopened_total = self._open_pass(query, verify_count=True)
                                if self._abort:
                                    break
                                if reopened_total != total:
                                    raise RuntimeError("Action held: filtered count changed during pass restart "
                                                       f"(expected {total}, saw {reopened_total})")
                                # A new traversal owns its own occurrence
                                # allowance. Actual toggles remain cumulative
                                # and bounded by the original signature budget.
                                keepers.clear()
                                keepers.update(initial_keepers)
                                result["checked"] = result["skipped"] = 0
                                if dry_run:
                                    result[count_key] = 0
                                clear_traversal()
                                if sync is not None:
                                    sync.new_traversal()
                                self._current_flags = flags
                                continue
                            decision, frame, kind, reason = scanner._recover_failed_transition(frame)
                        if self._abort:
                            break
                        if decision is None:
                            if kind != "invalid" or frame is None:
                                raise RuntimeError(f"Action held: {reason}")
                            # Unresolved CP cannot authorize a keeper star, but
                            # a confirmed position may be skipped without input.
                            snapshot, frame = self._acquire_identity(
                                previous_frame, previous_snapshot, transition, allow_gym_skip=True)
                            gym_skip = snapshot is not None and snapshot.in_gym is True
                        else:
                            snapshot = decision.snapshot
                            if flags is not None:
                                snapshot = replace(snapshot, **flags)
                                decision = replace(decision, snapshot=snapshot)
                    if self._abort or snapshot is None:
                        break
                    self._check_generation(generation)
                    self._action_position = result["checked"] + 1
                    # A generic family is sufficient for collection scanning,
                    # but cannot authorize an exact-form keeper action.
                    match_key = self._snapshot_keeper_key(snapshot) if decision and decision.exact_form else None
                    matches = keepers is None or (match_key is not None and keepers[match_key] > 0)
                    if matches:
                        change_options = {}
                        if nonfavorite:
                            change_options["nonfavorite"] = True
                        if (keepers is not None and not dry_run
                                and changed_occurrences[match_key] >= initial_keepers[match_key]):
                            change_options["allow_change"] = False
                        phase = "star"
                        self._star_input_sent = False
                        changed, frame = self._set_star(snapshot, frame, target, dry_run=dry_run,
                                                        cp_decision=decision, **change_options)
                        if self._abort and not self._star_confirmed:
                            break
                        result[count_key] += int(changed)
                        phase = "persist"
                        if sync is not None and sync.error:
                            raise RuntimeError(sync.error)
                        if keepers is not None:
                            if not dry_run:
                                changed_occurrences[match_key] += int(changed)
                            keepers[match_key] -= 1
                            if not keepers[match_key]:
                                del keepers[match_key]
                    else:
                        result["skipped"] += 1
                except _ReacquireAction:
                    if phase == "star" and self._star_input_sent:
                        raise RuntimeError("Action held: pause interrupted star input; outcome is uncertain")
                    if nonfavorite and result[count_key] and not dry_run:
                        raise _RefreshFavorites("Pause invalidated the changed carousel")
                    continue
                phase = "progress"
                result["checked"] += 1
                if self.on_progress:
                    summary = snapshot.caught_species or snapshot.detected_species or "Unknown species"
                    self.on_progress(result["checked"], total,
                                     f"#{result['checked']} {summary} HP{snapshot.hp} "
                                     f"{snapshot.atk}/{snapshot.def_}/{snapshot.sta} · {count_key}: {result[count_key]}"
                                     + (f" · restart {result['restarts']}/1" if result["restarts"] else "")
                                     + (" (dry run)" if dry_run else ""))
                scanner._previous_validated_identity_key = scanner._last_validated_identity_key
                scanner._last_validated_identity_key = scanner._complete_identity_key(snapshot) if decision else None
                scanner._last_accepted_image = (
                    frame if decision and not scanner._paused and scanner._pause_generation == generation
                    else None
                )
                scanner._last_accepted_pause_generation = generation if decision else None
                previous_frame, previous_snapshot = frame, snapshot
                if result["checked"] >= total or (keepers is not None and not keepers) or not self._active():
                    break
                scanner._last_stable_image = frame
                self._action_position = result["checked"]
                phase = "advance"
                advance_options = {"allow_gym_skip": True} if gym_skip else {}
                if not self._advance_action(snapshot, frame, **advance_options):
                    if self._abort:
                        break
                    raise RuntimeError("Action held: could not advance from confirmed appraisal")
                previous_frame = scanner._last_stable_image
                transition = True
            if (sync is not None and keepers is None and not self._abort
                    and total > 0 and result["checked"] == total):
                sync.complete_uniform_pass(query, target)
        except Exception as exc:
            # Refresh only phases known to have sent no star, after at least
            # one confirmed removal. Post-input uncertainty and persistence
            # failure remain hard holds with completed changes preserved.
            can_refresh = (nonfavorite and not dry_run and result[count_key] > 0
                           and (isinstance(exc, _RefreshFavorites)
                                or (isinstance(exc, RuntimeError) and phase in ("read", "advance"))
                                or (isinstance(exc, RuntimeError) and phase == "star"
                                    and not self._star_input_sent)))
            if can_refresh:
                result["refresh_needed"] = True
                log.info("Discarding changed nonfavorite carousel: %s", exc)
            else:
                result["error"] = str(exc)
                log.exception("Mass action held")
                if self.on_error:
                    self.on_error(str(exc))
        finally:
            self._current_flags = None
        result["aborted"] = self._abort
        if keepers is None:
            self._add_sync_result(result)
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
