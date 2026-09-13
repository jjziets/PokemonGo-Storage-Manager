# Appraisal settle timeout after Fearow

## Authority and scope

The owner reported another interrupted Normal scan after saved Pokémon #510,
Fearow CP1247, IV13/13/11. This continues the explicitly requested scan-recovery
fixes. Local authority decision: Proceed under REQ-SCAN-001/002/003 and
REQ-DATA-001, baseline REQ-BASELINE-2026-09-09-001, semantic SHA256
`4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`.
TraceWeaver is advisory; this record is not a packaged release gate. No restart,
device input, account change, production database write or publication is part
of this fix.

## Observed failure

The existing log `logs/scan_20260912_205127.log` records Fearow as saved at
21:15:17 with HP110 and calculated CP independently confirmed by both settled
frames. The next appraisal arrow was sent at 21:15:18. The subsequent six
comparisons witnessed a transition, but stabilization never succeeded.
The final three broad-region differences were respectively
`0.03/0.25/0.05`, `0.00/0.12/0.04` and `0.03/0.18/0.06`, below the 1.5 limit.
Those pairs still failed the separate IV-bar stability gate. The scan then
stopped with 510 stored positions and one earlier skipped position.

The exact rejected frames were not saved. Consequently the log does not prove
whether the next Pokémon's bars were animating or another visual effect caused
the rejection. No stream-fallback warning accompanied this stop; another app,
GPU load or capture interference is not established by the evidence.

## Defect and intended correction

`_wait_for_stable_appraisal` returns the typed `appraisal_not_stable` result
after its bounded six comparisons. `_acquire_validated_snapshot` returns
immediately when that result has no frame, bypassing its existing three-attempt
acquisition loop. This also bypasses failure-image persistence.

The correction retries this specific read-only exhaustion within the existing
acquisition budget. Each attempt must obtain fresh evidence and preserve the
pending transition and identity requirements. It must not authorize a skip,
favorite action or navigation using the rejected pixels. Other failure types
remain explicit, and stop/pause and source-generation guards remain required.
Terminal rejected frames and the individual settle gates are retained as
diagnostic evidence without making them action-authorizing frames. Broadly
moving frames still skip the more expensive narrow-bar predicate. Diagnostic
state is discarded with invalidated stream windows.

Independent review identified a separate pre-existing confirmation edge case:
after CP recovery had already been attempted, a later calculated read could
bypass a failed independent-pair confirmation. The retry keeps the recovery
input budget unchanged and now holds that unconfirmed result. Confirmed pairs
and visible CP validation retain their existing paths. This edge case is not
claimed as the cause of the Fearow stop.

## Verification

A minimal replay against the isolated pre-fix source reproduced immediate
exit: a mocked `appraisal_not_stable` result caused exactly one settling call.
The same replay against the fixed source made exactly three calls and retained
the terminal typed hold. Neither replay constructed a reader, ADB controller
or database.

- Complete isolated suite: **1,485 tests and 11,107 subtests passed**, with
  17 skips for private captures or the locally built native stream client and
  one existing Requests dependency-version warning. The copy used offscreen
  Qt, an unavailable ADB path and no production environment overrides.
- Fifteen new regressions cover late settling, bounded exhaustion, terminal
  diagnostic pairs, non-retryable statuses, stop/pause/source failures, prior
  invalid reads, discarded pairs, CP-recovery limits and IV-conflict anchors.
  Independent review found no remaining issues and separately passed these
  tests plus 74 adjacent tests.
- The changed-file code/test trace-anchor scan reported zero findings;
  `git diff --check` passed. This does not claim full-storage reliability or
  live acceptance. No exact historical failed frames exist for visual replay.
- A raw read-only SQLite query verified 510 saved rows for the reported scan
  session, ending in Fearow CP1247 HP110 IV13/13/11 at zero-based position510.
  No records were changed. The app and device received no inputs or restart;
  source changes await a separately agreed reload.

Offline output is `verification.log` in the copy referenced by
`/tmp/pokemgr-appraisal-settle-path.txt`. Anchor scan receipts are
`/tmp/pokemgr-appraisal-settle-traces.jsonl` and the matching Markdown report.
