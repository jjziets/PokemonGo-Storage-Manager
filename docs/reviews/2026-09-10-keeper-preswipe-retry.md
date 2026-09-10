# Keeper check before swiping

<!-- TRACEWEAVER: file-role=keeper-preswipe-review; req=REQ-MASS-001,REQ-SCAN-003; trace=TRACE-MASS-001,TRACE-SCAN-003; ver=VER-SCAN-001 -->

Authority: the owner's stopped-favorite screenshot and existing shared-action
requirements REQ-MASS-001 / TRACE-MASS-001, with REQ-SCAN-003 preserving position
checks. Baseline REQ-BASELINE-2026-09-09-001, semantic SHA256
`4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`.
Advisory authority decision: Proceed with bounded observation retries and
failure evidence. This does not authorize new phone actions or decision/data
changes. Packaged TraceWeaver runtime gates are not claimed.

## Incident evidence

The run searched `!shiny&!shadow&!dynamax&!gigantamax&!lucky`, with 2,886 matches,
and stopped at 06:05:06 on action position 20: Kommo-o CP3652, HP162, IV15/15/15.
The last logged species/name reads both say Kommo-o. The 20 observed entries
each map to a single already-favorited KEEP row. The skipped counter means
they were outside the pending keeper signatures; it does not mean 20 failed
scans. No pending keeper was consumed and no star was changed.

The executor stopped on the first disagreeing pre-swipe read. It logged neither
the compared fields nor pixel differences and saved no failure frames, so the
original differing field or region cannot be established. Eight later fresh
frames from the still-idle Kommo-o page all agreed, with static-region mean
differences below 0.22 (threshold 1.5). These later reads are not proof of the
original cause. The read-only diagnostic disabled phone inputs and had no DB
connection; its private evidence is in `cache/keeper-preswipe-proof/`.

## Local change and verification

The unchanged fast path takes one fresh matching read. On disagreement, the
executor permits at most three read attempts per pause generation, separated
by 100ms. Recovery requires two new matching reads, ordered source evidence,
stable static pixels and stable narrow IV bars. Every comparison retains the
original completed identity and image. Original, failed, duplicate and
pre-pause source receipts cannot be reused as new confirmation. Weak image
references avoid retaining captured buffers. Pause resets the pending proof;
abort prevents subsequent input. Counts, keeper consumption and stars are
outside this retry, and the only successful input is one next-position swipe.

Persistent disagreement stays held. Logs now include changed fields and region
differences; both comparison images are saved with the actual action ordinal.
The scanner evidence helper's optional ordinal preserves its existing default
scan-position behavior.

161 focused tests passed across pre-swipe retry, action scanning, both UI action
routes, worker cleanup, bar settling/reacquisition and specimen/transition
guards. This includes 25 new retry/evidence regressions. A saved-image replay
used actual Kommo-o OCR and bar reading, injected one missing-HP observation,
then confirmed recovery from two matching images with the swipe mocked. This
is synthetic transient-failure validation, not a reproduction of the original
unknown mismatch.

Independent review caught a missing narrow-bar check and reuse of a frame
across pause/resume; both were repaired and covered by tests. Local traceability
passes for this scope, and `git diff --check` passes. Other accumulated changes
were outside this review. No real favorite run, restart, DB write or publication
was performed. Runtime reload and full keeper-run completion remain unverified.
