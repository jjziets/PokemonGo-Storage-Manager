# Nidoran caught-name recovery

The owner reported another stop and concern that repeated interruptions would
prevent completion tonight. Supervision and bounded local fixes remain
explicitly authorized. Advisory baseline REQ-BASELINE-2026-09-09-001 and hash
`4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`
apply under REQ-IDENTITY-001 / REQ-SCAN-001/004. Scope is reader normalization
and review-only recovery; exact CP and transition checks remain required.

Session `d896f984-69df-40d8-8871-517dd089f4a6` stopped after178 stored,
180 visited and two verified gym skips. The database has1,380 saved rows;
Normal has1,387 visited positions and1,511 remaining of its observed2,898.
The last saved Pokemon is Scraggy576. The next unsaved appraisal is visibly
male Nidoran575, HP94, IV12/15/15. The original and reread caught names were
`Nidorano'` and `Nidorano`: OCR lost the male sign differently. The hard
identity mismatch happened before any model/detail input. The log's
“Nidoran Female” was the fuzzy display-name suggestion, not authoritative
caught evidence, and must not decide the species.

A bounded helper now handles only literal caught-text Nidoran forms. Explicit
male/female symbols or words remain distinct; an opposing clear same-frame
sex icon is a conflict. The observed ambiguous Nidoran/o/o-apostrophe forms
need strict same-frame male/female glyph evidence to resolve. Unknown suffixes
and missing/conflicting evidence remain unresolved. Other species are unchanged.
The caller uses the original frame's strict glyph reader and preserves that
stronger gender result. It never uses editable nicknames, fuzzy display matches,
previous frames or CP plausibility to choose a sex.

Both saved failure frames independently yielded a strict male icon. A live
acquisition with all device taps/swipes forbidden confirmed the exact tuple
Nidoran Male/575/94/12/15/15 at level24, using two settled appraisals and no CP
animation or database writes. Private evidence:
`cache/scan-supervision/live-nidoran-proof.json`. The1380-row prefix digest is
saved in `cache/scan-supervision/nidoran-resume-prefix.json` for resume checks.

Caught-text changes limited to case, whitespace, Unicode normalization and
terminal quote punctuation now have a separate review-only path. Complete HP
and IV reads, unchanged display name, gender, flags and candy evidence, and
distinct stable appraisal frames must agree. All species letters, digits,
gender symbols and form words remain significant. Three bounded reads latch
review mode even if the spelling returns to its original value; this path
cannot produce CP evidence or a stored row. A stable witnessed position can
then preserve an existing favorite or set it once, verify the star, count one
skip and clear transition checkpoints. Pause/resume keeps the confirmation
phase after a possible tap, so it cannot toggle the star a second time.

Validation: 918 tests and 1,175 subtests passed, with three optional skips and
the existing Requests dependency warning. This includes 17 new caught-name
review regressions, strict Nidoran acquisition and helper tests, and existing
CP recovery, gym review, transition, pause and abort coverage. Independent
read-only review found no actionable issue. The local implementation anchor
check passed with zero findings; `git diff --check` passed. These are local
advisory results, not a claim of packaged TraceWeaver acceptance.

At 21:38 SAST the patched app resumed from that independently verified unsaved
Nidoran with Max 1511, Skip 0 and Unfavorite disabled. New session
`ff0c0137-b001-4399-b646-92dfd7a41517` begins at Normal offset 1387. Its first
committed rows are Nidoran Male 575, Taillow 575 and Sandile 575, starting at
position 0. Ten new rows were committed during verification, bringing the
database to 1390; the original 1380-row digest is unchanged. The app-only
stream reports the physical display off. Private integrity evidence is in
`cache/scan-supervision/nidoran-resume-verified.json`.

The 30-minute thread heartbeat remains active. Its authoritative local ledger,
`cache/scan-supervision/state.json`, seals the stopped segment and records the
new session, remaining target and seven earlier verified skips. Scan from Here
does not launch later category passes automatically. The separate tagged,
disjoint category queue and confirmed-empty handling remain required before
those passes; overall inventory completion is not claimed.

Follow-up at 21:40 SAST: 50 new rows committed (1430 total). A gym-defending
Voltorb at session position 38 was verified already favorited and skipped; the
scan continued to Popplio and beyond at about 26/min. This eighth overall skip
is recorded separately in the supervision ledger.

## Supervision follow-up: bare apostrophe glyph

The 22:34 SAST heartbeat observed the next Normal segment running, then caught
a stop at 22:35:50 after 572 stored/visited positions and zero new skips. The
database contains 2088 rows; Normal has visited 2096 of 2898, leaving 802.
Both saved position573 frames show male Nidoran353, HP73, IV13/12/12, and the
strict same-frame sex detector returns male. The caught text changed from
canonical Male to raw `Nidoran'`, which the helper rejected as an unknown
suffix. This stop preceded recovery input.

The bounded ambiguous pattern now accepts one ASCII or curly apostrophe after
the literal Nidoran stem, with or without its already-supported trailing `o`.
It still needs strict same-frame male/female evidence. Explicit contradictions,
unknown letters, doubled quotes and mixed suffixes stay unresolved. This is an
authorized local follow-up under the same identity/scan baseline; no resolver,
nickname or CP-plausibility inference was added. Regressions include both sexes,
missing gender, invalid suffixes and the real353/73/13/12/12 acquisition tuple.
An independent saved-frame review and a live all-inputs-forbidden acquisition
both confirm Nidoran Male353 at level15. Private evidence:
`cache/scan-supervision/live-nidoran353-proof.json` and
`cache/scan-supervision/nidoran353-resume-prefix.json`.

The full suite passes: 941 tests and 1252 subtests, three optional skips and the
existing Requests dependency warning. Local implementation anchors pass with
zero findings; diff checks pass. No packaged acceptance or publication claim.

## Supervision follow-up: single-digit glyph

The quote fix resumed at Nidoran353 and stored fourteen new rows, then stopped
at a female Nidoran whose caught-text sex symbol was read as `Nidoran 4`.
The saved frame and strict same-frame icon independently establish female;
CP348, HP83 and IV15/14/15 resolve exactly at level15. The fuzzy detail-name
candidate incorrectly said Male and remains excluded from this resolution.

The ambiguous suffix now permits one ASCII digit or `o`, optional separator
whitespace, and at most one quote. Bare stems and quote-only variants remain
supported. Every such read requires the independent strict sex icon; the
digit itself supplies no sex evidence. Unknown letters, multiple or Unicode
digits, form words and explicit sex conflicts remain held. Tests cover all ten
digits with both sexes and absent/invalid evidence, plus the real female tuple.
An independent reviewer found no issue with the incremental implementation.

The full suite passes 943 tests and 1690 subtests, with three optional skips
and the existing Requests warning. Local implementation anchors and diff
checks pass. Both pre-restart and post-restart live acquisitions confirmed
female Nidoran348 without taps, swipes or database writes.

Session `a08f0d10-ba9e-41d4-aa4b-49ca35848a38` resumed at Normal offset2110,
target788, skip0 and unfavorite off. Its first rows were female Nidoran348,
Elgyem348 and Litten347. Twenty new rows were committed at the integrity
checkpoint; all original2102 rows retained their complete-row SHA256 digest.
Evidence: `cache/scan-supervision/nidoran348-resume-verified.json`. The ledger
seals both stopped segments and retains eight earlier verified skips. This
establishes a correct resume, not full-inventory completion.
