"""ScreenReader facade — combines OCR, bar reading, and icon detection."""

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from ..calibration.profile import CalibrationProfile
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

    def __init__(self, profile: CalibrationProfile):
        self.regions = profile.regions
        self._save_screenshots = True

    def read_hp(self, image: Image.Image) -> int:
        """Read HP from the screen. Returns max HP or -1."""
        w, h = self.regions.screen_width, self.regions.screen_height
        return ocr.read_hp(image, w, h)

    def read_detail_screen(self, image: Image.Image,
                           adb_controller=None) -> dict:
        """Read species, CP, and icon states from the Pokemon detail screen.

        Pass adb_controller to enable multi-capture shiny detection.
        """
        r = self.regions

        species, name_conf = ocr.read_species_name(image, r.name_region)
        cp, cp_conf = ocr.read_cp(image, r.cp_region)

        # Shiny detection disabled — use search filter passes instead
        # (visual sparkle detection is unreliable on appraisal screen)
        shiny = False
        # Shadow detection disabled — use search filter passes instead
        # (visual detection has false positives on dark-themed Pokemon like Ghost types)
        shadow = False
        favorited = icons.is_favorited(image, r.favorite_star_region)
        lucky = icons.is_lucky(image, r.lucky_icon_region)
        gender = _detect_gender(image, r.gender_region)
        weight_tag = ocr.read_size_label(image, r.weight_label_region)
        height_tag = ocr.read_size_label(image, r.height_label_region)

        # Dynamax detection disabled — use search filter passes instead
        # (visual purple icon detection has false positives)
        is_dynamax = False

        return {
            "species": species,
            "cp": cp,
            "shiny": shiny,
            "shadow": shadow,
            "favorited": favorited,
            "lucky": lucky,
            "gender": gender,
            "weight_tag": weight_tag,
            "height_tag": height_tag,
            "is_dynamax": is_dynamax,
            "confidence": (name_conf + cp_conf) / 2,
        }

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
