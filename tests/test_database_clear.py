"""Clear scanned data transactionally without replacing a live WAL database."""

# TRACEWEAVER: file-role=database-clear-regressions; req=REQ-DATA-001; trace=TRACE-DATA-001; verifies=VER-SCAN-001

import os
from contextlib import closing
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QMessageBox

from pokemgr.data.database import PokemonDatabase
from pokemgr.gui.main_window import MainWindow
from tests.test_database_positions import pokemon_read


class DatabaseClearTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.root / 'selected.db'
        self.db = PokemonDatabase(self.path)
        self.addCleanup(self.db.close)
        for session, device in (('old-account', 'old-phone'), ('current-account', 'tablet')):
            self.db.create_session(session, device)
            self.db.insert_pokemon(pokemon_read(), session, 1)
        self.db.flush()

    def counts(self, connection):
        return tuple(connection.execute(
            'SELECT (SELECT COUNT(*) FROM pokemon), (SELECT COUNT(*) FROM scan_sessions)'
        ).fetchone())

    def test_wal_readers_observe_committed_clear_without_old_rows_returning(self):
        reader = sqlite3.connect(self.path)
        self.addCleanup(reader.close)
        self.assertEqual('wal', reader.execute('PRAGMA journal_mode').fetchone()[0])
        reader.execute('BEGIN')
        self.assertEqual((2, 2), self.counts(reader))
        inode = self.path.stat().st_ino
        connection = self.db.conn

        self.assertEqual({'pokemon': 2, 'scan_sessions': 2}, self.db.clear_scanned_data())
        self.assertIs(connection, self.db.conn)
        self.assertEqual(inode, self.path.stat().st_ino)
        # An existing read transaction legitimately keeps its old snapshot.
        self.assertEqual((2, 2), self.counts(reader))
        reader.commit()
        self.assertEqual((0, 0), self.counts(reader))
        self.assertEqual((0, 0), self.counts(self.db.conn))
        with closing(sqlite3.connect(self.path)) as reopened:
            self.assertEqual((0, 0), self.counts(reopened))

        self.db.create_session('new-scan', 'tablet')
        self.db.insert_pokemon(pokemon_read(species='Pikachu'), 'new-scan', 1)
        self.db.flush()
        self.assertEqual((1, 1), self.counts(reader))
        self.assertEqual(['Pikachu'], [row[0] for row in reader.execute('SELECT species FROM pokemon')])

    def test_clear_includes_pending_batch_and_preserves_schema_metadata(self):
        self.db.insert_pokemon(pokemon_read(), 'current-account', 2)
        self.assertEqual(1, self.db._pending_writes)
        metadata = [tuple(row) for row in self.db.conn.execute('SELECT key, value FROM meta')]
        self.assertEqual({'pokemon': 3, 'scan_sessions': 2}, self.db.clear_scanned_data())
        self.assertEqual(0, self.db._pending_writes)
        self.assertEqual(metadata, [tuple(row) for row in self.db.conn.execute('SELECT key, value FROM meta')])
        self.assertEqual({'pokemon': 0, 'scan_sessions': 0}, self.db.clear_scanned_data())

    def test_failure_deleting_sessions_rolls_back_deleted_pokemon(self):
        self.db.conn.execute("""CREATE TRIGGER hold_sessions BEFORE DELETE ON scan_sessions
                                BEGIN SELECT RAISE(ABORT, 'session deletion held'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'session deletion held'):
            self.db.clear_scanned_data()
        self.assertEqual((2, 2), self.counts(self.db.conn))
        self.assertFalse(self.db.conn.in_transaction)
        with closing(sqlite3.connect(self.path)) as independent:
            self.assertEqual((2, 2), self.counts(independent))

    def test_zero_verification_failure_rolls_back_the_whole_clear(self):
        self.db.conn.execute("""CREATE TRIGGER retain_pokemon BEFORE DELETE ON pokemon
                                BEGIN SELECT RAISE(IGNORE); END""")
        with self.assertRaisesRegex(RuntimeError, 'could not be verified'):
            self.db.clear_scanned_data()
        self.assertEqual((2, 2), self.counts(self.db.conn))
        with closing(sqlite3.connect(self.path)) as independent:
            self.assertEqual((2, 2), self.counts(independent))

    def test_foreign_key_enforcement_allows_child_before_parent_deletion(self):
        self.db.conn.execute('PRAGMA foreign_keys=ON')
        self.db.clear_scanned_data()
        self.assertEqual((0, 0), self.counts(self.db.conn))

    def test_untracked_transaction_is_not_committed_or_rolled_back_by_clear(self):
        self.db.conn.execute("INSERT INTO meta VALUES ('pending-setting', 'value')")
        with self.assertRaisesRegex(RuntimeError, 'pending database transaction'):
            self.db.clear_scanned_data()
        self.assertTrue(self.db.conn.in_transaction)
        self.assertEqual((2, 2), self.counts(self.db.conn))
        self.assertIsNotNone(self.db.conn.execute("SELECT value FROM meta WHERE key='pending-setting'").fetchone())
        self.db.conn.rollback()

    def test_gui_clears_active_connection_without_touching_configured_other_database(self):
        other_path = self.root / 'unrelated.db'
        other = PokemonDatabase(other_path)
        other.create_session('unrelated', 'other-device')
        other.insert_pokemon(pokemon_read(species='Eevee'), 'unrelated', 1)
        other.close()
        original = other_path.read_bytes()
        connection = self.db.conn
        window = SimpleNamespace(
            db=self.db, scan_tab=Mock(), collection_tab=Mock(), decision_tab=Mock(),
            _scan_worker=None, _last_session_id='old-account',
            statusBar=Mock(return_value=Mock()),
        )

        def cleared_status(message):
            self.assertEqual('Database cleared', message)
            with closing(sqlite3.connect(self.path)) as independent:
                self.assertEqual((0, 0), self.counts(independent))

        window.scan_tab.status_label.setText.side_effect = cleared_status
        with patch('pokemgr.config.DB_PATH', other_path), \
             patch('pokemgr.data.database.DB_PATH', other_path), \
             patch('pokemgr.gui.main_window.QMessageBox.warning', return_value=QMessageBox.Yes), \
             patch('pokemgr.gui.main_window.PokemonDatabase') as replacement, \
             patch('os.remove') as remove:
            MainWindow._clear_database(window)
        replacement.assert_not_called()
        remove.assert_not_called()
        self.assertIs(window.db, self.db)
        self.assertIs(connection, self.db.conn)
        self.assertEqual(original, other_path.read_bytes())
        window.collection_tab.load_pokemon.assert_called_once_with([])
        window.decision_tab.load_pokemon.assert_called_once_with([])
        self.assertIsNone(window._last_session_id)
        window.scan_tab.status_label.setText.assert_called_once_with('Database cleared')


if __name__ == '__main__':
    unittest.main()
