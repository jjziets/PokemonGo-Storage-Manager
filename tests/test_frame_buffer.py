"""Wire-level stream consumer checks using the native atomic copy helper."""

import os
from pathlib import Path
import struct
import tempfile
import unittest

from pokemgr.adb.frame_buffer import FrameBuffer, FrameBufferError


LIBRARY = Path(__file__).resolve().parents[1] / "cache/scrcpy-frame-build/libpk_frame_reader.dylib"
NONCE = "a" * 32


@unittest.skipUnless(LIBRARY.exists(), "Build the project-local stream client first")
class FrameBufferTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "frames.bin"
        self.slot_size = 64 + 2 * 4 * 3
        self.raw = bytearray(128 + 2 * self.slot_size)
        self.raw[:8] = b"PKFRM001"
        struct.pack_into("<6I", self.raw, 8, 1, 128, 2, 2, 4, 3)
        struct.pack_into("<3Q", self.raw, 32, self.slot_size, 2, os.getpid())
        struct.pack_into("<I", self.raw, 56, 1)
        self.raw[64:96] = NONCE.encode()
        for seq in (1, 2):
            offset = 128 + (seq - 1) * self.slot_size
            struct.pack_into("<QQqQQ", self.raw, offset,
                             seq * 2, seq, 1000 + seq, 2_000_000 + seq, 24)
            self.raw[offset + 64:offset + 88] = bytes([seq, 10, 20]) * 8
        self.path.write_bytes(self.raw)
        self.path.chmod(0o600)

    def reader(self, **changes):
        args = dict(session=NONCE, writer_pid=os.getpid(), width=2, height=4,
                    reader_library=LIBRARY)
        args.update(changes)
        return FrameBuffer(self.path, **args)

    def overwrite(self, offset, data):
        with self.path.open("r+b", buffering=0) as stream:
            stream.seek(offset)
            stream.write(data)

    def test_reads_exact_rgb_and_source_metadata_with_no_capture_time_invention(self):
        with self.reader() as reader:
            self.assertEqual([1, 2], list(reader.sequences()))
            frame = reader.read(2)
            self.assertEqual((2, 1002, 2_000_002),
                             (frame.sequence, frame.pts_us, frame.received_ns))
            self.assertEqual((2, 10, 20), frame.image.getpixel((0, 0)))
            self.assertNotIn("pokemgr_capture_started_at", frame.image.info)

    def test_returned_image_is_independent_of_later_ring_writes(self):
        with self.reader() as reader:
            image = reader.read(1).image
            self.overwrite(128 + 64, b"\xff" * 24)
            self.assertEqual((1, 10, 20), image.getpixel((0, 0)))

    def test_in_flight_and_overwritten_slots_do_not_become_observations(self):
        with self.reader() as reader:
            self.overwrite(128, struct.pack("<Q", 3))
            self.assertIsNone(reader.read(1))
            self.overwrite(40, struct.pack("<Q", 4))
            self.assertIsNone(reader.read(1))
            self.assertIsNone(reader.read(2))

    def test_source_identity_geometry_and_private_permissions_are_required(self):
        for args in ({"session": "b" * 32}, {"writer_pid": os.getpid() + 1},
                     {"width": 3}):
            with self.subTest(args=args), self.assertRaises(FrameBufferError):
                self.reader(**args)
        self.path.chmod(0o644)
        with self.assertRaises(FrameBufferError):
            self.reader()

    def test_closed_producer_recreated_file_and_invalid_metadata_fail_closed(self):
        with self.reader() as reader:
            self.overwrite(128 + 32, struct.pack("<Q", 23))
            with self.assertRaises(FrameBufferError):
                reader.read(1)
            self.overwrite(56, struct.pack("<I", 2))
            with self.assertRaises(FrameBufferError):
                reader.read(2)
        self.path.write_bytes(self.raw)
        with self.reader() as reader:
            self.path.unlink()
            self.path.write_bytes(self.raw)
            with self.assertRaises(FrameBufferError):
                reader.read(2)

    def test_short_file_and_capacity_change_are_rejected(self):
        with self.reader() as reader:
            self.overwrite(16, struct.pack("<I", 3))
            with self.assertRaises(FrameBufferError):
                reader.read(1)
        self.path.write_bytes(self.raw[:-1])
        with self.assertRaises(FrameBufferError):
            self.reader()

    def test_closed_reader_cannot_return_old_pixels(self):
        reader = self.reader()
        reader.close()
        reader.close()
        with self.assertRaises(FrameBufferError):
            reader.read(1)
