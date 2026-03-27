"""ADB controller for screen capture, taps, swipes, and device interaction."""

import io
import subprocess
import re
import logging
from PIL import Image

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


class ADBController:
    """Controls an Android device via ADB for screen capture and input simulation."""

    def __init__(self, adb_path: str = ADB_PATH, serial: str | None = None):
        self.adb_path = adb_path
        self.serial = serial
        self._device_info: DeviceInfo | None = None

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
                result = self._run(["shell", cmd])
            else:
                raise ADBError("Device disconnected")
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
        """Target a specific device. If None, uses the first connected device."""
        if serial:
            self.serial = serial
            return

        devices = self.get_devices()
        if not devices:
            raise ADBError("No devices connected. Check USB connection and USB debugging.")
        self.serial = devices[0]
        log.info("Connected to device: %s", self.serial)

    def get_device_info(self) -> DeviceInfo:
        """Get device model, serial, resolution, and density."""
        if self._device_info:
            return self._device_info

        model = self.shell("getprop ro.product.model")
        serial = self.shell("getprop ro.serialno")

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
        log.warning("Device disconnected — waiting for reconnection (up to %ds)...", max_wait)
        deadline = time.time() + max_wait
        while time.time() < deadline:
            devices = self.get_devices()
            if devices:
                if self.serial and self.serial in devices:
                    log.info("Device %s reconnected!", self.serial)
                    return True
                elif devices:
                    self.serial = devices[0]
                    log.info("Device reconnected as %s", self.serial)
                    return True
            time.sleep(3)
        log.error("Device did not reconnect within %ds", max_wait)
        return False

    def screencap(self) -> Image.Image:
        """Capture the screen and return as a PIL Image.

        Uses exec-out for speed (no temp file on device).
        Handles foldable devices that prepend a "Multiple displays" warning.
        Waits for reconnection if device disconnects.
        """
        result = self._run(["exec-out", "screencap", "-p"])
        if result.returncode != 0:
            # Check if device disconnected
            stderr = result.stderr.decode(errors="replace") if result.stderr else ""
            if "not found" in stderr or "offline" in stderr:
                if self.wait_for_device():
                    # Retry after reconnect
                    result = self._run(["exec-out", "screencap", "-p"])
                    if result.returncode != 0:
                        raise ADBError("screencap failed after reconnect")
                else:
                    raise ADBError("Device disconnected and did not reconnect")
            else:
                raise ADBError("screencap failed")
        data = result.stdout
        if not data or len(data) < 100:
            raise ADBError(f"screencap returned {len(data) if data else 0} bytes — is the screen on?")

        # Foldable devices (Fold/Flip) prepend a warning about multiple displays.
        # Strip everything before the PNG magic bytes.
        png_magic = b"\x89PNG"
        idx = data.find(png_magic)
        if idx > 0:
            data = data[idx:]
        elif idx < 0:
            # No PNG header found at all — try file fallback
            log.warning("No PNG header in screencap output, falling back to file method")
            return self._screencap_file()

        try:
            return Image.open(io.BytesIO(data))
        except Exception:
            # Try CRLF fix (some devices corrupt LF → CRLF over USB)
            fixed = data.replace(b"\r\n", b"\n")
            try:
                return Image.open(io.BytesIO(fixed))
            except Exception as e:
                raise ADBError(f"Failed to decode screencap PNG ({len(data)} bytes): {e}")

    # ── Input simulation ─────────────────────────────────────────────

    def tap(self, x: int, y: int, jitter: int = DEFAULT_TAP_JITTER):
        """Tap at coordinates with random jitter for human emulation."""
        tx = jitter_coord(x, jitter)
        ty = jitter_coord(y, jitter)
        log.debug("Tap: (%d, %d) → (%d, %d)", x, y, tx, ty)
        self.shell(f"input tap {tx} {ty}")

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
        self.shell(f"input swipe {sx1} {sy1} {sx2} {sy2} {actual_duration}")

    def input_text(self, text: str):
        """Type text on the device using ADB.

        Wraps text in single quotes to prevent shell interpretation of
        special characters like ! & etc.
        """
        # Use shell with single quotes to prevent ! & interpretation
        self.shell(f"input text '{text}'")

    def key_event(self, keycode: int | str):
        """Send a key event (e.g., KEYCODE_BACK = 4)."""
        self.shell(f"input keyevent {keycode}")

    # ── Device status ────────────────────────────────────────────────

    def get_battery_level(self) -> int:
        """Return battery percentage (0-100)."""
        output = self.shell("dumpsys battery")
        match = re.search(r"level:\s*(\d+)", output)
        return int(match.group(1)) if match else -1

    def is_screen_on(self) -> bool:
        """Check if the device screen is currently on."""
        output = self.shell("dumpsys power")
        return "mWakefulness=Awake" in output or "Display Power: state=ON" in output

    def wake_screen(self):
        """Turn on the screen if it's off."""
        if not self.is_screen_on():
            self.key_event(26)  # KEYCODE_POWER
