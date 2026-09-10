# Stream delivery and background scheduling

## Authority and scope

The owner reported smooth gameplay on the phone but stuttering in the app
stream, and explicitly authorized ADB diagnosis and stream improvements.
Local authority decision: **Proceed** for the native stream process lifecycle
under REQ-STREAM-001 / TRACE-STREAM-001, baseline
REQ-BASELINE-2026-09-09-001, semantic SHA256
`4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`.
The requirements and intent contract remain unchanged. TraceWeaver is advisory
in this checkout; this is a local evidence review, not a packaged runtime gate
or publication approval.

All phone measurements were performed while the manager and scanner were
closed. The tests moved the existing game to temporary app displays, changed
only physical display power, and restored the physical screen afterwards.
No scan, favorite, database write, game setting change, or other-app shutdown
was part of the diagnosis. Recordings and traces remain in ignored private
`cache/stutter-diagnosis/`; transient display IDs and process identities must
not be reused.

## What was measured

The physical-phone Perfetto baseline corroborates the owner's observation:
859 consecutive game SurfaceView buffers were latched at **59.93 FPS**.
The longest latch interval was 19.99 ms, with no repeated or skipped buffer
numbers. FrameTimeline did not expose the game's SurfaceView, so its generic
late-present labels were not treated as a game jank percentage. See the
[Perfetto SurfaceView limitation](https://perfetto.dev/docs/data-sources/frametimeline).

Controlled captures used the same animated map, full 968x2376 geometry,
H.264 at 8 Mbps, a 30 FPS cap, and synthetic encoder repeats disabled. Recorded
PTS describes source time, not when bytes arrive at the Mac. A private sampler
also checked the owned producer, nonce, inode, ring layout and slot guards,
then compared source timestamps with host sink-entry timestamps.

| Condition | Measurement | Result |
| --- | --- | --- |
| Physical-screen recording, no decode/preview/export | Final steady interval | 30 FPS; longest source interval 51 ms |
| App-display recording, no decode/preview/export | Last 15 seconds | 30 FPS; longest source interval 56 ms |
| RGB exporter, no preview | Same stream, panel ON/OFF/ON | 29.98–30.00 FPS; source p95 about 34 ms; no accumulating backlog |
| Metal preview and RGB exporter | Three separate repetitions | Delivery stalls reproduced; one phase added about 5.05 seconds of lag despite source intervals no longer than 36.1 ms |
| OpenGL preview and RGB exporter | First trial then repeat | First trial steady; repeat still had a 563 ms delivery interval; renderer switch rejected as a sufficient fix |
| Metal preview with scoped user-initiated activity | Three 15-second phases | 29.98–29.99 FPS; longest sink interval 67 ms; no accumulating backlog |
| Same activity, longer repeat | Three 30-second phases | 29.80/30.00/30.00 FPS; phase-end backlog changes all below 2 ms in magnitude |

The longer activity trial still had one **487 ms** delivery interval in its
first phase, which caught up. The two later phases had maximum intervals below
75 ms. These results support reducing sustained background delay, not a claim
that all stutter has disappeared. They do not measure scan throughput or
reproduce every Pokémon appraisal scene.

Physical panel OFF and virtual display ON were verified from committed
display-device state at each phase's entry, rather than stale display-override
metadata. These snapshots do not exclude a later movement-triggered wake.
One initial screen-off recording was invalid because the phone auto-locked;
it was excluded and repeated after the owner unlocked it, using the normal
keep-awake settings.

## Interpretation and bounded fix

The stream's 30 FPS cap also explains why even steady preview motion looks less
fluid than the observed 60 FPS physical game. The cap is retained in this fix.

The earlier Kyurem result in `2026-09-10-stream-jitter.md` measured uneven
**captured source timestamps**, not physical-phone render cadence. It must not
be cited as proof that the game itself was stuttering.

No sustained competing Android CPU hog or memory reclamation was found. The
phone reported thermal limits even while the physical render trace stayed
smooth; those limits were not established as the cause. A follow-up Mac sample
showed background activity and substantial memory compression, but spare CPU
capacity and almost no swapping. It does not implicate a particular other app.

A native stack sample mixed healthy and stalled intervals: the demux thread
spent substantial sampled wall time in receive, decode and RGB conversion,
without a sampled preview/exporter mutex wait. This cannot identify an exact
OS or driver fault. The process-activity A/B supports **background scheduling
or deferral** as a contributor; App Nap state itself was not directly observed.

The native exporter therefore declares a user-initiated activity for its
lifetime, ending it during cleanup. It uses
`NSActivityUserInitiatedAllowingIdleSystemSleep`, which Apple recommends for
lengthy user-requested work to prevent deferral/App Nap while allowing idle
system sleep. See [Apple's activity guidance](https://developer.apple.com/library/archive/documentation/Performance/Conceptual/power_efficiency_guidelines_osx/PrioritizeWorkAtTheAppLevel.html).
This is process-local: no global defaults, privilege elevation, display-sleep
assertion, or latency-critical priority is part of the product change.
Source PTS, freshness rejection, ring capacity, geometry and phone controls
retain their existing contracts.

## Integrated verification

The project-local client was rebuilt with the Foundation activity bridge,
without the diagnostic injection. The cached upstream patch upgraded from its
exact previous manifest match; the launcher then reused the new source-matched
build without rebuilding. Client SHA256:
`1a3ed4038fc7109cd712ddf90a5d3c41905dc7075df22591a141cde1a2f6e4e4`.

The final live run used Metal, native RGB export, the usual 50 ms preview buffer,
no recording, and no scanner. Three 30-second ON/OFF/ON samples measured
**29.97 / 29.99 / 29.99 FPS**, with maximum sink intervals
**71.5 / 85.6 / 67.6 ms**. Using one time reference across all three phases,
maximum additional lag was 66.1 ms and the ending difference was 10.6 ms.
There was no accumulating multi-second backlog in this bounded run. The stream
closed and returned the game to the physical display with its panel restored.

Verification completed:

- Full pinned native client and copying-library build.
- Thirteen native protocol/lifecycle check groups: token lifetime, invalid init,
  repeated cleanup, duplicate open, shape/HDR/PTS failures, wire metadata,
  source clock, color, wraparound and concurrent copying. Synthetic publishing
  and copying timings are not scan-throughput measurements.
- Ten disposable-repository migration tests, including preservation of edited
  and staged files, rejected replacement patches, restoration and retry.
- Fifty-seven adjacent Python launcher/capture/cleanup tests with 32 subtests;
  the launcher and migration subset was rerun after integration (16 tests).
  The existing Requests dependency warning remains unrelated to this change.
- Independent activity-lifecycle, build-migration and measurement reviews;
  no outstanding actionable findings. Final diff check and the scoped local
  code-anchor scan passed, with zero anchor findings.

VER-STREAM-ACTIVITY-001 links these checks to REQ-STREAM-001. This records a
verified local mitigation, not full requirement closure, a release decision,
zero-stutter assurance, or unattended scan validation. The installed packaged
TraceWeaver runtime was not available; no packaged gate pass is claimed.
Raw evidence and the final input manifest are in private
`cache/stutter-diagnosis/integrated-verification.json` and adjacent logs.
