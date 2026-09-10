# Minun checkpoint and compatible clock refreshes

Authorized local fix under REQ-SCAN-003 / REQ-STREAM-001, baseline
REQ-BASELINE-2026-09-09-001, semantic SHA256
`4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`.

## Evidence and cause

The third scan segment stopped after 47 stored/visited and zero skipped, leaving
1,053 saved rows. The previous checkpoint was Minun CP717/HP86/15-14-12, while
the reverse probe read Minun CP715/HP87/14-13-14. The error displayed only their
shared species name. Stopped-scanner navigation confirmed the saved CP715 male
at 3.35 kg / 0.36 m, followed by another CP715 female at 5.41 kg / 0.48 m with
the same HP, IVs and catch date (2026-03-28).

The old log shows specimen proof exited before its four OCR reads, but lacks
metadata to distinguish its clock guard from its pixel-stability guard.
Investigation found a reproducible clock bug: the stream refreshes its mapping
every ten seconds, and every compatible refinement increments its revision.
Requiring equal revisions across all four specimen frames therefore rejects
valid moves that cross a routine refinement.

## Fix and verification

Each clock instance now supplies an independent random continuity token in its
immutable frame bounds. Compatible observations retain it; invalidation,
failure and expired coverage replace it. Generation, clock compatibility,
freshness, source age and input barriers retain their existing checks.

The specimen comparison permits differing revisions only with matching valid
continuity tokens, one stream session, increasing sequence/PTS, nondecreasing
revisions and finite nonoverlapping capture bounds. Missing tokens retain the
old strict revision rule. Partial/conflicting tokens fail even when revisions
are equal. Four exact tuples, repeated differing details and pause checks still
apply. Rejection logs now identify the guard and relevant evidence; checkpoint
errors include species, CP, HP and IVs.

A live test deliberately waited 10.5 seconds before the swipe. Four fresh
Minun frames had revisions 1,2,2,2 and one continuity token, with repeated
3.35→5.41 kg observations. The old revision rule rejected them; the new
acquisition returned `ok` and confirmed the distinct specimen. It created no
rows or star changes and left the game on the unsaved female Minun. Private
evidence: `cache/resume-903/live-minun-clock-proof.json` and `proof-minun-*.png`.

Full pytest: **860 passed, 3 optional skips, 887 subtests**, one existing Requests
dependency warning, 20.32 s. Final focused clock tests: 40 passed/43 subtests.
Independent review found no actionable issue in the continuity model/consumer.
Changed-file trace anchors and `git diff --check` passed. This is local advisory
verification, not a formal packaged TraceWeaver acceptance or publication.

The exact cause of the original uninstrumented rejection remains unproven;
the reproduced clock failure is fixed. Full-storage unattended reliability is
not claimed. No database clear, commit, push, PR or merge was performed.

## Gym defender found during resumed scanning

The resumed session `5527e6ed-6090-4a40-b25c-59d5c088f290` accepted the female
Minun and twelve more Pokemon, bringing saved rows to 1,066. It then stopped on
a balloon Pikachu defending a gym: the appraisal showed GO TO GYM and no HP,
with visible motivation CP142 and IVs 14/12/7. The old favorite-for-review path
required HP, so it rejected this known appraisal instead of counting a skip.

Snapshot reads now retain the explicit gym indicator. Gym snapshots are
excluded from all normal CP resolution and model recovery, even if OCR supplies
a stray HP or visible CP. Review favorite handling requires the repeated gym
indicator, caught species, display name, IVs and stable appraisal pixels. It
preserves an observed gold star, or sends at most one toggle and verifies gold
on the same appraisal. A verified gym skip counts one visited position without
creating a Pokemon row. This uses the existing authorized favorite-and-skip
scope under REQ-SCAN-001/003/004.

The stopped-screen diagnostic `cache/resume-903/gym-review-proof.py` prohibited
all device input. It confirmed Pikachu 14/12/7, HP unavailable, and the existing
gold star across fresh stream frames. CP resolution stayed rejected; no tap or
database write occurred. Evidence is `live-gym-review-proof.json` in the same
private directory. The complete suite before the final pause refinement passed
879 tests, 931 subtests and three optional skips in 21.95 s. Gym trace anchors
passed with zero findings.

Independent gym review identified that pause invalidation must retry locally
rather than escape as a fatal error. The final implementation waits for resume,
rereads the same original identity and static pixels, and retains the current
review or post-tap confirmation phase. Each phase has three reads; a star can
be tapped at most once across pauses. Abort returns without further input.
The final suite passed **881 tests, 933 subtests, three optional skips** in
19.20 s with the same existing Requests dependency warning. Final trace anchors
and diff checks passed. The read-only gym proof also passed after clean restart.

At 21:00:26 SAST the Normal remainder resumed from gym Pikachu in session
`e2abb877-cd37-43fb-91dc-b99fee168e0d`, target 1,832 positions, no resume skips,
and unfavorite disabled. Pikachu was verified already starred and counted once
as skipped. The next twenty rows committed at positions 1–20, bringing the
database to 1,086 rows. A SHA256 comparison of all pre-existing 1,066 complete
rows matched exactly. The app continued past row 22 without another error at
the 21:01:18 observation; this is a bounded resumed sample. Evidence:
`cache/resume-903/gym-resume-verified.json`. The running scanner owns device
input. Scan from Here does not automatically launch the later category passes.
