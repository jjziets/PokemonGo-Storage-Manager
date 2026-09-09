"""Power Up evidence must never confuse preview confirmation with navigation."""

from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from pokemgr.reader.powerup import (
    PowerUpPreview, _Word, _cp_pair, find_powerup_button, read_powerup_preview,
    read_powerup_resource_prompt, has_detail_menu,
)


def word(text, x, y, width=80, height=28, line=1, confidence=95):
    return _Word(text, confidence, (x, y, x+width, y+height), (1, 1, line))


def preview_words(name="WEEZING"):
    return [word("POWER", 258, 1601, 137), word("UP:", 407, 1601, 58),
            word(name, 477, 1601, 176), word("CANCEL", 402, 2233, 162, line=2)]


def resource_words(name="Zygarde"):
    return [
        word("Transform", 202, 1598, 192), word("a", 406, 1608, 20),
        word("Rare", 439, 1599, 82), word("Candy", 532, 1598, 118),
        word("into", 662, 1598, 72, confidence=28),
        word("a", 745, 1608, 20, confidence=28),
        word(name, 328, 1661, 152, line=2), word("Candy?", 492, 1661, 136, line=2),
        word("2", 457, 1754, 22, line=3), word("3", 587, 1754, 22, line=3),
        word("CANCEL", 402, 2233, 162, line=4),
    ]


def cp_image(arrow=True):
    image = Image.new("RGB", (968, 2376), "white")
    draw = ImageDraw.Draw(image)
    for x in (290, 325, 365, 395, 425, 455):
        draw.rectangle((x, 1708, x+18, 1743), fill=(80, 120, 120))
    if arrow:
        points = [(0, 11), (31, 11), (26, 4), (30, 0), (42, 13),
                  (30, 26), (26, 22), (31, 15), (0, 15)]
        draw.polygon([(494+x, 1714+y) for x, y in points], fill=(80, 120, 120))
    for x in (550, 580, 610, 640):
        draw.rectangle((x, 1708, x+18, 1743), fill=(250, 150, 40))
    return image


class PowerUpReaderTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.new("RGB", (968, 2376), "white")
        ImageDraw.Draw(self.image).rectangle((70, 1490, 450, 1620), fill=(80, 210, 140))

    @patch("pokemgr.reader.powerup._words")
    def test_detail_button_target_comes_from_unique_green_left_column_label(self, words):
        words.return_value = [word("POWER", 156, 1540, 142), word("UP", 314, 1540, 49)]
        self.assertEqual((259, 1554), find_powerup_button(self.image))

    @patch("pokemgr.reader.powerup._words")
    def test_preview_heading_disqualifies_even_a_left_column_power_up_label(self, words):
        words.return_value = preview_words() + [
            word("POWER", 156, 1540, 142, line=3), word("UP", 314, 1540, 49, line=3),
        ]
        self.assertIsNone(find_powerup_button(self.image))

    @patch("pokemgr.reader.powerup._words")
    def test_confirmation_button_cannot_be_opened_when_heading_ocr_is_missing(self, words):
        words.return_value = [word("POWER", 380, 2110, 142), word("UP", 538, 2110, 49)]
        self.assertIsNone(find_powerup_button(self.image))

    @patch("pokemgr.reader.powerup._words")
    def test_non_green_low_confidence_and_ambiguous_buttons_are_rejected(self, words):
        first = [word("POWER", 156, 1540, 142), word("UP", 314, 1540, 49)]
        words.return_value = first
        self.assertIsNone(find_powerup_button(Image.new("RGB", self.image.size, "white")))
        words.return_value = [word("POWER", 156, 1540, 142, confidence=40), first[1]]
        self.assertIsNone(find_powerup_button(self.image))
        words.return_value = first + [word("POWER", 156, 1580, 142, line=2),
                                      word("UP", 314, 1580, 49, line=2)]
        self.assertIsNone(find_powerup_button(self.image))

    @patch("pokemgr.reader.powerup._cp_pair", return_value=(2178, 2194))
    @patch("pokemgr.reader.powerup._words", return_value=preview_words())
    def test_exact_known_name_and_cancel_identify_preview(self, _words, _cp):
        self.assertEqual(PowerUpPreview(2178, 2194, (483, 2247)),
                         read_powerup_preview(self.image, "  weezing  "))

    @patch("pokemgr.reader.powerup._cp_pair", return_value=None)
    @patch("pokemgr.reader.powerup._words", return_value=preview_words("EXEGGUTOR"))
    def test_caught_species_alias_is_exact_and_retains_cancel_without_cp(self, _words, _cp):
        self.assertEqual(PowerUpPreview(None, None, (483, 2247)),
                         read_powerup_preview(self.image, ("Exeggeutor", "Exeggutor")))
        self.assertIsNone(read_powerup_preview(self.image, "Exeggeutor"))

    @patch("pokemgr.reader.powerup._cp_pair")
    @patch("pokemgr.reader.powerup._words")
    def test_wrong_or_missing_heading_cancel_and_bad_geometry_reject(self, words, cp):
        valid = preview_words()
        cases = [
            preview_words("KOFFING"), valid[1:], valid[:-1],
            valid + [word("CANCEL", 402, 2280, 162, line=3)],
            valid[:-1] + [word("CANCEL", 402, 2000, 162, line=2)],
            valid[:-1] + [word("CANCEL", 800, 2233, 162, line=2)],
            [word("POWER", 258, 1400, 137), word("UP:", 407, 1400, 58),
             word("WEEZING", 477, 1400, 176), valid[-1]],
        ]
        for observed in cases:
            with self.subTest(words=observed):
                words.return_value = observed
                self.assertIsNone(read_powerup_preview(self.image, "Weezing"))
        cp.assert_not_called()

    @patch("pokemgr.reader.powerup._cp_pair", side_effect=RuntimeError("OCR failed"))
    @patch("pokemgr.reader.powerup._words", return_value=preview_words())
    def test_cp_failure_preserves_only_verified_cancel(self, _words, _cp):
        self.assertEqual(PowerUpPreview(None, None, (483, 2247)),
                         read_powerup_preview(self.image, "Weezing"))

    @patch("pokemgr.reader.powerup.pytesseract.image_to_string")
    def test_arrow_separates_labeled_current_from_orange_future(self, ocr):
        ocr.side_effect = ["CP 2178", "CP2178", "2194", "2194"]
        self.assertEqual((2178, 2194), _cp_pair(cp_image(), 1629))
        self.assertEqual(4, ocr.call_count)

    @patch("pokemgr.reader.powerup.pytesseract.image_to_string")
    def test_partial_conflicting_unlabeled_or_nonincreasing_cp_is_rejected(self, ocr):
        for readings in (
            ["CP21 78", "CP21 noise78"], ["CP2178", "CP2161"],
            ["2178", "2178"], ["CP2194", "CP2194", "2178", "2178"],
            ["CP2178", "CP2178", "2178", "2178"],
            ["CP2178", "CP2178", "21 94", "21noise94"],
            ["CP2178", "CP2178", "2194", "2210"],
        ):
            with self.subTest(readings=readings):
                ocr.side_effect = readings
                self.assertIsNone(_cp_pair(cp_image(), 1629))

    @patch("pokemgr.reader.powerup.pytesseract.image_to_string")
    def test_missing_arrow_rejects_cp_without_ocr(self, ocr):
        self.assertIsNone(_cp_pair(cp_image(arrow=False), 1629))
        ocr.assert_not_called()


class PowerUpResourcePromptTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.new("RGB", (968, 2376), "white")

    @patch("pokemgr.reader.powerup._cp_pair")
    @patch("pokemgr.reader.powerup._words", return_value=resource_words())
    def test_exact_named_prompt_exposes_cancel_without_cp_parsing(self, _words, cp):
        self.assertEqual((483, 2247), read_powerup_resource_prompt(
            self.image, ("Zygarde (Complete)", " zygarde "),
        ))
        self.assertIsNone(read_powerup_preview(self.image, "Zygarde"))
        self.assertIsNone(find_powerup_button(self.image))
        cp.assert_not_called()

    @patch("pokemgr.reader.powerup._words", return_value=resource_words())
    def test_unknown_name_and_empty_expected_names_reject(self, _words):
        for expected in ("Weezing", "Zygard", "", ()):
            with self.subTest(expected=expected):
                self.assertIsNone(read_powerup_resource_prompt(self.image, expected))

    @patch("pokemgr.reader.powerup._words")
    def test_unknown_incomplete_or_low_confidence_prompt_rejects(self, words):
        valid = resource_words()
        for prompt in (
            valid[1:],
            [word("Transfer", 202, 1598, 192)] + valid[1:],
            valid[:2] + [word("XL", 439, 1599, 82)] + valid[3:],
            valid[:6] + [word("Zygarde", 328, 1661, 152, line=2, confidence=30)] + valid[7:],
            valid[:7] + [word("Candy", 492, 1661, 136, line=2)] + valid[8:],
            preview_words("ZYGARDE"),
        ):
            with self.subTest(prompt=prompt):
                words.return_value = prompt
                self.assertIsNone(read_powerup_resource_prompt(self.image, "Zygarde"))

    @patch("pokemgr.reader.powerup._words")
    def test_missing_ambiguous_or_misplaced_cancel_rejects(self, words):
        valid = resource_words()
        for prompt in (
            valid[:-1],
            valid + [word("CANCEL", 402, 2280, 162, line=5)],
            valid[:-1] + [word("CANCEL", 402, 2100, 162, line=4)],
            valid[:-1] + [word("OK", 402, 2233, 162, line=4)],
        ):
            with self.subTest(prompt=prompt):
                words.return_value = prompt
                self.assertIsNone(read_powerup_resource_prompt(self.image, "Zygarde"))

    @patch("pokemgr.reader.powerup._words", side_effect=RuntimeError("OCR failed"))
    def test_ocr_failure_never_provides_a_target(self, _words):
        self.assertIsNone(read_powerup_resource_prompt(self.image, "Zygarde"))


class DetailMenuTests(unittest.TestCase):
    @staticmethod
    def menu_image(shape="circle", offsets=(-19, 0, 19), vertical=False,
                   fill=(30, 130, 150)):
        image = Image.new("RGB", (968, 2376), "white")
        draw = ImageDraw.Draw(image)
        if shape == "circle":
            draw.ellipse((755, 2170, 915, 2330), fill=fill)
        elif shape == "square":
            draw.rectangle((755, 2170, 915, 2330), fill=fill)
        else:
            draw.ellipse((755, 2200, 915, 2300), fill=fill)
        for offset in offsets:
            box = ((835+offset-5, 2219, 835+offset+5, 2281) if vertical else
                   (804, 2250+offset-5, 866, 2250+offset+5))
            draw.rectangle(box, fill=(110, 240, 160))
        return image

    def test_teal_disk_with_three_bars_contains_calibrated_tap(self):
        image = self.menu_image()
        self.assertTrue(has_detail_menu(image, (835, 2250)))
        self.assertTrue(has_detail_menu(image, (910, 2260)))
        self.assertFalse(has_detail_menu(image, (940, 2260)))
        self.assertFalse(has_detail_menu(image, (484, 2250)))

    def test_wrong_outline_or_color_is_not_the_detail_menu(self):
        for image in (self.menu_image(shape="square"), self.menu_image(shape="oval"),
                      self.menu_image(fill=(230, 230, 230)),
                      self.menu_image(fill=(110, 240, 160))):
            self.assertFalse(has_detail_menu(image, (835, 2250)))

    def test_missing_vertical_or_uneven_bars_reject(self):
        for image in (self.menu_image(offsets=()), self.menu_image(offsets=(-19, 19)),
                      self.menu_image(offsets=(-28, -9, 10, 29)),
                      self.menu_image(vertical=True), self.menu_image(offsets=(-30, 0, 17))):
            self.assertFalse(has_detail_menu(image, (835, 2250)))

    def test_covered_dimmed_or_partially_clipped_button_rejects(self):
        covered = self.menu_image()
        ImageDraw.Draw(covered).rectangle((0, 2235, 968, 2376), fill="white")
        dimmed = self.menu_image().point(lambda value: value // 2)
        for image in (covered, dimmed, Image.new("RGB", (968, 2376), "black")):
            self.assertFalse(has_detail_menu(image, (835, 2250)))

    def test_real_detail_and_dialog_frames(self):
        root = Path(__file__).resolve().parents[1]
        cases = {
            "cache/preview-zygarde/after-resource-cancel.png": True,
            "cache/skipped-replay/weezing-low-detail.png": True,
            "cache/preview-zygarde/confirmed-resource-prompt.png": False,
            "cache/skipped-replay/weezing-low-powerup-preview.png": False,
        }
        if not all((root / name).exists() for name in cases):
            self.skipTest("Local live detail/preview captures are unavailable")
        for name, expected in cases.items():
            with self.subTest(image=name), Image.open(root / name) as image:
                self.assertEqual(expected, has_detail_menu(image, (910, 2260)))


FIXTURES = Path(__file__).resolve().parents[1] / "cache/skipped-replay"


@unittest.skipUnless(shutil.which("tesseract"), "Tesseract is unavailable")
class LivePowerUpImageTests(unittest.TestCase):
    def test_saved_zygarde_resource_prompt_is_cancel_only_and_name_bound(self):
        path = Path(__file__).resolve().parents[1] / "cache/private-fixtures/zygarde_rare_candy_prompt.png"
        if not path.exists():
            self.skipTest("Local resource-dialog capture is unavailable")
        # Preserve the original screenshot geometry while storing only the
        # relevant lower crop. No OCR text or dialog pixels are synthesized.
        image = Image.new("RGB", (968, 2376), "white")
        with Image.open(path) as crop:
            image.paste(crop, (0, 1354))
        self.assertEqual((483, 2246), read_powerup_resource_prompt(image, "Zygarde"))
        self.assertIsNone(read_powerup_resource_prompt(image, "Weezing"))
        self.assertIsNone(read_powerup_preview(image, "Zygarde"))
        self.assertIsNone(find_powerup_button(image))
        self.assertFalse(has_detail_menu(image, (910, 2260)))

    def test_saved_weezing_images_read_current_cp_and_never_confirm(self):
        detail = FIXTURES / "weezing-low-detail.png"
        preview = FIXTURES / "weezing-low-powerup-preview.png"
        if not detail.exists() or not preview.exists():
            self.skipTest("Local live capture fixtures are unavailable")
        with Image.open(detail) as image:
            target = find_powerup_button(image)
            self.assertIsNotNone(target)
            self.assertTrue(150 < target[0] < 365 and 1490 < target[1] < 1620)
            self.assertIsNone(read_powerup_preview(image, "Weezing"))
        with Image.open(preview) as image:
            self.assertIsNone(read_powerup_resource_prompt(image, "Weezing"))
            self.assertIsNone(find_powerup_button(image))
            evidence = read_powerup_preview(image, "Weezing")
            self.assertIsNotNone(evidence)
            self.assertEqual((2178, 2194), (evidence.current_cp, evidence.next_cp))
            self.assertTrue(400 < evidence.cancel_target[0] < 565)
            self.assertTrue(2210 < evidence.cancel_target[1] < 2280)


if __name__ == "__main__":
    unittest.main()
