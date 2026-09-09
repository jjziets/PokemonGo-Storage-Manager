"""Motion windows only accept fresh, matching independent CP observations."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tests import test_cp_model_recovery as recovery_fixture
from tests import test_settled_pair_reuse as pair_fixture
from tests.test_cp_model_recovery import _frame, _snapshot


SESSION = "a" * 32
AFTER_NS = 1_000_000_000


def stream_frame(seq, cp=5561, *, pts=None, session=SESSION, started=1.1,
                 clock_generation=1, **stats):
    image = _frame("detail", _snapshot(cp=cp, **stats))
    image.info.update(pokemgr_stream_session=session, pokemgr_stream_sequence=seq,
                      pokemgr_stream_pts_us=seq * 1000 if pts is None else pts,
                      pokemgr_capture_started_at=started,
                      pokemgr_source_clock_generation=clock_generation)
    return image


class StreamCpWindowTests(unittest.TestCase):
    def setUp(self):
        # Reuse the fixed resolver/profile setup, without inheriting its tests.
        self.fixture = recovery_fixture.CpModelRecoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.sm = self.fixture.sm
        self.sm.adb.has_stream_frames = True
        self.sm.reader._native_enabled = True
        self.sm.reader.native_fields = Mock(side_effect=lambda image: SimpleNamespace(
            display_name=image.info["snapshot"].display_name,
            hp=image.info["snapshot"].hp,
        ))
        self.sm.reader.native_cp = Mock(side_effect=lambda image, **kwargs:
                                        (image.info["snapshot"].cp, .95))
        self.closed = 0

    def window(self, frames, *, before_yield=None, on_close=None):
        def generate(**kwargs):
            try:
                for index, image in enumerate(frames):
                    if before_yield:
                        before_yield(index)
                    yield image
            finally:
                self.closed += 1
                if on_close:
                    on_close()
        self.sm.adb.stream_frames = Mock(side_effect=generate)
        return self.sm._read_cp_motion_window(
            self.fixture.original, {5561, 5594}, after_ns=AFTER_NS,
            expected_session=SESSION,
        )

    def test_two_whole_exact_cps_accept_and_close_before_return(self):
        frames = [stream_frame(1), stream_frame(2), stream_frame(3)]
        cp, decision, image = self.window(frames)
        self.assertEqual(5561, cp)
        self.assertTrue(decision.accepted)
        self.assertIs(image, frames[1])
        self.assertEqual(2, self.sm.reader.native_cp.call_count)
        self.assertEqual(1, self.closed)
        args = self.sm.adb.stream_frames.call_args.kwargs
        self.assertEqual((AFTER_NS, 1.2, 30),
                         (args["after_ns"], args["timeout"], args["max_frames"]))

    def test_partial_561_cannot_be_promoted_to_candidate(self):
        cp, decision, _ = self.window([stream_frame(1, 561), stream_frame(2, 561)])
        self.assertIsNone(cp)
        self.assertIsNone(decision)

    def test_two_possible_but_conflicting_cps_discard_window(self):
        cp, _, _ = self.window([stream_frame(1, 5561), stream_frame(2, 5594), stream_frame(3)])
        self.assertIsNone(cp)
        self.assertEqual(2, self.sm.reader.native_cp.call_count)
        self.assertEqual(1, self.closed)

    def test_native_same_frame_conflict_discards_window(self):
        self.sm.reader.native_cp.side_effect = [(-1, -1.0), (5561, .95), (5561, .95)]
        self.assertIsNone(self.window([stream_frame(1), stream_frame(2), stream_frame(3)])[0])
        self.assertEqual(1, self.sm.reader.native_cp.call_count)

    def test_wrong_hp_or_name_never_reaches_cp_acceptance(self):
        frames = [stream_frame(1, hp=172), stream_frame(2, display_name="Dragonite")]
        self.assertIsNone(self.window(frames)[0])
        self.sm.reader.native_cp.assert_not_called()

    def test_identical_frame_and_duplicate_pts_are_not_two_observations(self):
        for frames in ([stream_frame(1), stream_frame(1)],
                       [stream_frame(1, pts=1000), stream_frame(2, pts=1000)]):
            with self.subTest(frames=[image.info for image in frames]):
                self.assertIsNone(self.window(frames)[0])

    def test_reordered_sequence_or_pts_cannot_add_evidence(self):
        for frames in ([stream_frame(2), stream_frame(1)],
                       [stream_frame(1, pts=2000), stream_frame(2, pts=1000)]):
            with self.subTest(frames=[image.info for image in frames]):
                self.assertIsNone(self.window(frames)[0])

    def test_stale_source_time_and_wrong_or_changed_session_are_rejected(self):
        scenarios = [
            [stream_frame(1, started=1.0), stream_frame(2, started=.9)],
            [stream_frame(1, session="b" * 32), stream_frame(2, session="b" * 32)],
            [stream_frame(1), stream_frame(2, session="b" * 32), stream_frame(3)],
        ]
        for frames in scenarios:
            with self.subTest(frames=[image.info for image in frames]):
                self.assertIsNone(self.window(frames)[0])

    def test_missing_source_metadata_cannot_authorize_cp(self):
        frames = [stream_frame(1), stream_frame(2)]
        frames[0].info.pop("pokemgr_stream_pts_us")
        frames[1].info.pop("pokemgr_capture_started_at")
        self.assertIsNone(self.window(frames)[0])
        self.sm.reader.native_cp.assert_not_called()

    def test_changed_clock_generation_discards_window(self):
        self.assertIsNone(self.window([
            stream_frame(1), stream_frame(2, clock_generation=2), stream_frame(3),
        ])[0])

    def test_pause_and_resume_invalidate_controller_stream_evidence(self):
        self.sm.pause()
        self.sm.resume()
        self.assertEqual(2, self.sm.adb.invalidate_stream_frames.call_count)

    def test_capture_invalidation_retries_fresh_read_without_inputs(self):
        from pokemgr.adb.controller import StreamCaptureInvalidated
        image = stream_frame(1)
        self.sm._settled_frame_pair = (image, image)
        self.sm.adb.screencap = Mock(side_effect=[StreamCaptureInvalidated("paused"), image])
        self.sm.pause()
        self.assertIs(image, self.sm._fast_screencap())
        self.assertEqual(2, self.sm.adb.screencap.call_count)
        self.assertIsNone(self.sm._settled_frame_pair)
        self.sm.adb.tap.assert_not_called()
        self.sm.adb.swipe.assert_not_called()

    def test_capture_invalidation_does_not_retry_after_abort(self):
        from pokemgr.adb.controller import StreamCaptureInvalidated
        self.sm.adb.screencap = Mock(side_effect=StreamCaptureInvalidated("invalidated"))
        self.sm.abort()
        with self.assertRaises(StreamCaptureInvalidated):
            self.sm._fast_screencap()
        self.sm.adb.screencap.assert_called_once()

    def test_real_capture_error_is_never_retried(self):
        from pokemgr.adb.controller import ADBError
        self.sm.adb.screencap = Mock(side_effect=ADBError("display changed"))
        with self.assertRaisesRegex(ADBError, "display changed"):
            self.sm._fast_screencap()
        self.sm.adb.screencap.assert_called_once()

    def test_reopen_screen_observation_retries_invalidation_before_any_gesture(self):
        from pokemgr.adb.controller import StreamCaptureInvalidated
        self.sm.nav.detect_screen = Mock(side_effect=[StreamCaptureInvalidated("paused"), "appraisal"])
        # setUp replaces this method for isolated recovery tests.
        from pokemgr.indexer.state_machine import IndexingStateMachine
        self.assertTrue(IndexingStateMachine._reopen_appraisal(self.sm))
        self.assertEqual(2, self.sm.nav.detect_screen.call_count)
        self.sm.adb.tap.assert_not_called()

    def test_window_never_examines_more_than_thirty_frames(self):
        frames = [stream_frame(seq, 561) for seq in range(1, 31)]
        frames += [stream_frame(31), stream_frame(32)]
        self.assertIsNone(self.window(frames)[0])
        self.assertEqual(30, self.sm.reader.native_cp.call_count)

    def test_abort_between_frames_closes_and_discards_evidence(self):
        def abort_second(index):
            if index == 1:
                self.sm.abort()
        self.assertIsNone(self.window([stream_frame(1), stream_frame(2)],
                                     before_yield=abort_second)[0])
        self.assertEqual(1, self.closed)

    def test_pause_then_resume_between_frames_invalidates_generation(self):
        def pause_second(index):
            if index == 1:
                self.sm.pause()
                self.sm.resume()
        self.assertIsNone(self.window([stream_frame(1), stream_frame(2)],
                                     before_yield=pause_second)[0])
        self.assertEqual(1, self.closed)

    def test_pause_during_second_ocr_invalidates_evidence(self):
        def recognize(image, **kwargs):
            if image.info["pokemgr_stream_sequence"] == 2:
                self.sm.pause()
                self.sm.resume()
            return 5561, .95
        self.sm.reader.native_cp.side_effect = recognize
        self.assertIsNone(self.window([stream_frame(1), stream_frame(2)])[0])

    def test_pause_or_abort_during_generator_close_cannot_accept(self):
        for action in (self.sm.pause, self.sm.abort):
            with self.subTest(action=action.__name__):
                self.sm._abort = self.sm._paused = False
                self.assertIsNone(self.window([stream_frame(1), stream_frame(2)],
                                             on_close=action)[0])

    def test_generator_final_target_validation_failure_propagates(self):
        def changed():
            raise RuntimeError("display identity changed")
        with self.assertRaisesRegex(RuntimeError, "display identity changed"):
            self.window([stream_frame(1), stream_frame(2)], on_close=changed)

    def _motion_windows(self, cps):
        from pokemgr.indexer.snapshot import validate_snapshot
        results = []
        for index, cp in enumerate(cps):
            image = stream_frame(index + 1, cp=cp or -1)
            decision = validate_snapshot(replace(self.fixture.original, cp=cp), False) if cp else None
            results.append((cp, decision, image))
        self.sm._read_cp_motion_window = Mock(side_effect=results)

    def test_initial_tap_window_avoids_delay_and_capture_then_rechecks_appraisal(self):
        self.fixture._prepare([])
        self._motion_windows([5561])
        decision, frame = self.fixture._recover()
        self.assertTrue(decision.accepted)
        self.assertIs(frame, self.fixture.final)
        self.assertEqual(2, self.sm._fast_screencap.call_count)
        self.assertEqual(1, len(self.fixture._model_taps()))
        self.assertFalse(any(call.args == (.2, 1.0) for call in self.fixture.delay.call_args_list))
        self.sm._read_appraisal_snapshot.assert_any_call(self.fixture.final)

    def test_failed_window_uses_one_fresh_capture_before_next_gesture(self):
        self.fixture._prepare([5561])
        self._motion_windows([None])
        decision, _ = self.fixture._recover()
        self.assertTrue(decision.accepted)
        self.assertEqual(3, self.sm._fast_screencap.call_count)
        self.assertEqual(1, len(self.fixture._model_taps()))
        self.sm.reader.read_detail_screen.assert_any_call(self.fixture.attempt_frames[0])
        self.assertFalse(any(call.args == (.2, 1.0) for call in self.fixture.delay.call_args_list))

    def test_rotation_and_rotated_tap_each_use_window(self):
        self.fixture._prepare([-1] * 4, rotation_cps=[-1], rotation_animation_cps=[-1])
        self._motion_windows([None] * 5 + [5561])
        decision, _ = self.fixture._recover()
        self.assertTrue(decision.accepted)
        self.assertEqual(6, self.sm._read_cp_motion_window.call_count)
        self.assertEqual(7, self.sm._fast_screencap.call_count)
        self.assertEqual(1, self.sm.adb.swipe.call_count)
        self.assertEqual(5, len(self.fixture._model_taps()))
        self.assertFalse(any(call.args == (.2, 1.0) for call in self.fixture.delay.call_args_list))

    def test_stream_window_still_requires_final_full_identity_match(self):
        self.fixture._prepare([], final=_snapshot(hp=172))
        self._motion_windows([5561])
        with self.assertRaisesRegex(RuntimeError, "final appraisal identity changed"):
            self.fixture._recover()
        self.sm.db.insert_pokemon.assert_not_called()

    def test_pause_after_window_acceptance_discards_cp_before_final_acceptance(self):
        self.fixture._prepare([])
        self._motion_windows([5561])
        def pause_on_reopen():
            self.sm.pause()
            self.sm.resume()
            return True
        self.sm._reopen_appraisal.side_effect = pause_on_reopen
        decision, _ = self.fixture._recover()
        self.assertFalse(decision.accepted)
        self.sm._read_appraisal_snapshot.assert_any_call(self.fixture.final)

    def test_pause_during_final_cp_read_cannot_attach_window_evidence(self):
        self.fixture._prepare([], final=_snapshot(cp=-1))
        self._motion_windows([5561])
        def read_cp(image, **kwargs):
            if image is self.fixture.final:
                self.sm.pause()
                self.sm.resume()
            return -1, .0
        self.sm.reader.read_cp.side_effect = read_cp
        decision, _ = self.fixture._recover()
        self.assertFalse(decision.accepted)

    def test_no_stream_or_no_native_reader_preserves_legacy_delay(self):
        for stream, native in [(False, True), (True, False)]:
            with self.subTest(stream=stream, native=native):
                self.sm.adb.has_stream_frames = stream
                self.sm.reader._native_enabled = native
                self.fixture.delay.reset_mock()
                self.fixture._prepare([5561])
                self._motion_windows([])
                decision, _ = self.fixture._recover()
                self.assertTrue(decision.accepted)
                self.sm._read_cp_motion_window.assert_not_called()
                self.fixture.delay.assert_any_call(.2, 1.0)
                self.assertEqual(3, self.sm._fast_screencap.call_count)


class StreamPairGenerationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = pair_fixture.SettledPairReuseTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    @staticmethod
    def pair_frame(sequence, generation=1):
        image = pair_fixture.frame()
        image.info.update(pokemgr_stream_session=SESSION,
                          pokemgr_stream_sequence=sequence,
                          pokemgr_stream_pts_us=sequence * 1000,
                          pokemgr_source_clock_generation=generation)
        return image

    def test_matching_clock_generation_reuses_distinct_source_pair(self):
        older, newest = self.pair_frame(1), self.pair_frame(2)
        decision, frame, status, _ = self.fixture.acquire([older, newest])
        self.assertEqual("ok", status)
        self.assertTrue(decision.accepted)
        self.assertIs(newest, frame)
        self.assertEqual(2, self.fixture.sm._fast_screencap.call_count)

    def test_changed_clock_or_duplicate_pts_requires_fresh_confirmation(self):
        for fault in ("clock", "PTS", "session", "missing_clock"):
            with self.subTest(fault=fault):
                older, newest, fresh = self.pair_frame(1), self.pair_frame(2), pair_fixture.frame()
                if fault == "clock":
                    newest.info["pokemgr_source_clock_generation"] = 2
                elif fault == "PTS":
                    newest.info["pokemgr_stream_pts_us"] = 1000
                elif fault == "session":
                    newest.info["pokemgr_stream_session"] = "b" * 32
                else:
                    newest.info.pop("pokemgr_source_clock_generation")
                decision, frame, status, _ = self.fixture.acquire([older, newest, fresh])
                self.assertEqual("ok", status)
                self.assertTrue(decision.accepted)
                self.assertIs(fresh, frame)
                self.assertEqual(3, self.fixture.sm._fast_screencap.call_count)


if __name__ == "__main__":
    unittest.main()
