# TRACEWEAVER: file-role=stable-scan-loop-tests; req=REQ-SCAN-003; trace=TRACE-SCAN-003; verifies=VER-SCAN-001
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from pokemgr.calibration.profile import CalibrationProfile
from pokemgr.calibration.regions import ScreenRegions
from pokemgr.indexer.snapshot import AppraisalSnapshot, SnapshotDecision
from pokemgr.indexer.state_machine import IndexingStateMachine


def profile():
    return CalibrationProfile(
        device_model="test",
        serial="serial",
        resolution="968x2376",
        density=420,
        regions=ScreenRegions.default_for_resolution(968, 2376, density=420),
    )


def accepted(species="Zubat", cp=10, hp=12, ivs=(0, 12, 15)):
    snapshot = AppraisalSnapshot(
        display_name=species,
        detected_species=species,
        caught_species=species.split(" (")[0],
        cp=cp,
        hp=hp,
        atk=ivs[0],
        def_=ivs[1],
        sta=ivs[2],
        shiny=False,
        shadow=False,
        favorited=False,
        lucky=False,
        gender="none",
        weight_tag="",
        height_tag="",
        is_dynamax=False,
        detail_confidence=0.9,
        appraisal_confidence=0.95,
    )
    return SnapshotDecision(True, "exact", snapshot, level=1.0)


class _ADB:
    def __init__(self):
        self.actions = []

    def get_device_info(self):
        return SimpleNamespace(fingerprint="test-device")

    def tap(self, *args, **kwargs):
        self.actions.append(("tap", args, kwargs))

    def swipe(self, *args, **kwargs):
        self.actions.append(("swipe", args, kwargs))

    def screencap(self):
        return Image.new("RGB", (968, 2376), "white")

    def get_battery_level(self):
        return 100


class _DB:
    def __init__(self):
        self.rows = []
        self.sessions = []

    def create_session(self, session_id, fingerprint):
        self.sessions.append((session_id, fingerprint))

    def complete_session(self, session_id, total):
        self.completed = (session_id, total)

    def insert_pokemon(self, pokemon, session_id, position):
        self.rows.append((pokemon, session_id, position))
        return len(self.rows)


class StableScanLoopTests(unittest.TestCase):
    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_invalidated_confirmation_reacquires_without_losing_pending_transition(self, _paddle):
        adb, db = _ADB(), _DB()
        sm = IndexingStateMachine(adb, profile(), db)
        frame = adb.screencap()
        sm._transition_required = True
        sm._last_stable_image = frame
        sm._acquire_validated_snapshot = Mock(side_effect=[
            (None, None, "reacquire", "pause invalidated appraisal confirmation"),
            (accepted(), frame, "ok", "exact"),
        ])
        sm._advance_from_confirmed_appraisal = Mock()
        sm._favorite_unresolved_snapshot = Mock()

        sm.start(expected_total=1)

        self.assertEqual((1, 1, 0), (sm.count, sm.visited_count, sm.skipped_count))
        self.assertTrue(all(call.kwargs["require_transition"]
                            for call in sm._acquire_validated_snapshot.call_args_list))
        sm._advance_from_confirmed_appraisal.assert_not_called()
        sm._favorite_unresolved_snapshot.assert_not_called()

    def test_advance_uses_calibrated_conservative_swipe(self):
        adb = _ADB()
        sm = IndexingStateMachine(adb, profile(), _DB())

        self.assertTrue(sm._fast_swipe())

        self.assertEqual("swipe", adb.actions[0][0])
        regions = profile().regions
        self.assertEqual(
            (*regions.swipe_start, *regions.swipe_end,
             regions.swipe_duration_ms),
            adb.actions[0][1],
        )
        self.assertEqual(0, adb.actions[0][2]["jitter"])

    @patch("pokemgr.reader.ocr.is_in_gym", return_value=False)
    @patch("pokemgr.reader.ocr.read_caught_species",
           side_effect=RuntimeError("bubble OCR crashed"))
    def test_background_ocr_exception_marks_snapshot_incomplete(
            self, _caught, _gym):
        adb = _ADB()
        sm = IndexingStateMachine(adb, profile(), _DB())
        sm.reader.read_hp = Mock(return_value=12)
        sm.reader.read_detail_screen = Mock(return_value={
            "species": "Zubat",
            "cp": 10,
            "shiny": False,
            "shadow": False,
            "favorited": False,
            "lucky": False,
            "confidence": 1.0,
        })
        sm.reader.read_appraisal_screen = Mock(return_value={
            "atk": 0,
            "def_": 12,
            "sta": 15,
            "confidence": 1.0,
        })

        detail, _appraisal = sm._read_appraisal_snapshot(adb.screencap())

        self.assertFalse(detail["snapshot_read_complete"])

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_two_verified_positions_commit_once_and_advance_once(self, _paddle):
        adb = _ADB()
        db = _DB()
        sm = IndexingStateMachine(adb, profile(), db)
        frame = adb.screencap()
        sm._acquire_validated_snapshot = Mock(side_effect=[
            (accepted(), frame, "ok", "exact"),
            (accepted("Zorua (Hisuian)", 882, 89, (3, 12, 15)),
             frame, "ok", "exact"),
        ])
        sm._advance_from_confirmed_appraisal = Mock(return_value=True)
        sm._favorite_unresolved_snapshot = Mock()

        sm.start(expected_total=2)

        self.assertEqual(2, sm.count)
        self.assertEqual(2, sm.visited_count)
        self.assertEqual([0, 1], [row[2] for row in db.rows])
        self.assertEqual([10, 882], [row[0].cp for row in db.rows])
        sm._advance_from_confirmed_appraisal.assert_called_once_with()
        self.assertFalse(any(action[0] == "tap" for action in adb.actions))
        sm._favorite_unresolved_snapshot.assert_not_called()

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_ambiguous_position_is_favorited_before_counting_skip_and_advancing(self, _paddle):
        adb = _ADB()
        db = _DB()
        sm = IndexingStateMachine(adb, profile(), db)
        frame = adb.screencap()
        favorited_frame = adb.screencap()
        sm._acquire_validated_snapshot = Mock(side_effect=[
            (None, frame, "invalid", "hidden CP is ambiguous"),
            (accepted(), frame, "ok", "exact"),
        ])
        events = []
        def favorite(current):
            self.assertIs(frame, current)
            self.assertEqual(0, sm.visited_count)
            self.assertEqual(0, sm.skipped_count)
            self.assertEqual([], db.rows)
            events.append("favorite verified")
            return favorited_frame
        sm._favorite_unresolved_snapshot = Mock(side_effect=favorite)
        def advance():
            self.assertEqual(1, sm.visited_count)
            self.assertEqual(1, sm.skipped_count)
            self.assertIs(favorited_frame, sm._last_stable_image)
            events.append("advance")
            return True
        sm._advance_from_confirmed_appraisal = Mock(side_effect=advance)

        sm.start(expected_total=2)

        self.assertEqual(1, sm.count)
        self.assertEqual(2, sm.visited_count)
        self.assertEqual(1, sm.skipped_count)
        self.assertEqual(1, db.rows[0][2])
        self.assertFalse(any(action[0] == "tap" for action in adb.actions))
        sm._advance_from_confirmed_appraisal.assert_called_once_with()
        sm._favorite_unresolved_snapshot.assert_called_once_with(frame)
        self.assertEqual(["favorite verified", "advance"], events)

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_failed_unresolved_favorite_stops_without_counting_or_advancing(self, _paddle):
        adb, db = _ADB(), _DB()
        sm = IndexingStateMachine(adb, profile(), db)
        sm._acquire_validated_snapshot = Mock(return_value=(
            None, adb.screencap(), "invalid", "hidden CP is ambiguous",
        ))
        sm._favorite_unresolved_snapshot = Mock(side_effect=RuntimeError("star not confirmed"))
        sm._advance_from_confirmed_appraisal = Mock(return_value=True)

        with self.assertRaisesRegex(RuntimeError, "star not confirmed"):
            sm.start(expected_total=2)

        self.assertEqual((0, 0, 0), (sm.count, sm.visited_count, sm.skipped_count))
        self.assertEqual([], db.rows)
        self.assertEqual((sm.session_id, 0), db.completed)
        sm._advance_from_confirmed_appraisal.assert_not_called()

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_aborted_unresolved_favorite_does_not_count_or_advance(self, _paddle):
        adb, db = _ADB(), _DB()
        sm = IndexingStateMachine(adb, profile(), db)
        sm._acquire_validated_snapshot = Mock(return_value=(
            None, adb.screencap(), "invalid", "hidden CP is ambiguous",
        ))
        def abort(_frame):
            sm._abort = True
            return None
        sm._favorite_unresolved_snapshot = Mock(side_effect=abort)
        sm._advance_from_confirmed_appraisal = Mock(return_value=True)

        sm.start(expected_total=2)

        self.assertEqual((0, 0, 0), (sm.count, sm.visited_count, sm.skipped_count))
        self.assertEqual([], db.rows)
        sm._advance_from_confirmed_appraisal.assert_not_called()

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_lost_appraisal_raises_without_any_later_device_action(self, _paddle):
        adb = _ADB()
        db = _DB()
        sm = IndexingStateMachine(adb, profile(), db)
        sm._acquire_validated_snapshot = Mock(return_value=(
            None, None, "lost_appraisal_game_map", "lost appraisal game map"
        ))
        sm._advance_from_confirmed_appraisal = Mock(return_value=True)

        with self.assertRaisesRegex(RuntimeError, "lost appraisal game map"):
            sm.start(expected_total=2)

        self.assertEqual([], db.rows)
        self.assertEqual([], adb.actions)
        self.assertEqual((sm.session_id, 0), db.completed)
        sm._advance_from_confirmed_appraisal.assert_not_called()

    @patch("pokemgr.indexer.state_machine.human_delay", return_value=None)
    def test_a_transient_a_is_not_a_confirmed_next_identity(self, _delay):
        adb = _ADB()
        sm = IndexingStateMachine(adb, profile(), _DB())
        pokemon_a = Image.new("RGB", (968, 2376), "white")
        transient = Image.new("RGB", (968, 2376), "black")
        sm._fast_screencap = Mock(
            side_effect=[transient, pokemon_a, pokemon_a, pokemon_a,
                         pokemon_a, pokemon_a, pokemon_a]
        )
        sm.reader.are_bars_visible = Mock(return_value=True)
        sm.reader.appraisal_bars_stable = Mock(return_value=True)
        sm.nav.detect_screen = Mock(return_value="appraisal")

        frame, status = sm._wait_for_stable_appraisal(
            previous_accepted=pokemon_a,
            require_transition=True,
        )

        self.assertIsNone(frame)
        self.assertEqual("transition_returned_to_previous", status)

    def test_stable_observation_wait_counts_capture_transport_time(self):
        for transport_time, chosen_interval, remaining in (
            (0.3, 0.25, 0.0),
            (0.7, 0.15, 0.0),
            (0.08, 0.15, 0.07),
            (0.08, 0.25, 0.17),
        ):
            with self.subTest(transport=transport_time, interval=chosen_interval):
                adb = _ADB()
                sm = IndexingStateMachine(adb, profile(), _DB())
                first, second = adb.screencap(), adb.screencap()
                first.info["pokemgr_capture_started_at"] = 100.0
                second.info["pokemgr_capture_started_at"] = 101.0
                sm._fast_screencap = Mock(side_effect=[first, second])
                sm.reader.are_bars_visible = Mock(return_value=True)
                sm.reader.appraisal_bars_stable = Mock(return_value=True)
                sm.nav.detect_screen = Mock(return_value="appraisal")

                with patch("pokemgr.indexer.state_machine.time.monotonic",
                           return_value=100.0 + transport_time), \
                        patch("pokemgr.indexer.state_machine.random.uniform",
                              return_value=chosen_interval) as choose_interval, \
                        patch("pokemgr.config.STABLE_FRAME_INTERVAL", (0.15, 0.10)), \
                        patch("pokemgr.indexer.state_machine.human_delay") as delay:
                    frame, status = sm._wait_for_stable_appraisal()

                self.assertEqual("stable", status)
                self.assertIs(second, frame)
                self.assertIsNot(first, frame)
                self.assertEqual(2, sm._fast_screencap.call_count)
                choose_interval.assert_called_once_with(0.15, 0.25)
                delay.assert_called_once()
                self.assertAlmostEqual(remaining, delay.call_args.args[0])
                self.assertEqual(0.0, delay.call_args.args[1])
                self.assertEqual([first, second], [
                    call.args[0] for call in sm.reader.are_bars_visible.call_args_list
                ])

    @patch("pokemgr.config.STABLE_FRAME_INTERVAL", (0.15, 0.10))
    @patch("pokemgr.indexer.state_machine.human_delay")
    def test_stable_observation_without_capture_timestamp_keeps_full_wait(self, delay):
        adb = _ADB()
        sm = IndexingStateMachine(adb, profile(), _DB())
        first, second = adb.screencap(), adb.screencap()
        sm._fast_screencap = Mock(side_effect=[first, second])
        sm.reader.are_bars_visible = Mock(return_value=True)
        sm.reader.appraisal_bars_stable = Mock(return_value=True)
        sm.nav.detect_screen = Mock(return_value="appraisal")

        frame, status = sm._wait_for_stable_appraisal()

        self.assertEqual("stable", status)
        self.assertIs(second, frame)
        self.assertEqual(2, sm._fast_screencap.call_count)
        delay.assert_called_once_with(0.15, 0.10)

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_transition_return_to_previous_propagates_and_is_not_stored(
            self, _paddle):
        adb = _ADB()
        db = _DB()
        sm = IndexingStateMachine(adb, profile(), db)
        frame = adb.screencap()
        sm._acquire_validated_snapshot = Mock(side_effect=[
            (accepted(), frame, "ok", "exact"),
            (None, None, "transition_returned_to_previous",
             "transition returned to previous"),
        ])
        sm._advance_from_confirmed_appraisal = Mock(return_value=True)

        with self.assertRaisesRegex(
                RuntimeError, "could not confirm advance"):
            sm.start(expected_total=2)

        self.assertEqual(1, len(db.rows))
        self.assertEqual(10, db.rows[0][0].cp)
        self.assertEqual((sm.session_id, 1), db.completed)

    @patch("pokemgr.indexer.state_machine.time.sleep")
    @patch("pokemgr.indexer.state_machine.time.monotonic", return_value=100.0)
    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_resume_skip_delay_and_offset_do_not_consume_new_target(
            self, _paddle, _clock, sleep):
        adb = _ADB()
        db = _DB()
        sm = IndexingStateMachine(adb, profile(), db)
        frame = adb.screencap()
        frame.info["pokemgr_capture_finished_at"] = 100.0
        sm.skip_first_n = 2
        sm.skip_delay = 0.37
        sm._fast_screencap = Mock(return_value=frame)
        sm.nav.detect_screen = Mock(return_value="appraisal")
        sm._fast_swipe = Mock(return_value=True)
        sm._wait_for_stable_appraisal = Mock(side_effect=[
            (frame, "stable"),
            (frame, "stable"),
        ])
        sm._acquire_validated_snapshot = Mock(side_effect=[
            (accepted(), frame, "ok", "exact"),
            (accepted("Zorua (Hisuian)", 882, 89, (3, 12, 15)),
             frame, "ok", "exact"),
        ])
        sm._advance_from_confirmed_appraisal = Mock(return_value=True)

        sm.start(expected_total=2)

        self.assertEqual(2, sm.visited_count)
        self.assertEqual([2, 3], [row[2] for row in db.rows])
        self.assertEqual(2, sm._wait_for_stable_appraisal.call_count)
        # The second resume swipe reuses the just-confirmed frame, while
        # every swipe still waits for two fresh settled post-swipe frames.
        self.assertEqual(1, sm._fast_screencap.call_count)
        self.assertEqual(2, sm._acquire_validated_snapshot.call_count)
        self.assertEqual(2, sleep.call_count)
        sleep.assert_any_call(0.37)

    @patch("pokemgr.indexer.state_machine.time.sleep")
    @patch("pokemgr.indexer.state_machine.time.monotonic", return_value=100.0)
    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_resume_reuses_only_a_recent_finished_capture(self, _paddle, _clock, _sleep):
        for finished_at, expected_captures in (
            (None, 2),
            (99.74, 2),
            (99.75, 1),
            (100.0, 1),
        ):
            with self.subTest(finished_at=finished_at):
                adb, db = _ADB(), _DB()
                sm = IndexingStateMachine(adb, profile(), db)
                initial, settled, refreshed = (
                    adb.screencap(), adb.screencap(), adb.screencap()
                )
                # A recent start time must never substitute for a missing or
                # old finish time when deciding whether the result is fresh.
                settled.info["pokemgr_capture_started_at"] = 100.0
                if finished_at is not None:
                    settled.info["pokemgr_capture_finished_at"] = finished_at
                sm.skip_first_n = 2
                sm.skip_delay = 0.0
                sm._fast_screencap = Mock(side_effect=[initial, refreshed])
                sm.nav.detect_screen = Mock(return_value="appraisal")
                sm._fast_swipe = Mock(return_value=True)
                sm._wait_for_stable_appraisal = Mock(return_value=(settled, "stable"))
                sm._acquire_validated_snapshot = Mock(return_value=(
                    accepted(), settled, "ok", "exact",
                ))

                sm.start(expected_total=1)

                self.assertEqual(expected_captures, sm._fast_screencap.call_count)
                second_reference = sm._wait_for_stable_appraisal.call_args_list[1]
                self.assertIs(
                    settled if expected_captures == 1 else refreshed,
                    second_reference.kwargs["previous_accepted"],
                )
                self.assertEqual(2, sm._fast_swipe.call_count)
                self.assertEqual([2], [row[2] for row in db.rows])

    @patch("pokemgr.indexer.state_machine.time.monotonic", return_value=100.0)
    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_paused_resume_invalidates_settled_frame_before_next_swipe(self, _paddle, _clock):
        adb, db = _ADB(), _DB()
        sm = IndexingStateMachine(adb, profile(), db)
        initial, settled, refreshed = adb.screencap(), adb.screencap(), adb.screencap()
        settled.info["pokemgr_capture_finished_at"] = 100.0
        sm.skip_first_n = 2
        sm.skip_delay = 0.0
        events = []
        sm._fast_screencap = Mock(side_effect=[initial, refreshed])
        sm.nav.detect_screen = Mock(return_value="appraisal")

        def swipe():
            self.assertFalse(sm._paused, "resume must not swipe while paused")
            events.append("swipe")
            return True

        def settle(**_kwargs):
            if events == ["swipe"]:
                events.append("pause")
                sm.pause()
            return settled, "stable"

        def sleep(_duration):
            if sm._paused:
                self.assertEqual(["swipe", "pause"], events)
                self.assertEqual(1, sm._fast_screencap.call_count)
                events.append("resume")
                sm.resume()

        sm._fast_swipe = Mock(side_effect=swipe)
        sm._wait_for_stable_appraisal = Mock(side_effect=settle)
        sm._acquire_validated_snapshot = Mock(return_value=(
            accepted(), settled, "ok", "exact",
        ))

        with patch("pokemgr.indexer.state_machine.time.sleep", side_effect=sleep):
            sm.start(expected_total=1)

        self.assertEqual(["swipe", "pause", "resume", "swipe"], events)
        self.assertEqual(2, sm._fast_screencap.call_count)
        self.assertIs(
            refreshed,
            sm._wait_for_stable_appraisal.call_args_list[1].kwargs["previous_accepted"],
        )

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_pause_during_resume_capture_or_detection_waits_and_recaptures(self, _paddle):
        for pause_during in ("capture", "detection"):
            with self.subTest(pause_during=pause_during):
                adb, db = _ADB(), _DB()
                sm = IndexingStateMachine(adb, profile(), db)
                stale, refreshed, settled = adb.screencap(), adb.screencap(), adb.screencap()
                sm.skip_first_n = 1
                sm.skip_delay = 0.0
                events = []

                def capture():
                    events.append("capture")
                    if events.count("capture") == 1:
                        if pause_during == "capture":
                            events.append("pause")
                            sm.pause()
                        return stale
                    return refreshed

                def detect(frame):
                    if frame is stale and pause_during == "detection":
                        events.append("pause")
                        sm.pause()
                    return "appraisal"

                def sleep(_duration):
                    if sm._paused:
                        sm._fast_swipe.assert_not_called()
                        self.assertEqual(1, sm._fast_screencap.call_count)
                        events.append("resume")
                        sm.resume()

                def swipe():
                    self.assertFalse(sm._paused, "resume must not swipe while paused")
                    self.assertIn("resume", events)
                    self.assertIs(refreshed, sm._last_stable_image)
                    events.append("swipe")
                    return True

                sm._fast_screencap = Mock(side_effect=capture)
                sm.nav.detect_screen = Mock(side_effect=detect)
                sm._fast_swipe = Mock(side_effect=swipe)
                sm._wait_for_stable_appraisal = Mock(return_value=(settled, "stable"))
                sm._acquire_validated_snapshot = Mock(return_value=(
                    accepted(), settled, "ok", "exact",
                ))

                with patch("pokemgr.indexer.state_machine.time.sleep", side_effect=sleep):
                    sm.start(expected_total=1)

                self.assertEqual(["capture", "pause", "resume", "capture", "swipe"], events)
                self.assertEqual([1], [row[2] for row in db.rows])
                sm._fast_swipe.assert_called_once_with()
                sm._wait_for_stable_appraisal.assert_called_once_with(
                    previous_accepted=refreshed,
                    require_transition=True,
                )

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_abort_while_resume_capture_is_paused_sends_no_swipe(self, _paddle):
        adb, db = _ADB(), _DB()
        sm = IndexingStateMachine(adb, profile(), db)
        sm.skip_first_n = 1

        def capture():
            sm.pause()
            return adb.screencap()

        sm._fast_screencap = Mock(side_effect=capture)
        sm.nav.detect_screen = Mock(return_value="appraisal")
        sm._fast_swipe = Mock(return_value=True)
        sm._wait_for_stable_appraisal = Mock(return_value=(adb.screencap(), "stable"))
        sm._acquire_validated_snapshot = Mock()
        with patch("pokemgr.indexer.state_machine.time.sleep", side_effect=lambda _: sm.abort()):
            sm.start(expected_total=1)

        sm._fast_screencap.assert_called_once_with()
        sm._fast_swipe.assert_not_called()
        sm._wait_for_stable_appraisal.assert_not_called()
        sm._acquire_validated_snapshot.assert_not_called()
        self.assertEqual([], db.rows)
        self.assertEqual((sm.session_id, 0), db.completed)

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_resume_target_mismatch_raises_before_commit(self, _paddle):
        adb = _ADB()
        db = _DB()
        sm = IndexingStateMachine(adb, profile(), db)
        frame = adb.screencap()
        sm.skip_first_n = 1
        sm.resume_target_species = "Zubat"
        sm.resume_target_cp = 10
        sm._fast_screencap = Mock(return_value=frame)
        sm.nav.detect_screen = Mock(return_value="appraisal")
        sm._fast_swipe = Mock(return_value=True)
        sm._wait_for_stable_appraisal = Mock(return_value=(frame, "stable"))
        sm._acquire_validated_snapshot = Mock(return_value=(
            accepted("Zorua (Hisuian)", 882, 89, (3, 12, 15)),
            frame,
            "ok",
            "exact",
        ))

        with self.assertRaisesRegex(RuntimeError, "Resume target mismatch"):
            sm.start(expected_total=1)

        self.assertEqual([], db.rows)
        self.assertEqual((sm.session_id, 0), db.completed)

    @patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None)
    def test_advance_failure_raises_after_safe_partial_commit(self, _paddle):
        adb = _ADB()
        db = _DB()
        sm = IndexingStateMachine(adb, profile(), db)
        frame = adb.screencap()
        sm._acquire_validated_snapshot = Mock(return_value=(
            accepted(), frame, "ok", "exact"
        ))
        sm._advance_from_confirmed_appraisal = Mock(return_value=False)

        with self.assertRaisesRegex(RuntimeError, "could not verify and advance"):
            sm.start(expected_total=2)

        self.assertEqual(1, len(db.rows))
        self.assertEqual((sm.session_id, 1), db.completed)


if __name__ == "__main__":
    unittest.main()
