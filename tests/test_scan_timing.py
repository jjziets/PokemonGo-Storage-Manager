"""Optional timing must preserve behavior and correlate existing worker spans."""

import io
import json
import logging
import os
from subprocess import CompletedProcess
import threading
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from pokemgr import timing


class EventHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.events = []

    def emit(self, record):
        message = record.getMessage()
        if message.startswith("SCAN_TIMING "):
            self.events.append(json.loads(message[len("SCAN_TIMING "):]))


class ScanTimingTests(unittest.TestCase):
    def setUp(self):
        enabled = patch.object(timing, "ENABLED", True)
        enabled.start()
        self.addCleanup(enabled.stop)
        self.handler = EventHandler()
        logger = timing.log
        old_level, old_propagate = logger.level, logger.propagate
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(self.handler)
        self.addCleanup(logger.removeHandler, self.handler)
        self.addCleanup(logger.setLevel, old_level)
        self.addCleanup(setattr, logger, "propagate", old_propagate)

    def events(self, phase):
        return [event for event in self.handler.events if event["phase"] == phase]

    def test_disabled_decorator_and_worker_binding_return_original_callable(self):
        def operation(value):
            return value

        with patch.object(timing, "ENABLED", False), \
                patch.object(timing, "monotonic", side_effect=AssertionError("clock used")), \
                patch.object(timing, "copy_context", side_effect=AssertionError("context copied")):
            self.assertIs(operation, timing.timed("disabled")(operation))
            self.assertIs(operation, timing.bind_context(operation))
            with timing.span("disabled", session_id="ignored", position=1):
                self.assertEqual(7, operation(7))

        self.assertEqual([], self.handler.events)

    def test_disabled_span_preserves_the_original_exception(self):
        error = RuntimeError("original error")
        with patch.object(timing, "ENABLED", False):
            with self.assertRaises(RuntimeError) as raised:
                with timing.span("disabled"):
                    raise error
        self.assertIs(error, raised.exception)
        self.assertEqual([], self.handler.events)

    def test_nested_spans_record_monotonic_intervals_and_inherit_context(self):
        with patch.object(timing, "monotonic", side_effect=[10.0, 12.0, 13.0, 18.0]):
            with timing.span("outer", session_id="session", position=85):
                with timing.span("inner"):
                    pass

        inner, outer = self.handler.events
        self.assertEqual((12.0, 1.0), (inner["start_s"], inner["duration_s"]))
        self.assertEqual((10.0, 8.0), (outer["start_s"], outer["duration_s"]))
        self.assertEqual(outer["span_id"], inner["parent_id"])
        self.assertIsNone(outer["parent_id"])
        self.assertEqual("session", inner["session_id"])
        self.assertEqual(85, inner["position"])
        self.assertEqual(outer["thread_id"], inner["thread_id"])
        self.assertNotEqual(outer["span_id"], inner["span_id"])
        self.assertEqual("ok", inner["status"])

    def test_decorator_preserves_result_and_never_logs_arguments_or_result(self):
        private_result = object()

        @timing.timed("operation")
        def operation(_private_argument):
            return private_result

        self.assertIs(private_result, operation("private argument"))
        event = self.events("operation")[0]
        self.assertEqual({
            "phase", "span_id", "parent_id", "start_s", "duration_s",
            "thread_id", "thread_name", "status",
        }, set(event))
        self.assertNotIn("private", json.dumps(event))
        self.assertEqual("operation", operation.__name__)

    def test_exception_is_rethrown_unchanged_without_logging_its_message(self):
        error = ValueError("private error detail")

        @timing.timed("failure")
        def fail():
            raise error

        with self.assertRaises(ValueError) as raised:
            fail()
        self.assertIs(error, raised.exception)
        event = self.events("failure")[0]
        self.assertEqual("error", event["status"])
        self.assertEqual("ValueError", event["exception_type"])
        self.assertNotIn("private", json.dumps(event))
        with timing.span("after_failure"):
            pass
        self.assertIsNone(self.events("after_failure")[0]["parent_id"])
        self.assertNotIn("session_id", self.events("after_failure")[0])

    def test_broken_logging_handler_does_not_change_return_or_exception(self):
        error = RuntimeError("scan failure")
        with patch.object(timing.log, "info", side_effect=OSError("logger failure")):
            with timing.span("success"):
                result = 123
            self.assertEqual(123, result)
            with self.assertRaises(RuntimeError) as raised:
                with timing.span("failure"):
                    raise error
            self.assertIs(error, raised.exception)

    def test_parallel_workers_have_distinct_spans_with_shared_parent_and_context(self):
        rendezvous = threading.Barrier(3)

        def worker():
            with timing.span("worker"):
                rendezvous.wait(timeout=3)

        with timing.span("acquire", session_id="parallel", position=12):
            threads = [threading.Thread(target=timing.bind_context(worker)) for _ in range(2)]
            for thread in threads:
                thread.start()
            rendezvous.wait(timeout=3)
            for thread in threads:
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())

        outer = self.events("acquire")[0]
        first, second = self.events("worker")
        for event in (first, second):
            self.assertEqual(outer["span_id"], event["parent_id"])
            self.assertEqual("parallel", event["session_id"])
            self.assertEqual(12, event["position"])
            self.assertNotEqual(outer["thread_id"], event["thread_id"])
        self.assertNotEqual(first["thread_id"], second["thread_id"])
        self.assertEqual(3, len({event["span_id"] for event in self.handler.events}))
        self.assertLessEqual(first["start_s"], second["start_s"] + second["duration_s"])
        self.assertLessEqual(second["start_s"], first["start_s"] + first["duration_s"])

    def test_scan_decorator_tags_next_one_based_storage_position(self):
        class Scanner:
            session_id = "scanner"
            skip_first_n = 78
            visited_count = 7

            @timing.timed("scan.advance", scan=True)
            def advance(self):
                with timing.span("capture"):
                    return True

        self.assertTrue(Scanner().advance())
        for event in self.handler.events:
            self.assertEqual("scanner", event["session_id"])
            self.assertEqual(86, event["position"])

    def test_real_appraisal_worker_keeps_parallel_context_and_same_frame_reads(self):
        from pokemgr.calibration.profile import CalibrationProfile
        from pokemgr.calibration.regions import ScreenRegions
        from pokemgr.indexer.state_machine import IndexingStateMachine

        regions = ScreenRegions.default_for_resolution(968, 2376, density=420)
        profile = CalibrationProfile(
            device_model="test", serial="serial", resolution="968x2376",
            density=420, regions=regions,
        )
        sm = IndexingStateMachine(Mock(), profile, Mock())
        image = Image.new("RGB", (96, 237))
        seen_images = []

        def hp(read_image):
            seen_images.append(read_image)
            return 174

        sm.reader.read_hp = timing.timed("reader.hp.test")(hp)
        sm.reader.read_detail_screen = Mock(return_value={"species": "Zacian", "cp": -1})
        sm.reader.read_appraisal_screen = Mock(return_value={"atk": 11, "def_": 10, "sta": 14})
        read = timing.timed("reader.snapshot.test")(sm._read_appraisal_snapshot)
        with patch("pokemgr.reader.ocr.is_in_gym", return_value=False), \
                patch("pokemgr.reader.ocr.read_caught_species", return_value="Zacian"):
            with timing.span("scan.acquire.test", session_id="actual-thread", position=3):
                detail, appraisal = read(image)

        self.assertEqual([image], seen_images)
        self.assertEqual(174, detail["hp"])
        self.assertEqual("Zacian", detail["caught_species"])
        self.assertTrue(detail["snapshot_read_complete"])
        self.assertEqual({"atk": 11, "def_": 10, "sta": 14}, appraisal)
        sm.reader.read_detail_screen.assert_called_once_with(image, include_cp=False)
        sm.reader.read_appraisal_screen.assert_called_once_with(image)
        worker = self.events("reader.hp_gym_caught")[0]
        hp_event = self.events("reader.hp.test")[0]
        snapshot_events = self.events("reader.snapshot") or self.events("reader.snapshot.test")
        snapshot_event = snapshot_events[0]
        self.assertEqual(snapshot_event["span_id"], worker["parent_id"])
        self.assertEqual(worker["span_id"], hp_event["parent_id"])
        self.assertNotEqual(snapshot_event["thread_id"], worker["thread_id"])
        self.assertEqual("actual-thread", worker["session_id"])
        self.assertEqual(3, worker["position"])

    def test_capture_spans_separate_transport_decode_and_preserve_freshness_metadata(self):
        from pokemgr.adb.controller import ADBController

        output = io.BytesIO()
        Image.new("RGB", (96, 237), "white").save(output, format="PNG")
        with patch.dict(os.environ, {}, clear=True):
            adb = ADBController(adb_path="unused")
        adb._run = Mock(return_value=CompletedProcess([], 0, stdout=output.getvalue(), stderr=b""))
        adb.validate_display_target = Mock(return_value=None)
        capture = timing.timed("capture.test")(adb.screencap)

        with timing.span("scan.acquire.test", session_id="capture", position=5):
            result = capture()

        self.assertEqual((96, 237), result.size)
        self.assertEqual((255, 255, 255), result.getpixel((0, 0)))
        transport = self.events("capture.transport")[0]
        decode = self.events("capture.decode")[0]
        self.assertEqual(transport["parent_id"], decode["parent_id"])
        self.assertEqual("capture", transport["session_id"])
        self.assertEqual(5, decode["position"])
        self.assertLessEqual(transport["start_s"], result.info["pokemgr_capture_started_at"])
        self.assertLessEqual(result.info["pokemgr_capture_finished_at"], decode["start_s"])
        adb._run.assert_called_once_with(["exec-out", "screencap", "-p"])
        adb.validate_display_target.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
