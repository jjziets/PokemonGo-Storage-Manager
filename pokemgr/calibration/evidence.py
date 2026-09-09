"""Local calibration evidence: screenshots, overlays, and manifests."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..config import CACHE_DIR, PROJECT_ROOT
from .profile import CalibrationProfile
from .regions import BBox


EVIDENCE_SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative_project_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def _draw_label(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str,
                color: tuple[int, int, int, int], font: ImageFont.ImageFont):
    x, y = xy
    box = draw.textbbox((x, y), text, font=font)
    draw.rectangle(
        (box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2),
        fill=(0, 0, 0, 205),
    )
    draw.text((x, y), text, fill=color, font=font)


def annotate_calibration(image: Image.Image,
                         profile: CalibrationProfile) -> Image.Image:
    """Overlay every configured region/target and its provenance."""
    annotated = image.convert("RGBA")
    draw = ImageDraw.Draw(annotated, "RGBA")
    try:
        font = ImageFont.load_default(size=max(12, annotated.width // 55))
    except TypeError:  # Pillow before configurable default-font sizes
        font = ImageFont.load_default()

    bbox_color = (255, 75, 75, 255)
    tap_color = (30, 220, 255, 255)
    swipe_color = (255, 210, 40, 255)

    for field_name in profile.regions.__dataclass_fields__:
        value = getattr(profile.regions, field_name)
        if isinstance(value, BBox):
            draw.rectangle(value.as_tuple(), outline=bbox_color, width=3)
            _draw_label(draw, (value.x + 3, value.y + 3), field_name,
                        bbox_color, font)

    tap_fields = (
        "menu_button", "appraise_menu_item", "dismiss_professor",
        "close_appraisal_target", "back_button", "storage_first_item",
        "storage_search_bar", "storage_search_clear", "map_pokeball",
        "map_pokemon_button", "appraisal_close_x",
    )
    radius = max(8, annotated.width // 85)
    for field_name in tap_fields:
        value = getattr(profile.regions, field_name, None)
        if not value:
            continue
        x, y = value
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            outline=tap_color,
            width=3,
        )
        draw.line((x - radius, y, x + radius, y), fill=tap_color, width=2)
        draw.line((x, y - radius, x, y + radius), fill=tap_color, width=2)
        _draw_label(draw, (x + radius + 3, max(0, y - radius)), field_name,
                    tap_color, font)

    draw.line(
        (*profile.regions.swipe_start, *profile.regions.swipe_end),
        fill=swipe_color,
        width=5,
    )
    _draw_label(draw, profile.regions.swipe_start, "swipe_start -> swipe_end",
                swipe_color, font)

    source = profile.metadata.get("coordinate_source", "unspecified")
    status = profile.metadata.get("verification_status", "unverified")
    header = (
        f"CALIBRATION {profile.fingerprint} | {profile.layout} | "
        f"coordinates={source} | status={status}"
    )
    header_box = draw.textbbox((10, 10), header, font=font)
    draw.rectangle(
        (0, 0, annotated.width, header_box[3] + 12),
        fill=(0, 0, 0, 220),
    )
    draw.text((10, 10), header, fill=(255, 255, 255, 255), font=font)
    return annotated


def write_calibration_evidence(
        profile: CalibrationProfile,
        image: Image.Image,
        *,
        output_root: Path | str | None = None,
        source: str = "adb_screencap",
        visible_screen: str = "unknown",
        note: str = "",
        captured_at: str | None = None,
) -> dict:
    """Persist an original screenshot, region overlay, and validation manifest.

    Capturing evidence does not verify a calibration.  The manifest and profile
    validation entry remain explicitly unverified until a known-truth scan is
    confirmed.
    """
    captured_at = captured_at or _utc_now()
    expected_size = (
        profile.regions.screen_width,
        profile.regions.screen_height,
    )
    if image.size != expected_size:
        raise ValueError(
            f"Screenshot is {image.size[0]}x{image.size[1]}, but the profile "
            f"is for {expected_size[0]}x{expected_size[1]}"
        )
    stamp = captured_at.replace("-", "").replace(":", "").replace("+", "_")
    stamp = stamp.replace(".", "_")
    output_root = Path(output_root) if output_root else CACHE_DIR / "calibration"
    evidence_dir = output_root / profile.fingerprint / stamp
    evidence_dir.mkdir(parents=True, exist_ok=False)

    screenshot_path = evidence_dir / "screenshot.png"
    annotated_path = evidence_dir / "annotated.png"
    manifest_path = evidence_dir / "manifest.json"

    screenshot = image.copy()
    screenshot.save(screenshot_path, format="PNG")
    annotate_calibration(screenshot, profile).save(annotated_path, format="PNG")
    screenshot_sha256 = hashlib.sha256(screenshot_path.read_bytes()).hexdigest()

    coordinate_source = profile.metadata.get("coordinate_source", "unspecified")
    verification_status = profile.metadata.get("verification_status", "unverified")
    manifest = {
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "captured_at": captured_at,
        "device": {
            "fingerprint": profile.fingerprint,
            "model": profile.device_model,
            "serial": profile.serial,
            "resolution": profile.resolution,
            "density": profile.density,
            "layout": profile.layout,
        },
        "profile": {
            "schema_version": profile.schema_version,
            "coordinate_source": coordinate_source,
            "verification_status": verification_status,
            "calibrated_at": profile.calibrated_at,
        },
        "capture": {
            "source": source,
            "visible_screen": visible_screen,
            "screenshot": screenshot_path.name,
            "annotated_screenshot": annotated_path.name,
            "sha256": screenshot_sha256,
            "width": screenshot.width,
            "height": screenshot.height,
        },
        "validation": {
            "status": "unverified",
            "note": note,
            "known_truth": [],
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    manifest_ref = _relative_project_path(manifest_path)
    return {
        "manifest_path": manifest_path,
        "manifest": manifest,
        "validation_entry": {
            "kind": "calibration_capture",
            "status": "unverified",
            "visible_screen": visible_screen,
            "coordinate_source": coordinate_source,
            "manifest": manifest_ref,
            "screenshot_sha256": screenshot_sha256,
            "note": note,
        },
    }


def confirm_calibration_evidence(
        manifest_path: Path | str,
        profile: CalibrationProfile,
        known_truth: list[dict],
        note: str = "",
) -> dict:
    """Confirm a manifest after an explicit, bounded known-truth scan."""
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    manifest_fingerprint = manifest.get("device", {}).get("fingerprint")
    if manifest_fingerprint != profile.fingerprint:
        raise ValueError(
            f"Evidence is for {manifest_fingerprint!r}, not "
            f"{profile.fingerprint!r}"
        )
    if len(known_truth) < 2:
        raise ValueError("At least two known-truth Pokemon are required")

    capture = manifest.get("capture", {})
    manifest_size = (capture.get("width"), capture.get("height"))
    expected_size = (
        profile.regions.screen_width,
        profile.regions.screen_height,
    )
    if manifest_size != expected_size:
        raise ValueError(
            f"Evidence resolution {manifest_size!r} does not match profile "
            f"resolution {expected_size!r}"
        )
    screenshot_path = manifest_path.parent / capture.get("screenshot", "")
    if not screenshot_path.is_file():
        raise ValueError("Evidence screenshot is missing")
    actual_sha256 = hashlib.sha256(screenshot_path.read_bytes()).hexdigest()
    if actual_sha256 != capture.get("sha256"):
        raise ValueError("Evidence screenshot hash does not match its manifest")

    verified_at = _utc_now()
    manifest["validation"] = {
        "status": "verified",
        "verified_at": verified_at,
        "known_truth": known_truth,
        "note": note,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    manifest_ref = _relative_project_path(manifest_path)
    profile.mark_verified(manifest_ref, known_truth, note)
    return manifest
