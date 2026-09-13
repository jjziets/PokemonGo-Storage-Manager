"""Reviewed star writes and manual decisions commit atomically on temporary WAL DBs."""

from contextlib import closing
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest

from pokemgr.data.database import PokemonDatabase
from pokemgr.execution.pvp_cleanup import plan_pvp_cleanup
from tests.test_database_positions import pokemon_read
from tests.test_pvp_cleanup_plan import key_for

# TRACEWEAVER: file-role=reviewed-favorite-persistence-tests; req=REQ-MASS-001,REQ-DATA-001; verifies=VER-SCAN-001


class DatabaseReviewedActionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.root / "reviewed.db"
        self.db = PokemonDatabase(self.path)
        self.addCleanup(self.db.close)
        self.db.create_session("session-a", "tablet_dpi420")
        self.db.create_session("session-b", "other-device")
        for position in (1, 2):
            pid = self.db.insert_pokemon(pokemon_read(
                species="Swampert", cp=1400 + position, hp=120 + position,
                atk=0, def_=15, sta=15, favorited=True,
            ), "session-a", position)
            self.db.update_decision(pid, "TRANSFER")
        self.db.flush()
        self.reviewed = self.db.get_all()

    def stored(self):
        with closing(sqlite3.connect(self.path)) as independent:
            return list(independent.execute(
                "SELECT id, favorited, decision, decision_reason FROM pokemon ORDER BY id"))

    def make_uniform_group(self, *, different_form=False):
        """Make the two isolated fixture rows one fully reviewed group."""
        first, second = self.reviewed
        species = "Swampert (Mega)" if different_form else first.species
        with self.db.conn:
            self.db.conn.execute("UPDATE pokemon SET cp = ?, hp = ?, species = ? WHERE id = ?",
                                 (first.cp, first.hp, species, second.id))
        self.reviewed = self.db.get_all_for_cleanup()
        return self.reviewed

    def test_fingerprint_lookup_is_read_only_and_preserves_missing_bindings(self):
        self.db.conn.execute("UPDATE scan_sessions SET device_fingerprint = NULL WHERE id = 'session-b'")
        self.db.conn.commit()
        changes = self.db.conn.total_changes
        self.assertEqual(self.db.get_session_device_fingerprints([
            "session-a", "missing", "session-b", "session-a"]),
            {"session-a": "tablet_dpi420", "session-b": None})
        self.assertEqual(self.db.get_session_device_fingerprints([]), {})
        self.assertEqual(self.db.conn.total_changes, changes)
        self.assertFalse(self.db.conn.in_transaction)

    def test_successful_off_is_visible_to_other_connection_and_replay_is_held(self):
        self.db.update_favorited_reviewed(self.reviewed)
        self.assertEqual([row[1] for row in self.stored()], [0, 0])
        self.assertEqual(self.db._pending_writes, 0)
        self.assertFalse(self.db.conn.in_transaction)
        with self.assertRaisesRegex(RuntimeError, "changed|ambiguous"):
            self.db.update_favorited_reviewed(self.reviewed)
        self.assertTrue(all(row.favorited for row in self.reviewed))

    def test_whole_duplicate_group_off_commits_atomically(self):
        self.make_uniform_group()
        self.assertEqual(plan_pvp_cleanup(self.reviewed, key_for).eligible, 2)
        self.db.update_favorited_reviewed(self.reviewed)
        self.assertEqual([row[1] for row in self.stored()], [0, 0])
        self.assertTrue(all(row.favorited for row in self.reviewed))

    def test_whole_same_stat_form_group_off_commits_atomically(self):
        self.make_uniform_group(different_form=True)
        self.db.update_favorited_reviewed(self.reviewed)
        self.assertEqual([row[1] for row in self.stored()], [0, 0])

    def test_partial_duplicate_group_save_is_refused(self):
        for different_form in (False, True):
            with self.subTest(different_form=different_form):
                self.make_uniform_group(different_form=different_form)
                for member in self.reviewed:
                    with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                        self.db.update_favorited_reviewed([member])
                self.assertEqual([row[1] for row in self.stored()], [1, 1])

    def test_uniform_group_current_protection_or_cross_session_change_holds_all(self):
        self.make_uniform_group(different_form=True)
        second = self.reviewed[1]
        for field, value in (("decision", "KEEP"), ("favorited", 0),
                             ("scan_session_id", "session-b"), ("shiny", 2),
                             ("atk", 7)):
            with self.subTest(field=field):
                with self.db.conn:
                    self.db.conn.execute(f"UPDATE pokemon SET {field} = ? WHERE id = ?",
                                         (value, second.id))
                before = self.stored()
                with self.assertRaises(RuntimeError):
                    self.db.update_favorited_reviewed(self.reviewed)
                self.assertEqual(self.stored(), before)
                with self.db.conn:
                    self.db.conn.execute(f"UPDATE pokemon SET {field} = ? WHERE id = ?",
                                         (getattr(second, field), second.id))

    def test_uniform_group_second_write_failure_rolls_back_first(self):
        self.make_uniform_group(different_form=True)
        second = self.reviewed[1]
        self.db.conn.execute(f"""CREATE TRIGGER hold_group_second BEFORE UPDATE OF favorited ON pokemon
            WHEN NEW.id = {second.id} BEGIN SELECT RAISE(ABORT, 'group held'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "group held"):
            self.db.update_favorited_reviewed(self.reviewed)
        self.assertEqual([row[1] for row in self.stored()], [1, 1])
        self.assertFalse(self.db.conn.in_transaction)

    def test_partial_other_form_keep_inserted_after_review_holds_group(self):
        self.make_uniform_group(different_form=True)
        first = self.reviewed[0]
        pid = self.db.insert_pokemon(pokemon_read(
            species="Different species", cp=first.cp, hp=-1, atk=first.atk,
            def_=first.def_, sta=first.sta, favorited=False,
        ), "session-b", 1)
        self.db.update_decision(pid, "KEEP")
        self.db.flush()
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            self.db.update_favorited_reviewed(self.reviewed)
        self.assertEqual([row[1] for row in self.stored()], [1, 1, 0])

    def test_only_exact_off_target_and_complete_reviewed_records_are_allowed(self):
        for target in (True, 0, None, "false"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.db.update_favorited_reviewed(self.reviewed, target=target)
        first = self.reviewed[0]
        for change in ({"decision": "KEEP"}, {"favorited": False}, {"atk": 7},
                       {"hp": -1}, {"id": True}, {"scan_session_id": ""}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.db.update_favorited_reviewed([replace(first, **change)])
        with self.assertRaises(RuntimeError):
            self.db.update_favorited_reviewed([first, first])
        self.assertEqual([row[1] for row in self.stored()], [1, 1])

    def test_stale_identity_decision_or_star_holds_whole_group(self):
        first = self.reviewed[0]
        changes = {"scan_session_id": "session-b", "species": "Marshtomp", "cp": 900,
                   "hp": 125, "atk": 1, "def_": 14, "sta": 14,
                   "shiny": 1, "shadow": 1, "lucky": 1, "is_dynamax": 1,
                   "decision": "KEEP", "favorited": 0, "position": 3}
        for field, value in changes.items():
            with self.subTest(field=field):
                with self.db.conn:
                    self.db.conn.execute(f"UPDATE pokemon SET {field} = ? WHERE id = ?", (value, first.id))
                before = self.stored()
                with self.assertRaises(RuntimeError):
                    self.db.update_favorited_reviewed(self.reviewed)
                self.assertEqual(self.stored(), before)
                with self.db.conn:
                    self.db.conn.execute(f"UPDATE pokemon SET {field} = ? WHERE id = ?",
                                         (getattr(first, field), first.id))

    def test_partial_keep_companion_inserted_after_review_holds_even_unstarred(self):
        first = self.reviewed[0]
        pid = self.db.insert_pokemon(pokemon_read(
            species=first.species, cp=first.cp, hp=-1, atk=first.atk,
            def_=first.def_, sta=first.sta, favorited=False,
        ), "session-b", 1)
        self.db.update_decision(pid, "KEEP")
        self.db.flush()
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            self.db.update_favorited_reviewed([first])
        self.assertEqual(self.stored()[0][1], 1)

    def test_exact_unstarred_cross_session_duplicate_holds(self):
        first = self.reviewed[0]
        self.db.insert_pokemon(pokemon_read(
            species=first.species, cp=first.cp, hp=first.hp, atk=first.atk,
            def_=first.def_, sta=first.sta, favorited=False,
        ), "session-b", 1)
        self.db.flush()
        with self.assertRaises(RuntimeError):
            self.db.update_favorited_reviewed([first])
        self.assertEqual(self.stored()[0][1], 1)

    def test_corrupt_sqlite_boolean_is_not_coerced_into_authority(self):
        for field in ("shiny", "shadow", "lucky", "is_dynamax", "favorited"):
            with self.subTest(field=field):
                with self.db.conn:
                    self.db.conn.execute(f"UPDATE pokemon SET {field} = 2 WHERE id = ?", (self.reviewed[0].id,))
                # General reads historically convert booleans. The conditional
                # writer must independently reject the underlying corrupt flag.
                apparent = self.db.get_all()[0]
                with self.assertRaises(RuntimeError):
                    self.db.update_favorited_reviewed([apparent])
                with self.db.conn:
                    self.db.conn.execute(f"UPDATE pokemon SET {field} = ? WHERE id = ?",
                                         (getattr(self.reviewed[0], field), self.reviewed[0].id))

    def test_cleanup_read_preserves_invalid_flags_before_any_action(self):
        first = self.reviewed[0]
        for field in ("shiny", "shadow", "lucky", "is_dynamax", "favorited"):
            with self.subTest(field=field):
                with self.db.conn:
                    self.db.conn.execute(f"UPDATE pokemon SET {field} = 2 WHERE id = ?", (first.id,))
                strict = self.db.get_all_for_cleanup()
                self.assertIs(type(getattr(strict[0], field)), int)
                self.assertEqual(getattr(strict[0], field), 2)
                self.assertIs(getattr(self.db.get_all()[0], field), True)
                self.assertNotIn(first.id, [row.id for row in plan_pvp_cleanup(strict, key_for).candidates])
                with self.db.conn:
                    self.db.conn.execute(f"UPDATE pokemon SET {field} = ? WHERE id = ?",
                                         (getattr(first, field), first.id))

    def test_strict_read_keeps_malformed_companion_ambiguous_in_preview(self):
        first = self.reviewed[0]
        pid = self.db.insert_pokemon(pokemon_read(
            species=first.species, cp=first.cp, hp=first.hp, atk=first.atk,
            def_=first.def_, sta=first.sta, favorited=False,
        ), "session-b", 1)
        self.db.update_decision(pid, "KEEP")
        self.db.flush()
        with self.db.conn:
            self.db.conn.execute("UPDATE pokemon SET shiny = 2 WHERE id = ?", (pid,))
        plan = plan_pvp_cleanup(self.db.get_all_for_cleanup(), key_for)
        self.assertNotIn(first.id, [row.id for row in plan.candidates])
        self.assertEqual(plan.ambiguous, 1)

    def test_second_update_failure_rolls_back_first_off_and_leaves_no_delayed_write(self):
        second = self.reviewed[1]
        self.db.conn.execute(f"""CREATE TRIGGER hold_second BEFORE UPDATE OF favorited ON pokemon
            WHEN NEW.id = {second.id} BEGIN SELECT RAISE(ABORT, 'write held'); END""")
        before = self.stored()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "write held"):
            self.db.update_favorited_reviewed(self.reviewed)
        self.assertEqual(self.stored(), before)
        self.assertFalse(self.db.conn.in_transaction)
        self.db.flush()
        self.assertEqual(self.stored(), before)

    def test_readback_failure_rolls_back_false_off_claim(self):
        self.db.conn.execute("""CREATE TRIGGER revert_star AFTER UPDATE OF favorited ON pokemon
            WHEN NEW.favorited = 0 BEGIN UPDATE pokemon SET favorited = 1 WHERE id = NEW.id; END""")
        with self.assertRaisesRegex(RuntimeError, "could not be verified"):
            self.db.update_favorited_reviewed(self.reviewed)
        self.assertEqual([row[1] for row in self.stored()], [1, 1])

    def test_writer_lock_prevents_other_connection_changing_keep_during_cleanup(self):
        other = self.enterContext(closing(sqlite3.connect(self.path, timeout=0)))
        attempted = []

        def competing_writer(sql):
            if sql.lstrip().startswith("UPDATE pokemon SET favorited = 0"):
                try:
                    other.execute("UPDATE pokemon SET decision = 'KEEP' WHERE id = ?",
                                  (self.reviewed[0].id,))
                except sqlite3.OperationalError as exc:
                    attempted.append(str(exc))
                else:
                    attempted.append("unexpected write")

        self.db.conn.set_trace_callback(competing_writer)
        try:
            self.db.update_favorited_reviewed([self.reviewed[0]])
        finally:
            self.db.conn.set_trace_callback(None)
        self.assertEqual(attempted, ["database is locked"])
        other.rollback()
        self.assertEqual(self.stored()[0][1:3], (0, "TRANSFER"))

    def test_manual_decision_is_immediately_durable_with_manual_reason(self):
        pid = self.reviewed[0].id
        for decision in ("KEEP", "TRANSFER"):
            self.db.set_manual_decision(pid, decision)
            self.assertEqual(self.stored()[0][2:], (decision, "MANUAL"))
            self.assertEqual(self.db._pending_writes, 0)
            self.assertFalse(self.db.conn.in_transaction)

    def test_invalid_missing_or_untracked_manual_write_is_held(self):
        for pid, decision in ((True, "KEEP"), (0, "KEEP"), (1.0, "KEEP"),
                              (1, "keep"), (1, None)):
            with self.subTest(pid=pid, decision=decision), self.assertRaises(ValueError):
                self.db.set_manual_decision(pid, decision)
        with self.assertRaises(RuntimeError):
            self.db.set_manual_decision(999, "KEEP")
        self.db.conn.execute("INSERT INTO meta VALUES ('pending', 'value')")
        with self.assertRaisesRegex(RuntimeError, "pending database transaction"):
            self.db.set_manual_decision(self.reviewed[0].id, "KEEP")
        self.assertTrue(self.db.conn.in_transaction)
        self.db.conn.rollback()
        self.assertEqual(self.stored()[0][2:], ("TRANSFER", ""))

    def test_manual_commit_failure_rolls_back_requested_change(self):
        self.db.conn.execute("PRAGMA foreign_keys = ON")
        self.db.conn.executescript("""
            CREATE TABLE guard_parent(id INTEGER PRIMARY KEY);
            CREATE TABLE guard_child(id INTEGER REFERENCES guard_parent(id)
                DEFERRABLE INITIALLY DEFERRED);
            CREATE TRIGGER deferred_commit_failure AFTER UPDATE OF decision ON pokemon
                BEGIN INSERT INTO guard_child VALUES (999); END;
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.set_manual_decision(self.reviewed[0].id, "KEEP")
        self.assertFalse(self.db.conn.in_transaction)
        self.assertEqual(self.db._pending_writes, 0)
        self.assertEqual(self.stored()[0][2:], ("TRANSFER", ""))
        self.db.flush()
        self.assertEqual(self.stored()[0][2:], ("TRANSFER", ""))


if __name__ == "__main__":
    unittest.main()
