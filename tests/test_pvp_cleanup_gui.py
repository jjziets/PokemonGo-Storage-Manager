# TRACEWEAVER: file-role=pvp-cleanup-gui-regressions; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
# TRACEWEAVER: verifies=VER-SCAN-001; req=REQ-MASS-001; trace=TRACE-MASS-001
"""Cleanup preview selection and worker routing preserve the reviewed scope."""

import os
import sqlite3
import tempfile
from pathlib import Path
from dataclasses import replace
from types import MethodType
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from pokemgr.data.models import Pokemon
from pokemgr.data.database import PokemonDatabase
from pokemgr.execution.executor import Executor
from pokemgr.execution.pvp_cleanup import plan_pvp_cleanup
from pokemgr.gui.main_window import MainWindow
from pokemgr.gui.widgets.pvp_cleanup import PvpCleanupDialog
from pokemgr.gui.widgets.decision_review import DecisionReview
from pokemgr.gui.widgets.mass_actions import MassActions
from pokemgr.gui.widgets.keeper_progress import KeeperProgress
from pokemgr.gui.workers import PvpCleanupWorker
from tests.test_decision_action_routing import decision_window
from tests.test_mass_action_gui import worker
from tests.test_keeper_progress_widget import progress


def candidate(**changes):
    row = Pokemon(id=1, species="Azumarill", cp=1498, hp=180, atk=0, def_=12, sta=12,
                  iv_total=24, iv_pct=24/45, shiny=False, shadow=False, lucky=False,
                  favorited=True, position=0, decision="TRANSFER", scan_session_id="scan-one")
    return replace(row, **changes)


class PvpCleanupGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_preview_only_lists_candidates_and_deselection_is_authoritative(self):
        rows = [candidate(), candidate(id=2, cp=1450), candidate(id=3, decision="KEEP"),
                candidate(id=4, atk=13, def_=13, sta=13)]
        # KEEP companion makes id1 ambiguous; id2 remains individually selectable.
        plan = plan_pvp_cleanup(rows, Executor._keeper_key)
        dialog = PvpCleanupDialog(plan)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual([2], [p.id for p in dialog.selected_candidates()])
        self.assertEqual("1★", dialog.tree.topLevelItem(0).text(4))
        dialog.tree.topLevelItem(0).setCheckState(0, Qt.Unchecked)
        self.assertFalse(dialog.real_button.isEnabled())
        self.assertFalse(dialog.dry_button.isEnabled())
        dialog._start(False)
        self.assertEqual(QDialog.Rejected, dialog.result())
        dialog._select_all(Qt.Checked)
        selected = dialog.selected_candidates()
        selected[0].cp = 1
        self.assertEqual(1450, dialog.selected_candidates()[0].cp)
        dialog._start(True)
        self.assertTrue(dialog.dry_run)
        self.assertEqual(QDialog.Accepted, dialog.result())

    def target(self):
        target = decision_window()
        target._review_pvp_cleanup = MethodType(MainWindow._review_pvp_cleanup, target)
        target.db.get_all_for_cleanup.return_value = [candidate()]
        return target

    def test_matching_review_group_is_selected_and_protected_as_a_unit(self):
        rows = [candidate(id=1, position=0, species="Oricorio (Baile)"),
                candidate(id=2, position=1, species="Oricorio (Sensu)"), candidate(id=3, position=2, cp=1300)]
        dialog = PvpCleanupDialog(plan_pvp_cleanup(rows, Executor._keeper_key))
        self.addCleanup(dialog.deleteLater)
        items = {dialog.tree.topLevelItem(i).data(0, Qt.UserRole): dialog.tree.topLevelItem(i)
                 for i in range(dialog.tree.topLevelItemCount())}
        self.assertEqual({1, 2, 3}, {p.id for p in dialog.selected_candidates()})
        self.assertIn("group of 2", items[1].text(5))
        items[1].setCheckState(0, Qt.Unchecked)
        self.assertEqual(Qt.Unchecked, items[2].checkState(0))
        self.assertEqual([3], [p.id for p in dialog.selected_candidates()])
        items[2].setCheckState(0, Qt.Checked)
        self.assertEqual({1, 2, 3}, {p.id for p in dialog.selected_candidates()})
        dialog._select_all(Qt.Unchecked)
        self.assertFalse(dialog.real_button.isEnabled())
        dialog._select_all(Qt.Checked)
        self.assertEqual({1, 2, 3}, {p.id for p in dialog.selected_candidates()})

    def test_cancel_and_operation_started_during_preview_never_start_cleanup(self):
        for cancel in (True, False):
            target = self.target()
            dialog = Mock()
            dialog.selected_candidates.return_value = [candidate()]
            def preview():
                if not cancel:
                    target._scan_worker = Mock()
                    target._scan_worker.isRunning.return_value = True
                return QDialog.Rejected if cancel else QDialog.Accepted
            dialog.exec.side_effect = preview
            with patch("pokemgr.gui.widgets.pvp_cleanup.PvpCleanupDialog", return_value=dialog), \
                 patch("pokemgr.gui.workers.PvpCleanupWorker") as factory, \
                 patch("pokemgr.gui.main_window.QMessageBox.warning"):
                target._review_pvp_cleanup()
            factory.assert_not_called()
            self.assertEqual([], target.adb.mock_calls)

    def test_dry_and_real_route_frozen_selection_to_initiating_panel(self):
        for dry in (True, False):
            for mass in (True, False):
                target, action = self.target(), worker()
                dialog = Mock(dry_run=dry)
                dialog.exec.return_value = QDialog.Accepted
                dialog.selected_candidates.return_value = [candidate()]
                with patch("pokemgr.gui.widgets.pvp_cleanup.PvpCleanupDialog", return_value=dialog), \
                     patch("pokemgr.gui.workers.PvpCleanupWorker", return_value=action) as factory:
                    target._review_pvp_cleanup(from_mass=mass)
                self.assertEqual(dry, factory.call_args.kwargs["dry_run"])
                self.assertEqual([1], [p.id for p in factory.call_args.args[3]])
                if mass:
                    self.assertIs(target._mass_worker, action)
                else:
                    self.assertIs(target._fav_worker, action)
                    self.assertEqual("PvP cleanup", target.decision_tab.set_favoriting.call_args.kwargs["action"])
                action.start.assert_called_once()

    def test_protected_only_plan_and_missing_connection_send_no_inputs(self):
        for protected in (True, False):
            target = self.target()
            if protected:
                target.db.get_all_for_cleanup.return_value = [candidate(decision="KEEP")]
            else:
                target.adb = None
            with patch("pokemgr.gui.widgets.pvp_cleanup.PvpCleanupDialog") as dialog, \
                 patch("pokemgr.gui.main_window.QMessageBox.information"), \
                 patch("pokemgr.gui.main_window.QMessageBox.warning"), \
                 patch("pokemgr.gui.workers.PvpCleanupWorker") as factory:
                dialog.return_value.exec.return_value = QDialog.Accepted
                dialog.return_value.selected_candidates.return_value = [candidate()]
                target._review_pvp_cleanup()
            factory.assert_not_called()
            if protected:
                dialog.assert_not_called()

    def test_cleanup_controls_locked_and_progress_uses_off_wording(self):
        for kind in (DecisionReview, MassActions):
            panel = kind()
            self.addCleanup(panel.deleteLater)
            if kind is DecisionReview:
                panel.set_favoriting(True, action="PvP cleanup")
            else:
                panel.set_running(True, "PvP cleanup")
            self.assertFalse(panel.pvp_cleanup_btn.isEnabled())
            if kind is DecisionReview:
                self.assertFalse(panel.move_to_keep_btn.isEnabled())
                self.assertFalse(panel.move_to_transfer_btn.isEnabled())
            panel.on_action_progress(progress(action="unfavorite", unfavorited_total=3,
                                              target_total=7, pending_total=4))
            self.assertIn("3/7 cleanup targets unfavorited", panel.keeper_progress.summary_label.text())
        pane = KeeperProgress()
        self.addCleanup(pane.deleteLater)
        pane.update_progress(progress(action="unfavorite", unfavorited_total=2, dry_run=True))
        pane.finish({"unfavorited": 4, "dry_run": True, "aborted": True})
        self.assertIn("would unfavorite 4/351", pane.summary_label.text())

    def test_manual_keep_is_saved_before_refresh_and_protects_cleanup(self):
        from pokemgr.reader.screen import PokemonRead
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "collection.db"
            db = PokemonDatabase(path)
            self.addCleanup(db.close)
            db.create_session("scan-one", "test-device")
            record = PokemonRead(species="Azumarill", cp=1498, hp=180,
                                 atk=0, def_=12, sta=12, favorited=True, shiny=False, shadow=False, lucky=False)
            pid = db.insert_pokemon(record, "scan-one", 0)
            db.update_decision(pid, "TRANSFER")
            target = self.target()
            target.db = db
            MainWindow._save_manual_decision(target, pid, "KEEP")
            with sqlite3.connect(path) as independent:
                self.assertEqual(("KEEP", "MANUAL"), independent.execute(
                    "SELECT decision, decision_reason FROM pokemon WHERE id=?", (pid,)).fetchone())
            self.assertEqual(0, plan_pvp_cleanup(db.get_all(), Executor._keeper_key).eligible)
            target._refresh_decisions.assert_called_once()

    def test_group_verification_and_unfavorite_sweeps_are_distinguishable(self):
        from PySide6.QtWidgets import QProgressBar
        pane = KeeperProgress()
        bar = QProgressBar()
        self.addCleanup(pane.deleteLater)
        self.addCleanup(bar.deleteLater)
        pane.update_progress(progress(action="unfavorite", cleanup_phase="verify",
                                      verified_total=12, checked_total=0, current=12, total=20))
        pane.render_traversal(bar)
        self.assertIn("Step 1/2: verify group", pane.pass_label.text())
        self.assertIn("12 cards verified", pane.summary_label.text())
        self.assertIn("0 action checks", pane.summary_label.text())
        self.assertNotIn("0 checks total", pane.summary_label.text())
        self.assertIn("verify group", bar.format())
        self.assertIn("12/20 verified", bar.format())
        self.assertEqual((12, 20), (bar.value(), bar.maximum()))
        pane.update_progress(progress(action="unfavorite", cleanup_phase="unfavorite",
                                      verified_total=20, checked_total=5, current=5, total=20))
        pane.render_traversal(bar)
        self.assertIn("Step 2/2: unfavorite matches", pane.pass_label.text())
        self.assertIn("unfavorite matches", bar.format())
        pane.finish(dict(unfavorited=3, verified=20, checked=5, error="held"))
        self.assertNotIn("Step 2/2", pane.pass_label.text())
        self.assertIn("20 cards verified", pane.summary_label.text())

    def test_cleanup_dry_run_does_not_promise_a_second_mutation_sweep(self):
        pane = KeeperProgress()
        self.addCleanup(pane.deleteLater)
        pane.update_progress(progress(action="unfavorite", cleanup_phase="verify", dry_run=True))
        self.assertIn("Dry run: verify group", pane.pass_label.text())
        self.assertNotIn("1/2", pane.pass_label.text())
        self.assertNotIn("action checks", pane.summary_label.text())

    def test_completed_cleanup_counts_distinguish_verification_from_action_visits(self):
        for dry in (True, False):
            for kind in (DecisionReview, MassActions):
                panel = kind()
                self.addCleanup(panel.deleteLater)
                result = dict(dry_run=dry, verified=20, checked=0 if dry else 20,
                              unfavorited=3, unmatched=0)
                if kind is DecisionReview:
                    panel.set_favoriting(True, dry_run=dry, action="PvP cleanup")
                    panel.on_fav_finished(result)
                    status = panel.fav_status_label.text()
                else:
                    panel.set_running(True, "PvP cleanup")
                    panel.on_finished(result)
                    status = panel.status_label.text()
                self.assertIn("cards verified: 20", status)
                if dry:
                    self.assertIn("would unfavorite: 3", status)
                    self.assertNotIn("action checks", status)
                else:
                    self.assertIn("action checks: 20", status)

    def test_manual_decision_waits_for_persistence_and_is_blocked_during_actions(self):
        panel = DecisionReview()
        self.addCleanup(panel.deleteLater)
        panel.load_pokemon([candidate()])
        panel.transfer_tree.setCurrentItem(panel.transfer_tree.topLevelItem(0).child(0))
        requests = []
        panel.decision_requested.connect(lambda pid, decision: requests.append((pid, decision)))
        panel._move_to_keep()
        self.assertEqual([(1, "KEEP")], requests)
        self.assertEqual("TRANSFER", panel._pokemon_map[1].decision)
        panel.set_decision_editing_enabled(False)
        panel._move_to_keep()
        self.assertEqual(1, len(requests))
        target = self.target()
        target._scan_worker = Mock()
        target._scan_worker.isRunning.return_value = True
        with patch("pokemgr.gui.main_window.QMessageBox.warning"):
            MainWindow._save_manual_decision(target, 1, "KEEP")
        target.db.set_manual_decision.assert_not_called()

    def test_failed_manual_decision_keeps_the_existing_view(self):
        target = self.target()
        target.db.set_manual_decision.side_effect = RuntimeError("save failed")
        with patch("pokemgr.gui.main_window.QMessageBox.critical") as error:
            MainWindow._save_manual_decision(target, 1, "KEEP")
        target.db.set_manual_decision.assert_called_once_with(1, "KEEP")
        target._refresh_collection.assert_not_called()
        target._refresh_decisions.assert_not_called()
        error.assert_called_once()


class PvpCleanupWorkerTests(unittest.TestCase):
    def test_abort_before_or_during_construction_sends_no_action(self):
        for during in (False, True):
            action = PvpCleanupWorker(Mock(), Mock(), Mock(), [candidate()])
            engine = Mock()
            if not during:
                action.abort()
            def construct(*args):
                action.abort()
                return engine
            with patch("pokemgr.gui.workers.Executor", side_effect=construct) as factory:
                results = []
                action.finished.connect(results.append)
                action.run()
            if not during:
                factory.assert_not_called()
            else:
                engine._close_reader.assert_called_once()
            engine.unfavorite_pvp_candidates.assert_not_called()
            self.assertTrue(results[0]["aborted"])

    def test_frozen_selection_pause_and_partial_result(self):
        row = candidate()
        action = PvpCleanupWorker(Mock(), Mock(), Mock(), [row], dry_run=True)
        row.cp = 9
        action.pause()
        engine = Mock()
        def execute(rows, **kwargs):
            self.assertEqual(1498, rows[0].cp)
            self.assertTrue(kwargs["dry_run"])
            action.abort()
            return {"unfavorited": 2, "checked": 7, "error": "read held"}
        engine.unfavorite_pvp_candidates.side_effect = execute
        results = []
        action.finished.connect(results.append)
        with patch("pokemgr.gui.workers.Executor", return_value=engine):
            action.run()
        engine.pause.assert_called_once()
        self.assertEqual([dict(unfavorited=2, checked=7, error="read held", dry_run=True, aborted=True)], results)
