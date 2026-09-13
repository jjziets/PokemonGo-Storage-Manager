"""Returning from appraisal observes transitions before sending another input."""

# TRACEWEAVER: file-role=storage-navigation-settle-tests; req=REQ-SCAN-003,REQ-STREAM-001; trace=TRACE-SCAN-003,TRACE-STREAM-001; verifies=VER-SCAN-001

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from PIL import Image

from pokemgr.adb.navigator import GameNavigator
from pokemgr.calibration.regions import ScreenRegions


class StorageNavigationSettleTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch('pokemgr.config.CACHE_DIR', self.root))
        self.sleep = self.enterContext(patch('pokemgr.adb.navigator.time.sleep'))
        self.delay = self.enterContext(patch('pokemgr.adb.navigator.human_delay'))
        self.adb = Mock(display_id=15)
        self.adb.get_device_info.return_value = SimpleNamespace(width=968, height=2376)
        self.regions = ScreenRegions.default_for_resolution(968, 2376, 420)
        self.aborted = False
        self.generation = 0
        self.nav = GameNavigator(
            self.adb, self.regions, cancelled=lambda: self.aborted,
            observation_generation=lambda: self.generation,
        )
        self.events = []
        self.adb.tap.side_effect = lambda *args, **kwargs: self.events.append(('tap', args))
        self.adb.key_event.side_effect = lambda key: self.events.append(('key', key))
        self.nav.ensure_pokemon_go = Mock(side_effect=AssertionError('must not relaunch stream'))

    def observations(self, screens, on_observe=None):
        screens = iter(screens)

        def capture():
            screen = next(screens)
            frame = Image.new('RGB', (96, 237), (40, 100, 150))
            frame.info['test_screen'] = screen
            frame.info['pokemgr_stream_sequence'] = self.adb.screencap.call_count
            return frame

        def detect():
            frame = self.adb.screencap()
            self.nav._last_screen_image = frame
            screen = frame.info['test_screen']
            self.events.append(('observe', screen))
            if on_observe:
                on_observe(screen)
            return screen

        self.adb.screencap.side_effect = capture
        self.nav.detect_screen = Mock(side_effect=detect)

    def assert_no_restart(self):
        self.nav.ensure_pokemon_go.assert_not_called()
        self.adb.start_pokemon_go.assert_not_called()
        self.adb.shell.assert_not_called()
        self.adb.wake_screen.assert_not_called()

    def test_transient_frames_after_close_and_back_do_not_end_the_pass(self):
        self.observations(['appraisal', 'other', 'detail', 'other', 'storage'])
        self.assertTrue(self.nav.navigate_to_storage())
        self.assertEqual([
            ('observe', 'appraisal'), ('tap', self.regions.appraisal_close_x),
            ('observe', 'other'), ('observe', 'detail'), ('key', 4),
            ('observe', 'other'), ('observe', 'storage'),
        ], self.events)
        self.assertEqual(5, self.adb.screencap.call_count)
        self.assertEqual([], list(self.root.iterdir()))
        self.assert_no_restart()

    def test_close_that_reaches_storage_never_sends_back(self):
        self.observations(['appraisal', 'other', 'storage'])
        self.assertTrue(self.nav.navigate_to_storage())
        self.adb.tap.assert_called_once_with(*self.regions.appraisal_close_x, jitter=3)
        self.adb.key_event.assert_not_called()

    def test_phone_and_tablet_returns_use_their_exact_calibrated_close_target(self):
        for width, height, density in ((968, 2376, 420), (1440, 2304, 280)):
            with self.subTest(size=(width, height)):
                self.adb.reset_mock()
                self.adb.get_device_info.return_value = SimpleNamespace(width=width, height=height)
                self.regions = ScreenRegions.default_for_resolution(width, height, density)
                self.regions.appraisal_close_x = (width // 2 + 5, height - 120)
                self.nav = GameNavigator(self.adb, self.regions)
                self.observations(['appraisal', 'detail', 'storage'])
                self.assertTrue(self.nav.navigate_to_storage())
                self.adb.tap.assert_called_once_with(*self.regions.appraisal_close_x, jitter=3)
                self.adb.key_event.assert_called_once_with(4)

    def test_same_departure_surfaces_are_observed_without_duplicate_inputs(self):
        self.observations(['appraisal', 'appraisal', 'other', 'detail', 'detail', 'storage'])
        self.assertTrue(self.nav.navigate_to_storage())
        self.adb.tap.assert_called_once()
        self.adb.key_event.assert_called_once_with(4)

    def test_unknown_before_any_input_can_settle_to_storage(self):
        self.observations(['other', 'other', 'other', 'storage'])
        self.assertTrue(self.nav.navigate_to_storage())
        self.assertEqual([call(0.25)] * 3, self.sleep.call_args_list)
        self.adb.tap.assert_not_called()
        self.adb.key_event.assert_not_called()
        self.assert_no_restart()

    def test_persistent_unknown_holds_after_four_fresh_frames_and_saves_last_frame(self):
        # Exercise the real classifier and diagnostic capture retention too.
        def capture():
            index = self.adb.screencap.call_count
            frame = Image.new('RGB', (96, 237), (30 + index, 100, 150))
            frame.info['pokemgr_stream_sequence'] = index
            return frame

        self.adb.screencap.side_effect = capture
        self.assertFalse(self.nav.navigate_to_storage())
        self.assertEqual(4, self.adb.screencap.call_count)
        self.adb.tap.assert_not_called()
        self.adb.key_event.assert_not_called()
        self.assert_no_restart()
        artifacts = self.root / 'navigation_failures'
        metadata_path, = artifacts.glob('*.json')
        metadata = json.loads(metadata_path.read_text())
        self.assertEqual('other', metadata['screen'])
        self.assertEqual(4, metadata['attempt'])
        self.assertEqual(4, metadata['capture']['pokemgr_stream_sequence'])
        with Image.open(metadata_path.with_suffix('.png')) as saved:
            self.assertEqual((34, 100, 150), saved.getpixel((0, 0)))

    def test_persistent_unknown_after_close_never_sends_unproven_back(self):
        self.observations(['appraisal'] + ['other'] * 4)
        self.assertFalse(self.nav.navigate_to_storage())
        self.adb.tap.assert_called_once()
        self.adb.key_event.assert_not_called()
        metadata_path, = (self.root / 'navigation_failures').glob('*.json')
        self.assertEqual('appraisal', json.loads(metadata_path.read_text())['pending_departure'])
        self.assert_no_restart()

    def test_uncompleted_departure_exhausts_without_repeat_x_or_back(self):
        for screen in ('appraisal', 'detail'):
            with self.subTest(screen=screen):
                self.adb.reset_mock()
                self.observations([screen] * 8)
                self.assertFalse(self.nav.navigate_to_storage())
                self.assertEqual(8, self.adb.screencap.call_count)
                self.assertEqual(1 if screen == 'appraisal' else 0, self.adb.tap.call_count)
                self.assertEqual(1 if screen == 'detail' else 0, self.adb.key_event.call_count)

    def test_pause_during_detail_observation_discards_stale_back_authority(self):
        def pause_and_change(screen):
            if screen == 'detail':
                self.generation += 1

        self.observations(['appraisal', 'detail', 'storage'], pause_and_change)
        self.assertTrue(self.nav.navigate_to_storage())
        self.adb.tap.assert_called_once()
        self.adb.key_event.assert_not_called()

    def test_pause_after_sent_input_does_not_repeat_that_input(self):
        for screen, following in (('appraisal', 'detail'), ('detail', 'storage')):
            with self.subTest(screen=screen):
                self.adb.reset_mock()
                self.delay.side_effect = lambda *_args: setattr(self, 'generation', self.generation + 1)
                self.observations([screen, screen, following, 'storage'])
                self.assertTrue(self.nav.navigate_to_storage())
                self.assertEqual(1 if screen == 'appraisal' else 0, self.adb.tap.call_count)
                self.adb.key_event.assert_called_once_with(4)

    def test_pause_during_map_menu_delay_discards_second_tap(self):
        self.delay.side_effect = lambda *_args: setattr(self, 'generation', self.generation + 1)
        self.observations(['game_map', 'storage'])
        self.assertTrue(self.nav.navigate_to_storage())
        self.adb.tap.assert_called_once_with(*self.regions.map_pokeball, jitter=5)

    def test_abort_during_close_or_back_wait_prevents_further_capture_or_input(self):
        for screen in ('appraisal', 'detail'):
            with self.subTest(screen=screen):
                self.aborted = False
                self.adb.reset_mock()
                self.observations([screen])
                self.delay.side_effect = lambda *_args: setattr(self, 'aborted', True)
                self.assertFalse(self.nav.navigate_to_storage())
                self.assertEqual(1, self.adb.screencap.call_count)
                self.assertEqual(1 if screen == 'appraisal' else 0, self.adb.tap.call_count)
                self.assertEqual(1 if screen == 'detail' else 0, self.adb.key_event.call_count)
        self.assertEqual([], list(self.root.iterdir()))

    def test_abort_during_unknown_settle_prevents_further_capture_or_input(self):
        self.observations(['other'])
        self.sleep.side_effect = lambda *_args: setattr(self, 'aborted', True)
        self.assertFalse(self.nav.navigate_to_storage())
        self.assertEqual(1, self.adb.screencap.call_count)
        self.adb.tap.assert_not_called()
        self.adb.key_event.assert_not_called()
        self.assertEqual([], list(self.root.iterdir()))

    def test_abort_during_observation_prevents_departure_input(self):
        self.observations(['appraisal'], lambda _screen: setattr(self, 'aborted', True))
        self.assertFalse(self.nav.navigate_to_storage())
        self.adb.tap.assert_not_called()
        self.adb.key_event.assert_not_called()

    def test_diagnostic_write_error_does_not_change_safe_hold(self):
        self.observations(['other'] * 4)
        with patch('PIL.Image.Image.save', side_effect=OSError('disk full')), \
                self.assertLogs('pokemgr.adb.navigator', level='ERROR'):
            self.assertFalse(self.nav.navigate_to_storage())
        self.adb.tap.assert_not_called()
        self.adb.key_event.assert_not_called()
        self.assert_no_restart()


if __name__ == '__main__':
    unittest.main()
