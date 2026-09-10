"""Inventory partitions must be disjoint, pending, and safe to tag."""

# TRACEWEAVER: file-role=inventory-queue-tests; req=REQ-SCAN-003,REQ-DATA-001; trace=TRACE-SCAN-003,TRACE-DATA-001; verifies=VER-SCAN-001
import json
from pathlib import Path
import tempfile
import unittest

from pokemgr.indexer.scan_queue import load_scan_queue


def partition(mask=1):
    traits = ("shiny", "shadow", "dynamax", "gigantamax")
    flags = {trait: bool(mask & (1 << i)) for i, trait in enumerate(traits)}
    tags = {key: True for key, value in (
        ("shiny", flags["shiny"]), ("shadow", flags["shadow"]),
        ("is_dynamax", flags["dynamax"] or flags["gigantamax"]),
    ) if value}
    return {"id": f"category-{mask:02d}",
            "query": "&".join(("" if flags[trait] else "!") + trait for trait in traits),
            "tags": tags, "status": "pending", "sessions": [], "observed_count": None}


def ledger():
    return {"version": 1, "normal": {"completed": True, "active_session": None},
            "remaining_partitions": [partition(mask) | {"status": "pending" if mask == 1 else "completed"}
                                     for mask in range(1, 16)]}


class ScanQueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.directory) / "state.json"

    def load(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")
        return load_scan_queue(self.path)

    def test_all_fifteen_non_normal_masks_are_disjoint_and_tags_are_derived(self):
        value = ledger()
        value["remaining_partitions"] = [partition(mask) for mask in range(1, 16)]
        result = self.load(value)
        self.assertEqual(15, len(result))
        self.assertEqual({"shiny": True}, result[0]["tags"])
        self.assertEqual({"is_dynamax": True}, result[7]["tags"])
        self.assertEqual({"shiny": True, "shadow": True, "is_dynamax": True}, result[-1]["tags"])
        self.assertEqual(value, json.loads(self.path.read_text()))
        result[0]["tags"]["shadow"] = True
        self.assertEqual({"shiny": True}, load_scan_queue(self.path)[0]["tags"])

    def test_normal_must_be_explicitly_completed_with_no_active_session(self):
        for normal in ({}, {"completed": False}, {"completed": 1}, {"completed": "true"},
                       {"completed": True}, {"completed": True, "active_session": {"id": "live"}}):
            with self.subTest(normal=normal):
                value = ledger()
                value["normal"] = normal
                with self.assertRaises(ValueError):
                    self.load(value)
        value = ledger() | {"active_session": "live"}
        with self.assertRaises(ValueError):
            self.load(value)

    def test_pending_work_with_history_or_active_session_is_held(self):
        for changes in ({"sessions": ["old-session"]}, {"sessions": None},
                        {"active_session": "live"}, {"status": "running"},
                        {"status": "partial"}, {"status": "paused"}, {"status": "failed"}):
            with self.subTest(changes=changes):
                value = ledger()
                value["remaining_partitions"][0].update(changes)
                with self.assertRaises(ValueError):
                    self.load(value)

    def test_completed_partitions_are_skipped_but_an_empty_queue_is_rejected(self):
        value = ledger()
        value["remaining_partitions"][0]["status"] = "completed"
        value["remaining_partitions"][1]["status"] = "pending"
        self.assertEqual(["category-02"], [item["name"] for item in self.load(value)])
        completed = [item | {"status": "completed"} for item in value["remaining_partitions"]]
        for parts in ([], completed, None, {}):
            with self.subTest(parts=parts):
                with self.assertRaises(ValueError):
                    self.load(value | {"remaining_partitions": parts})

    def test_omitted_partition_cannot_be_mistaken_for_completed_coverage(self):
        value = ledger()
        value["remaining_partitions"].pop()
        with self.assertRaisesRegex(ValueError, "all 15"):
            self.load(value)

    def test_queries_require_exactly_one_literal_for_each_trait(self):
        queries = ("shiny", "shiny&shadow&dynamax", "shiny&shadow&dynamax&gigantamax&lucky",
                   "shiny&!shiny&!dynamax&!gigantamax", "shiny|!shadow&!dynamax&!gigantamax",
                   "shiny& !shadow&!dynamax&!gigantamax", "SHINY&!shadow&!dynamax&!gigantamax",
                   "shiny&!shadow&!!dynamax&!gigantamax", "", None)
        for query in queries:
            with self.subTest(query=query):
                value = ledger()
                value["remaining_partitions"][0]["query"] = query
                with self.assertRaises(ValueError):
                    self.load(value)

    def test_normal_and_duplicate_masks_or_identifiers_are_rejected(self):
        repeated = partition()
        repeated["id"] = "different-id"
        repeated["query"] = "!shadow&shiny&!gigantamax&!dynamax"
        for parts in ([partition(0)], [partition(), repeated], [partition(), partition()]):
            with self.subTest(parts=parts):
                with self.assertRaises(ValueError):
                    self.load(ledger() | {"remaining_partitions": parts})

    def test_conflicting_or_arbitrary_sql_column_tags_are_rejected(self):
        for tags in ({}, {"shiny": 1}, {"shiny": False}, {"shiny": True, "shadow": True},
                     {"shiny": True, "is_dynamax": False}, {"shiny": True, "position = 0": True}, []):
            with self.subTest(tags=tags):
                value = ledger()
                value["remaining_partitions"][0]["tags"] = tags
                with self.assertRaises(ValueError):
                    self.load(value)

    def test_malformed_json_wrong_versions_and_duplicate_json_fields_are_rejected(self):
        for text in ("", "0", "[]", "{", '{"version":1,"version":1}'):
            with self.subTest(text=text):
                self.path.write_text(text)
                with self.assertRaises(ValueError):
                    load_scan_queue(self.path)
        for version in (None, True, 2, "1"):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    self.load(ledger() | {"version": version})

    def test_bad_partition_structures_and_names_are_rejected(self):
        for item in (None, [], "query", partition() | {"id": "<b>shiny</b>"}, partition() | {"id": ""}):
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    self.load(ledger() | {"remaining_partitions": [item]})


if __name__ == "__main__":
    unittest.main()
