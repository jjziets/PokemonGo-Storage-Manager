# Reviewed cleanup and scan recovery publication

The owner requested "commit and push" on September 13. The existing target is
`dev` at `git@github.com:jjziets/PokemonGo-Storage-Manager.git`, confirmed by
the repository configuration, intent contract and live remote HEAD. This
publication includes the previously local fixed-canvas commit `98af90a` and
the pending reviewed changes below. No PR, merge, device operation or database
mutation is part of this publication.

## Scope and reused evidence

- Reviewed PvP cleanup preserves all KEEP and 3–4-star records, selects matching
  unwanted groups together, verifies grouped carousel inventories and saves
  confirmed OFF states atomically. Generic Oricorio observations are supported
  only where the reviewed group covers every possible identity and occurrence.
  See `2026-09-12-pvp-cleanup.md`, `2026-09-13-pvp-cleanup-batches.md` and
  `2026-09-13-pvp-cleanup-uniform-groups.md` in this directory.
- Decisions and Mass Actions show selected pass, verification/action phase,
  cumulative progress and unresolved outcomes through the shared progress panel.
- Tablet navigation and checkpoint recovery use observed departures and fresh
  confirmation pairs; database clearing is transactional and active-worker
  guarded. See `2026-09-11-tablet-recovery.md`.
- Appraisal instability receives bounded read-only retries with diagnostic
  frames. See `2026-09-12-appraisal-settle-retry.md`.

Baseline REQ-BASELINE-2026-09-09-001 remains unchanged. Its semantic SHA256 was
recomputed as
`4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`.
The matrix links this scope to REQ-MASS-001, REQ-SCAN-001/002/003/004,
REQ-IDENTITY-001, REQ-DECISION-001, REQ-STREAM-001 and REQ-DATA-001.
Prior scoped reviews are reused because behavior, linked tests and verification
remain unchanged. Completed review documentation and requirement-link comments
differ from the tested copy. This record is publication bookkeeping, not a
changed requirement.

VER-PUB-20260913 reuses the complete isolated suite in
`/var/folders/2f/ntb_0p9558v4wfcwr5_64dg00000gn/T/pokemgr-cleanup-groups-_pb0gntb/full-pytest.log`:
**1,535 tests and 11,173 subtests passed, 17 existing skips**. All current source
and tests match that copy's executable syntax trees; publication added only
requirement-link comments. Private-capture/native-build skips and
the existing requests dependency warning remain disclosed. The latest scoped
trace-anchor report contains zero findings, and `git diff --check` passes.

TRACE-PUB-20260913 and REVIEW-PUB-20260913 identify this scope/evidence check;
the linked implementation records retain their independent-review results.
Runtime databases, calibration, captures, logs, generated artifacts and the
paused cleanup monitor checkpoint remain outside the Git candidate.

## Boundaries

This publishes source and reproducible tests. Complete unattended cleanup,
universal device compatibility, measured live throughput and full transfer
advice remain unverified. The no-device Mac launcher behavior is diagnosed but
unchanged: the paired launcher still requires an authorized ADB device before
opening the GUI. The separately requested cleanup run has not started because
the Mac locked; its five-minute monitor remains paused.
