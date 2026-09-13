# Tablet navigation, capture recovery and collection cleanup

## Authority and limits

The owner explicitly requested fixes for the Normal pass return failure and
Dynamax checkpoint failures, followed by continuation. They then requested
notice before deployment because they resumed scanning, and separately
authorized removing their older phone entries while keeping today's tablet
collection. Source changes remain offline until that deployment boundary.

Local authority decision: Proceed under REQ-SCAN-003/004, REQ-STREAM-001 and
REQ-DATA-001, baseline REQ-BASELINE-2026-09-09-001, semantic SHA256
`4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`.
TraceWeaver is advisory; this record does not claim a packaged gate or
publication approval. Existing identity, source-clock and no-spending checks
remain required. Previous uncommitted keeper-progress work is preserved.

## Evidence

- Normal completed 635 positions, stored 634, and skipped one gym defender.
  At 14:54:08 the final screen was appraisal; at 14:54:12 it was unrecognized.
  The old navigation path sent appraisal X and Back without observing the
  intermediate screen, then immediately stopped on an unknown app-stream
  frame. No screenshot of that failure was retained. A missed X, transition
  timing and recognition failure cannot be distinguished retrospectively.
- Dynamax stored 32 entries through Wailmer CP811. Confirmation later used
  fresh direct captures instead of stream frames; their missing stream
  metadata correctly failed the same-stat continuity check. The recovery
  path used a calibrated reverse swipe and then lost appraisal detail.
- Manual resumes encountered the same capture-source boundary at Wailmer
  CP806 and an unsettled confirmation after Kabuto CP763. Stream timeouts
  permit a fresh display-bound JPEG capture, but not invented source metadata.
  The same producer session subsequently delivered frames again. There is
  no evidence identifying another app, GPU load or producer death as the cause.
- A passive 30-second read of the existing stream's metadata measured about
  26.28 FPS, maximum source interval 565 ms, maximum sink interval 595 ms,
  and under 1 ms change in relative lag. This sample crossed normal UI work;
  it does not establish why a specific earlier timeout occurred. It sent no
  device input and did not capture additional phone images.

Primary private evidence is in `logs/scan_20260911_143134.log`,
`logs/scan_20260911_150141.log`, `logs/macos_launcher.log` and
`cache/tablet-recovery/`.

## Authorized data recovery

The idle GUI and completed scan sessions were verified before the transaction.
A consistent SQLite backup was written to
`data/backups/before-tablet-recovery-20260911-151647.db`.

The transaction removed 3,455 rows in 28 older phone sessions and preserved
all 786 rows in eight tablet sessions dated September 11. Selection required
both the observed device fingerprint and date boundary. Every retained row,
including its IDs, stats and decisions, was compared before commit. Foreign
keys and SQLite quick-check passed. The retained-row SHA256 is
`82aada1374b2c4252b103e85fb1fab6b5b8d6db777e7573e7c616e35896cda00`.
The refreshed Collection visibly showed 786 of 786 entries.

Manual continuation collected the remaining Dynamax entries: 32 + 1 + 3 + 35
equals the originally verified count of 71. Gigantamax subsequently completed
all eight results at 15:12:07. These counts do not prove cross-pass uniqueness.
The three Scan from Here sessions did not inherit a Dynamax flag in stored
rows; their category requires separate verification before any tag repair.
No inferred tag repair or replay of completed scans is part of this cleanup.

## Verification status

Navigation now observes each departure before sending the next input, bounds
unknown-screen rereads and saves the last failure frame. Checkpoint recovery
uses observed appraisal arrows with the calibrated swipe fallback. Unsettled
transition confirmation discards the old pair and permits at most three fresh
pairs, retaining complete identity, source chronology and narrow IV-bar checks.
Stream timeout diagnostics report observed rejection reasons once per fallback
burst without weakening capture freshness or inventing stream provenance.

The old Clear Database handler unlinked only the main SQLite file and did not
verify zero records; a surviving WAL or wrong configured path is a plausible
mechanism, not a proven history of this particular clear attempt. The replacement
clears and verifies both tables in one transaction on the existing connection.
Real SQLite tests cover WAL readers, rollback, pending writes, wrong-default-path
protection and active-worker guards. Post-commit display-refresh errors are
reported separately from database failure.

The final isolated offline suite passed 1,402 tests and 2,663 subtests, with 17
skips for unavailable native builds/private captures and one existing Requests
dependency warning. Independent navigation/checkpoint and database-clear reviews
reported no remaining findings. `git diff --check` passed. The final test output
is `pytest-final.log` in the source copy referenced by
`/tmp/pokemgr-tablet-fixes-path.txt`.

The data recovery is applied and verified in the running Collection view.
After the owner explicitly authorized restart, the idle manager was closed
through its window and its paired stream exited. The standard launcher reopened
the updated source on the tablet at 968×2376/420, with physical display off and
no automatic scan. The new GUI was observed Ready. The owner then connected,
reran decisions and began live keeper favoriting; its new progress display
showed Pass 1/5, four upcoming categories and 786 total records. The attempted
Connect click was rejected by the UI tool because the owner changed the window;
no further UI input was sent once the active favorite run was observed.

The owner had also rerun decisions before restart (500 KEEP), and the active
run later showed 503 KEEP; these are owner actions, not recovery mutations.
`cache/tablet-recovery/pre-restart.json` preserves the 786 rows/eight sessions
immediately before relaunch. No completed scan was replayed, source changes
remain uncommitted and no completed live reliability claim is made.
