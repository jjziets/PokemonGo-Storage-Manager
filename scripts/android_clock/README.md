# Android source-clock sampling

Build the small read-only helper with installed Java and Android build tools:

```sh
python3 scripts/android_clock/build.py
```

The defaults use JDK 17, Android build-tools 36.0.0, and platform android-36.
`--sdk`, `--java-home`, `--build-tools`, and `--platform` select another installed
toolchain. No download, Gradle project, APK install, or device operation occurs.
The output is `cache/android-clock/pokemgr-clock.jar`; its adjacent build manifest
records the source, build script, toolchain, and artifact hashes. The jar has
fixed ZIP metadata. Two builds with the current toolchain produced identical
SHA256 `4944498053eb783dc4d1fd27214a46d2f82cb59f42878433e92b4e390d306aed`.

The launcher deploys the validated jar to
`/data/local/tmp/pokemgr-clock.jar` on the explicitly selected device, mode 0444.
Deployment is separate from the sampling API. Coordinate any manual deployment
with the process that owns the device. The helper reads clocks and boot identity;
it does not read or change the game, display, input, or settings.

Use the persistent sampler, excluding JVM startup from each measurement:

```python
from pokemgr.adb.clock_sync import AndroidClockSync, ClockSampler

clock = AndroidClockSync()
with ClockSampler(adb) as sampler:
    clock.observe(sampler.sample())
    bounds = clock.map_pts_us(source_pts_us, received_ns=decoder_received_ns)
    usable = (
        bounds.sync_generation == clock.generation
        and bounds.strictly_after(gesture_completed_ns)
        and bounds.fresh_at(now_ns, max_age_ns=250_000_000)
    )
```

`ClockSampler` owns one selected-device `adb shell -T` process invoking
`CLASSPATH=/data/local/tmp/pokemgr-clock.jar app_process / pokemgr.tools.ClockSample --loop`.
The READY handshake has a separate five-second startup deadline. Subsequent
requests carry random nonces and have one-second nonblocking I/O deadlines.
Closing stdin ends the helper; bounded termination handles an unresponsive owned
process. The one-shot `sample_clock()` is available for diagnostics, but JVM
startup made its measured uncertainty too large for scanning on the tested phone.

Each reply brackets `SystemClock.elapsedRealtimeNanos()` with two
`System.nanoTime()` calls. Host send/receive bounds enclose the entire exchange.
Mapping retains that full uncertainty, the device bracket, microsecond PTS
truncation, and a relative drift allowance; it never substitutes an RTT midpoint
or decoder arrival time for source time. Decoder receipt only supplies a causal
upper bound. A frame is after an input only when its **earliest** possible source
time is strictly later than input completion. Frame age is measured from that
same earliest bound.

Refresh every ten seconds. The default calibration expires after thirty seconds
and assumes relative clock drift no greater than 1000 ppm. This is an explicit
operating bound, not a universal hardware guarantee. Every successful observation
changes the generation; discard or remap evidence from older generations.
Invalidate on pause/resume, host suspend, reconnect, device change, or a new
stream session. Boot identity, clock regressions, incompatible offset intervals,
and changes in the BOOTTIME-minus-MONOTONIC interval fail closed. BOOTTIME is used
only to detect suspend between samples; it is never the PTS mapping clock.

The device smoke evidence is in `cache/scan-action-timing/clock-smoke.json`.
Three persistent exchanges took 3.34, 3.92, and 2.24 ms; all compatible interval
intersections passed. This verifies clock sampling, not end-to-end video capture
freshness or scan throughput.

## Source-clock requirements

The supported source is the original Android surface timestamp with no encoder
timestamp offsets, time-lapse, synthetic repeats, or client timestamp rewriting.
The paired scrcpy export client must force
`--video-codec-options=repeat-previous-frame-after:long=0` and reject other custom
codec timing options. Stock scrcpy 4.1 otherwise requests a repeated frame after
100000 microseconds, which can attach a later PTS to old pixels.

- Android's [`System.nanoTime()` implementation](https://android.googlesource.com/platform/libcore/+/refs/tags/android-16.0.0_r1/ojluni/src/main/native/System.c)
  uses `CLOCK_MONOTONIC`. [`SystemClock`](https://developer.android.com/reference/android/os/SystemClock)
  distinguishes this uptime basis from elapsed real time, which includes sleep.
- Surface queue automatic timestamps use
  [`SYSTEM_TIME_MONOTONIC`](https://android.googlesource.com/platform/frameworks/native/+/refs/heads/main/libs/gui/Surface.cpp).
  [`GraphicBufferSource`](https://android.googlesource.com/platform/frameworks/av/+/refs/tags/android-16.0.0_r1/media/module/bqhelper/GraphicBufferSource.cpp)
  converts nanoseconds to codec microseconds and contains the synthetic-repeat
  and timestamp adjustment behavior this integration disables.
- scrcpy 4.1 [`Streamer`](https://github.com/Genymobile/scrcpy/blob/v4.1/server/src/main/java/com/genymobile/scrcpy/device/Streamer.java)
  forwards `BufferInfo.presentationTimeUs`, and its
  [`demuxer`](https://github.com/Genymobile/scrcpy/blob/v4.1/app/src/demuxer.c)
  preserves those PTS after removing protocol flags. The paired sink preserves
  decoded `AVFrame.pts` and rejects missing/negative PTS and session resets.
- scrcpy's [`SurfaceEncoder`](https://github.com/Genymobile/scrcpy/blob/v4.1/server/src/main/java/com/genymobile/scrcpy/video/SurfaceEncoder.java)
  applies explicit codec options after its default repeat setting. Android 16
  [`ACodec`](https://android.googlesource.com/platform/frameworks/av/+/refs/tags/android-16.0.0_r1/media/libstagefright/ACodec.cpp)
  and [`CCodec`](https://android.googlesource.com/platform/frameworks/av/+/refs/tags/android-16.0.0_r1/media/codec2/sfplugin/CCodec.cpp)
  configure repeats only for positive values; zero leaves repeats disabled.
- On macOS the paired native sink uses `mach_absolute_time()` with timebase
  conversion, matching Python `time.monotonic_ns()`. A different host clock epoch
  or sleep behavior would invalidate the receipt bound.
