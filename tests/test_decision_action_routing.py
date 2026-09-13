"""Decisions owns the same verified action workers through cleanup and stop."""

# TRACEWEAVER: file-role=decision-action-routing-tests; req=REQ-MASS-001; trace=TRACE-MASS-001; verifies=VER-SCAN-001

from types import MethodType
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QMessageBox

from pokemgr.gui.main_window import MainWindow
from tests.test_mass_action_gui import window, worker
from tests.test_mass_action_scanning import pokemon


def decision_window():
    target = window()
    for name in ('_start_decision_worker', '_finish_decision_action', '_toggle_fav_pause',
                 '_stop_favorite', '_force_stop_favorite'):
        setattr(target, name, MethodType(getattr(MainWindow, name), target))
    return target


class DecisionActionRoutingTests(unittest.TestCase):
    # TRACEWEAVER: entrypoint=test_confirmation_reconciles_all_keepers_without_starting_an_action; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
    def test_confirmation_reconciles_all_keepers_without_starting_an_action(self):
        for dry_run, from_mass in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(dry_run=dry_run, from_mass=from_mass):
                target = decision_window()
                target.db.get_all.return_value = (
                    [pokemon(favorited=True) for _ in range(951)]
                    + [pokemon() for _ in range(320)]
                    + [pokemon(hp=0) for _ in range(31)]
                    + [pokemon(cp=501, decision="TRANSFER")]
                )
                with patch('pokemgr.gui.main_window.QMessageBox.question', return_value=QMessageBox.No) as message, \
                     patch('pokemgr.gui.workers.FavoriteWorker') as made:
                    target._start_favorite(dry_run=dry_run, from_mass=from_mass)
                body = message.call_args.args[2]
                for line in ('KEEP records: 1,302', 'Already favorited (recorded): 951',
                             'Unstarred (recorded): 351',
                             'Eligible before pass/live checks: 320', 'Held for review: 31',
                             'excludes existing favorites on the phone.',
                             'validated species/form, CP, HP and IVs',
                             'Passes: Normal, Shiny', 'Recorded favorites may differ from the phone.'):
                    self.assertIn(line, body)
                self.assertIn('No stars will be tapped.' if dry_run
                              else 'Will tap the star only on confirmed unstarred matches.', body)
                made.assert_not_called()
                self.assertEqual([], target.adb.mock_calls)
                target.mass_tab.set_running.assert_not_called()
                target.decision_tab.set_favoriting.assert_not_called()

    def test_all_recorded_favorites_does_not_claim_phone_verification(self):
        target = decision_window()
        target.db.get_all.return_value = [pokemon(favorited=True)]
        with patch('pokemgr.gui.main_window.QMessageBox.information') as message, \
             patch('pokemgr.gui.main_window.QMessageBox.question') as question, \
             patch('pokemgr.gui.workers.FavoriteWorker') as made:
            target._start_favorite()
        self.assertIn('Already favorited (recorded): 1', message.call_args.args[2])
        self.assertIn('Phone state has not been checked.', message.call_args.args[2])
        question.assert_not_called()
        made.assert_not_called()

    def test_dry_real_and_unfavorite_keep_progress_controls_and_cleanup_in_decisions(self):
        for kind in ('dry', 'real', 'unfavorite'):
            with self.subTest(kind=kind):
                target, action = decision_window(), worker()
                factory = 'UnfavoriteWorker' if kind == 'unfavorite' else 'FavoriteWorker'
                with patch(f'pokemgr.gui.workers.{factory}', return_value=action) as made, \
                     patch('pokemgr.gui.main_window.QMessageBox.question', return_value=QMessageBox.Yes), \
                     patch('pokemgr.gui.main_window.QMessageBox.warning', return_value=QMessageBox.Yes):
                    if kind == 'unfavorite':
                        target._unfavorite_all()
                    else:
                        target._start_favorite(dry_run=kind == 'dry')
                        self.assertEqual(['Normal', 'Shiny'], made.call_args.kwargs['selected_passes'])
                self.assertIs(target._fav_worker, action)
                self.assertEqual('Unfavoriting' if kind == 'unfavorite' else 'Favoriting',
                                 target.decision_tab.set_favoriting.call_args.kwargs['action'])
                target._apply_speed_settings.assert_called_once()
                target.mass_tab.set_running.assert_not_called()
                action.progress.emit(2, 10, 'Checked Dragonite')
                target.decision_tab.on_fav_progress.assert_called_once_with(2, 10, 'Checked Dragonite')
                payload = {'traversal_id': 2, 'checked': 2, 'phase': 'reading'}
                action.action_progress.emit(payload)
                target.decision_tab.on_action_progress.assert_called_once_with(payload)
                target.mass_tab.on_action_progress.assert_not_called()
                action.error.emit('Read held')
                target.decision_tab.on_fav_error.assert_called_once_with('Read held')
                target.decision_tab.fav_pause_btn.text.return_value = 'Pause'
                target._toggle_fav_pause()
                action.pause.assert_called_once()
                target.decision_tab.set_fav_paused.assert_called_with(True)
                result = {'checked': 2, 'unfavorited' if kind == 'unfavorite' else 'favorited': 1,
                          'dry_run': kind == 'dry'}
                with patch('PySide6.QtCore.QTimer.singleShot') as timer:
                    action.finished.emit(result)
                    target.decision_tab.on_fav_finished.assert_not_called()
                    action.isRunning.return_value = False
                    with patch('pokemgr.gui.main_window.QMessageBox.information'):
                        timer.call_args.args[1]()
                target.decision_tab.on_fav_finished.assert_called_once_with(result)

    def test_stop_waits_for_actual_result_and_old_signals_cannot_replace_new_action(self):
        target, old, new = decision_window(), worker(), worker()
        target._start_decision_worker(old)
        with patch('PySide6.QtCore.QTimer.singleShot') as timer:
            target._stop_favorite()
            target.decision_tab.set_fav_stopping.assert_called_once()
            old.isRunning.return_value = False
            timer.call_args.args[1]()
            followup = timer.call_args.args[1]
        target.decision_tab.on_fav_finished.assert_not_called()
        result = {'favorited': 3, 'unmatched': 2, 'error': 'Device lost'}
        with patch('pokemgr.gui.main_window.QMessageBox.information'):
            old.finished.emit(result)
            followup()
        target.decision_tab.on_fav_finished.assert_called_once_with({**result, 'aborted': True})
        old.action_progress.emit({'phase': 'late after cleanup'})
        target.decision_tab.on_action_progress.assert_not_called()
        target._start_decision_worker(new)
        target.decision_tab.reset_mock()
        old.progress.emit(99, 99, 'old')
        old.action_progress.emit({'phase': 'late after replacement'})
        old.error.emit('old')
        old.finished.emit({'favorited': 99})
        followup()
        target.decision_tab.on_fav_finished.assert_not_called()
        target.decision_tab.on_fav_progress.assert_not_called()
        target.decision_tab.on_action_progress.assert_not_called()
        target.decision_tab.on_fav_error.assert_not_called()
        new.abort.assert_not_called()

    def test_engine_cannot_rewrite_decisions_during_a_phone_action(self):
        target = decision_window()
        target._mass_worker = worker()
        target._mass_worker.isRunning.return_value = True
        with patch('pokemgr.gui.main_window.QMessageBox.warning') as warning, \
             patch('pokemgr.gui.main_window.DecisionEngine') as engine:
            MainWindow._run_decision_engine(target)
        warning.assert_called_once()
        engine.assert_not_called()
        target.db.clear_decisions.assert_not_called()

    def test_completion_titles_preserve_unfavorite_dry_run_and_review_state(self):
        for result, expected in (({'unfavorited': 2, 'error': 'Lost'}, 'Unfavoriting Failed'),
                                 ({'favorited': 0, 'error': ''}, 'Favoriting Failed'),
                                 ({'favorited': 0, 'dry_run': True, 'error': ''}, 'Dry Run Failed'),
                                 ({'favorited': 2, 'unmatched': 1}, 'Needs Review'),
                                 ({'favorited': 2, 'dry_run': True}, 'Dry Run Complete')):
            with self.subTest(result=result):
                target = decision_window()
                with patch('pokemgr.gui.main_window.QMessageBox.information') as message:
                    MainWindow._on_favorite_finished(target, result)
                self.assertIn(expected, message.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
