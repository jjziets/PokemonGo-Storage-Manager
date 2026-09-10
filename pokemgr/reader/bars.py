"""IV bar reading via pixel color sampling.

Pokemon Go IV bars use orange fill (R~242, G~166, B~74) or pink/red for max
(R~222, G~125, B~128) against a gray unfilled background (R~226, G~226, B~224).

The bar Y positions shift based on Pokemon name length and professor text,
so we DYNAMICALLY locate them by scanning for colored horizontal lines.
"""

import logging
import numpy as np
from PIL import Image

from ..calibration.regions import BBox

log = logging.getLogger(__name__)

# TRACEWEAVER: file-role=iv-bar-settling; req=REQ-SCAN-001; trace=TRACE-SCAN-001; ver=VER-SCAN-001
NUM_SEGMENTS = 15  # IV bars have 15 segments (values 0-15)

# Bar region to scan within (X range where bars appear, Y range to search)
# These define the search area, not the exact bar positions
BAR_SEARCH_X_START = 80
BAR_SEARCH_X_END = 480
BAR_SEARCH_Y_START = 1700
BAR_SEARCH_Y_END = 2150


def _is_bar_pixel(r: float, g: float, b: float) -> bool:
    """Check if a pixel is part of an IV bar (filled orange/pink OR unfilled gray).

    Strict matching to avoid false positives from star rating circle,
    Power Up button, and other UI elements.
    """
    # Orange filled bar: R=230-250, G=150-180, B=60-90
    if 225 < r < 255 and 145 < g < 185 and 55 < b < 100:
        return True

    # Pink/red max stat bar: R=210-235, G=115-140, B=115-140
    if 205 < r < 240 and 110 < g < 145 and 110 < b < 145:
        return True

    # Gray unfilled bar: R≈G≈B ≈ 224-228
    if 218 < r < 232 and 218 < g < 232 and 218 < b < 232:
        spread = max(r, g, b) - min(r, g, b)
        if spread < 10:
            return True

    return False


def _is_filled(r: float, g: float, b: float) -> bool:
    """Check if a pixel is filled (orange or pink) vs gray/unfilled."""
    if r < 200:
        return False
    spread = max(r, g, b) - min(r, g, b)
    return spread > 30


def find_bars(image: Image.Image) -> list[BBox] | None:
    """Dynamically locate the 3 IV bars in the appraisal screen.

    Scans vertically in the expected region for rows of bar-colored pixels.
    Returns list of 3 BBox (ATK, DEF, STA) or None if not found.
    """
    arr = np.asarray(image)
    h, w = arr.shape[:2]

    # Scale search area based on actual screen size
    sx = w / 968
    sy = h / 2376
    x_start = int(BAR_SEARCH_X_START * sx)
    x_end = int(BAR_SEARCH_X_END * sx)
    y_start = int(BAR_SEARCH_Y_START * sy)
    y_end = min(int(BAR_SEARCH_Y_END * sy), h - 1)

    # Apply the same color predicate to the sparse sample grid in one NumPy
    # operation. This keeps the exact sample locations and three-pixel rule
    # while avoiding thousands of Python pixel visits per independent frame.
    sample_x = np.arange(x_start + 20, x_end - 20, 30)
    samples = arr[y_start:y_end, sample_x[sample_x < w], :3]
    r, g, b = samples[..., 0], samples[..., 1], samples[..., 2]
    orange = ((r > 225) & (r < 255) & (g > 145) & (g < 185)
              & (b > 55) & (b < 100))
    pink = ((r > 205) & (r < 240) & (g > 110) & (g < 145)
            & (b > 110) & (b < 145))
    gray = ((r > 218) & (r < 232) & (g > 218) & (g < 232)
            & (b > 218) & (b < 232)
            & (samples.max(axis=2) - samples.min(axis=2) < 10))
    bar_rows = (np.flatnonzero((orange | pink | gray).sum(axis=1) >= 3)
                + y_start).tolist()

    if not bar_rows:
        return None

    # Cluster consecutive bar rows into groups (each bar is ~15-20px tall)
    bars = []
    current_group = [bar_rows[0]]
    for y in bar_rows[1:]:
        if y - current_group[-1] <= 3:  # consecutive or near-consecutive
            current_group.append(y)
        else:
            if len(current_group) >= 5:  # bar must be at least 5px tall
                bars.append(current_group)
            current_group = [y]
    if len(current_group) >= 5:
        bars.append(current_group)

    if len(bars) < 3:
        log.debug("Found %d bar groups (need 3): rows=%s", len(bars),
                  [f"{g[0]}-{g[-1]}" for g in bars])
        return None

    # Take the first 3 groups = ATK, DEF, STA
    # For each bar, find the actual X start and end by scanning horizontally
    result = []
    for group in bars[:3]:
        y_top = group[0]
        y_bot = group[-1]
        y_mid = (y_top + y_bot) // 2
        bar_height = y_bot - y_top + 1

        # Scan to find actual bar start (first bar pixel) and end (last bar pixel)
        bar_x_start = x_start
        bar_x_end = x_end
        for x in range(x_start, x_end):
            r, g, b = float(arr[y_mid, x, 0]), float(arr[y_mid, x, 1]), float(arr[y_mid, x, 2])
            if _is_bar_pixel(r, g, b):
                bar_x_start = x
                break
        for x in range(x_end - 1, x_start, -1):
            r, g, b = float(arr[y_mid, x, 0]), float(arr[y_mid, x, 1]), float(arr[y_mid, x, 2])
            if _is_bar_pixel(r, g, b):
                bar_x_end = x + 1
                break

        result.append(BBox(x=bar_x_start, y=y_top, w=bar_x_end - bar_x_start, h=bar_height))

    log.debug("Found bars at: %s",
              [f"y={b.y}-{b.y2}" for b in result])
    return result


def read_iv_bar(image: Image.Image, bar_region: BBox) -> tuple[int, float]:
    """Read an IV bar value (0-15) by sampling pixel colors.

    Returns (value, confidence).
    """
    crop = np.array(image.crop(bar_region.as_tuple()))
    bar_height = crop.shape[0]
    bar_width = crop.shape[1]

    y_center = bar_height // 2
    segment_width = bar_width / NUM_SEGMENTS
    filled_count = 0
    segment_colors = []

    for i in range(NUM_SEGMENTS):
        x = int(segment_width * i + segment_width / 2)
        x = min(x, bar_width - 1)

        y_start = max(0, y_center - 1)
        y_end = min(bar_height, y_center + 2)

        patch = crop[y_start:y_end, max(0, x - 1):min(bar_width, x + 2)]
        avg_r = float(np.mean(patch[:, :, 0]))
        avg_g = float(np.mean(patch[:, :, 1]))
        avg_b = float(np.mean(patch[:, :, 2]))

        is_f = _is_filled(avg_r, avg_g, avg_b)
        segment_colors.append((avg_r, avg_g, avg_b, is_f))

        if is_f:
            filled_count += 1

    # Confidence: filled segments should be contiguous from left
    contiguous = True
    found_unfilled = False
    for _, _, _, filled in segment_colors:
        if not filled:
            found_unfilled = True
        elif found_unfilled:
            contiguous = False
            break

    confidence = 0.95 if contiguous else 0.5
    return filled_count, confidence


def read_bars_dynamic(image: Image.Image) -> dict | None:
    """Find bars dynamically and read all 3 IV values.

    Returns dict with atk, def_, sta, confidence or None if bars not found.
    """
    bar_boxes = find_bars(image)
    if bar_boxes is None or len(bar_boxes) < 3:
        return None

    atk, atk_c = read_iv_bar(image, bar_boxes[0])
    def_, def_c = read_iv_bar(image, bar_boxes[1])
    sta, sta_c = read_iv_bar(image, bar_boxes[2])

    return {
        "atk": atk,
        "def_": def_,
        "sta": sta,
        "confidence": (atk_c + def_c + sta_c) / 3,
    }


# TRACEWEAVER: entrypoint=appraisal_bars_stable; req=REQ-SCAN-001; trace=TRACE-SCAN-001; ver=VER-SCAN-001
def appraisal_bars_stable(first: Image.Image, second: Image.Image, *,
                          fallback_regions=None) -> bool:
    """Require stable values, fill edges and colors on all three IV bars.

    Whole-card averages can hide a narrow bar still filling. Compare its
    central three rows directly, allowing at most one scaled pixel of fill
    jitter and a mean RGB difference of 3 for compression noise. This rejects
    movement within a rounded IV and the orange-to-pink maximum transition.
    Calibrated regions are used only when dynamic detection is unavailable.
    """
    if first is second or first.size != second.size:
        return False
    first_boxes = find_bars(first)
    second_boxes = find_bars(second)
    first_boxes = first_boxes if first_boxes is not None else fallback_regions
    second_boxes = second_boxes if second_boxes is not None else fallback_regions
    if first_boxes is None or second_boxes is None or len(first_boxes) != 3 or len(second_boxes) != 3:
        return False
    width, height = first.size
    x_tolerance = max(1., 2 * width / 968)
    y_tolerance = max(1., 2 * height / 2376)
    fill_tolerance = max(1., width / 968)
    arrays = [np.asarray(image if image.mode == "RGB" else image.convert("RGB"))
              for image in (first, second)]

    def valid(box):
        return (isinstance(box, BBox)
                and all(type(value) is int for value in (box.x, box.y, box.w, box.h))
                and box.w >= 15 and box.h >= 3 and box.x >= 0 and box.y >= 0
                and box.x2 <= width and box.y2 <= height)

    for index, (a, b) in enumerate(zip(first_boxes, second_boxes)):
        if not valid(a) or not valid(b):
            return False
        if index and (a.y < first_boxes[index - 1].y2 or b.y < second_boxes[index - 1].y2):
            return False
        if (max(abs(a.x - b.x), abs(a.w - b.w)) > x_tolerance
                or max(abs(a.y - b.y), abs(a.h - b.h)) > y_tolerance):
            return False
        value_a, confidence_a = read_iv_bar(first, a)
        value_b, confidence_b = read_iv_bar(second, b)
        if value_a != value_b or min(confidence_a, confidence_b) < .9:
            return False
        # Shared screen coordinates avoid introducing interpolation or moving
        # a fill edge merely because the detected rounded cap shifts one pixel.
        left, right = max(a.x, b.x), min(a.x2, b.x2)
        middle = round((a.y + a.h / 2 + b.y + b.h / 2) / 2)
        top, bottom = max(a.y, b.y, middle - 1), min(a.y2, b.y2, middle + 2)
        if right - left < 15 or bottom - top < 2:
            return False
        profiles = [array[top:bottom, left:right].mean(axis=0) for array in arrays]
        filled = []
        for profile in profiles:
            # White empty calibration boxes must not masquerade as three
            # stable zero-IV bars. Require the expected bar palette itself.
            if sum(_is_bar_pixel(*rgb) for rgb in profile) < .8 * len(profile):
                return False
            filled.append((profile[:, 0] >= 200) & (np.ptp(profile, axis=1) > 30))
        if abs(int(filled[0].sum()) - int(filled[1].sum())) > fill_tolerance:
            return False
        edges = [int(np.flatnonzero(mask)[-1]) if mask.any() else -1 for mask in filled]
        if abs(edges[0] - edges[1]) > fill_tolerance:
            return False
        if float(np.abs(profiles[0] - profiles[1]).mean()) > 3.:
            return False
    return True


def are_bars_present(image: Image.Image) -> bool:
    """Quick check: are IV bars visible in the image?"""
    return find_bars(image) is not None
