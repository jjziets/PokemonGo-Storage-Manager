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


def _has_pokeball(arr: np.ndarray) -> bool:
    """Check if the red pokeball is visible at bottom center.
    The red top-half has pixels RGB ~(255, 57, 70) around y=2220, x=460-500."""
    h, w = arr.shape[:2]
    # Check a broad normalized area around the map's bottom-center Pokeball.
    # Tablet UI places it noticeably higher than the Fold cover-screen layout.
    y_start = int(h * 0.84)
    y_end = int(h * 0.94)
    x_start = int(w * 0.44)
    x_end = int(w * 0.56)
    if y_end > h or x_end > w:
        return False
    region = arr[y_start:y_end, x_start:x_end]
    # Count very red pixels (R>200, G<120, B<120)
    red_mask = (region[:, :, 0] > 200) & (region[:, :, 1] < 120) & (region[:, :, 2] < 120)
    red_ratio = np.sum(red_mask) / red_mask.size
    return red_ratio > 0.03


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
                 cancelled=None):
        self.adb = adb
        self.r = regions
        w, h = regions.screen_width, regions.screen_height
        self.sx = w / 968
        self.sy = h / 2376
        self._cancelled = cancelled or (lambda: False)
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

    def navigate_to_storage(self) -> bool:
        """From any screen, get to Pokemon storage."""
        restarted_unknown = False
        cleared_touch_protection = False

        for attempt in range(8):
            if self.is_cancelled():
                return False
            screen = self.detect_screen()
            log.info("navigate_to_storage attempt %d: '%s'", attempt + 1, screen)

            if screen == 'storage':
                return True
            elif screen == 'exit_dialog':
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
                self.adb.tap(*pokemon_button, jitter=5)
                human_delay(3.0, 0.5)
            elif screen == 'screen_off':
                if self._virtual_display:
                    log.error("App stream is black or off; restart the stream before scanning")
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
                human_delay(1.0, 0.2)
                if self.is_cancelled():
                    return False
                # Now on detail screen — back to storage
                self.adb.key_event(4)
                human_delay(1.0, 0.3)
            elif screen == 'detail':
                # Back is screen-relative and safe from a confirmed detail
                # surface; it returns directly to storage.
                self.adb.key_event(4)
                human_delay(1.0, 0.3)
            else:
                if self._virtual_display:
                    log.error("Unknown app-stream screen; navigation stopped")
                    return False
                # Do not guess at tablet coordinates on an unknown surface.
                if not restarted_unknown:
                    log.info("Unknown screen — restarting Pokemon Go safely")
                    self.ensure_pokemon_go(restart=True)
                    restarted_unknown = True
                else:
                    time.sleep(2)

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
        if self.is_cancelled():
            return False
        if verify and self.detect_screen() != "storage":
            return False
        if self.is_cancelled():
            return False

        # Step 1: Clear existing filter — tap X button at right end of search bar
        clear_target = self.r.storage_search_clear or self._s(920, 300)
        self.adb.tap(*clear_target, jitter=3)
        human_delay(0.5, 0.1)
        if self.is_cancelled():
            return False

        # Step 2: Tap search bar to open keyboard
        search_target = self.r.storage_search_bar or self._s(*SEARCH_BAR)
        self.adb.tap(*search_target, jitter=3)
        human_delay(1.0, 0.2)
        if self.is_cancelled():
            return False

        # The focused game editor exposes its full value independently of the
        # clipped Unity preview. Never infer successful clearing from a tap.
        if verify:
            # Unity also uses a native editor for nicknames. Its editor alone
            # cannot prove search context; storage stays visible above the IME.
            if self.detect_screen() != "storage":
                return False
            if self.is_cancelled():
                return False
            from .search_text import read_search_text
            previous = read_search_text(self.adb)
            if previous is None:
                log.warning("Could not read the complete storage search editor")
                return False
            clear_length = len(previous)
        else:
            clear_length = 40
        if self.is_cancelled():
            return False

        # Step 3: Clear remaining text (legacy callers retain their old path).
        self.adb.key_event(123)  # MOVE_END
        time.sleep(0.1)
        for _ in range(clear_length):
            if self.is_cancelled():
                return False
            self.adb.key_event(67)  # DEL
        time.sleep(0.2)
        if self.is_cancelled():
            return False
        if verify and read_search_text(self.adb) != "":
            log.warning("Storage search clearing was not confirmed")
            return False
        if self.is_cancelled():
            return False

        # Step 4: Type the new query
        if verify and self.detect_screen() != "storage":
            return False
        if self.is_cancelled():
            return False
        if query:
            self.adb.input_text(query)
        human_delay(0.5, 0.2)
        if self.is_cancelled():
            return False
        if verify and read_search_text(self.adb) != query:
            log.warning("Applied search held: complete query readback did not match")
            return False
        if self.is_cancelled():
            return False

        # Step 5: Apply
        if verify and self.detect_screen() != "storage":
            return False
        if self.is_cancelled():
            return False
        self.adb.key_event(66)  # ENTER
        human_delay(2.0, 0.5)
        if self.is_cancelled():
            return False
        return not verify or self.detect_screen() == "storage"

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
