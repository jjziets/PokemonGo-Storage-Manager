# TRACEWEAVER: file-role=unique-favorite-change-persistence-tests; req=REQ-MASS-001,REQ-DATA-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
"""Only confirmed OFF-to-ON changes can accumulate across filtered laps."""

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from pokemgr.data.database import PokemonDatabase
from pokemgr.execution.executor import Executor
from pokemgr.execution.favorite_sync import FavoriteSync
from tests.test_database_positions import pokemon_read


class FavoriteChangeSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "collection.db"
        self.db = PokemonDatabase(self.path)
        self.addCleanup(self.db.close)
        self.db.create_session("one", "test-device")
        self.db.create_session("two", "test-device")

    def insert(self, *, session="one", **kwargs):
        return self.db.insert_pokemon(pokemon_read(**kwargs), session, len(self.db.get_all()))

    def state(self):
        with closing(sqlite3.connect(self.path)) as conn:
            return dict(conn.execute("SELECT id, favorited FROM pokemon"))

    def sync(self):
        rows = self.db.get_all()
        return FavoriteSync(self.db, rows, Executor._keeper_key, changes_only=True), rows

    def test_unique_confirmed_change_commits_immediately(self):
        row_id = self.insert()
        sync, rows = self.sync()

        sync.observe(Executor._keeper_key(rows[0]), True)

        self.assertEqual({row_id: 1}, self.state())
        self.assertEqual({"db_synced": 1, "db_unresolved": 0}, sync.result())

    def test_duplicate_change_certificates_accumulate_across_laps(self):
        a, b = self.insert(), self.insert()
        sync, rows = self.sync()
        key = Executor._keeper_key(rows[0])
        sync.observe(key, True)
        self.assertEqual({a: 0, b: 0}, self.state())

        sync.new_traversal()
        sync.observe(key, True)

        self.assertEqual({a: 1, b: 1}, self.state())
        self.assertEqual(2, sync.observations[key])
        self.assertEqual({"db_synced": 2, "db_unresolved": 0}, sync.result())

    def test_partial_duplicate_changes_do_not_assign_arbitrary_stored_rows(self):
        ids = [self.insert() for _ in range(3)]
        sync, rows = self.sync()
        key = Executor._keeper_key(rows[0])
        sync.observe(key, True)
        sync.new_traversal()
        sync.observe(key, True)
        sync.new_traversal()

        self.assertEqual(dict.fromkeys(ids, 0), self.state())
        self.assertEqual((0, 2), (sync.result()["db_synced"], sync.result()["db_unresolved"]))

    def test_recorded_starred_companion_is_not_a_free_change_certificate(self):
        a, b = self.insert(), self.insert(favorited=True)
        sync, rows = self.sync()
        key = Executor._keeper_key(rows[0])
        sync.observe(key, True)
        sync.new_traversal()

        self.assertEqual({a: 0, b: 1}, self.state())
        self.assertEqual((0, 1), (sync.result()["db_synced"], sync.result()["db_unresolved"]))
        sync.observe(key, True)
        self.assertEqual({a: 1, b: 1}, self.state())
        self.assertEqual({"db_synced": 2, "db_unresolved": 0}, sync.result())

    def test_different_signatures_do_not_contribute_to_each_others_certificate(self):
        a, b, c = self.insert(), self.insert(), self.insert(cp=20)
        sync, rows = self.sync()
        duplicate_key = Executor._keeper_key(next(row for row in rows if row.id == a))
        unique_key = Executor._keeper_key(next(row for row in rows if row.id == c))
        sync.observe(duplicate_key, True)
        sync.observe(unique_key, True)
        self.assertEqual({a: 0, b: 0, c: 1}, self.state())
        sync.new_traversal()
        sync.observe(duplicate_key, True)

        self.assertEqual({a: 1, b: 1, c: 1}, self.state())
        self.assertEqual({"db_synced": 3, "db_unresolved": 0}, sync.result())

    def test_multiple_scan_sessions_remain_unresolved_after_all_changes(self):
        a, b = self.insert(), self.insert(session="two")
        sync, rows = self.sync()
        key = Executor._keeper_key(rows[0])
        sync.observe(key, True)
        sync.new_traversal()
        sync.observe(key, True)

        self.assertEqual({a: 0, b: 0}, self.state())
        self.assertEqual((0, 2), (sync.result()["db_synced"], sync.result()["db_unresolved"]))

    def test_missing_invalid_or_duplicate_ids_cannot_complete_a_group(self):
        row_sets = [
            [SimpleNamespace(scan_session_id="one")],
            *[[SimpleNamespace(id=value, scan_session_id="one")]
              for value in (None, "1", True, 0, -1)],
            [SimpleNamespace(id=1, scan_session_id="one"),
             SimpleNamespace(id=1, scan_session_id="one")],
        ]
        for rows in row_sets:
            with self.subTest(rows=rows):
                db = Mock()
                sync = FavoriteSync(db, rows, lambda _row: "key", changes_only=True)
                for _ in rows:
                    sync.observe("key", True)
                    sync.new_traversal()
                db.update_favorited_many.assert_not_called()
                self.assertEqual((0, len(rows)),
                                 (sync.result()["db_synced"], sync.result()["db_unresolved"]))

    def test_unsupported_targets_are_rejected_before_any_certificate_or_save(self):
        self.insert()
        sync, rows = self.sync()
        key = Executor._keeper_key(rows[0])
        for target in (False, None, 1, "on"):
            with self.subTest(target=target):
                with self.assertRaisesRegex(ValueError, "OFF-to-ON"):
                    sync.observe(key, target)
        self.assertEqual(0, sync.confirmed)
        self.assertFalse(sync.observations)
        self.assertFalse(sync.all_observations)
        self.assertEqual({"db_synced": 0, "db_unresolved": 0}, sync.result())
        self.assertTrue(all(value == 0 for value in self.state().values()))

    def test_uniform_completion_never_bypasses_changes_only_evidence(self):
        a, b = self.insert(shiny=True), self.insert(shiny=True)
        sync, rows = self.sync()
        sync.observe(Executor._keeper_key(rows[0]), True)
        for query, target in (("shiny", True), ("cp0-", False)):
            sync.complete_uniform_pass(query, target)

        self.assertEqual({a: 0, b: 0}, self.state())
        self.assertFalse(sync.uniform_complete)
        self.assertEqual((0, 1), (sync.result()["db_synced"], sync.result()["db_unresolved"]))

    def test_default_mode_still_discards_partial_duplicate_reads_on_restart(self):
        a, b = self.insert(), self.insert()
        rows = self.db.get_all()
        sync = FavoriteSync(self.db, rows, Executor._keeper_key)
        key = Executor._keeper_key(rows[0])
        sync.observe(key, True)
        sync.new_traversal()
        sync.observe(key, True)
        self.assertEqual({a: 0, b: 0}, self.state())
        sync.observe(key, True)
        self.assertEqual({a: 1, b: 1}, self.state())
        sync.complete_uniform_pass("cp0-", False)
        self.assertEqual({a: 0, b: 0}, self.state())

    def test_atomic_save_error_retains_unresolved_certificates(self):
        a, b = self.insert(), self.insert()
        sync, rows = self.sync()
        self.db.conn.execute("DELETE FROM pokemon WHERE id = ?", (b,))
        self.db.conn.commit()
        key = Executor._keeper_key(rows[0])
        sync.observe(key, True)
        sync.new_traversal()
        sync.observe(key, True)

        self.assertEqual({a: 0}, self.state())
        self.assertIn("could not be saved", sync.result()["error"])
        self.assertEqual((0, 2), (sync.result()["db_synced"], sync.result()["db_unresolved"]))
        self.assertEqual(2, sync.observations[key])

    def test_extra_changes_remain_unresolved_without_resaving_a_completed_group(self):
        self.insert()
        self.insert()
        sync, rows = self.sync()
        self.db.update_favorited_many = Mock(wraps=self.db.update_favorited_many)
        key = Executor._keeper_key(rows[0])
        for _ in range(3):
            sync.observe(key, True)
            sync.new_traversal()

        self.db.update_favorited_many.assert_called_once()
        self.assertEqual((2, 1), (sync.result()["db_synced"], sync.result()["db_unresolved"]))


if __name__ == "__main__":
    unittest.main()
