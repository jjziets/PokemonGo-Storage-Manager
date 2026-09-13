"""Favorite passes remain visible across shrinking result batches and refreshes."""

# TRACEWEAVER: file-role=keeper-progress-widget-tests; req=REQ-MASS-001; trace=TRACE-MASS-001; verifies=VER-SCAN-001

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from pokemgr.gui.widgets.decision_review import DecisionReview
from pokemgr.gui.widgets.mass_actions import MassActions


def progress(**changes):
    return dict(dict(schema=1, pass_name="Normal", pass_index=1, pass_total=5,
                     passes_completed=0, passes_remaining=5,
                     selected_passes=["Normal", "Shiny", "Shadow", "Dynamax", "Gigantamax"],
                     batch_index=3, traversal_index=2, stage="scanning", current=4, total=14,
                     checked_total=100, favorited_total=58, target_total=351,
                     pending_total=293, ambiguous_total=31, dry_run=False), **changes)


def at(seconds):
    return patch("pokemgr.gui.widgets.keeper_progress.time.monotonic", return_value=float(seconds))


class KeeperProgressWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def panels(self):
        for kind in (DecisionReview, MassActions):
            panel = kind()
            self.addCleanup(panel.deleteLater)
            if kind is DecisionReview:
                start = lambda: panel.set_favoriting(True)
                legacy, finish = panel.on_fav_progress, panel.on_fav_finished
                pause, stop = panel.set_fav_paused, panel.set_fav_stopping
                bar, status, log = panel.fav_progress_bar, panel.fav_status_label, panel.fav_log
            else:
                start = lambda: panel.set_running(True, "Favoriting")
                legacy, finish = panel.on_progress, panel.on_finished
                pause, stop = panel.set_paused, panel.set_stopping
                bar, status, log = panel.progress_bar, panel.status_label, panel.action_log
            with at(10):
                start()
            yield panel, start, legacy, finish, pause, stop, bar, status, log

    def test_pass_and_overall_counts_survive_refresh_without_whole_action_eta(self):
        for panel, _start, legacy, _finish, _pause, _stop, bar, _status, log in self.panels():
            with self.subTest(panel=type(panel).__name__), at(110):
                pane = panel.keeper_progress
                panel.on_action_progress(progress())
                self.assertEqual("Pass 1/5: Normal · 4 passes after this", pane.pass_label.text())
                self.assertEqual("Up next: Shiny → Shadow → Dynamax → Gigantamax", pane.next_label.text())
                self.assertIn("58/351 target keepers favorited", pane.summary_label.text())
                self.assertIn("100 checks total", pane.summary_label.text())
                self.assertIn("60 checks/min overall", pane.summary_label.text())
                self.assertIn("31 records need review", pane.summary_label.text())
                self.assertEqual("Batch 3 · round 2 — 4/14 checked this round", bar.format())

                panel.on_action_progress(progress(stage="refreshing"))
                self.assertEqual(0, bar.maximum())
                self.assertIn("Refreshing", bar.format())
                panel.on_action_progress(progress(traversal_index=3, current=1, total=7,
                                                  checked_total=101))
                legacy(58, 0, "Refreshing remaining nonfavorites")
                self.assertEqual(7, bar.maximum())
                self.assertIn("round 3 — 1/7 checked this round", bar.format())
                self.assertIn("101 checks total", pane.summary_label.text())
                self.assertIn("Refreshing remaining nonfavorites", log.toPlainText())
                self.assertNotIn("min left", bar.format() + pane.summary_label.text())

    def test_pause_stop_and_final_partial_outcome_survive_late_progress(self):
        for panel, start, legacy, finish, pause, stop, bar, status, _log in self.panels():
            with self.subTest(panel=type(panel).__name__):
                pane = panel.keeper_progress
                with at(20):
                    panel.on_action_progress(progress(checked_total=10))
                    pause(True)
                with at(40):
                    pause(True)
                    panel.on_action_progress(progress(checked_total=10))
                    self.assertTrue(pane.pass_label.text().startswith("Paused"))
                    self.assertIn("60 checks/min overall", pane.summary_label.text())
                with at(50):
                    pause(False)
                with at(60):
                    panel.on_action_progress(progress(checked_total=20))
                    self.assertIn("60 checks/min overall", pane.summary_label.text())
                    stop()
                    pause(False)
                    panel.on_action_progress(progress(checked_total=20))
                    self.assertEqual("Stopping...", status.text())
                    self.assertTrue(pane.pass_label.text().startswith("Stopping"))
                    finish(dict(error="Read held", aborted=True, checked=20, favorited=58))
                self.assertEqual("Failed · 0/5 passes complete", pane.pass_label.text())
                self.assertEqual("5 passes unfinished", pane.next_label.text())
                final = (pane.pass_label.text(), pane.summary_label.text(), bar.format(), status.text())
                panel.on_action_progress(progress(pass_index=5, pass_name="Gigantamax"))
                legacy(100, 100, "Old callback")
                self.assertEqual(final, (pane.pass_label.text(), pane.summary_label.text(), bar.format(), status.text()))
                start()
                self.assertTrue(pane.isHidden())
                self.assertFalse(pane.has_progress)
                # Single-filter/unfavorite actions still use their existing bar.
                legacy(1, 4, "Unfavorite progress")
                self.assertEqual(4, bar.maximum())

    def test_selected_empty_pass_and_dry_run_completion_do_not_claim_live_favorites(self):
        for panel, _start, _legacy, finish, _pause, _stop, bar, _status, _log in self.panels():
            with self.subTest(panel=type(panel).__name__):
                pane = panel.keeper_progress
                panel.on_action_progress(progress(
                    stage="pass_complete", pass_name="Shiny", pass_index=2, pass_total=3,
                    passes_completed=2, selected_passes=["Normal", "Shiny", "Shadow"],
                    batch_index=0, current=0, total=0, dry_run=True,
                    favorited_total=3, target_total=3, pending_total=0))
                self.assertEqual("2/3 passes complete · 1 remaining", pane.pass_label.text())
                self.assertEqual("Up next: Shadow", pane.next_label.text())
                self.assertIn("would favorite 3/3", pane.summary_label.text())
                self.assertEqual("No pending keeper targets in this pass", bar.format())
                panel.on_action_progress(progress(
                    stage="finished", pass_name="Shadow", pass_index=3, pass_total=3,
                    passes_completed=3, selected_passes=["Normal", "Shiny", "Shadow"],
                    dry_run=True, favorited_total=3, target_total=3, pending_total=0))
                finish(dict(dry_run=True, favorited=3, checked=10))
                self.assertEqual("Dry run complete · 3/3 passes complete", pane.pass_label.text())
                self.assertEqual("All selected passes finished", pane.next_label.text())
                self.assertNotIn("keepers favorited", pane.summary_label.text())

    def test_unconfirmed_count_on_failure_does_not_claim_empty_phone_results(self):
        for panel, _start, _legacy, _finish, _pause, _stop, bar, _status, _log in self.panels():
            with self.subTest(panel=type(panel).__name__):
                panel.on_action_progress(progress(stage="error", current=0, total=0))
                self.assertEqual("Round held", bar.format())
                panel.on_action_progress(progress(stage="stopped", current=0, total=0))
                self.assertEqual("Stopped", bar.format())
                panel.on_action_progress(progress(stage="scanning", current=0, total=0))
                self.assertEqual("No Pokémon in this round", bar.format())


if __name__ == "__main__":
    unittest.main()
