#!/usr/bin/env python3
"""Summarize completed SCAN_TIMING spans from fixed snapshots of local logs.

Inclusive durations contain nested work. Exclusive durations subtract the union
of direct-child intervals clipped to their parent. Parallel sibling spans can
still overlap; use observed_union_s for coverage without double counting.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
import os
from pathlib import Path
import statistics


MARKER = "SCAN_TIMING "


def union_duration(intervals):
    """Length of covered time, counting overlapping intervals only once."""
    total = 0.0
    end = None
    for start, stop in sorted(intervals):
        if stop <= start:
            continue
        if end is None or start >= end:
            total += stop - start
        elif stop > end:
            total += stop - end
        end = stop if end is None else max(end, stop)
    return total


def statistics_for(values):
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "total_s": round(math.fsum(ordered), 9),
        "median_s": round(statistics.median(ordered), 9) if ordered else None,
        "p90_s": round(ordered[math.ceil(len(ordered) * .9) - 1], 9) if ordered else None,
    }


def _validate(event):
    if not isinstance(event, dict):
        raise ValueError("event must be a JSON object")
    for key in ("phase", "span_id"):
        if not isinstance(event.get(key), str) or not event[key].strip():
            raise ValueError(f"{key} must be a nonempty string")
    parent = event.get("parent_id")
    if parent is not None and (not isinstance(parent, str) or not parent.strip()
                               or parent == event["span_id"]):
        raise ValueError("parent_id must be null or a distinct nonempty span ID")
    for key in ("start_s", "duration_s"):
        value = event.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{key} must be a finite nonnegative number")
    if not math.isfinite(event["start_s"] + event["duration_s"]):
        raise ValueError("span end must be finite")
    return event


def _read_snapshot(path):
    path = Path(path)
    with path.open("rb") as stream:
        # Do not chase appends while profiling an active log.
        payload = stream.read(os.fstat(stream.fileno()).st_size)
    boundary = payload.rfind(b"\n") + 1
    complete = payload[:boundary]
    source = dict(path=str(path.resolve()), captured_bytes=len(payload),
                  complete_bytes=boundary, ignored_tail_bytes=len(payload) - boundary)
    events, malformed = [], []
    for line_number, raw in enumerate(complete.splitlines(), 1):
        if MARKER.encode() not in raw:
            continue
        location = dict(path=source["path"], line=line_number)
        try:
            text = raw.split(MARKER.encode(), 1)[1].decode("utf-8")
            event = _validate(json.loads(text))
        except (ValueError, UnicodeDecodeError, OverflowError) as exc:
            malformed.append(location | {"reason": str(exc)})
        else:
            events.append((event, location))
    source["complete_timing_events"] = len(events) + len(malformed)
    return source, events, malformed


def summarize(paths):
    """Aggregate multiple files; repeated identical span IDs count only once."""
    sources, malformed, conflicts = [], [], []
    spans, locations, rejected = {}, {}, set()
    duplicates = 0
    for path in paths:
        source, events, invalid = _read_snapshot(path)
        sources.append(source)
        malformed.extend(invalid)
        for event, location in events:
            span_id = event["span_id"]
            if span_id in rejected:
                continue
            if span_id in spans:
                if spans[span_id] == event:
                    duplicates += 1
                else:
                    conflicts.append(dict(span_id=span_id, first=locations[span_id],
                                          conflicting=location))
                    rejected.add(span_id)
                    del spans[span_id]
            else:
                spans[span_id], locations[span_id] = event, location

    children = defaultdict(list)
    missing = defaultdict(list)
    outside = []
    for span_id, event in spans.items():
        parent_id = event.get("parent_id")
        if parent_id is None:
            continue
        if parent_id not in spans:
            missing[parent_id].append(span_id)
            continue
        children[parent_id].append(event)
        parent = spans[parent_id]
        if (event["start_s"] < parent["start_s"]
                or event["start_s"] + event["duration_s"] > parent["start_s"] + parent["duration_s"]):
            outside.append(dict(span_id=span_id, parent_id=parent_id))

    inclusive, exclusive, runs = defaultdict(list), defaultdict(list), defaultdict(list)
    for span_id, event in spans.items():
        start, duration = event["start_s"], event["duration_s"]
        end = start + duration
        covered = union_duration(
            (max(start, child["start_s"]), min(end, child["start_s"] + child["duration_s"]))
            for child in children[span_id]
        )
        inclusive[event["phase"]].append(duration)
        exclusive[event["phase"]].append(max(0.0, duration - covered))
        # Emitter IDs use a random process UUID prefix. Do not merge monotonic
        # epochs from separate processes/boots just because numbers overlap.
        run_id = span_id.split(":", 1)[0] if ":" in span_id else "unscoped"
        runs[run_id].append((start, end))

    run_rows = []
    for run_id, intervals in sorted(runs.items()):
        first, last = min(start for start, _ in intervals), max(end for _, end in intervals)
        run_rows.append(dict(run_id=run_id, start_s=first, end_s=last,
                             observed_elapsed_s=round(last - first, 9),
                             observed_union_s=round(union_duration(intervals), 9)))
    phases = [dict(phase=phase, inclusive=statistics_for(values),
                   exclusive=statistics_for(exclusive[phase]))
              for phase, values in inclusive.items()]
    phases.sort(key=lambda row: (-row["exclusive"]["total_s"], row["phase"]))
    return dict(
        schema=1, sources=sources, span_count=len(spans), runs=run_rows,
        total=dict(
            inclusive_s=round(math.fsum(row["inclusive"]["total_s"] for row in phases), 9),
            exclusive_s=round(math.fsum(row["exclusive"]["total_s"] for row in phases), 9),
            observed_elapsed_s=round(math.fsum(row["observed_elapsed_s"] for row in run_rows), 9),
            observed_union_s=round(math.fsum(row["observed_union_s"] for row in run_rows), 9),
        ),
        phases=phases,
        diagnostics=dict(
            malformed_complete_events=malformed, duplicate_events=duplicates,
            conflicting_span_ids=conflicts,
            missing_parents=[dict(parent_id=parent, child_span_ids=sorted(ids))
                             for parent, ids in sorted(missing.items())],
            children_outside_parent=outside,
        ),
        notes=[
            "Only newline-terminated completed events are included; an unterminated tail is ignored.",
            "P90 uses nearest rank; inclusive durations contain nested work.",
            "Exclusive duration subtracts the union of clipped direct-child intervals, across all threads.",
            "Exclusive phase totals still overlap for parallel siblings; neither phase-total sum is wall time.",
            "Observed union counts covered intervals once per emitter process; elapsed includes gaps within each process.",
            "Missing parents can be unfinished spans in a growing log. Missing child events can overstate exclusive time.",
            "Conflicting duplicate IDs are excluded; identical events repeated across inputs are deduplicated.",
        ],
    )


def markdown(report):
    total, diagnostics = report["total"], report["diagnostics"]
    lines = ["# Scan span timings", "",
             f"{report['span_count']} completed spans in {len(report['runs'])} process runs. "
             f"Observed coverage: **{total['observed_union_s']:.3f} s**; "
             f"elapsed intervals including gaps: {total['observed_elapsed_s']:.3f} s.", "",
             "All durations below are seconds. Inclusive totals contain nested work. Exclusive durations "
             "subtract the union of direct children; parallel siblings can still overlap, so phase totals are not wall time.", "",
             "| Phase | Count | Inclusive total | Median | P90 | Exclusive total | Median | P90 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in report["phases"]:
        inc, exc = row["inclusive"], row["exclusive"]
        phase = row["phase"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {phase} | {inc['count']} | {inc['total_s']:.3f} | {inc['median_s']:.3f} | "
                     f"{inc['p90_s']:.3f} | {exc['total_s']:.3f} | {exc['median_s']:.3f} | {exc['p90_s']:.3f} |")
    lines.extend(["", f"Malformed complete events: {len(diagnostics['malformed_complete_events'])}; "
                  f"missing parent IDs: {len(diagnostics['missing_parents'])}; "
                  f"conflicting IDs excluded: {len(diagnostics['conflicting_span_ids'])}; "
                  f"identical duplicates skipped: {diagnostics['duplicate_events']}.", ""])
    for event in diagnostics["malformed_complete_events"][:10]:
        lines.append(f"- Malformed: {event['path']}:{event['line']} — {event['reason']}")
    for entry in diagnostics["missing_parents"][:10]:
        lines.append(f"- Missing parent `{entry['parent_id']}` for {len(entry['child_span_ids'])} child span(s); "
                     "it may still be running.")
    if diagnostics["children_outside_parent"]:
        lines.append(f"- {len(diagnostics['children_outside_parent'])} child interval(s) extend outside their parent; subtraction was clipped.")
    if len(diagnostics["malformed_complete_events"]) > 10 or len(diagnostics["missing_parents"]) > 10:
        lines.append("- Additional diagnostics are available with --format json.")
    lines.extend(["", "Inputs:", ""])
    for source in report["sources"]:
        lines.append(f"- {source['path']}: {source['complete_bytes']} complete bytes; "
                     f"{source['ignored_tail_bytes']} unterminated tail bytes ignored.")
    lines.extend(["", "P90 uses nearest rank. Missing child events can overstate exclusive time. "
                  "Run totals are added separately because independent process clocks may have different epochs.", ""])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--output", type=Path, help="Write the report here instead of stdout")
    args = parser.parse_args(argv)
    try:
        report = summarize(args.logs)
        output = json.dumps(report, indent=2, allow_nan=False) + "\n" if args.format == "json" else markdown(report)
        if args.output:
            args.output.write_text(output)
        else:
            print(output, end="")
    except OSError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
