"""Read the Power Up preview without exposing its confirmation control."""

from collections.abc import Collection
from dataclasses import dataclass
import logging
import re
import unicodedata

import cv2
import numpy as np
from PIL import Image
import pytesseract

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PowerUpPreview:
    current_cp: int | None
    next_cp: int | None
    cancel_target: tuple[int, int]


@dataclass(frozen=True)
class _Word:
    text: str
    confidence: float
    box: tuple[int, int, int, int]
    line: tuple[int, int, int]


def _normalise(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def has_detail_menu(image: Image.Image, target: tuple[int, int]) -> bool:
    """Recognize the uncovered teal menu disk and its three green bars.

    The supplied calibrated tap must fall inside the observed disk. Name/HP
    visible behind a modal are insufficient evidence that this control exists.
    """
    width, height = image.size
    tx, ty = target
    if not (0.65 * width < tx < width and 0.84 * height < ty < height):
        return False
    radius = max(1, round(width * 0.20))
    left, top = max(0, tx-radius), max(0, ty-radius)
    rgb = np.array(image.convert("RGB"), dtype=np.int16)[
        top:min(height, ty+radius), left:min(width, tx+radius),
    ]
    red, green_channel, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    teal = ((red < 100) & (green_channel > 90) & (green_channel < 190)
            & (blue > 90) & (blue < 210) & (np.abs(green_channel-blue) < 70))
    green = ((red > 60) & (red < 210) & (green_channel > 190)
             & (blue > 90) & (blue < 220)
             & (green_channel > red+35) & (green_channel > blue+25))
    kernel = np.ones((max(1, round(width * 0.003)),) * 2, np.uint8)
    mask = cv2.morphologyEx((teal | green).astype(np.uint8), cv2.MORPH_CLOSE, kernel)
    contours, _hierarchy = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    matches = 0
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if not (0.08 * width < w < 0.24 * width and 0.88 < w/h < 1.12):
            continue
        area, perimeter = cv2.contourArea(contour), cv2.arcLength(contour, True)
        if (not perimeter or not 0.65 < area/(w*h) < 0.85
                or 4*np.pi*area/(perimeter*perimeter) < 0.83):
            continue
        if cv2.pointPolygonTest(contour, (tx-left, ty-top), False) < 0:
            continue
        disk = np.zeros(mask.shape, np.uint8)
        cv2.drawContours(disk, [contour], -1, 1, thickness=cv2.FILLED)
        if np.mean(teal[disk.astype(bool)]) < 0.60:
            continue
        _count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
            green[y:y+h, x:x+w].astype(np.uint8),
        )
        bars = []
        for bx, by, bw, bh, _area in stats[1:]:
            if (0.28*w < bw < 0.55*w and 0.025*h < bh < 0.10*h
                    and 4 < bw/bh < 12 and abs(bx+bw/2-w/2) < 0.08*w
                    and 0.25*h < by < 0.70*h):
                bars.append((int(bx), int(by), int(bw), int(bh)))
        if len(bars) != 3:
            continue
        bars.sort(key=lambda bar: bar[1])
        centers = [by+bh/2 for _bx, by, _bw, bh in bars]
        gaps = np.diff(centers)
        if (np.all(gaps > 0.08*h) and np.all(gaps < 0.17*h)
                and abs(gaps[0]-gaps[1]) < 0.035*h
                and max(bar[2] for bar in bars)-min(bar[2] for bar in bars) < 0.07*w):
            matches += 1
    return matches == 1


def _words(image: Image.Image) -> list[_Word]:
    """Locate labels in the lower detail/preview area, retaining image coords."""
    top = int(image.height * 0.57)
    crop = np.array(image.convert("RGB"))[top:]
    crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    data = pytesseract.image_to_data(
        crop, config="--psm 6", output_type=pytesseract.Output.DICT,
    )
    result = []
    for i, text in enumerate(data["text"]):
        if not text.strip():
            continue
        x = round(data["left"][i] / 2)
        y = top + round(data["top"][i] / 2)
        result.append(_Word(
            text.strip(), float(data["conf"][i]),
            (x, y, x + round(data["width"][i] / 2),
             y + round(data["height"][i] / 2)),
            (data["block_num"][i], data["par_num"][i], data["line_num"][i]),
        ))
    return result


def _lines(words: list[_Word]) -> list[list[_Word]]:
    grouped = {}
    for word in words:
        grouped.setdefault(word.line, []).append(word)
    return [sorted(line, key=lambda word: word.box[0]) for line in grouped.values()]


def _heading(line: list[_Word]) -> str | None:
    match = re.fullmatch(r"POWER\s+UP:\s+(.+)", " ".join(w.text for w in line), re.I)
    return _normalise(match.group(1)) if match else None


def _resource_name(lines: list[list[_Word]], height: int) -> str | None:
    words = sorted(
        (word for line in lines for word in line
         if 0.65 * height < word.box[1] < 0.72 * height),
        key=lambda word: (word.line, word.box[0]),
    )
    text = " ".join(word.text for word in words)
    match = re.fullmatch(
        r"Transform a Rare Candy into a (.+) Candy\?", text, re.I,
    )
    if not match:
        return None
    # The captured prompt reads the complete sentence correctly, but gives
    # its short "into a" words low confidence. Require strong recognition of
    # the action/resource anchors and every name word, without fuzzy repair.
    anchors = words[:4] + words[6:]
    if not anchors or min(word.confidence for word in anchors) < 70:
        return None
    return _normalise(match.group(1))


def _cancel_target(lines: list[list[_Word]], width: int,
                   height: int) -> tuple[int, int] | None:
    cancels = [line[0] for line in lines
               if len(line) == 1 and line[0].text.upper() == "CANCEL"
               and line[0].confidence >= 70]
    if len(cancels) != 1:
        return None
    x1, y1, x2, y2 = cancels[0].box
    target = ((x1+x2)//2, (y1+y2)//2)
    if (0.35*width < target[0] < 0.65*width
            and 0.90*height < target[1] < 0.98*height):
        return target
    return None


def find_powerup_button(image: Image.Image) -> tuple[int, int] | None:
    """Find one green, left-column detail button; never a preview confirm."""
    try:
        lines = _lines(_words(image))
    except Exception as exc:
        log.debug("Power Up button OCR failed: %s", exc)
        return None
    # Both screens contain POWER UP. A preview heading disqualifies the
    # entire image before any matching button could become a tap target.
    if any(re.search(r"\bPOWER\s+UP\s*:", " ".join(w.text for w in line), re.I)
           for line in lines):
        return None
    if _resource_name(lines, image.height) is not None:
        return None

    rgb = np.array(image.convert("RGB"), dtype=np.int16)
    w, h = image.size
    targets = []
    for line in lines:
        for first, second in zip(line, line[1:]):
            if (first.text.upper(), second.text.upper()) != ("POWER", "UP"):
                continue
            if min(first.confidence, second.confidence) < 70:
                continue
            x1, y1 = first.box[:2]
            x2, y2 = second.box[2:]
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            if not (0.10 * w < cx < 0.42 * w and 0.59 * h < cy < 0.82 * h):
                continue
            if second.box[0] - first.box[2] > 0.07 * w:
                continue
            pad = max(2, round(0.006 * h))
            patch = rgb[max(0, y1-pad):min(h, y2+pad), max(0, x1-pad):min(w, x2+pad)]
            green = ((patch[:, :, 1] > 150)
                     & (patch[:, :, 1] > patch[:, :, 0] + 20)
                     & (patch[:, :, 1] > patch[:, :, 2] + 10))
            if green.size and np.mean(green) > 0.55:
                targets.append((cx, cy))
    return targets[0] if len(targets) == 1 else None


def _right_arrow(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """Find the separate right-arrow glyph, not a digit in either CP value."""
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask)
    arrows = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if not (h >= 8 and 1.3 < w / h < 2.4 and area > 20):
            continue
        component = labels[y:y+h, x:x+w] == i
        # A right arrow has a narrow horizontal stem on the left and its
        # upper/lower head strokes on the right. This excludes left arrows,
        # minus signs, and the tall disconnected digit glyphs.
        ys, xs = np.where(component)
        left_rows = ys[xs < w * 0.40]
        tips = xs[(ys < h * 0.25) | (ys > h * 0.75)]
        if (left_rows.size and tips.size
                and left_rows.min() >= h * 0.30
                and left_rows.max() <= h * 0.70
                and tips.min() > w * 0.45):
            arrows.append((x, y, x+w, y+h))
    return arrows[0] if len(arrows) == 1 else None


def _cp_pair(image: Image.Image, heading_bottom: int) -> tuple[int, int] | None:
    w, h = image.size
    x0 = int(w * 0.15)
    top, bottom = heading_bottom + int(h * 0.014), heading_bottom + int(h * 0.060)
    rgb = np.array(image.convert("RGB"))[top:bottom, x0:int(w * 0.85)]
    if not rgb.size:
        return None
    dark = (np.max(rgb, axis=2) < 180).astype(np.uint8)
    arrow = _right_arrow(dark)
    if arrow is None or not (0.40 * w < x0 + arrow[0] < 0.65 * w):
        return None
    orange = ((rgb[:, :, 0] > 200) & (rgb[:, :, 1] > 80)
              & (rgb[:, :, 1] < 200) & (rgb[:, :, 2] < 100))
    if np.any(orange[:, :arrow[2]]) or not np.any(orange[:, arrow[2]:]):
        return None

    # The current CP crop ends before the observed arrow. The orange future
    # CP crop begins after it, so the upgraded value cannot become current CP.
    current = (1 - dark[:, :arrow[0]]) * 255
    future = (~orange[:, arrow[2]:]).astype(np.uint8) * 255
    values = []
    for crop, pattern in ((current, r"CP\s*([1-9][0-9]{0,4})"),
                          (future, r"([1-9][0-9]{0,4})")):
        crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        observed = set()
        for psm in (7, 8):
            text = pytesseract.image_to_string(crop, config=f"--psm {psm}").strip()
            match = re.fullmatch(pattern, text, re.I)
            if match:
                observed.add(int(match.group(1)))
        if len(observed) != 1:
            return None
        values.append(observed.pop())
    return tuple(values) if values[0] < values[1] else None


def read_powerup_preview(
    image: Image.Image, expected_display_name: str | Collection[str],
) -> PowerUpPreview | None:
    """Recognize this Pokemon's preview; preserve CANCEL if CP is unreadable."""
    names = ([expected_display_name] if isinstance(expected_display_name, str)
             else expected_display_name)
    expected = {_normalise(name) for name in names if name.strip()}
    if not expected:
        return None
    try:
        lines = _lines(_words(image))
    except Exception as exc:
        log.debug("Power Up preview OCR failed: %s", exc)
        return None
    w, h = image.size
    headings = [line for line in lines if _heading(line) is not None]
    if len(headings) != 1 or _heading(headings[0]) not in expected:
        return None
    heading = headings[0]
    if (min(word.confidence for word in heading) < 70
            or not all(0.64 * h < word.box[1] < 0.73 * h for word in heading)):
        return None
    target = _cancel_target(lines, w, h)
    if target is None:
        return None
    try:
        pair = _cp_pair(image, max(word.box[3] for word in heading))
    except Exception as exc:
        log.debug("Power Up preview CP unreadable: %s", exc)
        pair = None
    return PowerUpPreview(*(pair or (None, None)), cancel_target=target)


def read_powerup_resource_prompt(
    image: Image.Image, expected_display_name: str | Collection[str],
) -> tuple[int, int] | None:
    """Return only CANCEL for this Pokemon's exact Rare Candy conversion prompt.

    Candy quantities and their arrow are never passed to the CP reader. This
    is a resource prompt, not evidence of current or upgraded Pokemon CP.
    """
    names = ([expected_display_name] if isinstance(expected_display_name, str)
             else expected_display_name)
    expected = {_normalise(name) for name in names if name.strip()}
    if not expected:
        return None
    try:
        lines = _lines(_words(image))
    except Exception as exc:
        log.debug("Power Up resource prompt OCR failed: %s", exc)
        return None
    if any(_heading(line) is not None for line in lines):
        return None
    if _resource_name(lines, image.height) not in expected:
        return None
    return _cancel_target(lines, image.width, image.height)
