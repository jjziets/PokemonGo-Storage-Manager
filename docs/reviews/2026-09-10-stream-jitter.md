# Stream jitter and redundant frame copies

<!-- TRACEWEAVER: file-role=stream-jitter-review; req=REQ-STREAM-001; trace=TRACE-STREAM-001; ver=VER-SCAN-001 -->

Authority: the owner's report that the live stream is slow or stutters, under
the existing REQ-STREAM-001 baseline. Work is limited to preview delivery and
avoiding redundant consumer work; source-time correctness is unchanged.

## Observation

A 15-second header-only sample during storage navigation/search observed 128
sequence advances, about 8.5 frames/second. Source PTS intervals were 50.2ms
median, 401.7ms p95 and 466.2ms maximum. Sink intervals showed similar gaps;
relative source-to-sink backlog shrank about 102ms. This mixed-scene sample
cannot distinguish static-screen variable frame rate from animation stutter.

A subsequent 15-second sample on Kyurem's animated appraisal observed
15.39 frames/second, with source intervals 33.48ms median, 233.85ms p95 and
266.91ms maximum. Sink intervals closely tracked those values and relative
backlog changed only +2.26ms. The client used 39.8% of one CPU core and
310MiB RSS. This confirms uneven source output on an animated page while
the Mac kept pace; it is not an absolute end-to-end latency measurement.

Follow-up: these source timestamps describe the captured stream, not the
physical game's presentation cadence. A later physical-only trace measured
smooth 59.93 FPS gameplay. The [controlled display, exporter and preview
comparisons](2026-09-10-stream-background-scheduling.md) also reproduced a
separate delivery backlog and tested a process-activity mitigation.

The paired client used Apple vImage conversion, about 30.7% CPU and 312MiB
resident memory. A native process sample found the decoder input mostly
waiting for data, with the display thread mostly waiting for events. This
sample does not show saturated Mac decoding or an accumulating RGB backlog.

Android reported the virtual display at 60Hz, battery saver off, battery at
37.5°C while charging, and thermal status 2 with skin sensors at 42.5–42.7°C.
Android defines status 2 as
[moderate throttling](https://developer.android.com/reference/android/os/PowerManager#THERMAL_STATUS_MODERATE).
Thermal pressure may contribute to source gaps; this does not prove causation.

## Bounded changes

The launcher passes `--video-buffer=50`, a 50ms preview buffer supported by
the installed scrcpy 4.1 client. Its screen sink receives frames through a
video regulator; the RGB exporter remains attached directly to the decoder.
Scanner frames therefore do not acquire this preview delay. The buffer can
smooth small delivery jitter, but cannot fill a real 400ms source gap.

`StreamCapture.capture()` now considers each published sequence at most once
per request. Previously, it could copy the same rejected 6.9MB frame every
5ms while waiting. Rejected sequences do not change accepted sequence/PTS
history. Input/pause epochs, producer identity, session, clock bounds and
timeouts still apply; a subsequent request can reconsider the frame under its
own observation boundary.

## Verification

73 adjacent capture/controller/clock/frame-buffer tests passed. New regressions
cover stale-to-fresh delivery, repeated stale copies, timeout, request-local
state, raced slots, pause/input invalidation and changed producer sessions.
Independent review found no freshness regression and verified the pinned
client's preview/exporter topology. Full focused verification and current
runtime observations are recorded with the keeper speed evidence.

The private header sampler in `cache/keeper-speed-proof/sample_stream.py`
discovers the current paired GUI and producer rather than reusing transient
display or process IDs. It reads metadata only and makes no phone inputs.
