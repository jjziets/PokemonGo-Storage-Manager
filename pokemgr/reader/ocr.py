"""OCR for reading Pokemon species names and CP values."""

import re
import logging
from collections.abc import Collection
import cv2
import numpy as np
import pytesseract
from PIL import Image

from ..calibration.regions import BBox, is_tablet_layout
from .. import timing

log = logging.getLogger(__name__)


@timing.timed("reader.name")
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


@timing.timed("reader.cp")
def read_cp(image: Image.Image, region: BBox, *,
            expected_cps: Collection[int] | None = None,
            fast: bool = False) -> tuple[int, float]:
    """Read the CP value from the detail screen.

    CP is white text on a gradient/colored background.
    Returns (cp_value, confidence). Returns (-1, 0.0) on failure.

    Recovery may supply exact CP candidates from already confirmed identity
    evidence. In that mode all Tesseract variants must agree on at most one
    observed candidate; no missing digits or font substitutions are inferred.
    ``fast`` limits an unconstrained primary read to grayscale PSM 8 so the
    scanner can try HP/IV calculation before spending time on OCR recovery.
    Exact-candidate recovery always remains exhaustive, regardless of ``fast``.
    """
    crop = np.array(image.crop(region.as_tuple()))

    # Fast path: Tesseract first (~10ms)
    scale = 5
    crop_5x = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    crop_rgb = crop_5x[:, :, :3] if crop_5x.shape[2] == 4 else crop_5x
    gray = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2GRAY)
    white_mask = np.all(crop_5x > 200, axis=2).astype(np.uint8) * 255

    if expected_cps is not None:
        expected = frozenset(expected_cps)
        if not expected:
            return -1, 0.0
        observed: dict[int, float] = {}

        def observe_exact(text, confidence):
            # Require the entire CP token. In particular, do not take a
            # suffix from a five-digit OCR run, join separated fragments, or
            # repair A to 4 merely because that would fit an expected value.
            match = re.fullmatch(
                r"(?:c?p\s*)?([1-9][0-9]{1,3})", text.strip(), re.IGNORECASE,
            )
            if match:
                cp = int(match.group(1))
                if cp in expected:
                    observed[cp] = max(confidence, observed.get(cp, 0.0))

        for prepared, psm, confidence in (
            (gray, 8, 0.8), (gray, 7, 0.75),
            (white_mask, 8, 0.72), (white_mask, 7, 0.68),
        ):
            observe_exact(
                pytesseract.image_to_string(prepared, config=f"--psm {psm}"),
                confidence,
            )

        if len(observed) > 1:
            log.warning("Conflicting exact CP observations: %s", sorted(observed))
            return -1, 0.0
        if not observed:
            # The numeric Paddle helper concatenates digit fragments. Parse
            # its raw text instead, under the same whole-token requirement.
            try:
                from .ocr_engine import read_text_paddle
                paddle_rgb = crop[:, :, :3] if crop.shape[2] == 4 else crop
                observe_exact(read_text_paddle(paddle_rgb), 0.9)
            except Exception as exc:
                log.debug("Exact CP Paddle fallback failed: %s", exc)
        if observed:
            cp, confidence = next(iter(observed.items()))
            log.debug("CP via exact recovery observations: %d", cp)
            return cp, confidence
        return -1, 0.0

    # The CP crop is a single visual token.  PSM 7 (single line) can clip the
    # last digit on wide tablet frames (for example CP567 -> CP56), while PSM 8
    # reads the same mask correctly and remains accurate on narrow phones.
    def _parse_cp_digits(text: str) -> str:
        """Repair the evidenced Pokemon Go font confusion before parsing.

        Tesseract reads the thin middle 4 in ``CP747`` as ``CP7A7``.  A full
        digit whitelist is unsafe because it can reinterpret the literal
        ``CP`` label as ``61`` (CP1333 -> 61333).  Only repair an A between two
        digits, which targets the exact evidenced pattern without promoting a
        partial transition such as ``A7`` to a plausible CP47.
        """
        repaired = re.sub(r"(?i)(?<=\d)a(?=\d)", "4", text)
        compact = re.sub(r"\s+", "", repaired)

        # Prefer digits explicitly following the CP/P label, but retain a
        # digits-only fallback for OCR engines that omit that label entirely.
        labelled = re.search(r"(?i)c?p([0-9]+)", compact)
        if labelled and 2 <= len(labelled.group(1)) <= 4:
            return labelled.group(1)

        # Never concatenate separate digit runs across OCR noise.  For example
        # ``-6p1333`` contains a stray 6 plus the real CP1333; stripping every
        # non-digit would manufacture CP61333.  A raw five-digit run is also
        # rejected so the independent PSM/fallback gets a turn.
        valid_runs = [run for run in re.findall(r"[0-9]+", repaired)
                      if 2 <= len(run) <= 4]
        if not valid_runs:
            return ""
        return max(valid_runs,
                   key=lambda run: (len(run), repaired.rfind(run)))

    # Preserve grayscale contrast before trying a hard white mask.  Bright
    # appraisal backgrounds can merge a real digit with a nearby bokeh circle
    # in the mask (the live CP369 frame became CP3869), while grayscale reads
    # the same visible glyphs as 369.
    full_text = pytesseract.image_to_string(
        gray, config="--psm 8"
    ).strip()

    digits = _parse_cp_digits(full_text)
    if digits:
        cp = int(digits)
        if cp >= 10:
            log.debug("CP via Tesseract (fast): %d", cp)
            return cp, 0.8
        log.debug("Rejected one-digit CP candidate: %d", cp)

    if fast:
        return -1, 0.0

    # Preserve compatibility with unusually spaced/narrow phone crops where
    # Tesseract's single-line segmentation can still outperform single-word.
    line_text = pytesseract.image_to_string(
        gray, config="--psm 7"
    ).strip()
    digits = _parse_cp_digits(line_text)
    if digits:
        cp = int(digits)
        if cp >= 10:
            log.debug("CP via Tesseract (line fallback): %d", cp)
            return cp, 0.75
        log.debug("Rejected one-digit CP line candidate: %d", cp)

    # Hard-mask fallback remains useful on low-contrast or very busy
    # backgrounds where grayscale segmentation cannot isolate the white text.
    for psm, confidence in ((8, 0.72), (7, 0.68)):
        masked_text = pytesseract.image_to_string(
            white_mask, config=f"--psm {psm}"
        ).strip()
        digits = _parse_cp_digits(masked_text)
        if digits:
            cp = int(digits)
            if cp >= 10:
                log.debug("CP via white-mask fallback: %d", cp)
                return cp, confidence

    # Slow path: PaddleOCR fallback (~250ms, better accuracy)
    try:
        from .ocr_engine import read_number_paddle
        paddle_rgb = crop[:, :, :3] if crop.shape[2] == 4 else crop
        paddle_cp = read_number_paddle(paddle_rgb)
        if paddle_cp >= 10:
            log.debug("CP via PaddleOCR (fallback): %d", paddle_cp)
            return paddle_cp, 0.9
    except Exception:
        pass

    log.warning("Failed to read CP from text: word='%s' line='%s'", full_text, line_text)
    return -1, 0.0


@timing.timed("reader.caught_species")
def read_caught_species(image: Image.Image, screen_width: int, screen_height: int,
                        density: int | None = None) -> str:
    """Read the real species name from the professor's speech bubble.

    The bubble says 'This [Species] was caught on [date]...'
    Uses PaddleOCR for best accuracy on the speech bubble text.
    Returns the species name or empty string if not found.
    """
    arr = np.array(image)
    w, h = image.size

    # Crop the speech bubble area.  On wide tablets the bubble is much taller
    # than on narrow phones; the old last-220px crop cut off "This <species>"
    # and left only the location line.
    is_tablet = is_tablet_layout(w, h, density)
    # Current narrow-phone appraisal bubbles are also substantially taller
    # than 220 px.  On the Fold cover screen, the old last-220px crop started
    # below "This <species> was" and made authoritative species recovery
    # impossible for large models whose CP is hidden.
    bubble_start = int(h * (0.72 if is_tablet else 0.80))
    bubble_end = int(h * 0.94) if is_tablet else h - 10
    bubble = arr[bubble_start:bubble_end, 20:w - 20]
    if bubble.shape[2] == 4:
        bubble = bubble[:, :, :3]
    bubble = bubble.copy()  # ensure contiguous

    def _extract_species(text: str) -> str:
        # OCR can split the line as "This" + "s Abra was".  Tolerate one
        # stray single-letter edge artifact but require both anchor words so
        # appraisal labels cannot be mistaken for identity evidence.
        match = re.search(
            r"\bThis\s+(.{2,40}?)\s+was\b",
            text,
            re.IGNORECASE,
        )
        if not match:
            return ""
        candidate = re.sub(r"\s+", " ", match.group(1)).strip()
        parts = candidate.split(" ")
        if len(parts) > 1 and len(parts[0]) == 1 and parts[0].isalpha():
            candidate = " ".join(parts[1:])
        allowed_punctuation = set(" .'-:♀♂%’")
        if not candidate or not all(
                char.isalpha() or char.isdigit() or char in allowed_punctuation
                for char in candidate):
            return ""
        return candidate

    # This speech-bubble line is high contrast and Tesseract reads it reliably
    # in a few milliseconds on the supplied tablet frames.  Make it the fast
    # path; Paddle remains the fallback for unusual fonts or split detections.
    try:
        gray = cv2.cvtColor(bubble, cv2.COLOR_RGB2GRAY)
        tesseract_text = pytesseract.image_to_string(
            gray, config="--psm 11"
        ).strip()
        species = _extract_species(tesseract_text)
        if species:
            log.debug("Caught species via Tesseract: '%s' (text='%s')",
                      species, tesseract_text)
            return species
    except Exception as e:
        log.debug("Tesseract caught-species read failed: %s", e)

    try:
        from .ocr_engine import read_text_paddle
        # PaddleOCR returns all text lines — find the one with "This [Species]"
        from .ocr_engine import _get_paddle
        reader = _get_paddle()
        if reader:
            result = reader.ocr(bubble)
            if result:
                texts = []
                for item in result:
                    if isinstance(item, dict) and 'rec_texts' in item:
                        texts.extend(str(text) for text in item['rec_texts'] if text)

                # Paddle can split the sentence into adjacent OCR items.
                combined = " ".join(texts)
                species = _extract_species(combined)
                if species:
                    log.debug("Caught species from bubble: '%s' (text='%s')",
                              species, combined)
                    return species
    except Exception as e:
        log.debug("Failed to read caught species: %s", e)

    return ""


_gym_template = None
_gym_template_gray = None

@timing.timed("reader.gym")
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


@timing.timed("reader.hp")
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
    h, w = arr.shape[:2]
    for y in range(int(h * 0.35), int(h * 0.70)):
        row = arr[y, int(w * 0.20):int(w * 0.80), :3]
        # Green HP bar.  Appraisal overlays tint the normal 109/235/180 bar to
        # roughly 160/226/156, so detect green dominance rather than a narrow
        # absolute RGB range.
        row_i = row.astype(np.int16)
        green_mask = ((row_i[:, 1] > 200) &
                      ((row_i[:, 1] - row_i[:, 0]) > 30) &
                      ((row_i[:, 1] - row_i[:, 2]) > 30))
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

    # Read the whole text line first.  A generous crop is more reliable on
    # tablets than sliding short 25px windows, which can clip the top of a 3
    # and turn e.g. 130 into 120.
    line_crop = image.crop((
        int(w * 0.25),
        bar_y + max(10, int(h * 0.006)),
        int(w * 0.75),
        min(h, bar_y + max(80, int(h * 0.045))),
    ))
    line_gray = cv2.cvtColor(np.array(line_crop), cv2.COLOR_RGB2GRAY)
    line_gray = cv2.resize(line_gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    _, line_binary = cv2.threshold(line_gray, 210, 255, cv2.THRESH_BINARY_INV)
    line_text = pytesseract.image_to_string(line_binary, config="--psm 7").strip()
    line_match = re.search(r'(\d{1,3})\s*/\s*(\d{2,3})\s*H\s*P', line_text, re.IGNORECASE)
    if line_match:
        max_hp = int(line_match.group(2))
        if 10 <= max_hp <= 500:
            log.debug("HP read (full line): %d (text='%s', bar_y=%d)", max_hp, line_text, bar_y)
            return max_hp

    # The appraisal tint can obscure the trailing "HP" while leaving a clear
    # full-health value (for example "90/90").  This crop is anchored directly
    # below a detected HP bar, so accepting equal repeated values is safe and
    # avoids several guaranteed-failure OCR retries.
    repeated_match = re.search(r'(\d{1,3})\s*/\s*(\d{1,3})', line_text)
    if repeated_match and repeated_match.group(1) == repeated_match.group(2):
        max_hp = int(repeated_match.group(2))
        if 10 <= max_hp <= 500:
            log.debug("HP read (bar-anchored pair): %d (text='%s', bar_y=%d)",
                      max_hp, line_text, bar_y)
            return max_hp

    # Step 2: Read HP text below the bar
    # Try Tesseract first (fast), then PaddleOCR if Tesseract fails or disagrees
    from collections import Counter
    hp_readings = []

    for offset in range(25, 55, 3):
        y_start = bar_y + offset
        crop = image.crop((int(w * 0.25), y_start, int(w * 0.75), y_start + max(25, int(25 * sy))))
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


@timing.timed("reader.hp_paddle")
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


@timing.timed("reader.size_label")
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
