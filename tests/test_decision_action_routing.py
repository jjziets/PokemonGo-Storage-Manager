"""Decisions owns the same verified action workers through cleanup and stop."""

from types import MethodType
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QMessageBox

from pokemgr.gui.main_window import MainWindow
from tests.test_mass_action_gui import window, worker


def decision_window():
    target = window()
    for name in ('_start_decision_worker', '_finish_decision_action', '_toggle_fav_pause',
                 '_stop_favorite', '_force_stop_favorite'):
        setattr(target, name, MethodType(getattr(MainWindow, name), target))
    return target


class DecisionActionRoutingTests(unittest.TestCase):
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
        target._start_decision_worker(new)
        target.decision_tab.reset_mock()
        old.progress.emit(99, 99, 'old')
        old.error.emit('old')
        old.finished.emit({'favorited': 99})
        followup()
        target.decision_tab.on_fav_finished.assert_not_called()
        target.decision_tab.on_fav_progress.assert_not_called()
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
