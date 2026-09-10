# TRACEWEAVER: file-role=stable-keeper-candidate-queries; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
"""Narrow candidate visits while leaving keeper identity checks to the executor."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


_FLAG_NAMES = ("shiny", "shadow", "lucky", "is_dynamax")


@dataclass(frozen=True)
class KeeperCPBatch:
    query: str
    keys: tuple[tuple, ...]


def _boolean_flags(values):
    return all(type(value) in (bool, int) and value in (0, 1) for value in values)


# TRACEWEAVER: entrypoint=pending_cp_batches; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
def pending_cp_batches(
    base_query: str,
    remaining_keys: Iterable[tuple],
    flags: Mapping[str, bool | int],
    *,
    max_query_length: int = 500,
) -> list[KeeperCPBatch]:
    """Append disjoint exact-CP groups to an already established flag query.

    A keeper key is the executor's ten-field species/CP/HP/IV/flag tuple.
    All occurrences of a CP stay in one chunk; this function neither consumes
    their counts nor treats CP as identity authority. A containing range and
    excluded gaps express the exact set using AND alone, so no OR precedence
    can weaken the established flag query.
    """
    if not isinstance(base_query, str) or not base_query.strip():
        raise ValueError("A nonempty established base query is required")
    if type(max_query_length) is not int or not 1 <= max_query_length <= 500:
        raise ValueError("The full query limit must be between 1 and 500 characters")
    if not isinstance(flags, Mapping) or set(flags) != set(_FLAG_NAMES):
        raise ValueError("All four established keeper flags are required")
    expected_flags = tuple(flags[name] for name in _FLAG_NAMES)
    if not _boolean_flags(expected_flags):
        raise ValueError("Established keeper flags must be boolean values")

    keys_by_cp = {}
    for key in remaining_keys:
        if not isinstance(key, tuple) or len(key) != 10:
            raise ValueError("A complete ten-field keeper signature is required")
        key_flags = key[6:]
        if not _boolean_flags(key_flags):
            raise ValueError("Keeper signature flags must be boolean values")
        if key_flags != expected_flags:
            continue
        cp = key[1]
        if type(cp) is not int or cp <= 0:
            raise ValueError("Pending keeper CP must be a positive integer")
        keys_by_cp.setdefault(cp, set()).add(key)

    prefix = base_query + "&"
    batches = []
    gaps = []
    batch_keys = []
    gap_length = 0
    start = end = None

    def cp_term(low, high):
        return f"cp{low}" if low == high else f"cp{low}-{high}"

    def completed_batch():
        query = prefix + "&".join([cp_term(start, end), *gaps])
        return KeeperCPBatch(query, tuple(batch_keys))

    for cp in sorted(keys_by_cp):
        single = f"cp{cp}"
        if len(prefix) + len(single) > max_query_length:
            raise ValueError("The base query leaves no room for a pending CP")

        gap = "!" + cp_term(end + 1, cp - 1) if end is not None and cp > end + 1 else None
        enclosing = cp_term(start, cp) if start is not None else single
        extra_gap_length = len(gap) + 1 if gap else 0
        if len(prefix) + len(enclosing) + gap_length + extra_gap_length > max_query_length:
            batches.append(completed_batch())
            gaps = []
            batch_keys = []
            gap_length = 0
            start = end = cp
        else:
            if start is None:
                start = cp
            end = cp
            if gap:
                gaps.append(gap)
                gap_length += extra_gap_length
        batch_keys.extend(sorted(keys_by_cp[cp], key=repr))

    if batch_keys:
        batches.append(completed_batch())
    return batches


def pending_cp_queries(
    base_query: str,
    remaining_keys: Iterable[tuple],
    flags: Mapping[str, bool | int],
    *,
    max_query_length: int = 500,
) -> list[str]:
    """Return only query text when a caller does not need chunk ownership."""
    return [batch.query for batch in pending_cp_batches(
        base_query, remaining_keys, flags, max_query_length=max_query_length)]
