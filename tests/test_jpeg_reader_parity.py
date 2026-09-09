"""Quality-100 JPEG must preserve scanner evidence on actual saved pixels.

JPEG uses 4:2:0 subsampling, matching the observed Android screencap output.
Local full-screen regressions skip when their diagnostic captures are absent;
the checked-in star fixtures remain independently useful.
"""

from dataclasses import asdict
from io import BytesIO
from pathlib import Path
import os
import shutil
import unittest

from PIL import Image, ImageDraw

from pokemgr.calibration.profile import CalibrationProfile
from pokemgr.calibration.regions import BBox
from pokemgr.reader import bars, ocr
from pokemgr.reader.icons import favorite_state
from pokemgr.reader.powerup import (
    find_powerup_button, has_detail_menu, read_powerup_preview,
    read_powerup_resource_prompt,
)
from pokemgr.reader.screen import ScreenReader


ROOT = Path(__file__).resolve().parents[1]
PROFILE = Path(os.environ.get("POKEMGR_TEST_PROFILE", ROOT / "cache/test-profile.json"))
FIXTURES = ROOT / "tests/fixtures/reader"
PARITY_OBSERVATIONS = []


def jpeg100(image):
    encoded = BytesIO()
    image.convert("RGB").save(encoded, format="JPEG", quality=100, subsampling=2)
    encoded.seek(0)
    with Image.open(encoded) as decoded:
        return decoded.convert("RGB")


class JpegFavoriteParityTests(unittest.TestCase):
    def test_real_on_and_off_glyphs_keep_their_affirmative_state(self):
        region = BBox(55, 10, 85, 85)
        for state in ("on", "off"):
            with self.subTest(state=state), Image.open(FIXTURES / f"favorite_{state}.png") as image:
                actual = favorite_state(jpeg100(image), region)
                PARITY_OBSERVATIONS.append({"fixture": f"favorite_{state}", "expected": state, "jpeg": actual})
                self.assertEqual(state, favorite_state(image, region))
                self.assertEqual(state, actual)

    def test_absent_and_occluded_stars_remain_unknown(self):
        region = BBox(55, 10, 85, 85)
        with Image.open(FIXTURES / "favorite_on.png") as source:
            occluded = source.convert("RGB")
        ImageDraw.Draw(occluded).rectangle((0, 0, 100, occluded.height), fill="white")
        for image in (Image.new("RGB", occluded.size, "white"),
                      Image.new("RGB", occluded.size, "black"), occluded):
            with self.subTest(pixel=image.getpixel((0, 0))):
                self.assertEqual("unknown", favorite_state(image, region))
                self.assertEqual("unknown", favorite_state(jpeg100(image), region))


@unittest.skipUnless(shutil.which("tesseract"), "Tesseract is unavailable")
class JpegDialogParityTests(unittest.TestCase):
    def test_real_resource_dialog_stays_cancel_only_and_name_bound(self):
        path = ROOT / "cache/private-fixtures/zygarde_rare_candy_prompt.png"
        if not path.exists():
            self.skipTest("Local resource-dialog capture is unavailable")
        image = Image.new("RGB", (968, 2376), "white")
        with Image.open(path) as crop:
            image.paste(crop, (0, 1354))
        expected = read_powerup_resource_prompt(image, "Zygarde")
        decoded = jpeg100(image)
        actual = read_powerup_resource_prompt(decoded, "Zygarde")
        PARITY_OBSERVATIONS.append({"fixture": "zygarde_resource", "png": expected, "jpeg": actual})
        self.assertIsNotNone(expected)
        self.assertIsNotNone(actual)
        # JPEG shifts the same recognized CANCEL glyph's center by one pixel.
        # This checks target parity without changing any recognition threshold.
        self.assertLessEqual(max(abs(a-b) for a, b in zip(expected, actual)), 1)
        self.assertIsNone(read_powerup_resource_prompt(decoded, "Weezing"))
        self.assertIsNone(read_powerup_preview(decoded, "Zygarde"))
        self.assertFalse(has_detail_menu(decoded, (910, 2260)))

    def test_real_powerup_preview_keeps_current_and_future_cp_distinct(self):
        path = ROOT / "cache/skipped-replay/weezing-low-powerup-preview.png"
        if not path.exists():
            self.skipTest("Local power-up capture is unavailable")
        with Image.open(path) as image:
            original = read_powerup_preview(image, "Weezing")
            decoded = jpeg100(image)
        actual = read_powerup_preview(decoded, "Weezing")
        PARITY_OBSERVATIONS.append({
            "fixture": str(path.relative_to(ROOT)),
            "png": asdict(original) if original else None,
            "jpeg": asdict(actual) if actual else None,
        })
        self.assertIsNotNone(original)
        self.assertEqual((2178, 2194), (original.current_cp, original.next_cp))
        self.assertEqual(original, actual)
        self.assertIsNone(find_powerup_button(decoded))
        self.assertFalse(has_detail_menu(decoded, (910, 2260)))

    def test_real_detail_menu_survives_and_does_not_become_a_dialog(self):
        path = ROOT / "cache/skipped-replay/weezing-low-detail.png"
        if not path.exists():
            self.skipTest("Local detail capture is unavailable")
        with Image.open(path) as image:
            original = find_powerup_button(image)
            decoded = jpeg100(image)
        actual = find_powerup_button(decoded)
        PARITY_OBSERVATIONS.append({"fixture": "weezing_detail_opener", "png": original, "jpeg": actual})
        self.assertIsNotNone(original)
        self.assertEqual(original, actual)
        self.assertTrue(has_detail_menu(decoded, (910, 2260)))


@unittest.skipUnless(shutil.which("tesseract") and PROFILE.exists(), "Local scanner calibration or Tesseract is unavailable")
class JpegAppraisalParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = CalibrationProfile.load(str(PROFILE))
        cls.reader = ScreenReader(cls.profile, fast_cp=True, read_size_tags=False)

    def evidence(self, image):
        detail = self.reader.read_detail_screen(image, include_cp=False)
        return {
            "detail": detail,
            "hp": self.reader.read_hp(image),
            "caught": ocr.read_caught_species(image, image.width, image.height, density=420),
            "iv": self.reader.read_appraisal_screen(image),
            "bars": bars.are_bars_present(image),
            "favorite": favorite_state(image, self.profile.regions.favorite_star_region),
            "detail_menu": has_detail_menu(image, self.profile.regions.menu_button),
        }

    def test_saved_appraisals_preserve_all_identity_and_pixel_metadata(self):
        fixtures = (
            ("cache/preview-zygarde/fast-calculated-appraisal.png", "Zygarde", 264, (15, 12, 11)),
            ("cache/dragonite/verified-appraisal.png", "Dragonite", 188, (15, 15, 15)),
            ("cache/skipped-replay/tyranitar-4099-verified.png", "Tyranitar", 195, (13, 14, 13)),
        )
        tested = 0
        for name, species, hp, ivs in fixtures:
            if not (ROOT / name).exists():
                continue
            tested += 1
            with self.subTest(fixture=name), Image.open(ROOT / name) as image:
                original, decoded = self.evidence(image), self.evidence(jpeg100(image))
                PARITY_OBSERVATIONS.append({"fixture": name, "png": original, "jpeg": decoded})
                self.assertEqual(species, original["caught"])
                self.assertEqual(hp, original["hp"])
                self.assertEqual(ivs, tuple(original["iv"][key] for key in ("atk", "def_", "sta")))
                self.assertTrue(original["bars"])
                self.assertEqual(original, decoded)
        if not tested:
            self.skipTest("Local real appraisal captures are unavailable")

    def test_exact_cp_candidates_remain_the_same_after_encoding(self):
        fixtures = (
            ("cache/dragonite-rotated-animation/rotation-before-tap.png", {4287, 4313}),
            ("cache/skipped-replay/tyranitar-4099-verified.png", {4099}),
        )
        tested = 0
        for name, candidates in fixtures:
            if not (ROOT / name).exists():
                continue
            tested += 1
            with self.subTest(fixture=name), Image.open(ROOT / name) as image:
                original = self.reader.read_cp(image, expected_cps=candidates)[0]
                decoded = self.reader.read_cp(jpeg100(image), expected_cps=candidates)[0]
                PARITY_OBSERVATIONS.append({"fixture": name, "candidates": sorted(candidates), "png_cp": original, "jpeg_cp": decoded})
                self.assertIn(original, candidates)
                self.assertEqual(original, decoded)
        if not tested:
            self.skipTest("Local real CP captures are unavailable")

    def test_native_android_jpeg_pairs_preserve_complete_evidence(self):
        tested = 0
        for index in range(3):
            base = ROOT / "cache/scan-speed-v2"
            png_path, jpeg_path = base / f"capture-{index}-p.png", base / f"capture-{index}-j.jpeg"
            if not png_path.exists() or not jpeg_path.exists():
                continue
            tested += 1
            with self.subTest(pair=index), Image.open(png_path) as png, Image.open(jpeg_path) as jpeg:
                original, native = self.evidence(png), self.evidence(jpeg)
                original["cp"] = self.reader.read_cp(png, fast=True)[0]
                native["cp"] = self.reader.read_cp(jpeg, fast=True)[0]
                PARITY_OBSERVATIONS.append({"fixture": f"native_pair_{index}", "png": original, "jpeg": native})
                self.assertTrue(original["bars"])
                self.assertGreater(original["cp"], 0)
                self.assertGreater(original["hp"], 0)
                self.assertTrue(original["caught"])
                self.assertEqual(original, native)
        if not tested:
            self.skipTest("Local native Android PNG/JPEG pairs are unavailable")


if __name__ == "__main__":
    unittest.main()
