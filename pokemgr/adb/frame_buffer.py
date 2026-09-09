"""Read immutable RGB frames from the paired scrcpy client's bounded ring.

The producer owns the file and publishes each slot with a sequence lock. This
consumer never treats decoder arrival time as camera/display capture time;
callers must map source PTS through a bounded device-clock measurement.
"""

from dataclasses import dataclass
import ctypes
import mmap
import os
from pathlib import Path
import stat
import struct

from PIL import Image


class FrameBufferError(RuntimeError):
    pass


@dataclass(frozen=True)
class StreamFrame:
    session: str
    sequence: int
    pts_us: int
    received_ns: int
    image: Image.Image


class FrameBuffer:
    HEADER_SIZE = 128
    SLOT_HEADER_SIZE = 64
    MAGIC = b"PKFRM001"

    def __init__(self, path, *, session: str, writer_pid: int,
                 width: int, height: int, reader_library=None):
        if (len(session) != 32 or any(c not in "0123456789abcdef" for c in session)
                or writer_pid <= 0 or width <= 0 or height <= 0):
            raise FrameBufferError("Invalid stream buffer identity")
        self.path = Path(path)
        self.session = session
        self.writer_pid = writer_pid
        self.width, self.height = width, height
        self._map = None
        self._native_handle = None
        if reader_library is None:
            reader_library = Path(__file__).resolve().parents[2] / "cache/scrcpy-frame-build/libpk_frame_reader.dylib"
        try:
            self._native = ctypes.CDLL(str(reader_library))
            self._native.pk_frame_open.argtypes = [ctypes.c_int, ctypes.c_size_t]
            self._native.pk_frame_open.restype = ctypes.c_void_p
            self._native.pk_frame_close.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            self._native.pk_frame_close.restype = None
            self._native.pk_frame_copy.argtypes = [
                ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_uint64,
                ctypes.c_void_p, ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_int64), ctypes.POINTER(ctypes.c_uint64),
            ]
            self._native.pk_frame_copy.restype = ctypes.c_int
        except (OSError, AttributeError) as exc:
            raise FrameBufferError("Native stream reader is unavailable; build the paired client") from exc
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.path, flags)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) & 0o077):
                raise FrameBufferError("Stream buffer must be a private regular file")
            self._file_identity = (info.st_dev, info.st_ino, info.st_size)
            raw = os.pread(fd, self.HEADER_SIZE, 0)
            self._validate_header(raw)
            required = self.HEADER_SIZE + self.capacity * self.slot_size
            if info.st_size != required:
                raise FrameBufferError("Stream buffer length does not match its geometry")
            self._map = mmap.mmap(fd, 0, access=mmap.ACCESS_READ)
            self._native_handle = self._native.pk_frame_open(fd, info.st_size)
            if not self._native_handle:
                self._map.close()
                self._map = None
                raise FrameBufferError("Cannot map stream frames for atomic copying")
        finally:
            os.close(fd)

    def _validate_header(self, raw):
        if len(raw) != self.HEADER_SIZE or raw[:8] != self.MAGIC:
            raise FrameBufferError("Stream frame producer is not ready")
        version, header_size, capacity, width, height, channels = struct.unpack_from("<6I", raw, 8)
        slot_size, _latest, pid = struct.unpack_from("<3Q", raw, 32)
        status, = struct.unpack_from("<I", raw, 56)
        if (version != 1 or header_size != self.HEADER_SIZE or not 2 <= capacity <= 30
                or (width, height, channels) != (self.width, self.height, 3)
                or slot_size != self.SLOT_HEADER_SIZE + width * height * channels):
            raise FrameBufferError("Stream frame format or dimensions changed")
        if raw[64:96] != self.session.encode("ascii") or pid != self.writer_pid:
            raise FrameBufferError("Stream frame producer belongs to another session")
        if status != 1:
            raise FrameBufferError("Stream frame producer stopped or is not ready")
        if hasattr(self, "capacity") and (capacity, slot_size) != (self.capacity, self.slot_size):
            raise FrameBufferError("Stream frame layout changed")
        self.capacity, self.slot_size = capacity, slot_size

    def _header(self):
        if self._map is None:
            raise FrameBufferError("Stream frame reader is closed")
        try:
            info = self.path.stat(follow_symlinks=False)
            if (info.st_dev, info.st_ino, info.st_size) != self._file_identity:
                raise FrameBufferError("Stream frame file was replaced")
            os.kill(self.writer_pid, 0)
        except (OSError, ValueError) as exc:
            raise FrameBufferError("Stream frame producer is unavailable") from exc
        raw = self._map[:self.HEADER_SIZE]
        self._validate_header(raw)
        return raw

    @property
    def latest_sequence(self):
        raw = self._header()
        return struct.unpack_from("<Q", raw, 40)[0]

    def sequences(self, *, after: int = 0):
        """Return available sequence numbers, oldest first, without copying pixels."""
        latest = self.latest_sequence
        return range(max(1, after + 1, latest - self.capacity + 1), latest + 1)

    def read(self, sequence: int) -> StreamFrame | None:
        """Copy one published slot; return None if it raced with an overwrite."""
        latest = self.latest_sequence
        if sequence <= 0 or sequence > latest or sequence <= latest - self.capacity:
            return None
        offset = self.HEADER_SIZE + ((sequence - 1) % self.capacity) * self.slot_size
        size = self.width * self.height * 3
        pixels = ctypes.create_string_buffer(size)
        pts_us, received_ns = ctypes.c_int64(), ctypes.c_uint64()
        copied = self._native.pk_frame_copy(
            self._native_handle, len(self._map), offset, sequence, pixels, size,
            ctypes.byref(pts_us), ctypes.byref(received_ns),
        )
        if copied == 0:
            return None
        if copied != 1:
            raise FrameBufferError("Stream frame metadata is invalid")
        # Validate the producer again after copying, before exposing the frame.
        self._header()
        image = Image.frombytes("RGB", (self.width, self.height), pixels.raw)
        image.info.update(pokemgr_stream_session=self.session,
                          pokemgr_stream_sequence=sequence,
                          pokemgr_stream_pts_us=pts_us.value,
                          pokemgr_stream_received_ns=received_ns.value)
        return StreamFrame(self.session, sequence, pts_us.value, received_ns.value, image)

    def close(self):
        if self._native_handle is not None:
            self._native.pk_frame_close(self._native_handle, self._file_identity[2])
            self._native_handle = None
        if self._map is not None:
            self._map.close()
            self._map = None

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        self.close()
