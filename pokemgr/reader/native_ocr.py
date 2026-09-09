"""Persistent macOS Vision OCR and strict, frame-bound appraisal text parsing.

Vision chooses its compute device automatically. This module does not claim GPU
execution, retain video frames, or substitute text between independent frames.
The caller still validates HP/IV/species/CP and independently confirms identity.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections.abc import Collection
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time

from PIL import Image

from ..calibration.regions import BBox, ScreenRegions, is_tablet_layout
from ..config import CACHE_DIR, PROJECT_ROOT
from .. import timing


class NativeOCRError(RuntimeError):
    """Unavailable worker, timed-out request, or invalid frame response."""


class NativeOCRFrameError(NativeOCRError):
    """A synchronized response has unusable image-local text geometry."""


@dataclass(frozen=True)
class TextObservation:
    text: str
    confidence: float
    bbox: tuple[float, float, float, float]  # top-left x, y, width, height in pixels


@dataclass(frozen=True)
class NativeFrameText:
    frame_id: str
    width: int
    height: int
    observations: tuple[TextObservation, ...]
    ocr_ms: float


@dataclass(frozen=True)
class NativeFields:
    cp: int = -1
    hp: int = -1
    display_name: str = ""
    caught_species: str = ""
    cp_confidence: float = 0.0
    hp_confidence: float = 0.0
    name_confidence: float = 0.0
    caught_confidence: float = 0.0
    cp_conflict: bool = False
    lucky: bool | None = None
    candy_family: str = ""
    candy_confidence: float = 0.0
    candy_conflict: bool = False


_build_lock = threading.Lock()
_MAX_RESPONSE = 2 * 1024 * 1024
_MAX_IMAGE = 64 * 1024 * 1024


def _worker_binary() -> Path:
    if sys.platform != "darwin":
        raise NativeOCRError("Native OCR requires macOS")
    source = PROJECT_ROOT / "scripts" / "native_ocr.swift"
    try:
        digest = hashlib.sha256(source.read_bytes() + platform.machine().encode()).hexdigest()[:16]
    except OSError as exc:
        raise NativeOCRError("Native OCR source is unavailable") from exc
    directory = CACHE_DIR / "native-ocr"
    destination = directory / f"vision-{digest}"
    with _build_lock:
        if destination.is_file() and os.access(destination, os.X_OK):
            return destination
        compiler = shutil.which("swiftc")
        if not compiler:
            raise NativeOCRError("Native OCR requires the Swift command line tools")
        try:
            directory.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".vision-", dir=directory)
            os.close(descriptor)
        except OSError as exc:
            raise NativeOCRError(f"Native OCR cache is not writable: {exc}") from exc
        try:
            result = subprocess.run(
                [compiler, "-O", str(source), "-o", temporary],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
                check=False,
            )
            if result.returncode:
                reason = result.stderr.decode("utf-8", errors="replace")[-2000:]
                raise NativeOCRError(f"Native OCR compilation failed: {reason}")
            os.chmod(temporary, 0o700)
            os.replace(temporary, destination)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NativeOCRError(f"Native OCR compilation failed: {exc}") from exc
        finally:
            Path(temporary).unlink(missing_ok=True)
    return destination


class NativeOCR:
    """One persistent, serialized Vision worker with bounded I/O and timeouts.

    Call ``start`` before scanning to compile/start once, then ``recognize`` for
    each selected frame. ``close`` releases the worker. A failed request tears
    down the worker; the caller can fall back or explicitly retry a fresh frame.
    """

    def __init__(self, *, timeout: float = 5.0, executable: Path | str | None = None):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        self.timeout = timeout
        self._executable = executable
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._request_id = 0

    def start(self) -> None:
        with self._lock:
            self._start_locked()

    def _start_locked(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._close_locked()
        binary = Path(self._executable) if self._executable else _worker_binary()
        try:
            self._process = subprocess.Popen(
                [str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0,
            )
            os.set_blocking(self._process.stdin.fileno(), False)
            os.set_blocking(self._process.stdout.fileno(), False)
        except OSError as exc:
            self._close_locked()
            raise NativeOCRError(f"Cannot start native OCR: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.5)
        for stream in (process.stdin, process.stdout):
            if stream:
                stream.close()

    def __enter__(self) -> NativeOCR:
        self.start()
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    @staticmethod
    def _wait(fd: int, *, write: bool, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise NativeOCRError("Native OCR request timed out")
        readable, writable, _ = select.select(
            [] if write else [fd], [fd] if write else [], [], remaining,
        )
        if not (writable if write else readable):
            raise NativeOCRError("Native OCR request timed out")

    def _exchange(self, header: bytes, pixels: bytes, deadline: float) -> dict:
        process = self._process
        for chunk in (struct.pack(">I", len(header)), header, pixels):
            pending = memoryview(chunk)
            while pending:
                self._wait(process.stdin.fileno(), write=True, deadline=deadline)
                count = os.write(process.stdin.fileno(), pending)
                if count <= 0:
                    raise NativeOCRError("Native OCR input closed")
                pending = pending[count:]
        response = bytearray()
        while not response.endswith(b"\n"):
            self._wait(process.stdout.fileno(), write=False, deadline=deadline)
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise NativeOCRError("Native OCR worker closed before responding")
            response.extend(chunk)
            if len(response) > _MAX_RESPONSE:
                raise NativeOCRError("Native OCR response exceeded its size limit")
        return json.loads(response)

    @timing.timed("reader.native_request")
    def recognize(self, image: Image.Image, *, frame_id: str | int,
                  mode: str = "fast") -> NativeFrameText:
        if mode not in ("fast", "accurate"):
            raise ValueError("mode must be fast or accurate")
        if type(frame_id) not in (str, int) or not str(frame_id) or len(str(frame_id)) > 256:
            raise ValueError("frame_id must be a nonempty string or integer")
        width, height = image.size
        if not (0 < width <= 8192 and 0 < height <= 8192) or width * height * 3 > _MAX_IMAGE:
            raise ValueError("Native OCR image dimensions exceed the supported limit")
        # Copy bytes for this request before handing them to the persistent
        # worker. It never references a mutable/reused decoder buffer.
        pixels = image.convert("RGB").tobytes()
        with self._lock:
            self._start_locked()
            self._request_id += 1
            request_id = self._request_id
            header = json.dumps({
                "id": request_id, "frame_id": str(frame_id), "width": width,
                "height": height, "payload_bytes": len(pixels), "mode": mode,
            }, separators=(",", ":")).encode()
            try:
                response = self._exchange(header, pixels, time.monotonic() + self.timeout)
                return _decode_response(response, request_id, str(frame_id), width, height)
            except NativeOCRFrameError:
                # The complete response belongs to this request. Reject its
                # text without restarting or retrying; the next image can use
                # the same synchronized worker.
                raise
            except (OSError, ValueError, TypeError, KeyError, NativeOCRError) as exc:
                self._close_locked()
                if isinstance(exc, NativeOCRError):
                    raise
                raise NativeOCRError(f"Invalid native OCR response: {exc}") from exc

    def recognize_region(self, image: Image.Image, region: BBox, *,
                         frame_id: str | int, mode: str = "accurate") -> NativeFrameText:
        """Read exact source pixels and map observations to the original frame.

        Cropping changes recognition scale/context, never the glyphs. The
        returned frame identity/dimensions remain those of the original image;
        callers can combine these observations only with that same frame.
        """
        if (not isinstance(region, BBox)
                or any(type(value) is not int for value in (region.x, region.y, region.w, region.h))
                or region.x < 0 or region.y < 0 or region.w <= 0 or region.h <= 0
                or region.x2 > image.width or region.y2 > image.height):
            raise ValueError("Native OCR region must be an integer box inside the source image")
        result = self.recognize(image.crop(region.as_tuple()), frame_id=frame_id, mode=mode)
        observations = tuple(replace(observation, bbox=(
            observation.bbox[0] + region.x, observation.bbox[1] + region.y,
            observation.bbox[2], observation.bbox[3],
        )) for observation in result.observations)
        return replace(result, width=image.width, height=image.height, observations=observations)


def _decode_response(response: dict, request_id: int, frame_id: str,
                     width: int, height: int) -> NativeFrameText:
    if not isinstance(response, dict) or response.get("id") != request_id or response.get("frame_id") != frame_id:
        raise NativeOCRError("Native OCR returned text for a different request/frame")
    if response.get("error"):
        raise NativeOCRError(f"Native OCR failed: {str(response['error'])[:500]}")
    if response.get("width") != width or response.get("height") != height:
        raise NativeOCRError("Native OCR returned different image dimensions")
    elapsed = float(response["ocr_ms"])
    if not math.isfinite(elapsed) or elapsed < 0:
        raise NativeOCRError("Native OCR returned an invalid duration")
    items = response["observations"]
    if not isinstance(items, list) or len(items) > 4096:
        raise NativeOCRError("Native OCR returned invalid observations")
    observations = []
    invalid_geometry = None
    for index, item in enumerate(items):
        text = item["text"]
        confidence = float(item["confidence"])
        bbox = tuple(float(value) for value in item["bbox"])
        if (not isinstance(text, str) or len(text) > 10000 or
                not math.isfinite(confidence) or not 0 <= confidence <= 1 or
                len(bbox) != 4 or not all(math.isfinite(value) for value in bbox)):
            raise NativeOCRError("Native OCR returned a malformed observation")
        x, y, w, h = bbox
        # Tiny float round-off at the image edges is harmless; dimensions and
        # association remain exact, and parsing only uses interior regions.
        if w <= 0 or h <= 0 or x < -0.01 or y < -0.01 or x + w > width + 0.01 or y + h > height + 0.01:
            invalid_geometry = (index, bbox)
        observations.append(TextObservation(text, confidence, bbox))
    if invalid_geometry is not None:
        # Check every observation's schema first: an earlier bad box must not
        # hide a later malformed response. No partial fields escape this frame.
        index, bbox = invalid_geometry
        raise NativeOCRFrameError(
            f"Native OCR returned an out-of-frame observation {index}: "
            f"bbox={bbox!r}, image={width}x{height}"
        )
    return NativeFrameText(frame_id, width, height, tuple(observations), elapsed)


_CP = re.compile(r"CP\s*([1-9][0-9]{1,3})", re.IGNORECASE)
_BARE_CP = re.compile(r"[1-9][0-9]{1,3}")
_HP = re.compile(r"([0-9]{1,3})\s*/\s*([1-9][0-9]{1,2})\s*HP", re.IGNORECASE)
_CAUGHT = re.compile(r"This\s+(.{2,40}?)\s+was\b", re.IGNORECASE)
_LUCKY = re.compile(r"LUCKY POK[ÉE]MON", re.IGNORECASE)


def _inside(observation: TextObservation, region: BBox) -> bool:
    x, y, w, h = observation.bbox
    return region.x <= x + w / 2 <= region.x2 and region.y <= y + h / 2 <= region.y2


def _name(text: str) -> str:
    normalized = " ".join(text.split())
    punctuation = " .'-:♀♂%’"
    return normalized if (2 <= len(normalized) <= 40 and any(c.isalpha() for c in normalized)
                          and all(c.isalpha() or c.isdigit() or c in punctuation for c in normalized)) else ""


def _unique(values: list[tuple[object, float]], missing):
    distinct = {value for value, _ in values}
    if len(distinct) != 1:
        return missing, 0.0
    value = next(iter(distinct))
    return value, max(confidence for observed, confidence in values if observed == value)


def _lucky_from_card(frame: NativeFrameText, name_boxes, hp_boxes, *, tablet: bool):
    """Use the visible phone card's label/spacing, never its animated backdrop.

    The normal card has a short name-to-HP gap; Lucky adds a separate text row.
    An unreadable expanded row or an unfamiliar/ambiguous layout stays unknown.
    """
    if tablet or len(set(name_boxes)) != 1 or len(set(hp_boxes)) != 1:
        return None
    nx, ny, nw, nh = name_boxes[0]
    hx, hy, hw, hh = hp_boxes[0]
    sy = frame.height / 2376
    if (not 30 * sy <= nh <= 80 * sy or not 12 * sy <= hh <= 35 * sy
            or not .40 * frame.width <= nx + nw / 2 <= .60 * frame.width
            or not .40 * frame.width <= hx + hw / 2 <= .60 * frame.width):
        return None
    name_bottom = ny + nh
    gap = hy - name_bottom
    if not 40 * sy <= gap <= 140 * sy:
        return None
    between = [observation for observation in frame.observations
               if observation.bbox[1] >= name_bottom
               and observation.bbox[1] + observation.bbox[3] <= hy
               and .25 * frame.width <= observation.bbox[0]
                   + observation.bbox[2] / 2 <= .75 * frame.width]
    if any(observation.confidence >= .5
           and _LUCKY.fullmatch(" ".join(observation.text.split()))
           for observation in between):
        return True
    # Absence of OCR text alone proves nothing. Only the measured normal card
    # spacing, with both readable anchors and no intervening text, proves False.
    if gap <= 80 * sy and not between:
        return False
    return None


def parse_appraisal_fields(frame: NativeFrameText, regions: ScreenRegions, *,
                           density: int | None = None,
                           expected_cps: Collection[int] | None = None) -> NativeFields:
    """Extract only whole observed tokens in the matching calibrated frame.

    Names remain raw (no species expansion). Primary CP requires the CP prefix;
    a whole bare numeric token is accepted only against explicit exact recovery
    candidates. HP requires the current/max HP line. Conflicting observations
    make that field missing. Occluded/split digits never get repaired or joined.
    """
    if (frame.width, frame.height) != (regions.screen_width, regions.screen_height):
        raise ValueError("Native OCR frame dimensions do not match calibration")
    w, h = frame.width, frame.height
    # HP changes vertical position for Lucky/tablet layouts. These are the
    # existing reader's bounded detail-card/anchored-bubble search areas.
    hp_region = BBox(int(w * .25), int(h * .35), int(w * .50), int(h * .35))
    tablet = is_tablet_layout(w, h, density)
    start, end = int(h * (.72 if tablet else .80)), int(h * .94) if tablet else h - 10
    caught_region = BBox(20, start, w - 40, end - start)
    expected = frozenset(expected_cps) if expected_cps is not None else None
    cp_values, hp_values, names, caught_names = [], [], [], []
    hp_boxes, name_boxes = [], []
    for observation in frame.observations:
        if observation.confidence < .5:
            continue
        text = observation.text.strip()
        if _inside(observation, regions.cp_region):
            match = _CP.fullmatch(text)
            if match:
                cp = int(match.group(1))
                if expected is None or cp in expected:
                    cp_values.append((cp, observation.confidence))
            elif expected is not None and _BARE_CP.fullmatch(text) and int(text) in expected:
                # An occluded Zacian frame yields only "561" from CP5561;
                # never promote that suffix as an unconstrained primary CP.
                cp_values.append((int(text), observation.confidence))
        if _inside(observation, hp_region):
            match = _HP.fullmatch(text)
            if match:
                current, maximum = map(int, match.groups())
                if 0 <= current <= maximum and 10 <= maximum <= 500:
                    hp_values.append((maximum, observation.confidence))
                    hp_boxes.append(observation.bbox)
        if _inside(observation, regions.name_region):
            name = _name(text)
            if name:
                names.append((name, observation.confidence))
                name_boxes.append(observation.bbox)
        if _inside(observation, caught_region):
            match = _CAUGHT.match(text)
            if match and (name := _name(match.group(1))):
                caught_names.append((name, observation.confidence))
    cp, cp_conf = _unique(cp_values, -1)
    hp, hp_conf = _unique(hp_values, -1)
    name, name_conf = _unique(names, "")
    caught, caught_conf = _unique(caught_names, "")
    lucky = (_lucky_from_card(frame, name_boxes, hp_boxes, tablet=tablet)
             if name and hp > 0 else None)
    from .candy import parse_candy_observations
    candy, candy_conf, candy_conflict = parse_candy_observations(
        ((item.text, item.confidence, item.bbox) for item in frame.observations), w, h,
    )
    return NativeFields(cp, hp, name, caught, cp_conf, hp_conf, name_conf, caught_conf,
                        cp_conflict=len({value for value, _ in cp_values}) > 1,
                        lucky=lucky, candy_family=candy, candy_confidence=candy_conf,
                        candy_conflict=candy_conflict)
