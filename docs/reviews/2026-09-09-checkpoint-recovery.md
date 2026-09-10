# Same-stat checkpoint recovery: Larvesta and Plusle

Scope: authorized local recovery fix, with bounded live Plusle evidence below.
The initial checkpoint-only change and its limitations are retained as history.

## Observed failure

The September 9 scan saved 903 rows, with 903 visited positions and zero skips.
Its final accepted rows were Quagsire CP832/HP116/13-14-10 and Larvesta
CP832/HP94/12-15-12. Both CP values were calculated and independently confirmed.
The next forward observation had the same Larvesta identity. The reverse probe
expected Quagsire but read that Larvesta identity again, then stopped at the
first mismatch. Source: local `logs/macos_launcher.log`, 17:59:34–17:59:41;
session `a86b0d6c-a813-47c6-bfef-7abf364035ac`.

The log cannot distinguish an unsuccessful/delayed swipe from an identical
adjacent Larvesta. There is no saved failure image for this checkpoint probe.
CP recognition did not cause this stop. The normal favorite-and-skip branch
requires a known new position; using it here could count a nonexistent skip
or favorite a previously saved Pokemon.

## Local change and verification

`_recover_failed_transition` now permits at most three acquisitions per phase.
A mismatched exact checkpoint gets fresh reads without another carousel swipe.
An incomplete identity or other failed acquisition still stops. A pause
invalidates the current read even when the reader returned success; recovery
waits for resume and rereads the same phase. Invalidation uses the same bounded
attempt budget. Probes do not add rows or change counters. Logs now include the
expected and observed full identities; exhaustion reports the species involved.

The following command passed **104 tests and 79 subtests** in 2.07 seconds:

```bash
.venv/bin/python -m pytest -q tests/test_transition_retry.py tests/test_stable_scan_loop.py tests/test_stream_appraisal_settle.py tests/test_settled_pair_reuse.py tests/test_scan_recovery.py tests/test_favorite_unresolved.py tests/test_database_positions.py
```

There was one existing Requests dependency-version warning. An incomplete-HP
regression was observed failing before its fix, then passing; no blanket
test-first claim is made. Independent review identified the pause-generation
case, which was fixed and covered before the final run. Final review found no
remaining actionable issue in this bounded change. `git diff --check` passed.

## Authority and limits

Authority is unchanged: `REQ-BASELINE-2026-09-09-001`, semantic SHA256
`4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`.
REQ-SCAN-003 / TRACE-SCAN-003 links the scanner and transition tests; REQ-SCAN-001,
REQ-SCAN-004 and REQ-DATA-001 preserve exact identity and mutation/count limits.
The changed-file trace-anchor check passed with zero findings. The project is
advisory; packaged TraceWeaver child skills were unavailable, so this is local
source/test review, not a formal packaged-workflow acceptance or release claim.

This patch has not been tested on the live phone and does not prove the #903
incident will recover. A persistent identical checkpoint still stops. Automatic
continuation through an unknown number of positions would require explicit
incomplete-coverage tracking, including resume and downstream occurrence use.
No app restart, phone input, database mutation or publication was performed.
The next validation is an operator-observed recovery attempt after restarting
the manager; full-storage reliability remains unclaimed.

## Subsequent live resume

The owner then requested continuation after the 903 saved rows. After restarting
the manager, a verified reverse move reached Quagsire, followed by a forward move
to the saved Larvesta. One further forward move showed an identical CP/HP/IV
tuple but a different specimen: the saved Larvesta was 24.34 kg / 0.91 m, caught
June 20; the next was 37.48 kg / 1.12 m, caught July 31. Thus this incident involved
an identical-stat neighbor, not a CP failure. Fresh images under the ignored
`cache/resume-903/` preserve this evidence. Bounded rereads alone do not resolve
that persistent identity ambiguity automatically.

Scan from Here began on the unsaved Larvesta with a target of 1,995 remaining
Normal positions (2,898 initial filtered count minus 903 saved/visited). The
original session retained all 903 rows; the first new row is database ID904,
followed by Sinistea831 and Cyndaquil831. At verification, 20 new rows had committed
and the UI was scanning. The resumed session has its own position counter, which
starts at zero; no saved rows were overwritten or removed. This mode stops after
the current segment and does not automatically run later category passes. The
active scanner owns device input. Details: `cache/resume-903/result.json`.

## Plusle failure and specimen evidence

The resumed segment saved 103 rows before the same stop recurred at Plusle
CP755/HP87/14-15-14; the reverse checkpoint expected Tropius but saw Plusle.
The two sessions retained 1,006 total rows and zero skipped positions. A stopped-
scanner diagnostic confirmed Tropius, then the saved male Plusle (3.01 kg,
0.31 m), then the next female Plusle (5.91 kg, 0.47 m). Both Plusle have the same
battle tuple and catch date, March 28, 2026. The distinct specimens explain the
failure; there was no observed notification or other-app interference.

The scanner now retains the accepted frame separately from the fresh pre-swipe
frame. When battle stats are identical, two captures before and two after must
each validate the complete tuple and remain settled on their respective side.
At least one confident weight, height, sex or catch-date field must repeat on
each side and change across the swipe. Optional accurate Vision OCR refines
same-frame text only in this ambiguous path. Combined fast/accurate observations
reject conflicts rather than treating a conflicting empty field as missing.
Sex uses a conservative complete-glyph check; missing/unknown sex never proves
a difference. Its slightly expanded crop avoids the legacy crop's clipped
lower circle/cross. Ordinary reads and existing display gender remain unchanged.

All four frames must have distinct chronological capture evidence from the same
stream session and clock generation, or nonoverlapping legacy capture times.
Pause generations invalidate retained evidence, including pauses during OCR or
completion callbacks. CP recovery discards the old transition pair. Successful
checkpoint restoration refreshes the accepted reference only after both exact
checkpoints pass. Keeper actions share the new fallback; category actions and
bulk resume skipping retain their existing stricter transition path.

On the live app display, the acquisition returned `ok` with
`same stats; distinct specimen details confirmed` for the 3.01→5.91 kg move.
All four independently timestamped source frames repeated the expected sizes,
and the diagnostic created zero rows, changed zero stars, and left the game on
the first unsaved Plusle. Private source evidence remains ignored under
`cache/resume-903/live-specimen-proof.json` and `proof-plusle-*.png`. These markers
are evidence for adjacent positions, not globally unique IDs. Truly identical
or unreadable details can still leave a transition unresolved; no blind skip,
full-storage reliability, or GPU utilization is claimed.

## Final local verification

The complete discovered suite passed: **845 tests and 809 subtests**, with
three existing skips and one existing Requests dependency warning, in 21.47 s
(`.venv/bin/python -m pytest -q`). The changed-file code-anchor check passed
with zero findings, and `git diff --check` passed. Independent review covered
frame/pause bookkeeping, parser/cache conflicts, and transition integration;
the glyph detector has synthetic and private-image verification. The live
acquisition was repeated successfully with the final optional sex check in
place. An offline replay of all four source images also confirmed male/male and
female/female, alongside the expected weight/height/date tuples.

That four-image replay measured added accurate OCR at 97.7–192.5 ms per frame
and sex classification at 0.4–0.5 ms after a 3.3 ms initial call. This is a small
local sample, not a whole-scan benchmark or proof of GPU execution. Sandbox
Vision pixel-buffer creation was unavailable; the verified replay used normal
local macOS execution. No forced compute-device change was made.

## Resume after 1,006 saved records

The final code was loaded by restarting the stopped paired manager. A fresh
appraisal on its newly assigned display verified the unsaved female Plusle's
complete battle tuple, 5.91 kg / 0.47 m and catch date before starting. Scan from
Here resumed with a target of 1,892 remaining Normal positions, Skip first zero,
and Unfavorite during scan off. The phone's physical display remained off.

A read-only canonical row digest verified all original 1,006 records unchanged.
Ten new rows had committed, starting with ID1007 Plusle755 (female), ID1008
Crobat754 and ID1009 Cranidos754; the UI had reached 17 new positions and was
still scanning. The new session is `1a29b2fa-f912-4640-814d-e96f905d3fdd` and its
position counter starts at zero. Private resume evidence is
`cache/resume-903/resume-1006-result.json`. This segment does not automatically
start later category passes. No commit, PR, merge or database clear occurred.
