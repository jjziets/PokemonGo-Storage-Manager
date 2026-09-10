"""Confirmed phone state survives interruption without guessing row identity."""

from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from pokemgr.data.database import PokemonDatabase
from pokemgr.execution.executor import Executor
from pokemgr.execution.favorite_sync import FavoriteSync
from pokemgr.indexer.snapshot import SnapshotDecision
from tests.test_database_positions import pokemon_read
from tests import test_mass_action_scanning as action_fixtures
from tests.test_mass_action_scanning import frame, snapshot


class FavoriteSyncDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "collection.db"
        self.db = PokemonDatabase(self.path)
        self.addCleanup(self.db.close)
        self.db.create_session("one", "test-device")
        self.db.create_session("two", "test-device")

    def insert(self, *, session="one", **kwargs):
        position = len(self.db.get_all())
        row_id = self.db.insert_pokemon(pokemon_read(**kwargs), session, position)
        return row_id

    def state(self):
        # A second connection proves the change is committed immediately,
        # even when the process never reaches its ordinary batch flush.
        with closing(sqlite3.connect(self.path)) as conn:
            return dict(conn.execute("SELECT id, favorited FROM pokemon"))

    def sync(self):
        rows = self.db.get_all()
        return FavoriteSync(self.db, rows, Executor._keeper_key), rows

    def test_unique_confirmed_star_commits_on_and_off_immediately(self):
        row_id = self.insert()
        for target in (True, False):
            with self.subTest(target=target):
                sync, rows = self.sync()
                sync.observe(Executor._keeper_key(rows[0]), target)
                self.assertEqual({row_id: int(target)}, self.state())
                self.assertEqual({"db_synced": 1, "db_unresolved": 0}, sync.result())

    def test_duplicate_group_does_not_assign_first_read_to_arbitrary_row(self):
        a, b = self.insert(), self.insert(favorited=True)
        sync, rows = self.sync()
        key = Executor._keeper_key(rows[0])
        sync.observe(key, True)
        self.assertEqual({a: 0, b: 1}, self.state())
        self.assertEqual(1, sync.result()["db_unresolved"])
        sync.observe(key, True)
        self.assertEqual({a: 1, b: 1}, self.state())
        self.assertEqual({"db_synced": 2, "db_unresolved": 0}, sync.result())

    def test_restart_cannot_combine_partial_duplicate_reads(self):
        a, b = self.insert(), self.insert()
        sync, rows = self.sync()
        key = Executor._keeper_key(rows[0])
        sync.observe(key, True)
        sync.new_traversal()
        sync.observe(key, True)
        self.assertEqual({a: 0, b: 0}, self.state())
        sync.observe(key, True)
        self.assertEqual({a: 1, b: 1}, self.state())
        self.assertEqual(0, sync.result()["db_unresolved"])

    def test_same_signature_in_different_sessions_is_not_guessed(self):
        a, b = self.insert(), self.insert(session="two")
        sync, rows = self.sync()
        key = Executor._keeper_key(rows[0])
        sync.observe(key, True)
        sync.observe(key, True)
        self.assertEqual({a: 0, b: 0}, self.state())
        self.assertEqual(2, sync.result()["db_unresolved"])

    def test_uniform_unfavorite_commits_only_after_complete_pass(self):
        a, b = self.insert(favorited=True), self.insert(favorited=True, cp=15)
        sync, _rows = self.sync()
        sync.observe(None, False)
        self.assertEqual({a: 1, b: 1}, self.state())
        self.assertEqual(1, sync.result()["db_unresolved"])
        sync.observe(None, False)
        sync.complete_uniform_pass("cp0-", False)
        self.assertEqual({a: 0, b: 0}, self.state())
        self.assertEqual({"db_synced": 2, "db_unresolved": 0}, sync.result())

    def test_completed_category_only_updates_proven_membership(self):
        normal = self.insert()
        shiny = self.insert(shiny=True)
        sync, _rows = self.sync()
        sync.observe(None, True)
        sync.complete_uniform_pass("shiny", True)
        self.assertEqual({normal: 0, shiny: 1}, self.state())
        self.assertEqual(1, sync.result()["db_synced"])

    def test_unknown_category_does_not_guess_database_membership(self):
        row_id = self.insert(is_dynamax=True)
        sync, _rows = self.sync()
        sync.observe(None, True)
        sync.complete_uniform_pass("gigantamax", True)
        self.assertEqual({row_id: 0}, self.state())
        self.assertEqual(1, sync.result()["db_unresolved"])

    def test_collapsed_dynamax_flag_does_not_authorize_gigantamax_rows(self):
        row_id = self.insert(is_dynamax=True)
        sync, _rows = self.sync()
        sync.observe(None, True)
        sync.complete_uniform_pass("dynamax", True)
        self.assertEqual({row_id: 0}, self.state())
        self.assertEqual(1, sync.result()["db_unresolved"])

    def test_three_star_does_not_use_incorrect_model_rating_for_44_iv(self):
        near = self.insert(atk=15, def_=15, sta=14)
        perfect = self.insert(atk=15, def_=15, sta=15)
        sync, _rows = self.sync()
        sync.observe(None, True)
        sync.complete_uniform_pass("3*", True)
        self.assertEqual({near: 1, perfect: 0}, self.state())

    def test_missing_row_rolls_back_whole_group_and_reports_save_failure(self):
        a, b = self.insert(), self.insert()
        sync, rows = self.sync()
        self.db.conn.execute("DELETE FROM pokemon WHERE id = ?", (b,))
        self.db.conn.commit()
        key = Executor._keeper_key(rows[0])
        sync.observe(key, True)
        sync.observe(key, True)
        self.assertEqual({a: 0}, self.state())
        self.assertIn("could not be saved", sync.result()["error"])
        self.assertEqual(0, sync.result()["db_synced"])


class FavoriteSyncExecutorTests(unittest.TestCase):
    """Reuse the fake stream; every database in these tests is temporary."""

    def setUp(self):
        action_fixtures.MassActionScanningTests.setUp(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "collection.db"
        self.db = PokemonDatabase(self.path)
        self.addCleanup(self.db.close)
        self.db.create_session("one", "test")
        self.executor.db = self.db

    setup_positions = action_fixtures.MassActionScanningTests.setup_positions

    def insert(self, read=None, *, favorited=False):
        read = read or snapshot()
        return self.db.insert_pokemon(pokemon_read(
            species=read.detected_species, cp=read.cp, hp=read.hp,
            atk=read.atk, def_=read.def_, sta=read.sta,
            favorited=favorited, shiny=read.shiny, shadow=read.shadow,
            lucky=read.lucky, is_dynamax=read.is_dynamax,
        ), "one", len(self.db.get_all()))

    def state(self):
        with closing(sqlite3.connect(self.path)) as conn:
            return dict(conn.execute("SELECT id, favorited FROM pokemon"))

    def prepare_sync(self):
        self.executor._favorite_sync = FavoriteSync(self.db, self.db.get_all(), Executor._keeper_key)

    def prepare_keepers(self, reads):
        ids = [self.insert(read) for read in reads]
        for row_id in ids:
            self.db.update_decision(row_id, "KEEP", "test")
        self.setup_positions(reads)
        self.scanner._acquire_validated_snapshot = Mock(side_effect=[
            (SnapshotDecision(True, "exact", read, cp_source="calculated"),
             frame(read), "ok", "exact") for read in reads
        ])
        return ids

    def test_real_readback_persists_without_waiting_for_pass_completion(self):
        row_id = self.insert()
        self.prepare_sync()
        read = snapshot()
        self.scanner._fast_screencap = Mock(side_effect=[frame(read, "off"), frame(read, "on")])
        changed, _ = self.executor._set_star(read, frame(read), True,
            cp_decision=SnapshotDecision(True, "exact", read, cp_source="calculated"))
        self.assertTrue(changed)
        self.assertEqual({row_id: 1}, self.state())

    def test_already_correct_live_star_repairs_stale_database_without_tap(self):
        row_id = self.insert()
        self.prepare_sync()
        read = snapshot()
        self.scanner._fast_screencap = Mock(return_value=frame(read, "on"))
        changed, _ = self.executor._set_star(read, frame(read), True,
            cp_decision=SnapshotDecision(True, "exact", read, cp_source="calculated"))
        self.assertFalse(changed)
        self.assertEqual({row_id: 1}, self.state())
        self.adb.tap.assert_not_called()

    def test_dry_run_does_not_persist_even_when_live_star_already_on(self):
        row_id = self.insert()
        self.prepare_sync()
        read = snapshot()
        for state in ("on", "off"):
            self.scanner._fast_screencap = Mock(return_value=frame(read, state))
            self.executor._set_star(read, frame(read), True, dry_run=True,
                cp_decision=SnapshotDecision(True, "exact", read, cp_source="calculated"))
        self.assertEqual({row_id: 0}, self.state())
        self.adb.tap.assert_not_called()

    def test_unconfirmed_tap_does_not_persist(self):
        row_id = self.insert()
        self.prepare_sync()
        read = snapshot()
        self.scanner._fast_screencap = Mock(return_value=frame(read, "off"))
        with self.assertRaisesRegex(RuntimeError, "not confirmed"):
            self.executor._set_star(read, frame(read), True,
                cp_decision=SnapshotDecision(True, "exact", read, cp_source="calculated"))
        self.assertEqual({row_id: 0}, self.state())
        self.adb.tap.assert_called_once()

    def test_full_unfavorite_updates_database_and_next_keeper_plan(self):
        row_id = self.insert(favorited=True)
        self.db.update_decision(row_id, "KEEP", "test")
        self.setup_positions([snapshot()], target=False)
        result = self.executor.unfavorite_all()
        self.assertNotIn("error", result)
        self.assertEqual({row_id: 0}, self.state())
        self.assertEqual(1, result["db_synced"])
        self.assertEqual(1, Executor.plan_keeper_favorites(self.db.get_all()).unstarred)

    def test_interrupted_unfavorite_does_not_clear_unvisited_rows(self):
        a, b = self.insert(favorited=True), self.insert(snapshot(cp=501), favorited=True)
        self.setup_positions([snapshot(), snapshot(cp=501)], target=False)
        self.executor.on_progress = lambda *_: self.executor.abort()
        result = self.executor.unfavorite_all()
        self.assertTrue(result["aborted"])
        self.assertEqual({a: 1, b: 1}, self.state())
        self.assertEqual(1, result["db_unresolved"])

    def test_failed_input_cannot_count_as_successful_full_unfavorite(self):
        row_id = self.insert(favorited=True)
        self.setup_positions([snapshot()], target=False)
        self.scanner._safe_tap = Mock(return_value=False)
        result = self.executor.unfavorite_all()
        self.assertIn("star input was not sent", result["error"])
        self.assertEqual(0, result["checked"])
        self.assertEqual({row_id: 1}, self.state())

    def test_keeper_partial_stop_saves_first_row_but_not_unvisited_second(self):
        a, b = self.prepare_keepers([snapshot(), snapshot(cp=501)])
        self.executor.on_progress = lambda current, *_: self.executor.abort() if current else None
        result = self.executor.favorite_keepers(selected_passes=["Normal"])
        self.assertTrue(result["aborted"])
        self.assertEqual({a: 1, b: 0}, self.state())
        self.assertEqual((1, 1), (result["favorited"], result["db_synced"]))

    def test_database_error_preserves_actual_star_count_and_never_retaps(self):
        row_id, = self.prepare_keepers([snapshot()])
        self.db.update_favorited_many = Mock(side_effect=sqlite3.OperationalError("test disk failure"))
        result = self.executor.favorite_keepers(selected_passes=["Normal"])
        self.assertIn("could not be saved", result["error"])
        self.assertEqual((1, 0, 1), (result["favorited"], result["db_synced"], result["db_unresolved"]))
        self.assertEqual({row_id: 0}, self.state())
        self.adb.tap.assert_called_once()
        self.executor._advance_action.assert_not_called()

    def test_abort_after_confirmed_save_keeps_count_and_committed_row(self):
        row_id, = self.prepare_keepers([snapshot()])
        save = self.db.update_favorited_many
        def save_then_abort(ids, target):
            save(ids, target)
            self.executor.abort()
        self.db.update_favorited_many = save_then_abort
        result = self.executor.favorite_keepers(selected_passes=["Normal"])
        self.assertTrue(result["aborted"])
        self.assertEqual({row_id: 1}, self.state())
        self.assertEqual((1, 1), (result["favorited"], result["db_synced"]))

    def test_unknown_filtered_count_does_not_bulk_clear_recorded_favorites(self):
        row_id = self.insert(favorited=True)
        self.executor._open_pass = Mock(side_effect=RuntimeError("count not confirmed"))
        result = self.executor.unfavorite_all()
        self.assertIn("count not confirmed", result["error"])
        self.assertEqual({row_id: 1}, self.state())
        self.assertEqual(0, result["db_synced"])


if __name__ == "__main__":
    unittest.main()
