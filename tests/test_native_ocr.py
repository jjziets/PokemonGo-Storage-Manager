"""Native OCR wire protocol and strict parsing; no phone or Vision required."""

from dataclasses import replace
from pathlib import Path
import tempfile
import textwrap
import unittest

from PIL import Image

from pokemgr.calibration.regions import BBox, ScreenRegions
from pokemgr.reader.native_ocr import (
    NativeOCR, NativeOCRError, NativeOCRFrameError, NativeFrameText, TextObservation,
    _decode_response, parse_appraisal_fields,
)


class NativeParserTests(unittest.TestCase):
    def setUp(self):
        self.regions = ScreenRegions.default_for_resolution(968, 2376)

    def frame(self, *observations):
        return NativeFrameText("frame-1", 968, 2376, tuple(observations), 12.0)

    def observation(self, text, field="cp", confidence=.5):
        boxes = {
            "cp": (330., 120., 240., 60.),
            "hp": (405., 978., 170., 24.),
            "name": (350., 855., 250., 55.),
            "caught": (60., 2150., 800., 40.),
            "outside": (30., 350., 200., 40.),
        }
        return TextObservation(text, confidence, boxes[field])

    def parse(self, *observations):
        return parse_appraisal_fields(self.frame(*observations), self.regions)

    def test_complete_whole_tokens_from_one_frame(self):
        fields = self.parse(
            self.observation("cp3474"), self.observation("153 / 153 HP", "hp"),
            self.observation("Gardevoir", "name"),
            self.observation("This Gardevoir was caught on 2025/03/23", "caught"),
        )
        self.assertEqual((fields.cp, fields.hp, fields.display_name, fields.caught_species),
                         (3474, 153, "Gardevoir", "Gardevoir"))
        self.assertEqual(fields.cp_confidence, .5)

    def test_cp_never_repairs_or_extracts_partial_digits(self):
        for text in ("CP428T", "CP7A7", "CP4 287", "CP4287x", "-CP4287",
                     "4287", "P4287", "CP04287", "CP12345", "CP7", "CP１２３４", "฿4099"):
            with self.subTest(text=text):
                self.assertEqual(self.parse(self.observation(text)).cp, -1)

    def test_cp_cannot_join_separate_observations(self):
        self.assertEqual(self.parse(self.observation("CP"), self.observation("4287")).cp, -1)

    def test_whole_bare_cp_requires_an_exact_recovery_candidate(self):
        fields = parse_appraisal_fields(self.frame(self.observation("4287")), self.regions,
                                        expected_cps={4287, 4310})
        self.assertEqual(fields.cp, 4287)
        fields = parse_appraisal_fields(self.frame(self.observation("561")), self.regions,
                                        expected_cps={5561, 5594})
        self.assertEqual(fields.cp, -1)
        fields = parse_appraisal_fields(self.frame(self.observation("CP4287"), self.observation("4310")),
                                        self.regions, expected_cps={4287, 4310})
        self.assertEqual(fields.cp, -1)
        self.assertTrue(fields.cp_conflict)

    def test_hp_requires_whole_line_and_consistent_current_max(self):
        for text in ("188/188", "188 / 188 H", "188/188 HPx", "188/18 HP", "188/588 HP",
                     "l88/188 HP", "188 / 1 88 HP", "188/188 HP extra", "188/0188 HP"):
            with self.subTest(text=text):
                self.assertEqual(self.parse(self.observation(text, "hp")).hp, -1)
        self.assertEqual(self.parse(self.observation("0 / 188 HP", "hp")).hp, 188)

    def test_unrelated_numbers_and_names_outside_regions_ignored(self):
        fields = self.parse(*(self.observation(text, "outside") for text in
                              ("CP3474", "153 / 153 HP", "Gardevoir", "This Gardevoir was caught")))
        self.assertEqual((fields.cp, fields.hp, fields.display_name, fields.caught_species),
                         (-1, -1, "", ""))
        self.assertFalse(fields.cp_conflict)

    def test_conflicting_complete_observations_are_missing(self):
        fields = self.parse(
            self.observation("CP3474"), self.observation("CP3480", confidence=1),
            self.observation("153/153 HP", "hp"), self.observation("154/154 HP", "hp"),
            self.observation("Gardevoir", "name"), self.observation("Scizor", "name"),
            self.observation("This Gardevoir was caught", "caught"),
            self.observation("This Scizor was caught", "caught"),
        )
        self.assertEqual((fields.cp, fields.hp, fields.display_name, fields.caught_species),
                         (-1, -1, "", ""))
        self.assertTrue(fields.cp_conflict)

    def test_duplicate_identical_observations_do_not_conflict(self):
        fields = self.parse(self.observation("CP3474"), self.observation("cp3474", confidence=1))
        self.assertEqual((fields.cp, fields.cp_confidence), (3474, 1))

    def test_low_confidence_is_missing(self):
        self.assertEqual(self.parse(self.observation("CP3474", confidence=.49)).cp, -1)

    def test_names_remain_raw_without_species_expansion_or_artifact_repair(self):
        fields = self.parse(self.observation("Ke", "name"),
                            self.observation("This s Abra was caught", "caught"))
        self.assertEqual(fields.display_name, "Ke")
        self.assertEqual(fields.caught_species, "s Abra")
        self.assertEqual(self.parse(self.observation("Gardevoir|", "name")).display_name, "")

    def test_caught_identity_requires_both_anchor_words_in_one_observation(self):
        for text in ("This Gardevoir", "Gardevoir was caught", "about This Gardevoir was", "This was caught"):
            with self.subTest(text=text):
                self.assertEqual(self.parse(self.observation(text, "caught")).caught_species, "")

    def test_calibration_dimensions_must_match(self):
        with self.assertRaises(ValueError):
            parse_appraisal_fields(replace(self.frame(), width=969), self.regions)

    def test_native_response_checks_frame_id_dimensions_and_boxes(self):
        response = {
            "id": 1, "frame_id": "one", "width": 968, "height": 2376,
            "ocr_ms": 12., "observations": [
                {"text": "CP3474", "confidence": .5, "bbox": [330, 120, 240, 60]},
            ],
        }
        result = _decode_response(response, 1, "one", 968, 2376)
        self.assertEqual(result.observations[0].bbox, (330., 120., 240., 60.))
        for changes in ({"id": 2}, {"frame_id": "older"}, {"width": 969},
                        {"ocr_ms": float("nan")}, {"error": "Vision failed"},
                        {"observations": [{"text": "CP3474", "confidence": .5,
                                            "bbox": [330, 120, 900, 60]}]}):
            with self.subTest(changes=changes), self.assertRaises(NativeOCRError):
                _decode_response({**response, **changes}, 1, "one", 968, 2376)

    def test_tablet_bubble_uses_calibrated_layout(self):
        regions = ScreenRegions.default_for_resolution(1440, 2304, density=280)
        observations = (
            TextObservation("CP3474", .5, (500, 140, 220, 60)),
            TextObservation("153/153 HP", .5, (600, 1280, 240, 25)),
            TextObservation("Gardevoir", .5, (570, 1130, 300, 65)),
            TextObservation("This Gardevoir was caught", .5, (70, 1770, 1150, 45)),
        )
        fields = parse_appraisal_fields(NativeFrameText("tablet", 1440, 2304, observations, 12),
                                        regions, density=280)
        self.assertEqual((fields.cp, fields.hp, fields.display_name, fields.caught_species),
                         (3474, 153, "Gardevoir", "Gardevoir"))

    def test_out_of_frame_geometry_rejects_all_fields_without_hiding_protocol_errors(self):
        response = {
            "id": 1, "frame_id": "one", "width": 968, "height": 2376, "ocr_ms": 12.,
            "observations": [
                {"text": "CP3474", "confidence": .5, "bbox": [330, 120, 240, 60]},
                {"text": "edge text", "confidence": .5, "bbox": [900, 120, 100, 60]},
            ],
        }
        with self.assertRaisesRegex(NativeOCRFrameError, "image=968x2376"):
            _decode_response(response, 1, "one", 968, 2376)
        response["observations"].append({"text": "malformed", "confidence": float("nan"),
                                         "bbox": [0, 0, 10, 10]})
        with self.assertRaises(NativeOCRError) as raised:
            _decode_response(response, 1, "one", 968, 2376)
        self.assertNotIsInstance(raised.exception, NativeOCRFrameError)


_WORKER = '''\
#!/usr/bin/env python3
import json, struct, sys, time

def read_exact(count):
    result = b""
    while len(result) < count:
        chunk = sys.stdin.buffer.read(count - len(result))
        if not chunk:
            raise EOFError
        result += chunk
    return result

while True:
    try:
        header = json.loads(read_exact(struct.unpack(">I", read_exact(4))[0]))
        pixels = read_exact(header["payload_bytes"])
    except EOFError:
        break
    box = [0, 0, 1, 1]
    ACTION
    result = dict(header, ocr_ms=1., observations=[
        {"text": str(pixels[0]), "confidence": .5, "bbox": box}
    ])
    print(json.dumps(result), flush=True)
'''


class NativeWorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def worker(self, action="pass", timeout=1):
        path = Path(self.directory.name) / "worker"
        path.write_text(textwrap.dedent(_WORKER).replace("ACTION", action))
        path.chmod(0o700)
        native = NativeOCR(executable=path, timeout=timeout)
        self.addCleanup(native.close)
        return native

    def test_persistent_worker_reads_independent_pixel_payloads(self):
        native = self.worker()
        native.start()
        process = native._process
        native.start()
        self.assertIs(native._process, process)
        first = native.recognize(Image.new("RGBA", (2, 2), (20, 0, 0, 255)), frame_id="first")
        second = native.recognize(Image.new("RGB", (2, 2), (90, 0, 0)), frame_id="second")
        self.assertEqual((first.frame_id, first.observations[0].text), ("first", "20"))
        self.assertEqual((second.frame_id, second.observations[0].text), ("second", "90"))
        self.assertIs(native._process, process)
        native.close()
        self.assertIsNotNone(process.poll())

    def test_wrong_request_id_discards_worker(self):
        native = self.worker('header["id"] -= 1')
        with self.assertRaisesRegex(NativeOCRError, "different request/frame"):
            native.recognize(Image.new("RGB", (2, 2)), frame_id=1)
        self.assertIsNone(native._process)

    def test_invalid_geometry_then_healthy_frame_uses_same_worker_without_retry(self):
        native = self.worker('box = [0, 0, 3, 1] if header["id"] == 1 else box')
        native.start()
        process = native._process
        with self.assertRaises(NativeOCRFrameError):
            native.recognize(Image.new("RGB", (2, 2), (20, 0, 0)), frame_id="invalid")
        self.assertIs(native._process, process)
        self.assertIsNone(process.poll())
        self.assertEqual(native._request_id, 1)
        result = native.recognize(Image.new("RGB", (2, 2), (90, 0, 0)), frame_id="healthy")
        self.assertEqual((result.frame_id, result.observations[0].text), ("healthy", "90"))
        self.assertIs(native._process, process)
        self.assertEqual(native._request_id, 2)

    def test_nonfinite_geometry_is_protocol_failure_and_discards_worker(self):
        native = self.worker('box = [0, 0, float("nan"), 1]')
        with self.assertRaises(NativeOCRError) as raised:
            native.recognize(Image.new("RGB", (2, 2)), frame_id="malformed")
        self.assertNotIsInstance(raised.exception, NativeOCRFrameError)
        self.assertIsNone(native._process)

    def test_region_uses_exact_pixels_and_maps_boxes_to_original_frame(self):
        native = self.worker()
        image = Image.new("RGB", (10, 12), (10, 0, 0))
        image.putpixel((3, 4), (90, 0, 0))
        image.info["pokemgr_native_text"] = "inherited evidence must not be used"
        result = native.recognize_region(image, BBox(3, 4, 5, 6), frame_id="source-frame")
        self.assertEqual((result.frame_id, result.width, result.height), ("source-frame", 10, 12))
        self.assertEqual(result.observations[0].text, "90")
        self.assertEqual(result.observations[0].bbox, (3., 4., 1., 1.))

    def test_invalid_region_does_not_start_worker(self):
        native = self.worker()
        for region in (BBox(-1, 0, 2, 2), BBox(0, 0, 0, 2), BBox(1, 1, 2, 2),
                       BBox(0.0, 0, 2, 2), BBox(True, 0, 1, 1), (0, 0, 2, 2)):
            with self.subTest(region=region), self.assertRaises(ValueError):
                native.recognize_region(Image.new("RGB", (2, 2)), region, frame_id="source-frame")
        self.assertIsNone(native._process)

    def test_timeout_discards_worker_and_does_not_wait_indefinitely(self):
        native = self.worker("time.sleep(10)", timeout=.1)
        with self.assertRaisesRegex(NativeOCRError, "timed out"):
            native.recognize(Image.new("RGB", (2, 2)), frame_id=1)
        self.assertIsNone(native._process)

    def test_worker_exit_is_an_error_and_can_start_fresh(self):
        native = self.worker("sys.exit(1)")
        with self.assertRaises(NativeOCRError):
            native.recognize(Image.new("RGB", (2, 2)), frame_id=1)
        self.assertIsNone(native._process)
        Path(native._executable).write_text(textwrap.dedent(_WORKER).replace("ACTION", "pass"))
        result = native.recognize(Image.new("RGB", (2, 2)), frame_id=2)
        self.assertEqual(result.frame_id, "2")

    def test_invalid_arguments_do_not_start_a_worker(self):
        native = self.worker()
        for arguments in ({"frame_id": ""}, {"frame_id": 1, "mode": "typo"},
                          {"frame_id": True}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                native.recognize(Image.new("RGB", (2, 2)), **arguments)
        self.assertIsNone(native._process)


if __name__ == "__main__":
    unittest.main()
