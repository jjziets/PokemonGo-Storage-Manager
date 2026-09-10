"""Calibration profile management — save/load per-device configurations."""

import copy
import json
import logging
import shutil
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path

from ..config import CALIBRATIONS_DIR
from ..adb.device import DeviceInfo
from .regions import BBox, ScreenRegions, is_tablet_layout

log = logging.getLogger(__name__)

PROFILE_SCHEMA_VERSION = 2
COORDINATE_SOURCE_TEMPLATE = "template"
COORDINATE_SOURCE_LEGACY = "legacy_profile"
COORDINATE_SOURCE_SHARED = "shared_profile"
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
        return f"{self.legacy_fingerprint}_dpi{self.density}"

    @property
    def legacy_fingerprint(self) -> str:
        """Previous identity, accepted only alongside exact geometry metadata."""
        return f"{self.device_model}_{self.serial}_{self.resolution}"

    @staticmethod
    def fingerprint_for(info: DeviceInfo) -> str:
        return f"{info.model}_{info.serial}_{info.resolution}_dpi{info.density}"

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

    @staticmethod
    def _legacy_path_for_device(info: DeviceInfo, directory: Path) -> Path:
        return directory / f"{info.model}_{info.serial}_{info.resolution}.json"

    def _matches_geometry(self, info: DeviceInfo) -> bool:
        """Do not infer layout compatibility from pixel dimensions alone."""
        return (all(type(value) is int and value > 0 for value in (
                    self.density, self.regions.screen_width, self.regions.screen_height,
                    info.density, info.width, info.height,
                ))
                and self.resolution == info.resolution
                and self.density == info.density
                and (self.regions.screen_width, self.regions.screen_height) == (info.width, info.height)
                and self.layout == self._layout_for(info.width, info.height, info.density))

    def _matches_device(self, info: DeviceInfo) -> bool:
        return (self.device_model == info.model and self.serial == info.serial
                and self._matches_geometry(info))

    @staticmethod
    def _coordinates_fit_canvas(regions: ScreenRegions) -> bool:
        """Shared coordinates must describe usable pixels on this canvas."""
        width, height = regions.screen_width, regions.screen_height
        if not all(type(value) is int and value > 0 for value in (width, height)):
            return False
        for definition in fields(ScreenRegions):
            if definition.name in ("screen_width", "screen_height", "swipe_duration_ms"):
                continue
            value = getattr(regions, definition.name)
            if definition.type is BBox:
                if (not isinstance(value, BBox)
                        or not all(type(part) is int for part in (value.x, value.y, value.w, value.h))
                        or value.x < 0 or value.y < 0 or value.w <= 0 or value.h <= 0
                        or value.x2 > width or value.y2 > height):
                    return False
            elif value is None and definition.default is None:
                # Only the optional navigation targets have None defaults.
                continue
            elif (not isinstance(value, tuple) or len(value) != 2
                  or not all(type(part) is int for part in value)
                  or not (0 <= value[0] < width and 0 <= value[1] < height)):
                return False
        return True

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
        """Load this device's profile or borrow compatible coordinates in memory.

        Existing device overrides win. A shared result remains unverified and
        is not saved until the caller explicitly saves the returned profile.
        """
        directory = Path(directory) if directory else CALIBRATIONS_DIR
        if not directory.exists():
            return None

        fingerprint = cls.fingerprint_for(info)
        path = cls.path_for_device(info, directory)
        if path.exists():
            profile = cls.load(path)
            if not profile._matches_device(info):
                raise ValueError(f"Saved calibration does not match device geometry: {path.name}")
            log.info("Found calibration profile: %s", path)
            return profile

        legacy = cls._legacy_path_for_device(info, directory)
        if legacy.exists():
            profile = cls.load(legacy)
            if profile._matches_device(info):
                log.info("Found matching legacy calibration without changing its file: %s", legacy)
                return profile
            log.warning("Ignoring legacy calibration with different device geometry: %s", legacy.name)

        shared = cls._shared_coordinates(info, directory)
        if shared is not None:
            return shared

        log.info("No calibration profile found for %s", fingerprint)
        return None

    @classmethod
    def _shared_coordinates(cls, info: DeviceInfo, directory: Path) -> "CalibrationProfile | None":
        candidates = []
        for path in sorted(directory.glob("*.json")):
            try:
                # Check supplied values before legacy migration can replace a
                # malformed falsey optional target with a template default.
                raw_regions = ScreenRegions.from_dict(json.loads(path.read_text())["regions"])
                if not cls._coordinates_fit_canvas(raw_regions):
                    log.warning("Ignoring shared calibration with invalid coordinates: %s", path.name)
                    continue
                donor = cls.load(path)
                if (not donor.device_model or not donor.serial or not donor._matches_geometry(info)
                        or (donor.device_model, donor.serial) == (info.model, info.serial)
                        or not cls._coordinates_fit_canvas(donor.regions)):
                    continue
                qualified = donor.path(directory)
                legacy = directory / f"{donor.legacy_fingerprint}.json"
                if path not in (qualified, legacy):
                    continue
                # A density-qualified donor supersedes its older file too.
                if path == legacy and qualified.exists():
                    continue
                coordinates = donor.regions.to_dict()
                coordinates.pop("swipe_duration_ms")
                candidates.append((path, donor, coordinates))
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                log.warning("Ignoring unreadable shared calibration candidate: %s", path.name)

        verified = [candidate for candidate in candidates
                    if candidate[1].metadata.get("verification_status") == VERIFICATION_VERIFIED]
        candidates = verified or candidates
        if not candidates:
            return None
        path, donor, coordinates = candidates[0]
        if any(candidate[2] != coordinates for candidate in candidates[1:]):
            log.warning("Compatible calibration donors disagree; no coordinates were shared")
            return None

        shared = cls.create_default(info)
        duration = shared.regions.swipe_duration_ms
        shared.regions = copy.deepcopy(donor.regions)
        shared.regions.swipe_duration_ms = duration
        shared.metadata = {
            "coordinate_source": COORDINATE_SOURCE_SHARED,
            "verification_status": VERIFICATION_UNVERIFIED,
            "shared_from_fingerprint": donor.fingerprint,
            "shared_from_profile": path.name,
            "shared_geometry": f"{info.resolution}/dpi{info.density}/{shared.layout}",
        }
        log.info("Reusing compatible coordinates from %s for %s; device verification is unverified",
                 path.name, shared.fingerprint)
        return shared

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
            source = cls._legacy_path_for_device(info, directory)
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
