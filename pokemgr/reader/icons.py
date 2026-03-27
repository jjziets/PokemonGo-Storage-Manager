"""Icon detection for shiny, shadow, favorite, lucky, and gender states."""

import logging
import cv2
import numpy as np
from PIL import Image

from ..calibration.regions import BBox

log = logging.getLogger(__name__)


def _region_to_hsv(image: Image.Image, region: BBox) -> np.ndarray:
    """Crop a region and convert to HSV."""
    crop = np.array(image.crop(region.as_tuple()))
    return cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)


def is_shiny(image: Image.Image, region: BBox) -> bool:
    """Detect the shiny sparkle icon (3 bright stars near the name).

    Shiny sparkles are small bright colored points on a light/green background.
    Key: they have COLOR (saturation > 0), unlike the plain white/light
    background which has zero saturation. Pure white = not shiny.

    NOTE: Sparkles animate/flicker — a single frame may miss them.
    Use check_shiny_multi() with multiple screenshots for reliable detection.
    """
    crop = np.array(image.crop(region.as_tuple()))
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)

    # Shiny sparkles: bright (V > 200) AND colorful (S > 30)
    sparkle_mask = (hsv[:, :, 2] > 200) & (hsv[:, :, 1] > 30)
    sparkle_ratio = np.sum(sparkle_mask) / sparkle_mask.size

    is_detected = sparkle_ratio > 0.03
    log.debug("Shiny check: sparkle_ratio=%.3f → %s", sparkle_ratio, is_detected)
    return is_detected


def check_shiny_multi(adb_controller, region: BBox, num_captures: int = 3,
                      delay: float = 0.15) -> bool:
    """Take multiple rapid screenshots to catch flickering shiny sparkles.

    Returns True if ANY screenshot shows the shiny indicator.
    """
    import time
    for i in range(num_captures):
        img = adb_controller.screencap()
        if is_shiny(img, region):
            log.debug("Shiny detected on capture %d/%d", i + 1, num_captures)
            return True
        if i < num_captures - 1:
            time.sleep(delay)
    return False


def is_shadow(image: Image.Image, region: BBox) -> bool:
    """Detect shadow Pokemon by their dark blue/purple background.

    Shadow Pokemon have a distinctly dark background (avg brightness < 100)
    compared to normal Pokemon which have a light green/blue background (> 150).
    We sample the background area behind the Pokemon.
    """
    # Sample the top-left background area (always visible, not covered by Pokemon)
    w, h = image.size
    # Use a region in the upper-left that's always background
    bg_crop = np.array(image.crop((int(w * 0.05), int(h * 0.05), int(w * 0.25), int(h * 0.15))))

    avg_brightness = float(np.mean(bg_crop))

    is_detected = avg_brightness < 110  # shadow backgrounds are very dark
    log.debug("Shadow check: avg_brightness=%.0f → %s", avg_brightness, is_detected)
    return is_detected


def is_favorited(image: Image.Image, region: BBox) -> bool:
    """Detect if the favorite star is filled (gold) vs outline (gray).

    Gold star: high red and green channels, low blue → yellow hue in HSV.
    """
    crop = np.array(image.crop(region.as_tuple()))
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)

    # Gold/yellow in HSV: hue ~15-35, high saturation, high value
    gold_mask = (
        (hsv[:, :, 0] > 15) & (hsv[:, :, 0] < 35) &
        (hsv[:, :, 1] > 100) &
        (hsv[:, :, 2] > 150)
    )
    gold_ratio = np.sum(gold_mask) / gold_mask.size

    is_detected = gold_ratio > 0.10
    log.debug("Favorite check: gold_ratio=%.3f → %s", gold_ratio, is_detected)
    return is_detected


def is_lucky(image: Image.Image, region: BBox) -> bool:
    """Detect the lucky Pokemon indicator (sparkly gold background).

    Lucky Pokemon have a distinct golden shimmer in their background.
    """
    hsv = _region_to_hsv(image, region)

    # Gold/warm: hue ~10-40, decent saturation, high brightness
    lucky_mask = (
        (hsv[:, :, 0] > 10) & (hsv[:, :, 0] < 40) &
        (hsv[:, :, 1] > 40) &
        (hsv[:, :, 2] > 160)
    )
    lucky_ratio = np.sum(lucky_mask) / lucky_mask.size

    is_detected = lucky_ratio > 0.15
    log.debug("Lucky check: lucky_ratio=%.3f → %s", lucky_ratio, is_detected)
    return is_detected


def is_dynamax_or_gmax(image: Image.Image, region: BBox) -> bool:
    """Detect the Dynamax/Gigantamax purple circle icon.

    Both Dynamax and Gmax show a purple circular badge (R~120-160, G~60-100, B~140-180)
    in the area between the type icons and the stardust row. The icon position
    varies if Mega Evolve is available, so we scan the whole region.
    """
    crop = np.array(image.crop(region.as_tuple()))
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)

    # The purple icon: hue ~130-155 in OpenCV (purple/magenta), high saturation
    purple_mask = (
        (hsv[:, :, 0] > 125) & (hsv[:, :, 0] < 160) &
        (hsv[:, :, 1] > 40) &
        (hsv[:, :, 2] > 80)
    )
    purple_ratio = np.sum(purple_mask) / purple_mask.size

    is_detected = purple_ratio > 0.02  # small icon in a large scan area
    log.debug("Dynamax check: purple_ratio=%.4f → %s", purple_ratio, is_detected)
    return is_detected


def detect_gender(image: Image.Image, region: BBox) -> str:
    """Detect the gender symbol near the Pokemon name/HP area.

    Returns 'male', 'female', or 'none' (genderless).
    Both symbols are a pale gray-blue (HSV hue ~96, low saturation).
    We detect the symbol presence first, then distinguish by shape:
      ♂ (male) has an arrow pointing up-right — more pixels in top-right
      ♀ (female) has a cross pointing down — more pixels in bottom-center
    """
    crop = np.array(image.crop(region.as_tuple()))
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)

    h, w = gray.shape

    # The gender symbol is darker than the white background
    # Background is ~255, symbol is ~190-220
    symbol_mask = gray < 230

    symbol_ratio = np.sum(symbol_mask) / symbol_mask.size
    log.debug("Gender check: symbol_ratio=%.3f", symbol_ratio)

    if symbol_ratio < 0.03:
        return "none"  # no symbol visible

    # Split into quadrants to determine shape
    mid_y, mid_x = h // 2, w // 2
    top_right = np.sum(symbol_mask[:mid_y, mid_x:])
    bottom_center = np.sum(symbol_mask[mid_y:, mid_x//2:mid_x + mid_x//2])
    top_left = np.sum(symbol_mask[:mid_y, :mid_x])

    log.debug("Gender quadrants: top_right=%d, bottom_center=%d, top_left=%d",
              top_right, bottom_center, top_left)

    # ♂ has the arrow in top-right, ♀ has the cross at bottom
    if top_right > bottom_center and top_right > top_left:
        return "male"
    elif bottom_center > top_right:
        return "female"

    return "none"
