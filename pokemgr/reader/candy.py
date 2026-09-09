"""Whole candy labels are evolution-family evidence, never active species."""

from collections import defaultdict
import re

from ..calibration.regions import BBox


_LABEL = re.compile(r"(.{2,40}?)\s+CANDY(?:\s+XL)?", re.IGNORECASE)
_CANDY_ANCHOR = re.compile(r"CANDY(?:\s+XL)?", re.IGNORECASE)


def _family_label(text):
    label = " ".join(text.split()).upper()
    if (not 2 <= len(label) <= 40 or label in {"RARE", "STARDUST", "MEGA ENERGY"}
            or "CANDY" in label.split() or not any(char.isalpha() for char in label)
            or not all(char.isalpha() or char.isdigit() or char in " .'-:♀♂%’" for char in label)):
        return ""
    return label


def _immediately_above(label_box, anchor_box, width, height):
    """Both complete lines use the resource font and share one centered column."""
    lx, ly, lw, lh = label_box
    ax, ay, aw, ah = anchor_box
    sx, sy = width / 968, height / 2376
    if (min(lw, aw) <= 0 or not 10*sy <= lh <= 40*sy
            or not 10*sy <= ah <= 40*sy or not .67 <= lh/ah <= 1.5):
        return False
    gap = ay - (ly+lh)
    overlap = min(lx+lw, ax+aw) - max(lx, ax)
    return (0 <= gap <= min(32*sy, max(20*sy, .9*max(lh, ah)))
            and abs((lx+lw/2) - (ax+aw/2)) <= max(12*sx, .25*min(lw, aw))
            and overlap >= .75*min(lw, aw))


def candy_region(width: int, height: int) -> BBox:
    # Resource rows move for Lucky, XL and Mega energy. Keep their search below
    # the name/HP card and above the professor's identity bubble.
    return BBox(int(width * .02), int(height * .49), int(width * .96), int(height * .31))


def parse_candy_observations(observations, width, height):
    """Read whole labels or a uniquely aligned family line above a candy anchor.

    Spatial pairing uses only complete lines from this frame. It never repairs
    spelling, combines family-name fragments, or borrows an adjacent column.
    """
    region = candy_region(width, height)
    labels = {}
    visible = []
    for text, confidence, (x, y, w, h) in observations:
        if (not region.x <= x + w/2 <= region.x2
                or not region.y <= y + h/2 <= region.y2):
            continue
        text = " ".join(text.split())
        visible.append((text, confidence, (x, y, w, h)))
        match = _LABEL.fullmatch(text)
        if confidence < .5 or not match:
            continue
        label = _family_label(match.group(1))
        if not label:
            continue
        labels[label] = max(confidence, labels.get(label, 0.))

    for anchor, anchor_confidence, anchor_box in visible:
        if anchor_confidence < .5 or not _CANDY_ANCHOR.fullmatch(anchor):
            continue
        ax, ay, aw, _ = anchor_box
        for text, confidence, box in visible:
            label = _family_label(text)
            if confidence < .5 or not label or not _immediately_above(box, anchor_box, width, height):
                continue
            label_bottom = box[1]+box[3]
            # An intervening line, even unreadable/low-confidence text, means
            # this is not the immediate label for the anchor below it.
            if any(label_bottom <= other_box[1] + other_box[3]/2 < ay
                   and ax <= other_box[0] + other_box[2]/2 <= ax+aw
                   for _, _, other_box in visible):
                continue
            paired_confidence = min(confidence, anchor_confidence)
            labels[label] = max(paired_confidence, labels.get(label, 0.))
    if len(labels) != 1:
        return "", 0., len(labels) > 1
    label, confidence = next(iter(labels.items()))
    return label, confidence, False


def read_candy_family(image):
    """One bounded legacy OCR pass, used only when the caller needs fallback."""
    import pytesseract
    from PIL import ImageOps

    region = candy_region(*image.size)
    crop = ImageOps.grayscale(image.crop(region.as_tuple()))
    data = pytesseract.image_to_data(crop, config="--psm 11", output_type=pytesseract.Output.DICT)
    lines = defaultdict(list)
    for index, text in enumerate(data["text"]):
        if not text.strip():
            continue
        key = tuple(data[field][index] for field in ("block_num", "par_num", "line_num"))
        lines[key].append((text, float(data["conf"][index]) / 100,
                           data["left"][index], data["top"][index],
                           data["width"][index], data["height"][index]))
    observations = []
    for words in lines.values():
        x, y = min(word[2] for word in words), min(word[3] for word in words)
        x2 = max(word[2]+word[4] for word in words)
        y2 = max(word[3]+word[5] for word in words)
        observations.append((" ".join(word[0] for word in words), min(word[1] for word in words),
                             (region.x+x, region.y+y, x2-x, y2-y)))
    label, confidence, conflict = parse_candy_observations(observations, *image.size)
    return ("", -1.) if conflict else (label, confidence)
