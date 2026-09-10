"""Locate the visible forward triangle on an appraisal, without fixed targets."""

# TRACEWEAVER: file-role=appraisal-forward-target; req=REQ-SCAN-003,REQ-MASS-001; trace=TRACE-SCAN-003; ver=VER-SCAN-001

import cv2
import numpy as np
from PIL import Image

from .bars import find_bars


def next_appraisal_target(image: Image.Image) -> tuple[int, int] | None:
    """Return an interior point of one observed right arrow, or no target.

    The caller must already have confirmed its current appraisal. Three
    detected IV bars anchor a narrow search near DEF; the obsolete calibrated
    next/close coordinate is never consulted. No left arrow is required at
    the first position. Uncertain shape, clipping or multiple arrows fall back
    to the caller's existing navigation rather than inventing a tap target.
    """
    boxes = find_bars(image)
    if boxes is None or len(boxes) != 3:
        return None
    width, height = image.size
    if any(box.w < 15 or box.h < 3 or box.x < 0 or box.y < 0
           or box.x2 > width or box.y2 > height for box in boxes):
        return None
    if any(first.y2 >= second.y for first, second in zip(boxes, boxes[1:])):
        return None

    middle = boxes[1]
    scale = height / 2376
    margin = max(round(48 * scale), middle.h * 3)
    left, top = int(width * .91), max(0, middle.y - margin)
    right, bottom = width, min(height, middle.y2 + margin)
    rgb = np.asarray(image.crop((left, top, right, bottom)).convert("RGB"))
    if not rgb.size:
        return None
    white = ((rgb.min(axis=2) >= 225) & (np.ptp(rgb, axis=2) <= 25)).astype(np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(white)
    targets = []
    expected_y = middle.center[1] - .85 * middle.h
    y_tolerance = max(10 * scale, middle.h)

    for index in range(1, count):
        x, y, w, h, area = (int(value) for value in stats[index])
        # A cropped white panel or half-arrow is not evidence of a button.
        if x <= 0 or y <= 0 or x + w >= white.shape[1] or y + h >= white.shape[0]:
            continue
        if not (.012 * width <= w <= .06 * width
                and .010 * height <= h <= .035 * height
                and .35 <= w / h <= 1.15
                and .40 <= area / (w * h) <= .72):
            continue
        if abs(top + y + h / 2 - expected_y) > y_tolerance:
            continue
        component = (labels[y:y+h, x:x+w] == index).astype(np.uint8)
        contours, _hierarchy = cv2.findContours(
            component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
        )
        if len(contours) != 1:
            continue
        contour = contours[0]
        hull_area = cv2.contourArea(cv2.convexHull(contour))
        if hull_area <= 0 or cv2.contourArea(contour) / hull_area < .9:
            continue
        # Compare a filled triangle, tolerating antialiasing and rounded tips.
        # A left arrow, diamond, rectangle or jacket edge has different mass
        # distribution and cannot satisfy both orientation and overlap.
        ideal = np.zeros_like(component)
        cv2.fillConvexPoly(ideal, np.array(((0, 0), (0, h-1), (w-1, h//2)), np.int32), 1)
        union = np.count_nonzero(component | ideal)
        overlap = np.count_nonzero(component & ideal)
        cx, cy = centroids[index]
        if union == 0 or overlap / union < .72 or cx - x > .44 * w:
            continue
        local_x, local_y = round(cx - x), round(cy - y)
        # Check clearance inside the observed white glyph, not merely its box.
        padded = np.pad(component, 1)
        clearance = cv2.distanceTransform(padded, cv2.DIST_L2, 3)
        if (not 0 <= local_x < w or not 0 <= local_y < h
                or clearance[local_y + 1, local_x + 1] < max(2, 2 * scale)):
            continue
        targets.append((left + x + local_x, top + y + local_y))

    return targets[0] if len(targets) == 1 else None
