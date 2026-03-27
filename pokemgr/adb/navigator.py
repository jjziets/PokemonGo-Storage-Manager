"""Screen-aware navigation through Pokemon Go UI.

Detects the current screen by looking for specific visual markers,
then navigates step by step to the target.
"""

import logging
import time

from PIL import Image
import numpy as np

from .controller import ADBController
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
    sy = h / 2376
    sx = w / 968
    # Check a small area where the red part of the pokeball sits
    y_start = int(2210 * sy)
    y_end = int(2240 * sy)
    x_start = int(450 * sx)
    x_end = int(510 * sx)
    if y_end > h or x_end > w:
        return False
    region = arr[y_start:y_end, x_start:x_end]
    # Count very red pixels (R>200, G<120, B<120)
    red_mask = (region[:, :, 0] > 200) & (region[:, :, 1] < 120) & (region[:, :, 2] < 120)
    red_ratio = np.sum(red_mask) / red_mask.size
    return red_ratio > 0.3


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
    return is_white


def _has_hp_bar(arr: np.ndarray, sx: float, sy: float) -> bool:
    """Check if an HP bar is visible (green=full or gray=depleted)."""
    for y in range(int(900 * sy), int(1100 * sy)):
        row = arr[y, int(300 * sx):int(650 * sx), :3]
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
    def __init__(self, adb: ADBController, regions: ScreenRegions):
        self.adb = adb
        self.r = regions
        w, h = regions.screen_width, regions.screen_height
        self.sx = w / 968
        self.sy = h / 2376

    def _s(self, x: int, y: int) -> tuple[int, int]:
        return (int(x * self.sx), int(y * self.sy))

    def detect_screen(self, img: Image.Image | None = None) -> str:
        """Detect current screen: 'game_map', 'storage', 'detail', 'appraisal', 'screen_off', or 'other'."""
        if img is None:
            img = self.adb.screencap()
        arr = np.array(img)

        # Check if screen is off (all black)
        if np.mean(arr) < 10:
            return 'screen_off'

        # Check for exit dialog ("Do you want to exit Pokemon GO?")
        # The dialog has a dark overlay (dim background) with a white box in center
        # Key: the TOP of the screen is very dark (overlay) unlike game map or detail
        h_img, w_img = arr.shape[:2]
        sy = h_img / 2376
        sx = w_img / 968
        top_area = arr[int(200 * sy):int(400 * sy), int(100 * sx):int(868 * sx)]
        top_brightness = np.mean(top_area)
        # Exit dialog overlay makes the top very dark (<80) AND there's a white box in center
        if top_brightness < 80:
            dialog_center = arr[int(900 * sy):int(1050 * sy), int(250 * sx):int(718 * sx)]
            center_brightness = np.mean(dialog_center)
            if center_brightness > 200:
                return 'exit_dialog'

        # Pokeball visible = game map (most reliable, check first)
        if _has_pokeball(arr):
            return 'game_map'

        # Check appraisal (has IV bars)
        from ..reader.bars import are_bars_present
        if are_bars_present(img):
            return 'appraisal'

        # Check storage (white header area)
        if _has_storage_header(arr):
            return 'storage'

        # Check detail screen: HP bar visible (green or gray) = detail or appraisal
        # Since appraisal was already checked above, this must be the detail screen
        if _has_hp_bar(arr, sx, sy):
            return 'detail'

        return 'other'

    def navigate_to_appraisal(self, search_query: str | None = None) -> bool:
        """From any screen, navigate to the first Pokemon's appraisal."""

        for attempt in range(8):
            screen = self.detect_screen()
            log.info("Attempt %d: detected '%s'", attempt + 1, screen)

            if screen == 'appraisal':
                log.info("Already on appraisal — ready")
                return True

            elif screen == 'exit_dialog':
                log.info("Exit dialog — tapping CANCEL")
                w, h = self.r.screen_width, self.r.screen_height
                self.adb.tap(w // 2, int(h * 0.52), jitter=3)  # CANCEL button
                human_delay(1.0, 0.2)
                continue

            elif screen == 'screen_off':
                log.info("Screen off — waking up and launching Pokemon Go")
                self.adb.wake_screen()
                time.sleep(2)
                w, h = self.r.screen_width, self.r.screen_height
                self.adb.swipe(w // 2, int(h * 0.7), w // 2, int(h * 0.3), 300, jitter=0)
                time.sleep(1)
                self.ensure_pokemon_go()
                continue

            elif screen == 'game_map':
                log.info("On game map — opening storage...")
                self.adb.tap(*self._s(*POKEBALL), jitter=5)
                human_delay(1.5, 0.2)
                self.adb.tap(*self._s(*POKEMON_BTN), jitter=5)
                human_delay(3.0, 0.5)
                continue

            elif screen == 'storage':
                log.info("In storage — entering search and tapping first Pokemon...")
                if search_query:
                    self.enter_search(search_query)
                    search_query = None  # don't re-enter on retry
                self.tap_first_pokemon()
                self.open_first_appraisal()
                continue

            else:
                # Unknown screen — launch Pokemon Go
                log.info("Unknown screen — launching Pokemon Go")
                self.ensure_pokemon_go()
                continue

        log.error("Failed to reach appraisal after 8 attempts")
        return False

    def navigate_to_storage(self) -> bool:
        """From any screen, get to Pokemon storage."""
        for attempt in range(8):
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
                self.adb.tap(*self._s(*POKEBALL), jitter=5)
                human_delay(1.5, 0.2)
                self.adb.tap(*self._s(*POKEMON_BTN), jitter=5)
                human_delay(3.0, 0.5)
            elif screen == 'screen_off':
                self.adb.wake_screen()
                time.sleep(2)
                self.ensure_pokemon_go()
            elif screen == 'appraisal':
                # Tap X button to close appraisal (back key doesn't work)
                self.adb.tap(*self._s(484, 2260), jitter=3)
                human_delay(1.0, 0.2)
                # Now on detail screen — back to storage
                self.adb.key_event(4)
                human_delay(1.0, 0.3)
            else:
                # Unknown — likely detail screen or other view
                # Tap X button area first (closes appraisal if visible)
                self.adb.tap(*self._s(484, 2260), jitter=3)
                human_delay(0.5, 0.1)
                # Then press back
                self.adb.key_event(4)
                human_delay(1.0, 0.3)

        return False

    def tap_first_pokemon(self):
        log.info("Tapping first Pokemon...")
        self.adb.tap(*self._s(*FIRST_GRID_ITEM), jitter=5)
        human_delay(2.0, 0.3)

    def open_first_appraisal(self):
        log.info("Opening appraisal...")
        r = self.r
        self.adb.tap(*r.menu_button, jitter=3)
        human_delay(0.8, 0.15)
        self.adb.tap(*r.appraise_menu_item, jitter=3)
        human_delay(1.0, 0.2)
        self.adb.tap(*r.dismiss_professor, jitter=3)
        human_delay(1.5, 0.3)

    def enter_search(self, query: str):
        """Clear any existing search and enter a new filter.

        Taps the X to clear old filter first, then types the new one.
        """
        log.info("Searching: %s", query)

        # Step 1: Clear existing filter — tap X button at right end of search bar
        self.adb.tap(*self._s(920, 300), jitter=3)
        human_delay(0.5, 0.1)

        # Step 2: Tap search bar to open keyboard
        self.adb.tap(*self._s(*SEARCH_BAR), jitter=3)
        human_delay(1.0, 0.2)

        # Step 3: Clear any remaining text (40 backspaces)
        self.adb.shell("input keyevent 123")  # MOVE_END
        time.sleep(0.1)
        for _ in range(40):
            self.adb.shell("input keyevent 67")  # DEL
        time.sleep(0.2)

        # Step 4: Type the new query
        self.adb.input_text(query)
        human_delay(0.5, 0.2)

        # Step 5: Apply
        self.adb.key_event(66)  # ENTER
        human_delay(2.0, 0.5)

    def read_filtered_count(self) -> int:
        """Read the Pokemon count from the storage header after a filter is applied.

        Verifies we're on the storage screen first. Retries if needed.
        Returns the count or 0 if unreadable.
        """
        import pytesseract, cv2, re

        # Make sure we're on storage — retry up to 5 times
        import time as _time
        img = None
        for attempt in range(5):
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

        # The count text "Q(2876)" is at y~155-175. Crop generously and OCR.
        crop = img.crop((
            int(300 * sx), int(155 * sy),
            int(670 * sx), int(185 * sy)
        ))
        arr = np.array(crop)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        gray = cv2.resize(gray, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC)
        _, binary = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY_INV)

        text = pytesseract.image_to_string(
            binary, config="--psm 7 -c tessedit_char_whitelist=0123456789()Q/"
        ).strip()

        digits = re.findall(r'\d+', text)
        if digits:
            counts = [int(d) for d in digits if 1 <= int(d) <= 10000]
            if counts:
                count = max(counts)
                log.info("Filtered count: %d (text='%s')", count, text)
                return count

        # OCR failed — retry with a fresh screenshot
        log.warning("Count OCR failed (text='%s') — retrying", text)
        for retry in range(3):
            _time.sleep(0.3)
            img2 = self.adb.screencap()
            crop2 = img2.crop((
                int(300 * sx), int(155 * sy),
                int(670 * sx), int(185 * sy)
            ))
            arr2 = np.array(crop2)
            gray2 = cv2.cvtColor(arr2, cv2.COLOR_RGB2GRAY)
            gray2 = cv2.resize(gray2, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC)
            _, binary2 = cv2.threshold(gray2, 140, 255, cv2.THRESH_BINARY_INV)
            text2 = pytesseract.image_to_string(
                binary2, config="--psm 7 -c tessedit_char_whitelist=0123456789()Q/"
            ).strip()
            digits2 = re.findall(r'\d+', text2)
            if digits2:
                counts2 = [int(d) for d in digits2 if 1 <= int(d) <= 10000]
                if counts2:
                    count = max(counts2)
                    log.info("Filtered count (retry %d): %d (text='%s')", retry + 1, count, text2)
                    return count

        log.warning("Could not read filtered count after retries")
        return 0

    def ensure_pokemon_go(self):
        self.adb.shell(
            'am start -n com.nianticlabs.pokemongo/'
            'com.nianticproject.holoholo.libholoholo.unity.UnityMainActivity'
        )
        time.sleep(5)
