"""Summarize completed scan cycles from one or two existing text logs.

Reads a fixed byte snapshot of each file, without interacting with the scanner
or phone. Existing logs contain whole-second wall times, so cycle and phase
durations are approximate; explicitly logged tap-to-capture times retain their
millisecond precision. Run with the active log first and an optional predecessor.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import statistics


LINE = re.compile(r"^(\d{2}:\d{2}:\d{2}) (DEBUG|INFO|WARNING|ERROR|CRITICAL) ([^:]+): (.*)$")
ACCEPTED = re.compile(r"^Accepted (.+) CP(\d+) from (\S+) evidence at L([\d.]+)$")
SETTLE = re.compile(r"^Appraisal settle (\d+)/6:")
TAP = re.compile(r"^CP (animation|rotated animation) attempt (\d+)/4: capture returned ([\d.]+)s after tap")
ROTATION = re.compile(r"^CP rotation attempt (\d+)/4:")


def stats(values):
    values = list(values)
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    return {
        "count": len(values), "total_s": round(sum(values), 6),
        "mean_s": round(statistics.mean(values), 6),
        "median_s": round(statistics.median(values), 6),
        "min_s": round(min(values), 6), "max_s": round(max(values), 6),
        "p90_nearest_rank_s": round(ordered[max(0, (9 * len(values) + 9) // 10 - 1)], 6),
    }


def read_events(path):
    payload = path.read_bytes()
    # A running logger may be midway through its final write.
    complete = payload[:payload.rfind(b"\n") + 1]
    events = []
    day_offset = 0
    previous_time = None
    for line_number, line in enumerate(complete.decode(errors="replace").splitlines(), 1):
        match = LINE.match(line)
        if not match:
            continue
        clock, level, logger, message = match.groups()
        hours, minutes, seconds = map(int, clock.split(":"))
        within_day = hours * 3600 + minutes * 60 + seconds
        if previous_time is not None and within_day < previous_time - 12 * 3600:
            day_offset += 24 * 3600
        previous_time = within_day
        events.append(dict(clock=clock, second=within_day + day_offset,
                           level=level, logger=logger, message=message, line=line_number))
    return events, dict(path=str(path.resolve()), captured_bytes=len(payload),
                        complete_bytes=len(complete), sha256=hashlib.sha256(complete).hexdigest())


def analyze(path):
    events, source = read_events(path)
    source["last_complete_event"] = events[-1]["clock"] if events else None
    cycles = []
    window = []
    boundary = None
    session_number = 0
    session_first = False
    for event in events:
        message = event["message"]
        if message.startswith("Starting stable scan session:"):
            boundary = event
            window = []
            session_number += 1
            session_first = True
        if boundary is None:
            continue
        window.append(event)
        accepted = ACCEPTED.match(message)
        if not accepted:
            continue
        species, cp, evidence, level = accepted.groups()
        settles = [entry for entry in window if SETTLE.match(entry["message"])]
        taps = [TAP.match(entry["message"]) for entry in window if TAP.match(entry["message"])]
        rotations = [entry for entry in window if ROTATION.match(entry["message"])]
        recovery_logs = [entry for entry in window if (
            TAP.match(entry["message"]) or ROTATION.match(entry["message"])
            or entry["message"].startswith("Recovery:")
            or "read from cancelled power-up preview" in entry["message"]
        )]
        row_lines = [re.match(r"^#(\d+) ", entry["message"]) for entry in window]
        row_numbers = [int(match[1]) for match in row_lines if match]
        seconds = event["second"] - boundary["second"]
        is_recovery = evidence in ("screen_after_animation", "screen_after_powerup_preview") or bool(recovery_logs)
        cycle = dict(
            session_number=session_number, first_in_session=session_first,
            row=row_numbers[-1] if row_numbers else None, species=species, cp=int(cp),
            evidence=evidence, level=float(level), category="recovery" if is_recovery else "routine",
            from_clock=boundary["clock"], to_clock=event["clock"], completed_cycle_s=seconds,
            from_line=boundary["line"], to_line=event["line"],
            first_settle_to_accept_s=event["second"] - settles[0]["second"] if settles else None,
            previous_accept_to_first_settle_s=(settles[0]["second"] - boundary["second"])
                if settles and not session_first else None,
            settle_observations=len(settles),
            settle_attempts=[int(SETTLE.match(entry["message"])[1]) for entry in settles],
            initial_taps=sum(match[1] == "animation" for match in taps),
            rotated_taps=sum(match[1] == "rotated animation" for match in taps),
            rotations=len(rotations),
            logged_tap_to_capture_s=[float(match[3]) for match in taps],
            appraisal_reopens=sum(entry["message"].startswith("Recovery: opening appraisal") for entry in window),
            cancelled_preview_reads=sum(entry["message"].startswith("Current CP") and
                                        "read from cancelled power-up preview" in entry["message"] for entry in window),
            observed_recovery_log_to_accept_s=event["second"] - recovery_logs[0]["second"] if recovery_logs else None,
            pause_resume_events=[entry["message"] for entry in window if
                                 entry["message"].startswith(("Paused at", "Resumed"))],
        )
        cycles.append(cycle)
        boundary = event
        window = []
        session_first = False
    grouped = defaultdict(list)
    for cycle in cycles:
        grouped[cycle["category"]].append(cycle["completed_cycle_s"])
    total = sum(cycle["completed_cycle_s"] for cycle in cycles)
    by_evidence = {}
    for evidence in sorted({cycle["evidence"] for cycle in cycles}):
        by_evidence[evidence] = stats(cycle["completed_cycle_s"] for cycle in cycles if cycle["evidence"] == evidence)
    errors = [dict(clock=event["clock"], line=event["line"], message=event["message"])
              for event in events if event["level"] in ("ERROR", "CRITICAL")]
    return dict(
        source=source,
        logged_session_starts=session_number,
        completed_cycles=stats(cycle["completed_cycle_s"] for cycle in cycles),
        completed_rate_per_min=round(len(cycles) * 60 / total, 3) if total else None,
        by_category={key: stats(values) for key, values in sorted(grouped.items())},
        by_evidence=by_evidence,
        recovery_share_of_completed_time=round(sum(grouped["recovery"]) / total, 6) if total else None,
        coarse_phases={
            "previous_accept_to_first_settle": stats(cycle["previous_accept_to_first_settle_s"] for cycle in cycles
                                                      if cycle["previous_accept_to_first_settle_s"] is not None),
            "first_settle_to_accept_routine": stats(cycle["first_settle_to_accept_s"] for cycle in cycles
                                                    if cycle["category"] == "routine" and cycle["first_settle_to_accept_s"] is not None),
            "first_settle_to_accept_recovery": stats(cycle["first_settle_to_accept_s"] for cycle in cycles
                                                     if cycle["category"] == "recovery" and cycle["first_settle_to_accept_s"] is not None),
        },
        settle_attempt_histogram=dict(Counter(str(attempt) for cycle in cycles for attempt in cycle["settle_attempts"])),
        logged_actions={key: sum(cycle[key] for cycle in cycles) for key in
                        ("initial_taps", "rotated_taps", "rotations", "appraisal_reopens", "cancelled_preview_reads")},
        logged_tap_to_capture=stats(value for cycle in cycles for value in cycle["logged_tap_to_capture_s"]),
        skipped_log_messages=[dict(clock=event["clock"], message=event["message"]) for event in events
                              if event["message"].startswith("Skipped")],
        errors=errors,
        uncompleted_tail=dict(from_clock=boundary["clock"] if boundary else None,
                              through_clock=events[-1]["clock"] if events else None,
                              log_events=len(window),
                              excluded_from_completed_cycle_metrics=True),
        cycles=cycles,
    )


def benchmarks(directory):
    capture = json.loads((directory / "capture-formats.json").read_text())
    acquisition = json.loads((directory / "real-acquisition-formats.json").read_text())
    sequence = json.loads((directory / "live-sequence.json").read_text())
    return dict(
        sources=[str((directory / name).resolve()) for name in
                 ("capture-formats.json", "real-acquisition-formats.json", "live-sequence.json")],
        capture={format: {
            "command_and_decode": stats(row["command_s"] + row["decode_s"] for row in capture if row["format"] == format),
            "verification": stats(row["verification_s"] for row in capture if row["format"] == format),
        } for format in ("PNG", "JPEG")},
        gardevoir_acquisition={format: stats(row["elapsed_s"] for row in acquisition if row["format"] == format)
                              for format in ("png", "jpeg")},
        six_pokemon_acquisition=stats(row["acquisition_s"] for row in sequence),
        five_advances=stats(row["advance_s"] for row in sequence if "advance_s" in row),
        acquisition_capture_counts=dict(Counter(str(row["captures"]) for row in sequence)),
        note="Separate controlled earlier benchmark; not timings sampled from the currently running scan.",
    )


def markdown(report):
    lines = ["# Scan action timing from existing logs", "", f"Snapshot generated: {report['generated_at']}.", "",
             "Completed cycles run from the preceding acceptance to the next acceptance; the first starts at the stable-scan session marker. They include navigation, capture, OCR, optional recovery, and storage. They are not isolated acquisition timings. Whole-second log times make each difference approximate (up to about one second of rounding uncertainty).", ""]
    for index, profile in enumerate(report["logs"]):
        title = "Latest selected log snapshot" if index == 0 else "Predecessor snapshot"
        source = profile["source"]
        lines.extend([f"## {title}", "", f"Source: [{Path(source['path']).name}](<{source['path']}>); through **{source['last_complete_event']}**.", "",
                      f"**{profile['completed_cycles']['count']} completed rows**, {profile['completed_cycles'].get('total_s', 0):.0f} s of completed cycle time, **{profile['completed_rate_per_min']} Pokémon/min**. The in-progress or failed tail is excluded.", "",
                      "| Path | Count | Total seconds | Mean seconds | Median seconds | P90 seconds |",
                      "|---|---:|---:|---:|---:|---:|"])
        for category, values in profile["by_category"].items():
            lines.append(f"| {category} | {values['count']} | {values['total_s']:.0f} | {values['mean_s']:.2f} | {values['median_s']:.1f} | {values['p90_nearest_rank_s']:.1f} |")
        lines.extend(["", f"Recovery rows consumed **{100 * profile['recovery_share_of_completed_time']:.1f}%** of completed cycle time. This is the full time spent on those Pokémon, including normal work, not isolated recovery overhead.", "",
                      "| Evidence path | Count | Mean cycle seconds |", "|---|---:|---:|"])
        for evidence, values in profile["by_evidence"].items():
            lines.append(f"| {evidence} | {values['count']} | {values['mean_s']:.2f} |")
        lines.extend(["", "| Observable phase | Samples | Mean seconds | Median seconds |", "|---|---:|---:|---:|"])
        for phase, values in profile["coarse_phases"].items():
            if values["count"]:
                lines.append(f"| {phase.replace('_', ' ')} | {values['count']} | {values['mean_s']:.2f} | {values['median_s']:.1f} |")
        lines.extend(["", f"Settle attempt numbers: `{profile['settle_attempt_histogram']}`. Every logged attempt compares a pair; an attempt numbered 1 does not mean one screenshot. Extra calls after returning to appraisal can start again at 1.", "",
                      f"Logged recovery actions: `{profile['logged_actions']}`. Successful preview reads and reopen messages count only their visible log events; missing action logs cannot establish a zero count.", ""])
        tap = profile["logged_tap_to_capture"]
        if tap["count"]:
            lines.append(f"Explicit tap-return to capture-return times: {tap['count']} observations, mean **{tap['mean_s']:.3f} s**, range {tap['min_s']:.3f}–{tap['max_s']:.3f} s. These include intentional animation wait and capture/transfer, but exclude the input command and subsequent OCR.")
        lines.extend(["", "Longest completed cycles:", ""])
        for row in sorted(profile["cycles"], key=lambda row: row["completed_cycle_s"], reverse=True)[:8]:
            lines.append(f"- #{row['row']} {row['species']} CP{row['cp']}: **{row['completed_cycle_s']} s**, {row['evidence']}; {row['initial_taps']} initial taps, {row['rotations']} rotations, {row['rotated_taps']} rotated taps ({row['from_clock']}–{row['to_clock']}).")
        if profile["errors"]:
            lines.extend(["", "Logged errors (excluded failed tail can contain additional time):", ""])
            lines.extend(f"- {error['clock']}: {error['message']}" for error in profile["errors"])
        lines.extend(["", f"Skipped-position messages: {len(profile['skipped_log_messages'])}.", ""])
    bench = report["controlled_benchmarks"]
    lines.extend(["## Separately measured action costs", "", bench["note"], "",
                  "| Earlier measurement | Samples | Mean seconds |", "|---|---:|---:|"])
    for format, values in bench["capture"].items():
        lines.append(f"| {format} capture/transfer + decode | {values['command_and_decode']['count']} | {values['command_and_decode']['mean_s']:.3f} |")
        lines.append(f"| Display verification before {format} capture | {values['verification']['count']} | {values['verification']['mean_s']:.3f} |")
    for format, values in bench["gardevoir_acquisition"].items():
        lines.append(f"| Complete Gardevoir acquisition, {format} | {values['count']} | {values['mean_s']:.3f} |")
    advances = bench["five_advances"]
    lines.extend([f"| Confirmed advance (fresh screenshot, verification and swipe) | {advances['count']} | {advances['mean_s']:.3f} |", "",
                  "Sources: " + ", ".join(f"[{Path(path).name}](<{path}>)" for path in bench["sources"]) + ".", "",
                  "## Measurement limits", "",
                  "- Existing logs do not split display verification, screenshot acquisition, transport, decoding, OCR regions, inference, database writes, and actual gesture execution.",
                  "- The gap before the first settle event mixes the previous advance and initial captures. The gap after it mixes appraisal reading, validation, any recovery, and acceptance.",
                  "- Recovery entry is not explicitly logged. First recovery-action log to acceptance is only an observed tail; the full recovery phase starts earlier.",
                  "- PNG/JPEG benchmark samples are earlier controlled observations on this phone, not a concurrent profile. They cannot be added to coarse log phases without double counting.",
                  "- A still-running or failed final cycle is excluded. No scan pause, phone operation, new ADB connection, or production change was required to build this report.", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path("cache/scan-action-timing"))
    parser.add_argument("--benchmarks", type=Path, default=Path("cache/scan-speed-v2"))
    args = parser.parse_args()
    if len(args.logs) > 2:
        parser.error("Supply at most the active log and its predecessor")
    report = dict(generated_at=datetime.now(timezone.utc).isoformat(), logs=[analyze(path) for path in args.logs],
                  controlled_benchmarks=benchmarks(args.benchmarks))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "log-profile.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.output / "log-profile.md").write_text(markdown(report))
    print(json.dumps({"output": str(args.output.resolve()), "logs": [
        {"path": profile["source"]["path"], "through": profile["source"]["last_complete_event"],
         "completed": profile["completed_cycles"], "categories": profile["by_category"],
         "recovery_share": profile["recovery_share_of_completed_time"]} for profile in report["logs"]]}, indent=2))


if __name__ == "__main__":
    main()
