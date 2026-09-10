# Appraisal bar animation recovery

The owner reported another stop after Snorunt CP523, at 21:45 SAST. The stopped
Normal segment `ff0c0137-b001-4399-b646-92dfd7a41517` stored 136 rows and visited
137 positions, with one verified gym Voltorb skip. The database contains 1516
rows; 1524 Normal positions have been visited, leaving 1374 of the observed
2898. The pending, unsaved specimen is male costume Pikachu CP523, HP75,
IV15/10/15, caught July 16, 2026.

The saved recovery frames show actual bar animation: the earlier attack bar is
orange and reads 14; the later bar is pink and reads 15. The badge changes from
two to three stars. Both frames retain HP75, defense10, stamina15 and the same
caught species/name. The bar classifier correctly reports each frame's visible
state at confidence .95; the earlier state was acquired before animation ended.
The tight attack-bar pixel difference is 40.42, compared with .97/.98 for the
other bars. Broad static-region averages dilute such narrow changes.

This is an authorized local fix under the owner's repeated scan-recovery
requests and advisory baseline REQ-BASELINE-2026-09-09-001, hash
`4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`.
REQ-SCAN-001/002/003 govern exact evidence, independent confirmation and
position accounting. The allowed change waits for bars and reacquires a
conflicting pre-input observation within a bounded, witnessed position.
Identity, CP and transition requirements remain in force. No publication,
database clearing, resource spending or transfer is part of this work.

Before editing, a read-only live acquisition with taps/swipes forbidden
confirmed the now-settled Pikachu/523/75/15/10/15 tuple at level20. Evidence is
`cache/scan-supervision/live-pikachu-proof.json`. The original 1516-row digest
is stored in `cache/scan-supervision/pikachu-resume-prefix.json` for resume
verification. Prior tests establish earlier changes only; new verification
is required for this behavior. The packaged TraceWeaver runtime is unavailable,
so these are local advisory checks rather than packaged acceptance claims.

The new narrow-bar predicate checks geometry, complete IV values, contiguous
fill, the fill edge and central-strip color on each bar, using calibrated boxes
when dynamic location is unavailable. Its palette checks reject blank fallback
regions. It allows at most one scaled pixel of fill jitter and mean RGB noise
of three; larger movement, including motion within one rounded IV value, keeps
settling. It supplements existing appraisal stability and source checks.
Private replay rejects the animated pair and accepts the later settled capture
against the saved final frame. Five local measurements were 10.1–12.9 ms per
pair; this measures the added predicate, not end-to-end scan throughput.

A typed pre-input IV conflict can now spend the remaining acquisition attempts
on a fresh pair, only after the original position was witnessed. All other
identity fields must remain exact. The original transition requirement and
pause generation persist. Every rejected image is excluded, source metadata
must agree, and capture timestamps must be finite, ordered and nonoverlapping.
Both replacement frames must independently confirm the complete new readings
before CP recovery resumes. Persistent disagreement stops after the existing
three-attempt budget and cannot turn into an invalid-row favorite or a guessed
record. Generic failures after any recovery input are not retried this way.

Validation: 940 tests and 1218 subtests passed, with three optional skips and
the existing Requests dependency warning. New tests cover moving bars within a
rounded IV, maximum color transitions, calibrated fallback, compression noise,
fresh replacement evidence, retained transition requirements, bounded exhaustion,
pause/abort and post-input failures. Independent review identified missing
legacy capture chronology; this was fixed and tested alongside discarded-frame
reuse. Final review found no remaining actionable issue. The local code-anchor
check passes with zero findings after adding missing test-file verification
anchors; diff checks pass. These are local advisory results.

At 22:02 SAST, the paired launcher restarted with the physical display off.
The patched acquisition again confirmed Pikachu/523/75/15/10/15 without any
device input. Scan from Here then started session
`f723a327-54c7-4497-ba10-a1ab2918cf7c`, offset1524, Max1374, Skip0,
Unfavorite disabled. At 22:03, ten new rows were committed beginning with
Pikachu, Chimchar and Hatenna; all original1516 rows retain their exact digest.
Database total is1526 at this checkpoint. The scanner remains active and owns
device input. Resume evidence is in
`cache/scan-supervision/pikachu-resume-verified.json`. The 30-minute supervision
ledger records the new session and eight earlier skips; later disjoint tagged
category passes remain pending. Full inventory completion is not claimed.
