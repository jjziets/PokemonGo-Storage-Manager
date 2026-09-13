"""Mass Actions keeps each initiating action's controls and outcome together."""

# TRACEWEAVER: file-role=mass-action-gui-tests; req=REQ-MASS-001; trace=TRACE-MASS-001; verifies=VER-SCAN-001

import os
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QMessageBox

from pokemgr.gui.main_window import MainWindow
from pokemgr.gui.widgets.mass_actions import MassActions
from tests.test_mass_action_scanning import pokemon


class Event:
    def __init__(self): self.callbacks = []
    def connect(self, callback): self.callbacks.append(callback)
    def emit(self, *args):
        for callback in self.callbacks:
            callback(*args)


def worker():
    result = Mock()
    result.progress, result.error, result.finished = Event(), Event(), Event()
    result.action_progress = Event()
    result.isRunning.return_value = False
    result.start.side_effect = lambda: setattr(result.isRunning, 'return_value', True)
    return result


def window():
    result = SimpleNamespace(adb=Mock(), profile=Mock(), db=Mock(), decision_tab=Mock(),
                             mass_tab=Mock(), _scan_worker=None, _mass_worker=None,
                             _refresh_collection=Mock(), _refresh_decisions=Mock(),
                             _apply_speed_settings=Mock(), statusBar=Mock(return_value=Mock()))
    result.db.get_all.return_value = [pokemon()]
    result.decision_tab.get_selected_fav_passes.return_value = ['Normal', 'Shiny']
    for name in ('_start_mass_worker', '_on_mass_action_finished', '_start_favorite',
                 '_unfavorite_all', '_start_favorite_filter', '_toggle_mass_pause',
                 '_stop_mass_action', '_force_stop_mass'):
        setattr(result, name, MethodType(getattr(MainWindow, name), result))
    return result


class MassActionRoutingTests(unittest.TestCase):
    def test_mass_buttons_explicitly_select_mass_origin(self):
        target = SimpleNamespace(scan_tab=Mock(), decision_tab=Mock(), collection_tab=Mock(),
                                 mass_tab=Mock(), tabs=Mock())
        for name, value in MainWindow.__dict__.items():
            if callable(value) and name.startswith('_'):
                setattr(target, name, Mock())
        MainWindow._connect_signals(target)
        target.mass_tab.unfav_all_btn.clicked.connect.call_args.args[0]()
        target._unfavorite_all.assert_called_once_with(from_mass=True)
        target.mass_tab.fav_keepers_dry_btn.clicked.connect.call_args.args[0]()
        target.mass_tab.fav_keepers_real_btn.clicked.connect.call_args.args[0]()
        self.assertEqual([c.kwargs for c in target._start_favorite.call_args_list],
                         [{'dry_run': True, 'from_mass': True}, {'dry_run': False, 'from_mass': True}])

    def test_all_action_types_route_progress_errors_pause_and_completion_to_mass(self):
        for kind in ('dry', 'real', 'category', 'unfavorite'):
            with self.subTest(kind=kind):
                target, action = window(), worker()
                factory_name = ('FavoriteFilterWorker' if kind == 'category' else
                                'UnfavoriteWorker' if kind == 'unfavorite' else 'FavoriteWorker')
                with patch(f'pokemgr.gui.workers.{factory_name}', return_value=action) as factory, \
                     patch('pokemgr.gui.main_window.QMessageBox.question', return_value=QMessageBox.Yes), \
                     patch('pokemgr.gui.main_window.QMessageBox.warning', return_value=QMessageBox.Yes):
                    if kind in ('dry', 'real'):
                        target._start_favorite(dry_run=kind == 'dry', from_mass=True)
                        self.assertEqual(factory.call_args.kwargs['dry_run'], kind == 'dry')
                    elif kind == 'unfavorite':
                        target._unfavorite_all(from_mass=True)
                    else:
                        target._start_favorite_filter('shiny', 'Shiny')
                self.assertIs(target._mass_worker, action)
                target.decision_tab.set_favoriting.assert_not_called()
                action.progress.emit(3, 10, 'Checked Dragonite')
                target.mass_tab.on_progress.assert_called_once_with(3, 10, 'Checked Dragonite')
                payload = {'traversal_id': 2, 'checked': 3, 'phase': 'reading'}
                action.action_progress.emit(payload)
                target.mass_tab.on_action_progress.assert_called_once_with(payload)
                target.decision_tab.on_action_progress.assert_not_called()
                action.error.emit('Read held')
                target.mass_tab.on_error.assert_called_once_with('Read held')
                target.mass_tab.pause_btn.text.return_value = 'Pause'
                target._toggle_mass_pause()
                action.pause.assert_called_once()
                target.mass_tab.set_paused.assert_called_with(True)
                target.mass_tab.pause_btn.text.return_value = 'Resume'
                target._toggle_mass_pause()
                action.resume.assert_called_once()
                target.mass_tab.set_paused.assert_called_with(False)
                result = {'checked': 3, 'unresolved': 1}
                with patch('PySide6.QtCore.QTimer.singleShot') as timer:
                    action.finished.emit(result)
                target.mass_tab.on_finished.assert_not_called()
                action.isRunning.return_value = False
                timer.call_args.args[1]()
                target.mass_tab.on_finished.assert_called_once_with(result)

    def test_declined_confirmation_starts_nothing_and_preserves_idle_controls(self):
        for action in ('keepers', 'unfavorite'):
            target = window()
            with patch('pokemgr.gui.main_window.QMessageBox.question', return_value=QMessageBox.No), \
                 patch('pokemgr.gui.main_window.QMessageBox.warning', return_value=QMessageBox.No), \
                 patch('pokemgr.gui.workers.FavoriteWorker') as favorite, \
                 patch('pokemgr.gui.workers.UnfavoriteWorker') as unfavorite:
                if action == 'keepers':
                    target._start_favorite(from_mass=True)
                else:
                    target._unfavorite_all(from_mass=True)
            favorite.assert_not_called()
            unfavorite.assert_not_called()
            target.mass_tab.set_running.assert_not_called()

    def test_other_tabs_cannot_start_an_action_while_mass_owns_device(self):
        for method, arguments in (('_start_favorite', {}), ('_unfavorite_all', {}),
                                  ('_start_favorite_filter', {'query': 'shiny', 'label': 'Shiny'}),
                                  ('_start_full_scan', {}), ('_start_current_scan', {})):
            target = window()
            target._mass_worker = worker()
            target._mass_worker.isRunning.return_value = True
            with patch('pokemgr.gui.main_window.QMessageBox.warning') as warning:
                getattr(MainWindow, method)(target, **arguments)
            self.assertEqual(warning.call_args.args[1], 'Operation Running')
            target.db.get_all.assert_not_called()

    def test_stop_preserves_failure_and_stale_callbacks_cannot_finish_new_action(self):
        target, old, new = window(), worker(), worker()
        target._start_mass_worker(old, 'Old')
        with patch('PySide6.QtCore.QTimer.singleShot') as timer:
            target._stop_mass_action()
        target.mass_tab.set_stopping.assert_called_once()
        stop_callback = timer.call_args.args[1]
        old.isRunning.return_value = False
        old.finished.emit({'favorited': 2, 'error': 'Device lost'})
        target.mass_tab.on_finished.assert_called_once_with({'favorited': 2, 'error': 'Device lost', 'aborted': True})
        stop_callback()
        self.assertEqual(target.mass_tab.on_finished.call_count, 1)
        old.action_progress.emit({'phase': 'late after cleanup'})
        target.mass_tab.on_action_progress.assert_not_called()
        target._start_mass_worker(new, 'New')
        target.mass_tab.reset_mock()
        old.progress.emit(10, 10, 'Old progress')
        old.action_progress.emit({'phase': 'late after replacement'})
        old.error.emit('Old error')
        old.finished.emit({'favorited': 10})
        stop_callback()
        target.mass_tab.on_progress.assert_not_called()
        target.mass_tab.on_action_progress.assert_not_called()
        target.mass_tab.on_error.assert_not_called()
        target.mass_tab.on_finished.assert_not_called()
        new.abort.assert_not_called()

    def test_keeper_completion_never_promises_all_keepers_are_protected(self):
        for result in ({'favorited': 2, 'unresolved': 1}, {'dry_run': True, 'favorited': 2},
                       {'favorited': 2, 'aborted': True}, {'error': 'Read failed'}):
            target = window()
            with patch('pokemgr.gui.main_window.QMessageBox.information') as information:
                MainWindow._on_favorite_finished(target, result)
            self.assertNotIn('mass-transfer', information.call_args.args[2])
            self.assertNotIn('all keepers', information.call_args.args[2].lower())

    def test_stop_poll_before_result_delivery_preserves_actual_counts_and_error(self):
        target, action = window(), worker()
        target._start_mass_worker(action, 'Keepers')
        with patch('PySide6.QtCore.QTimer.singleShot') as timer:
            target._stop_mass_action()
            action.isRunning.return_value = False
            timer.call_args.args[1]()  # Exit was observed before queued result delivery.
            followup = timer.call_args.args[1]
        target.mass_tab.on_finished.assert_not_called()
        result = {'favorited': 3, 'unresolved': 2, 'error': 'Device lost'}
        action.finished.emit(result)
        followup()
        target.mass_tab.on_finished.assert_called_once_with({**result, 'aborted': True})


class MassActionWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = MassActions()
        self.addCleanup(self.panel.deleteLater)
        self.panel.set_running(True, 'Keepers — dry run')

    def test_active_paused_stopping_and_idle_controls(self):
        actions = [self.panel.unfav_all_btn, self.panel.fav_keepers_dry_btn,
                   self.panel.fav_keepers_real_btn, *self.panel._cat_buttons]
        self.assertTrue(all(not button.isEnabled() for button in actions))
        self.panel.set_paused(True)
        self.assertEqual(self.panel.pause_btn.text(), 'Resume')
        self.assertEqual(self.panel.status_label.text(), 'Paused')
        self.panel.set_paused(False)
        self.assertEqual(self.panel.status_label.text(), 'Keepers — dry run')
        self.panel.set_stopping()
        self.assertFalse(self.panel.pause_btn.isEnabled())
        self.panel.on_finished({'checked': 7, 'aborted': True})
        self.assertTrue(self.panel.status_label.text().startswith('Stopped'))
        self.assertTrue(all(button.isEnabled() for button in actions))
        self.panel.set_running(True, 'New')
        self.assertTrue(self.panel.pause_btn.isEnabled())
        self.assertTrue(self.panel.stop_btn.isEnabled())

    def test_dry_run_error_and_unresolved_outcomes_stay_visible(self):
        self.panel.on_progress(3, 10, 'Dragonite <nickname>')
        self.panel.on_finished({'favorited': 2, 'checked': 3, 'unresolved': 1, 'dry_run': True})
        self.assertIn('Dry run complete', self.panel.status_label.text())
        self.assertIn('would favorite: 2', self.panel.status_label.text())
        self.assertNotIn('dry_run:', self.panel.status_label.text())
        self.assertIn('unresolved: 1', self.panel.status_label.text())
        self.assertIn('Dragonite <nickname>', self.panel.action_log.toPlainText())
        self.panel.set_running(True, 'Next')
        self.panel.on_error('Lost capture')
        self.panel.on_finished({'error': 'Lost capture', 'aborted': True})
        self.assertTrue(self.panel.status_label.text().startswith('Failed'))
        self.assertFalse(self.panel.action_log.isHidden())

    def test_progress_rate_excludes_paused_time_and_resets_for_a_new_pass(self):
        with patch('pokemgr.gui.widgets.mass_actions.time.monotonic', return_value=10.):
            self.panel.set_running(True, 'Keepers')
        with patch('pokemgr.gui.widgets.mass_actions.time.monotonic', return_value=20.):
            self.panel.on_progress(10, 100, 'First pass')
        with patch('pokemgr.gui.widgets.mass_actions.time.monotonic', return_value=30.):
            self.panel.set_paused(True)
        with patch('pokemgr.gui.widgets.mass_actions.time.monotonic', return_value=50.):
            self.panel.set_paused(False)
        with patch('pokemgr.gui.widgets.mass_actions.time.monotonic', return_value=60.):
            self.panel.on_progress(20, 100, 'Continued')
        self.assertIn('40/min', self.panel.progress_bar.format())
        with patch('pokemgr.gui.widgets.mass_actions.time.monotonic', return_value=70.):
            self.panel.on_progress(1, 20, 'Next pass')
        self.assertEqual(self.panel._start_time, 70.)

    def test_unmatched_or_ambiguous_completion_is_amber_and_preserves_dry_run(self):
        for key in ('unmatched', 'ambiguous', 'unresolved'):
            for dry in (False, True):
                with self.subTest(key=key, dry=dry):
                    self.panel.set_running(True, 'Keepers')
                    self.panel.on_finished({'favorited': 2, key: 1, 'dry_run': dry})
                    self.assertIn('needs review', self.panel.status_label.text())
                    self.assertIn('#fc8', self.panel.status_label.styleSheet())
                    self.assertIn(f'{key}: 1', self.panel.status_label.text())
                    if dry:
                        self.assertTrue(self.panel.status_label.text().startswith('Dry run complete'))
                        self.assertIn('would favorite: 2', self.panel.status_label.text())

    def test_terminal_note_is_visible_in_status_and_persistent_log(self):
        note = 'Filter empty or count unreadable; no Pokemon opened'
        self.panel.on_finished({'checked': 0, 'favorited': 0, 'note': note})
        self.assertIn(note, self.panel.status_label.text())
        self.assertIn(note, self.panel.action_log.toPlainText())
        self.assertFalse(self.panel.status_label.isHidden())
        self.assertFalse(self.panel.action_log.isHidden())

    def test_zero_unmatched_and_ambiguous_counts_keep_normal_completion(self):
        self.panel.on_finished({'favorited': 2, 'unmatched': 0, 'ambiguous': 0})
        self.assertTrue(self.panel.status_label.text().startswith('Done'))
        self.assertIn('#8f8', self.panel.status_label.styleSheet())


if __name__ == '__main__':
    unittest.main()
