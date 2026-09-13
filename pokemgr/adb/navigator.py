# TRACEWEAVER: file-role=storage-count-reader; req=REQ-SCAN-003; trace=TRACE-SCAN-006; ver=VER-SCAN-001
"""Screen-aware navigation through Pokemon Go UI.

Detects the current screen by looking for specific visual markers,
then navigates step by step to the target.
"""

import logging
import time

from PIL import Image
import numpy as np

from .controller import ADBController, ADBError
from ..calibration.regions import ScreenRegions
from ..config import human_delay

log = logging.getLogger(__name__)

# Known coordinates for 968x2376 (Fold6 cover screen)
POKEBALL = (484, 2280)
POKEMON_BTN = (110, 1960)
FIRST_GRID_ITEM = (160, 550)
SEARCH_BAR = (484, 300)


# TRACEWEAVER: entrypoint=map-screen-marker; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
def _has_pokeball(arr: np.ndarray) -> bool:
    """Require the map button's red hemisphere, white base and neutral center."""
    import cv2

    h, w = arr.shape[:2]
    if arr.ndim != 3 or arr.shape[2] < 3 or min(h, w) < 40:
        return False
    # Include the whole button, including the higher tablet position. A narrow
    # center crop would turn a wide Max Moves banner into a ball-shaped strip.
    x_start, x_end = int(w * 0.30), int(w * 0.70)
    y_start, y_end = int(h * 0.82), int(h * 0.97)
    region = arr[y_start:y_end, x_start:x_end, :3]
    red = ((region[:, :, 0] > 200) & (region[:, :, 1] < 120)
           & (region[:, :, 2] < 120)).astype(np.uint8)
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(red, 8)
    for component in range(1, count):
        x, y, width, height, area = stats[component]
        if not (w * 0.04 <= width <= w * 0.18
                and 0.35 <= height / width <= 0.65
                and 0.45 <= area / (width * height) <= 0.85):
            continue
        center_x = x_start + x + width / 2
        bottom_y = y_start + y + height
        if abs(center_x - w / 2) > w * 0.025:
            continue
        # The curved upper edge narrows towards the pole; a red rectangle or
        # ribbon with a similarly sized bounding box is not a hemisphere.
        shape = labels[y:y + height, x:x + width] == component
        band = max(1, height // 4)
        if shape[:band].mean() >= shape[-band:].mean() * 0.85:
            continue

        def patch(dx1, dx2, dy1, dy2):
            x1, x2 = round(center_x + dx1 * width), round(center_x + dx2 * width)
            y1, y2 = round(bottom_y + dy1 * width), round(bottom_y + dy2 * width)
            if x1 < 0 or y1 < 0 or x2 > w or y2 > h or x1 >= x2 or y1 >= y2:
                return None
            return arr[y1:y2, x1:x2, :3]

        lower = patch(-0.30, 0.30, 0.18, 0.34)
        center = patch(-0.06, 0.06, -0.04, 0.06)
        if lower is None or center is None:
            continue
        white = (lower.min(axis=2) > 225) & (np.ptp(lower, axis=2) < 30)
        neutral = ((center.min(axis=2) > 100) & (center.max(axis=2) < 215)
                   & (np.ptp(center, axis=2) < 55))
        if white.mean() > 0.85 and neutral.mean() > 0.75:
            return True
    return False


def _has_storage_header(arr: np.ndarray) -> bool:
    """Check if Pokemon storage header is visible.
    Storage has a WHITE/CREAM background (not blue sky or green map).
    All RGB channels must be high and close to each other (neutral white)."""
    h, w = arr.shape[:2]
    sy = h / 2376
    y1 = int(100 * sy)
    y2 = int(170 * sy)
    region = arr[y1:y2, int(w * 0.3):int(w * 0.7)]
    avg_r = np.mean(region[:, :, 0])
    avg_g = np.mean(region[:, :, 1])
    avg_b = np.mean(region[:, :, 2])
    # White/cream: all channels > 210 and spread < 30
    spread = max(avg_r, avg_g, avg_b) - min(avg_r, avg_g, avg_b)
    is_white = avg_r > 210 and avg_g > 210 and avg_b > 210 and spread < 30
    # The map can also have a near-white sky here.  Storage additionally has
    # the dark/teal POKEMON title and count in the central header.
    dark_ratio = np.mean(np.max(region[:, :, :3], axis=2) < 170)
    return is_white and dark_ratio > 0.01


def _has_hp_bar(arr: np.ndarray, sx: float, sy: float) -> bool:
    """Check if an HP bar is visible (green=full or gray=depleted)."""
    h, w = arr.shape[:2]
    # Scan a broad normalized band.  The HP bar is much lower on Pokemon Go's
    # tablet layout than on the narrow Fold6 cover-screen layout.
    for y in range(int(h * 0.35), int(h * 0.70)):
        row = arr[y, int(w * 0.20):int(w * 0.80), :3]
        # Green HP bar: R~80-140, G>220, B~160-200
        green_mask = (row[:, 0] > 80) & (row[:, 0] < 140) & (row[:, 1] > 220) & (row[:, 2] > 160) & (row[:, 2] < 200)
        if np.sum(green_mask) / len(green_mask) > 0.3:
            return True
        # Gray depleted HP bar: R≈G≈B ≈ 228-235, tight spread
        gray_mask = (row[:, 0] > 225) & (row[:, 0] < 240) & (row[:, 1] > 225) & (row[:, 1] < 240) & (row[:, 2] > 225) & (row[:, 2] < 240)
        gray_spread = np.max(row, axis=1) - np.min(row, axis=1)
        tight_gray = gray_mask & (gray_spread < 8)
        if np.sum(tight_gray) / len(tight_gray) > 0.5:
            return True
    return False


class GameNavigator:
    def __init__(self, adb: ADBController, regions: ScreenRegions,
                 cancelled=None, observation_generation=None):
        self.adb = adb
        self.r = regions
        w, h = regions.screen_width, regions.screen_height
        self.sx = w / 968
        self.sy = h / 2376
        self._cancelled = cancelled or (lambda: False)
        self._observation_generation = observation_generation or (lambda: 0)
        self._virtual_display = isinstance(getattr(adb, "display_id", None), int)
        if self._virtual_display:
            info = adb.get_device_info()
            if (w, h) != (info.width, info.height):
                raise ADBError("Calibration dimensions do not match the selected app stream")

    def is_cancelled(self) -> bool:
        """Return whether the owning scan asked navigation to stop."""
        try:
            return bool(self._cancelled())
        except Exception:
            # A broken UI callback must fail closed before another device input.
            log.exception("Navigation cancellation callback failed")
            return True

    def _s(self, x: int, y: int) -> tuple[int, int]:
        return (int(x * self.sx), int(y * self.sy))

    def clear_touch_protection(self):
        """Dismiss Samsung Game Booster's ADB-blocking touch overlay.

        The package is absent on non-Samsung devices, where force-stop is a
        harmless no-op.  This changes no Pokemon Go or account data.
        """
        if self.is_cancelled():
            return False
        self.adb.shell(
            "am force-stop --user 0 com.samsung.android.game.gametools"
        )
        return not self.is_cancelled()

    def appraisal_close_target(self) -> tuple[int, int]:
        return (
            getattr(self.r, "appraisal_close_x", None)
            or self._s(484, 2260)
        )

    def detect_screen(self, img: Image.Image | None = None) -> str:
        """Detect current screen: 'game_map', 'storage', 'detail', 'appraisal', 'screen_off', or 'other'."""
        if img is None:
            img = self.adb.screencap()
        self._last_screen_image = img
        arr = np.array(img)
        # Samsung screencaps are commonly RGBA.  Alpha is always 255, so
        # averaging all four channels makes a pure-black frame look bright
        # (mean 63.75) and misclassifies it as ``other``.  Screen detection is
        # based only on visible RGB pixels.
        rgb = arr[:, :, :3] if arr.ndim == 3 and arr.shape[2] >= 3 else arr
        h_img, w_img = rgb.shape[:2]
        sy = h_img / 2376
        sx = w_img / 968

        # Check if screen is off (all black)
        if np.mean(rgb) < 10:
            return 'screen_off'

        # Pokeball visible = game map (most reliable, check first)
        if _has_pokeball(rgb):
            return 'game_map'

        # Check appraisal (has IV bars)
        from ..reader.bars import are_bars_present
        if are_bars_present(img):
            return 'appraisal'

        # Check storage (white header area)
        if _has_storage_header(rgb):
            return 'storage'

        # Check detail screen: HP bar visible (green or gray) = detail or appraisal
        # Since appraisal was already checked above, this must be the detail screen
        if _has_hp_bar(rgb, sx, sy):
            return 'detail'

        # Check for exit dialog ("Do you want to exit Pokemon GO?") only
        # after ruling out appraisal/detail.  Dark-background Pokemon such as
        # Zweilous have a dark top plus a white stats card and previously
        # matched this coarse dialog heuristic.
        top_area = rgb[int(200 * sy):int(400 * sy), int(100 * sx):int(868 * sx)]
        top_brightness = np.mean(top_area)
        if top_brightness < 80:
            dialog_center = rgb[int(900 * sy):int(1050 * sy), int(250 * sx):int(718 * sx)]
            center_brightness = np.mean(dialog_center)
            if center_brightness > 200:
                return 'exit_dialog'

        return 'other'

    def navigate_to_appraisal(self, search_query: str | None = None) -> bool:
        """From any screen, navigate to the first Pokemon's appraisal."""

        restarted_unknown = False
        cleared_touch_protection = False

        for attempt in range(8):
            if self.is_cancelled():
                return False
            screen = self.detect_screen()
            log.info("Attempt %d: detected '%s'", attempt + 1, screen)

            if screen == 'appraisal':
                log.info("Already on appraisal — ready")
                return True

            elif screen == 'exit_dialog':
                log.info("Exit dialog — tapping CANCEL")
                w, h = self.r.screen_width, self.r.screen_height
                if self.is_cancelled():
                    return False
                self.adb.tap(w // 2, int(h * 0.52), jitter=3)  # CANCEL button
                human_delay(1.0, 0.2)
                continue

            elif screen == 'screen_off':
                if self._virtual_display:
                    log.error("App stream is black or off; restart the stream before scanning")
                    return False
                if self.adb.is_screen_on():
                    # Samsung Game Booster Touch Protection can leave Pokemon
                    # Go focused and the display awake while screencap is
                    # black.  Clear that overlay before considering a Unity
                    # restart; relaunching Pokemon Go alone does not remove it.
                    if not cleared_touch_protection:
                        log.info("Black game frame while display is awake — clearing Touch Protection")
                        if not self.clear_touch_protection():
                            return False
                        cleared_touch_protection = True
                        time.sleep(0.25)
                    elif not restarted_unknown:
                        log.info("Black game frame while display is awake — restarting Pokemon Go")
                        self.ensure_pokemon_go(restart=True)
                        restarted_unknown = True
                    else:
                        time.sleep(2)
                else:
                    log.info("Screen off — waking up and launching Pokemon Go")
                    if self.is_cancelled():
                        return False
                    self.adb.wake_screen()
                    time.sleep(2)
                    if self.is_cancelled():
                        return False
                    w, h = self.r.screen_width, self.r.screen_height
                    self.adb.swipe(w // 2, int(h * 0.7), w // 2, int(h * 0.3), 300, jitter=0)
                    time.sleep(1)
                    if self.is_cancelled():
                        return False
                    self.ensure_pokemon_go()
                continue

            elif screen == 'game_map':
                log.info("On game map — opening storage...")
                pokeball = self.r.map_pokeball or self._s(*POKEBALL)
                pokemon_button = self.r.map_pokemon_button or self._s(*POKEMON_BTN)
                self.adb.tap(*pokeball, jitter=5)
                human_delay(1.5, 0.2)
                if self.is_cancelled():
                    return False
                self.adb.tap(*pokemon_button, jitter=5)
                human_delay(3.0, 0.5)
                continue

            elif screen == 'storage':
                log.info("In storage — entering search and tapping first Pokemon...")
                if search_query:
                    if not self.enter_search(search_query):
                        return False
                    search_query = None  # don't re-enter on retry
                if not self.tap_first_pokemon():
                    return False
                if not self.open_first_appraisal():
                    return False
                continue

            elif screen == 'detail':
                # A storage animation can briefly resemble a detail card, and
                # callers may also intentionally start on a confirmed detail
                # screen.  Open appraisal from that known surface instead of
                # restarting Pokemon Go.
                if not self.open_first_appraisal():
                    return False
                continue

            else:
                if self._virtual_display:
                    log.error("Unknown app-stream screen; navigation stopped")
                    return False
                # Never apply coordinate taps to an unknown surface.  Restart
                # once to recover a stuck Unity/Game Booster frame, then allow
                # later attempts time to settle without restarting in a loop.
                if not restarted_unknown:
                    log.info("Unknown screen — restarting Pokemon Go safely")
                    self.ensure_pokemon_go(restart=True)
                    restarted_unknown = True
                else:
                    time.sleep(2)
                continue

        log.error("Failed to reach appraisal after 8 attempts")
        return False

    def _save_storage_navigation_failure(self, reason, screen, attempt, pending_departure):
        """Preserve the final observed pixels without requesting another frame."""
        image = getattr(self, "_last_screen_image", None)
        if not isinstance(image, Image.Image):
            return
        import json
        from ..config import CACHE_DIR

        try:
            destination = CACHE_DIR / "navigation_failures"
            destination.mkdir(parents=True, exist_ok=True)
            prefix = destination / f"storage_{time.time_ns()}"
            image.save(prefix.with_suffix(".png"))
            prefix.with_suffix(".json").write_text(json.dumps({
                "reason": reason, "screen": screen, "attempt": attempt,
                "pending_departure": pending_departure,
                "image_size": list(image.size),
                "appraisal_close_target": self.appraisal_close_target(),
                "capture": {key: image.info[key] for key in (
                    "pokemgr_capture_started_at", "pokemgr_capture_finished_at",
                    "pokemgr_stream_session", "pokemgr_stream_sequence", "pokemgr_stream_pts_us",
                    "pokemgr_source_clock_generation", "pokemgr_source_clock_continuity",
                ) if key in image.info},
            }, indent=2), encoding="utf-8")
            log.info("Saved storage navigation failure evidence: %s", prefix)
        except (OSError, TypeError, ValueError):
            log.exception("Could not save storage navigation failure evidence")

    def navigate_to_storage(self) -> bool:
        """From any screen, get to Pokemon storage."""
        restarted_unknown = False
        cleared_touch_protection = False
        pending_departure = None
        unknown_observations = 0
        self._last_screen_image = None
        screen = "unobserved"

        for attempt in range(8):
            if self.is_cancelled():
                return False
            generation = self._observation_generation()
            screen = self.detect_screen()
            if self.is_cancelled():
                return False
            if generation != self._observation_generation():
                # A pause may outlast or replace this screen. Observe again
                # before using any screen-relative navigation coordinates.
                continue
            log.info("navigate_to_storage attempt %d: '%s'", attempt + 1, screen)

            if screen == 'storage':
                return True
            if screen != 'other':
                unknown_observations = 0
            # Sending another X or BACK while the first transition is still
            # visible can overshoot storage. Retain the sent-input phase across
            # pause/resume and observe again before authorizing another input.
            if screen == pending_departure:
                log.info("Waiting for %s departure after confirmed input", screen)
                time.sleep(0.25)
                continue
            if screen not in ('other', 'screen_off'):
                pending_departure = None

            if screen == 'exit_dialog':
                # Tap CANCEL
                w, h = self.r.screen_width, self.r.screen_height
                self.adb.tap(w // 2, int(h * 0.52), jitter=3)
                human_delay(1.0, 0.2)
            elif screen == 'game_map':
                pokeball = self.r.map_pokeball or self._s(*POKEBALL)
                pokemon_button = self.r.map_pokemon_button or self._s(*POKEMON_BTN)
                self.adb.tap(*pokeball, jitter=5)
                human_delay(1.5, 0.2)
                if self.is_cancelled():
                    return False
                if generation != self._observation_generation():
                    continue
                self.adb.tap(*pokemon_button, jitter=5)
                human_delay(3.0, 0.5)
            elif screen == 'screen_off':
                if self._virtual_display:
                    log.error("App stream is black or off; restart the stream before scanning")
                    self._save_storage_navigation_failure(
                        "App stream is black or off", screen, attempt + 1, pending_departure)
                    return False
                if self.adb.is_screen_on():
                    if not cleared_touch_protection:
                        log.info("Black game frame while display is awake — clearing Touch Protection")
                        if not self.clear_touch_protection():
                            return False
                        cleared_touch_protection = True
                        time.sleep(0.25)
                    elif not restarted_unknown:
                        log.info("Black game frame while display is awake — restarting Pokemon Go")
                        self.ensure_pokemon_go(restart=True)
                        restarted_unknown = True
                    else:
                        time.sleep(2)
                else:
                    if self.is_cancelled():
                        return False
                    self.adb.wake_screen()
                    time.sleep(2)
                    if self.is_cancelled():
                        return False
                    self.ensure_pokemon_go()
            elif screen == 'appraisal':
                # Tap X button to close appraisal (back key doesn't work)
                self.adb.tap(*self.appraisal_close_target(), jitter=3)
                pending_departure = 'appraisal'
                human_delay(1.0, 0.2)
                # The next fresh observation must establish detail or storage;
                # never infer that X succeeded merely because time elapsed.
            elif screen == 'detail':
                # Back is screen-relative and safe from a confirmed detail
                # surface; it returns directly to storage.
                self.adb.key_event(4)
                pending_departure = 'detail'
                human_delay(1.0, 0.3)
            else:
                if self._virtual_display:
                    unknown_observations += 1
                    if unknown_observations < 4:
                        log.info("Waiting for app-stream navigation to settle (%d/4)",
                                 unknown_observations)
                        time.sleep(0.25)
                        continue
                    log.error("Unknown app-stream screen after 4 observations; navigation stopped")
                    self._save_storage_navigation_failure(
                        "Unknown app-stream screen after 4 observations",
                        screen, attempt + 1, pending_departure)
                    return False
                # Do not guess at tablet coordinates on an unknown surface.
                if not restarted_unknown:
                    log.info("Unknown screen — restarting Pokemon Go safely")
                    self.ensure_pokemon_go(restart=True)
                    restarted_unknown = True
                else:
                    time.sleep(2)

        if not self.is_cancelled():
            self._save_storage_navigation_failure(
                "Storage navigation did not settle after 8 observations",
                screen, 8, pending_departure)
        return False

    def tap_first_pokemon(self) -> bool:
        log.info("Tapping first Pokemon...")
        if self.is_cancelled():
            return False
        target = self.r.storage_first_item or self._s(*FIRST_GRID_ITEM)
        self.adb.tap(*target, jitter=5)
        human_delay(2.0, 0.3)
        return not self.is_cancelled()

    def open_first_appraisal(self) -> bool:
        log.info("Opening appraisal...")
        r = self.r
        if self.is_cancelled():
            return False
        self.adb.tap(*r.menu_button, jitter=3)
        human_delay(0.8, 0.15)
        if self.is_cancelled():
            return False
        self.adb.tap(*r.appraise_menu_item, jitter=3)
        human_delay(1.0, 0.2)
        if self.is_cancelled():
            return False
        self.adb.tap(*r.dismiss_professor, jitter=3)
        human_delay(1.5, 0.3)
        return not self.is_cancelled()

    def enter_search(self, query: str, *, verify: bool = False) -> bool:
        """Clear any existing search and enter a new filter.

        Verified actions read the complete native editor, including text
        clipped by the visible field, before applying the filter.
        """
        log.info("Searching: %s", query)
        chunked = verify and len(query) > 80
        if chunked and len(query) > 512:
            log.warning("Applied search held: query exceeds complete editor readback limit")
            return False
        generation = self._observation_generation() if chunked else None

        def invalidated():
            return self.is_cancelled() or (
                chunked and generation != self._observation_generation())

        if invalidated():
            return False
        if verify and self.detect_screen() != "storage":
            return False
        if invalidated():
            return False

        # Step 1: Clear existing filter — tap X button at right end of search bar
        clear_target = self.r.storage_search_clear or self._s(920, 300)
        self.adb.tap(*clear_target, jitter=3)
        human_delay(0.5, 0.1)
        if invalidated():
            return False

        # Step 2: Tap search bar to open keyboard
        search_target = self.r.storage_search_bar or self._s(*SEARCH_BAR)
        self.adb.tap(*search_target, jitter=3)
        human_delay(1.0, 0.2)
        if invalidated():
            return False

        # The focused game editor exposes its full value independently of the
        # clipped Unity preview. Never infer successful clearing from a tap.
        if verify:
            # Unity also uses a native editor for nicknames. Its editor alone
            # cannot prove search context; storage stays visible above the IME.
            if self.detect_screen() != "storage":
                return False
            if invalidated():
                return False
            from .search_text import read_search_text
            previous = read_search_text(self.adb)
            if previous is None:
                log.warning("Could not read the complete storage search editor")
                return False
            clear_length = len(previous)
        else:
            clear_length = 40
        if invalidated():
            return False

        # Step 3: Clear remaining text (legacy callers retain their old path).
        self.adb.key_event(123)  # MOVE_END
        time.sleep(0.1)
        for _ in range(clear_length):
            if invalidated():
                return False
            self.adb.key_event(67)  # DEL
        time.sleep(0.2)
        if invalidated():
            return False
        if verify and read_search_text(self.adb) != "":
            log.warning("Storage search clearing was not confirmed")
            return False
        if invalidated():
            return False

        # Step 4: Type the new query
        if verify and self.detect_screen() != "storage":
            return False
        if invalidated():
            return False
        if chunked:
            if not self._type_verified_search_chunks(query, invalidated):
                return False
        elif query:
            self.adb.input_text(query)
        human_delay(0.5, 0.2)
        if invalidated():
            return False
        if verify and read_search_text(self.adb) != query:
            log.warning("Applied search held: complete query readback did not match")
            return False
        if invalidated():
            return False

        # Step 5: Apply
        if verify and self.detect_screen() != "storage":
            return False
        if invalidated():
            return False
        self.adb.key_event(66)  # ENTER
        human_delay(2.0, 0.5)
        if invalidated():
            return False
        return not verify or self.detect_screen() == "storage"

    # TRACEWEAVER: entrypoint=_type_verified_search_chunks; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
    def _type_verified_search_chunks(self, query, invalidated):
        """Append bounded chunks only to the independently confirmed prefix.

        A failed read stops immediately; retrying or appending to an uncertain
        editor could silently change the filter. The caller still owns final
        full-query readback and ENTER.
        """
        from .search_text import read_search_text

        for offset in range(0, len(query), 80):
            if invalidated() or self.detect_screen() != "storage":
                return False
            if invalidated():
                return False
            if read_search_text(self.adb) != query[:offset]:
                log.warning("Applied search held: prefix changed before chunk at %d", offset)
                return False
            if invalidated() or self.detect_screen() != "storage":
                return False
            if invalidated():
                return False
            self.adb.input_text(query[offset:offset + 80])
            human_delay(0.1, 0.02)
            if invalidated() or self.detect_screen() != "storage":
                return False
            if invalidated():
                return False
            if read_search_text(self.adb) != query[:offset + 80]:
                log.warning("Applied search held: incomplete chunk readback at %d", offset)
                return False
            if invalidated():
                return False
        return True

# TRACEWEAVER: entrypoint=read_filtered_count; req=REQ-SCAN-003; trace=TRACE-SCAN-006; ver=VER-SCAN-001
    def read_filtered_count(self) -> int:
        """Read the Pokemon count from the storage header after a filter is applied.

        Verifies we're on the storage screen first. Retries if needed.
        Returns the count or 0 if unreadable.
        """
        import pytesseract, cv2, re

        def _parse_count(raw_text: str) -> int:
            # Unfiltered storage uses "owned/capacity" (for example
            # 2947/3250); the scan target is the owned value, never capacity.
            used_and_capacity = re.search(r"(\d+)\s*/\s*(\d+)", raw_text)
            if used_and_capacity:
                owned = int(used_and_capacity.group(1))
                return owned if 1 <= owned <= 10000 else 0

            parenthesized = re.search(r"\((\d+)\)", raw_text)
            if parenthesized:
                filtered = int(parenthesized.group(1))
                return filtered if 1 <= filtered <= 10000 else 0

            for digits in re.findall(r"\d+", raw_text):
                count = int(digits)
                if 1 <= count <= 10000:
                    return count
            return 0

        # Make sure we're on storage — retry up to 5 times
        import time as _time
        img = None
        for attempt in range(5):
            if self.is_cancelled():
                return 0
            img = self.adb.screencap()
            screen = self.detect_screen(img)
            if screen == 'storage':
                break
            log.warning("Not on storage (%s) — retrying (%d/5)", screen, attempt + 1)
            _time.sleep(0.5)
        else:
            log.warning("Not on storage after 5 attempts — can't read count")
            return 0
        w, h = img.size
        sx, sy = w / 968, h / 2376

        # Keep the full glyph height around the count text.  A former 30px
        # crop clipped the live "Q(2622)" header and Tesseract read it as
        # "Q(9677)" even though the source digits were clear.
        crop = img.crop((
            int(300 * sx), int(145 * sy),
            int(670 * sx), int(195 * sy)
        ))
        arr = np.array(crop)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        gray = cv2.resize(gray, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC)
        _, binary = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY_INV)

        text = pytesseract.image_to_string(
            binary, config="--psm 7 -c tessedit_char_whitelist=0123456789()Q/"
        ).strip()

        count = _parse_count(text)
        if count:
            log.info("Filtered count: %d (text='%s')", count, text)
            return count

        # OCR failed — retry with a fresh screenshot
        log.warning("Count OCR failed (text='%s') — retrying", text)
        for retry in range(3):
            if self.is_cancelled():
                return 0
            _time.sleep(0.3)
            if self.is_cancelled():
                return 0
            img2 = self.adb.screencap()
            crop2 = img2.crop((
                int(300 * sx), int(145 * sy),
                int(670 * sx), int(195 * sy)
            ))
            arr2 = np.array(crop2)
            gray2 = cv2.cvtColor(arr2, cv2.COLOR_RGB2GRAY)
            gray2 = cv2.resize(gray2, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC)
            _, binary2 = cv2.threshold(gray2, 140, 255, cv2.THRESH_BINARY_INV)
            text2 = pytesseract.image_to_string(
                binary2, config="--psm 7 -c tessedit_char_whitelist=0123456789()Q/"
            ).strip()
            count = _parse_count(text2)
            if count:
                log.info("Filtered count (retry %d): %d (text='%s')", retry + 1, count, text2)
                return count

        log.warning("Could not read filtered count after retries")
        return 0

    # TRACEWEAVER: entrypoint=read_filtered_count_verified; req=REQ-SCAN-003; trace=TRACE-SCAN-006; ver=VER-SCAN-001
    def read_filtered_count_verified(self) -> int | None:
        """Confirm a storage count twice; distinguish explicit zero from failure.

        The caller must first apply and verify its search. Each observation
        validates storage again. Bare OCR zero is ambiguous; only a complete
        parenthesized or owned/capacity header can prove an empty result.
        """
        import math
        import re
        import cv2
        import pytesseract

        def parse(text, *, allow_bare=True):
            text = text.strip()
            match = re.fullmatch(r"(?:Q\s*)?\(\s*(\d+)\s*\)", text)
            if match:
                value = int(match.group(1))
                return value if 0 <= value <= 10000 else None
            match = re.fullmatch(r"(?:Q\s*)?(\d+)\s*/\s*(\d+)", text)
            if match:
                value, capacity = map(int, match.groups())
                return value if 0 <= value <= capacity <= 10000 and capacity > 0 else None
            if allow_bare and re.fullmatch(r"\d+", text):
                value = int(text)
                return value if 1 <= value <= 10000 else None
            return None

        def independent(older, newer):
            if older is newer or older.size != newer.size:
                return False
            bounds = tuple(image.info.get(key) for image in (older, newer)
                           for key in ("pokemgr_capture_started_at", "pokemgr_capture_finished_at"))
            if (not all(type(value) in (int, float) and math.isfinite(value) for value in bounds)
                    or not (0 < bounds[0] <= bounds[1] < bounds[2] <= bounds[3])):
                return False
            keys = ("pokemgr_stream_session", "pokemgr_stream_sequence",
                    "pokemgr_stream_pts_us", "pokemgr_source_clock_generation")
            token_key = "pokemgr_source_clock_continuity"
            if not any(key in image.info for image in (older, newer) for key in (*keys, token_key)):
                return True
            first, second = (tuple(image.info.get(key) for key in keys) for image in (older, newer))
            if (not isinstance(first[0], str) or not first[0] or first[0] != second[0]
                    or not all(type(value) is int and value > 0 for value in (*first[1:], *second[1:]))
                    or second[1] <= first[1] or second[2] <= first[2]):
                return False
            if any(token_key in image.info for image in (older, newer)):
                tokens = (older.info.get(token_key), newer.info.get(token_key))
                return (all(isinstance(token, str) and re.fullmatch(r"[0-9a-f]{32}", token)
                            for token in tokens)
                        and tokens[0] == tokens[1] and second[3] >= first[3])
            return first[3] == second[3]

        previous = None
        observed = None
        for attempt in range(4):
            if self.is_cancelled():
                return None
            if attempt:
                time.sleep(0.3)
                if self.is_cancelled():
                    return None
            try:
                image = self.adb.screencap()
            except ADBError as exc:
                log.warning("Verified count capture unavailable: %s", exc)
                return None
            if self.is_cancelled():
                return None
            screen = self.detect_screen(image)
            if self.is_cancelled() or screen != "storage":
                log.warning("Verified count lost storage context")
                return None
            w, h = image.size
            crop = image.crop((int(300 * w / 968), int(145 * h / 2376),
                               int(670 * w / 968), int(195 * h / 2376)))
            try:
                gray = cv2.cvtColor(np.asarray(crop.convert("RGB")), cv2.COLOR_RGB2GRAY)
                gray = cv2.resize(gray, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC)
                _, binary = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY_INV)
                text = pytesseract.image_to_string(
                    binary, config="--psm 7 -c tessedit_char_whitelist=0123456789()Q/",
                ).strip()
            except (pytesseract.TesseractError, OSError, ValueError, cv2.error) as exc:
                log.warning("Verified count OCR unavailable: %s", exc)
                return None
            if self.is_cancelled():
                return None
            count = parse(text)
            if count is None:
                # Inverted text can lose the thin owned/capacity slash. Retain
                # its lighter edge pixels in normal polarity, without repair.
                try:
                    _, binary = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY)
                    text = pytesseract.image_to_string(
                        binary, config="--psm 7 -c tessedit_char_whitelist=0123456789()Q/",
                    ).strip()
                except (pytesseract.TesseractError, OSError, ValueError, cv2.error) as exc:
                    log.warning("Verified count fallback OCR unavailable: %s", exc)
                    return None
                if self.is_cancelled():
                    return None
                # A lone number could be only the capacity half. The retry
                # must recover a complete header, not merely readable digits.
                count = parse(text, allow_bare=False)
            if count is None:
                previous = None
                continue
            if observed is not None and count != observed:
                log.warning("Verified count disagreed: %d then %d", observed, count)
                return None
            observed = count
            if previous is not None:
                if not independent(previous, image):
                    log.warning("Verified count captures were not independent")
                    return None
                log.info("Verified filtered count: %d (text='%s')", count, text)
                return count
            previous = image
        log.warning("Could not independently confirm the filtered count")
        return None

    def ensure_pokemon_go(self, restart: bool = False) -> bool:
        if self.is_cancelled():
            return False
        if self._virtual_display:
            self.adb.start_pokemon_go(restart=restart)
            time.sleep(10 if restart else 5)
            return not self.is_cancelled()
        if restart:
            self.adb.shell('am force-stop com.nianticlabs.pokemongo')
            time.sleep(0.5)
            if self.is_cancelled():
                return False
        self.adb.shell(
            'am start -n com.nianticlabs.pokemongo/'
            'com.nianticproject.holoholo.libholoholo.unity.UnityMainActivity'
        )
        # A clean Unity restart needs longer than an ordinary foregrounding
        # before screen detection can reliably see the map controls.
        time.sleep(10 if restart else 5)
        return not self.is_cancelled()
