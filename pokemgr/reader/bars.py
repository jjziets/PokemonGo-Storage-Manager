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
    arr = np.array(image)
    h, w = arr.shape[:2]

    # Scale search area based on actual screen size
    sx = w / 968
    sy = h / 2376
    x_start = int(BAR_SEARCH_X_START * sx)
    x_end = int(BAR_SEARCH_X_END * sx)
    y_start = int(BAR_SEARCH_Y_START * sy)
    y_end = min(int(BAR_SEARCH_Y_END * sy), h - 1)

    # Sample at x midpoint of bar area
    sample_x = (x_start + x_end) // 2

    # Scan vertically: for each row, check if it's a bar row
    bar_rows = []
    for y in range(y_start, y_end):
        # Sample a few pixels across the bar width
        bar_count = 0
        for x in range(x_start + 20, x_end - 20, 30):
            if x < w:
                r, g, b = arr[y, x, :3]
                if _is_bar_pixel(float(r), float(g), float(b)):
                    bar_count += 1
        # If most samples are bar pixels, this is a bar row
        if bar_count >= 3:
            bar_rows.append(y)

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


def are_bars_present(image: Image.Image) -> bool:
    """Quick check: are IV bars visible in the image?"""
    return find_bars(image) is not None
