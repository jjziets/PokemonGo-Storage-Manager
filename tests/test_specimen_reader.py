# TRACEWEAVER: file-role=specimen-reader-tests; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
"""Specimen refinement stays on one image and never adds routine OCR work."""

from dataclasses import replace
import gc
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call

from PIL import Image

from pokemgr.calibration.regions import ScreenRegions
from pokemgr.reader.native_ocr import (
    NativeFields, NativeFrameText, NativeOCRError, NativeOCRFrameError, TextObservation,
)
from pokemgr.reader.screen import ScreenReader


# TRACEWEAVER: verifies=VER-SCAN-001; req=REQ-SCAN-003; trace=TRACE-SCAN-003
class SpecimenReaderTests(unittest.TestCase):
    OLD = ("24.34", "0.91", "2026-06-20")
    NEW = ("37.48", "1.12", "2026-07-31")

    def setUp(self):
        self.profile = SimpleNamespace(
            regions=ScreenRegions.default_for_resolution(968, 2376), density=420,
        )
        self.worker = Mock()
        self.worker.recognize.side_effect = self.recognize
        self.reader = self.make_reader(self.worker)
        self.image = Image.new("RGB", (968, 2376), "white")
        self.fast = self.observations(self.OLD, confidence=.5)
        self.accurate = self.observations(self.OLD)

    def make_reader(self, worker):
        reader = ScreenReader(self.profile, fast_cp=True, read_size_tags=False)
        reader._native_enabled = True
        reader._native_ocr = worker
        self.addCleanup(reader.close)
        return reader

    def observations(self, values, *, confidence=1.0):
        weight, height, caught_date = values
        items = [
            TextObservation("CP832", 1., (330., 120., 240., 60.)),
            TextObservation("94/94 HP", 1., (405., 978., 170., 24.)),
        ]
        if weight:
            items.extend((TextObservation(weight + "kg", confidence, (115., 1124., 171., 40.)),
                          TextObservation("WEIGHT", confidence, (143., 1181., 113., 20.))))
        if height:
            items.extend((TextObservation(height + "m", confidence, (740., 1125., 112., 37.)),
                          TextObservation("HEIGHT", confidence, (745., 1181., 105., 20.))))
        if caught_date:
            items.append(TextObservation(
                "This Larvesta was caught on " + caught_date.replace("-", "/"),
                confidence, (65., 2166., 820., 39.),
            ))
        return tuple(items)

    def recognize(self, image, *, frame_id, mode="fast"):
        if image.getpixel((0, 0))[0] == 0:
            observations = self.observations(self.NEW, confidence=.5 if mode == "fast" else 1.)
        else:
            observations = self.fast if mode == "fast" else self.accurate
        return NativeFrameText(str(frame_id), image.width, image.height, observations, 12.)

    def test_normal_native_fields_never_request_specimen_refinement(self):
        first = self.reader.native_fields(self.image)
        self.assertIs(first, self.reader.native_fields(self.image))
        self.assertEqual(94, self.reader.read_hp(self.image))
        self.assertEqual((832, 1.), self.reader.native_cp(self.image))
        self.worker.recognize.assert_called_once_with(self.image, frame_id=str(id(self.image)))
        self.assertNotIn("pokemgr_specimen_markers", self.image.info)

    def test_complete_confident_fast_markers_need_no_accurate_request(self):
        self.fast = self.observations(self.OLD)
        self.assertEqual(self.OLD, self.reader.specimen_markers(self.image))
        self.assertEqual(self.OLD, self.reader.specimen_markers(self.image))
        self.worker.recognize.assert_called_once_with(self.image, frame_id=str(id(self.image)))

    def test_refinement_reuses_fast_text_and_caches_only_optional_markers(self):
        original_fields = self.reader.native_fields(self.image)
        original_text = self.image.info["pokemgr_native_text"]
        self.assertEqual(self.OLD, self.reader.specimen_markers(self.image))
        self.assertEqual(self.OLD, self.reader.specimen_markers(self.image))
        self.assertIs(original_fields, self.reader.native_fields(self.image))
        self.assertIs(original_text, self.image.info["pokemgr_native_text"])
        self.assertEqual([
            call(self.image, frame_id=str(id(self.image))),
            call(self.image, frame_id=str(id(self.image)), mode="accurate"),
        ], self.worker.recognize.call_args_list)
        self.worker.recognize_region.assert_not_called()

    def test_copied_and_transformed_images_do_not_inherit_markers(self):
        self.assertEqual(self.OLD, self.reader.specimen_markers(self.image))
        copied = self.image.copy()
        copied.putpixel((0, 0), (0, 0, 0))
        self.assertEqual(self.NEW, self.reader.specimen_markers(copied))
        transformed = self.image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        transformed.putpixel((0, 0), (0, 0, 0))
        self.assertEqual(self.NEW, self.reader.specimen_markers(transformed))
        self.assertEqual(6, self.worker.recognize.call_count)
        self.assertEqual(self.OLD, self.reader.specimen_markers(self.image))
        self.assertEqual(6, self.worker.recognize.call_count)

    def test_another_reader_cannot_reuse_the_first_readers_cache(self):
        self.assertEqual(self.OLD, self.reader.specimen_markers(self.image))
        worker = Mock()
        worker.recognize.side_effect = lambda image, *, frame_id, mode="fast": NativeFrameText(
            str(frame_id), image.width, image.height,
            self.observations(self.NEW, confidence=.5 if mode == "fast" else 1.), 12.,
        )
        another = self.make_reader(worker)
        self.assertEqual(self.NEW, another.specimen_markers(self.image))
        self.assertEqual(2, worker.recognize.call_count)
        self.assertEqual(self.OLD, self.reader.specimen_markers(self.image))
        self.assertEqual(4, self.worker.recognize.call_count)

    def test_dead_source_reference_cannot_authorize_a_copied_marker_cache(self):
        source = Image.new("RGB", self.image.size, "white")
        self.assertEqual(self.OLD, self.reader.specimen_markers(source))
        copied = source.copy()
        source_ref = copied.info["pokemgr_specimen_markers"][1]
        self.worker.reset_mock()
        del source
        gc.collect()
        self.assertIsNone(source_ref())
        copied.putpixel((0, 0), (0, 0, 0))
        self.assertEqual(self.NEW, self.reader.specimen_markers(copied))
        self.assertEqual(2, self.worker.recognize.call_count)

    def test_integer_owner_ids_cannot_authorize_a_marker_cache(self):
        self.image.info["pokemgr_specimen_markers"] = (
            id(self.reader), id(self.image), self.image.size, self.NEW,
        )
        self.assertEqual(self.OLD, self.reader.specimen_markers(self.image))
        self.assertEqual(2, self.worker.recognize.call_count)

    def test_changed_image_dimensions_invalidate_cached_markers(self):
        self.assertEqual(self.OLD, self.reader.specimen_markers(self.image))
        self.image.thumbnail((484, 1188))
        self.assertEqual(("", "", ""), self.reader.specimen_markers(self.image))
        self.assertEqual(3, self.worker.recognize.call_count)

    def test_confident_fast_accurate_disagreement_discards_only_that_field(self):
        self.fast = self.observations(("37.48", "", ""))
        self.assertEqual(("", self.OLD[1], self.OLD[2]),
                         self.reader.specimen_markers(self.image))
        self.assertEqual(2, self.worker.recognize.call_count)

    def test_an_internal_conflict_cannot_be_resurrected_by_the_other_pass(self):
        for conflicting_mode in ("fast", "accurate"):
            with self.subTest(conflicting_mode=conflicting_mode):
                image = Image.new("RGB", self.image.size, "white")
                self.fast = self.observations((self.OLD[0], "", ""))
                self.accurate = self.observations(self.OLD)
                conflict = self.observations((self.NEW[0], "", ""))
                if conflicting_mode == "fast":
                    self.fast += conflict
                else:
                    self.accurate += conflict
                self.assertEqual(("", self.OLD[1], self.OLD[2]),
                                 self.reader.specimen_markers(image))

    def test_refinement_rejects_wrong_frame_identity_and_dimensions(self):
        for changes in ({"frame_id": "other-frame"}, {"width": 967}, {"height": 2375}):
            with self.subTest(changes=changes):
                image = Image.new("RGB", self.image.size, "white")

                def recognize(frame, *, frame_id, mode="fast"):
                    result = self.recognize(frame, frame_id=frame_id, mode=mode)
                    return replace(result, **changes) if mode == "accurate" else result

                self.worker.recognize.side_effect = recognize
                self.assertEqual(("", "", ""), self.reader.specimen_markers(image))

    def test_refinement_cannot_merge_mismatched_cached_fast_text(self):
        for changes in ({"frame_id": "other-frame"}, {"width": 967}, {"height": 2375}):
            with self.subTest(changes=changes):
                image = Image.new("RGB", self.image.size, "white")
                self.reader.native_fields(image)
                image.info["pokemgr_native_text"] = replace(
                    image.info["pokemgr_native_text"], **changes,
                )
                self.assertEqual(("", "", ""), self.reader.specimen_markers(image))

    def test_accurate_errors_are_cached_as_unavailable_without_affecting_native_fields(self):
        for error in (NativeOCRError("timeout"), NativeOCRFrameError("bad box"),
                      ValueError("wrong geometry")):
            with self.subTest(error=type(error).__name__):
                image = Image.new("RGB", self.image.size, "white")
                self.fast = self.observations((self.OLD[0], "", ""))

                def recognize(frame, *, frame_id, mode="fast"):
                    if mode == "accurate":
                        raise error
                    return self.recognize(frame, frame_id=frame_id, mode=mode)

                self.worker.recognize.side_effect = recognize
                original = self.reader.native_fields(image)
                before = self.worker.recognize.call_count
                self.assertEqual(("", "", ""), self.reader.specimen_markers(image))
                self.assertEqual(("", "", ""), self.reader.specimen_markers(image))
                self.assertIs(original, self.reader.native_fields(image))
                self.assertEqual(before + 1, self.worker.recognize.call_count)

    def test_absent_raw_fast_text_cannot_supply_fields_to_accurate_only_result(self):
        self.reader.native_fields = Mock(return_value=NativeFields(specimen_weight="24.34"))
        self.accurate = self.observations(("", "0.91", "2026-06-20"))
        self.assertEqual(("", "0.91", "2026-06-20"),
                         self.reader.specimen_markers(self.image))
        self.worker.recognize.assert_called_once_with(
            self.image, frame_id=str(id(self.image)), mode="accurate",
        )

    def test_disabled_native_reader_does_not_start_ocr_for_specimen_markers(self):
        self.reader._native_enabled = False
        self.reader._native_ocr = None
        self.reader.prepare_native_ocr = Mock()
        self.assertEqual(("", "", ""), self.reader.specimen_markers(self.image))
        self.reader.prepare_native_ocr.assert_not_called()
        self.worker.recognize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
