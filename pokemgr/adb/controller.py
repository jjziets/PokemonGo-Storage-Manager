"""ADB controller for screen capture, taps, swipes, and device interaction."""

import io
import os
import shlex
import subprocess
import re
import logging
import time
from PIL import Image
from .. import timing

from ..config import (
    ADB_PATH,
    DEFAULT_TAP_JITTER,
    DEFAULT_SWIPE_JITTER,
    DEFAULT_SWIPE_DURATION_MS,
    SWIPE_DURATION_JITTER_MS,
    jitter_coord,
)
from .device import DeviceInfo

log = logging.getLogger(__name__)


class ADBError(Exception):
    """Raised when an ADB command fails."""
    pass


class StreamCaptureInvalidated(ADBError):
    """Discard this capture after input/pause; a fresh operation may retry."""


class ADBController:
    """Controls an Android device via ADB for screen capture and input simulation."""

    def __init__(self, adb_path: str = ADB_PATH, serial: str | None = None):
        self.adb_path = adb_path
        stream_serial = os.environ.get("POKEMGR_DEVICE_SERIAL") or None
        self.serial = serial if serial is not None else stream_serial
        self._device_info: DeviceInfo | None = None
        logical = os.environ.get("POKEMGR_DISPLAY_ID")
        capture = os.environ.get("POKEMGR_CAPTURE_DISPLAY_ID")
        if (logical is None) != (capture is None):
            raise ADBError("POKEMGR_DISPLAY_ID and POKEMGR_CAPTURE_DISPLAY_ID must be set together")
        if logical is not None and (
            not re.fullmatch(r"[0-9]+", logical)
            or not re.fullmatch(r"[0-9]+", capture)
            or int(logical) <= 0 or int(capture) <= 0
        ):
            raise ADBError("Stream display IDs must be positive integers; logical display 0 is not isolated")
        if (logical is not None and serial is not None and stream_serial is not None
                and serial != stream_serial):
            raise ADBError("Stream display IDs belong to a different selected device serial")
        capture_format = os.environ.get("POKEMGR_CAPTURE_FORMAT", "png")
        if capture_format not in ("png", "jpeg"):
            raise ADBError("POKEMGR_CAPTURE_FORMAT must be png or jpeg")
        if capture_format == "jpeg" and logical is None:
            raise ADBError("JPEG capture requires an explicitly selected app display")
        # Freeze the explicit target for this controller's lifetime. A later
        # scrcpy session must create a new controller and rediscover both IDs.
        self._display_id = int(logical) if logical is not None else None
        self._capture_display_id = int(capture) if capture is not None else None
        self._display_unique_id = None
        self._display_geometry = None
        expected_geometry = os.environ.get("POKEMGR_EXPECTED_DISPLAY_GEOMETRY")
        self._expected_display_geometry = None
        if expected_geometry is not None:
            match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)/([1-9][0-9]*)", expected_geometry)
            if logical is None or match is None:
                raise ADBError("Expected display geometry requires an app display and WIDTHxHEIGHT/DPI")
            self._expected_display_geometry = tuple(int(value) for value in match.groups())
        self._target_serial = self.serial if logical is not None else None
        self._capture_format = capture_format
        self._jpeg_unsupported = False
        frame_path = os.environ.get("POKEMGR_FRAME_BUFFER")
        frame_session = os.environ.get("POKEMGR_FRAME_SESSION")
        stream_pid = os.environ.get("POKEMGR_STREAM_PID")
        frame_reader = os.environ.get("POKEMGR_FRAME_READER")
        frame_values = (frame_path, frame_session, stream_pid)
        if any(value is not None for value in (*frame_values, frame_reader)):
            if not all(frame_values) or logical is None:
                raise ADBError("Stream frames require a buffer, session, writer PID, and explicit virtual display")
            if (not os.path.isabs(frame_path)
                    or not re.fullmatch(r"[0-9a-f]{32}", frame_session)
                    or not re.fullmatch(r"[1-9][0-9]*", stream_pid)
                    or int(stream_pid) >= 1 << 31
                    or (frame_reader is not None and not os.path.isabs(frame_reader))):
                raise ADBError("Invalid stream frame buffer identity or reader path")
        self._stream_config = (
            dict(path=frame_path, session=frame_session, writer_pid=int(stream_pid),
                 reader_library=frame_reader) if all(frame_values) else None
        )
        self._stream_capture = None
        self._stream_fault = None
        self._stream_input_ns = 0
        self._stream_generation = 0

    @property
    def display_id(self) -> int | None:
        return self._display_id

    @property
    def capture_display_id(self) -> int | None:
        return self._capture_display_id

    @property
    def has_stream_frames(self) -> bool:
        """Whether this controller was explicitly bound to a paired producer."""
        return self._stream_config is not None

    def _stream_source(self, target):
        if self._stream_fault:
            raise ADBError(self._stream_fault)
        if self._stream_capture is None:
            if target is None:
                raise ADBError("Stream frames require a verified virtual display")
            from .stream_capture import StreamCapture
            self._stream_capture = StreamCapture(
                self, **self._stream_config, width=target["width"], height=target["height"],
            )
            if self._stream_input_ns:
                self._stream_capture.mark_input(self._stream_input_ns)
        return self._stream_capture

    def close_stream_capture(self):
        """Release the native ring reader and its persistent clock sampler."""
        self._stream_generation += 1
        source, self._stream_capture = self._stream_capture, None
        if source is not None:
            source.close()

    def invalidate_stream_frames(self):
        """Discard clock/frame evidence across pause, reconnect, or new input."""
        self._stream_generation += 1
        self._stream_input_ns = time.monotonic_ns()
        if self._stream_capture is not None:
            self._stream_capture.invalidate()

    def _fail_stream(self, error):
        self._stream_fault = f"App stream frame source failed: {error}; restart the stream connection"
        self.close_stream_capture()
        raise ADBError(self._stream_fault) from error

    def stream_frames(self, *, after_ns, timeout=1.2, max_frames=30, should_stop=lambda: False):
        """Observe one bounded app-display operation, validating entry and exit.

        The producer/session/PID and source clock are checked for every frame.
        Display validation brackets the <=1.2s/30-frame window, rather than
        spending one ADB round trip per frame. Consumers must explicitly close
        this generator when stopping early; final capture/input validates again.
        """
        if not self.has_stream_frames:
            raise ADBError("No paired stream frame source is configured")
        if (type(max_frames) is not int or not 1 <= max_frames <= 30
                or not isinstance(timeout, (int, float)) or not 0 < timeout <= 1.2
                or type(after_ns) is not int or after_ns <= 0):
            raise ValueError("Stream observation window must be bounded to 1.2 seconds and 30 frames")
        from .clock_sync import ClockSyncError
        from .frame_buffer import FrameBufferError
        target = self.validate_display_target()
        generation = self._stream_generation
        def stopped():
            return should_stop() or generation != self._stream_generation
        try:
            source = self._stream_source(target)
            for image in source.frames(after_ns=after_ns, timeout=timeout,
                                       max_frames=max_frames, should_stop=stopped):
                if stopped():
                    return
                yield image
                # Check before asking the source iterator for its next frame:
                # invalidate() clears clock evidence and may close the reader.
                if stopped():
                    return
        except (ClockSyncError, FrameBufferError, OSError) as exc:
            if generation != self._stream_generation:
                return
            self._fail_stream(exc)
        finally:
            self.validate_display_target()

    @timing.timed("adb.display_validation")
    def validate_display_target(self) -> dict | None:
        """Fail closed if the selected virtual display disappeared or changed."""
        if self.display_id is None:
            return None
        if self.serial is None:
            self.connect()
        if self._target_serial is not None and self.serial != self._target_serial:
            raise ADBError("Stream device changed; restart the stream connection")
        self._target_serial = self.serial

        def query(command):
            result = self._run(["shell", command])
            if result.returncode != 0:
                raise ADBError(f"Could not verify stream display: {command}")
            return result.stdout.decode(errors="replace")

        # Both reports are still fetched before every operation. One shell
        # round trip avoids duplicate ADB startup/transport overhead; && keeps
        # a failed first report from authorizing a later operation.
        displays = query("cmd display get-displays && dumpsys SurfaceFlinger --display-id")
        matches = re.findall(
            rf"^Display id {self.display_id}: (.+)$", displays, re.MULTILINE,
        )
        if len(matches) != 1:
            raise ADBError(f"Logical stream display {self.display_id} is missing or ambiguous")
        info = matches[0]
        name = re.search(r'^DisplayInfo\{"([^"]+)"', info)
        unique = re.search(r'\buniqueId "([^"]+)"', info)
        size = re.search(r"\breal (\d+) x (\d+)", info)
        density = re.search(r"\bdensity (\d+)", info)
        state = re.search(r"\bstate ([A-Z_]+)", info)
        if (not name or not unique or not size or not density or not state
                or not re.search(r"\btype VIRTUAL\b", info)):
            raise ADBError("Selected logical display is not a verified virtual display")
        stream_displays = [
            line for line in displays.splitlines()
            if re.search(r'^Display id \d+: DisplayInfo\{"scrcpy"', line)
            and re.search(r"\btype VIRTUAL\b", line)
        ]
        if name.group(1) != "scrcpy" or len(stream_displays) != 1:
            raise ADBError("Expected exactly one scrcpy virtual logical display")
        if state.group(1) != "ON":
            raise ADBError("Virtual stream display is off; restart the app stream")
        geometry = (int(size.group(1)), int(size.group(2)), int(density.group(1)))
        if any(value <= 0 for value in geometry):
            raise ADBError("Virtual display has invalid dimensions or density")
        if self._expected_display_geometry is not None and geometry != self._expected_display_geometry:
            raise ADBError("App display does not match the requested resolution and density "
                           f"(expected {self._expected_display_geometry}, saw {geometry}); restart the stream")
        if self._display_unique_id is not None and unique.group(1) != self._display_unique_id:
            raise ADBError("Virtual display was recreated; restart the stream connection")
        if self._display_geometry is not None and geometry != self._display_geometry:
            raise ADBError("Virtual display dimensions or density changed; recalibrate before scanning")

        captures = displays
        capture = re.search(
            rf"^Display {self.capture_display_id} \(Virtual display\): (.+)$",
            captures, re.MULTILINE,
        )
        if not capture:
            raise ADBError(f"Virtual capture display {self.capture_display_id} is missing")
        capture_name = re.search(r'displayName="([^"]+)"', capture.group(1))
        if not capture_name or capture_name.group(1) != name.group(1):
            raise ADBError("Capture display does not match the selected logical display name")
        stream_captures = re.findall(
            r'^Display \d+ \(Virtual display\): [^\n]*\bdisplayName="scrcpy".*$',
            captures, re.MULTILINE,
        )
        if len(stream_captures) != 1:
            raise ADBError("Expected exactly one scrcpy virtual capture display")
        self._display_unique_id = unique.group(1)
        self._display_geometry = geometry
        return dict(width=geometry[0], height=geometry[1], density=geometry[2],
                    unique_id=self._display_unique_id, state=state.group(1))

    # ── ADB command helpers ──────────────────────────────────────────

    def _run(self, args: list[str], capture_output: bool = True,
             timeout: float = 30) -> subprocess.CompletedProcess:
        """Run an ADB command and return the result."""
        cmd = [self.adb_path]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += args
        log.debug("ADB: %s", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                capture_output=capture_output,
                timeout=timeout,
            )
            if result.returncode != 0 and capture_output:
                stderr = result.stderr.decode(errors="replace").strip()
                if stderr:
                    log.warning("ADB stderr: %s", stderr)
            return result
        except subprocess.TimeoutExpired:
            raise ADBError(f"ADB command timed out: {' '.join(args)}")
        except FileNotFoundError:
            raise ADBError(f"ADB not found at {self.adb_path}")

    def shell(self, cmd: str) -> str:
        """Run a command on the device shell and return stdout."""
        result = self._run(["shell", cmd])
        stderr = result.stderr.decode(errors="replace") if result.stderr else ""
        if "not found" in stderr or "offline" in stderr:
            if self.wait_for_device():
                self.validate_display_target()
                result = self._run(["shell", cmd])
            else:
                raise ADBError("Device disconnected")
        if self.display_id is not None and result.returncode != 0:
            raise ADBError("Command failed on the selected stream display")
        return result.stdout.decode(errors="replace").strip()

    # ── Device management ────────────────────────────────────────────

    def get_devices(self) -> list[str]:
        """Return list of connected device serial numbers."""
        result = self._run(["devices"])
        output = result.stdout.decode(errors="replace")
        devices = []
        for line in output.strip().split("\n")[1:]:  # skip header
            parts = line.strip().split("\t")
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
        return devices

    def connect(self, serial: str | None = None):
        """Connect the chosen device; discover one only when no serial was set."""
        if serial:
            if self.display_id is not None and self._target_serial not in (None, serial):
                raise ADBError("Stream device is fixed; create a new connection to change devices")
            self.serial = serial

        devices = self.get_devices()
        if not devices:
            raise ADBError("No devices connected. Check USB connection and USB debugging.")
        if self.serial:
            if self.serial not in devices:
                raise ADBError(f"Selected device {self.serial} is not connected")
            return
        if self.display_id is not None and len(devices) != 1:
            raise ADBError("Select one device explicitly before connecting the app stream")
        self.serial = devices[0]
        log.info("Connected to device: %s", self.serial)

    def get_device_info(self) -> DeviceInfo:
        """Get device model, serial, resolution, and density."""
        target = self.validate_display_target()
        if self._device_info:
            return self._device_info

        model = self.shell("getprop ro.product.model")
        serial = self.shell("getprop ro.serialno")

        if target is not None:
            width, height, density = target["width"], target["height"], target["density"]
        else:
            # Parse resolution: "Physical size: 1080x2400"
            size_output = self.shell("wm size")
            size_match = re.search(r"(\d+)x(\d+)", size_output)
            if not size_match:
                raise ADBError(f"Could not parse screen size: {size_output}")
            width, height = int(size_match.group(1)), int(size_match.group(2))

            # Parse density: "Physical density: 420"
            density_output = self.shell("wm density")
            density_match = re.search(r"(\d+)", density_output)
            density = int(density_match.group(1)) if density_match else 0

        self._device_info = DeviceInfo(
            model=model,
            serial=serial,
            width=width,
            height=height,
            density=density,
        )
        log.info("Device: %s", self._device_info)
        return self._device_info

    # ── Screen capture ───────────────────────────────────────────────

    def wait_for_device(self, max_wait: int = 120) -> bool:
        """Wait for the device to reconnect after a disconnect.

        Polls every 3 seconds. Returns True if reconnected, False if timeout.
        """
        import time
        self.invalidate_stream_frames()
        log.warning("Device disconnected — waiting for reconnection (up to %ds)...", max_wait)
        deadline = time.time() + max_wait
        while time.time() < deadline:
            devices = self.get_devices()
            if devices:
                if self.serial and self.serial in devices:
                    log.info("Device %s reconnected!", self.serial)
                    return True
                elif not self.serial and len(devices) == 1:
                    self.serial = devices[0]
                    log.info("Device reconnected as %s", self.serial)
                    return True
            time.sleep(3)
        log.error("Device did not reconnect within %ds", max_wait)
        return False

    @timing.timed("capture.total")
    def screencap(self) -> Image.Image:
        """Capture the screen and return as a PIL Image.

        App streams opt into Android's quality-100 JPEG capture. Older devices
        that reject or ignore its flag get a new, verified PNG capture instead.
        Every attempt targets the same frozen display and fully decodes before
        the image is shared with OCR threads.
        """
        if self.has_stream_frames:
            from .clock_sync import ClockSyncError
            from .frame_buffer import FrameBufferError
            from .stream_capture import StreamCaptureTimeout
            generation = self._stream_generation
            target = self.validate_display_target()
            try:
                image = self._stream_source(target).capture()
                # Verify the display epoch after the source wait as well. A
                # recreated display cannot authorize an old ring-buffer frame.
                self.validate_display_target()
                if generation != self._stream_generation:
                    raise StreamCaptureInvalidated("Stream capture was invalidated during the operation")
                return image
            except StreamCaptureTimeout:
                if generation != self._stream_generation:
                    raise StreamCaptureInvalidated("Stream capture was invalidated during the operation")
                # Only an ordinary absence of fresh frames can use the existing
                # fresh, frozen-target screencap path. Protocol/identity/clock
                # errors are held and never silently converted to JPEG success.
                log.info("No fresh stream frame; requesting a fresh app-display capture")
            except (ClockSyncError, FrameBufferError, OSError) as exc:
                if generation != self._stream_generation:
                    raise StreamCaptureInvalidated("Stream capture was invalidated during the operation") from exc
                self._fail_stream(exc)
        use_jpeg = (self._capture_format == "jpeg" or self.has_stream_frames) and not self._jpeg_unsupported
        formats = ("jpeg", "png") if use_jpeg else ("png",)
        for capture_format in formats:
            self.validate_display_target()
            capture_args = ["exec-out", "screencap", "-j" if capture_format == "jpeg" else "-p"]
            if self.capture_display_id is not None:
                capture_args += ["-d", str(self.capture_display_id)]
            with timing.span("capture.transport"):
                capture_started_at = time.monotonic()
                result = self._run(capture_args)
                capture_finished_at = time.monotonic()
            if result.returncode != 0:
                stderr = result.stderr.decode(errors="replace") if result.stderr else ""
                if "not found" in stderr or "offline" in stderr:
                    if not self.wait_for_device():
                        raise ADBError("Device disconnected and did not reconnect")
                    self.validate_display_target()
                    with timing.span("capture.transport"):
                        capture_started_at = time.monotonic()
                        result = self._run(capture_args)
                        capture_finished_at = time.monotonic()
                if result.returncode != 0:
                    error_text = ((result.stderr or b"") + b"\n" + (result.stdout or b"")).decode(errors="replace")
                    if capture_format == "jpeg" and re.search(
                        r"(?:invalid|unknown|unrecognized|unsupported) option[^\n]*"
                        r"(?:['\"`]-?j['\"`]|--jpeg\b|\bj\s*(?:\n|$))", error_text, re.IGNORECASE,
                    ):
                        self._jpeg_unsupported = True
                        log.info("Device does not support JPEG screencap; using PNG")
                        continue
                    raise ADBError("screencap failed")

            data = result.stdout
            if not data or len(data) < 100:
                raise ADBError(f"screencap returned {len(data) if data else 0} bytes — is the screen on?")

            # Some foldable devices prepend a multiple-displays warning.
            png_idx = data.find(b"\x89PNG")
            jpeg_idx = data.find(b"\xff\xd8\xff") if capture_format == "jpeg" else -1
            starts = [idx for idx in (png_idx, jpeg_idx) if idx >= 0]
            if starts:
                data = data[min(starts):]
            elif self.display_id is not None:
                raise ADBError(
                    f"Virtual screencap returned no {capture_format.upper()}; "
                    "refusing physical-display fallback"
                )
            else:
                log.warning("No PNG header in screencap output, falling back to file method")
                return self._screencap_file()

            @timing.timed("capture.decode")
            def decode(payload):
                image = Image.open(io.BytesIO(payload))
                image.load()
                return image

            try:
                image = decode(data)
            except Exception as error:
                if capture_format == "jpeg":
                    raise ADBError(f"Failed to decode screencap JPEG ({len(data)} bytes): {error}") from error
                # Retain the legacy PNG-only CRLF transport workaround.
                try:
                    image = decode(data.replace(b"\r\n", b"\n"))
                except Exception as error:
                    raise ADBError(f"Failed to decode screencap PNG ({len(data)} bytes): {error}") from error
            if self.display_id is not None and image.size != self._display_geometry[:2]:
                raise ADBError("Stream screenshot dimensions do not match the calibrated display")
            if capture_format == "jpeg":
                if image.format == "PNG":
                    # Ignored flags are not capability success. Discard this
                    # frame, revalidate the target, and request a fresh PNG.
                    self._jpeg_unsupported = True
                    log.info("Device ignored JPEG screencap flag; using PNG")
                    continue
                if image.format != "JPEG":
                    raise ADBError("JPEG screencap returned an unexpected image format")
                with timing.span("capture.convert_rgb"):
                    image = image.convert("RGB")
            image.info["pokemgr_capture_started_at"] = capture_started_at
            image.info["pokemgr_capture_finished_at"] = capture_finished_at
            if self.has_stream_frames:
                self.validate_display_target()
                if generation != self._stream_generation:
                    raise StreamCaptureInvalidated("Stream capture was invalidated during the operation")
            return image

        raise ADBError("screencap failed")

    # ── Input simulation ─────────────────────────────────────────────

    @timing.timed("adb.tap")
    def tap(self, x: int, y: int, jitter: int = DEFAULT_TAP_JITTER):
        """Tap at coordinates with random jitter for human emulation."""
        tx = jitter_coord(x, jitter)
        ty = jitter_coord(y, jitter)
        log.debug("Tap: (%d, %d) → (%d, %d)", x, y, tx, ty)
        self._input("tap", tx, ty)

    @timing.timed("adb.swipe")
    def swipe(self, x1: int, y1: int, x2: int, y2: int,
              duration_ms: int = DEFAULT_SWIPE_DURATION_MS,
              jitter: int = DEFAULT_SWIPE_JITTER):
        """Swipe between two points with jitter on position and duration."""
        import random
        sx1 = jitter_coord(x1, jitter)
        sy1 = jitter_coord(y1, jitter)
        sx2 = jitter_coord(x2, jitter)
        sy2 = jitter_coord(y2, jitter)
        # Vary swipe speed like a real finger
        actual_duration = duration_ms + random.randint(-SWIPE_DURATION_JITTER_MS, SWIPE_DURATION_JITTER_MS)
        actual_duration = max(100, actual_duration)  # minimum 100ms
        log.debug("Swipe: (%d,%d)→(%d,%d) in %dms", sx1, sy1, sx2, sy2, actual_duration)
        self._input("swipe", sx1, sy1, sx2, sy2, actual_duration)

    def input_text(self, text: str):
        """Type text on the device using ADB.

        Wraps text in single quotes to prevent shell interpretation of
        special characters like ! & etc.
        """
        self._input("text", text)

    def key_event(self, keycode: int | str):
        """Send a key event (e.g., KEYCODE_BACK = 4)."""
        normalized_key = str(keycode).strip().upper()
        if normalized_key.isdecimal():
            normalized_key = str(int(normalized_key))
        if self.display_id is not None and normalized_key in {
            "26", "POWER", "KEYCODE_POWER", "223", "SLEEP", "KEYCODE_SLEEP",
            "224", "WAKEUP", "KEYCODE_WAKEUP",
        }:
            raise ADBError("Physical power events are disabled for the app stream")
        self._input("keyevent", keycode)

    def _input(self, action: str, *values):
        self.validate_display_target()
        args = ["input"]
        if self.display_id is not None:
            args += ["-d", str(self.display_id)]
        args += [action, *(str(value) for value in values)]
        self.shell(shlex.join(args))
        self._stream_generation += 1
        self._stream_input_ns = time.monotonic_ns()
        if self._stream_capture is not None:
            self._stream_capture.mark_input(self._stream_input_ns)

    def start_pokemon_go(self, restart: bool = False):
        """Launch Pokemon Go on the selected display without changing target."""
        self.validate_display_target()
        if restart:
            self.shell("am force-stop com.nianticlabs.pokemongo")
        args = ["am", "start"]
        if self.display_id is not None:
            args += ["--display", str(self.display_id)]
        args += ["-n", "com.nianticlabs.pokemongo/"
                 "com.nianticproject.holoholo.libholoholo.unity.UnityMainActivity"]
        self.shell(shlex.join(args))

    # ── Device status ────────────────────────────────────────────────

    def turn_phone_screen_off(self):
        """Darken physical display 0 only while a verified app stream is active."""
        if self.display_id is None:
            raise ADBError("Turning the phone screen off requires an app-only stream")
        if not self.serial or self._target_serial not in (None, self.serial):
            raise ADBError("Connect the selected stream device before turning its screen off")
        selected_serial = self.serial
        if self.serial not in self.get_devices():
            raise ADBError(f"Selected device {self.serial} is not connected")
        self.validate_display_target()
        if self.serial != selected_serial:
            raise ADBError("Selected device changed while turning off the phone screen")
        result = self._run(["shell", "cmd display power-off 0"], timeout=10)
        output = result.stdout.decode(errors="replace").strip()
        error = result.stderr.decode(errors="replace").strip()
        if result.returncode != 0 or re.search(
            r"(?im)^(error|exception|unknown command)\b", output + "\n" + error,
        ):
            detail = error or output or f"exit status {result.returncode}"
            raise ADBError(f"Could not turn off the phone screen: {detail}")

    def turn_phone_screen_on(self):
        """Explicit user action to restore physical display 0 on this device.

        This is separate from automatic navigation's wake_screen(): it never
        changes the selected app display or toggles a global power key.
        """
        if not self.serial:
            raise ADBError("Connect to a device before turning on its phone screen")
        if self._target_serial not in (None, self.serial):
            raise ADBError("Selected stream device changed; reconnect before controlling its screen")
        if self.serial not in self.get_devices():
            raise ADBError(f"Selected device {self.serial} is not connected")
        selected_serial = self.serial
        # Reset scrcpy's panel override, then wake a genuinely sleeping phone.
        # The targeted WAKEUP is intentional only for this explicit UI action.
        for command in (
            "cmd display power-reset 0",
            "input -d 0 keyevent KEYCODE_WAKEUP",
        ):
            if self.serial != selected_serial:
                raise ADBError("Selected device changed while turning on the phone screen")
            result = self._run(["shell", command], timeout=10)
            output = result.stdout.decode(errors="replace").strip()
            error = result.stderr.decode(errors="replace").strip()
            if result.returncode != 0 or re.search(
                r"(?im)^(error|exception|unknown command)\b", output + "\n" + error,
            ):
                detail = error or output or f"exit status {result.returncode}"
                raise ADBError(f"Could not turn on the phone screen: {detail}")

    def get_battery_level(self) -> int:
        """Return battery percentage (0-100)."""
        output = self.shell("dumpsys battery")
        match = re.search(r"level:\s*(\d+)", output)
        return int(match.group(1)) if match else -1

    def is_screen_on(self) -> bool:
        """Check if the device screen is currently on."""
        if self.display_id is not None:
            return self.validate_display_target()["state"] == "ON"
        output = self.shell("dumpsys power")
        return "mWakefulness=Awake" in output or "Display Power: state=ON" in output

    def wake_screen(self):
        """Turn on the screen if it's off."""
        if self.display_id is not None:
            self.validate_display_target()
            return
        if not self.is_screen_on():
            self.key_event(26)  # KEYCODE_POWER
