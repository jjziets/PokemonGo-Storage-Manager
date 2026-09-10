# TRACEWEAVER: file-role=action-persistence-gui-tests; req=REQ-MASS-001,REQ-DATA-001; trace=TRACE-MASS-001,TRACE-DATA-001; ver=VER-SCAN-001
"""Completed live actions refresh saved favorites and show persistence holds."""

import os
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from pokemgr.gui.main_window import MainWindow
from pokemgr.gui.widgets.decision_review import DecisionReview
from pokemgr.gui.widgets.mass_actions import MassActions
from tests.test_decision_action_routing import decision_window
from tests.test_mass_action_gui import window, worker


class ActionPersistenceRoutingTests(unittest.TestCase):
    def start(self, origin, *, dry=False):
        target = decision_window() if origin == "decisions" else window()
        action = worker()
        action.dry_run = dry
        if origin == "decisions":
            target._start_decision_worker(action, dry_run=dry)
        else:
            target._start_mass_worker(action, "Keepers")
        return target, action

    def test_both_views_refresh_once_only_after_live_worker_cleanup(self):
        for origin in ("decisions", "mass"):
            with self.subTest(origin=origin):
                target, action = self.start(origin)
                result = {"favorited": 2, "db_synced": 2, "db_unresolved": 0}
                with patch("PySide6.QtCore.QTimer.singleShot") as timer, \
                     patch("pokemgr.gui.main_window.QMessageBox.information"):
                    action.finished.emit(result)
                    target._refresh_collection.assert_not_called()
                    target._refresh_decisions.assert_not_called()
                    finish = timer.call_args.args[1]
                    action.isRunning.return_value = False
                    finish()
                    action.finished.emit(result)
                    finish()
                target._refresh_collection.assert_called_once_with()
                target._refresh_decisions.assert_called_once_with()
                target.db.clear_decisions.assert_not_called()

    def test_dry_worker_mode_prevents_refresh_even_when_result_omits_its_flag(self):
        for origin in ("decisions", "mass"):
            with self.subTest(origin=origin):
                target, action = self.start(origin, dry=True)
                action.isRunning.return_value = False
                with patch("pokemgr.gui.main_window.QMessageBox.information"):
                    action.finished.emit({"favorited": 2, "db_synced": 2})
                target._refresh_collection.assert_not_called()
                target._refresh_decisions.assert_not_called()
                finished = (target.decision_tab.on_fav_finished if origin == "decisions"
                            else target.mass_tab.on_finished)
                self.assertIs(finished.call_args.args[0]["dry_run"], True)

    def test_live_partial_error_and_stop_refresh_committed_favorite_state(self):
        for origin in ("decisions", "mass"):
            for ending in ({"error": "Connection lost"}, {"aborted": True}):
                with self.subTest(origin=origin, ending=ending):
                    target, action = self.start(origin)
                    action.isRunning.return_value = False
                    with patch("pokemgr.gui.main_window.QMessageBox.information"):
                        action.finished.emit({"unfavorited": 1, "db_synced": 1, **ending})
                    target._refresh_collection.assert_called_once()
                    target._refresh_decisions.assert_called_once()

    def test_stale_worker_results_cannot_refresh_a_new_actions_views(self):
        for origin in ("decisions", "mass"):
            with self.subTest(origin=origin):
                target, old = self.start(origin)
                if origin == "decisions":
                    target._start_decision_worker(worker())
                else:
                    target._start_mass_worker(worker(), "Next")
                old.isRunning.return_value = False
                old.finished.emit({"favorited": 7, "db_synced": 7})
                target._refresh_collection.assert_not_called()
                target._refresh_decisions.assert_not_called()

    def test_refresh_loads_current_database_rows_into_both_views(self):
        target, action = self.start("mass")
        target.collection_tab = Mock()
        target._refresh_collection = MethodType(MainWindow._refresh_collection, target)
        target._refresh_decisions = MethodType(MainWindow._refresh_decisions, target)
        saved = [SimpleNamespace(id=1, favorited=True)]
        target.db.get_all.return_value = saved
        action.isRunning.return_value = False

        action.finished.emit({"favorited": 1, "db_synced": 1, "db_unresolved": 0})

        target.collection_tab.load_pokemon.assert_called_once_with(saved)
        target.decision_tab.load_pokemon.assert_called_once_with(saved)
        target.db.clear_decisions.assert_not_called()

    def test_live_dialog_and_status_distinguish_matching_from_unsaved_favorite_state(self):
        target = window()
        result = {"favorited": 4, "unmatched": 0, "db_synced": 3, "db_unresolved": 1}
        with patch("pokemgr.gui.main_window.QMessageBox.information") as dialog:
            MainWindow._on_favorite_finished(target, result)

        title, body = dialog.call_args.args[1:3]
        self.assertIn("Needs Review", title)
        for line in ("Unresolved: 0", "Favorite status saved: 3", "Favorite status not saved: 1"):
            self.assertIn(line, body)
        status = target.statusBar.return_value.showMessage.call_args.args[0]
        self.assertIn("Favorite status saved: 3", status)
        self.assertIn("Favorite status not saved: 1", status)

    def test_dry_dialog_does_not_claim_saved_state_or_persistence_review(self):
        for key, label in (("favorited", "Would favorite"), ("unfavorited", "Would unfavorite")):
            with self.subTest(key=key):
                target = window()
                with patch("pokemgr.gui.main_window.QMessageBox.information") as dialog:
                    MainWindow._on_favorite_finished(target, {
                        key: 4, "dry_run": True, "db_synced": 3, "db_unresolved": 1,
                    })
                title, body = dialog.call_args.args[1:3]
                self.assertEqual("Dry Run Complete", title)
                self.assertIn(f"{label}: 4", body)
                self.assertNotIn("Favorite status", body)
                self.assertNotIn("Favorite status", target.statusBar.return_value.showMessage.call_args.args[0])


class ActionPersistenceWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def panel(self, origin, *, dry=False):
        if origin == "decisions":
            panel = DecisionReview()
            panel.set_favoriting(True, dry_run=dry)
            finish, status, log = panel.on_fav_finished, panel.fav_status_label, panel.fav_log
        else:
            panel = MassActions()
            panel.set_running(True, "Keepers")
            finish, status, log = panel.on_finished, panel.status_label, panel.action_log
        self.addCleanup(panel.deleteLater)
        return finish, status, log

    def test_unsaved_favorite_status_is_visible_and_requires_review_in_both_tabs(self):
        for origin in ("decisions", "mass"):
            with self.subTest(origin=origin):
                finish, status, log = self.panel(origin)
                finish({"favorited": 3, "unmatched": 0, "db_synced": 2, "db_unresolved": 1})
                for text in ("needs review", "favorite status saved: 2", "favorite status not saved: 1"):
                    self.assertIn(text, status.text())
                    self.assertIn(text, log.toPlainText())
                self.assertIn("#fc8", status.styleSheet())

    def test_all_saved_favorite_states_keep_successful_completion(self):
        for origin in ("decisions", "mass"):
            with self.subTest(origin=origin):
                finish, status, _log = self.panel(origin)
                finish({"unfavorited": 3, "db_synced": 3, "db_unresolved": 0})
                self.assertTrue(status.text().startswith("Done"))
                self.assertIn("favorite status saved: 3", status.text())
                self.assertIn("#8f8", status.styleSheet())

    def test_dry_widgets_suppress_persistence_claims_and_review_counts(self):
        for origin in ("decisions", "mass"):
            with self.subTest(origin=origin):
                finish, status, log = self.panel(origin, dry=True)
                finish({"favorited": 3, "dry_run": True, "db_synced": 3, "db_unresolved": 1})
                self.assertTrue(status.text().startswith("Dry run complete"))
                self.assertIn("would favorite: 3", status.text())
                self.assertNotIn("favorite status", status.text())
                self.assertNotIn("favorite status", log.toPlainText())
                self.assertNotIn("needs review", status.text())
                self.assertIn("#8cf", status.styleSheet())


if __name__ == "__main__":
    unittest.main()
