"""Calibration profile management — save/load per-device configurations."""

import copy
import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..config import CALIBRATIONS_DIR
from ..adb.device import DeviceInfo
from .regions import ScreenRegions, is_tablet_layout

log = logging.getLogger(__name__)

PROFILE_SCHEMA_VERSION = 2
COORDINATE_SOURCE_TEMPLATE = "template"
COORDINATE_SOURCE_LEGACY = "legacy_profile"
VERIFICATION_UNVERIFIED = "unverified"
VERIFICATION_VERIFIED = "verified"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    schema_version: int = PROFILE_SCHEMA_VERSION
    layout: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.calibrated_at:
            self.calibrated_at = _utc_now()
        if not self.layout:
            self.layout = self._layout_for(
                self.regions.screen_width,
                self.regions.screen_height,
                self.density,
            )
        # A profile is not verified merely because it has coordinates.  Direct
        # constructors are used in tests and by older callers, so retain an
        # explicit "unspecified" source instead of claiming those coordinates
        # came from the built-in template or a real-device validation.
        self.metadata = dict(self.metadata or {})
        self.metadata.setdefault("coordinate_source", "unspecified")
        self.metadata.setdefault("verification_status", VERIFICATION_UNVERIFIED)

    @property
    def fingerprint(self) -> str:
        return f"{self.device_model}_{self.serial}_{self.resolution}"

    @staticmethod
    def fingerprint_for(info: DeviceInfo) -> str:
        return f"{info.model}_{info.serial}_{info.resolution}"

    @staticmethod
    def _layout_for(width: int, height: int, density: int) -> str:
        return "tablet" if is_tablet_layout(width, height, density) else "phone"

    @classmethod
    def path_for_device(cls, info: DeviceInfo,
                        directory: Path | str | None = None) -> Path:
        directory = Path(directory) if directory else CALIBRATIONS_DIR
        return directory / f"{cls.fingerprint_for(info)}.json"

    def path(self, directory: Path | str | None = None) -> Path:
        directory = Path(directory) if directory else CALIBRATIONS_DIR
        return directory / f"{self.fingerprint}.json"

    # ── Persistence ──────────────────────────────────────────────────

    def save(self, directory: Path | str | None = None) -> Path:
        """Save profile as JSON."""
        directory = Path(directory) if directory else CALIBRATIONS_DIR
        directory.mkdir(parents=True, exist_ok=True)

        path = self.path(directory)
        data = {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "device_model": self.device_model,
            "serial": self.serial,
            "resolution": self.resolution,
            "density": self.density,
            "layout": self.layout,
            "calibrated_at": self.calibrated_at,
            "metadata": self.metadata,
            "validation_results": self.validation_results,
            "regions": self.regions.to_dict(),
        }
        path.write_text(json.dumps(data, indent=2))
        log.info("Calibration saved: %s", path)
        self.schema_version = PROFILE_SCHEMA_VERSION
        return path

    @classmethod
    def load(cls, path: Path | str) -> "CalibrationProfile":
        """Load a profile, migrating legacy schema-v1 data in memory."""
        path = Path(path)
        data = cls._migrate_data(json.loads(path.read_text()))
        regions = ScreenRegions.from_dict(data["regions"])
        return cls(
            device_model=data["device_model"],
            serial=data["serial"],
            resolution=data["resolution"],
            density=data["density"],
            regions=regions,
            calibrated_at=data.get("calibrated_at", ""),
            validation_results=data.get("validation_results", []),
            schema_version=data["schema_version"],
            layout=data["layout"],
            metadata=data["metadata"],
        )

    @classmethod
    def _migrate_data(cls, raw_data: dict) -> dict:
        """Return current-schema data without changing the source file."""
        data = copy.deepcopy(raw_data)
        version = int(data.get("schema_version", 1))
        if version > PROFILE_SCHEMA_VERSION:
            raise ValueError(
                f"Calibration schema {version} is newer than supported "
                f"schema {PROFILE_SCHEMA_VERSION}"
            )

        regions = data.get("regions", {})
        width = int(regions.get("screen_width", 0))
        height = int(regions.get("screen_height", 0))
        if not width or not height:
            try:
                width, height = (int(v) for v in data["resolution"].split("x", 1))
            except (KeyError, TypeError, ValueError):
                width, height = 0, 0
        density = int(data.get("density", 0) or 0)
        layout = cls._layout_for(width, height, density) if width and height else "unknown"

        metadata = dict(data.get("metadata") or {})
        if version == 1:
            metadata.setdefault("coordinate_source", COORDINATE_SOURCE_LEGACY)
            metadata.setdefault("verification_status", VERIFICATION_UNVERIFIED)
            metadata["migrated_from_schema_version"] = 1
            metadata.setdefault(
                "migration_note",
                "Legacy coordinates were preserved but have not been verified "
                "by the schema-v2 calibration workflow.",
            )

        # Optional navigation targets were introduced over time. Materialize
        # the same template values that the navigator otherwise uses as runtime
        # fallbacks, including for an earlier schema-v2 profile. Existing tuned
        # values are never replaced, and every filled field remains explicit in
        # metadata so it cannot be mistaken for operator-verified calibration.
        if width and height:
            defaults = ScreenRegions.default_for_resolution(
                width, height, density=density
            ).to_dict()
            filled_fields = list(metadata.get("template_filled_fields", []))
            for field_name in (
                "storage_first_item", "storage_search_bar",
                "storage_search_clear", "map_pokeball",
                "map_pokemon_button", "appraisal_close_x",
            ):
                if not regions.get(field_name):
                    regions[field_name] = defaults[field_name]
                    if field_name not in filled_fields:
                        filled_fields.append(field_name)
            if filled_fields:
                metadata["template_filled_fields"] = filled_fields
        data["metadata"] = metadata

        data["schema_version"] = PROFILE_SCHEMA_VERSION
        data.setdefault("layout", layout)
        data["metadata"].setdefault("coordinate_source", "unspecified")
        data["metadata"].setdefault("verification_status", VERIFICATION_UNVERIFIED)
        data.setdefault("validation_results", [])
        return data

    @classmethod
    def find_for_device(cls, info: DeviceInfo,
                        directory: Path | str | None = None) -> "CalibrationProfile | None":
        """Look for an existing calibration profile matching the device fingerprint."""
        directory = Path(directory) if directory else CALIBRATIONS_DIR
        if not directory.exists():
            return None

        fingerprint = cls.fingerprint_for(info)
        path = cls.path_for_device(info, directory)
        if path.exists():
            log.info("Found calibration profile: %s", path)
            return cls.load(path)

        log.info("No calibration profile found for %s", fingerprint)
        return None

    @classmethod
    def create_default(cls, info: DeviceInfo) -> "CalibrationProfile":
        """Create an unverified profile from the built-in coordinate template."""
        regions = ScreenRegions.default_for_resolution(
            info.width, info.height, density=info.density)
        layout = cls._layout_for(info.width, info.height, info.density)
        template_reference = (
            "SM-X516B_1440x2304"
            if layout == "tablet"
            else "SM-F956B_968x2376"
        )
        return cls(
            device_model=info.model,
            serial=info.serial,
            resolution=info.resolution,
            density=info.density,
            regions=regions,
            layout=layout,
            metadata={
                "coordinate_source": COORDINATE_SOURCE_TEMPLATE,
                "verification_status": VERIFICATION_UNVERIFIED,
                "template_reference": template_reference,
                "template_scaled_to_resolution": info.resolution,
            },
        )

    @classmethod
    def backup_for_device(
            cls,
            info: DeviceInfo,
            directory: Path | str | None = None,
            backup_directory: Path | str | None = None,
            timestamp: str | None = None,
    ) -> Path | None:
        """Back up an existing profile before an explicit recalibration."""
        directory = Path(directory) if directory else CALIBRATIONS_DIR
        source = cls.path_for_device(info, directory)
        if not source.exists():
            return None

        backup_directory = (
            Path(backup_directory) if backup_directory else directory / "backups"
        )
        backup_directory.mkdir(parents=True, exist_ok=True)
        stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = backup_directory / f"{source.stem}.{stamp}.bak.json"
        counter = 1
        while target.exists():
            target = backup_directory / (
                f"{source.stem}.{stamp}.{counter}.bak.json"
            )
            counter += 1
        shutil.copy2(source, target)
        log.info("Calibration backup saved: %s", target)
        return target

    def add_validation(self, validation_data: dict):
        """Record calibration or known-truth validation evidence."""
        self.validation_results.append({
            "timestamp": _utc_now(),
            **validation_data,
        })

    def mark_verified(self, manifest_path: str, known_truth: list[dict],
                      note: str = ""):
        """Mark coordinates verified after an explicit known-truth check."""
        verified_at = _utc_now()
        self.metadata.update({
            "verification_status": VERIFICATION_VERIFIED,
            "verified_at": verified_at,
            "verification_manifest": manifest_path,
        })
        self.add_validation({
            "kind": "known_truth_scan",
            "status": VERIFICATION_VERIFIED,
            "manifest": manifest_path,
            "known_truth": known_truth,
            "note": note,
        })
