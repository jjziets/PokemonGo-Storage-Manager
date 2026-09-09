"""Exact, form-aware Pokemon identity resolution.

The scanner must not repair one OCR field by borrowing a merely plausible
value from another Pokemon.  This module therefore resolves only exact
GameMaster candidates:

* all IVs are within the real 0-15 range;
* a provided HP value matches exactly;
* a positive CP value matches exactly;
* an authoritative caught-species family restricts candidates without fuzzy
  matching; and
* one canonical species/form/level candidate remains.

No candidate is selected by proximity or input iteration order.  A hidden CP
is recoverable only when HP, IVs, and (when available) the caught family leave
one canonical candidate class whose ``expected_cp`` can be trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
from numbers import Integral
import re
from typing import Mapping
import unicodedata

from .calculator import compute_cp
from .cpm_table import CPM_TABLE, LEVELS_ASCENDING
from .gamemaster import download_gamemaster, parse_species


_COSMETIC_ID_SUFFIX = re.compile(r"_(?:shadow|purified)$", re.IGNORECASE)
_COSMETIC_NAME_SUFFIX = re.compile(
    r"\s*\((?:shadow|purified)\)\s*$", re.IGNORECASE
)
_TRAILING_PARENTHETICAL = re.compile(r"\s*\([^)]*\)\s*$")


@dataclass(frozen=True, slots=True)
class ResolvedPokemon:
    """One exact canonical GameMaster candidate."""

    species: str
    species_id: str
    level: float
    expected_cp: int
    expected_hp: int


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    """Reason-bearing result for diagnostics and scanner logging."""

    status: str
    resolved: ResolvedPokemon | None
    candidates: tuple[ResolvedPokemon, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class _RawCandidate:
    species: str
    species_id: str
    canonical_species_id: str
    level: float
    expected_cp: int
    expected_hp: int
    base_atk: int
    base_def: int
    base_sta: int


def _normalise_name(value: str) -> str:
    """Return a Unicode-safe comparison key for a Pokemon name.

    Gender signs are expanded before punctuation is removed so Nidoran female
    and male remain distinct.  Diacritics are folded so OCR text such as
    ``Flabébé`` matches the GameMaster spelling ``Flabebe``.  Colons and
    other punctuation remain semantically harmless (``Type: Null`` matches
    ``Type (Null)``).
    """
    value = (value or "").strip().replace("♀", " female ").replace("♂", " male ")
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return "".join(char for char in without_marks if char.isalnum())


@lru_cache(maxsize=4096)
def _family_aliases(species_id: str, species_name: str) -> frozenset[str]:
    """Build exact aliases for an authoritative caught-family name."""
    full_name = _COSMETIC_NAME_SUFFIX.sub("", species_name or "").strip()
    base_name = _TRAILING_PARENTHETICAL.sub("", full_name).strip()
    canonical_id = _canonical_species_id(species_id, full_name)

    aliases = {
        _normalise_name(full_name),
        _normalise_name(base_name),
        _normalise_name(canonical_id),
    }
    aliases.discard("")
    return frozenset(aliases)


def _canonical_species_id(species_id: str, species_name: str) -> str:
    """Collapse cosmetic Shadow/Purified duplicates, but preserve real forms."""
    cleaned_id = _COSMETIC_ID_SUFFIX.sub("", (species_id or "").strip().casefold())
    if cleaned_id:
        return cleaned_id

    cleaned_name = _COSMETIC_NAME_SUFFIX.sub("", species_name or "")
    return _normalise_name(cleaned_name)


def _is_valid_iv(value: object) -> bool:
    return isinstance(value, Integral) and not isinstance(value, bool) and 0 <= int(value) <= 15


def _optional_positive_int(value: object) -> int | None:
    """Treat scanner sentinels (None, zero, negative) as unavailable."""
    if value is None:
        return None
    if not isinstance(value, Integral) or isinstance(value, bool):
        raise ValueError("expected an integer")
    parsed = int(value)
    return parsed if parsed > 0 else None


@lru_cache(maxsize=1)
def _default_species_map() -> Mapping[str, Mapping[str, object]]:
    """Load the cached/downloaded GameMaster once for runtime callers."""
    return parse_species(download_gamemaster())


def _build_candy_index(species_map) -> dict[str, frozenset[str]]:
    """Group authoritative evolution families and same-dex forms, transitively."""
    parents = {key: key for key in species_map}

    def root(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    groups = {}
    for key, data in species_map.items():
        family = str(data.get("family_id") or "")
        dex = data.get("dex")
        tags = [("family", family)] if family else []
        if isinstance(dex, Integral) and not isinstance(dex, bool) and dex > 0:
            tags.append(("dex", int(dex)))
        for tag in tags:
            if tag in groups:
                parents[root(key)] = root(groups[tag])
            else:
                groups[tag] = key
    members = {}
    for key in species_map:
        members.setdefault(root(key), set()).add(key)
    index = {}
    for group in members.values():
        families = {str(species_map[key].get("family_id") or "") for key in group} - {""}
        aliases = {_normalise_name(family.removeprefix("FAMILY_")) for family in families}
        if not families:
            # No evolution mapping: exact species/form aliases narrow only to
            # this same-dex group, never to a guessed evolution chain.
            for key in group:
                data = species_map[key]
                aliases.update(_family_aliases(str(data.get("id") or key), str(data.get("name") or key)))
        for alias in aliases - {""}:
            index[alias] = index.get(alias, frozenset()) | frozenset(group)
    return index


@lru_cache(maxsize=1)
def _default_candy_index():
    return _build_candy_index(_default_species_map())


def candy_family_species_ids(label: str, species_map=None) -> frozenset[str] | None:
    """Return exact evolution-family members; unknown labels supply no evidence."""
    index = _default_candy_index() if species_map is None else _build_candy_index(species_map)
    return index.get(_normalise_name(label))


def candidate_species_family(candidates) -> str | None:
    """Name one active species shared by forms, never a candy/evolution root."""
    names = {_TRAILING_PARENTHETICAL.sub("", _COSMETIC_NAME_SUFFIX.sub("", candidate.species)).strip()
             for candidate in candidates}
    return next(iter(names)) if len(names) == 1 else None


def _representative(candidates: list[_RawCandidate]) -> _RawCandidate:
    """Choose a deterministic label for an already-equivalent class."""
    return min(
        candidates,
        key=lambda candidate: (
            candidate.species_id != candidate.canonical_species_id,
            bool(_COSMETIC_NAME_SUFFIX.search(candidate.species)),
            candidate.species_id.casefold(),
            candidate.species.casefold(),
        ),
    )


def _canonicalise(raw_candidates: list[_RawCandidate]) -> tuple[ResolvedPokemon, ...]:
    """Deduplicate cosmetic aliases without merging real forms or levels."""
    classes: dict[tuple[object, ...], list[_RawCandidate]] = {}
    for candidate in raw_candidates:
        key = (
            candidate.canonical_species_id,
            candidate.base_atk,
            candidate.base_def,
            candidate.base_sta,
            candidate.level,
            candidate.expected_cp,
            candidate.expected_hp,
        )
        classes.setdefault(key, []).append(candidate)

    resolved = []
    for equivalent in classes.values():
        candidate = _representative(equivalent)
        resolved.append(
            ResolvedPokemon(
                species=_COSMETIC_NAME_SUFFIX.sub("", candidate.species).strip(),
                species_id=candidate.canonical_species_id,
                level=candidate.level,
                expected_cp=candidate.expected_cp,
                expected_hp=candidate.expected_hp,
            )
        )

    return tuple(
        sorted(
            resolved,
            key=lambda candidate: (
                candidate.species_id,
                candidate.level,
                candidate.expected_cp,
                candidate.expected_hp,
                candidate.species,
            ),
        )
    )


def resolve_candidates(
    atk: int,
    def_: int,
    sta: int,
    *,
    hp: int | None = None,
    cp: int | None = None,
    caught_family: str | None = None,
    candy_family: str | None = None,
    species_map: Mapping[str, Mapping[str, object]] | None = None,
) -> ResolutionResult:
    """Resolve exact GameMaster candidates and report why resolution failed.

    Non-positive ``hp`` and ``cp`` values are treated as scanner sentinels for
    an unreadable field.  Positive values are exact constraints.  When
    ``caught_family`` restricts the active species and forms; ``candy_family``
    restricts the wider evolution group. With neither, uniqueness is global.
    """
    if not all(_is_valid_iv(value) for value in (atk, def_, sta)):
        return ResolutionResult(
            status="invalid_input",
            resolved=None,
            reason="IVs must be integers in the inclusive range 0-15",
        )

    try:
        exact_hp = _optional_positive_int(hp)
        exact_cp = _optional_positive_int(cp)
    except ValueError:
        return ResolutionResult(
            status="invalid_input",
            resolved=None,
            reason="HP and CP must be integers when provided",
        )

    family_key = _normalise_name(caught_family or "")
    candidates_by_species = species_map if species_map is not None else _default_species_map()
    candy_ids = candy_family_species_ids(candy_family, species_map) if candy_family else None
    if candy_family and candy_ids is None and not family_key:
        return ResolutionResult("no_match", None, reason="candy label does not identify a known evolution family")
    raw_candidates: list[_RawCandidate] = []

    for map_species_id, data in candidates_by_species.items():
        if candy_ids is not None and map_species_id not in candy_ids:
            continue
        species_id = str(data.get("id") or map_species_id)
        species_name = str(data.get("name") or species_id)

        if family_key and family_key not in _family_aliases(species_id, species_name):
            continue

        try:
            base_atk = int(data["base_atk"])
            base_def = int(data["base_def"])
            base_sta = int(data["base_sta"])
        except (KeyError, TypeError, ValueError):
            continue
        if min(base_atk, base_def, base_sta) <= 0:
            continue

        canonical_species_id = _canonical_species_id(species_id, species_name)
        for level in LEVELS_ASCENDING:
            cpm = CPM_TABLE[level]
            expected_hp = max(10, math.floor((base_sta + int(sta)) * cpm))
            if exact_hp is not None and expected_hp != exact_hp:
                continue

            expected_cp = compute_cp(
                base_atk,
                base_def,
                base_sta,
                int(atk),
                int(def_),
                int(sta),
                cpm,
            )
            if exact_cp is not None and expected_cp != exact_cp:
                continue

            raw_candidates.append(
                _RawCandidate(
                    species=species_name,
                    species_id=species_id,
                    canonical_species_id=canonical_species_id,
                    level=level,
                    expected_cp=expected_cp,
                    expected_hp=expected_hp,
                    base_atk=base_atk,
                    base_def=base_def,
                    base_sta=base_sta,
                )
            )

    candidates = _canonicalise(raw_candidates)
    if not candidates:
        return ResolutionResult(
            status="no_match",
            resolved=None,
            reason="no exact GameMaster candidate matched all provided fields",
        )
    if len(candidates) != 1:
        scope = "caught family" if family_key else "candy evolution family" if candy_ids else "global GameMaster"
        return ResolutionResult(
            status="ambiguous",
            resolved=None,
            candidates=candidates,
            reason=f"{len(candidates)} canonical candidates remain in {scope}",
        )

    return ResolutionResult(
        status="resolved",
        resolved=candidates[0],
        candidates=candidates,
        reason="one exact canonical candidate remains",
    )


def resolve_exact(
    atk: int,
    def_: int,
    sta: int,
    *,
    hp: int | None = None,
    cp: int | None = None,
    caught_family: str | None = None,
    candy_family: str | None = None,
    species_map: Mapping[str, Mapping[str, object]] | None = None,
) -> ResolvedPokemon | None:
    """Return one exact Pokemon candidate, otherwise ``None``."""
    return resolve_candidates(
        atk,
        def_,
        sta,
        hp=hp,
        cp=cp,
        caught_family=caught_family,
        candy_family=candy_family,
        species_map=species_map,
    ).resolved
