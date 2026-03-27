"""Gender detection from the gender symbol region.

♂ Male: circle with arrow pointing UP-RIGHT → more dark pixels in top half
♀ Female: circle with cross pointing DOWN → more dark pixels in bottom half
Genderless: no symbol → all white/light pixels
"""

import logging
import cv2
import numpy as np
from PIL import Image

from ..calibration.regions import BBox

log = logging.getLogger(__name__)

SYMBOL_THRESHOLD = 220  # pixels darker than this are part of the symbol
MIN_SYMBOL_RATIO = 0.02  # at least 2% of region must be symbol to count


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
