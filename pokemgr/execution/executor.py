"""Execution module — favorite all KEEP Pokemon.

Approach:
  1. Search '!favorite' to show all unfavorited Pokemon
  2. Tap first → open appraisal (same as scanning)
  3. Read species, CP, IVs from appraisal screen
  4. Match against keeper list using ALL fields: species + CP + ATK + DEF + STA
  5. If exact match → tap star to favorite
  6. Swipe left → repeat

This is essentially a second scan pass, but instead of storing data
we compare against the DB and favorite matches.
"""

import logging
import time
import json
from pathlib import Path
from typing import Callable
from collections import defaultdict

from ..adb.controller import ADBController
from ..adb.navigator import GameNavigator
from ..calibration.profile import CalibrationProfile
from ..reader.screen import ScreenReader
from ..reader.ocr import read_cp, read_caught_species
from ..reader.icons import is_favorited
from ..data.database import PokemonDatabase
from ..data.models import Pokemon
from ..config import human_delay, DELAY_AFTER_SWIPE, LOGS_DIR

log = logging.getLogger(__name__)


class Executor:
    """Favorites KEEP Pokemon by opening appraisal and matching full stats."""

    def __init__(self, adb: ADBController, profile: CalibrationProfile,
                 db: PokemonDatabase):
        self.adb = adb
        self.profile = profile
        self.nav = GameNavigator(adb, profile.regions)
        self.reader = ScreenReader(profile)
        self.regions = profile.regions
        self.db = db

        self.on_progress: Callable | None = None
        self.on_error: Callable | None = None
        self._abort = False
        self._paused = False

    ALL_FAV_PASSES = [
        ("Normal", "!favorite&!shiny&!shadow&!dynamax&!gigantamax"),
        ("Shiny", "!favorite&shiny"),
        ("Shadow", "!favorite&shadow"),
        ("Dynamax", "!favorite&dynamax"),
        ("Gigantamax", "!favorite&gigantamax"),
    ]

    def favorite_keepers(self, dry_run: bool = False,
                         selected_passes: list[str] | None = None) -> dict:
        """Favorite all KEEP Pokemon using multi-pass search filters.

        Args:
            dry_run: if True, don't actually tap the star
            selected_passes: list of pass names to run, e.g. ["Shiny", "Shadow"].
                           If None, runs all passes.
        """
        all_pokemon = self.db.get_all()
        keepers = [p for p in all_pokemon if p.decision == "KEEP"]
        need_fav = [p for p in keepers if not p.favorited]

        if not need_fav:
            log.info("All keepers already favorited")
            return {"favorited": 0, "checked": 0}

        # Build keeper lookup: species + IVs + HP (CP is unreliable from OCR)
        keeper_set = set()
        for p in need_fav:
            keeper_set.add((p.species.lower(), p.atk, p.def_, p.sta, p.hp))

        log.info("Need to favorite %d keepers (dry_run=%s)", len(need_fav), dry_run)
        self._write_log(all_pokemon)

        # Filter passes if selected
        fav_passes = self.ALL_FAV_PASSES
        if selected_passes:
            fav_passes = [(n, q) for n, q in fav_passes if n in selected_passes]
            log.info("Running selected passes: %s", [n for n, _ in fav_passes])

        total_favorited = 0
        total_checked = 0

        for pass_name, search_query in fav_passes:
            if self._abort:
                break
            if not keeper_set:
                log.info("All keepers favorited — done")
                break

            log.info("=== Favorite pass: %s (%s) ===", pass_name, search_query)
            if self.on_progress:
                self.on_progress(total_favorited, len(need_fav),
                                 f"Pass: {pass_name}")

            result = self._run_favorite_pass(
                search_query, keeper_set, dry_run, len(need_fav)
            )
            total_favorited += result["favorited"]
            total_checked += result["checked"]

        result = {"favorited": total_favorited, "checked": total_checked, "dry_run": dry_run}
        log.info("All favorite passes complete: %s", result)
        return result

    def _run_favorite_pass(self, search_query: str, keeper_set: set,
                           dry_run: bool, total_keepers: int) -> dict:
        """Run one favorite pass: navigate, search, swipe through, favorite matches."""

        # Navigate to storage
        if not self.nav.navigate_to_storage():
            raise Exception("Cannot get to Pokemon storage")

        # Enter search filter
        self.nav.enter_search(search_query)
        time.sleep(1)

        pass_total = self.nav.read_filtered_count()
        log.info("Filter '%s' matched %d Pokemon", search_query, pass_total)

        if pass_total == 0:
            # Could be OCR fail or truly empty — try anyway
            pass

        # Tap first and open appraisal
        self.nav.tap_first_pokemon()
        self.nav.open_first_appraisal()

        favorited = 0
        checked = 0
        last_key = None
        same_count = 0

        while not self._abort:
            # Pause
            while self._paused and not self._abort:
                time.sleep(0.5)

            # Read appraisal
            img = self._wait_for_bars(max_wait=2.0)
            if img is None:
                same_count += 1
                if same_count >= 3:
                    log.info("End of pass — no bars found")
                    break
                self.adb.swipe(*self.regions.swipe_start, *self.regions.swipe_end,
                               self.regions.swipe_duration_ms)
                human_delay(*DELAY_AFTER_SWIPE)
                continue

            if self._abort:
                break

            # Read only what we need: species (bubble), IVs (bars), HP, fav status
            import threading
            appraisal = self.reader.read_appraisal_screen(img)

            hp_result = [-1]
            species_result = [""]
            def _read_hp_species():
                w, h = self.regions.screen_width, self.regions.screen_height
                hp_result[0] = self.reader.read_hp(img)
                species_result[0] = read_caught_species(img, w, h)
            bg = threading.Thread(target=_read_hp_species)
            bg.start()

            atk = appraisal.get("atk", -1)
            def_ = appraisal.get("def_", -1)
            sta = appraisal.get("sta", -1)
            already_fav = is_favorited(img, self.regions.favorite_star_region)

            bg.join(timeout=5)
            if self._abort:
                break
            hp = hp_result[0]
            validated_species = species_result[0]

            # Fallback to OCR name if bubble failed
            if not validated_species:
                detail = self.reader.read_detail_screen(img)
                validated_species = detail.get("species", "")

            # HP retry
            if hp <= 0 and not self._abort:
                time.sleep(0.2)
                retry_img = self.adb.screencap()
                hp = self.reader.read_hp(retry_img)

            checked += 1
            log.info("Fav check #%d: %s %d/%d/%d HP%d%s",
                     checked, validated_species, atk, def_, sta, hp,
                     " (already fav)" if already_fav else "")

            # End-of-list detection (ignore HP — it can fail and flip the key)
            current_key = (validated_species, atk, def_, sta)
            if current_key == last_key:
                same_count += 1
                if same_count >= 3:
                    log.info("End of list — same Pokemon %d times", same_count + 1)
                    break
            else:
                same_count = 0
                last_key = current_key

            # Stop if we've exceeded the filtered count (+10% safety margin)
            if pass_total > 0 and checked > pass_total * 1.1 + 5:
                log.info("Exceeded filter count (%d checked, %d expected) — stopping",
                         checked, pass_total)
                break

            # Match against keeper list: species + IVs + HP
            if validated_species and hp > 0:
                match_key = (validated_species.lower(), atk, def_, sta, hp)
                is_keeper = match_key in keeper_set
            else:
                is_keeper = False

            if is_keeper and not already_fav:
                if dry_run:
                    favorited += 1
                    keeper_set.discard(match_key)
                    log.info("DRY RUN — would favorite: %s %d/%d/%d HP%d",
                             validated_species, atk, def_, sta, hp)
                else:
                    star_center = self.regions.favorite_star_region.center
                    self.adb.tap(*star_center, jitter=3)
                    human_delay(0.3, 0.1)

                    img2 = self.adb.screencap()
                    if is_favorited(img2, self.regions.favorite_star_region):
                        favorited += 1
                        keeper_set.discard(match_key)
                        log.info("Favorited: %s %d/%d/%d HP%d",
                                 validated_species, atk, def_, sta, hp)
                    else:
                        log.warning("Star tap failed for %s %d/%d/%d",
                                    validated_species, atk, def_, sta)

            elif is_keeper and already_fav:
                keeper_set.discard(match_key)
                log.info("Already favorited: %s %d/%d/%d HP%d",
                         validated_species, atk, def_, sta, hp)

            # Progress: checked/total so user sees how far through storage we are
            if self.on_progress:
                fav_str = f"fav:{favorited}" if favorited > 0 else ""
                self.on_progress(
                    checked, pass_total if pass_total > 0 else 0,
                    f"#{checked} {validated_species or '?'} {atk}/{def_}/{sta} HP{hp} {fav_str}".strip()
                )

            # Swipe to next
            self.adb.swipe(*self.regions.swipe_start, *self.regions.swipe_end,
                           self.regions.swipe_duration_ms)
            human_delay(*DELAY_AFTER_SWIPE)

        # Back to storage
        self.adb.tap(*self.nav._s(484, 2260), jitter=3)  # X close
        time.sleep(0.5)
        self.adb.key_event(4)
        time.sleep(0.5)

        log.info("Pass done: %d favorited, %d checked", favorited, checked)
        return {"favorited": favorited, "checked": checked}

    def favorite_by_filter(self, search_query: str, label: str = "") -> dict:
        """Favorite ALL Pokemon matching a search filter.

        No keeper matching — just swipe through and star everything unfavorited.
        Uses filter like 'shiny', 'shadow', 'legendary', '4*', etc.
        """
        # Prepend !favorite so we only see unfavorited ones
        full_query = f"!favorite&{search_query}"
        log.info("Favorite by filter: '%s' (%s)", full_query, label)

        if not self.nav.navigate_to_storage():
            raise Exception("Cannot get to Pokemon storage")

        self.nav.enter_search(full_query)
        time.sleep(1)

        pass_total = self.nav.read_filtered_count()
        log.info("Filter '%s' matched %d Pokemon", full_query, pass_total)

        if pass_total == 0:
            return {"favorited": 0, "checked": 0, "label": label}

        self.nav.tap_first_pokemon()
        self.nav.open_first_appraisal()

        favorited = 0
        checked = 0
        last_key = None
        same_count = 0

        while not self._abort:
            while self._paused and not self._abort:
                time.sleep(0.5)

            img = self._wait_for_bars(max_wait=2.0)
            if img is None:
                same_count += 1
                if same_count >= 3:
                    log.info("End of list — no bars")
                    break
                self.adb.swipe(*self.regions.swipe_start, *self.regions.swipe_end,
                               self.regions.swipe_duration_ms)
                human_delay(*DELAY_AFTER_SWIPE)
                continue

            if self._abort:
                break

            # Read species from bubble + IVs for logging/dedup detection
            w, h = self.regions.screen_width, self.regions.screen_height
            species = read_caught_species(img, w, h) or "?"
            appraisal = self.reader.read_appraisal_screen(img)
            atk = appraisal.get("atk", -1)
            def_ = appraisal.get("def_", -1)
            sta = appraisal.get("sta", -1)
            already_fav = is_favorited(img, self.regions.favorite_star_region)

            checked += 1

            # End-of-list detection
            current_key = (species, atk, def_, sta)
            if current_key == last_key:
                same_count += 1
                if same_count >= 3:
                    log.info("End of list — same Pokemon %d times", same_count + 1)
                    break
            else:
                same_count = 0
                last_key = current_key

            # Stop if we've exceeded the filtered count
            if pass_total > 0 and checked > pass_total * 1.1 + 5:
                log.info("Exceeded filter count (%d checked, %d expected) — stopping",
                         checked, pass_total)
                break

            if not already_fav:
                star_center = self.regions.favorite_star_region.center
                self.adb.tap(*star_center, jitter=3)
                human_delay(0.3, 0.1)
                favorited += 1
                log.info("Favorited #%d: %s %d/%d/%d", favorited, species, atk, def_, sta)
            else:
                log.info("Already fav: %s %d/%d/%d", species, atk, def_, sta)

            if self.on_progress:
                fav_str = f"fav:{favorited}" if favorited > 0 else ""
                self.on_progress(
                    checked, pass_total if pass_total > 0 else 0,
                    f"#{checked} {species} {atk}/{def_}/{sta} {fav_str}".strip()
                )

            self.adb.swipe(*self.regions.swipe_start, *self.regions.swipe_end,
                           self.regions.swipe_duration_ms)
            human_delay(*DELAY_AFTER_SWIPE)

        # Close appraisal
        self.adb.tap(*self.nav._s(484, 2260), jitter=3)
        time.sleep(0.5)
        self.adb.key_event(4)
        time.sleep(0.5)

        log.info("Filter fav done: %d favorited, %d checked", favorited, checked)
        return {"favorited": favorited, "checked": checked, "label": label}

    def unfavorite_all(self) -> dict:
        """Unfavorite ALL Pokemon by searching 'favorite' and swiping through detail screens."""
        log.info("Unfavoriting all Pokemon...")

        if not self.nav.navigate_to_storage():
            raise Exception("Cannot get to Pokemon storage")

        self.nav.enter_search("favorite")
        time.sleep(1)

        count_expected = self.nav.read_filtered_count()
        log.info("Found %d favorited Pokemon", count_expected)

        if count_expected == 0:
            return {"unfavorited": 0}

        # Tap first Pokemon — stays on detail screen (no appraisal needed)
        self.nav.tap_first_pokemon()
        time.sleep(1)

        unfavorited = 0
        last_cp = None
        same_count = 0
        max_checks = count_expected + 20 if count_expected > 0 else 5000

        for i in range(max_checks):
            if self._abort:
                break

            img = self.adb.screencap()
            cp, _ = read_cp(img, self.regions.cp_region)
            fav = is_favorited(img, self.regions.favorite_star_region)

            if cp == last_cp:
                same_count += 1
                if same_count >= 4:
                    break
            else:
                same_count = 0
                last_cp = cp

            if fav:
                star_center = self.regions.favorite_star_region.center
                self.adb.tap(*star_center, jitter=3)
                human_delay(0.2, 0.1)
                unfavorited += 1

                if self.on_progress:
                    self.on_progress(unfavorited, count_expected,
                                     f"Unfavorited #{unfavorited} (CP{cp})")

            self.adb.swipe(*self.regions.swipe_start, *self.regions.swipe_end,
                           self.regions.swipe_duration_ms)
            human_delay(*DELAY_AFTER_SWIPE)

        self.adb.key_event(4)
        time.sleep(1)

        result = {"unfavorited": unfavorited}
        log.info("Unfavoriting complete: %s", result)
        return result

    def _wait_for_bars(self, max_wait: float = 2.0):
        """Wait for appraisal bars to appear."""
        deadline = time.time() + max_wait
        while time.time() < deadline and not self._abort:
            img = self.adb.screencap()
            if self.reader.are_bars_visible(img):
                return img
            time.sleep(0.3)
        return None

    def _write_log(self, all_pokemon: list[Pokemon]):
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        path = LOGS_DIR / f"execution_{timestamp}.json"

        data = {"keep": [], "transfer": []}
        for p in all_pokemon:
            entry = {
                "species": p.species, "cp": p.cp,
                "atk": p.atk, "def": p.def_, "sta": p.sta,
                "iv_pct": round(p.iv_pct, 4),
                "decision": p.decision, "reason": p.decision_reason,
            }
            if p.decision == "KEEP":
                data["keep"].append(entry)
            else:
                data["transfer"].append(entry)

        path.write_text(json.dumps(data, indent=2))
        log.info("Execution log saved: %s", path)

    def pause(self):
        self._paused = True
        log.info("Executor paused")

    def resume(self):
        self._paused = False
        log.info("Executor resumed")

    def abort(self):
        self._abort = True
        self._paused = False  # unblock if paused
