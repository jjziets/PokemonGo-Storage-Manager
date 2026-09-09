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
import re
from difflib import SequenceMatcher
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


def _normalise_name(value: str) -> str:
    """Normalise OCR/species names for conservative fuzzy comparison."""
    value = re.sub(r"^[xX]\s+", "", (value or "").strip())
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _name_hint_score(name_hint: str, match: dict) -> float:
    """Score how plausibly an OCR hint names this match.

    CP values are highly non-unique across Pokemon.  A fuzzy-but-close visible
    name is therefore stronger evidence than an unrelated exact CP match.
    """
    hint = _normalise_name(name_hint)
    if len(hint) < 3:
        return 0.0

    candidates = {
        _normalise_name(match.get("species", "")),
        _normalise_name(match.get("species_id", "")),
    }
    candidates.discard("")

    best = 0.0
    for candidate in candidates:
        if hint == candidate:
            score = 1.0
        elif min(len(hint), len(candidate)) >= 4 and (hint in candidate or candidate in hint):
            score = 0.9
        else:
            score = SequenceMatcher(None, hint, candidate).ratio()
        best = max(best, score)
    return best


def _name_hint_matches(name_hint: str, match: dict) -> bool:
    """Return True only when an OCR hint plausibly names this match."""
    return _name_hint_score(name_hint, match) >= 0.72


def fingerprint(atk: int, def_: int, sta: int, hp: int,
                cp_hint: int = -1, name_hint: str = "",
                strict_name_hint: bool = False,
                require_unique_species: bool = False) -> dict | None:
    """Identify a Pokemon from its IVs and HP.

    Args:
        atk, def_, sta: IV values (0-15)
        hp: max HP from screen
        cp_hint: OCR'd CP (used to narrow results, not trusted)
        name_hint: OCR'd name (used to prefer matching species)
        strict_name_hint: never return a species unrelated to ``name_hint``.
            Use this for storage scans, where protecting an unreadable Pokemon
            is safer than silently assigning another species with the same CP.
        require_unique_species: ignore the visible name and return a result only
            when CP + HP + IVs identify exactly one normalized species.  This
            is the safe mode when the visible text may be a nickname.

    Returns dict with:
        species: str (correct species name)
        level: float
        expected_cp: int (calculated from formula)
        cp_match: bool (expected_cp == cp_hint)
    Or None if no match found.
    """
    if hp <= 0 or atk < 0 or def_ < 0 or sta < 0:
        return None
    if strict_name_hint and not _normalise_name(name_hint):
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

    if require_unique_species:
        # A nickname cannot be trusted as species evidence.  Only accept an
        # exact CP/HP/IV signature when it resolves globally to one species.
        cp_matches = [m for m in matches if m["cp_match"]]
        distinct_species = {
            _normalise_name(match["species"]) for match in cp_matches
        }
        if len(distinct_species) != 1:
            return None
        return cp_matches[0]

    # A visible name is stronger evidence than a globally non-unique CP.  Filter
    # by it before preferring exact CP matches, otherwise a truncated CP can
    # silently turn (for example) Absol into an unrelated Pokemon.
    if name_hint:
        scored_matches = [(_name_hint_score(name_hint, m), m) for m in matches]
        best_name_score = max(score for score, _ in scored_matches)
        if best_name_score >= 0.72:
            # Keep only the best visible-name match (plus exact-score form
            # ties).  Otherwise Nidorina and Nidorino can both pass the fuzzy
            # threshold and the unrelated CP hint chooses between them.
            best_matches = [m for score, m in scored_matches
                            if abs(score - best_name_score) < 1e-9]
            distinct_names = {
                _normalise_name(match["species"]) for match in best_matches
            }
            if strict_name_hint and len(distinct_names) > 1:
                # A truncated hint such as "Nidorin" is equally compatible
                # with Nidorina and Nidorino.  CP is not reliable enough to
                # break an identity tie, so protect/skip this read.
                return None
            matches = best_matches
        elif strict_name_hint:
            return None

    # Prefer matches where CP also validates, within the name-compatible set.
    cp_matches = [m for m in matches if m["cp_match"]]
    if cp_matches:
        return cp_matches[0]

    # CP may be badly misread (for example 747 -> 77).  Once no candidate
    # matches that hint, an exact on-screen HP match is stronger evidence than
    # numerical proximity to the corrupted CP.  CP distance only breaks ties
    # between candidates with equally good HP evidence.
    matches.sort(key=lambda m: (
        abs(m["expected_hp"] - hp),
        abs(m["expected_cp"] - cp_hint) if cp_hint > 0 else 0,
    ))

    return matches[0]
