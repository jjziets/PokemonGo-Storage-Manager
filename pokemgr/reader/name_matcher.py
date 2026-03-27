"""Match OCR'd Pokemon names against the official species list.

Uses the gamemaster species names as the source of truth.
Fuzzy matches OCR output to find the closest real Pokemon name.
"""

import logging
from functools import lru_cache

log = logging.getLogger(__name__)

_species_names: list[str] | None = None


def _load_species_names() -> list[str]:
    """Load all species names from the gamemaster."""
    global _species_names
    if _species_names is not None:
        return _species_names

    try:
        from ..pvp.gamemaster import download_gamemaster, parse_species
        gm = download_gamemaster()
        species_map = parse_species(gm)
        _species_names = [data["name"] for data in species_map.values()]
        log.info("Loaded %d species names for matching", len(_species_names))
    except Exception as e:
        log.warning("Could not load species names: %s", e)
        _species_names = []

    return _species_names


def _similarity(a: str, b: str) -> float:
    """Simple similarity score between two strings (0.0 to 1.0)."""
    a = a.lower().strip()
    b = b.lower().strip()
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0

    # Check if one contains the other
    if a in b or b in a:
        return 0.8

    # Character-level matching
    shorter = min(len(a), len(b))
    longer = max(len(a), len(b))
    if shorter == 0:
        return 0.0

    # Count matching characters (order-sensitive)
    matches = 0
    b_remaining = list(b)
    for char in a:
        if char in b_remaining:
            b_remaining.remove(char)
            matches += 1

    return matches / longer


@lru_cache(maxsize=4096)
def match_species_name(ocr_name: str) -> str:
    """Match an OCR'd name to the closest official species name.

    Returns the corrected name, or the original if no good match found.
    """
    if not ocr_name or len(ocr_name) < 2:
        return ocr_name

    names = _load_species_names()
    if not names:
        return ocr_name

    ocr_lower = ocr_name.lower().strip()

    # Exact match
    for name in names:
        if name.lower() == ocr_lower:
            return name

    # Find best fuzzy match
    best_name = ocr_name
    best_score = 0.0

    for name in names:
        score = _similarity(ocr_lower, name.lower())

        # Bonus for matching first 3 characters
        if len(ocr_lower) >= 3 and len(name) >= 3:
            if ocr_lower[:3] == name.lower()[:3]:
                score += 0.15
            elif ocr_lower[:2] == name.lower()[:2]:
                score += 0.05

        if score > best_score:
            best_score = score
            best_name = name

    # Only accept if confidence is high enough
    if best_score >= 0.6:
        if best_name.lower() != ocr_lower:
            log.debug("Name corrected: '%s' → '%s' (score=%.2f)", ocr_name, best_name, best_score)
        return best_name

    return ocr_name
