"""OCR for reading Pokemon species names and CP values."""

import re
import logging
import cv2
import numpy as np
import pytesseract
from PIL import Image

from ..calibration.regions import BBox

log = logging.getLogger(__name__)


def read_species_name(image: Image.Image, region: BBox) -> tuple[str, float]:
    """Read the Pokemon species name from the detail screen.

    Name is dark text on a white/light background.
    Returns (name, confidence) where confidence is 0.0-1.0.
    """
    crop = image.crop(region.as_tuple())
    gray = cv2.cvtColor(np.array(crop), cv2.COLOR_RGB2GRAY)

    # Upscale small crops for better OCR
    if gray.shape[0] < 80:
        scale = 3
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    # Dark text on light bg: invert so text becomes white on black
    _, binary = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY_INV)

    # Use image_to_string (simpler, doesn't drop low-confidence words)
    text = pytesseract.image_to_string(binary, config="--psm 7").strip()

    # Clean up OCR artifacts — keep letters, numbers, spaces, hyphens, dots
    name = re.sub(r"[^a-zA-Z0-9\s\-'.♀♂]", "", text).strip()

    # Estimate confidence from result quality
    confidence = 0.9 if len(name) >= 3 else (0.5 if name else 0.0)

    log.debug("OCR name: '%s' (confidence: %.2f)", name, confidence)
    return name, confidence


def read_cp(image: Image.Image, region: BBox) -> tuple[int, float]:
    """Read the CP value from the detail screen.

    CP is white text on a gradient/colored background.
    Returns (cp_value, confidence). Returns (-1, 0.0) on failure.
    """
    crop = np.array(image.crop(region.as_tuple()))

    # Fast path: Tesseract first (~10ms)
    scale = 5
    crop_5x = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    white_mask = np.all(crop_5x > 200, axis=2).astype(np.uint8) * 255

    full_text = pytesseract.image_to_string(
        white_mask, config="--psm 7"
    ).strip()

    digits = re.sub(r"[^0-9]", "", full_text)
    if digits:
        cp = int(digits)
        log.debug("CP via Tesseract (fast): %d", cp)
        return cp, 0.8

    # Slow path: PaddleOCR fallback (~250ms, better accuracy)
    try:
        from .ocr_engine import read_number_paddle
        crop_rgb = crop[:, :, :3] if crop.shape[2] == 4 else crop
        paddle_cp = read_number_paddle(crop_rgb)
        if paddle_cp > 0:
            log.debug("CP via PaddleOCR (fallback): %d", paddle_cp)
            return paddle_cp, 0.9
    except Exception:
        pass

    log.warning("Failed to read CP from text: '%s'", full_text)
    return -1, 0.0


def read_caught_species(image: Image.Image, screen_width: int, screen_height: int) -> str:
    """Read the real species name from the professor's speech bubble.

    The bubble says 'This [Species] was caught on [date]...'
    Uses PaddleOCR for best accuracy on the speech bubble text.
    Returns the species name or empty string if not found.
    """
    arr = np.array(image)
    w, h = image.size

    # Crop the speech bubble area (bottom of screen)
    bubble = arr[h - 220:h - 10, 20:w - 20]
    if bubble.shape[2] == 4:
        bubble = bubble[:, :, :3]
    bubble = bubble.copy()  # ensure contiguous

    try:
        from .ocr_engine import read_text_paddle
        # PaddleOCR returns all text lines — find the one with "This [Species]"
        from .ocr_engine import _get_paddle
        reader = _get_paddle()
        if reader:
            result = reader.ocr(bubble)
            if result:
                for item in result:
                    if isinstance(item, dict) and 'rec_texts' in item:
                        for text in item['rec_texts']:
                            match = re.search(r'This\s+(\w+)', text, re.IGNORECASE)
                            if match:
                                species = match.group(1)
                                log.debug("Caught species from bubble: '%s' (text='%s')", species, text)
                                return species
    except Exception as e:
        log.debug("Failed to read caught species: %s", e)

    return ""


_gym_template = None
_gym_template_gray = None

def is_in_gym(image: Image.Image, screen_width: int, screen_height: int) -> bool:
    """Check if Pokemon is in a gym using template matching of the GO TO GYM button."""
    global _gym_template, _gym_template_gray

    # Load template once
    if _gym_template_gray is None:
        from pathlib import Path
        template_path = Path(__file__).parent / "gym_button_template.png"
        if template_path.exists():
            _gym_template = cv2.imread(str(template_path))
            _gym_template_gray = cv2.cvtColor(_gym_template, cv2.COLOR_BGR2GRAY)
        else:
            log.debug("Gym button template not found")
            return False

    arr = np.array(image)
    sx = screen_width / 968
    sy = screen_height / 2376

    # Crop the area where the button could be (y=1100-1250, roughly)
    y_start = int(1100 * sy)
    y_end = min(int(1260 * sy), arr.shape[0])
    search_area = arr[y_start:y_end, :, :3]
    search_gray = cv2.cvtColor(search_area, cv2.COLOR_RGB2GRAY)

    # Scale template to match if needed
    template = _gym_template_gray
    if template.shape[0] > search_gray.shape[0] or template.shape[1] > search_gray.shape[1]:
        return False

    # Template match
    result = cv2.matchTemplate(search_gray, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val > 0.6:
        log.debug("Gym button match: score=%.2f at y=%d", max_val, y_start + max_loc[1])
        return True

    return False


def read_hp(image: Image.Image, screen_width: int, screen_height: int) -> int:
    """Read HP value from the appraisal/detail screen.

    HP text like '188 / 188 HP' is light gray on white background.
    Position varies based on Lucky/non-Lucky Pokemon.
    Scans a vertical range to find it dynamically.
    Returns the max HP value or -1 if not found.
    """
    sx = screen_width / 968
    sy = screen_height / 2376

    # Step 1: Find the HP bar dynamically (green = full HP, gray = depleted)
    arr = np.array(image)
    bar_y = -1
    for y in range(int(900 * sy), int(1100 * sy)):
        row = arr[y, int(300 * sx):int(650 * sx), :3]
        # Green HP bar: R~80-140, G>220, B~160-200
        green_mask = (row[:, 0] > 80) & (row[:, 0] < 140) & (row[:, 1] > 220) & (row[:, 2] > 160) & (row[:, 2] < 200)
        if np.sum(green_mask) / len(green_mask) > 0.3:
            bar_y = y
            break
        # Gray depleted HP bar: R≈G≈B ≈ 228-235 (distinct thin line on white background)
        gray_mask = (row[:, 0] > 225) & (row[:, 0] < 240) & (row[:, 1] > 225) & (row[:, 1] < 240) & (row[:, 2] > 225) & (row[:, 2] < 240)
        gray_spread = np.max(row, axis=1) - np.min(row, axis=1)
        tight_gray = gray_mask & (gray_spread < 8)
        if np.sum(tight_gray) / len(tight_gray) > 0.5:
            bar_y = y
            break

    if bar_y < 0:
        log.debug("HP bar not found — Pokemon may be in gym")
        return -2  # -2 = no bar (gym), -1 = bar found but text unreadable

    arr = np.array(image)

    # Step 2: Read HP text below the bar
    # Try Tesseract first (fast), then PaddleOCR if Tesseract fails or disagrees
    from collections import Counter
    hp_readings = []

    for offset in range(25, 55, 3):
        y_start = bar_y + offset
        crop = image.crop((int(280 * sx), y_start, int(680 * sx), y_start + int(25 * sy)))
        crop_arr = np.array(crop)
        gray = cv2.cvtColor(crop_arr, cv2.COLOR_RGB2GRAY)
        gray_5x = cv2.resize(gray, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC)
        _, binary = cv2.threshold(gray_5x, 210, 255, cv2.THRESH_BINARY_INV)
        text = pytesseract.image_to_string(binary, config="--psm 7").strip()

        # Require "HP" in the text to confirm it's the HP line (not stardust etc.)
        hp_match = re.search(r'(\d{1,3})\s*/\s*(\d{2,3})\s*H\s*P', text)
        if hp_match:
            max_hp = int(hp_match.group(2))
            if 10 <= max_hp <= 500:
                hp_readings.append(max_hp)
                log.debug("HP read: %s/%d (text='%s', bar_y=%d, offset=%d)",
                          hp_match.group(1), max_hp, text, bar_y, offset)
        else:
            # Looser match but only if "HP" or "H P" appears somewhere in text
            if 'HP' in text.upper() or 'H P' in text.upper():
                hp_match2 = re.search(r'(\d{1,3})\s*/\s*(\d{2,3})', text)
                if hp_match2:
                    max_hp = int(hp_match2.group(2))
                    if 10 <= max_hp <= 500:
                        hp_readings.append(max_hp)
                        log.debug("HP read (loose): %s/%d (text='%s', bar_y=%d, offset=%d)",
                                  hp_match2.group(1), max_hp, text, bar_y, offset)

    # Check if Tesseract gave a clear majority
    if hp_readings:
        counter = Counter(hp_readings)
        most_common_val, most_common_count = counter.most_common(1)[0]
        total_readings = len(hp_readings)

        # If >50% agree, trust Tesseract
        if most_common_count > total_readings / 2:
            log.debug("HP Tesseract majority: %d (%d/%d readings)",
                       most_common_val, most_common_count, total_readings)
            return most_common_val

        # Tesseract readings disagree — fall through to PaddleOCR
        log.debug("HP Tesseract disagreement: %s — trying PaddleOCR", dict(counter))

    # Step 3: PaddleOCR fallback — single crop of the HP text area
    paddle_hp = _read_hp_paddle(arr, bar_y, sx, sy)
    if paddle_hp > 0:
        return paddle_hp

    # Last resort: return Tesseract's most common even without majority
    if hp_readings:
        return Counter(hp_readings).most_common(1)[0][0]
    return -1


def _read_hp_paddle(arr: np.ndarray, bar_y: int, sx: float, sy: float) -> int:
    """Read HP using PaddleOCR on the text area below the HP bar.

    Returns max HP value or -1 if not found.
    """
    try:
        from .ocr_engine import read_text_paddle

        # Crop a generous area below the HP bar
        y_start = bar_y + int(15 * sy)
        y_end = bar_y + int(55 * sy)
        x_start = int(200 * sx)
        x_end = int(750 * sx)

        crop = arr[y_start:y_end, x_start:x_end, :3].copy()

        # Scale up 3x for PaddleOCR accuracy
        crop_3x = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)

        text = read_text_paddle(crop_3x)
        if not text:
            return -1

        log.debug("HP PaddleOCR raw: '%s'", text)

        # Clean common OCR substitutions: O→0, l→1, I→1
        cleaned = text.replace('O', '0').replace('o', '0').replace('l', '1').replace('I', '1')

        # Match HP pattern (handles "52/52HP", "52 / 52 HP", etc.)
        hp_match = re.search(r'(\d{1,3})\s*/\s*(\d{1,3})\s*H?\s*P?', cleaned)
        if hp_match:
            max_hp = int(hp_match.group(2))
            if 10 <= max_hp <= 500:
                log.debug("HP via PaddleOCR: %d", max_hp)
                return max_hp

        # PaddleOCR often drops "/" — look for repeated number pattern like "7070" = "70/70"
        # Also handles "7070P", "7070HP", etc.
        digits_only = re.sub(r'[^0-9]', '', cleaned)
        if len(digits_only) >= 4:
            # HP format is "cur/max" where cur==max (full HP) or cur<max
            # Try splitting digits in half — if both halves are equal, that's HP
            mid = len(digits_only) // 2
            left = digits_only[:mid]
            right = digits_only[mid:]
            if left == right:
                max_hp = int(right)
                if 10 <= max_hp <= 500:
                    log.debug("HP via PaddleOCR (split): %d", max_hp)
                    return max_hp

        # Try just extracting two numbers separated by /
        hp_match2 = re.search(r'(\d{1,3})\s*/\s*(\d{1,3})', cleaned)
        if hp_match2:
            max_hp = int(hp_match2.group(2))
            if 10 <= max_hp <= 500:
                log.debug("HP via PaddleOCR (loose): %d", max_hp)
                return max_hp

    except Exception as e:
        log.debug("PaddleOCR HP failed: %s", e)

    return -1


# Known size tag labels
SIZE_TAGS = {"LIGHTEST", "HEAVIEST", "SHORTEST", "TALLEST", "XXS", "XXL"}


def read_size_label(image: Image.Image, region: BBox) -> str:
    """Read a weight/height label to detect size tags.

    Returns one of: 'LIGHTEST', 'HEAVIEST', 'SHORTEST', 'TALLEST', or '' (normal).
    """
    crop = image.crop(region.as_tuple())
    gray = cv2.cvtColor(np.array(crop), cv2.COLOR_RGB2GRAY)

    if gray.shape[0] < 40:
        scale = 3
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    # These labels can be dark text on light bg OR gold text on light bg
    # Use adaptive approach: try both normal and inverted
    _, binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)
    text = pytesseract.image_to_string(binary, config="--psm 7").strip().upper()

    # Clean and match
    text = re.sub(r"[^A-Z]", "", text)

    for tag in SIZE_TAGS:
        if tag in text:
            log.debug("Size label: '%s' → %s", text, tag)
            return tag

    # Check for partial matches (OCR might misread)
    if "LIGHT" in text:
        return "LIGHTEST"
    if "HEAV" in text:
        return "HEAVIEST"
    if "SHORT" in text:
        return "SHORTEST"
    if "TALL" in text:
        return "TALLEST"

    log.debug("Size label: '%s' → normal", text)
    return ""
