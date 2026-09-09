"""Frame-bound native text reuse through the existing reader and scan facade."""

from dataclasses import replace
import gc
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from pokemgr.calibration.regions import BBox, ScreenRegions
from pokemgr.indexer.state_machine import IndexingStateMachine
from pokemgr.indexer.snapshot import AppraisalSnapshot
from pokemgr.reader.native_ocr import NativeFrameText, NativeOCRError, NativeOCRFrameError, TextObservation
from pokemgr.reader.screen import ScreenReader


class NativeReaderIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.regions = ScreenRegions.default_for_resolution(968, 2376)
        self.profile = SimpleNamespace(regions=self.regions, density=420)
        self.environment = patch.dict("os.environ", {"POKEMGR_NATIVE_OCR": "1"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.reader = ScreenReader(self.profile, fast_cp=True, read_size_tags=False)
        self.worker = Mock()
        self.worker.recognize.side_effect = self.recognize
        self.worker.recognize_region.side_effect = self.recognize_region
        self.reader._native_ocr = self.worker
        self.addCleanup(self.reader.close)
        self.cp_text = "CP3474"
        self.refinement_tokens = ()
        self.image = Image.new("RGB", (968, 2376), (255, 255, 255))

    def recognize(self, image, *, frame_id, mode="fast"):
        cp = self.cp_text if image.getpixel((0, 0))[0] == 255 else "CP3209"
        return NativeFrameText(str(frame_id), image.width, image.height, (
            TextObservation(cp, .5, (330, 120, 240, 60)),
            TextObservation("153/153 HP", .5, (405, 978, 170, 24)),
            TextObservation("Gardevoir", .5, (350, 855, 250, 55)),
            TextObservation("This Gardevoir was caught on 2025/03/23", .5, (60, 2150, 800, 40)),
        ), 12.)

    def recognize_region(self, image, region, *, frame_id, mode="accurate"):
        return NativeFrameText(str(frame_id), image.width, image.height, tuple(
            TextObservation(text, .5, (330, 120, 240, 60)) for text in self.refinement_tokens
        ), 10.)

    def test_all_text_fields_reuse_one_native_read_and_keep_icons(self):
        with patch("pokemgr.reader.screen.ocr.read_hp") as hp, \
             patch("pokemgr.reader.screen.ocr.read_cp") as cp, \
             patch("pokemgr.reader.screen.ocr.read_species_name") as name, \
             patch("pokemgr.reader.screen.match_species_name", side_effect=lambda text: text), \
             patch("pokemgr.reader.screen.icons.is_favorited", return_value=True), \
             patch("pokemgr.reader.screen.icons.is_lucky", return_value=True) as lucky, \
             patch("pokemgr.reader.screen._detect_gender", return_value="female"):
            detail = self.reader.read_detail_screen(self.image, include_cp=False)
            self.assertEqual(detail["cp"], -1)
            self.assertEqual(self.reader.read_hp(self.image), 153)
            self.assertEqual(self.reader.read_cp(self.image), (3474, .5))
            self.assertEqual(self.reader.native_fields(self.image).caught_species, "Gardevoir")
            self.assertEqual((detail["favorited"], detail["lucky"], detail["gender"]), (True, False, "female"))
            self.assertEqual(detail["display_name"], "Gardevoir")
            hp.assert_not_called()
            cp.assert_not_called()
            name.assert_not_called()
            lucky.assert_not_called()
        self.worker.recognize.assert_called_once()

    def test_copied_image_cannot_reuse_original_frame_text(self):
        self.assertEqual(self.reader.read_cp(self.image), (3474, .5))
        copied = self.image.copy()
        copied.putpixel((0, 0), (0, 0, 0))
        self.assertEqual(self.reader.read_cp(copied), (3209, .5))
        self.assertEqual(self.worker.recognize.call_count, 2)
        self.assertNotEqual(copied.info["pokemgr_native_text"].frame_id,
                            self.image.info["pokemgr_native_text"].frame_id)

    def test_candidate_cp_uses_raw_observation_only_from_this_frame(self):
        self.cp_text = "4287"
        self.assertEqual(self.reader.native_cp(self.image), (-1, 0.))
        with patch("pokemgr.reader.screen.ocr.read_cp") as fallback:
            self.assertEqual(self.reader.read_cp(self.image, expected_cps={4287, 4310}), (4287, .5))
            fallback.assert_not_called()
        self.worker.recognize.assert_called_once()
        self.worker.recognize_region.assert_not_called()

    def test_candidate_only_accurate_crop_recovers_exact_cp_and_preserves_full_fields(self):
        self.cp_text = "CP211T"
        self.refinement_tokens = ("CP2119",)
        original = self.reader.native_fields(self.image)
        self.assertEqual(self.reader.native_cp(self.image), (-1, 0.))
        self.worker.recognize_region.assert_not_called()
        with patch("pokemgr.reader.screen.ocr.read_cp") as fallback:
            self.assertEqual(self.reader.read_cp(self.image, expected_cps={2119, 2703}), (2119, .5))
            self.assertEqual(self.reader.read_cp(self.image, expected_cps={2119, 2703}), (2119, .5))
            fallback.assert_not_called()
        self.worker.recognize_region.assert_called_once_with(
            self.image, BBox(280, 80, 420, 130), frame_id=str(id(self.image)), mode="accurate",
        )
        self.assertIs(self.reader.native_fields(self.image), original)
        self.assertEqual((original.cp, original.hp, original.display_name, original.caught_species),
                         (-1, 153, "Gardevoir", "Gardevoir"))
        self.assertEqual(self.image.info["pokemgr_native_text"].observations[0].text, "CP211T")
        self.worker.recognize.assert_called_once()

    def test_copied_image_cannot_inherit_crop_evidence(self):
        self.cp_text = "CP211T"
        self.refinement_tokens = ("CP2119",)
        self.assertEqual(self.reader.native_cp(self.image, expected_cps={2119, 2703}), (2119, .5))
        copied = self.image.copy()
        copied.putpixel((0, 0), (0, 0, 0))
        self.refinement_tokens = ("CP2703",)
        self.assertEqual(self.reader.native_cp(copied, expected_cps={2119, 2703}), (2703, .5))
        self.assertEqual(self.worker.recognize.call_count, 2)
        self.assertEqual(self.worker.recognize_region.call_count, 2)
        self.assertNotEqual(copied.info["pokemgr_native_cp_text"].frame_id,
                            self.image.info["pokemgr_native_cp_text"].frame_id)

    def test_copy_of_copy_cannot_use_evidence_after_original_image_dies(self):
        self.cp_text = "CP211T"
        self.refinement_tokens = ("CP2119",)
        source = Image.new("RGB", self.image.size, "white")
        self.assertEqual(self.reader.native_cp(source, expected_cps={2119, 2703}), (2119, .5))
        copied = source.copy()
        source_ref = source.info["pokemgr_native_owner"][1]
        # Mock call records retain their input; remove those references so this
        # reproduces a real stream frame being released while a copy survives.
        self.worker.reset_mock()
        del source
        gc.collect()
        self.assertIsNone(source_ref())
        copied.putpixel((0, 0), (0, 0, 0))
        copy_of_copy = copied.copy()
        self.refinement_tokens = ("CP2703",)
        self.assertEqual(self.reader.native_cp(copy_of_copy, expected_cps={2119, 2703}), (2703, .5))
        self.worker.recognize.assert_called_once()
        self.worker.recognize_region.assert_called_once()

    def test_legacy_integer_owner_collision_cannot_authorize_inherited_evidence(self):
        self.reader.native_fields(self.image)
        copied = self.image.copy()
        copied.putpixel((0, 0), (0, 0, 0))
        # Simulate recycled integer IDs matching an older cached owner tuple.
        copied.info["pokemgr_native_owner"] = (id(self.reader), id(copied), copied.size)
        self.assertEqual(self.reader.native_cp(copied), (3209, .5))
        self.assertEqual(self.worker.recognize.call_count, 2)

    def test_candidate_change_rechecks_all_same_frame_observations_for_conflict(self):
        self.cp_text = "CP2703"
        self.refinement_tokens = ("CP2119",)
        self.assertEqual(self.reader.native_cp(self.image, expected_cps={2119}), (2119, .5))
        with patch("pokemgr.reader.screen.ocr.read_cp", return_value=(2119, .9)) as fallback:
            self.assertEqual(self.reader.read_cp(self.image, expected_cps={2119, 2703}), (-1, 0.))
            fallback.assert_not_called()
        self.worker.recognize_region.assert_called_once()

    def test_crop_conflicting_candidates_hold_without_tesseract(self):
        self.cp_text = "CP211T"
        self.refinement_tokens = ("CP2119", "2703")
        with patch("pokemgr.reader.screen.ocr.read_cp", return_value=(2119, .9)) as fallback:
            self.assertEqual(self.reader.read_cp(self.image, expected_cps={2119, 2703}), (-1, 0.))
            fallback.assert_not_called()
        self.worker.recognize_region.assert_called_once()

    def test_invalid_crop_identity_or_dimensions_cannot_be_reused(self):
        self.cp_text = "CP211T"
        self.refinement_tokens = ("CP2119",)
        for changes in ({"frame_id": "older-frame"}, {"width": 420, "height": 130}):
            with self.subTest(changes=changes):
                image = Image.new("RGB", self.image.size, "white")
                self.worker.recognize_region.side_effect = lambda frame, region, **kwargs: replace(
                    self.recognize_region(frame, region, **kwargs), **changes,
                )
                self.assertEqual(self.reader.native_cp(image, expected_cps={2119, 2703}), (-1, 0.))
                self.assertIsNone(image.info["pokemgr_native_cp_text"])
                self.assertEqual(self.reader.native_fields(image).hp, 153)

    def test_crop_still_requires_exact_tokens_inside_original_calibration(self):
        self.cp_text = "CP211T"
        for token, box in (("CP428T", (330, 120, 240, 60)),
                           ("561", (330, 120, 240, 60)),
                           ("CP5561", (285, 85, 10, 10))):
            with self.subTest(token=token, box=box):
                image = Image.new("RGB", self.image.size, "white")
                self.worker.recognize_region.side_effect = lambda frame, region, *, frame_id, mode: NativeFrameText(
                    str(frame_id), frame.width, frame.height, (TextObservation(token, .5, box),), 10.,
                )
                self.assertEqual(self.reader.native_cp(image, expected_cps={5561, 5594}), (-1, 0.))

    def test_partial_bare_cp_does_not_match_a_larger_candidate(self):
        self.cp_text = "561"
        self.assertEqual(self.reader.native_cp(self.image, expected_cps={5561, 5594}), (-1, 0.))
        with patch("pokemgr.reader.screen.ocr.read_cp", return_value=(-1, 0.)) as fallback:
            self.assertEqual(self.reader.read_cp(self.image, expected_cps={5561, 5594}), (-1, 0.))
            fallback.assert_called_once_with(self.image, self.regions.cp_region,
                                            expected_cps={5561, 5594}, fast=False)

    def test_conflicting_native_cp_cannot_be_overridden_by_another_engine(self):
        for tokens in (("CP4287", "CP4310"), ("4287", "4310")):
            with self.subTest(tokens=tokens):
                image = Image.new("RGB", self.image.size, "white")
                self.worker.recognize.side_effect = lambda frame, *, frame_id: NativeFrameText(
                    str(frame_id), frame.width, frame.height,
                    tuple(TextObservation(token, .5, (330, 120, 240, 60)) for token in tokens), 12.,
                )
                with patch("pokemgr.reader.screen.ocr.read_cp", return_value=(4287, .9)) as fallback:
                    self.assertEqual(self.reader.read_cp(image, expected_cps={4287, 4310}), (-1, 0.))
                    fallback.assert_not_called()
        self.worker.recognize_region.assert_not_called()

    def test_candidate_filter_cannot_erase_an_existing_primary_conflict(self):
        self.worker.recognize.side_effect = lambda frame, *, frame_id: NativeFrameText(
            str(frame_id), frame.width, frame.height, tuple(
                TextObservation(token, .5, (330, 120, 240, 60)) for token in ("CP4287", "CP4310")
            ), 12.,
        )
        with patch("pokemgr.reader.screen.ocr.read_cp", return_value=(4287, .9)) as fallback:
            self.assertEqual(self.reader.read_cp(self.image, expected_cps={4287}), (-1, 0.))
            fallback.assert_not_called()
        self.worker.recognize_region.assert_not_called()

    def test_native_failure_closes_worker_and_uses_existing_reader(self):
        self.worker.recognize.side_effect = NativeOCRError("timed out")
        with patch("pokemgr.reader.screen.ocr.read_hp", return_value=153) as fallback:
            self.assertEqual(self.reader.read_hp(self.image), 153)
            self.assertEqual(self.reader.read_hp(self.image), 153)
            self.assertEqual(fallback.call_count, 2)
        self.worker.recognize.assert_called_once()
        self.worker.close.assert_called_once()
        self.assertFalse(self.reader._native_enabled)

    def test_bad_geometry_is_cached_as_missing_only_on_that_image_then_next_frame_recovers(self):
        self.worker.recognize.side_effect = NativeOCRFrameError("out-of-frame observation")
        with patch("pokemgr.reader.screen.ocr.read_hp", return_value=153) as hp, \
             patch("pokemgr.reader.screen.ocr.read_cp", return_value=(-1, 0.)) as cp:
            self.assertIsNone(self.reader.native_fields(self.image))
            self.assertEqual(self.reader.read_hp(self.image), 153)
            self.assertEqual(self.reader.read_cp(self.image, expected_cps={3474}), (-1, 0.))
            self.assertIsNone(self.reader.native_fields(self.image))
            hp.assert_called_once()
            cp.assert_called_once()
        self.worker.recognize.assert_called_once()
        self.worker.recognize_region.assert_not_called()
        self.worker.close.assert_not_called()
        self.assertTrue(self.reader._native_enabled)
        self.assertNotIn("pokemgr_native_text", self.image.info)
        self.assertNotIn("pokemgr_native_cp_text", self.image.info)

        self.worker.recognize.side_effect = self.recognize
        healthy = self.image.copy()
        self.assertEqual(self.reader.read_cp(healthy), (3474, .5))
        self.assertEqual(self.worker.recognize.call_count, 2)
        self.assertIsNone(self.reader.native_fields(self.image))
        self.assertEqual(self.worker.recognize.call_count, 2)
        self.worker.close.assert_not_called()

    def test_warmup_geometry_error_does_not_disable_the_first_real_frame(self):
        self.reader._native_ocr = None

        def recognize(image, *, frame_id, mode="fast"):
            if frame_id == "warmup":
                raise NativeOCRFrameError("out-of-frame warmup observation")
            return self.recognize(image, frame_id=frame_id, mode=mode)

        self.worker.recognize.side_effect = recognize
        with patch("pokemgr.reader.native_ocr.NativeOCR", return_value=self.worker):
            self.assertEqual(self.reader.read_cp(self.image), (3474, .5))
        self.assertEqual(self.worker.recognize.call_count, 2)
        self.assertTrue(self.reader._native_enabled)
        self.worker.close.assert_not_called()

    def test_dimensions_changed_by_copy_resize_cannot_escape_as_parser_error(self):
        self.reader.native_fields(self.image)
        resized = self.image.resize((484, 1188))
        self.assertIsNone(self.reader.native_fields(resized))

    def test_appraisal_snapshot_reuses_caught_species_and_finishes_all_fields(self):
        # Exercise the real snapshot method without constructing a scanner or
        # device (which would intentionally warm OCR at construction time).
        scanner = SimpleNamespace(reader=self.reader, profile=self.profile, regions=self.regions,
                                  _reader_threads=[])
        with patch("pokemgr.reader.screen.match_species_name", side_effect=lambda text: text), \
             patch("pokemgr.reader.ocr.is_in_gym", return_value=False), \
             patch("pokemgr.reader.ocr.read_caught_species") as caught, \
             patch.object(self.reader, "read_appraisal_screen", return_value={
                 "atk": 14, "def_": 15, "sta": 14, "confidence": .95,
             }):
            detail, appraisal = IndexingStateMachine._read_appraisal_snapshot(scanner, self.image)
            caught.assert_not_called()
        self.assertEqual((detail["hp"], detail["caught_species"], detail["cp"]), (153, "Gardevoir", -1))
        self.assertTrue(detail["snapshot_read_complete"])
        self.assertEqual((appraisal["atk"], appraisal["def_"], appraisal["sta"]), (14, 15, 14))
        self.worker.recognize.assert_called_once()

    def test_hp_iv_calculation_precedes_native_cp_consumption_on_independent_frames(self):
        self.worker.recognize.side_effect = lambda image, *, frame_id: NativeFrameText(
            str(frame_id), image.width, image.height, (
                TextObservation("CP5594", .5, (330, 120, 240, 60)),
                TextObservation("174/174 HP", .5, (405, 978, 170, 24)),
                TextObservation("Zacian", .5, (350, 855, 250, 55)),
                TextObservation("This Zacian was caught", .5, (60, 2150, 800, 40)),
            ), 12.,
        )
        scanner = SimpleNamespace(reader=self.reader, profile=self.profile, regions=self.regions,
                                  use_calculated_cp=True, _abort=False, _reader_threads=[])
        species = {"zacian_crowned_sword": {"name": "Zacian (Crowned Sword)",
                    "base_atk": 332, "base_def": 240, "base_sta": 192}}
        with patch("pokemgr.pvp.resolver._default_species_map", return_value=species), \
             patch("pokemgr.reader.screen.match_species_name", side_effect=lambda text: text), \
             patch("pokemgr.reader.ocr.is_in_gym", return_value=False), \
             patch.object(self.reader, "read_cp") as cp, \
             patch.object(self.reader, "read_appraisal_screen", return_value={
                 "atk": 11, "def_": 10, "sta": 14, "confidence": .95,
             }):
            for image in (self.image, self.image.copy()):
                detail, appraisal = IndexingStateMachine._read_appraisal_snapshot(scanner, image)
                self.assertEqual(detail["cp"], -1)
                snapshot = AppraisalSnapshot.from_reads(detail, appraisal)
                decision = IndexingStateMachine._validate_appraisal_snapshot(scanner, snapshot, image)
                self.assertTrue(decision.accepted)
                self.assertEqual((decision.snapshot.cp, decision.cp_source), (5561, "calculated"))
            cp.assert_not_called()
        self.assertEqual(self.worker.recognize.call_count, 2)
        self.worker.recognize_region.assert_not_called()

    def test_calibrated_density_is_passed_to_parser(self):
        from pokemgr.reader.native_ocr import parse_appraisal_fields
        with patch("pokemgr.reader.native_ocr.parse_appraisal_fields", wraps=parse_appraisal_fields) as parser:
            self.reader.native_fields(self.image)
        self.assertEqual(parser.call_args.kwargs["density"], 420)


if __name__ == "__main__":
    unittest.main()
