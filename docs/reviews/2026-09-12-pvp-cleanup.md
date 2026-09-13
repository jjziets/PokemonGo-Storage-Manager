# Selective PvP favorite cleanup

## Scope and authority

The owner requested selective removal of weaker PvP favorites after scanning
and selecting keepers, preserving favorites kept for age/events. On September 12
they explicitly confirmed that every 3★ and 4★ Pokémon must stay protected,
and that their other low-appraisal favorites serve only a PvP purpose.

Local authority decision: proceed with reviewable, selective unfavoriting under
REQ-MASS-001, REQ-SCAN-004, REQ-DATA-001 and REQ-DECISION-001. Existing advisory
baseline REQ-BASELINE-2026-09-09-001/hash
`4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`
is retained. This follow-up records the owner's narrower selection policy; no
publication, automatic live action, transfer or spending is authorized here.

## Selection and execution

- Review saved favorite records whose decision is explicitly TRANSFER and
  whose three valid integer IVs sum to at most 36. KEEP, undecided and 3–4★
  favorites cannot be selected. No low-star rating itself establishes poor PvP
  quality: the saved decision supplies that choice.
- Hold incomplete identity, duplicate IDs, missing session and every duplicate
  full signature, including unstarred, deselected or protected companions.
  Incomplete companions also hold a candidate when their known fields cannot
  distinguish them; missing HP or IV evidence cannot prove a different Pokémon.
  Preview and worker receive detached copies of exact selected records.
- Both Decisions and Mass Actions offer the same preview with deselection,
  dry run and real action. Stop/pause and partial outcomes use existing action
  worker controls. Progress identifies cleanup and uses unfavorite wording.
- Stable category/flag and exact CP/HP queries retain membership when stars change.
  Independent game counts must prove a single live result, including both
  Dynamax variants when the stored flag cannot distinguish them. Reopening
  verifies count one again before opening appraisal; collisions stay held.
  Complete appraisal species/form, CP, HP and IV validation determines each
  live match; recorded nicknames do not identify the specimen.
- Verified OFF readback precedes database synchronization. No blanket clear of
  favorites, account transfer, or rewrite of keeper decisions is part of cleanup.
- Candidate decisions and identities are checked again before star input and
  saving. Conditional database writes commit atomically or roll back; failed
  persistence retains the known phone change in the partial result. Selected
  sessions must match the connected device/calibration; this
  is device binding, not proof of the signed-in game account.
- The pre-existing manual KEEP/TRANSFER buttons changed only the displayed
  model. They now persist the requested decision before refreshing, and manual
  changes are disabled while an action runs. This prevents a displayed KEEP
  from being ignored by cleanup's database-based protection.
- Appraisal stars derive from the three valid IV integers, including the 36/37
  and 44/45 boundaries; stale rounded percentages cannot authorize cleanup.

## Limits

The app does not store why a user starred a Pokémon, an account identifier,
catch age or event history. The owner must use the account the scan came from;
the preview permits personal exceptions. The existing decision model's evolution,
moveset and ranking-cache limitations remain: this action executes reviewed
saved decisions rather than presenting a new PvP assessment.

## Verification

- Complete isolated Python suite: **1,470 passed, 11,097 subtests passed,
  17 skipped**. The skips require private captures or the locally built native
  stream client; one existing Requests dependency-version warning remains.
  The copy used offscreen Qt and an unavailable ADB path, with production
  environment overrides removed. No native streaming code changed in this feature.
- Independent adversarial review closed the live duplicate, partial companion,
  malformed flag and manual-decision persistence findings. Its focused replay
  passed 68 tests and 8,434 subtests; full-suite coverage also checks existing
  scan, favorite, stop/pause and progress behavior.
- `tests/test_database_reviewed_actions.py` uses separate connections to temporary
  SQLite databases for durability, competing writer, conditional update,
  failed readback and commit-failure rollback checks. Both appraisal boundaries
  and every valid IV tuple are covered; detached preview/deselection and dry run
  routing are tested. A rendered offscreen preview was visually inspected.
- `git diff --check` is clean. Production app, device and collection were not
  touched during implementation. No cleanup run, restart or publication was
  performed; live behavior and throughput are not claimed as verified.
