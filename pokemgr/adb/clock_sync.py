"""Bound Android source PTS in the host monotonic clock without midpoint guesses.

Only original, unshifted Android surface PTS are supported. Disable encoder
synthetic repeats/time-lapse. Invalidate on pause/resume, reconnect, device or
stream changes, and host suspend. A finite drift allowance is an explicit
operating assumption, not a guarantee about arbitrary hardware clocks.
"""

from dataclasses import dataclass
import os
import re
import secrets
import selectors
import shlex
import subprocess
import threading
from time import monotonic_ns


# TRACEWEAVER: file-role=source-clock-bounds; req=REQ-STREAM-001; trace=TRACE-STREAM-001; ver=VER-SCAN-001
DEFAULT_REMOTE_JAR = "/data/local/tmp/pokemgr-clock.jar"
_REPLY = re.compile(
    r"POKEMGR_CLOCK_V1 ([0-9a-f]{32}) "
    r"([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}) "
    r"([0-9]{1,19}) ([0-9]{1,19}) ([0-9]{1,19})"
)


class ClockSyncError(RuntimeError):
    """Source freshness cannot be established with the current clock evidence."""


def _positive_int(value):
    return type(value) is int and 0 < value < 1 << 63


@dataclass(frozen=True)
class ClockSample:
    device_serial: str
    boot_id: str
    host_send_ns: int
    host_receive_ns: int
    device_before_ns: int
    device_after_ns: int
    device_boottime_ns: int

    def __post_init__(self):
        values = (self.host_send_ns, self.host_receive_ns, self.device_before_ns,
                  self.device_after_ns, self.device_boottime_ns)
        if (not all(_positive_int(value) for value in values)
                or self.host_receive_ns < self.host_send_ns
                or self.device_after_ns < self.device_before_ns
                or self.device_boottime_ns < self.device_before_ns
                or not isinstance(self.device_serial, str) or not self.device_serial.strip()
                or not isinstance(self.boot_id, str)
                or not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", self.boot_id)):
            raise ClockSyncError("Malformed clock sample")

    @property
    def round_trip_ns(self):
        return self.host_receive_ns - self.host_send_ns

    @classmethod
    def from_reply(cls, reply, *, nonce, device_serial, host_send_ns, host_receive_ns):
        match = _REPLY.fullmatch(reply.strip()) if isinstance(reply, str) else None
        if match is None or match[1] != nonce:
            raise ClockSyncError("Clock helper reply is invalid or belongs to another request")
        return cls(device_serial, match[2], host_send_ns, host_receive_ns,
                   int(match[3]), int(match[5]), int(match[4]))


def sample_clock(adb, *, remote_jar=DEFAULT_REMOTE_JAR):
    """Sample an already deployed helper on the explicitly selected device.

    Deployment is deliberately separate: this function never pushes files,
    changes settings, or supplies input. The caller coordinates device access.
    """
    serial = adb.serial
    if not isinstance(serial, str) or not serial.strip():
        raise ClockSyncError("Clock sampling requires an explicitly selected device")
    if not isinstance(remote_jar, str) or not remote_jar.startswith("/data/local/tmp/"):
        raise ClockSyncError("Clock helper must be deployed under /data/local/tmp/")
    nonce = secrets.token_hex(16)
    command = (f"CLASSPATH={shlex.quote(remote_jar)} "
               f"app_process / pokemgr.tools.ClockSample {nonce}")
    sent = monotonic_ns()
    reply = adb.shell(command)
    received = monotonic_ns()
    if adb.serial != serial:
        raise ClockSyncError("Selected device changed during clock sampling")
    return ClockSample.from_reply(
        reply, nonce=nonce, device_serial=serial,
        host_send_ns=sent, host_receive_ns=received,
    )


class ClockSampler:
    """One owned app_process helper with bounded nonce exchanges over ADB pipes.

    JVM startup/READY happens before timing a sample. Nonblocking pipe I/O has
    explicit deadlines. The helper never reads or changes the game/display.
    """

    def __init__(self, adb, *, remote_jar=DEFAULT_REMOTE_JAR,
                 startup_timeout_s=5.0, sample_timeout_s=1.0):
        if not isinstance(adb.serial, str) or not adb.serial.strip():
            raise ClockSyncError("Clock sampling requires an explicitly selected device")
        if not isinstance(remote_jar, str) or not remote_jar.startswith("/data/local/tmp/"):
            raise ClockSyncError("Clock helper must be deployed under /data/local/tmp/")
        if not 0 < startup_timeout_s <= 30 or not 0 < sample_timeout_s <= 5:
            raise ValueError("Clock helper timeouts must be positive and bounded")
        self.adb = adb
        self.serial = adb.serial
        self.remote_jar = remote_jar
        self.startup_timeout_ns = int(startup_timeout_s * 1_000_000_000)
        self.sample_timeout_ns = int(sample_timeout_s * 1_000_000_000)
        self._process = None
        self._buffer = b""
        self._last_read_ns = 0
        self._closed = False
        self._lock = threading.RLock()

    @staticmethod
    def _wait_pipe(pipe, event, deadline):
        remaining_ns = deadline - monotonic_ns()
        if remaining_ns <= 0:
            raise ClockSyncError("Clock helper I/O timed out")
        with selectors.DefaultSelector() as selector:
            selector.register(pipe, event)
            if not selector.select(remaining_ns / 1_000_000_000):
                raise ClockSyncError("Clock helper I/O timed out")

    def _read_line(self, deadline):
        while b"\n" not in self._buffer:
            if len(self._buffer) >= 512:
                raise ClockSyncError("Clock helper response exceeded protocol limit")
            self._wait_pipe(self._process.stdout, selectors.EVENT_READ, deadline)
            try:
                chunk = os.read(self._process.stdout.fileno(), 512 - len(self._buffer))
            except BlockingIOError:
                continue
            self._last_read_ns = monotonic_ns()
            if self._last_read_ns > deadline:
                raise ClockSyncError("Clock helper I/O timed out")
            if not chunk:
                raise ClockSyncError("Clock helper exited before replying")
            self._buffer += chunk
        line, self._buffer = self._buffer.split(b"\n", 1)
        if self._buffer:
            raise ClockSyncError("Clock helper returned unsolicited extra data")
        try:
            return line.decode("ascii"), self._last_read_ns
        except UnicodeDecodeError as exc:
            raise ClockSyncError("Clock helper response was not ASCII") from exc

    def _write_request(self, payload, deadline):
        view = memoryview(payload)
        while view:
            self._wait_pipe(self._process.stdin, selectors.EVENT_WRITE, deadline)
            try:
                written = os.write(self._process.stdin.fileno(), view)
            except BlockingIOError:
                continue
            if written <= 0:
                raise ClockSyncError("Clock helper input closed")
            view = view[written:]

    def _start(self):
        command = (f"CLASSPATH={shlex.quote(self.remote_jar)} "
                   "app_process / pokemgr.tools.ClockSample --loop")
        self._process = subprocess.Popen(
            [self.adb.adb_path, "-s", self.serial, "shell", "-T", command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        os.set_blocking(self._process.stdin.fileno(), False)
        os.set_blocking(self._process.stdout.fileno(), False)
        ready, _received = self._read_line(monotonic_ns() + self.startup_timeout_ns)
        if ready != "POKEMGR_CLOCK_READY_V1":
            raise ClockSyncError("Clock helper READY handshake was invalid")

    def sample(self):
        with self._lock:
            try:
                if self._closed or self.adb.serial != self.serial:
                    raise ClockSyncError("Clock sampler is closed or selected device changed")
                if self._process is None:
                    self._start()
                if self._process.poll() is not None:
                    raise ClockSyncError("Clock helper is no longer running")
                nonce = secrets.token_hex(16)
                sent = monotonic_ns()
                deadline = sent + self.sample_timeout_ns
                self._write_request((nonce + "\n").encode("ascii"), deadline)
                reply, received = self._read_line(deadline)
                if self.adb.serial != self.serial:
                    raise ClockSyncError("Selected device changed during clock sampling")
                return ClockSample.from_reply(
                    reply, nonce=nonce, device_serial=self.serial,
                    host_send_ns=sent, host_receive_ns=received,
                )
            except Exception:
                self.close()
                raise

    def close(self):
        """Close only this sampler's owned ADB/helper process; safe to repeat."""
        with self._lock:
            self._closed = True
            process, self._process = self._process, None
            if process is None:
                return
            try:
                if process.stdin is not None:
                    try:
                        process.stdin.close()  # Helper exits on EOF.
                    except OSError:
                        pass
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                # Cleanup must not replace the original protocol/I/O failure.
                pass
            finally:
                if process.stdout is not None:
                    try:
                        process.stdout.close()
                    except OSError:
                        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        self.close()


@dataclass(frozen=True)
class FrameTimeBounds:
    earliest_ns: int
    latest_ns: int
    sync_generation: int
    valid_until_ns: int
    continuity_token: str | None = None

    @property
    def uncertainty_ns(self):
        return self.latest_ns - self.earliest_ns

    def strictly_after(self, completed_ns):
        """True only when every possible source time is after the host action."""
        return _positive_int(completed_ns) and self.earliest_ns > completed_ns

    def fresh_at(self, now_ns, *, max_age_ns):
        return (_positive_int(now_ns) and _positive_int(max_age_ns)
                and now_ns <= self.valid_until_ns
                and self.latest_ns <= now_ns
                and now_ns - self.earliest_ns <= max_age_ns)


@dataclass(frozen=True)
class _Mapping:
    sample: ClockSample
    offset_low_ns: int
    offset_high_ns: int


class AndroidClockSync:
    """Short-lived offset intervals, checked on each sample and frame mapping.

    Refresh before max_age_ns (30s by default). The default 1000ppm relative
    drift budget widens both interval ends; it never improves measured RTT.
    Use generation to discard bounds created before any resync/invalidation.
    Continuity is separate evidence: compatible refreshes retain its token,
    while invalidation, failure or an expired sample-coverage gap rotates it.
    """

    def __init__(self, *, max_age_ns=30_000_000_000, max_rtt_ns=1_000_000_000,
                 max_drift_ppm=1000):
        if (not _positive_int(max_age_ns) or not _positive_int(max_rtt_ns)
                or type(max_drift_ppm) is not int or not 0 <= max_drift_ppm < 1_000_000):
            raise ValueError("Invalid clock-sync limits")
        self.max_age_ns = max_age_ns
        self.max_rtt_ns = max_rtt_ns
        self.max_drift_ppm = max_drift_ppm
        self._mapping = None
        self._generation = 0
        self._continuity_token = secrets.token_hex(16)
        self._lock = threading.RLock()

    @property
    def generation(self):
        with self._lock:
            return self._generation

    @property
    def continuity_token(self):
        with self._lock:
            return self._continuity_token

    def invalidate(self):
        with self._lock:
            self._mapping = None
            self._generation += 1
            self._continuity_token = secrets.token_hex(16)

    def _fail(self, reason):
        self.invalidate()
        raise ClockSyncError(reason)

    def _drift(self, elapsed_ns):
        # The denominator also covers expressing elapsed time in either clock.
        denominator = 1_000_000 - self.max_drift_ppm
        return (abs(elapsed_ns) * self.max_drift_ppm + denominator - 1) // denominator

    # TRACEWEAVER: entrypoint=AndroidClockSync.observe; req=REQ-STREAM-001; trace=TRACE-STREAM-001; ver=VER-SCAN-001
    def observe(self, sample):
        """Intersect compatible samples; reject clock changes instead of guessing."""
        with self._lock:
            if not isinstance(sample, ClockSample) or sample.round_trip_ns > self.max_rtt_ns:
                self._fail("Clock sample is missing or its round trip is too uncertain")
            spread = self._drift(sample.round_trip_ns)
            if sample.device_after_ns - sample.device_before_ns > sample.round_trip_ns + spread:
                self._fail("Device sampling interval exceeds host round-trip bounds")
            low = sample.host_send_ns - sample.device_after_ns - spread
            high = sample.host_receive_ns - sample.device_before_ns + spread
            previous = self._mapping
            if previous is not None:
                old = previous.sample
                if sample.device_serial != old.device_serial or sample.boot_id != old.boot_id:
                    self._fail("Clock sample belongs to a different device or boot")
                if (sample.host_send_ns < old.host_receive_ns
                        or sample.device_before_ns <= old.device_after_ns):
                    self._fail("Clock sample went backwards or was reused")
                old_sleep = (old.device_boottime_ns - old.device_after_ns,
                             old.device_boottime_ns - old.device_before_ns)
                new_sleep = (sample.device_boottime_ns - sample.device_after_ns,
                             sample.device_boottime_ns - sample.device_before_ns)
                if new_sleep[0] > old_sleep[1] or old_sleep[0] > new_sleep[1]:
                    self._fail("Android suspended or its clock basis changed; resync required")
                elapsed = sample.host_receive_ns - old.host_receive_ns
                spread = self._drift(elapsed)
                low = max(low, previous.offset_low_ns - spread)
                high = min(high, previous.offset_high_ns + spread)
                if low > high:
                    self._fail("Host/device clock offset changed outside its bounds")
                if elapsed > self.max_age_ns:
                    # Compatibility can establish a new mapping, but cannot
                    # retroactively fill a gap in source-clock coverage.
                    self._continuity_token = secrets.token_hex(16)
            self._mapping = _Mapping(sample, low, high)
            self._generation += 1
            return self._generation

    # TRACEWEAVER: entrypoint=AndroidClockSync.map_pts_us; req=REQ-STREAM-001; trace=TRACE-STREAM-001; ver=VER-SCAN-001
    def map_pts_us(self, pts_us, *, now_ns=None, received_ns=None):
        """Map original source microseconds, retaining full uncertainty.

        received_ns is the host decoder's receipt time. It supplies a causal
        upper bound, never a replacement for the source timestamp. Invalid
        clocks raise; stale frames should additionally fail fresh_at/after gates.
        """
        with self._lock:
            now = monotonic_ns() if now_ns is None else now_ns
            received = now if received_ns is None else received_ns
            if (not _positive_int(pts_us) or pts_us >= 1 << 61
                    or not _positive_int(now) or not _positive_int(received) or received > now):
                self._fail("Frame timestamp or receipt time is invalid")
            mapping = self._mapping
            if mapping is None:
                raise ClockSyncError("Source clock has not been synchronized")
            sample = mapping.sample
            age = now - sample.host_receive_ns
            if age < 0 or age > self.max_age_ns:
                self._fail("Source clock synchronization expired or host clock changed")
            device_ns = pts_us * 1000
            elapsed = max(age, abs(device_ns - sample.device_before_ns),
                          abs(device_ns - sample.device_after_ns))
            spread = self._drift(elapsed)
            earliest = device_ns + mapping.offset_low_ns - spread
            # Source timestamps are truncated to microseconds before encoding.
            latest = device_ns + 999 + mapping.offset_high_ns + spread
            if earliest > received:
                self._fail("Source PTS is ahead of receipt; wrong clock or transformed timestamp")
            return FrameTimeBounds(earliest, min(latest, received), self._generation,
                                   sample.host_receive_ns + self.max_age_ns,
                                   self._continuity_token)
