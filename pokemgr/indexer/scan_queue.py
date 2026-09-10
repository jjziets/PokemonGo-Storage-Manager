"""Read a reconciled inventory ledger without changing its completion state."""

# TRACEWEAVER: file-role=inventory-queue-loader; req=REQ-SCAN-003,REQ-DATA-001; trace=TRACE-SCAN-003,TRACE-DATA-001; ver=VER-SCAN-001
import json
from pathlib import Path
import re


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate ledger field: {key}")
        result[key] = value
    return result


# TRACEWEAVER: entrypoint=load_scan_queue; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
def load_scan_queue(path: str | Path) -> list[dict]:
    """Return only untouched pending, disjoint non-Normal inventory passes.

    Completion and active-session evidence belong to the supervising ledger.
    This loader never marks work complete or attempts to resume a partial pass.
    """
    try:
        ledger = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"Cannot load scan queue: {exc}") from exc
    if not isinstance(ledger, dict) or type(ledger.get("version")) is not int or ledger["version"] != 1:
        raise ValueError("Scan queue requires a version 1 inventory ledger")
    normal = ledger.get("normal")
    if not isinstance(normal, dict) or normal.get("completed") is not True:
        raise ValueError("Normal inventory must be completed before starting remaining partitions")
    if "active_session" not in normal or normal["active_session"] is not None or ledger.get("active_session") is not None:
        raise ValueError("Scan queue is held while an active session is recorded")
    partitions = ledger.get("remaining_partitions")
    if not isinstance(partitions, list) or not partitions:
        raise ValueError("Scan queue has no remaining partitions")

    traits = ("shiny", "shadow", "dynamax", "gigantamax")
    seen_masks, seen_ids, selected = set(), set(), []
    for partition in partitions:
        if not isinstance(partition, dict):
            raise ValueError("Each scan partition must be an object")
        name, query, tags = (partition.get(key) for key in ("id", "query", "tags"))
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) is None or name in seen_ids:
            raise ValueError("Scan partitions require unique plain identifiers")
        seen_ids.add(name)
        if not isinstance(query, str):
            raise ValueError(f"Partition {name} requires an exact four-literal query")
        literals = query.split("&")
        values = {}
        for literal in literals:
            trait = literal[1:] if literal.startswith("!") else literal
            if trait not in traits or trait in values:
                raise ValueError(f"Partition {name} has an unsupported or repeated query literal")
            values[trait] = not literal.startswith("!")
        if len(literals) != 4 or set(values) != set(traits):
            raise ValueError(f"Partition {name} must specify all four inventory traits")
        mask = tuple(values[trait] for trait in traits)
        if not any(mask) or mask in seen_masks:
            raise ValueError(f"Partition {name} repeats Normal or another inventory partition")
        seen_masks.add(mask)
        derived = {key: True for key, enabled in (
            ("shiny", values["shiny"]), ("shadow", values["shadow"]),
            ("is_dynamax", values["dynamax"] or values["gigantamax"]),
        ) if enabled}
        if (not isinstance(tags, dict) or any(type(value) is not bool for value in tags.values())
                or tags != derived):
            raise ValueError(f"Partition {name} tags conflict with its exact query")
        if partition.get("active_session") is not None:
            raise ValueError(f"Partition {name} has an active session")
        status = partition.get("status")
        if status == "completed":
            continue
        if status != "pending":
            raise ValueError(f"Partition {name} is {status!r}; reconcile partial work before launching")
        if partition.get("sessions") != []:
            raise ValueError(f"Pending partition {name} already has session history; reconcile it first")
        selected.append({"name": name, "query": query, "tags": derived})
    if len(seen_masks) != 15:
        raise ValueError("Inventory ledger must retain all 15 non-Normal partitions, including completed ones")
    if not selected:
        raise ValueError("Scan queue has no untouched pending partitions")
    return selected
