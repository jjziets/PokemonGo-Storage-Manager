"""Pokemon fingerprint — identify species and level from IVs + HP.

Given ATK/DEF/STA IVs and max HP, we can find which species at which level
produces that exact HP. Combined with CP verification, this gives us a
unique identification without needing the species name.

The HP formula is: max(10, floor((base_STA + iv_STA) * CPM))
The CP formula is: max(10, floor(ATK * DEF^0.5 * STA^0.5 / 10))

IVs + HP → species + level (lookup)
species + level + IVs → expected CP (calculate)
"""

import math
import logging
from functools import lru_cache

from .cpm_table import CPM_TABLE, LEVELS_ASCENDING
from .calculator import compute_cp
from .gamemaster import download_gamemaster, parse_species, get_species_base_stats

log = logging.getLogger(__name__)

_species_map = None


def _get_species_map():
    global _species_map
    if _species_map is None:
        gm = download_gamemaster()
        _species_map = parse_species(gm)
    return _species_map


def fingerprint(atk: int, def_: int, sta: int, hp: int,
                cp_hint: int = -1, name_hint: str = "") -> dict | None:
    """Identify a Pokemon from its IVs and HP.

    Args:
        atk, def_, sta: IV values (0-15)
        hp: max HP from screen
        cp_hint: OCR'd CP (used to narrow results, not trusted)
        name_hint: OCR'd name (used to prefer matching species)

    Returns dict with:
        species: str (correct species name)
        level: float
        expected_cp: int (calculated from formula)
        cp_match: bool (expected_cp == cp_hint)
    Or None if no match found.
    """
    if hp <= 0 or atk < 0 or def_ < 0 or sta < 0:
        return None

    species_map = _get_species_map()
    matches = []

    # Search all species for HP match
    for sid, data in species_map.items():
        base_sta = data["base_sta"]

        for level in LEVELS_ASCENDING:
            cpm = CPM_TABLE[level]
            expected_hp = max(10, math.floor((base_sta + sta) * cpm))

            # HP must match exactly (±1 for OCR)
            if abs(expected_hp - hp) > 1:
                continue

            # HP matches — calculate CP at this level
            expected_cp = compute_cp(
                data["base_atk"], data["base_def"], data["base_sta"],
                atk, def_, sta, cpm
            )

            matches.append({
                "species": data["name"],
                "species_id": sid,
                "level": level,
                "expected_cp": expected_cp,
                "expected_hp": expected_hp,
                "cp_match": expected_cp == cp_hint if cp_hint > 0 else None,
            })

    if not matches:
        return None

    # Prefer matches where CP also validates
    cp_matches = [m for m in matches if m["cp_match"]]
    if cp_matches:
        # If name hint helps, prefer it
        if name_hint:
            hint_lower = name_hint.lower()[:5]
            name_matches = [m for m in cp_matches
                            if hint_lower in m["species"].lower() or hint_lower in m["species_id"]]
            if name_matches:
                return name_matches[0]
        return cp_matches[0]

    # No CP match — prefer name hint first (CP might be badly misread)
    if name_hint:
        hint_lower = name_hint.lower().lstrip('x ').strip()[:5]
        name_matches = [m for m in matches
                        if hint_lower in m["species"].lower() or hint_lower in m["species_id"]]
        if name_matches:
            # Among name matches, prefer closest CP
            if cp_hint > 0:
                name_matches.sort(key=lambda m: abs(m["expected_cp"] - cp_hint))
            return name_matches[0]

    # No name match — sort by closest CP
    if cp_hint > 0:
        matches.sort(key=lambda m: abs(m["expected_cp"] - cp_hint))

    return matches[0]
