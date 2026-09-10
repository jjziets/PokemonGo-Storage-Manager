# Favorite state persistence

The owner identified that scans recorded favorite stars but later app actions
changed the phone without updating those stored flags. This left keeper planning
and the Collection's Fav column stale. The owner explicitly requested that scans,
keeper favoriting and unfavoriting keep the database consistent, and directed us
not to interfere with the currently running favorite pass.

Authority: REQ-MASS-001, REQ-DATA-001 and REQ-SCAN-004 under
REQ-BASELINE-2026-09-09-001, semantic SHA256
`4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`.
This is an authorized local defect fix. Requirements and baseline are unchanged.
TraceWeaver remains advisory; packaged gate closure and publication are not claimed.

## Behavior

- Keeper actions save affirmative same-position star readback, including an
  already-correct live star whose stored flag was stale. A unique exact
  species/form, CP, HP, IV and authoritative pass-flag match commits immediately.
- Indistinguishable rows are updated atomically only after the complete group
  is observed in one traversal. A restart clears incomplete group evidence;
  it preserves previously committed work. Cross-session ambiguity is not guessed.
- A failed input, unknown star, changed identity or missing post-input readback
  cannot save a target state. Database write failure preserves the actual phone
  change count, reports the save failure, and stops before another input.
- Unfavorite All and supported category actions use a strictly verified count.
  Their CP-free reads cannot assign partial traversal observations to exact
  database rows. Only a complete successful traversal synchronizes the entire
  corresponding recorded scope. Interrupted or unsupported scopes explicitly
  report confirmed phone statuses that were not saved to individual records.
- Uniform synchronization supports all storage, shiny, shadow, lucky, perfect
  IVs and 3-star IVs. The stored Dynamax flag also covers Gigantamax, so neither
  query is assumed to have exact database membership. Costume, purified and
  arbitrary query membership also remain unsupported for uniform synchronization.
- Optional unfavoriting during an ordinary scan verifies the unchanged complete
  identity and an affirmative OFF readback after at most one tap, then stores the
  corrected snapshot and flushes it before advancing. Normal scans retain their
  existing fast path and batched commits.
- Real action completion refreshes Collection and Decisions after worker cleanup,
  including partial and failed outcomes. Saved and unsaved favorite status counts
  are visible. Dry runs neither write nor claim persisted changes.

## Verification and limits

402 targeted tests passed. After adding immediate scan-commit durability,
71 focused integration, scanner, database and GUI tests passed again. Coverage
includes real temporary SQLite databases read through independent connections,
atomic rollback, first-record interruption, duplicate groups and restarts, stale
already-correct stars, failed readback/input, database failure after a confirmed
tap, strict-count failure, next keeper planning after complete unfavoriting,
worker cleanup and GUI result handling. No production database was opened or
modified for this fix; no phone, stream, app UI or running process was controlled.
The durability test uses an independent SQLite connection inside the next
navigation callback to prove OFF is committed before the scan ends. Final
`git diff --check` passed.

An independent reviewer reproduced duplicate/restart and rollback behavior in
memory. The review found a potentially unsafe Dynamax bulk mapping; it was removed
and a regression test added. No actionable findings remained in that review.

Uniform synchronization assumes the database represents this phone's current
collection, as the existing collection and decision workflows do. External/manual
star changes still require a new observation. Partial CP-free category actions
may leave stored flags unchanged and need rescanning; they never silently claim
that every database row was updated.

The currently running app keeps its loaded code. These changes take effect after
it is restarted; no restart or live validation was performed because the owner
has an active favorite run. Earlier changes in the dirty tree were preserved.
