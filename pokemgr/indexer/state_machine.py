"""Indexing state machine — scans Pokemon storage via appraisal screen.

FLOW (simple):
  - Precondition: already on the appraisal screen (user or navigator got us here)
  - Loop: read stats → swipe left → read stats → swipe left → ...
  - Stop on: wrap-around, max count, or too many consecutive failures
"""

import time
import logging
import uuid
import random
from typing import Callable

from ..adb.controller import ADBController
from ..calibration.profile import CalibrationProfile
from ..reader.screen import ScreenReader, PokemonRead
from ..data.database import PokemonDatabase
from ..config import (
    human_delay,
    maybe_micro_break,
    MIN_BATTERY_LEVEL,
    BATTERY_CHECK_INTERVAL,
    DELAY_AFTER_SWIPE,
    MICRO_BREAK_INTERVAL,
)

log = logging.getLogger(__name__)


class IndexingStateMachine:
    """Read → swipe → read → swipe loop. Must start on appraisal screen."""

    def __init__(self, adb: ADBController, profile: CalibrationProfile,
                 db: PokemonDatabase):
        self.adb = adb
        self.reader = ScreenReader(profile)
        self.reader._save_screenshots = False
        self.regions = profile.regions
        self.db = db

        from ..adb.navigator import GameNavigator
        self.nav = GameNavigator(adb, profile.regions)

        self.session_id = str(uuid.uuid4())
        self.count = 0
        self.consecutive_failures = 0
        self.max_consecutive_failures = 5
        self.max_count = 0              # 0 = unlimited
        self._start_time = 0.0

        self._first_pokemon_key: tuple | None = None
        self._last_pokemon_key: tuple | None = None
        self._same_count = 0            # how many times the same Pokemon appeared in a row
        self._paused = False
        self._abort = False
        self.unfavorite_all = False
        self._next_break_at = 0
        self.skipped_count = 0
        self.skip_first_n = 0          # skip this many Pokemon before scanning (for resume)
        self.skip_delay = 0.05         # delay between swipes when skipping
        self.resume_target_species = "" # optional: verify we landed on this species
        self.resume_target_cp = 0      # optional: verify we landed on this CP
        self.capture_size_tags = False  # close appraisal briefly to read size labels

        # Callbacks
        self.on_progress: Callable[[int, PokemonRead], None] | None = None
        self.on_error: Callable[[str], None] | None = None
        self.on_finished: Callable[[int], None] | None = None
        self.on_paused: Callable[[], None] | None = None

    def start(self, expected_total: int | None = None):
        """Start scanning. MUST already be on appraisal screen."""
        # Pre-load PaddleOCR so first Pokemon doesn't have to wait
        try:
            from ..reader.ocr_engine import _get_paddle
            _get_paddle()
        except Exception:
            pass
        log.info("Starting scan session: %s", self.session_id)
        self.db.create_session(self.session_id,
                               self.adb.get_device_info().fingerprint)
        self._start_time = time.time()
        self._next_break_at = random.randint(*MICRO_BREAK_INTERVAL)

        # Resume: fast-swipe past already scanned Pokemon
        if self.skip_first_n > 0:
            log.info("Resuming: fast-swiping past %d already-scanned Pokemon...", self.skip_first_n)
            if self.on_error:
                self.on_error(f"Resuming: swiping past {self.skip_first_n} Pokemon...")
            for i in range(self.skip_first_n):
                if self._abort:
                    break
                self._fast_swipe()
                time.sleep(self.skip_delay)
                if (i + 1) % 100 == 0:
                    log.info("Skipped %d/%d...", i + 1, self.skip_first_n)
                    if self.on_error:
                        self.on_error(f"Skipping: {i+1}/{self.skip_first_n}")
            log.info("Resume skip done, verifying position...")
            time.sleep(0.5)  # settle after fast swiping

            # Verify we landed on the right Pokemon (optional)
            if self.resume_target_species or self.resume_target_cp > 0:
                verify_img = self._fast_screencap()
                verify_detail = self.reader.read_detail_screen(verify_img)
                v_species = verify_detail.get("species", "")
                v_cp = verify_detail.get("cp", -1)
                species_ok = (not self.resume_target_species or
                              self.resume_target_species.lower()[:4] in v_species.lower())
                cp_ok = (self.resume_target_cp <= 0 or v_cp == self.resume_target_cp)

                if species_ok and cp_ok:
                    log.info("Resume verified: %s CP%d ✓", v_species, v_cp)
                else:
                    log.warning("Resume MISMATCH: expected %s CP%d, got %s CP%d — PAUSED",
                                self.resume_target_species, self.resume_target_cp, v_species, v_cp)
                    if self.on_error:
                        self.on_error(
                            f"Resume mismatch: expected {self.resume_target_species} CP{self.resume_target_cp}, "
                            f"got {v_species} CP{v_cp} — manually find the right Pokemon then click Resume"
                        )
                    self._paused = True
                    if self.on_paused:
                        self.on_paused()
                    while self._paused and not self._abort:
                        time.sleep(0.5)

        while not self._abort:
            if self._paused:
                time.sleep(0.5)
                continue

            # Anti-detection
            self._next_break_at = maybe_micro_break(self.count, self._next_break_at)

            # Battery
            if self.count > 0 and self.count % BATTERY_CHECK_INTERVAL == 0:
                self._check_battery()

            # ── READ: capture current appraisal screen ──
            # Fast path: take one screenshot, check bars immediately
            from ..config import BAR_WAIT_MAX
            img = self._fast_screencap()
            if not self.reader.are_bars_visible(img):
                # Bars not ready — wait and retry
                img = self._wait_for_bars(max_wait=BAR_WAIT_MAX)
            if img is None:
                self.consecutive_failures += 1
                log.warning("Bars not visible (failure %d/%d)",
                            self.consecutive_failures, self.max_consecutive_failures)

                # After 3 failures, try to recover by re-opening appraisal
                if self.consecutive_failures == 3:
                    log.info("Attempting to re-open appraisal...")
                    try:
                        self._reopen_appraisal()
                    except Exception:
                        pass
                    continue

                if self.consecutive_failures >= self.max_consecutive_failures:
                    log.error("Stopping: %d consecutive failures — lost appraisal screen",
                              self.consecutive_failures)
                    if self.on_error:
                        self.on_error(f"Stopped: lost appraisal screen after {self.consecutive_failures} failures")
                    break

                # Swipe and hope next one works
                self._fast_swipe()
                human_delay(*DELAY_AFTER_SWIPE)
                continue

            # Read stats — HP, gym check, and caught species run in parallel thread
            import threading
            from ..reader.ocr import is_in_gym, read_caught_species
            hp_result = [-1]
            gym_result = [False]
            caught_species_result = [""]
            def _read_hp_gym_species():
                w, h = self.regions.screen_width, self.regions.screen_height
                gym_result[0] = is_in_gym(img, w, h)
                if not gym_result[0]:
                    hp_result[0] = self.reader.read_hp(img)
                caught_species_result[0] = read_caught_species(img, w, h)
            hp_thread = threading.Thread(target=_read_hp_gym_species)
            hp_thread.start()

            detail = self.reader.read_detail_screen(img)
            appraisal = self.reader.read_appraisal_screen(img)

            hp_thread.join(timeout=15)  # PaddleOCR first load can take 10s+
            detail["hp"] = hp_result[0]
            detail["in_gym"] = gym_result[0]
            detail["caught_species"] = caught_species_result[0]

            # Use caught species as the authoritative name if available
            if caught_species_result[0]:
                log.info("Caught species from bubble: %s (OCR name: %s)",
                         caught_species_result[0], detail.get("species", "?"))
                detail["species"] = caught_species_result[0]

            log.info("Raw read: %s CP%s %d/%d/%d (HP:%s)",
                     detail.get("species", "?"), detail.get("cp", -1),
                     appraisal.get("atk", -1), appraisal.get("def_", -1), appraisal.get("sta", -1),
                     detail.get("hp", -1))

            # If CP-1 or stale IVs, the screenshot was bad — wait and re-read EVERYTHING
            needs_reread = False
            if detail.get("cp", -1) <= 0:
                log.warning("CP failed (-1) — screenshot was mid-transition, re-reading")
                needs_reread = True
            elif hasattr(self, '_prev_ivs'):
                current_ivs = (appraisal.get("atk", -1), appraisal.get("def_", -1), appraisal.get("sta", -1))
                if current_ivs == self._prev_ivs and current_ivs != (0, 0, 0):
                    log.warning("IVs same as previous (%s) — screen not updated, re-reading", current_ivs)
                    needs_reread = True

            if needs_reread:
                time.sleep(0.5)
                img = self._fast_screencap()
                detail = self.reader.read_detail_screen(img)
                appraisal = self.reader.read_appraisal_screen(img)
                # Read HP again too
                detail["hp"] = self.reader.read_hp(img)
                log.info("Re-read: %s CP%s %d/%d/%d (HP:%s)",
                         detail.get("species", "?"), detail.get("cp", -1),
                         appraisal.get("atk", -1), appraisal.get("def_", -1), appraisal.get("sta", -1),
                         detail.get("hp", -1))

            # Retry once if bars read all zero
            if appraisal["atk"] == 0 and appraisal["def_"] == 0 and appraisal["sta"] == 0:
                log.info("All-zero IVs — retrying")
                time.sleep(0.2)
                img = self._fast_screencap()
                appraisal = self.reader.read_appraisal_screen(img)
                detail = self.reader.read_detail_screen(img)
                log.info("Retry read: %s CP%s %d/%d/%d",
                         detail.get("species", "?"), detail.get("cp", -1),
                         appraisal.get("atk", -1), appraisal.get("def_", -1), appraisal.get("sta", -1))

            # Name retry: if empty or very short (< 3 chars), likely transition frame
            if not detail.get("species") or len(detail["species"]) < 3:
                retry_img = self._fast_screencap()
                retry_detail = self.reader.read_detail_screen(retry_img)
                if len(retry_detail.get("species", "")) > len(detail.get("species", "")):
                    detail["species"] = retry_detail["species"]
                    # Also grab CP from the retry if it was better
                    if retry_detail["cp"] > 0 and detail["cp"] <= 0:
                        detail["cp"] = retry_detail["cp"]

            # ── VALIDATION FLOW (fingerprint method) ──
            # 1. IVs + HP → find species + level + expected CP (fingerprint)
            # 2. If expected CP matches OCR CP → done
            # 3. If not → re-read appraisal (IVs/HP may have been wrong)
            # 4. If IVs/HP confirmed → CP is wrong → detail screen retry
            from ..reader.ocr import read_cp
            from ..pvp.fingerprint import fingerprint
            cp_valid = False

            species = detail.get("species", "")
            display_name = species  # save original screen name before correction
            detail["display_name"] = display_name
            cp = detail.get("cp", -1)
            hp = detail.get("hp", -1)
            atk_iv = appraisal.get("atk", -1)
            def_iv = appraisal.get("def_", -1)
            sta_iv = appraisal.get("sta", -1)

            # Step 1: Fingerprint — IVs + HP identify the Pokemon
            # Gym Pokemon: no HP, but validate CP against IVs + species name
            if detail.get("in_gym", False):
                # Gym Pokemon: CP is drained and useless. Favorite and skip.
                log.info("In gym — favoriting and skipping: %s", species)
                from ..reader.icons import is_favorited as _is_fav
                fav_img = self._fast_screencap()
                if not _is_fav(fav_img, self.regions.favorite_star_region):
                    self.adb.tap(*self.regions.favorite_star_region.center, jitter=3)
                    human_delay(0.2, 0.1)
                self.skipped_count += 1
                if self.on_error:
                    self.on_error(f"Skipped (gym): {species}")
                self._fast_swipe()
                human_delay(*DELAY_AFTER_SWIPE)
                continue

            # HP = -1 means text failed — retry HP read
            if not cp_valid and hp <= 0:
                log.info("HP text unreadable — retrying")
                time.sleep(0.2)
                retry_img = self._fast_screencap()
                hp = self.reader.read_hp(retry_img)
                detail["hp"] = hp
                if hp == -1:
                    # Still can't read — try one more time
                    time.sleep(0.3)
                    retry_img2 = self._fast_screencap()
                    hp = self.reader.read_hp(retry_img2)
                    detail["hp"] = hp

            fp = None
            if not cp_valid and hp > 0:
                fp = fingerprint(atk_iv, def_iv, sta_iv, hp, cp_hint=cp, name_hint=species)
            elif not cp_valid and hp <= 0:
                if cp > 0 and species and len(species) >= 3 and atk_iv >= 0:
                    from ..pvp.cp_validator import check_pokemon_cp
                    nohp_check = check_pokemon_cp(species, cp, atk_iv, def_iv, sta_iv)
                    if nohp_check["status"] == "valid":
                        log.warning("No HP — CP validates against name: %s CP%d %d/%d/%d → L%s",
                                    species, cp, atk_iv, def_iv, sta_iv, nohp_check.get("level", "?"))
                        cp_valid = True
                else:
                    log.warning("No HP and bad data — skipping: %s CP%d", species, cp)
                    from ..reader.icons import is_favorited as _is_fav
                    fav_img = self._fast_screencap()
                    if not _is_fav(fav_img, self.regions.favorite_star_region):
                        star_center = self.regions.favorite_star_region.center
                        self.adb.tap(*star_center, jitter=3)
                        human_delay(0.2, 0.1)
                    self.skipped_count += 1
                    if self.on_error:
                        self.on_error(f"Skipped (no HP, bad data): {species} CP{cp}")
                    self._fast_swipe()
                    human_delay(*DELAY_AFTER_SWIPE)
                    continue
            if fp:
                from ..config import USE_CALCULATED_CP
                detail["species"] = fp["species"]

                if fp["cp_match"]:
                    cp_valid = True
                    log.info("Fingerprint match: %s CP%d %d/%d/%d HP%d → L%s",
                             fp["species"], cp, atk_iv, def_iv, sta_iv, hp, fp["level"])
                elif USE_CALCULATED_CP:
                    # Calc CP mode: trust IVs+HP+species, use calculated CP
                    detail["cp"] = fp["expected_cp"]
                    cp = fp["expected_cp"]
                    cp_valid = True
                    log.info("Calc CP: %s CP%d (calculated from IVs+HP) %d/%d/%d HP%d → L%s",
                             fp["species"], cp, atk_iv, def_iv, sta_iv, hp, fp["level"])
                else:
                    log.warning("Fingerprint: %s (expected CP%d, got CP%d) %d/%d/%d HP%d",
                                fp["species"], fp["expected_cp"], cp, atk_iv, def_iv, sta_iv, hp)
                    detail["expected_cp"] = fp["expected_cp"]
            else:
                log.warning("No fingerprint match: CP%d %d/%d/%d HP%d name='%s'",
                            cp, atk_iv, def_iv, sta_iv, hp, species)

            # Step 2: Failed — re-read appraisal (IVs or HP might have been wrong)
            if not cp_valid:
                log.info("Fingerprint failed — re-reading appraisal")
                time.sleep(0.3)
                retry_img = self._fast_screencap()
                r_appraisal = self.reader.read_appraisal_screen(retry_img)
                r_detail = self.reader.read_detail_screen(retry_img)
                r_hp = self.reader.read_hp(retry_img)

                r_atk = r_appraisal.get("atk", atk_iv)
                r_def = r_appraisal.get("def_", def_iv)
                r_sta = r_appraisal.get("sta", sta_iv)
                r_cp = r_detail.get("cp", cp)
                if r_hp <= 0:
                    r_hp = hp
                if r_cp > 0:
                    cp = r_cp
                    detail["cp"] = cp

                log.info("Re-read: CP%d %d/%d/%d HP%d", cp, r_atk, r_def, r_sta, r_hp)

                # Fingerprint with re-read data
                fp2 = fingerprint(r_atk, r_def, r_sta, r_hp, cp_hint=cp, name_hint=species)
                if fp2:
                    if fp2["cp_match"]:
                        appraisal["atk"] = r_atk
                        appraisal["def_"] = r_def
                        appraisal["sta"] = r_sta
                        detail["hp"] = r_hp
                        detail["species"] = fp2["species"]
                        cp_valid = True
                        log.info("Fingerprint on re-read: %s CP%d → L%s",
                                 fp2["species"], cp, fp2["level"])
                    else:
                        # IVs+HP identified Pokemon but CP wrong
                        detail["species"] = fp2["species"]
                        detail["expected_cp"] = fp2["expected_cp"]
                        appraisal["atk"] = r_atk
                        appraisal["def_"] = r_def
                        appraisal["sta"] = r_sta
                        detail["hp"] = r_hp
                        log.info("Fingerprint: %s expected CP%d (got CP%d) — CP is wrong",
                                 fp2["species"], fp2["expected_cp"], cp)

            # Step 3: Still invalid — close appraisal, read CP from detail screen
            # We know the expected CP from the fingerprint
            expected_cp = detail.get("expected_cp", -1)
            if not cp_valid:
                if expected_cp > 0:
                    log.info("Looking for CP%d on detail screen (fingerprint: %s)",
                             expected_cp, detail.get("species", "?"))
                else:
                    log.info("Closing appraisal to read CP from detail screen")
                w = self.regions.screen_width
                h = self.regions.screen_height

                self.adb.tap(*self.nav._s(484, 2260), jitter=3)  # X button
                time.sleep(0.5)

                import numpy as np
                cur_atk = appraisal.get("atk", atk_iv)
                cur_def = appraisal.get("def_", def_iv)
                cur_sta = appraisal.get("sta", sta_iv)
                cur_hp = detail.get("hp", hp)

                # Re-read HP from detail screen (more reliable than appraisal)
                detail_img = self._fast_screencap()
                new_hp = self.reader.read_hp(detail_img)
                if new_hp > 0:
                    log.info("HP re-read from detail screen: %d (was %d)", new_hp, cur_hp)
                    cur_hp = new_hp
                    detail["hp"] = new_hp

                for retry in range(20):
                    if self._abort:
                        break

                    # Tap/rotate to trigger animation
                    if retry % 2 == 0:
                        self.adb.tap(w // 2, int(h * 0.25), jitter=20)
                    else:
                        self.adb.swipe(w // 3, int(h * 0.25), 2 * w // 3, int(h * 0.25), 150, jitter=10)
                    # Wait 0.5s — the animation midpoint where Pokemon has moved away from CP
                    time.sleep(0.5)

                    detail_img = self._fast_screencap()

                    # Tesseract
                    new_cp, _ = read_cp(detail_img, self.regions.cp_region)
                    if new_cp > 0:
                        # Accept if it matches expected CP from fingerprint
                        if expected_cp > 0 and new_cp == expected_cp:
                            detail["cp"] = new_cp
                            cp_valid = True
                            log.info("CP matches fingerprint (Tesseract, retry %d): %d",
                                     retry + 1, new_cp)
                            break
                        # Or validate via formula
                        fp3 = fingerprint(cur_atk, cur_def, cur_sta, cur_hp,
                                          cp_hint=new_cp, name_hint=detail.get("species", ""))
                        if fp3 and fp3["cp_match"]:
                            detail["cp"] = new_cp
                            detail["species"] = fp3["species"]
                            cp_valid = True
                            log.info("CP from detail (Tesseract, retry %d): %d → %s L%s",
                                     retry + 1, new_cp, fp3["species"], fp3["level"])
                            break

                    # PaddleOCR
                    try:
                        from ..reader.ocr_engine import read_number_from_crop
                        import cv2
                        cp_crop = np.array(detail_img.crop(self.regions.cp_region.as_tuple()))
                        cp_crop = cv2.resize(cp_crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
                        paddle_cp = read_number_from_crop(cp_crop, use_easyocr_fallback=True)
                        if paddle_cp > 0 and paddle_cp != new_cp:
                            if expected_cp > 0 and paddle_cp == expected_cp:
                                detail["cp"] = paddle_cp
                                cp_valid = True
                                log.info("CP matches fingerprint (PaddleOCR, retry %d): %d",
                                         retry + 1, paddle_cp)
                                break
                            fp4 = fingerprint(cur_atk, cur_def, cur_sta, cur_hp,
                                              cp_hint=paddle_cp, name_hint=detail.get("species", ""))
                            if fp4 and fp4["cp_match"]:
                                detail["cp"] = paddle_cp
                                detail["species"] = fp4["species"]
                                cp_valid = True
                                log.info("CP from detail (PaddleOCR, retry %d): %d → %s L%s",
                                         retry + 1, paddle_cp, fp4["species"], fp4["level"])
                                break
                    except Exception:
                        pass

                if not cp_valid:
                    log.warning("CP not validated after 20 detail retries: %s CP%d",
                                detail.get("species", "?"), detail.get("cp", -1))

                # Re-open appraisal so swipe flow continues
                self._reopen_appraisal()

                if not cp_valid:
                    log.warning("CP not validated: %s CP%d %d/%d/%d — skipping (favoriting to protect)",
                                detail.get("species", "?"), detail.get("cp", -1),
                                appraisal.get("atk", -1), appraisal.get("def_", -1), appraisal.get("sta", -1))
                    # Favorite this Pokemon to protect it from accidental transfer
                    from ..reader.icons import is_favorited
                    fav_img = self._fast_screencap()
                    if not is_favorited(fav_img, self.regions.favorite_star_region):
                        star_center = self.regions.favorite_star_region.center
                        self.adb.tap(*star_center, jitter=3)
                        human_delay(0.2, 0.1)
                        log.info("Favorited unreadable Pokemon for protection")
                    # Skip storing — don't put bad data in DB
                    self.skipped_count += 1
                    if self.on_error:
                        self.on_error(f"Skipped #{self.skipped_count}: {detail.get('species', '?')} CP{detail.get('cp', -1)} — favorited for protection")
                    self._fast_swipe()
                    human_delay(*DELAY_AFTER_SWIPE)
                    continue

            # Capture size tags (optional — closes appraisal briefly)
            from ..config import CAPTURE_SIZE_TAGS
            if self.capture_size_tags or CAPTURE_SIZE_TAGS:
                from ..reader.ocr import read_size_label
                # Close appraisal to reveal weight/height labels
                self.adb.tap(*self.nav._s(484, 2260), jitter=3)  # X button
                time.sleep(0.5)
                detail_img = self._fast_screencap()
                wt = read_size_label(detail_img, self.regions.weight_label_region)
                ht = read_size_label(detail_img, self.regions.height_label_region)
                if wt:
                    detail["weight_tag"] = wt
                    log.info("Size tag: %s", wt)
                if ht:
                    detail["height_tag"] = ht
                    log.info("Size tag: %s", ht)
                # Re-open appraisal
                self._reopen_appraisal()

            # Unfavorite while still on this Pokemon
            if self.unfavorite_all and detail.get("favorited", False):
                star_center = self.regions.favorite_star_region.center
                self.adb.tap(*star_center)
                human_delay(0.15, 0.1)

            # Store (only valid reads get here)
            self._prev_ivs = (appraisal.get("atk", -1), appraisal.get("def_", -1), appraisal.get("sta", -1))
            self._store_pokemon(detail, appraisal)
            self.consecutive_failures = 0

            # Detect end of list: same Pokemon appearing repeatedly
            current_key = (detail.get("species", ""), detail.get("cp", 0),
                           appraisal.get("atk", -1), appraisal.get("def_", -1), appraisal.get("sta", -1))
            if current_key == self._last_pokemon_key:
                self._same_count += 1
                if self._same_count >= 3:
                    log.info("End of list: same Pokemon %s appeared %d times in a row — removing duplicates",
                             current_key, self._same_count + 1)
                    # Remove the duplicate entries we just added
                    self._remove_last_n(self._same_count)
                    self.count -= self._same_count
                    break
            else:
                self._same_count = 0
                self._last_pokemon_key = current_key

            # Check limits
            if self.max_count > 0 and self.count >= self.max_count:
                log.info("Reached max count %d", self.max_count)
                break

            if self.count > 3 and self._is_wraparound():
                log.info("Wrap-around detected — scan complete")
                break

            # ── SWIPE to next Pokemon's appraisal ──
            self._fast_swipe()
            human_delay(*DELAY_AFTER_SWIPE)

        # Done
        self.db.complete_session(self.session_id, self.count)
        elapsed = time.time() - self._start_time
        rate = self.count / elapsed if elapsed > 0 else 0
        log.info("Done: %d Pokemon in %.0fs (%.1f/min, %.1fs each)",
                 self.count, elapsed, rate * 60, 1 / rate if rate else 0)
        if self.on_finished:
            self.on_finished(self.count)

    # ── Helpers ───────────────────────────────────────────────────────

    def _wait_for_bars(self, max_wait: float = 3.0):
        """Poll until bars are visible. Returns image or None."""
        deadline = time.time() + max_wait
        while time.time() < deadline:
            img = self._fast_screencap()
            if self.reader.are_bars_visible(img):
                return img
            time.sleep(0.3)
        return None

    def _store_pokemon(self, detail: dict, appraisal: dict):
        pokemon = PokemonRead(
            species=detail["species"],
            display_name=detail.get("display_name", detail["species"]),
            cp=detail["cp"],
            atk=appraisal["atk"],
            def_=appraisal["def_"],
            sta=appraisal["sta"],
            shiny=detail["shiny"],
            shadow=detail["shadow"],
            favorited=detail["favorited"],
            lucky=detail["lucky"],
            gender=detail.get("gender", "none"),
            weight_tag=detail.get("weight_tag", ""),
            height_tag=detail.get("height_tag", ""),
            is_dynamax=detail.get("is_dynamax", False),
            hp=detail.get("hp", -1),
            confidence=(detail["confidence"] + appraisal["confidence"]) / 2,
        )

        self.db.insert_pokemon(pokemon, self.session_id, self.count)
        self.count += 1

        if self.count == 1:
            self._first_pokemon_key = (pokemon.species, pokemon.cp, pokemon.atk, pokemon.def_, pokemon.sta)

        if self.on_progress:
            self.on_progress(self.count, pokemon)

        elapsed = time.time() - self._start_time
        rate = self.count / elapsed if elapsed > 0 else 0
        log.info("#%d %s (%.1f/min)", self.count, pokemon.summary(), rate * 60)

    def _is_wraparound(self) -> bool:
        if not self._first_pokemon_key:
            return False
        all_p = self.db.get_all(self.session_id)
        # Need at least 20 Pokemon before checking (avoid false positives from duplicates)
        if len(all_p) < 20:
            return False
        last = all_p[-1]
        last_key = (last.species, last.cp, last.atk, last.def_, last.sta)
        if last_key == self._first_pokemon_key:
            # Double check: also verify the second Pokemon matches
            if len(all_p) >= 2:
                # The first pokemon key should also not have CP=-1 (failed read)
                if self._first_pokemon_key[1] == -1:
                    return False  # can't trust wrap-around with failed CP
            log.info("Wrap-around: last=%s matches first=%s", last_key, self._first_pokemon_key)
            return True
        return False

    def _remove_last_n(self, n: int):
        """Remove the last N entries from the database (duplicate cleanup)."""
        all_p = self.db.get_all(self.session_id)
        if len(all_p) >= n:
            for p in all_p[-n:]:
                self.db.conn.execute("DELETE FROM pokemon WHERE id = ?", (p.id,))
            self.db.conn.commit()
            log.info("Removed %d duplicate entries", n)

    def _reopen_appraisal(self):
        """Recovery: try to open appraisal from detail screen.
        Only used as last resort when normal swipe flow breaks."""
        log.info("Recovery: opening appraisal via menu")
        self.adb.tap(*self.regions.menu_button)
        human_delay(0.8, 0.15)
        self.adb.tap(*self.regions.appraise_menu_item)
        human_delay(1.0, 0.2)
        self.adb.tap(*self.regions.dismiss_professor)
        human_delay(1.5, 0.3)

    def _fast_screencap(self):
        return self.adb.screencap()

    def _fast_swipe(self):
        from ..config import DEFAULT_SWIPE_DURATION_MS
        self.adb.swipe(
            *self.regions.swipe_start,
            *self.regions.swipe_end,
            DEFAULT_SWIPE_DURATION_MS,
        )

    def _check_battery(self):
        level = self.adb.get_battery_level()
        if 0 <= level < MIN_BATTERY_LEVEL:
            self._paused = True
            if self.on_error:
                self.on_error(f"Battery low ({level}%). Plug in and click Resume.")

    def pause(self):
        self._paused = True
        log.info("Paused at #%d", self.count)

    def resume(self):
        self._paused = False
        log.info("Resumed")

    def abort(self):
        self._abort = True
        log.info("Abort requested")
