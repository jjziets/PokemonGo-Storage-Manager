import io
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from pokemgr.adb.controller import ADBController


class ScreencapDecodeTests(unittest.TestCase):
    @staticmethod
    def _capture_result():
        encoded = io.BytesIO()
        Image.new("RGB", (96, 128), "white").save(encoded, format="PNG")
        return SimpleNamespace(returncode=0, stdout=encoded.getvalue(), stderr=b"")

    def test_capture_timestamps_exclude_display_verification_and_png_decode(self):
        result = self._capture_result()
        adb = ADBController(serial="test")
        clock = [10.0]
        events = []
        original_open = Image.open

        def verify():
            events.append("verify")
            clock[0] += 3.0

        def capture(args):
            self.assertEqual(["exec-out", "screencap", "-p"], args)
            events.append("capture")
            clock[0] += 0.7
            return result

        def decode(*args, **kwargs):
            events.append("decode")
            clock[0] += 0.4
            return original_open(*args, **kwargs)

        adb.validate_display_target = Mock(side_effect=verify)
        adb._run = Mock(side_effect=capture)
        with patch("pokemgr.adb.controller.time.monotonic", side_effect=lambda: clock[0]), \
                patch("pokemgr.adb.controller.Image.open", side_effect=decode):
            frame = adb.screencap()

        self.assertEqual(["verify", "capture", "decode"], events)
        self.assertEqual(13.0, frame.info["pokemgr_capture_started_at"])
        self.assertAlmostEqual(13.7, frame.info["pokemgr_capture_finished_at"])
        self.assertGreater(clock[0], frame.info["pokemgr_capture_finished_at"])

    def test_reconnect_capture_replaces_failed_attempt_timestamps_after_reverification(self):
        result = self._capture_result()
        adb = ADBController(serial="test")
        clock = [10.0]
        events = []

        def verify():
            events.append("verify")
            clock[0] += 2.0

        def capture(_args):
            events.append("capture")
            clock[0] += 0.5
            if events.count("capture") == 1:
                return SimpleNamespace(returncode=1, stdout=b"", stderr=b"device offline")
            return result

        def reconnect():
            events.append("reconnect")
            clock[0] += 30.0
            return True

        adb.validate_display_target = Mock(side_effect=verify)
        adb._run = Mock(side_effect=capture)
        adb.wait_for_device = Mock(side_effect=reconnect)
        with patch("pokemgr.adb.controller.time.monotonic", side_effect=lambda: clock[0]):
            frame = adb.screencap()

        self.assertEqual(["verify", "capture", "reconnect", "verify", "capture"], events)
        self.assertEqual(44.5, frame.info["pokemgr_capture_started_at"])
        self.assertEqual(45.0, frame.info["pokemgr_capture_finished_at"])

    def test_screencap_is_fully_loaded_before_parallel_ocr_reads(self):
        pixels = np.zeros((96, 128, 3), dtype=np.uint8)
        pixels[:, :, 1] = 170
        encoded = io.BytesIO()
        Image.fromarray(pixels).save(encoded, format="PNG")

        adb = ADBController(serial="test")
        adb._run = Mock(return_value=SimpleNamespace(
            returncode=0,
            stdout=encoded.getvalue(),
            stderr=b"",
        ))

        frame = adb.screencap()

        # A decoded single-frame image has released its source stream.  This
        # is the deterministic guard against two OCR threads racing in load().
        self.assertIsNone(frame.fp)

        errors = []

        def consume_frame():
            try:
                for _ in range(20):
                    np.array(frame)
                    frame.crop((0, 0, 64, 48)).tobytes()
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        workers = [threading.Thread(target=consume_frame) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

        self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
