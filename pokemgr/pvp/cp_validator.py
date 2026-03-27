"""CP validation and correction using the CP formula.

Given species + IVs, we can calculate the exact CP at every level (1-51).
This lets us:
  - Verify if a scanned CP is valid for the given IVs
  - Correct OCR-misread CPs by finding the closest valid CP
  - Flag impossible CP+IV combinations
"""

import logging
from .cpm_table import CPM_TABLE, LEVELS_ASCENDING
from .calculator import compute_cp
from .gamemaster import download_gamemaster, parse_species, get_species_base_stats

log = logging.getLogger(__name__)

# Cache the species map
_species_map: dict | None = None


def _get_species_map() -> dict:
    global _species_map
    if _species_map is None:
        gm = download_gamemaster()
        _species_map = parse_species(gm)
    return _species_map


def _clean_name(name: str) -> str:
    """Clean OCR artifacts from species name.
    Removes buddy badge 'x ' prefix, trailing symbols, etc."""
    import re
    name = name.strip()
    # Remove buddy badge "x " prefix
    name = re.sub(r'^[xX]\s+', '', name)
    # Remove common OCR artifacts
    name = re.sub(r'^[^a-zA-Z]+', '', name)
    return name.strip()


def _find_all_forms(species_name: str, species_map: dict) -> list[dict]:
    """Find all forms AND evolution family of a species.
    e.g. "Arctibax" also checks Baxcalibur (evolution).
    Returns list of base stat dicts."""
    clean = _clean_name(species_name).lower()
    forms = []
    seen_ids = set()

    # Exact match first
    base = get_species_base_stats(clean, species_map)
    if base:
        forms.append(base)
        seen_ids.add(base["id"])

    # Search for all forms containing this name
    for sid, data in species_map.items():
        name_lower = data["name"].lower()
        sid_lower = sid.lower()
        if (clean in sid_lower or clean in name_lower) and data["id"] not in seen_ids:
            forms.append(data)
            seen_ids.add(data["id"])

    # Also search evolution chains — if "arctibax" doesn't work, try its evolutions
    for sid, data in species_map.items():
        evolutions = data.get("evolutions", [])
        if evolutions:
            for evo in evolutions:
                evo_lower = evo.lower() if isinstance(evo, str) else ""
                # If this species evolves FROM our target, add the base
                if clean in evo_lower and data["id"] not in seen_ids:
                    forms.append(data)
                    seen_ids.add(data["id"])
                # If our target evolves INTO this species, add the evolution
                if evo_lower and data["name"].lower() == clean:
                    evo_data = species_map.get(evo)
                    if evo_data and evo_data["id"] not in seen_ids:
                        forms.append(evo_data)
                        seen_ids.add(evo_data["id"])

    # Broader search: if name is 5+ chars, check if any species name STARTS with same 5 chars
    # Catches OCR truncation like "Arctib" matching "Arctibax" and "Baxcalibur" via evo chain
    if len(clean) >= 5 and not forms:
        prefix = clean[:5]
        for sid, data in species_map.items():
            if sid.lower().startswith(prefix) and data["id"] not in seen_ids:
                forms.append(data)
                seen_ids.add(data["id"])

    return forms


def get_valid_cps(species_name: str, atk: int, def_: int, sta: int) -> list[tuple[float, int]]:
    """Calculate all valid (level, CP) pairs for a species + IV combination.
    Checks ALL forms of the species.

    Returns list of (level, cp) sorted by level ascending.
    """
    species_map = _get_species_map()
    forms = _find_all_forms(species_name, species_map)

    if not forms:
        return []

    results = []
    seen_cps = set()
    for form in forms:
        base_atk = form["base_atk"]
        base_def = form["base_def"]
        base_sta = form["base_sta"]

        for level in LEVELS_ASCENDING:
            cpm = CPM_TABLE[level]
            cp = compute_cp(base_atk, base_def, base_sta, atk, def_, sta, cpm)
            if cp not in seen_cps:
                results.append((level, cp))
                seen_cps.add(cp)

    results.sort(key=lambda x: x[1])
    return results


def find_closest_cp(species_name: str, atk: int, def_: int, sta: int,
                    ocr_cp: int) -> tuple[int, float] | None:
    """Find the valid CP closest to the OCR-read CP.

    Returns (correct_cp, level) or None if species not found.
    """
    valid_cps = get_valid_cps(species_name, atk, def_, sta)
    if not valid_cps:
        return None

    # Find closest match
    best = min(valid_cps, key=lambda x: abs(x[1] - ocr_cp))
    return (best[1], best[0])  # (cp, level)


def validate_cp(species_name: str, atk: int, def_: int, sta: int,
                scanned_cp: int, _recursed: bool = False) -> dict:
    """Validate a scanned CP against the formula.

    Returns:
        {
            "valid": bool,          # True if scanned CP matches a valid level
            "scanned_cp": int,      # the OCR-read CP
            "correct_cp": int,      # the closest valid CP
            "level": float,         # the level for the correct CP
            "error": int,           # difference between scanned and correct
            "all_valid_cps": [...], # list of all valid (level, cp) pairs
        }
    """
    valid_cps = get_valid_cps(species_name, atk, def_, sta)
    if not valid_cps:
        return {
            "valid": False,
            "scanned_cp": scanned_cp,
            "correct_cp": scanned_cp,
            "level": 0,
            "error": 0,
            "species_found": False,
            "all_valid_cps": [],
        }

    # Check exact match
    for level, cp in valid_cps:
        if cp == scanned_cp:
            return {
                "valid": True,
                "scanned_cp": scanned_cp,
                "correct_cp": scanned_cp,
                "level": level,
                "error": 0,
                "species_found": True,
                "all_valid_cps": valid_cps,
            }

    # No exact match — find closest
    best_level, best_cp = min(valid_cps, key=lambda x: abs(x[1] - scanned_cp))
    return {
        "valid": False,
        "scanned_cp": scanned_cp,
        "correct_cp": best_cp,
        "level": best_level,
        "error": abs(scanned_cp - best_cp),
        "species_found": True,
        "all_valid_cps": valid_cps,
    }


def compute_hp_at_level(species_name: str, sta: int, level: float) -> list[int]:
    """Calculate HP for ALL forms of a species at a given level and STA IV.

    Returns list of possible HP values (one per form).
    """
    import math
    species_map = _get_species_map()
    forms = _find_all_forms(species_name, species_map)
    hp_values = []
    cpm = CPM_TABLE.get(level, 0)
    if cpm:
        for form in forms:
            hp = max(10, math.floor((form["base_sta"] + sta) * cpm))
            if hp not in hp_values:
                hp_values.append(hp)
    return hp_values


def check_pokemon_cp(species: str, cp: int, atk: int, def_: int,
                     sta: int, hp: int = -1) -> dict:
    """Check if a Pokemon's CP is valid for its IVs.

    Returns:
        {
            "status": "valid" | "invalid" | "needs_rescan" | "unknown",
            "scanned_cp": int,
            "closest_valid_cp": int | None,
            "level": float | None,
            "error": int,
        }

    NEVER auto-corrects. Only flags for rescan.
    """
    if cp <= 0:
        return {"status": "needs_rescan", "scanned_cp": cp,
                "closest_valid_cp": None, "level": None, "error": 0}

    if atk < 0 or def_ < 0 or sta < 0:
        return {"status": "needs_rescan", "scanned_cp": cp,
                "closest_valid_cp": None, "level": None, "error": 0}

    result = validate_cp(species, atk, def_, sta, cp)

    if not result["species_found"]:
        # Species not in gamemaster — try searching all species by CP+IVs+HP
        if hp > 0:
            import math
            species_map = _get_species_map()
            for sid, data in species_map.items():
                for level in LEVELS_ASCENDING:
                    cpm = CPM_TABLE[level]
                    test_cp = compute_cp(data["base_atk"], data["base_def"], data["base_sta"],
                                         atk, def_, sta, cpm)
                    if test_cp == cp:
                        test_hp = max(10, math.floor((data["base_sta"] + sta) * cpm))
                        if abs(test_hp - hp) <= 1:
                            log.info("Species found by CP+IVs+HP: '%s' → '%s' at L%s",
                                     species, data["name"], level)
                            return {
                                "status": "valid",
                                "scanned_cp": cp,
                                "closest_valid_cp": cp,
                                "level": level,
                                "error": 0,
                                "corrected_species": data["name"],
                            }
        return {"status": "unknown", "scanned_cp": cp,
                "closest_valid_cp": None, "level": None, "error": 0}

    if result["valid"]:
        # Also verify HP if provided (allow ±1 for OCR digit confusion)
        if hp > 0 and result["level"]:
            valid_hps = compute_hp_at_level(species, sta, result["level"])
            if valid_hps:
                # Check if HP matches ANY form (±1 tolerance for OCR)
                hp_match = any(abs(expected - hp) <= 1 for expected in valid_hps)
                if not hp_match:
                    log.warning("CP valid but HP mismatch: expected=%s got=%d (STA IV may be wrong)",
                                valid_hps, hp)
                    return {
                        "status": "needs_rescan",
                        "scanned_cp": cp,
                        "closest_valid_cp": cp,
                        "level": result["level"],
                        "error": 0,
                        "hp_mismatch": True,
                        "expected_hp": valid_hps[0],
                        "actual_hp": hp,
                    }
        return {"status": "valid", "scanned_cp": cp,
                "closest_valid_cp": cp, "level": result["level"], "error": 0}

    # No tolerance — CP must match exactly. If it doesn't, rescan.

    # CP doesn't match — if error is very large, the species name might be wrong
    # Try all species to find one that matches CP+IVs+HP
    if result["error"] > 100 and hp > 0:
        import math
        species_map = _get_species_map()
        for sid, data in species_map.items():
            for level in LEVELS_ASCENDING:
                cpm = CPM_TABLE[level]
                test_cp = compute_cp(data["base_atk"], data["base_def"], data["base_sta"],
                                     atk, def_, sta, cpm)
                if test_cp == cp:
                    test_hp = max(10, math.floor((data["base_sta"] + sta) * cpm))
                    if abs(test_hp - hp) <= 1:
                        log.info("Species correction: '%s' → '%s' (CP%d IVs %d/%d/%d HP%d at L%s)",
                                 species, data["name"], cp, atk, def_, sta, hp, level)
                        return {
                            "status": "valid",
                            "scanned_cp": cp,
                            "closest_valid_cp": cp,
                            "level": level,
                            "error": 0,
                            "corrected_species": data["name"],
                        }

    return {
        "status": "needs_rescan",
        "scanned_cp": cp,
        "closest_valid_cp": result["correct_cp"],
        "level": result["level"],
        "error": result["error"],
    }
