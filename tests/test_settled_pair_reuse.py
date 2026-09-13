"""Calculated CP may reuse two settled captures, never one duplicated read."""

# TRACEWEAVER: file-role=settled-pair-reuse-tests; req=REQ-SCAN-002; trace=TRACE-SCAN-002; verifies=VER-SCAN-001

from dataclasses import replace
import unittest
from unittest.mock import Mock, call, patch

from PIL import Image

from pokemgr.calibration.profile import CalibrationProfile
from pokemgr.calibration.regions import ScreenRegions
from pokemgr.indexer.snapshot import AppraisalSnapshot, SnapshotDecision, validate_snapshot
from pokemgr.indexer.state_machine import IndexingStateMachine
from tests.test_stable_scan_loop import _ADB, _DB, accepted, profile


SPECIES = {
    "zacian_crowned_sword": {
        "name": "Zacian (Crowned Sword)",
        "base_atk": 332, "base_def": 240, "base_sta": 192,
    },
}


def snapshot(**changes):
    values = dict(
        display_name="Zacian", detected_species="Zacian", caught_species="Zacian",
        cp=-1, hp=174, atk=11, def_=10, sta=14,
        shiny=False, shadow=False, favorited=True, lucky=False, gender="none",
        weight_tag="", height_tag="", is_dynamax=False,
        detail_confidence=0.95, appraisal_confidence=0.95,
    )
    return AppraisalSnapshot(**(values | changes))


def frame(read=None, *, screen="appraisal", bars=True):
    image = Image.new("RGB", (96, 237), "white")
    image.info.update(snapshot=read or snapshot(), screen=screen, bars=bars)
    return image


class SettledPairReuseTests(unittest.TestCase):
    def setUp(self):
        species_patch = patch(
            "pokemgr.pvp.resolver._default_species_map", return_value=SPECIES,
        )
        species_patch.start()
        self.addCleanup(species_patch.stop)
        delay_patch = patch("pokemgr.indexer.state_machine.human_delay")
        delay_patch.start()
        self.addCleanup(delay_patch.stop)
        regions = ScreenRegions.default_for_resolution(968, 2376, density=420)
        profile = CalibrationProfile(
            device_model="test", serial="serial", resolution="968x2376",
            density=420, regions=regions,
        )
        self.adb = Mock()
        self.sm = IndexingStateMachine(self.adb, profile, Mock())
        self.sm.use_calculated_cp = True
        self.sm.nav.detect_screen = Mock(side_effect=lambda image: image.info["screen"])
        self.sm.reader.are_bars_visible = Mock(side_effect=lambda image: image.info["bars"])
        self.sm.reader.appraisal_bars_stable = Mock(return_value=True)
        self.sm.reader.read_cp = Mock(return_value=(-1, 0.0))
        self.sm._read_appraisal_snapshot = Mock(side_effect=self.read_snapshot)

    @staticmethod
    def read_snapshot(image):
        read = image.info["snapshot"]
        return (
            read.as_detail() | {"snapshot_read_complete": read.read_complete},
            read.as_appraisal(),
        )

    def acquire(self, captures, *, previous_key=None, transition=False):
        self.sm._fast_screencap = Mock(side_effect=captures)
        self.sm._last_validated_identity_key = previous_key
        return self.sm._acquire_validated_snapshot(
            previous_accepted=frame() if transition else None,
            require_transition=transition,
        )

    def assert_no_input(self):
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()

    def test_two_settled_captures_are_independently_read_and_keep_newest_record(self):
        older = frame(snapshot(favorited=False))
        newest = frame()
        self.sm._recover_cp_with_model_taps = Mock()

        decision, accepted_frame, status, _reason = self.acquire([older, newest])

        self.assertEqual("ok", status)
        self.assertEqual("calculated", decision.cp_source)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertTrue(decision.snapshot.favorited)
        self.assertIs(newest, accepted_frame)
        self.assertEqual(2, self.sm._fast_screencap.call_count)
        self.assertEqual([call(newest), call(older)], self.sm._read_appraisal_snapshot.call_args_list)
        self.sm._recover_cp_with_model_taps.assert_not_called()
        self.sm.reader.read_cp.assert_not_called()
        self.assertIsNone(self.sm._settled_frame_pair)
        self.assert_no_input()

    def test_pair_also_confirms_an_unobserved_transition_without_another_capture(self):
        older, newest = frame(), frame()
        prior_key = ("Zacian (Crowned Sword)", 5500, 174, 11, 10, 14)

        decision, accepted_frame, status, _reason = self.acquire(
            [older, newest], previous_key=prior_key, transition=True,
        )

        self.assertEqual("ok", status)
        self.assertNotEqual(prior_key, decision.snapshot.identity_key)
        self.assertIs(newest, accepted_frame)
        self.assertEqual(2, self.sm._fast_screencap.call_count)
        self.assertEqual(2, self.sm._read_appraisal_snapshot.call_count)
        self.assert_no_input()

    def test_pair_cannot_prove_transition_to_same_or_missing_prior_identity(self):
        accepted_key = validate_snapshot(snapshot(), True).snapshot.identity_key
        for prior_key, expected_status in (
            (accepted_key, "transition_returned_to_previous"),
            (None, "transition_identity_incomplete"),
        ):
            with self.subTest(prior_key=prior_key):
                decision, _frame, status, _reason = self.acquire(
                    [frame(), frame()], previous_key=prior_key, transition=True,
                )
                self.assertIsNone(decision)
                self.assertEqual(expected_status, status)
                self.assertEqual(2, self.sm._fast_screencap.call_count)
                self.assert_no_input()

    def test_incomplete_or_different_older_identity_falls_back_to_fresh_confirmation(self):
        for changes in (
            {"read_complete": False}, {"hp": -1}, {"hp": 173}, {"atk": 12},
            {"caught_species": ""}, {"display_name": "Different"},
        ):
            with self.subTest(changes=changes):
                older, newest, fresh = frame(snapshot(**changes)), frame(), frame()
                self.sm._read_appraisal_snapshot.reset_mock()

                decision, accepted_frame, status, _reason = self.acquire([older, newest, fresh])

                self.assertEqual("ok", status)
                self.assertEqual(5561, decision.snapshot.cp)
                self.assertIs(fresh, accepted_frame)
                self.assertEqual(3, self.sm._fast_screencap.call_count)
                self.assertEqual(
                    [call(newest), call(older), call(fresh)],
                    self.sm._read_appraisal_snapshot.call_args_list,
                )
                self.assert_no_input()

    def test_duplicating_one_image_does_not_count_as_two_independent_captures(self):
        newest, fresh = frame(), frame()

        decision, accepted_frame, status, _reason = self.acquire([newest, newest, fresh])

        self.assertEqual("ok", status)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertIs(fresh, accepted_frame)
        self.assertEqual([call(newest), call(fresh)], self.sm._read_appraisal_snapshot.call_args_list)
        self.assertEqual(3, self.sm._fast_screencap.call_count)

    def test_pair_requires_its_newest_member_and_both_confirmed_appraisals(self):
        for invalid in ("older_screen", "newer_screen", "older_bars", "newer_bars", "other_newest"):
            with self.subTest(invalid=invalid):
                older, newest, fresh = frame(), frame(), frame()
                pair_newest = newest
                if invalid == "older_screen":
                    older.info["screen"] = "detail"
                elif invalid == "newer_screen":
                    newest.info["screen"] = "unknown"
                elif invalid == "older_bars":
                    older.info["bars"] = False
                elif invalid == "newer_bars":
                    newest.info["bars"] = False
                else:
                    pair_newest = frame()

                def settled(**_kwargs):
                    self.sm._settled_frame_pair = (older, pair_newest)
                    return newest, "stable"

                with patch.object(self.sm, "_wait_for_stable_appraisal", side_effect=settled):
                    decision, accepted_frame, status, _reason = self.acquire([fresh])

                self.assertEqual("ok", status)
                self.assertEqual(5561, decision.snapshot.cp)
                self.assertIs(fresh, accepted_frame)
                self.assertEqual(1, self.sm._fast_screencap.call_count)
                self.assert_no_input()

    def test_mismatched_exact_confirmation_does_not_accept_pair(self):
        older, newest, fresh = frame(), frame(), frame()
        real_validate = validate_snapshot

        def inconsistent(read, allow_calculated_cp):
            result = real_validate(read, allow_calculated_cp)
            if read.favorited is False and result.accepted:
                return replace(result, snapshot=replace(result.snapshot, cp=5594))
            return result

        older.info["snapshot"] = snapshot(favorited=False)
        with patch("pokemgr.indexer.snapshot.validate_snapshot", side_effect=inconsistent):
            decision, accepted_frame, status, _reason = self.acquire([older, newest, fresh])

        self.assertEqual("ok", status)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertIs(fresh, accepted_frame)
        self.assertEqual(3, self.sm._fast_screencap.call_count)

    def test_abort_during_older_read_discards_pair_without_acceptance_or_input(self):
        older, newest = frame(), frame()

        def abort_older(image):
            if image is older:
                self.sm.abort()
            return self.read_snapshot(image)

        self.sm._read_appraisal_snapshot.side_effect = abort_older
        decision, _frame, status, _reason = self.acquire([older, newest])

        self.assertIsNone(decision)
        self.assertEqual("aborted", status)
        self.assertIsNone(self.sm._settled_frame_pair)
        self.assert_no_input()

    def test_pause_and_resume_during_read_requires_a_new_confirmation(self):
        for interrupted_read in ("older", "newest"):
            with self.subTest(interrupted_read=interrupted_read):
                older, newest, fresh = frame(), frame(), frame()
                interrupted = older if interrupted_read == "older" else newest

                def pause_read(image):
                    if image is interrupted:
                        self.sm.pause()
                        self.sm.resume()
                    return self.read_snapshot(image)

                self.sm._read_appraisal_snapshot.side_effect = pause_read
                decision, accepted_frame, status, _reason = self.acquire([older, newest, fresh])

                self.assertEqual("ok", status)
                self.assertEqual(5561, decision.snapshot.cp)
                self.assertIs(fresh, accepted_frame)
                self.assertEqual(3, self.sm._fast_screencap.call_count)
                self.assertIsNone(self.sm._settled_frame_pair)
                self.assert_no_input()

    def test_pause_and_resume_during_settling_cannot_publish_a_reusable_pair(self):
        older, newest, fresh = frame(), frame(), frame()
        captures = iter([older, newest, fresh])

        def capture():
            image = next(captures)
            if image is newest:
                self.sm.pause()
                self.sm.resume()
            return image

        self.sm._fast_screencap = Mock(side_effect=capture)
        decision, accepted_frame, status, _reason = self.sm._acquire_validated_snapshot()

        self.assertEqual("ok", status)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertIs(fresh, accepted_frame)
        self.assertEqual(3, self.sm._fast_screencap.call_count)
        self.assertEqual([call(newest), call(fresh)], self.sm._read_appraisal_snapshot.call_args_list)
        self.assertIsNone(self.sm._settled_frame_pair)
        self.assert_no_input()

    def test_acquisition_retry_discards_previous_pair(self):
        invalid = snapshot(read_complete=False)
        older, invalid_frame, valid_frame = frame(invalid), frame(invalid), frame()
        waits = 0

        def settle(**_kwargs):
            nonlocal waits
            waits += 1
            self.assertIsNone(self.sm._settled_frame_pair)
            if waits == 1:
                self.sm._settled_frame_pair = (older, invalid_frame)
                return invalid_frame, "stable"
            self.sm._settled_frame_pair = (frame(), valid_frame)
            return valid_frame, "stable"

        self.sm._wait_for_stable_appraisal = Mock(side_effect=settle)
        self.sm._fast_screencap = Mock()
        decision, accepted_frame, status, _reason = self.sm._acquire_validated_snapshot()

        self.assertEqual("ok", status)
        self.assertEqual(5561, decision.snapshot.cp)
        self.assertIs(valid_frame, accepted_frame)
        self.assertEqual(2, waits)
        self.sm._fast_screencap.assert_not_called()
        self.assertIsNone(self.sm._settled_frame_pair)
        self.assert_no_input()

    def test_recovery_cannot_reuse_before_recovery_pair_for_transition_confirmation(self):
        ambiguous = snapshot(cp=561, hp=173, atk=13, def_=13)
        older, newest, recovered_frame, confirmation = [frame(ambiguous) for _ in range(4)]
        recovered = replace(
            validate_snapshot(replace(ambiguous, cp=5561), False),
            cp_source="screen_after_animation",
        )

        def recovery(*_args, **_kwargs):
            # A nested settling operation can publish its own pair; acquisition
            # must clear it when recovery returns instead of retaining old proof.
            self.sm._settled_frame_pair = (newest, recovered_frame)
            return recovered, recovered_frame

        self.sm._recover_cp_with_model_taps = Mock(side_effect=recovery)
        prior_key = ("Zacian (Crowned Sword)", 5500, 173, 13, 13, 14)
        decision, accepted_frame, status, _reason = self.acquire(
            [older, newest, confirmation], previous_key=prior_key, transition=True,
        )

        self.assertEqual("ok", status)
        self.assertEqual("screen_after_animation", decision.cp_source)
        self.assertIs(confirmation, accepted_frame)
        self.assertEqual(3, self.sm._fast_screencap.call_count)
        self.assertIsNone(self.sm._settled_frame_pair)
        self.assertEqual([call(newest), call(confirmation)], self.sm._read_appraisal_snapshot.call_args_list)

    def test_wait_discards_previous_pair_even_when_new_wait_fails(self):
        self.sm._settled_frame_pair = (frame(), frame())

        result, status = self.sm._wait_for_stable_appraisal(require_transition=True)

        self.assertIsNone(result)
        self.assertEqual("transition_reference_missing", status)
        self.assertIsNone(self.sm._settled_frame_pair)
        self.adb.screencap.assert_not_called()

    def test_normal_advance_still_captures_a_fresh_screen(self):
        cached, fresh = frame(), frame(screen="unknown")
        self.sm._last_stable_image = cached
        self.sm._settled_frame_pair = (frame(), cached)
        self.sm._fast_screencap = Mock(return_value=fresh)

        self.assertFalse(self.sm._advance_from_confirmed_appraisal())

        self.sm._fast_screencap.assert_called_once_with()
        self.assert_no_input()


def observation(sequence, decision):
    image = Image.new("RGB", (96, 237), "white")
    image.info.update(
        pokemgr_stream_session="a" * 32,
        pokemgr_stream_sequence=sequence,
        pokemgr_stream_pts_us=sequence * 200_000,
        pokemgr_source_clock_generation=1,
        pokemgr_capture_started_at=sequence * .2,
        pokemgr_capture_finished_at=sequence * .2 + .01,
        decision=decision, screen="appraisal", bars=True,
    )
    return image


class VisibleSettledPairReuseTests(unittest.TestCase):
    def setUp(self):
        self.adb, self.db = _ADB(), _DB()
        self.sm = IndexingStateMachine(self.adb, profile(), self.db)
        self.previous_decision = accepted("Spearow", 579, 90, (14, 14, 13))
        self.decision = accepted("Spearow", 507, 85, (13, 13, 14))
        self.previous = observation(1, self.previous_decision)
        self.older = observation(2, self.decision)
        self.newest = observation(3, self.decision)
        self.fresh = observation(4, self.decision)
        self.pair = (self.older, self.newest)
        self.sm._last_validated_identity_key = self.previous_decision.snapshot.identity_key
        self.sm.nav.detect_screen = Mock(side_effect=lambda image: image.info["screen"])
        self.sm.reader.are_bars_visible = Mock(side_effect=lambda image: image.info["bars"])
        self.sm.reader.appraisal_bars_stable = Mock(return_value=True)
        self.sm._read_appraisal_snapshot = Mock(side_effect=self.read_snapshot)
        self.sm._validate_appraisal_snapshot = Mock(
            side_effect=lambda _snapshot, image: image.info["decision"],
        )
        self.sm._fast_screencap = Mock(return_value=self.fresh)
        self.sm._recover_cp_with_model_taps = Mock(side_effect=AssertionError("Unexpected recovery"))
        self.delay = self.enterContext(patch("pokemgr.indexer.state_machine.human_delay"))

    def read_snapshot(self, image):
        snapshot = image.info["decision"].snapshot or self.decision.snapshot
        return snapshot.as_detail(), snapshot.as_appraisal()

    def acquire(self, status="stable_transition_unobserved"):
        def settle(**_kwargs):
            self.sm._settled_frame_pair = self.pair
            return self.newest, status

        self.sm._wait_for_stable_appraisal = Mock(side_effect=settle)
        result = self.sm._acquire_validated_snapshot(
            previous_accepted=self.previous, require_transition=True,
        )
        self.assertEqual([], self.adb.actions)
        self.assertEqual([], self.db.rows)
        return result

    def assert_fresh_confirmation(self):
        decision, frame, kind, _reason = self.acquire()
        self.assertEqual("ok", kind)
        self.assertIs(decision, self.decision)
        self.assertIs(frame, self.fresh)
        self.sm._fast_screencap.assert_called_once_with()
        self.delay.assert_called_once_with(.2, .2)

    def test_exact_visible_pair_reuses_both_frames_without_wait_or_capture(self):
        for source in ("screen", "screen_alternate_ocr"):
            with self.subTest(source=source):
                self.decision = replace(self.decision, cp_source=source)
                self.newest.info["decision"] = self.decision
                self.sm._read_appraisal_snapshot.reset_mock()
                decision, frame, kind, _reason = self.acquire()
                self.assertEqual("ok", kind)
                self.assertIs(decision, self.decision)
                self.assertIs(frame, self.newest)
                self.assertEqual([self.newest, self.older], [
                    call.args[0] for call in self.sm._read_appraisal_snapshot.call_args_list
                ])
                self.sm._fast_screencap.assert_not_called()
                self.sm._recover_cp_with_model_taps.assert_not_called()
                self.delay.assert_not_called()

    def test_older_unique_calculation_can_confirm_same_visible_complete_tuple(self):
        self.older.info["decision"] = replace(self.decision, cp_source="calculated")
        decision, frame, kind, _reason = self.acquire()
        self.assertEqual("ok", kind)
        self.assertIs(decision, self.decision)
        self.assertIs(frame, self.newest)
        self.assertEqual(2, self.sm._read_appraisal_snapshot.call_count)
        self.sm._fast_screencap.assert_not_called()

    def test_conflicting_or_incomplete_older_identity_keeps_fresh_confirmation(self):
        cases = [
            {"detected_species": "Pidgey"}, {"cp": 506}, {"hp": 84},
            {"atk": 12}, {"def_": 12}, {"sta": 13}, {"hp": -1},
        ]
        for fields in cases:
            with self.subTest(fields=fields):
                self.sm._fast_screencap.reset_mock()
                self.delay.reset_mock()
                self.older.info["decision"] = replace(
                    self.decision, snapshot=replace(self.decision.snapshot, **fields),
                )
                self.assert_fresh_confirmation()

    def test_rejected_older_read_keeps_fresh_confirmation(self):
        self.older.info["decision"] = SnapshotDecision(False, "snapshot OCR did not finish")
        self.assert_fresh_confirmation()
        self.assertEqual(3, self.sm._read_appraisal_snapshot.call_count)

    def test_missing_misowned_repeated_and_copied_pair_keeps_fresh_confirmation(self):
        for pair in (None, (), (self.older,), (self.older, self.fresh),
                     (self.newest, self.newest), (self.newest.copy(), self.newest)):
            with self.subTest(pair_size=len(pair) if pair is not None else None):
                self.pair = pair
                self.sm._fast_screencap.reset_mock()
                self.delay.reset_mock()
                self.assert_fresh_confirmation()

    def test_changed_or_reordered_source_evidence_keeps_fresh_confirmation(self):
        cases = {
            "pokemgr_stream_session": ("b" * 32, ""),
            "pokemgr_stream_sequence": (3, 4, 0),
            "pokemgr_stream_pts_us": (600_000, 800_000, 0),
            "pokemgr_source_clock_generation": (2, 0),
        }
        for field, values in cases.items():
            original = self.older.info[field]
            for value in values:
                with self.subTest(field=field, value=value):
                    self.older.info[field] = value
                    self.sm._fast_screencap.reset_mock()
                    self.delay.reset_mock()
                    self.assert_fresh_confirmation()
            self.older.info[field] = original

    def test_legacy_pair_requires_independent_capture_times(self):
        # This case isolates legacy receipt validity; the fresh confirmation
        # must use the same capture source, rather than an implicit JPEG→stream switch.
        for image in (*self.pair, self.fresh):
            for field in tuple(image.info):
                if field.startswith("pokemgr_stream_") or field == "pokemgr_source_clock_generation":
                    del image.info[field]
        decision, frame, kind, _reason = self.acquire()
        self.assertEqual("ok", kind)
        self.assertIs(frame, self.newest)
        self.sm._fast_screencap.assert_not_called()

        for start, finish in ((None, None), (.7, .71), (.4, self.newest.info["pokemgr_capture_started_at"]), (.4, .3), (float("nan"), .41)):
            with self.subTest(start=start, finish=finish):
                self.older.info["pokemgr_capture_started_at"] = start
                self.older.info["pokemgr_capture_finished_at"] = finish
                self.sm._fast_screencap.reset_mock()
                self.delay.reset_mock()
                self.assert_fresh_confirmation()

    def test_lost_appraisal_or_bars_in_pair_keeps_fresh_confirmation(self):
        for image in self.pair:
            for field, invalid in (("screen", "detail"), ("bars", False)):
                with self.subTest(older=image is self.older, field=field):
                    original = image.info[field]
                    image.info[field] = invalid
                    self.sm._fast_screencap.reset_mock()
                    self.delay.reset_mock()
                    self.assert_fresh_confirmation()
                    image.info[field] = original

    def test_changed_static_pixels_or_moving_bars_keep_fresh_confirmation(self):
        self.older.paste("black", (0, 0, *self.older.size))
        self.assert_fresh_confirmation()
        self.older.paste("white", (0, 0, *self.older.size))
        self.sm._fast_screencap.reset_mock()
        self.delay.reset_mock()
        self.sm.reader.appraisal_bars_stable.return_value = False
        self.assert_fresh_confirmation()

    def test_pause_or_abort_during_pair_read_or_validation_discards_proof(self):
        for stage in ("read", "validate"):
            for action in ("pause", "pause_resume", "abort"):
                with self.subTest(stage=stage, action=action):
                    self.setUp()
                    def interrupt(image):
                        if image is self.older:
                            if action == "abort":
                                self.sm.abort()
                            else:
                                self.sm.pause()
                                if action == "pause_resume":
                                    self.sm.resume()

                    if stage == "read":
                        def read(image):
                            interrupt(image)
                            return self.read_snapshot(image)
                        self.sm._read_appraisal_snapshot.side_effect = read
                    else:
                        def validate(_snapshot, image):
                            interrupt(image)
                            return image.info["decision"]
                        self.sm._validate_appraisal_snapshot.side_effect = validate
                    decision, _frame, kind, _reason = self.acquire()
                    self.assertIsNone(decision)
                    self.assertEqual("aborted" if action == "abort" else "reacquire", kind)
                    self.sm._fast_screencap.assert_not_called()
                    self.sm._recover_cp_with_model_taps.assert_not_called()
                    self.delay.assert_not_called()

    def test_same_tuple_retains_specimen_and_failed_transition_handling(self):
        self.sm._last_validated_identity_key = self.decision.snapshot.identity_key
        self.sm._same_stats_specimen_advanced = Mock(return_value=False)
        decision, _frame, kind, _reason = self.acquire()
        self.assertIsNone(decision)
        self.assertEqual("transition_returned_to_previous", kind)
        self.sm._same_stats_specimen_advanced.assert_called_once()
        self.sm._fast_screencap.assert_called_once()

    def test_pause_during_bar_check_starts_no_older_read(self):
        def paused_bars(*_images):
            self.sm.pause()
            self.sm.resume()
            return True

        self.sm.reader.appraisal_bars_stable.side_effect = paused_bars
        decision, _frame, kind, _reason = self.acquire()
        self.assertIsNone(decision)
        self.assertEqual("reacquire", kind)
        self.assertEqual([self.newest], [
            read.args[0] for read in self.sm._read_appraisal_snapshot.call_args_list
        ])
        self.sm._fast_screencap.assert_not_called()
        self.delay.assert_not_called()

    def test_recovery_input_discards_pair_and_keeps_post_input_confirmation(self):
        self.sm._validate_appraisal_snapshot.side_effect = [
            SnapshotDecision(False, "CP unresolved"), self.decision,
        ]
        recovered = replace(self.decision, cp_source="screen_after_animation")
        self.sm._recover_cp_with_model_taps.side_effect = None
        self.sm._recover_cp_with_model_taps.return_value = recovered, self.newest
        self.sm._apply_animation_cp = Mock(return_value=recovered)
        decision, frame, kind, _reason = self.acquire()
        self.assertEqual("ok", kind)
        self.assertIs(frame, self.fresh)
        self.assertEqual("screen_after_animation", decision.cp_source)
        self.sm._fast_screencap.assert_called_once()
        self.assertEqual([self.newest, self.fresh], [
            call.args[0] for call in self.sm._read_appraisal_snapshot.call_args_list
        ])

    def test_calculated_path_and_observed_transition_do_not_use_visible_helper(self):
        self.sm._visible_cp_confirmed_by_pair = Mock(side_effect=AssertionError("Unexpected visible pair read"))
        self.sm._calculated_cp_confirmed_by_pair = Mock(return_value=True)
        self.newest.info["decision"] = replace(self.decision, cp_source="calculated")
        decision, frame, kind, _reason = self.acquire()
        self.assertEqual("ok", kind)
        self.assertIs(frame, self.newest)
        self.sm._fast_screencap.assert_not_called()
        self.sm._calculated_cp_confirmed_by_pair.return_value = False
        self.newest.info["decision"] = self.decision
        decision, frame, kind, _reason = self.acquire(status="stable")
        self.assertEqual("ok", kind)
        self.assertIs(frame, self.newest)
        self.sm._visible_cp_confirmed_by_pair.assert_not_called()


if __name__ == "__main__":
    unittest.main()
