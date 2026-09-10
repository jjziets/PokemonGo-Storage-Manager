# Final category recovery

<!-- TRACEWEAVER: file-role=scan-recovery-review; req=REQ-SCAN-003,REQ-DATA-001; trace=TRACE-SCAN-003,TRACE-DATA-001; verifies=VER-SCAN-001 -->

The first seven disjoint category passes completed 335 positions: 333 saved
and two reviewed skips. Database flags and position gaps agree with the log.
The two skips are Moltres deployed at Power Spots, whose pages show RECALL
without numeric HP or storage CP. The scanner's generic gym wording does not
make them ordinary gym defenders. No recall was performed.

Category-08 confirmed eleven matches twice but failed before creating a scan
session. It opened Rillaboom CP3550 correctly. The detail page's pink Max Moves
banner satisfied the old map detector, which accepted any sufficiently red
patch near the bottom centre. The saved stopped frame independently reproduces
this false `game_map` result.

The map marker now requires a centred red hemisphere with a white lower half
and neutral centre button. The existing map/appraisal/storage/detail detection
order remains unchanged; the broad HP heuristic was not promoted above map
recognition. Portable regressions reject banners, rectangles, misplaced and
partial buttons, and preserve compressed/scaled phone and tablet map buttons.
Eight saved map/detail/storage/appraisal images retained their expected
classifications, including Rillaboom now classified as detail.

Full suite: **1,016 tests and 1,836 subtests passed**, three optional skips,
and the existing Requests dependency warning. Independent detector review
found no actionable issue; additional calibrated-position and incomplete-button
probes passed. Changes are local and uncommitted.

## Recoverable Normal skip

The original Normal session `e2abb877-cd37-43fb-91dc-b99fee168e0d` skipped its
zero-based position 115 after misreading the Nidoran gender glyph. Its saved
appraisal clearly shows Nidoran Female CP648, HP112, IV15/15/13, 5.29kg, 0.31m,
and catch date 2025/08/16.

A verified exact Normal CP648 search found two cards, Nidoran followed by Bagon.
The Nidoran was opened, then all phone inputs were prohibited while acquiring
two fresh accepted snapshots. Both resolve the exact female form at level28,
with matching CP/HP/IVs, size and sex. The native date field is blank in the old
and new frames; the matching date was explicitly confirmed visually in all
three images and is not claimed as OCR evidence. Its existing favorite remains
on. The private one-record audit is separate from the production database.

The append-only import uses a new deterministic recovery session and an atomic
marker keyed to the original skipped occurrence. It checks the complete
3,220-row prefix, the absent original slot and keeper signature, then inserts
one record and its session with plain INSERTs in one transaction. Historical
rows, session totals, positions and skip history remain unchanged. A complete
in-memory database copy passed the import and rejected a repeated apply.
The coverage ledger must link the old skip to its recovery record, reducing
unresolved Normal review skips from eight to seven without changing historical
traversal or counting the recovered entry twice.

Evidence is local under `cache/scan-supervision/`: the misclassified Rillaboom
frame, `before-final-category-prefix.json`, `nidoran648-recovery/proof.json`,
two fresh appraisal images and the original failure screenshot. This document
alone does not claim the remaining category queue or recovery import completed;
the ledger and import receipt record those outcomes.


## Final queue and count outcome

The verified Nidoran import completed as row3221 in recovery session
`e8b9e0f8-6354-5200-99f6-acec235efdf7`; its original skip remains linked rather
than rewritten. The final queue loaded only category08–15 at00:56:27SAST.
Category08 completed eleven records, eleven positions and zero skips, session
`d9f57e98-a056-430a-851c-50f52d2c12f6`, IDs3222–3232, all tagged Dynamax/Gmax.
Categories09–15 each explicitly verified Q(0); the worker completed00:59:55.
Independent review checked session positions, tags, empty-pass evidence and
all three original2887/pre-final3220/post-Nidoran3221 prefix hashes unchanged.

The database now contains3232 records. The completed15category partitions
account for346 entries (344saved and2PowerSpot reviews); Normal accounts
for2898 (2888saved,7gym reviews,3inaccessible fusion donors). Total3244.
A stopped-worker count-only audit independently verified cp0-523=1475 and
cp524-=1769, with !cp0-=0. Thus the exhaustive searchable count equals the
partition ledger exactly. The overall owned/capacity header still reads
3246/3250, a separate two-slot discrepancy whose cause is not established.
An Eggs-tab inspection shows one incubating5km egg; it does not establish a
causal explanation for two slots. No eggs, incubators or Pokémon were changed.

Automatic approval review initially held count-query input because the GUI
and stream remained open. Read-only source inspection established that the
terminal Done log is emitted only after all scanning work returns. Fresh
unchanged terminal logs and independent DB review established worker idleness;
the subsequent search-only audit was approved and completed. An open stream
alone does not mean a scanner owns phone input.

The seven gym defenders show their full storage CP in a separately verified
`defender` grid. A bounded external-evidence recovery is being evaluated:
exact single-card CP/category queries, fresh repeated authoritative caught
species/IV/catch-date readings linked to the historical skip, followed by an
independently verified exact HP search and repeated appraisal identity. The
ordinary appraisal remains explicitly in_gym and rejected; no hidden fields
are fabricated in its snapshot. Pikachu candidates remain a generic family
when forms agree numerically. This paragraph does not claim deployed recovery
records have been imported; individual proofs and receipts must establish that.


## Separate deployed-entry proof

All seven gym defenders completed the external proof without phone/account
mutations beyond navigation and exact search. Each original review occurrence
has a unique known IV tuple; an exact single-card query establishes its current
storage CP. Two fresh settled appraisal frames then match that IV tuple,
authoritative caught species and the nonempty original caught date. After
returning to storage, the full CP/category query plus the candidate's exact HP
must again produce exactly one card. Two further fresh appraisal frames must
confirm the same original identity. Every capture follows its own completed
query, all four are ordered from one continuous source, and metadata is retained.
Blank size/sex fields are explicitly not used as positive identity evidence.

The complete query also proves favorite, not lucky, and pass traits. Gym pages
retain in_gym and absent HP/CP in their raw snapshots. Six Pikachu are stored
only at the generic family level with all nine agreeing form candidates retained
in evidence; no exact costume is invented. The Hisuian Voltorb has independent
100&hisui query evidence. Numeric results:

| Species | CP | HP | IVs | Effective level |
|---|---:|---:|---|---:|
| Pikachu | 710 | 83 | 14/12/7 | 28 |
| Pikachu | 670 | 85 | 6/11/10 | 28 |
| Pikachu | 659 | 86 | 11/4/13 | 27 |
| Pikachu | 646 | 79 | 8/7/2 | 28 |
| Pikachu | 639 | 81 | 9/12/9 | 26 |
| Pikachu | 633 | 87 | 3/2/13 | 28 |
| Voltorb (Hisuian) | 563 | 80 | 13/14/14 | 20 |

The seven-entry importer passed a full in-memory database-copy dry run,
repeat rejection and adversarial invalid-proof/rollback probes. Review repaired
two guards: validate and hash the exact same file bytes, and normalize species
for duplicate keeper signatures. Each new occurrence uses a deterministic new
session and an atomic meta recovery marker; historical rows/sessions/positions
remain unchanged. Production still has3232 rows at this review point.

The two Power Spot Moltres use separate explicit RECALL/destination evidence;
the native raid-specific in_gym flag remains false while the legacy button
classifier reports true. Exact text and its observed bounded regions are
required on the original and all four fresh frames. No RECALL button is tapped.
The verified Moltres/Dynamax grid shows one already-saved CP2256 and deployed
CP1934/CP1905. Exact IV resolution produces normal Moltres L20/HP130 for both.
Their live HP-query proofs and production import remain pending until their
individual evidence files and receipts confirm success.


The first CP1934 Power Spot proof held before its HP query because the raw
Lucky fallback changed True→False. Independent offline replay confirmed this
was the only differing snapshot field; native Lucky was unavailable and every
other field, the IV bars and static appraisal regions agreed. The animated fire
background triggered the visual heuristic. The original held screenshots are
preserved under `deployed-recovery/1934-initial-lucky-hold`.

The separate Power Spot proof now compares every raw snapshot field except
Lucky, preserving both raw readings. Lucky=false comes exclusively from the
exact `!lucky` constraint independently verified in both single-card CP and HP
queries. The importer enforces that same query authority; no ordinary scanner
rule or raw appraisal is changed. Actual Power Spot success/import still
requires fresh complete proof and its receipt.


## Committed recovery and final coverage

Both Power Spot proofs completed without recalls. CP1934 andCP1905 each
matched HP130, IV12/13/12 and10/11/11 respectively, and level20. CP1934's raw
Lucky readings [false,true,false,false] remain in the proof; both complete
queries independently establish not Lucky. All nine actual deployed proofs
passed the full in-memory copy dry run and repeat rejection. Independent
review passed94 final rejection/rollback probes, including all other snapshot
fields, missing/nonboolean raw Lucky evidence, query authority, file/proof
binding and rollback after a prior valid insertion.

The atomic production import completed at01:36SAST: nine new records,
IDs3233–3241, with nine deterministic recovery sessions and meta markers. The
entire prior3232-row prefix is unchanged, as are historical session totals and
positions. New Moltres rows3233/3234 have Dynamax=1; Hisui Voltorb3235 has exact
form evidence; Pikachu3236–3241 retain generic-family representation. No stars,
deployments, fusions, eggs, resources or existing records were changed.

Final coverage is3241 saved plus3 explicitly inaccessible fused donors =3244
searchable inventory entries, with zero unresolved review skips. All ten
historical review skips are linked to recovery rows (one Nidoran, seven gym
and two Power Spot entries). Normal2895saved+3fused=2898; all other disjoint
categories total346saved. Historical scan skip counts remain historical.

Reshiram2250, Zekrom2257 andSolgaleo2815 still cannot open appraisal while fused;
the game explicitly returned that restriction. Their HP/IVs were not guessed,
and they were not unfused. The full storage header remains3246; exhaustive
searches account for3244 and !cp0-=0, so the two-slot header difference remains
unexplained rather than represented as fabricated Pokémon.

Authoritative final artifacts are `cache/scan-supervision/final-coverage.json`,
`state.json`, `after-deployed-recovery-prefix.json` and
`deployed-recovery/import-result.json`. The paired app remains open and idle.
The requested supervision can now pause with the three inaccessible entries
reported explicitly; this is not a claim that all3244 have full appraisal data.

Final independent read-only audit passed: all nine recovery receipts, flags, source links and proof hashes agree with the committed rows; all original session totals/gaps and saved-row prefix hashes are preserved. The heartbeat is verified PAUSED, and the idle phone storage search has been restored to cp0-.
