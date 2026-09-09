"""Completed span accounting without double-counting nested/parallel children."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts.summarize_scan_spans import main, markdown, statistics_for, summarize, union_duration


def event(span_id, phase, start, duration, parent=None, thread=1):
    return dict(span_id=span_id, parent_id=parent, phase=phase, start_s=start,
                duration_s=duration, thread_id=thread, status="ok")


class SpanSummaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def log(self, events=(), *, name="scan.log", suffix=b""):
        path = self.directory / name
        path.write_bytes(b"ordinary log message\n" + b"".join(
            b"21:00:00 INFO pokemgr.timing: SCAN_TIMING " + json.dumps(item).encode() + b"\n"
            for item in events
        ) + suffix)
        return path

    def phase(self, report, phase):
        return next(row for row in report["phases"] if row["phase"] == phase)

    def test_parallel_children_use_union_and_grandchildren_are_not_subtracted_twice(self):
        # Completion order is intentionally children-first, as in the emitter.
        report = summarize([self.log([
            event("run:g", "ocr", 3, 1, "run:hp", 2),
            event("run:hp", "hp", 2, 5, "run:root", 2),
            event("run:name", "name", 5, 4, "run:root"),
            event("run:root", "acquire", 0, 10),
        ])])
        self.assertEqual(self.phase(report, "acquire")["exclusive"]["total_s"], 3)
        self.assertEqual(self.phase(report, "hp")["exclusive"]["total_s"], 4)
        self.assertEqual(report["total"]["inclusive_s"], 20)
        self.assertEqual(report["total"]["exclusive_s"], 12)  # Sibling overlap remains explicit.
        self.assertEqual(report["total"]["observed_union_s"], 10)
        self.assertEqual(report["diagnostics"]["missing_parents"], [])

    def test_child_intervals_are_clipped_to_parent(self):
        report = summarize([self.log([
            event("r:p", "parent", 2, 6),
            event("r:a", "child", 0, 3, "r:p"),
            event("r:b", "child", 7, 3, "r:p"),
            event("r:c", "child", 20, 1, "r:p"),
        ])])
        self.assertEqual(self.phase(report, "parent")["exclusive"]["total_s"], 4)
        self.assertEqual(len(report["diagnostics"]["children_outside_parent"]), 3)

    def test_statistics_use_median_nearest_rank_p90_and_zero_durations(self):
        values = statistics_for(range(1, 11))
        self.assertEqual(values, dict(count=10, total_s=55, median_s=5.5, p90_s=9))
        self.assertEqual(statistics_for([0])["p90_s"], 0)
        self.assertIsNone(statistics_for([])["median_s"])
        self.assertEqual(union_duration([(5, 5), (0, 2), (1, 3), (7, 9), (2, 1)]), 5)

    def test_multiple_logs_deduplicate_and_resolve_cross_file_parent(self):
        child = event("run:c", "child", 2, 3, "run:p")
        left = self.log([child], name="old.log")
        right = self.log([child, event("run:p", "parent", 0, 10)], name="new.log")
        report = summarize([left, right])
        self.assertEqual(report["span_count"], 2)
        self.assertEqual(report["diagnostics"]["duplicate_events"], 1)
        self.assertEqual(report["diagnostics"]["missing_parents"], [])
        self.assertEqual(self.phase(report, "parent")["exclusive"]["total_s"], 7)

    def test_conflicting_ids_are_excluded_instead_of_choosing_a_timing(self):
        report = summarize([self.log([
            event("r:p", "parent", 0, 10), event("r:p", "parent", 0, 20),
            event("r:c", "child", 1, 2, "r:p"),
        ])])
        self.assertEqual(report["span_count"], 1)
        self.assertEqual(len(report["diagnostics"]["conflicting_span_ids"]), 1)
        self.assertEqual(report["diagnostics"]["missing_parents"], [
            dict(parent_id="r:p", child_span_ids=["r:c"]),
        ])

    def test_incomplete_tail_is_ignored_but_malformed_complete_events_are_reported(self):
        malformed = b"SCAN_TIMING {broken}\nSCAN_TIMING [1,2]\n"
        tail = b'SCAN_TIMING {"phase":"unfinished"'
        path = self.log([event("r:a", "work", 1, 2)], suffix=malformed + tail)
        report = summarize([path])
        self.assertEqual(report["span_count"], 1)
        self.assertEqual(len(report["diagnostics"]["malformed_complete_events"]), 2)
        self.assertEqual(report["sources"][0]["ignored_tail_bytes"], len(tail))
        self.assertEqual(report["diagnostics"]["malformed_complete_events"][0]["line"], 3)

    def test_invalid_numeric_fields_and_self_parent_do_not_enter_aggregates(self):
        for change in ({"duration_s": -1}, {"start_s": True}, {"duration_s": float("inf")},
                       {"duration_s": float("nan")}, {"parent_id": "r:a"}, {"phase": ""}):
            with self.subTest(change=change):
                report = summarize([self.log([event("r:a", "work", 1, 2) | change])])
                self.assertEqual(report["span_count"], 0)
                self.assertEqual(len(report["diagnostics"]["malformed_complete_events"]), 1)

    def test_separate_emitter_epochs_are_not_merged_into_one_wall_interval(self):
        report = summarize([self.log([
            event("first:a", "work", 0, 10), event("second:a", "work", 0, 10),
        ])])
        self.assertEqual(report["total"]["observed_union_s"], 20)
        self.assertEqual(len(report["runs"]), 2)

    def test_missing_parent_is_flagged_and_markdown_explains_overlap(self):
        report = summarize([self.log([event("r:a", "work", 1, 2, "r:unfinished")])])
        rendered = markdown(report)
        self.assertIn("Missing parent `r:unfinished`", rendered)
        self.assertIn("parallel siblings can still overlap", rendered)
        self.assertIn("| work | 1 | 2.000", rendered)

    def test_cli_json_stdout_and_markdown_file_are_readable(self):
        path = self.log([event("r:a", "work", 1, 2)])
        output = io.StringIO()
        with redirect_stdout(output):
            main([str(path), "--format", "json"])
        self.assertEqual(json.loads(output.getvalue())["span_count"], 1)
        destination = self.directory / "report.md"
        main([str(path), "--output", str(destination)])
        self.assertIn("# Scan span timings", destination.read_text())

    def test_empty_snapshot_has_finite_zero_totals(self):
        report = summarize([self.log()])
        self.assertEqual(report["total"]["observed_union_s"], 0)
        self.assertEqual(report["phases"], [])
        json.dumps(report, allow_nan=False)
        self.assertIn("0 completed spans", markdown(report))


if __name__ == "__main__":
    unittest.main()
