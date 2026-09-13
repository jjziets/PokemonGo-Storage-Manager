"""Review-only selection for removing stars from lesser PvP candidates.

The stored decision is the authority; this module does not recalculate ranks.
Favorite origin, catch age and personal intent are not recorded. Indistinguishable
rows are actionable only when their whole reviewed group has the same authority.
"""

from collections import Counter
from dataclasses import dataclass, replace
from typing import Callable, Iterable

from ..data.models import Pokemon


# TRACEWEAVER: file-role=reviewed-pvp-cleanup-plan; req=REQ-MASS-001,REQ-DATA-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
@dataclass(frozen=True)
class PvpCleanupPlan:
    candidates: tuple[Pokemon, ...]
    remaining: Counter
    protected: int
    ambiguous: int
    total_favorited: int

    @property
    def eligible(self) -> int:
        return len(self.candidates)


def _valid_ivs(row: Pokemon) -> bool:
    return all(type(value) is int and 0 <= value <= 15
               for value in (row.atk, row.def_, row.sta))


def _complete_identity(row: Pokemon) -> bool:
    return (
        isinstance(row.species, str) and bool(row.species.strip())
        and all(type(value) is int and value > 0
                for value in (row.id, row.cp, row.hp))
        and type(row.position) is int and row.position >= 0
        and _valid_ivs(row)
        and all(type(value) is bool for value in
                (row.shiny, row.shadow, row.lucky, row.is_dynamax))
        and isinstance(row.scan_session_id, str)
        and bool(row.scan_session_id.strip())
    )


def cleanup_group_key(key):
    """Group complete live signatures without relying on a resolved species/form.

    CP, HP, IVs and flags may be identical for different forms. A protected
    companion must therefore hold that entire numeric group, even if the stored
    species labels differ. Exact keys remain the traversal occurrence budgets.
    """
    if (not isinstance(key, tuple) or len(key) != 10
            or not isinstance(key[0], str) or not key[0].strip()
            or any(type(value) is not int or value <= 0 for value in key[1:3])
            or any(type(value) is not int or not 0 <= value <= 15 for value in key[3:6])
            or any(type(value) is not bool for value in key[6:])):
        return None
    return key[1:]


def _compatible_partial_identity(candidate: Pokemon, other: Pokemon) -> bool:
    """Missing/malformed fields cannot prove that a companion is different."""
    for field in ("cp", "hp", "atk", "def_", "sta"):
        value = getattr(other, field)
        known = (type(value) is int
                 and (value > 0 if field in ("cp", "hp") else 0 <= value <= 15))
        if known and value != getattr(candidate, field):
            return False
    return not any(type(getattr(other, field)) is bool
                   and getattr(other, field) != getattr(candidate, field)
                   for field in ("shiny", "shadow", "lucky", "is_dynamax"))


# TRACEWEAVER: entrypoint=plan_pvp_cleanup; req=REQ-MASS-001,REQ-DATA-001; trace=TRACE-MASS-001,TRACE-DATA-001; ver=VER-SCAN-001
def plan_pvp_cleanup(
    rows: Iterable[Pokemon], key_for: Callable, *, selected_ids=None,
) -> PvpCleanupPlan:
    """Snapshot complete reviewed favorite groups with IV sum <= 36.

    ``selected_ids`` can only narrow the reviewed set. All supplied rows still
    participate in group protection, including unstarred/deselected rows.
    Counts partition recorded favorites: protected by decision, IVs or review;
    ambiguous because an intended candidate cannot be identified; or eligible.
    Derived IV percentages/totals are never used as mutation authority.
    """
    rows = tuple(rows)
    if selected_ids is not None:
        selected_ids = tuple(selected_ids)
        if any(type(pid) is not int or pid <= 0 for pid in selected_ids):
            raise ValueError("Selected Pokemon IDs must be positive integers")
        selected_ids = set(selected_ids)

    # Evaluate keys even for ineligible companions: they may be
    # indistinguishable from an otherwise eligible row in the game.
    keys = []
    for row in rows:
        try:
            key = key_for(row)
            if cleanup_group_key(key) is None:
                key = None
        except (AttributeError, TypeError, ValueError):
            key = None
        keys.append(key)
    id_counts = Counter(row.id for row in rows if type(row.id) is int)
    partial_rows = [row for row, key in zip(rows, keys)
                    if key is None or not _complete_identity(row)]

    def reviewed_authority(row):
        return (row.favorited is True and row.decision == "TRANSFER"
                and _valid_ivs(row) and row.atk + row.def_ + row.sta <= 36
                and (selected_ids is None or
                     (type(row.id) is int and row.id in selected_ids)))

    groups = {}
    for row, key in zip(rows, keys):
        if key is not None:
            groups.setdefault(cleanup_group_key(key), []).append(row)
    allowed_groups = set()
    for group, members in groups.items():
        if (all(_complete_identity(row) and id_counts[row.id] == 1
                and reviewed_authority(row) for row in members)
                and len({row.scan_session_id for row in members}) == 1
                and len({row.position for row in members}) == len(members)
                and not any(other is not members[0]
                            and _compatible_partial_identity(members[0], other)
                            for other in partial_rows)):
            allowed_groups.add(group)

    candidates = []
    remaining = Counter()
    protected = ambiguous = total_favorited = 0
    for row, key in zip(rows, keys):
        if row.favorited is not True:
            continue
        total_favorited += 1
        if (row.decision != "TRANSFER"
                or (selected_ids is not None and type(row.id) is int
                    and row.id not in selected_ids)
                or (_valid_ivs(row) and row.atk + row.def_ + row.sta >= 37)):
            protected += 1
        elif (not _complete_identity(row) or key is None
              or cleanup_group_key(key) not in allowed_groups):
            ambiguous += 1
        else:
            candidates.append(replace(row))
            remaining[key] += 1
    return PvpCleanupPlan(tuple(candidates), remaining, protected,
                          ambiguous, total_favorited)
