"""Calibration profile management — save/load per-device configurations."""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..config import CALIBRATIONS_DIR
from ..adb.device import DeviceInfo
from .regions import ScreenRegions

log = logging.getLogger(__name__)


@dataclass
class CalibrationProfile:
    """A saved calibration for a specific device and screen configuration."""

    device_model: str
    serial: str
    resolution: str         # "WxH"
    density: int
    regions: ScreenRegions
    calibrated_at: str = ""
    validation_results: list[dict] = field(default_factory=list)

    def __post_init__(self):
        if not self.calibrated_at:
            self.calibrated_at = datetime.now().isoformat()

    @property
    def fingerprint(self) -> str:
        return f"{self.device_model}_{self.serial}_{self.resolution}"

    @staticmethod
    def fingerprint_for(info: DeviceInfo) -> str:
        return f"{info.model}_{info.serial}_{info.resolution}"

    # ── Persistence ──────────────────────────────────────────────────

    def save(self, directory: Path | str | None = None):
        """Save profile as JSON."""
        directory = Path(directory) if directory else CALIBRATIONS_DIR
        directory.mkdir(parents=True, exist_ok=True)

        path = directory / f"{self.fingerprint}.json"
        data = {
            "device_model": self.device_model,
            "serial": self.serial,
            "resolution": self.resolution,
            "density": self.density,
            "calibrated_at": self.calibrated_at,
            "validation_results": self.validation_results,
            "regions": self.regions.to_dict(),
        }
        path.write_text(json.dumps(data, indent=2))
        log.info("Calibration saved: %s", path)

    @classmethod
    def load(cls, path: Path | str) -> "CalibrationProfile":
        """Load profile from a JSON file."""
        path = Path(path)
        data = json.loads(path.read_text())
        regions = ScreenRegions.from_dict(data["regions"])
        return cls(
            device_model=data["device_model"],
            serial=data["serial"],
            resolution=data["resolution"],
            density=data["density"],
            regions=regions,
            calibrated_at=data.get("calibrated_at", ""),
            validation_results=data.get("validation_results", []),
        )

    @classmethod
    def find_for_device(cls, info: DeviceInfo,
                        directory: Path | str | None = None) -> "CalibrationProfile | None":
        """Look for an existing calibration profile matching the device fingerprint."""
        directory = Path(directory) if directory else CALIBRATIONS_DIR
        if not directory.exists():
            return None

        fingerprint = cls.fingerprint_for(info)
        path = directory / f"{fingerprint}.json"
        if path.exists():
            log.info("Found calibration profile: %s", path)
            return cls.load(path)

        log.info("No calibration profile found for %s", fingerprint)
        return None

    @classmethod
    def create_default(cls, info: DeviceInfo) -> "CalibrationProfile":
        """Create a profile with default regions based on device resolution."""
        regions = ScreenRegions.default_for_resolution(info.width, info.height)
        return cls(
            device_model=info.model,
            serial=info.serial,
            resolution=info.resolution,
            density=info.density,
            regions=regions,
        )

    def add_validation(self, pokemon_data: dict):
        """Record a successful validation read."""
        self.validation_results.append({
            "timestamp": datetime.now().isoformat(),
            **pokemon_data,
        })
