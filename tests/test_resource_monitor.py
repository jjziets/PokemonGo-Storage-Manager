"""Resource rates, process attribution, unavailable metrics, and shutdown."""

from contextlib import nullcontext
import os
from pathlib import Path
import struct
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pokemgr.config import PROJECT_ROOT
from pokemgr.gui.main_window import MainWindow
from pokemgr.gui.resource_monitor import (
    ProcessUsage, ResourceSnapshot, ResourceSampler, ResourceMonitor, resource_text,
)


class ProcessError(Exception):
    pass


class FakeProcess:
    def __init__(self, pid, parent=1, started=10., executable="/python", cpu=1., rss=100):
        self.pid, self.parent, self.started = pid, parent, started
        self.executable, self.cpu, self.rss = executable, cpu, rss
        self.descendants, self.running = [], True

    def ppid(self): return self.parent
    def create_time(self): return self.started
    def exe(self): return str(self.executable)
    def name(self): return Path(self.executable).name
    def children(self, recursive=False):
        assert recursive
        return self.descendants
    def oneshot(self): return nullcontext()
    def is_running(self): return self.running
    def cpu_times(self):
        if self.cpu is None:
            raise ProcessError("denied")
        return SimpleNamespace(user=self.cpu, system=0., children_user=999.)
    def memory_info(self): return SimpleNamespace(rss=self.rss)


class ResourceSamplerTests(unittest.TestCase):
    def setUp(self):
        self.root = FakeProcess(10)
        self.processes = {10: self.root}
        self.now = 100.
        def process(pid):
            if pid not in self.processes:
                raise ProcessError("gone")
            return self.processes[pid]
        self.backend = SimpleNamespace(Process=process, Error=ProcessError)
        self.sampler = ResourceSampler(root_pid=10, environment={}, backend=self.backend,
                                       clock=lambda: self.now)

    def paired(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.frame_path = Path(temporary.name) / "session.frames"
        raw = bytearray(128)
        raw[:8] = b"PKFRM001"
        struct.pack_into("<II", raw, 8, 1, 128)
        struct.pack_into("<QI", raw, 48, 20, 1)
        raw[64:96] = b"a" * 32
        self.frame_path.write_bytes(raw)
        self.frame_path.chmod(0o600)
        self.stream = self.processes[20] = FakeProcess(
            20, started=9., executable=PROJECT_ROOT / "cache/scrcpy-frame-build/app/scrcpy")
        self.sampler.environment = {"POKEMGR_STREAM_PID": "20", "POKEMGR_FRAME_SESSION": "a"*32,
                                    "POKEMGR_FRAME_BUFFER": str(self.frame_path)}

    def test_cpu_delta_excludes_child_time_and_can_exceed_one_core(self):
        self.assertIsNone(self.sampler.sample().processes[0].cpu_percent)
        self.now += 2
        self.root.cpu += 3
        row = self.sampler.sample().processes[0]
        self.assertEqual(row.cpu_percent, 150.)
        self.assertEqual(row.rss_bytes, 100)

    def test_discovers_only_own_descendants_and_labels_native_ocr(self):
        self.processes[30] = FakeProcess(30, parent=999, cpu=1000.)  # unrelated
        child = FakeProcess(11, parent=10, executable=PROJECT_ROOT / "cache/native-ocr/vision-0123456789abcdef")
        self.root.descendants = [child]
        rows = self.sampler.sample().processes
        self.assertEqual([(r.pid, r.label) for r in rows], [(10, "App"), (11, "OCR")])

    def test_pid_reuse_starts_a_new_baseline_and_exited_helpers_disappear(self):
        child = FakeProcess(11, parent=10)
        self.root.descendants = [child]
        self.sampler.sample()
        self.now += 2
        child.started, child.cpu = 11., 200.
        self.assertIsNone(self.sampler.sample().processes[1].cpu_percent)
        child.running = False
        self.assertEqual([r.pid for r in self.sampler.sample().processes], [10])

    def test_counter_failure_resets_baseline_and_is_not_reported_as_zero(self):
        self.sampler.sample()
        self.root.cpu = None
        row = self.sampler.sample().processes[0]
        self.assertIsNone(row.cpu_percent)
        self.assertEqual(row.state, "Unavailable")
        self.root.cpu = 100.
        self.now += 2
        self.assertIsNone(self.sampler.sample().processes[0].cpu_percent)

    def test_decreasing_counter_and_zero_elapsed_do_not_invent_usage(self):
        self.sampler.sample()
        self.root.cpu += 1
        self.assertIsNone(self.sampler.sample().processes[0].cpu_percent)
        self.now += 2
        self.root.cpu = 0
        self.assertIsNone(self.sampler.sample().processes[0].cpu_percent)

    def test_sibling_stream_requires_matching_parent_executable_and_private_session(self):
        self.paired()
        self.assertEqual([r.label for r in self.sampler.sample().processes], ["App", "Stream"])
        for attribute, invalid in (("parent", 999), ("executable", "/other/scrcpy"), ("started", 12.)):
            original = getattr(self.stream, attribute)
            setattr(self.stream, attribute, invalid)
            self.assertEqual([r.pid for r in self.sampler.sample().processes], [10])
            setattr(self.stream, attribute, original)
        self.sampler.environment["POKEMGR_FRAME_SESSION"] = "b"*32
        self.assertEqual([r.pid for r in self.sampler.sample().processes], [10])

    def test_replaced_or_public_stream_file_and_recycled_stream_pid_are_rejected(self):
        self.paired()
        self.sampler.sample()
        self.stream.started = 9.5
        self.assertEqual([r.pid for r in self.sampler.sample().processes], [10])
        self.stream.started = 9.
        self.frame_path.chmod(0o644)
        self.assertEqual([r.pid for r in self.sampler.sample().processes], [10])
        self.frame_path.chmod(0o600)
        replacement = self.frame_path.with_suffix(".new")
        replacement.write_bytes(self.frame_path.read_bytes())
        replacement.chmod(0o600)
        replacement.replace(self.frame_path)
        self.assertEqual([r.pid for r in self.sampler.sample().processes], [10])

    def test_gpu_is_unmeasured_and_tooltip_reports_each_process(self):
        snapshot = ResourceSnapshot((ProcessUsage(10, "App", 10., 2**30),
                                     ProcessUsage(20, "OCR", 80., 2**20)))
        summary, detail = resource_text(snapshot)
        self.assertIn("CPU 90.0%", summary)
        self.assertIn("GPU usage not measured", summary)
        self.assertIn("<td>10</td><td>10.0%</td>", detail)
        self.assertIn("<td>20</td><td>80.0%</td>", detail)
        self.assertIn("100% = one core", detail)
        self.assertIn("shared pages", detail)
        self.assertIn("GPU acceleration can still be active", detail)
        self.assertIn("requires administrator access", detail)
        self.assertIn("this app does not run it", detail)
        summary, _ = resource_text(ResourceSampler(backend=None).sample())
        self.assertIn("CPU unavailable", summary)

    def test_new_short_lived_helper_does_not_hide_app_rate_or_count_as_idle(self):
        summary, detail = resource_text(ResourceSnapshot((ProcessUsage(10, "App", 75., 1024),
                                                         ProcessUsage(20, "ADB helper", None, 128))))
        self.assertIn("CPU 75.0% (partial)", summary)
        self.assertIn("<td>20</td><td>Measuring</td>", detail)


class ResourceMonitorLifecycleTests(unittest.TestCase):
    def test_sampling_runs_off_gui_thread_and_stop_wakes_long_interval(self):
        entered, sample_threads = threading.Event(), []
        def sample():
            sample_threads.append(threading.get_ident())
            entered.set()
            return ResourceSnapshot(())
        monitor = ResourceMonitor(sampler=SimpleNamespace(sample=sample), interval=20.)
        monitor.start()
        try:
            self.assertTrue(entered.wait(2))
            start = time.monotonic()
            self.assertTrue(monitor.stop())
            self.assertLess(time.monotonic() - start, .5)
            self.assertNotEqual(sample_threads[0], threading.get_ident())
        finally:
            monitor.stop()

    def test_window_close_waits_for_monitor_before_closing_shared_resources(self):
        events = []
        monitor = Mock()
        monitor.stop.return_value = False
        window = SimpleNamespace(_resource_monitor=monitor, adb=Mock(), db=Mock(),
                                 statusBar=Mock(return_value=Mock()))
        event = Mock()
        MainWindow.closeEvent(window, event)
        event.ignore.assert_called_once()
        window.adb.close_stream_capture.assert_not_called()
        monitor.stop.side_effect = lambda: events.append("monitor") or True
        window.adb.close_stream_capture.side_effect = lambda: events.append("stream")
        window.db.close.side_effect = lambda: events.append("database")
        MainWindow.closeEvent(window, Mock())
        self.assertEqual(events, ["monitor", "stream", "database"])

    def test_ui_update_sets_compact_text_and_detailed_tooltip(self):
        window = SimpleNamespace(_resource_label=Mock())
        MainWindow._update_resource_usage(window, ResourceSnapshot((ProcessUsage(10, "App", 25., 2**20),)))
        self.assertIn("CPU 25.0%", window._resource_label.setText.call_args.args[0])
        self.assertIn("<td>10</td>", window._resource_label.setToolTip.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
