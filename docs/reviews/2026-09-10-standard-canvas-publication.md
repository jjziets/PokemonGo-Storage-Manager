# Fixed app canvas and accumulated scan/action fixes

The owner asked to reuse the same app layout on another Android phone or tablet,
then explicitly requested updating the app, committing and pushing. The target
is the existing `dev` branch at `git@github.com:jjziets/PokemonGo-Storage-Manager.git`,
starting at `67f113b208cf2b27117de20070a8a882949f937f`. No PR, merge or application
restart is part of this publication. The owner then stated a scan is running;
all verification is offline and must leave that scan and production data alone.

The advisory baseline remains REQ-BASELINE-2026-09-09-001, semantic SHA256
`4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`.
The intent contract preserves the prior publication target as history and records
the current owner instruction. No baseline statement or semantic hash changed.

## Resulting behavior

- The stream defaults to a portrait 968×2376 canvas at 420 DPI on every device.
  `--native-display` explicitly retains device dimensions/DPI. The encoder cannot
  silently downsize after failure. A frozen requested-geometry envelope makes
  capture/input reject an actual display with a different size or density.
- Calibration identity includes density. Exact same-device and matching legacy
  files retain their overrides. New devices can borrow valid, compatible saved
  coordinates in memory; their timing and verification remain separate.
  Preferred donors must agree and contain valid in-bounds coordinates. Lookup
  never writes profiles. Legacy evidence requires exact device/geometry fields.
- The accumulated requested fixes since the prior commit include independent
  transition/identity recovery, app-stream scheduling and pixel processing,
  scan queue handling, full search/count verification, `!favorite` keeper
  traversals, gym-defender skips and confirmed favorite database persistence.
  The linked September9–10 review records describe each bounded change and its
  evidence. No automatic transfer or resource spending is introduced.
- A publication review found that pause during multipass setup could preserve
  a short search's old tags/count. Setup now has one pause epoch covering the
  complete query, count, first appraisal and scanner construction. Invalidated
  setup restarts; a started scanner is never replayed by this retry.

## Review evidence

PUB-REVIEW-CORE-20260910: independent core/native audit found the accumulated
source and build inputs coherent, including the new Objective-C activity files.
Fixed-canvas review confirmed immutable requested geometry, no silent physical
display fallback and support for the no-downsize option in the pinned client.

PUB-REVIEW-ACTIONS-20260910: independent execution/data/GUI audit found the
multipass setup pause race described above. It was repaired with mocked
query/count/card/reader-construction tests. No other new actionable favorite or
data/GUI finding remained in this review.

PUB-REVIEW-GEOMETRY-20260910: independent calibration review required rejecting
malformed/out-of-screen donor coordinates before cross-device reuse. It also
verified that the updated publication intent matches the owner's instruction
and that the canonical requirements hash remains unchanged.
The coordinate repair passed independent re-review, including valid phone and
tablet defaults, canvas-edge points and legacy optional fields. No remaining
actionable findings were reported in the bounded final reviews.

## Verification

VER-PUB-20260910 uses an isolated source copy in a temporary directory. No
production databases, calibration files, logs or captures are copied. Native
build outputs are separate from the running stream's cache. Test processes use
a nonexistent ADB path and mocked device operations.

- Full Python suite after repairs: **1,336 passed, 10 skipped and 2,591 subtests
  passed** in 20.66 seconds. One existing Requests dependency-version warning
  remains. The sandbox rejected the test process's lower-priority request;
  tests still ran successfully in the isolated copy.
- Project-local scrcpy client: complete isolated build passed, limited to two
  build jobs. The pinned source, server, assets and native build inputs matched.
- Native C/Objective-C bridge: lifecycle, pixel conversion, frame ring and
  concurrency checks passed in the isolated build.
- Android clock and search helpers: both Java/D8 builds passed; no helper was
  deployed or executed on a phone.
- Swift/Vision: compilation and a synthetic CP1234 image passed. The initial
  restricted-sandbox run could not allocate a CVPixelBuffer; the same isolated
  synthetic check passed with approved host access and no phone interaction.
- The full suite initially exposed two outdated GUI cleanup fixtures missing
  the collection refresh callbacks. They now assert refresh happens only after
  worker cleanup. Production callback code was already present.
- Shell syntax and `git diff --check` passed. Candidate scope contains 118
  changed/new source, test and documentation files; no runtime data paths.

TRACE-PUB-20260910 maps these changes to REQ-STREAM-001, REQ-DATA-001,
REQ-MASS-001 and REQ-SCAN-003/004 in the matrix. Runtime databases, personal
calibrations, generated build artifacts and raw live screenshots remain ignored.
No device compatibility, full-storage speed, GPU telemetry or runtime reload
claim is inferred from this verification. The changes take effect next launch.
