# Nonfavorite keeper passes and gym-defender skips

The owner requested excluding existing favorites and repairing the action that
stopped after Pidgey and Snivy with 231 checked and 12 newly favorited. The log
and retained failure image identify the next appraisal as a gym-defending
Voltorb. Its HP field is replaced by gym information; six fallback reads could
never satisfy the ordinary HP-required identity check. The saved image also
shows its star already ON, so the new filter will exclude this particular card.

Authority: REQ-MASS-001, REQ-DATA-001 and REQ-SCAN-004 under
REQ-BASELINE-2026-09-09-001, semantic SHA256
`4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`.
The stakeholder explicitly authorized this behavior change. The baseline is
unchanged; no package-gate closure or publication is claimed.

## Changes

- Keeper CP batches include `!favorite` before applying the full query length
  limit. The exact occurrence budget includes only eligible recorded unstarred
  keepers. Both Decisions and Mass Actions use this executor and describe the
  exclusion in their confirmation/help text.
- Each traversal has the independently verified original result count as its
  observation ceiling. Confirmed OFF-to-ON changes consume one keeper each.
  After a traversal changes stars, re-enter the full verified query from the
  beginning and require `new count = prior count - confirmed changes`, including
  after the last target. Nonkeepers and any neighbors skipped as the carousel
  shrinks remain available on the refreshed list.
- After mutations, pre-input/navigation failures may discard the carousel and
  refresh. No old-position backtracking or same-count restart is used for the
  mutable filter. An incomplete traversal without changes holds; a complete one
  ends with unmatched targets reported. Already-ON cards never consume a target
  or provide duplicate database evidence. Each changed traversal reduces the
  verified count, bounding progress without a time-based retry loop.
- Dispatch begins the uncertain-input phase. Any failure or pause after that
  point holds unless the single tap was affirmatively read back. Confirmed
  changes remain saved if later navigation/count verification fails. A database
  save failure also remains a hard hold. Abort sends no further navigation.
- `FavoriteSync(changes_only=True)` retains distinct OFF-to-ON observations
  across refreshed lists. Unique records save immediately. Indistinguishable
  groups save only after every original group member has a confirmed change;
  historical ON companions provide no extra evidence. Insufficient/multisession
  groups retain their recorded status and are reported as unsynchronized.
  Filter completion cannot authorize whole-category synchronization.
- The keeper unresolved-read fallback permits skip-only identity for an explicit
  gym defender. Two matching species/IV/flag observations must have independent,
  strictly ordered capture/source evidence and settled identity pixels and bars.
  The subsequent advance confirms this same gym identity again. Missing HP on
  an ordinary appraisal is still rejected. The gym skip never supplies a keeper
  key, changes its star, or updates its database row.

## Verification and limitations

The new `test_nonfavorite_carousel.py` simulator exercises the real executor,
query/count guards and star/readback path against frozen and shrinking lists.
It covers skipped neighbors, nonkeepers sharing CP, identical copies, ON-card
replays, count disagreement and unreadability, final count proof, no-progress
holds, dry runs, pause, abort and uncertain dispatched input. Independent review
found that a paused dispatched tap could enter the refresh path; it now raises
a hard hold and has a regression test.

`test_nonfavorite_filter.py` checks restricted opt-in, exact negative conjunction,
mandatory verified counts and no tile input before expected-count agreement.
`test_favorite_change_sync.py` verifies persistence using temporary databases.
`test_action_identity_acquisition.py` and `test_keeper_gym_skip.py` cover gym
capture proof and actual pass continuation without a gym star/database change.
Existing batching, stable-filter recovery and GUI expectations were updated for
the deliberate target-count and filter changes.

The final combined targeted run passed **526 tests** in 3.46 seconds;
`git diff --check` passed. The independent reviewer reported no remaining
actionable findings after the dispatched-input pause correction.

The retained private failure image was inspected from
`cache/scan_failures/00201849-d3ef-4f0c-8999-677dc20100b6/`; no account action was
needed to establish the gym cause. Regression receipts are saved privately in
`cache/favorite-filter-fix/`. Live favorite throughput and a complete run are
not established by the simulator. The normal full-query re-entry is retained;
the proposed shortcut of pressing ENTER on an unchanged search is not used
because its scroll/reset behavior has not been verified on the phone.

The Mac remained locked during UI verification; manual unlock was requested.
No running app was interrupted, no production database was edited by the
diagnostics, and no phone favorite was changed for this work.
