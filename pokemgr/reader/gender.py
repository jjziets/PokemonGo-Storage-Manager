"""Gender detection from the gender symbol region.

♂ Male: circle with arrow pointing UP-RIGHT → more dark pixels in top half
♀ Female: circle with cross pointing DOWN → more dark pixels in bottom half
Genderless: no symbol → all white/light pixels
"""

import logging
from functools import lru_cache
import cv2
import numpy as np
from PIL import Image

from ..calibration.regions import BBox

# TRACEWEAVER: file-role=gender-evidence-reader; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
log = logging.getLogger(__name__)

SYMBOL_THRESHOLD = 220  # pixels darker than this are part of the symbol
MIN_SYMBOL_RATIO = 0.02  # at least 2% of region must be symbol to count


def _normalized_glyph(mask):
    ys, xs = np.where(mask)
    cropped = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return cv2.resize(cropped.astype(np.uint8), (64, 64),
                      interpolation=cv2.INTER_NEAREST).astype(bool)


@lru_cache(maxsize=1)
def _gender_templates():
    """Analytic ring/arrow/cross silhouettes; no account image templates."""
    templates = []
    for gender in ("male", "female"):
        for thickness in (4, 5, 6, 7, 8):
            canvas = np.zeros((256, 256), dtype=np.uint8)

            def line(start, end):
                cv2.line(canvas, tuple(v * 4 for v in start),
                         tuple(v * 4 for v in end), 255, thickness * 4)

            if gender == "male":
                cv2.circle(canvas, (80, 132), 68, 255, thickness * 4)
                line((32, 21), (49, 3))
                line((33, 3), (49, 3))
                line((49, 3), (49, 19))
            else:
                cv2.circle(canvas, (68, 68), 56, 255, thickness * 4)
                line((17, 31), (17, 50))
                line((9, 41), (25, 41))
            templates.append((gender, _normalized_glyph(canvas > 0)))
    return tuple(templates)


# TRACEWEAVER: entrypoint=detect_gender_evidence; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
def detect_gender_evidence(image: Image.Image, region: BBox) -> str:
    """Return an optional complete gender glyph, never a pixel-balance guess.

    The caller supplies a bounded gender area with room around the symbol.
    A clipped crop, dark card, disconnected noise, non-circular opening or
    uncertain arrow/cross remains unavailable. This does not infer genderless.
    """
    if (not isinstance(region, BBox) or region.w <= 0 or region.h <= 0
            or region.x < 0 or region.y < 0
            or region.x2 > image.width or region.y2 > image.height):
        return ""
    rgb = np.asarray(image.crop(region.as_tuple()).convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if np.median(gray) < 240:
        return ""
    mask = (gray < SYMBOL_THRESHOLD).astype(np.uint8)
    ratio = np.count_nonzero(mask) / mask.size
    if not .02 <= ratio <= .35:
        return ""
    components, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if components != 2:
        return ""
    x, y, width, height, _area = stats[1]
    margin = max(2, round(min(region.w, region.h) * .02))
    if (width < 8 or height < 12 or x < margin or y < margin
            or x + width > region.w - margin or y + height > region.h - margin):
        return ""
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None or len(contours) != 2:
        return ""
    holes = [contour for contour, links in zip(contours, hierarchy[0]) if links[3] >= 0]
    if len(holes) != 1:
        return ""
    hole = holes[0]
    area, perimeter = cv2.contourArea(hole), cv2.arcLength(hole, True)
    _hx, _hy, hw, hh = cv2.boundingRect(hole)
    if (perimeter <= 0 or not .8 <= hw / hh <= 1.2
            or 4 * np.pi * area / perimeter ** 2 < .74
            or not .12 <= area / (width * height) <= .5):
        return ""
    moments = cv2.moments(hole)
    cx = (moments["m10"] / moments["m00"] - x) / width
    cy = (moments["m01"] / moments["m00"] - y) / height
    aspect = width / height
    if .8 <= aspect <= 1.2 and .25 <= cx <= .5 and .45 <= cy <= .8:
        candidate = "male"
        # Both arrowhead arms must exist; a diagonal tail alone is not male.
        features = ((.58, .0, .77, .16), (.84, .19, 1., .38))
    elif .5 <= aspect <= .85 and .4 <= cx <= .6 and .2 <= cy <= .45:
        candidate = "female"
        # Both cross arms must exist; a ring with a vertical tail is not female.
        features = ((.18, .69, .4, .84), (.6, .69, .82, .84))
    else:
        return ""
    normalized = _normalized_glyph(mask)
    for x1, y1, x2, y2 in features:
        if np.mean(normalized[int(y1 * 64):int(y2 * 64), int(x1 * 64):int(x2 * 64)]) < .3:
            return ""
    scores = {"male": 0., "female": 0.}
    for gender, template in _gender_templates():
        score = np.count_nonzero(normalized & template) / np.count_nonzero(normalized | template)
        scores[gender] = max(scores[gender], score)
    other = "female" if candidate == "male" else "male"
    return candidate if scores[candidate] >= .78 and scores[candidate] - scores[other] >= .25 else ""


def detect_gender(image: Image.Image, region: BBox) -> str:
    """Detect gender from the symbol region.

    Returns 'male', 'female', or 'none'.
    """
    crop = np.array(image.crop(region.as_tuple()))
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)

    # Find dark pixels (the symbol)
    mask = gray < SYMBOL_THRESHOLD
    symbol_ratio = np.sum(mask) / mask.size

    if symbol_ratio < MIN_SYMBOL_RATIO:
        return "none"  # no symbol visible

    # Find bounding box of symbol
    coords = np.where(mask)
    y_min, y_max = coords[0].min(), coords[0].max()
    x_min, x_max = coords[1].min(), coords[1].max()

    if y_max - y_min < 5 or x_max - x_min < 5:
        return "none"  # too small, noise

    # Compare top vs bottom half of the symbol's bounding box
    mid_y = (y_min + y_max) // 2
    top_pixels = np.sum(mask[y_min:mid_y, x_min:x_max])
    bottom_pixels = np.sum(mask[mid_y:y_max, x_min:x_max])

    if top_pixels > bottom_pixels:
        return "male"   # ♂ arrow goes up-right
    else:
        return "female"  # ♀ cross goes down
