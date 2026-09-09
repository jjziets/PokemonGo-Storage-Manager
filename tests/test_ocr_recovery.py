import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from pokemgr.calibration.regions import BBox
from pokemgr.reader.name_matcher import match_species_name
from pokemgr.reader.ocr import read_caught_species, read_cp, read_hp


class CpSegmentationTests(unittest.TestCase):
    @patch("pokemgr.reader.ocr_engine.read_number_paddle", return_value=4287)
    @patch("pokemgr.reader.ocr.pytesseract.image_to_string", return_value="")
    def test_fast_primary_read_stops_after_one_miss(self, tesseract, paddle):
        image = Image.new("RGB", (500, 160))

        result = read_cp(image, BBox(0, 0, 500, 160), fast=True)

        self.assertEqual((-1, 0.0), result)
        tesseract.assert_called_once()
        self.assertEqual("--psm 8", tesseract.call_args.kwargs["config"])
        paddle.assert_not_called()

    @patch("pokemgr.reader.ocr_engine.read_number_paddle", return_value=4287)
    @patch("pokemgr.reader.ocr.pytesseract.image_to_string", return_value="")
    def test_default_primary_read_keeps_all_fallbacks(self, tesseract, paddle):
        image = Image.new("RGB", (500, 160))

        result = read_cp(image, BBox(0, 0, 500, 160))

        self.assertEqual((4287, 0.9), result)
        self.assertEqual(4, tesseract.call_count)
        paddle.assert_called_once()

    @patch("pokemgr.reader.ocr.pytesseract.image_to_string",
           return_value="CP369")
    def test_cp_uses_grayscale_before_bokeh_prone_white_mask(self, ocr):
        pixels = np.zeros((160, 500, 3), dtype=np.uint8)
        pixels[:, :, 0] = np.linspace(40, 230, 500, dtype=np.uint8)
        pixels[:, :, 1] = 120
        pixels[:, :, 2] = 70

        cp, confidence = read_cp(
            Image.fromarray(pixels),
            BBox(0, 0, 500, 160),
        )

        self.assertEqual(369, cp)
        self.assertEqual(0.8, confidence)
        first_ocr_input = ocr.call_args_list[0].args[0]
        self.assertGreater(len(np.unique(first_ocr_input)), 2)

    @patch("pokemgr.reader.ocr.pytesseract.image_to_string")
    def test_cp_uses_single_word_segmentation_before_line_fallback(self, image_to_string):
        image_to_string.side_effect = lambda image, config: (
            "CP567" if "--psm 8" in config else "CP56"
        )
        image = Image.fromarray(np.zeros((160, 500, 3), dtype=np.uint8))

        cp, confidence = read_cp(image, BBox(0, 0, 500, 160))

        self.assertEqual(567, cp)
        self.assertEqual(0.8, confidence)
        self.assertIn("--psm 8", image_to_string.call_args_list[0].kwargs["config"])

    @patch("pokemgr.reader.ocr.pytesseract.image_to_string")
    def test_cp_repairs_four_misread_as_letter_inside_number(self, image_to_string):
        """Pokemon Go's thin 4 must not be discarded as a Tesseract 'A'."""
        image_to_string.side_effect = lambda image, config: "CP7A7"
        image = Image.fromarray(np.zeros((160, 500, 3), dtype=np.uint8))

        cp, confidence = read_cp(image, BBox(0, 0, 500, 160))

        self.assertEqual(747, cp)
        self.assertEqual(0.8, confidence)
        self.assertEqual("--psm 8", image_to_string.call_args_list[0].kwargs["config"])

    @patch("pokemgr.reader.ocr.pytesseract.image_to_string")
    def test_cp_label_is_not_reinterpreted_as_digits(self, image_to_string):
        image_to_string.side_effect = lambda image, config: (
            "61333" if "tessedit_char_whitelist" in config else "P1333"
        )
        image = Image.fromarray(np.zeros((160, 500, 3), dtype=np.uint8))

        cp, _confidence = read_cp(image, BBox(0, 0, 500, 160))

        self.assertEqual(1333, cp)

    @patch("pokemgr.reader.ocr.pytesseract.image_to_string")
    def test_cp_rejects_five_digit_label_artifact_and_uses_line_fallback(
            self, image_to_string):
        image_to_string.side_effect = lambda image, config: (
            "61333" if "--psm 8" in config else "CP1333"
        )
        image = Image.fromarray(np.zeros((160, 500, 3), dtype=np.uint8))

        cp, confidence = read_cp(image, BBox(0, 0, 500, 160))

        self.assertEqual(1333, cp)
        self.assertEqual(0.75, confidence)

    @patch("pokemgr.reader.ocr.pytesseract.image_to_string",
           return_value="-6p1333. &")
    def test_cp_does_not_concatenate_noise_with_real_digit_run(
            self, _image_to_string):
        image = Image.fromarray(np.zeros((160, 500, 3), dtype=np.uint8))

        cp, confidence = read_cp(image, BBox(0, 0, 500, 160))

        self.assertEqual(1333, cp)
        self.assertEqual(0.8, confidence)

    @patch("pokemgr.reader.ocr_engine.read_number_paddle", return_value=-1)
    @patch("pokemgr.reader.ocr.pytesseract.image_to_string", return_value="CPA7")
    def test_partial_letter_noise_is_not_promoted_to_valid_cp(
            self, _image_to_string, _paddle):
        image = Image.fromarray(np.zeros((160, 500, 3), dtype=np.uint8))

        cp, confidence = read_cp(image, BBox(0, 0, 500, 160))

        self.assertEqual(-1, cp)
        self.assertEqual(0.0, confidence)


class ExactCpObservationTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.fromarray(np.zeros((160, 500, 3), dtype=np.uint8))
        self.region = BBox(0, 0, 500, 160)

    @staticmethod
    def _read_sequence(image_to_string, texts):
        remaining = iter(texts)
        image_to_string.side_effect = lambda *_args, **_kwargs: next(remaining, "")

    @patch("pokemgr.reader.ocr_engine.read_text_paddle", return_value="")
    @patch("pokemgr.reader.ocr.pytesseract.image_to_string")
    def test_invalid_first_cp_does_not_hide_later_exact_observation(self, tesseract, _paddle):
        self._read_sequence(tesseract, ["CP4297", "CP4287", "", ""])

        cp, confidence = read_cp(
            self.image, self.region, expected_cps={4287, 4313},
        )

        self.assertEqual(4287, cp)
        self.assertGreater(confidence, 0.0)
        self.assertGreater(tesseract.call_count, 1)

    @patch("pokemgr.reader.ocr_engine.read_text_paddle", return_value="")
    @patch("pokemgr.reader.ocr.pytesseract.image_to_string")
    def test_live_dragonite_rotation_reads_full_cp_after_earlier_partial_noise(self, tesseract, _paddle):
        self._read_sequence(tesseract, ["4234287 ~~", "Ses 237", "24287", "4287"])

        cp, confidence = read_cp(
            self.image, self.region, expected_cps={4287, 4313},
        )

        self.assertEqual(4287, cp)
        self.assertGreater(confidence, 0.0)
        self.assertEqual(4, tesseract.call_count)

    @patch("pokemgr.reader.ocr_engine.read_text_paddle", return_value="")
    @patch("pokemgr.reader.ocr.pytesseract.image_to_string")
    def test_two_distinct_exact_observations_are_rejected_as_ambiguous(self, tesseract, _paddle):
        self._read_sequence(tesseract, ["CP4313", "CP4287", "CP4313", "CP4287"])

        cp, confidence = read_cp(
            self.image, self.region, expected_cps={4287, 4313},
        )

        self.assertEqual((-1, 0.0), (cp, confidence))

    @patch("pokemgr.reader.ocr_engine.read_text_paddle", return_value="")
    @patch("pokemgr.reader.ocr.pytesseract.image_to_string")
    def test_fast_flag_does_not_short_circuit_candidate_conflict_detection(self, tesseract, paddle):
        self._read_sequence(tesseract, ["CP4287", "", "", "CP4313"])

        result = read_cp(
            self.image, self.region, expected_cps={4287, 4313}, fast=True,
        )

        self.assertEqual((-1, 0.0), result)
        self.assertEqual(4, tesseract.call_count)
        paddle.assert_not_called()

    @patch("pokemgr.reader.ocr_engine.read_text_paddle", return_value="CP4287")
    @patch("pokemgr.reader.ocr.pytesseract.image_to_string", return_value="")
    def test_fast_flag_keeps_candidate_paddle_fallback(self, tesseract, paddle):
        result = read_cp(
            self.image, self.region, expected_cps={4287, 4313}, fast=True,
        )

        self.assertEqual((4287, 0.9), result)
        self.assertEqual(4, tesseract.call_count)
        paddle.assert_called_once()

    @patch("pokemgr.reader.ocr_engine.read_text_paddle", return_value="")
    @patch("pokemgr.reader.ocr.pytesseract.image_to_string")
    def test_partial_digit_reads_cannot_be_filled_from_expected_candidates(self, tesseract, _paddle):
        self._read_sequence(tesseract, ["CP428", "CP287", "CP431", "CP313"])

        cp, confidence = read_cp(
            self.image, self.region, expected_cps={4287, 4313},
        )

        self.assertEqual((-1, 0.0), (cp, confidence))

    @patch("pokemgr.reader.ocr_engine.read_text_paddle", return_value="")
    @patch("pokemgr.reader.ocr.pytesseract.image_to_string")
    def test_repeated_same_exact_observation_is_not_ambiguous(self, tesseract, _paddle):
        self._read_sequence(tesseract, ["CP4287", "CP4287", "", "CP4287"])

        cp, confidence = read_cp(
            self.image, self.region, expected_cps={4287, 4313},
        )

        self.assertEqual(4287, cp)
        self.assertGreater(confidence, 0.0)

    @patch("pokemgr.reader.ocr_engine.read_text_paddle", return_value="CP42 noise 87")
    @patch("pokemgr.reader.ocr.pytesseract.image_to_string", return_value="CP42 87")
    def test_separated_ocr_fragments_are_not_concatenated_into_expected_cp(self, _tesseract, paddle):
        cp, confidence = read_cp(
            self.image, self.region, expected_cps={4287, 4313},
        )

        self.assertEqual((-1, 0.0), (cp, confidence))
        paddle.assert_called_once()

    @patch("pokemgr.reader.ocr_engine.read_number_paddle", return_value=-1)
    @patch("pokemgr.reader.ocr.pytesseract.image_to_string", return_value="CP4297")
    def test_unconstrained_read_keeps_the_existing_single_read_fast_path(self, tesseract, paddle):
        cp, confidence = read_cp(self.image, self.region)

        self.assertEqual((4297, 0.8), (cp, confidence))
        tesseract.assert_called_once()
        paddle.assert_not_called()


class TintedHpTests(unittest.TestCase):
    @patch("pokemgr.reader.ocr.pytesseract.image_to_string", return_value="90/90 ~~")
    def test_tinted_tablet_appraisal_bar_accepts_anchored_equal_pair(self, _ocr):
        pixels = np.full((2304, 1440, 3), 255, dtype=np.uint8)
        pixels[1269:1275, int(1440 * 0.20):int(1440 * 0.80)] = (160, 226, 156)
        image = Image.fromarray(pixels)

        self.assertEqual(90, read_hp(image, 1440, 2304))


class CaughtSpeciesTests(unittest.TestCase):
    @patch("pokemgr.reader.ocr.pytesseract.image_to_string")
    def test_caught_species_keeps_unicode_and_punctuation(self, tesseract):
        image = Image.new("RGB", (968, 2376), "white")
        for text, expected in (
            ("This Type: Null was caught today", "Type: Null"),
            ("This Nidoran♀ was caught today", "Nidoran♀"),
            ("This Nidoran♂ was caught today", "Nidoran♂"),
            ("This Flabébé was caught today", "Flabébé"),
        ):
            with self.subTest(text=text):
                tesseract.return_value = text
                self.assertEqual(
                    expected,
                    read_caught_species(image, 968, 2376, density=420),
                )

    class _Paddle:
        def __init__(self):
            self.crop_height = 0

        def ocr(self, crop):
            self.crop_height = crop.shape[0]
            return [{"rec_texts": [
                "This", "Abomasnow was", "caught on 2025/06/05",
            ]}]

    @patch("pokemgr.reader.ocr.pytesseract.image_to_string", return_value="")
    @patch("pokemgr.reader.ocr_engine._get_paddle")
    def test_tablet_bubble_keeps_this_species_line_and_joins_ocr_items(
            self, get_paddle, _tesseract):
        paddle = self._Paddle()
        get_paddle.return_value = paddle
        image = Image.fromarray(np.zeros((2304, 1440, 3), dtype=np.uint8))

        species = read_caught_species(image, 1440, 2304)

        self.assertEqual("Abomasnow", species)
        self.assertGreater(paddle.crop_height, 220)

    @patch("pokemgr.reader.ocr_engine._get_paddle")
    @patch("pokemgr.reader.ocr.pytesseract.image_to_string",
           return_value="This Abra was caught on 2025/09/16 around")
    def test_clean_bubble_uses_fast_tesseract_path(self, _tesseract, get_paddle):
        image = Image.fromarray(np.zeros((2304, 1440, 3), dtype=np.uint8))

        species = read_caught_species(image, 1440, 2304)

        self.assertEqual("Abra", species)
        get_paddle.assert_not_called()

    @patch("pokemgr.reader.ocr_engine._get_paddle")
    @patch("pokemgr.reader.ocr.pytesseract.image_to_string")
    def test_phone_bubble_crop_keeps_authoritative_species_line(
            self, image_to_string, get_paddle):
        crop_heights = []

        def _read(crop, config):
            crop_heights.append(crop.shape[0])
            return "This Zygarde was caught on 2023/08/25 around"

        image_to_string.side_effect = _read
        image = Image.fromarray(np.zeros((2376, 968, 3), dtype=np.uint8))

        species = read_caught_species(image, 968, 2376, density=420)

        self.assertEqual("Zygarde", species)
        self.assertGreater(crop_heights[0], 400)
        get_paddle.assert_not_called()


class SpeciesNameSafetyTests(unittest.TestCase):
    def tearDown(self):
        match_species_name.cache_clear()

    @patch("pokemgr.reader.name_matcher._load_species_names",
           return_value=["Corphish", "Electabuzz", "Keldeo", "Mew"])
    def test_corrects_close_ocr_but_not_unrelated_nickname(self, _names):
        self.assertEqual("Corphish", match_species_name("Corvhish"))
        self.assertEqual("Best Zizle", match_species_name("Best Zizle"))
        self.assertEqual("Ke", match_species_name("Ke"))
        self.assertEqual("Mew", match_species_name("Mew"))


if __name__ == "__main__":
    unittest.main()
