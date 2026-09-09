# scrcpy frame exporter

Build a local scrcpy 4.1 client that publishes the same decoded app stream shown
in the preview. This adds no phone connection or display. The system scrcpy
installation and official Android server are unchanged.

```sh
.venv/bin/python scripts/scrcpy_frame_sink/build.py
.venv/bin/python scripts/scrcpy_frame_sink/test_native.py
```

The helper pins official source commit
`2926c06c5dc3064ae6d8db706f1a98a37cfcf3f0`, checks the official 4.1 server
SHA256, and installs pinned build tools in a project cache venv. It uses existing
Homebrew SDL3, FFmpeg, and libusb libraries. It never launches the client or
contacts a phone. The result manifest is
`cache/scrcpy-frame-build/pokemgr-build.json`; it records input hashes, the client
path, native reader library path, and pinned portable icon hashes. The build depends on the current
Homebrew libraries, so it is source reproducible rather than byte reproducible.

To enable export, the launcher supplies both environment variables:

- `POKEMGR_FRAME_BUFFER`: absolute path to a file that does **not** exist.
  The launcher prepares a private parent directory; the writer uses
  `O_CREAT | O_EXCL`, mode `0600`, and retains the file after exit.
- `POKEMGR_FRAME_SESSION`: exactly 32 hexadecimal characters identifying this
  launch. A new launch needs a new path and nonce.

With neither variable, normal client behavior is preserved. Partial settings
fail. Export mode enables video decoding even if playback is disabled. The
macOS build disables V4L2, leaving room for the preview and exporter sinks.
Shape/session changes, unsupported HDR, missing PTS, or conversion failures mark
the buffer erroneous and terminate delivery. There is no cached-image fallback.

The buffer holds the latest 30 RGB24 frames. At 968 × 2376 this uses about
197 MiB. RGB conversion runs in the existing decoder callback. On macOS, SDR
YUV420P with BT601/BT709 uses Apple's Accelerate/vImage conversion and a reusable
ARGB scratch buffer (about 9 MiB at this resolution). Other supported pixel
formats/matrices use libswscale. Both paths respect source range and color
matrix. vImage rounds differently from libswscale; the saved-frame comparison
bounded channel differences to 3/255 and retained HP/name/IV results. PQ/HLG require tone mapping and
are rejected. Wire v1 requires `width * height * 3` divisible by eight so every
slot guard stays naturally aligned; the current calibrated display satisfies
this. Dimensions and allocation are bounded.

## Wire v1

All integers are little-endian. Header size is 128 bytes:

| Offset | Type | Value |
| --- | --- | --- |
| 0 | 8 bytes | `PKFRM001` |
| 8 | u32 | version 1 |
| 12 | u32 | header size 128 |
| 16 | u32 | capacity 30 |
| 20, 24 | u32 | width, height |
| 28 | u32 | channels 3 |
| 32 | u64 | slot size: `64 + width * height * 3` |
| 40 | atomic u64 | latest published sequence |
| 48 | u64 | writer PID |
| 56 | atomic u32 | 0 initial, 1 ready, 2 closed, 3 error |
| 64 | 32 bytes | ASCII session nonce |

Each slot has 64 bytes of metadata followed by packed RGB pixels:

| Offset | Type | Value |
| --- | --- | --- |
| 0 | atomic u64 | `seq * 2 + 1` while writing, `seq * 2` when ready |
| 8 | u64 | sequence |
| 16 | i64 | unmodified Android source PTS in microseconds |
| 24 | u64 | host monotonic nanoseconds at sink entry |
| 32 | u64 | RGB payload byte count |

Sequence starts at one; slot index is `(seq - 1) % 30`. The writer invalidates
the guard, fences before overwriting metadata/pixels, then publishes the even
guard and latest sequence with release ordering. Shared atomics must be
lock-free. Readers use the native helper to copy exactly one requested
sequence; arrival time alone does not prove post-gesture freshness.

```c
void *pk_frame_open(int fd, size_t size);  /* NULL on failure */
void pk_frame_close(void *base, size_t size);
int pk_frame_copy(void *base, size_t map_size, size_t offset,
                  uint64_t seq, uint8_t *destination, size_t payload_size,
                  int64_t *out_pts, uint64_t *out_received);
```

Copy returns 1 for a stable exact sequence, 0 for an in-flight/overwritten slot,
or -1 for invalid arguments/metadata. Output pixels must be discarded unless
the result is 1. The caller owns a separate destination and validates the file,
session nonce, writer identity/status, display identity, dimensions and clock
mapping. It must not truncate a mapped file or close the mapping during a copy.
The reader library has no scrcpy/FFmpeg dependency.

## Timestamp contract

The Android encoder PTS is not rebased. The official 4.1 server passes
`BufferInfo.presentationTimeUs` to the stream; the demuxer passes that PTS to
FFmpeg. On macOS, the sink entry timestamp uses `mach_absolute_time()` and the
Mach timebase, matching CPython `time.monotonic_ns()`. A native offline check
brackets each sink timestamp between Python monotonic reads.

Synthetic encoder repeats can advance timestamps without new surface content.
The client therefore forces `repeat-previous-frame-after:long=0` for export and
rejects every other custom video codec option, including options that could
retime frames. Passing that exact repeat setting explicitly is also allowed.
The official server applies codec options after its default 100 ms repeat
setting. Android 16 ACodec only calls the repeat setter for values greater than
zero; CCodec initializes minimum FPS to zero and only derives repeating FPS
from a positive repeat interval. Zero therefore leaves repeating disabled on
both backends. This applies on initial encoder configuration; the exporter
does not support reconfiguring an already running encoder.

Primary source references:

- [scrcpy 4.1 SurfaceEncoder](https://github.com/Genymobile/scrcpy/blob/v4.1/server/src/main/java/com/genymobile/scrcpy/video/SurfaceEncoder.java)
- [scrcpy 4.1 Streamer](https://github.com/Genymobile/scrcpy/blob/v4.1/server/src/main/java/com/genymobile/scrcpy/device/Streamer.java)
- [Android 16 ACodec](https://android.googlesource.com/platform/frameworks/av/+/refs/tags/android-16.0.0_r1/media/libstagefright/ACodec.cpp) (repeat configuration guarded by `mRepeatFrameDelayUs > 0`)
- [Android 16 CCodec](https://android.googlesource.com/platform/frameworks/av/+/refs/tags/android-16.0.0_r1/media/codec2/sfplugin/CCodec.cpp) (repeat interval translated only when `value > 0`)

`test_native.py` compiles a fixture-only bridge and checks private creation,
wire fields, PTS preservation, host clock compatibility, exact RGB bytes,
ring wrap, error persistence, YUV range/matrix handling, and concurrent reader
copies in a separate process. Its report includes warmed full-resolution
publish/copy timing. The fixture publication includes filling source planes;
it excludes video decode and OCR, and is not an end-to-end scan benchmark.

The first live client profile spent about 36% of its demuxer samples in
libswscale, motivating the vImage path. An offline comparison using the same
saved Gardevoir image converted to explicit limited-range BT601 YUV420P measured
1.34 ms for libswscale versus 0.53 ms for vImage (100 warmed conversions). Both
converted frames retained HP 153, the Gardevoir display/caught names, and
14/15/14 appraisal IVs. Full-frame native CP was missing in both, as in the
source's primary parse. Live freshness remains independently enforced by the
controller; faster conversion does not make old frames acceptable.
