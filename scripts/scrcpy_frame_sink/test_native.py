"""Offline native protocol, race, color and timing checks. No phone access."""
# TRACEWEAVER: file-role=native-stream-verification; verifies=VER-STREAM-ACTIVITY-001; req=REQ-STREAM-001; trace=TRACE-STREAM-001
from __future__ import annotations

import ctypes as c
import json
import mmap
import multiprocessing as mp
import os
from pathlib import Path
import statistics
import struct
import subprocess
import tempfile
import time

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parents[1]
BUILD = ROOT / "cache/scrcpy-frame-build"
SOURCE = ROOT / "cache/scrcpy-frame-source"
BRIDGE = BUILD / "libpk_frame_test.dylib"
READER = BUILD / "libpk_frame_reader.dylib"


def libraries():
    bridge = c.CDLL(str(BRIDGE))
    bridge.pk_test_create.argtypes = [c.c_char_p, c.c_int, c.c_int, c.c_int]
    bridge.pk_test_create.restype = c.c_void_p
    bridge.pk_test_push.argtypes = [c.c_void_p, c.c_int64, *([c.c_int] * 6)]
    bridge.pk_test_push.restype = c.c_int
    bridge.pk_test_destroy.argtypes = [c.c_void_p]
    bridge.pk_test_init_only.argtypes = [c.c_char_p, c.c_char_p]
    bridge.pk_test_init_only.restype = c.c_void_p
    bridge.pk_test_close.argtypes = [c.c_void_p]
    bridge.sc_pk_activity_test_active_count.restype = c.c_uint
    reader = c.CDLL(str(READER))
    reader.pk_frame_open.argtypes = [c.c_int, c.c_size_t]
    reader.pk_frame_open.restype = c.c_void_p
    reader.pk_frame_close.argtypes = [c.c_void_p, c.c_size_t]
    reader.pk_frame_copy.argtypes = [c.c_void_p, c.c_size_t, c.c_size_t, c.c_uint64,
                                     c.c_void_p, c.c_size_t,
                                     c.POINTER(c.c_int64), c.POINTER(c.c_uint64)]
    reader.pk_frame_copy.restype = c.c_int
    return bridge, reader


class Buffer:
    def __init__(self, path, reader):
        self.fd = os.open(path, os.O_RDONLY)
        self.size = os.fstat(self.fd).st_size
        self.mm = mmap.mmap(self.fd, self.size, access=mmap.ACCESS_READ)
        self.base = reader.pk_frame_open(self.fd, self.size)
        assert self.base
        self.reader = reader
        self.slot_size = struct.unpack_from("<Q", self.mm, 32)[0]
        self.payload = self.slot_size - 64
        self.out = (c.c_uint8 * self.payload)()

    def copy(self, seq):
        pts, received = c.c_int64(), c.c_uint64()
        result = self.reader.pk_frame_copy(self.base, self.size,
                    128 + (seq - 1) % 30 * self.slot_size, seq,
                    self.out, self.payload, c.byref(pts), c.byref(received))
        return result, pts.value, received.value

    def close(self):
        self.reader.pk_frame_close(self.base, self.size)
        self.mm.close()
        os.close(self.fd)


def race_reader(path, ready, done, results):
    _, reader = libraries()
    buf = Buffer(path, reader)
    ready.set()
    accepted = raced = 0
    try:
        while not done.is_set():
            seq = struct.unpack_from("<Q", buf.mm, 40)[0]
            if not seq:
                continue
            result, pts, _ = buf.copy(seq)
            if result == 0:
                raced += 1
                continue
            assert result == 1 and pts == seq * 1000
            assert bytes(buf.out) == bytes([seq % 251]) * buf.payload
            accepted += 1
        results.put(dict(accepted=accepted, raced=raced))
    except BaseException as exc:
        results.put(dict(error=repr(exc)))
    finally:
        buf.close()


def compile_bridge():
    subprocess.run(["/usr/bin/clang", "-std=c11", "-O3", "-Wall", "-Wextra", "-Werror",
        "-fno-objc-arc", "-DSC_PK_ACTIVITY_TEST",
        "-dynamiclib", f"-I{PACKAGE}", f"-I{SOURCE / 'app/src'}", f"-I{BUILD / 'app'}",
        "-I/opt/homebrew/opt/ffmpeg/include", "-I/opt/homebrew/opt/sdl3/include",
        str(PACKAGE / "test_bridge.c"), str(PACKAGE / "pokemgr_frame_sink.c"),
        str(PACKAGE / "pokemgr_activity.m"),
        "-L/opt/homebrew/opt/ffmpeg/lib", "-L/opt/homebrew/opt/sdl3/lib",
        "-lavcodec", "-lavutil", "-lswscale", "-lSDL3", "-framework", "Accelerate",
        "-framework", "Foundation",
        "-o", str(BRIDGE)], check=True)


def main():
    compile_bridge()
    bridge, reader = libraries()
    report = dict(checks=[], benchmarks={})
    with tempfile.TemporaryDirectory(prefix="pk-frame-offline-") as tmp:
        tmp = Path(tmp)
        # TRACEWEAVER: req=REQ-STREAM-001; trace=TRACE-STREAM-001
        active = bridge.sc_pk_activity_test_active_count
        nonce = b"0123456789abcdef0123456789abcdef"
        assert active() == 0
        assert not bridge.pk_test_init_only(b"relative.bin", nonce)
        assert not bridge.pk_test_init_only(os.fsencode(tmp / "invalid.bin"), b"bad")
        assert active() == 0
        initialized = bridge.pk_test_init_only(os.fsencode(tmp / "init-only.bin"), nonce)
        assert initialized and active() == 1
        bridge.pk_test_close(initialized)
        assert active() == 0
        bridge.pk_test_close(initialized)
        bridge.pk_test_destroy(initialized)
        assert active() == 0
        initialized = bridge.pk_test_init_only(os.fsencode(tmp / "destroy-only.bin"), nonce)
        assert initialized and active() == 1
        bridge.pk_test_destroy(initialized)
        assert active() == 0
        report["checks"].append("activity starts only on valid init and ends once on close/destroy")
        path = tmp / "valid.bin"
        handle = bridge.pk_test_create(os.fsencode(path), 256, 256, 1)
        assert handle and active() == 1
        assert os.stat(path).st_mode & 0o777 == 0o600
        assert not bridge.pk_test_create(os.fsencode(path), 256, 256, 1)
        assert active() == 1  # Failed duplicate open releases only its own activity.
        buf = Buffer(path, reader)
        assert buf.mm[:8] == b"PKFRM001"
        assert struct.unpack_from("<6I", buf.mm, 8) == (1, 128, 30, 256, 256, 3)
        assert buf.mm[64:96] == b"0123456789abcdef0123456789abcdef"
        before = time.monotonic_ns()
        assert bridge.pk_test_push(handle, 987654321012, 55, 128, 128, 1, 1, 0)
        after = time.monotonic_ns()
        result, pts, received = buf.copy(1)
        assert result == 1 and pts == 987654321012 and before <= received <= after
        assert bytes(buf.out) == b"\x37" * buf.payload
        assert buf.copy(2)[0] == 0
        report["checks"] += ["wire metadata", "0600/O_EXCL", "unmodified PTS",
                             "Python monotonic clock domain", "RGB pixel equality"]
        for seq in range(2, 62):
            assert bridge.pk_test_push(handle, seq * 1000, seq % 251, 128, 128, 1, 1, 0)
        assert buf.copy(1)[0] == 0 and buf.copy(31)[0] == 0
        assert buf.copy(32)[0] == 1 and buf.copy(61)[0] == 1
        bridge.pk_test_destroy(handle)
        assert active() == 0
        assert struct.unpack_from("<I", buf.mm, 56)[0] == 2
        buf.close()
        report["checks"] += ["30-frame wrap rejects overwritten sequences", "closed status"]

        for label, pts, fault in [("shape", 1, 1), ("HDR", 1, 2), ("PTS", -1, 0)]:
            path = tmp / (label + ".bin")
            handle = bridge.pk_test_create(os.fsencode(path), 16, 16, 0)
            assert handle and active() == 1
            buf = Buffer(path, reader)
            assert not bridge.pk_test_push(handle, pts, 16, 128, 128, 1, 0, fault)
            assert active() == 0  # Release on failure, before eventual destruction.
            assert not bridge.pk_test_push(handle, 2, 16, 128, 128, 1, 0, 0)
            bridge.pk_test_destroy(handle)
            assert active() == 0
            assert struct.unpack_from("<I", buf.mm, 56)[0] == 3
            buf.close()
            report["checks"].append(label + " error remains failed closed")

        colors = {}
        path = tmp / "colors.bin"
        handle = bridge.pk_test_create(os.fsencode(path), 16, 16, 0)
        buf = Buffer(path, reader)
        for seq, (label, y, u, v, matrix, full) in enumerate([
            ("limited_black", 16, 128, 128, 1, 0),
            ("limited_white", 235, 128, 128, 1, 0),
            ("full_black", 0, 128, 128, 1, 1),
            ("full_white", 255, 128, 128, 1, 1),
            ("bt709_color", 100, 90, 220, 1, 0),
            ("bt601_color", 100, 90, 220, 6, 0),
        ], 1):
            assert bridge.pk_test_push(handle, seq, y, u, v, matrix, full, 0)
            assert buf.copy(seq)[0] == 1
            colors[label] = list(buf.out[:3])
        assert max(colors["limited_black"]) <= 1 and min(colors["limited_white"]) >= 253
        assert max(colors["full_black"]) <= 1 and min(colors["full_white"]) >= 253
        assert colors["bt709_color"] != colors["bt601_color"]
        report["colors"] = colors
        report["checks"].append("YUV range endpoints and matrix selection")
        bridge.pk_test_destroy(handle)
        buf.close()
        assert active() == 0

        path = tmp / "race.bin"
        handle = bridge.pk_test_create(os.fsencode(path), 512, 512, 1)
        ctx = mp.get_context("spawn")
        ready, done, results = ctx.Event(), ctx.Event(), ctx.Queue()
        child = ctx.Process(target=race_reader, args=(path, ready, done, results))
        child.start()
        assert ready.wait(10)
        for seq in range(1, 2001):
            assert bridge.pk_test_push(handle, seq * 1000, seq % 251, 128, 128, 1, 1, 0)
        done.set()
        child.join(10)
        assert child.exitcode == 0
        race = results.get(timeout=1)
        assert "error" not in race and race["accepted"] > 0, race
        report["race"] = race
        report["checks"].append("cross-process concurrent writer/copy exact pixels and PTS")
        bridge.pk_test_destroy(handle)

        path = tmp / "benchmark.bin"
        handle = bridge.pk_test_create(os.fsencode(path), 968, 2376, 0)
        buf = Buffer(path, reader)
        push, copy = [], []
        for seq in range(1, 101):
            start = time.perf_counter_ns()
            assert bridge.pk_test_push(handle, seq * 1000, 90, 110, 130, 1, 0, 0)
            elapsed = time.perf_counter_ns() - start
            start = time.perf_counter_ns()
            assert buf.copy(seq)[0] == 1
            copied = time.perf_counter_ns() - start
            if seq > 30:
                push.append(elapsed / 1e6)
                copy.append(copied / 1e6)
        for label, values in [("fixture_fill_plus_rgb_publish_ms", push), ("native_rgb_copy_ms", copy)]:
            report["benchmarks"][label] = dict(n=len(values), mean=statistics.mean(values),
                median=statistics.median(values), max=max(values))
        report["benchmarks"]["note"] = "968x2376 synthetic YUV420P; publish includes fixture fill; excludes video decode and OCR"
        bridge.pk_test_destroy(handle)
        buf.close()
    output = BUILD / "native-test-report.json"
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
