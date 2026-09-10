"""Low-overhead, read-only sampling of this app and its attributable helpers.

CPU is delta(user + system time) / monotonic elapsed time: 100% is one CPU core.
RSS is resident memory, not unique memory or Activity Monitor's footprint.
No shell subprocess, ADB call, privileged profiler, or device-wide GPU estimate
is used. This monitor does not collect GPU counters. Apple's powermetrics
requires administrator access; elevation alone does not add that integration.
"""

from dataclasses import dataclass
import html
import math
import os
from pathlib import Path
import re
import stat
import struct
import threading
import time

from PySide6.QtCore import QThread, Signal

from ..config import PROJECT_ROOT

try:
    import psutil
except ImportError:
    psutil = None


GPU_UNAVAILABLE = (
    "This monitor does not measure per-process GPU usage. GPU acceleration can still be active. "
    "Apple's powermetrics profiler requires administrator access; this app does not run it."
)


@dataclass(frozen=True)
class ProcessUsage:
    pid: int
    label: str
    cpu_percent: float | None = None
    rss_bytes: int | None = None
    state: str = ""


@dataclass(frozen=True)
class ResourceSnapshot:
    processes: tuple[ProcessUsage, ...]
    note: str = ""
    gpu_reason: str = GPU_UNAVAILABLE


class ResourceSampler:
    """Sample only descendants or the session-verified sibling scrcpy process."""

    def __init__(self, *, root_pid=None, environment=None, backend=psutil,
                 clock=time.monotonic):
        self.root_pid = os.getpid() if root_pid is None else root_pid
        source = os.environ if environment is None else environment
        self.environment = {key: source[key] for key in
                            ("POKEMGR_STREAM_PID", "POKEMGR_FRAME_SESSION", "POKEMGR_FRAME_BUFFER")
                            if key in source}
        self.backend = backend
        self.clock = clock
        self._root_started = None
        self._stream_started = None
        self._stream_file = None
        self._previous = {}

    def _paired_stream(self, root):
        """The stream is a sibling, so PID/name matching alone is insufficient."""
        env = self.environment
        raw_pid = env.get("POKEMGR_STREAM_PID", "")
        session = env.get("POKEMGR_FRAME_SESSION", "")
        path = Path(env.get("POKEMGR_FRAME_BUFFER", ""))
        if (not re.fullmatch(r"[1-9][0-9]{0,9}", raw_pid)
                or int(raw_pid) >= 1 << 31 or not path.is_absolute()
                or not re.fullmatch(r"[0-9a-f]{32}", session)):
            return None
        process = self.backend.Process(int(raw_pid))
        started = process.create_time()
        if (process.ppid() != root.ppid() or process.pid == root.pid
                or started > root.create_time()
                or Path(process.exe()).resolve() !=
                    (PROJECT_ROOT / "cache/scrcpy-frame-build/app/scrcpy").resolve()
                or (self._stream_started is not None and started != self._stream_started)):
            return None
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            identity = (info.st_dev, info.st_ino, info.st_size)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) & 0o077
                    or (self._stream_file is not None and identity != self._stream_file)):
                return None
            raw = os.pread(fd, 128, 0)
        finally:
            os.close(fd)
        if (len(raw) != 128 or raw[:8] != b"PKFRM001"
                or struct.unpack_from("<II", raw, 8) != (1, 128)
                or struct.unpack_from("<Q", raw, 48)[0] != process.pid
                or struct.unpack_from("<I", raw, 56)[0] != 1
                or raw[64:96] != session.encode("ascii") or not process.is_running()):
            return None
        self._stream_started, self._stream_file = started, identity
        return process

    @staticmethod
    def _helper_label(process):
        executable = Path(process.exe())
        if (executable.parent.resolve() == (PROJECT_ROOT / "cache/native-ocr").resolve()
                and re.fullmatch(r"vision-[0-9a-f]{16}", executable.name)):
            return "OCR"
        if executable.name == "adb":
            return "ADB helper"
        return f"Helper: {executable.name or process.name()}"

    def sample(self):
        if self.backend is None:
            return ResourceSnapshot((ProcessUsage(self.root_pid, "App", state="Unavailable"),),
                                    "Resource monitoring requires psutil.")
        errors = (self.backend.Error, OSError, ValueError)
        notes = []
        try:
            root = self.backend.Process(self.root_pid)
            started = root.create_time()
            if self._root_started is not None and started != self._root_started:
                raise ValueError("App process identity changed")
            self._root_started = started
        except errors:
            self._previous = {}
            return ResourceSnapshot((ProcessUsage(self.root_pid, "App", state="Unavailable"),))
        targets = [(root, "App")]
        try:
            # psutil verifies parent/child creation times, including PID reuse.
            children = root.children(recursive=True)
            if len(children) > 64:
                notes.append("Showing the first 64 helpers.")
            for child in children[:64]:
                try:
                    targets.append((child, self._helper_label(child)))
                except errors:
                    notes.append("A helper ended or its counters were unavailable.")
        except errors:
            notes.append("Helper process discovery is unavailable.")
        if self.environment.get("POKEMGR_STREAM_PID"):
            try:
                stream = self._paired_stream(root)
                if stream is not None:
                    targets.append((stream, "Stream"))
                else:
                    notes.append("Paired stream identity is unavailable.")
            except errors:
                notes.append("Paired stream is unavailable.")
        rows, current, seen = [], {}, set()
        for process, label in targets:
            if process.pid in seen:
                continue
            seen.add(process.pid)
            try:
                if not process.is_running():
                    continue
                with process.oneshot():
                    identity = (process.pid, process.create_time())
                    cpu_times = process.cpu_times()
                    cpu = cpu_times.user + cpu_times.system
                    rss = process.memory_info().rss
                sampled = self.clock()
                if not process.is_running():
                    continue  # Never attach counters to a recycled PID.
                previous = self._previous.get(identity)
                percent = None
                if previous is not None:
                    elapsed, used = sampled - previous[0], cpu - previous[1]
                    if elapsed > 0 and used >= 0 and math.isfinite(used / elapsed):
                        percent = 100 * used / elapsed
                if not math.isfinite(cpu) or cpu < 0 or rss < 0:
                    raise ValueError("Invalid resource counter")
                current[identity] = (sampled, cpu)
                rows.append(ProcessUsage(process.pid, label, percent, rss))
            except errors:
                rows.append(ProcessUsage(process.pid, label, state="Unavailable"))
        self._previous = current
        return ResourceSnapshot(tuple(rows), " ".join(dict.fromkeys(notes)))


def _memory(value):
    return f"{value / 2**30:.2f} GiB" if value >= 2**30 else f"{value / 2**20:.0f} MiB"


def resource_text(snapshot):
    """Compact total with per-process, explicitly attributed detail on hover."""
    rows = snapshot.processes
    measured = [row.cpu_percent for row in rows if row.cpu_percent is not None]
    cpu = (f"{sum(measured):.1f}%" + (" (partial)" if len(measured) != len(rows) else "")
           if measured else "unavailable" if not rows or any(row.state for row in rows) else "measuring")
    memory_ready = bool(rows) and all(row.rss_bytes is not None for row in rows)
    memory = _memory(sum(row.rss_bytes for row in rows)) if memory_ready else "unavailable"
    summary = f"App + helpers: CPU {cpu} · RAM {memory} · GPU usage not measured"
    details = ["<b>App and verified helpers</b><table>",
               "<tr><th align='left'>Process</th><th>PID</th><th>CPU</th><th>RAM (RSS)</th></tr>"]
    for row in rows:
        percent = f"{row.cpu_percent:.1f}%" if row.cpu_percent is not None else (row.state or "Measuring")
        resident = _memory(row.rss_bytes) if row.rss_bytes is not None else "Unavailable"
        details.append(f"<tr><td>{html.escape(row.label)}</td><td>{row.pid}</td>"
                       f"<td>{percent}</td><td>{resident}</td></tr>")
    details.append("</table><p>Updates every 2 seconds. CPU 100% = one core; multiple cores can exceed 100%."
                   "<br>A partial CPU total excludes processes still measuring or unavailable."
                   "<br>RAM is resident memory (RSS); shared pages can appear in more than one process."
                   "<br>Short-lived helpers may finish between samples.</p>")
    details.append(f"<p>GPU usage not measured: {html.escape(snapshot.gpu_reason)}</p>")
    if snapshot.note:
        details.append(f"<p>{html.escape(snapshot.note)}</p>")
    return summary, "".join(details)


class ResourceMonitor(QThread):
    """All discovery/counter reads run off the GUI thread; stop wakes the timer."""

    updated = Signal(object)

    def __init__(self, parent=None, *, sampler=None, interval=2.0):
        super().__init__(parent)
        self._sampler = sampler or ResourceSampler()
        self._interval = interval
        self._stop_event = threading.Event()

    def run(self):
        while not self._stop_event.is_set():
            try:
                snapshot = self._sampler.sample()
            except Exception:
                snapshot = ResourceSnapshot((), "Resource counters are unavailable.")
            if not self._stop_event.is_set():
                self.updated.emit(snapshot)
            self._stop_event.wait(self._interval)

    def stop(self, timeout_ms=1000):
        self._stop_event.set()
        return self.wait(timeout_ms)
