"""ScreenReader facade — combines OCR, bar reading, and icon detection."""

import logging
import os
import time
import weakref
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image

from ..calibration.profile import CalibrationProfile
from .. import timing
from ..config import (
    SCREEN_STABLE_THRESHOLD,
    SCREEN_STABLE_MAX_WAIT,
    SCREEN_STABLE_POLL,
    SCREENSHOTS_DIR,
)
import numpy as np

from . import ocr, bars
from . import icons
from .gender import detect_gender as _detect_gender
from .name_matcher import match_species_name

log = logging.getLogger(__name__)


@dataclass
class PokemonRead:
    """Result of reading a single Pokemon's stats from the screen."""
    species: str            # real species (from fingerprint or OCR)
    cp: int
    atk: int
    def_: int
    sta: int
    shiny: bool
    shadow: bool
    favorited: bool
    lucky: bool
    display_name: str = ""  # what's shown on screen (could be nickname)
    gender: str = "none"        # "male", "female", or "none"
    weight_tag: str = ""        # "LIGHTEST", "HEAVIEST", or ""
    height_tag: str = ""        # "SHORTEST", "TALLEST", or ""
    is_dynamax: bool = False    # CP > 10000 indicates Dynamax/Gmax
    hp: int = -1                # max HP from "### / ### HP"
    confidence: float = 0.0     # 0.0-1.0, aggregated
    screenshot_path: str = ""   # saved for manual review

    @property
    def iv_total(self) -> int:
        return self.atk + self.def_ + self.sta

    @property
    def iv_pct(self) -> float:
        return self.iv_total / 45.0

    def to_dict(self) -> dict:
        return {
            "species": self.species,
            "cp": self.cp,
            "atk": self.atk,
            "def": self.def_,
            "sta": self.sta,
            "iv_total": self.iv_total,
            "iv_pct": round(self.iv_pct, 4),
            "shiny": self.shiny,
            "shadow": self.shadow,
            "favorited": self.favorited,
            "lucky": self.lucky,
            "gender": self.gender,
            "weight_tag": self.weight_tag,
            "height_tag": self.height_tag,
            "is_dynamax": self.is_dynamax,
            "confidence": round(self.confidence, 3),
        }

    def summary(self) -> str:
        flags = []
        if self.shiny:
            flags.append("Shiny")
        if self.shadow:
            flags.append("Shadow")
        if self.lucky:
            flags.append("Lucky")
        if self.favorited:
            flags.append("Fav")
        if self.gender != "none":
            flags.append(self.gender[0].upper())  # M or F
        if self.is_dynamax:
            flags.append("Dmax")
        if self.weight_tag:
            flags.append(self.weight_tag)
        if self.height_tag:
            flags.append(self.height_tag)
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        return (
            f"{self.species} | CP {self.cp} | "
            f"{self.atk}/{self.def_}/{self.sta} "
            f"({self.iv_pct:.0%}){flag_str} "
            f"[conf: {self.confidence:.0%}]"
        )


class ScreenReader:
    """Reads Pokemon data from phone screenshots using calibrated regions."""

    def __init__(self, profile: CalibrationProfile, *, fast_cp: bool = False,
                 read_size_tags: bool = True):
        self.regions = profile.regions
        self._density = profile.density
        self._save_screenshots = True
        self._fast_cp = fast_cp
        self._read_size_tags = read_size_tags
        self._native_enabled = os.environ.get("POKEMGR_NATIVE_OCR") == "1"
        self._native_ocr = None

    def prepare_native_ocr(self):
        """Start and warm the native worker before measuring the scan."""
        if not self._native_enabled or self._native_ocr is not None:
            return
        from .native_ocr import NativeOCR, NativeOCRError, NativeOCRFrameError
        worker = NativeOCR()
        try:
            worker.start()
            worker.recognize(Image.new("RGB", (64, 64), "white"), frame_id="warmup")
        except NativeOCRFrameError as exc:
            log.warning("Native OCR warmup text was unusable; awaiting the first real frame: %s", exc)
            self._native_ocr = worker
        except NativeOCRError as exc:
            worker.close()
            self._native_enabled = False
            log.warning("Native OCR unavailable; using existing OCR: %s", exc)
        else:
            self._native_ocr = worker

    @timing.timed("reader.native_frame")
    def native_fields(self, image):
        """Recognize all text once, cached only on this exact image object."""
        if not self._native_enabled:
            return None
        key = "pokemgr_native_fields"
        owner = image.info.get("pokemgr_native_owner")
        if (key in image.info and isinstance(owner, tuple) and len(owner) == 3
                and isinstance(owner[0], weakref.ReferenceType) and owner[0]() is self
                and isinstance(owner[1], weakref.ReferenceType) and owner[1]() is image
                and owner[2] == image.size):
            return image.info[key]
        # Pillow copies info when images are copied/cropped/resized. Evidence
        # attached to the source object cannot authorize those new pixels.
        image.info.pop(key, None)
        image.info.pop("pokemgr_native_text", None)
        image.info.pop("pokemgr_native_cp_text", None)
        self.prepare_native_ocr()
        if self._native_ocr is None:
            return None
        from .native_ocr import NativeOCRError, NativeOCRFrameError, parse_appraisal_fields
        try:
            result = self._native_ocr.recognize(image, frame_id=str(id(image)))
            image.info["pokemgr_native_text"] = result
            fields = parse_appraisal_fields(result, self.regions, density=self._density)
        except NativeOCRFrameError as exc:
            log.warning("Native OCR rejected this frame; using existing OCR for it: %s", exc)
            fields = None
        except (NativeOCRError, ValueError) as exc:
            log.warning("Native OCR frame failed; using existing OCR: %s", exc)
            self.close()
            self._native_enabled = False
            fields = None
        image.info[key] = fields
        # Integer object IDs can be recycled after the source image dies while
        # a Pillow copy still carries its info. Weak references prove that the
        # live source object itself owns this evidence without keeping it alive.
        image.info["pokemgr_native_owner"] = (weakref.ref(self), weakref.ref(image), image.size)
        return fields

    def _native_cp_refinement(self, image, raw):
        """One accurate, contextual CP crop; keep its evidence separate."""
        key = "pokemgr_native_cp_text"
        if key in image.info:
            return image.info[key]
        from ..calibration.regions import BBox
        from .native_ocr import NativeFrameText, NativeOCRError
        region = self.regions.cp_region
        pad_x = max(1, round(20 * image.width / 968))
        pad_y = max(1, round(20 * image.height / 2376))
        x, y = max(0, region.x-pad_x), max(0, region.y-pad_y)
        x2, y2 = min(image.width, region.x2+pad_x), min(image.height, region.y2+pad_y)
        try:
            result = self._native_ocr.recognize_region(
                image, BBox(x, y, x2-x, y2-y), frame_id=raw.frame_id, mode="accurate",
            )
            if (not isinstance(result, NativeFrameText) or result.frame_id != raw.frame_id
                    or (result.width, result.height) != image.size):
                raise NativeOCRError("Native CP refinement belongs to a different frame")
        except (NativeOCRError, ValueError) as exc:
            log.warning("Native CP crop failed; retaining only the original frame text: %s", exc)
            result = None
        image.info[key] = result
        return result

    def native_cp(self, image, *, expected_cps=None):
        """Return a same-frame native observation without expensive OCR fallback."""
        fields = self.native_fields(image)
        if fields is not None and fields.cp_conflict:
            return -1, -1.0
        raw = image.info.get("pokemgr_native_text")
        if fields is not None and raw is not None and expected_cps is not None:
            from .native_ocr import parse_appraisal_fields
            refinement = image.info.get("pokemgr_native_cp_text")
            observed = (replace(raw, observations=raw.observations + refinement.observations)
                        if refinement is not None else raw)
            fields = parse_appraisal_fields(
                observed, self.regions, density=self._density, expected_cps=expected_cps,
            )
            if fields.cp <= 0 and not fields.cp_conflict and expected_cps and self._native_ocr is not None:
                refinement = self._native_cp_refinement(image, raw)
                if refinement is not None:
                    fields = parse_appraisal_fields(
                        replace(raw, observations=raw.observations + refinement.observations),
                        self.regions, density=self._density, expected_cps=expected_cps,
                    )
        if fields is not None and fields.cp_conflict:
            return -1, -1.0
        if (fields is not None and fields.cp > 0
                and (expected_cps is None or fields.cp in expected_cps)):
            return fields.cp, fields.cp_confidence
        return -1, 0.0

    def close(self):
        if self._native_ocr is not None:
            self._native_ocr.close()
            self._native_ocr = None

    def read_hp(self, image: Image.Image) -> int:
        """Read HP from the screen. Returns max HP or -1."""
        native = self.native_fields(image)
        if native is not None and native.hp > 0:
            return native.hp
        w, h = self.regions.screen_width, self.regions.screen_height
        return ocr.read_hp(image, w, h)

    def read_candy_family(self, image: Image.Image) -> tuple[str, float]:
        """Read evolution-family evidence only when explicitly requested."""
        native = self.native_fields(image)
        if native is not None and native.candy_conflict:
            return "", -1.0
        if native is not None and native.candy_family:
            return native.candy_family, native.candy_confidence
        owner = image.info.get("pokemgr_candy_owner")
        if (isinstance(owner, tuple) and len(owner) == 3
                and isinstance(owner[0], weakref.ReferenceType) and owner[0]() is self
                and isinstance(owner[1], weakref.ReferenceType) and owner[1]() is image
                and owner[2] == image.size):
            return image.info["pokemgr_candy_read"]
        from .candy import read_candy_family
        try:
            result = read_candy_family(image)
        except Exception as exc:
            log.debug("Candy label fallback failed: %s", exc)
            result = "", 0.0
        image.info["pokemgr_candy_read"] = result
        image.info["pokemgr_candy_owner"] = (weakref.ref(self), weakref.ref(image), image.size)
        return result

    def read_cp(self, image: Image.Image, *, expected_cps=None,
                fast: bool = False) -> tuple[int, float]:
        """Read CP, optionally constrained to exact recovery candidates."""
        cp, confidence = self.native_cp(image, expected_cps=expected_cps)
        if confidence < 0:
            return -1, 0.0
        if cp > 0:
            return cp, confidence
        native = self.native_fields(image)
        if native is not None and native.cp_conflict:
            return -1, 0.0
        return ocr.read_cp(
            image, self.regions.cp_region, expected_cps=expected_cps, fast=fast,
        )

    @timing.timed("reader.detail")
    def read_detail_screen(self, image: Image.Image,
                           adb_controller=None, *, include_cp: bool = True) -> dict:
        """Read species, CP, and icon states from the Pokemon detail screen.

        Pass adb_controller to enable multi-capture shiny detection. Appraisal
        scanning can defer CP until HP and IVs establish whether it is needed.
        """
        r = self.regions

        native = self.native_fields(image)
        display_name, name_conf = (
            (native.display_name, native.name_confidence)
            if native is not None and native.display_name
            else ocr.read_species_name(image, r.name_region)
        )
        species = match_species_name(display_name)
        cp, cp_conf = (
            self.read_cp(image, fast=self._fast_cp)
            if include_cp else (-1, 0.0)
        )

        # Shiny detection disabled — use search filter passes instead
        # (visual sparkle detection is unreliable on appraisal screen)
        shiny = False
        # Shadow detection disabled — use search filter passes instead
        # (visual detection has false positives on dark-themed Pokemon like Ghost types)
        shadow = False
        with timing.span("reader.icons"):
            favorited = icons.is_favorited(image, r.favorite_star_region)
            lucky = (native.lucky if native is not None and native.lucky is not None
                     else icons.is_lucky(image, r.lucky_icon_region))
            gender = _detect_gender(image, r.gender_region)
        weight_tag = (
            ocr.read_size_label(image, r.weight_label_region)
            if self._read_size_tags else ""
        )
        height_tag = (
            ocr.read_size_label(image, r.height_label_region)
            if self._read_size_tags else ""
        )

        # Dynamax detection disabled — use search filter passes instead
        # (visual purple icon detection has false positives)
        is_dynamax = False

        return {
            "species": species,
            "display_name": display_name,
            "name_corrected": species.casefold() != display_name.casefold(),
            "candy_family": native.candy_family if native is not None else "",
            "candy_confidence": native.candy_confidence if native is not None else 0.,
            "candy_conflict": native.candy_conflict if native is not None else False,
            "cp": cp,
            "shiny": shiny,
            "shadow": shadow,
            "favorited": favorited,
            "lucky": lucky,
            "gender": gender,
            "weight_tag": weight_tag,
            "height_tag": height_tag,
            "is_dynamax": is_dynamax,
            "confidence": (name_conf + cp_conf) / 2 if include_cp else name_conf,
        }

    @timing.timed("reader.iv")
    def read_appraisal_screen(self, image: Image.Image) -> dict:
        """Read ATK/DEF/STA IV values from the appraisal overlay.

        Uses dynamic bar finding to handle varying layouts.
        """
        # Try dynamic bar finding first (handles layout shifts)
        result = bars.read_bars_dynamic(image)
        if result is not None:
            return result

        # Fallback: use calibrated fixed positions
        r = self.regions
        atk, atk_conf = bars.read_iv_bar(image, r.atk_bar_region)
        def_, def_conf = bars.read_iv_bar(image, r.def_bar_region)
        sta, sta_conf = bars.read_iv_bar(image, r.sta_bar_region)

        return {
            "atk": atk,
            "def_": def_,
            "sta": sta,
            "confidence": (atk_conf + def_conf + sta_conf) / 3,
        }

    def read_pokemon(self, detail_img: Image.Image,
                     appraisal_img: Image.Image,
                     position: int = 0) -> PokemonRead:
        """Full read: combine detail screen and appraisal screen data."""
        detail = self.read_detail_screen(detail_img)
        appraisal = self.read_appraisal_screen(appraisal_img)

        # Save screenshots for debugging
        screenshot_path = ""
        if self._save_screenshots:
            SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
            path = SCREENSHOTS_DIR / f"pokemon_{position:05d}_detail.png"
            detail_img.save(str(path))
            path2 = SCREENSHOTS_DIR / f"pokemon_{position:05d}_appraisal.png"
            appraisal_img.save(str(path2))
            screenshot_path = str(path)

        # Aggregate confidence
        overall_conf = (detail["confidence"] + appraisal["confidence"]) / 2

        return PokemonRead(
            species=detail["species"],
            cp=detail["cp"],
            atk=appraisal["atk"],
            def_=appraisal["def_"],
            sta=appraisal["sta"],
            shiny=detail["shiny"],
            shadow=detail["shadow"],
            favorited=detail["favorited"],
            lucky=detail["lucky"],
            confidence=overall_conf,
            screenshot_path=screenshot_path,
        )

    @timing.timed("screen.bars_visible")
    def are_bars_visible(self, image: Image.Image) -> bool:
        """Check if IV bars are visible using dynamic bar finding."""
        return bars.are_bars_present(image)

    @staticmethod
    def wait_for_stable_screen(adb_controller, max_wait: float = SCREEN_STABLE_MAX_WAIT) -> Image.Image:
        """Take screenshots until two consecutive frames are nearly identical.

        This detects when animations have finished and the screen is ready to read.
        """
        prev = np.array(adb_controller.screencap())
        deadline = time.time() + max_wait

        while time.time() < deadline:
            time.sleep(SCREEN_STABLE_POLL)
            curr_img = adb_controller.screencap()
            curr = np.array(curr_img)
            diff = np.mean(np.abs(curr.astype(float) - prev.astype(float)))
            log.debug("Screen diff: %.2f", diff)
            if diff < SCREEN_STABLE_THRESHOLD:
                return curr_img
            prev = curr

        log.warning("Screen did not stabilize within %.1fs, using last frame", max_wait)
        return Image.fromarray(prev)
