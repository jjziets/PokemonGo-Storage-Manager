# Normal inventory count reconciliation

<!-- TRACEWEAVER: file-role=inventory-coverage-review; req=REQ-SCAN-003,REQ-DATA-001; trace=TRACE-SCAN-003,TRACE-DATA-001; verifies=VER-SCAN-001 -->

The Normal filter `!shiny&!shadow&!dynamax&!gigantamax` showed **2,898** entries.
The completed traversal had **2,887 saved records and eight established review
skips**, accounting for 2,895 accessible appraisal positions. The final session
`a08f0d10-ba9e-41d4-aa4b-49ca35848a38` stopped after 785 visited positions against
its target of 788. Its final Fletchling had no next arrow; the bounded transition
retry did not find another appraisal. That machine target remains unmet.

Count-only CP-range queries narrowed the three-entry difference to small
groups above CP 2,200. Their storage grids contain three greyed cards absent
from the saved records:

| Entry | Evidence | Verification status |
| --- | --- | --- |
| Reshiram CP2250 | [Filtered grid](../../cache/scan-supervision/audit-cp2234-2251.png), [game response](../../cache/scan-supervision/fusion-reshiram2250-response.png) | Confirmed fused; tapping the card displays the fusion message instead of opening an appraisal. |
| Zekrom CP2257 | [Filtered grid](../../cache/scan-supervision/audit-cp2252-2263.png), [game response](../../cache/scan-supervision/fusion-zekrom2257-response.png) | Confirmed fused; tapping the card displays the fusion message instead of opening an appraisal. |
| Solgaleo CP2815 | [Filtered grid](../../cache/scan-supervision/audit-cp2804-2836.png), [game response](../../cache/scan-supervision/fusion-solgaleo2815-response.png) | Confirmed fused; tapping the card displays the fusion message instead of opening an appraisal. |

Each card was independently checked with one tap, without changing the account.
All three game responses read: “This Pokémon is fused with another Pokémon.”
The greyed cards still show HP bars; the washed-out CP and sprite together with
the explicit game response establish donor status. The filter counts these
entries even though they do not open as ordinary appraisals. The query
measurements and narrowed groups are retained
in [normal-count-audit.jsonl](../../cache/scan-supervision/normal-count-audit.jsonl)
and [normal-missing-ranges.json](../../cache/scan-supervision/normal-missing-ranges.json).

## Data preservation

A private recovery audit scanned the first small group into
`cache/scan-supervision/normal-gap-audit/observations.db`. It saved four records
matching existing collection records, then stopped at four of the five counted
entries. No audit rows were imported into production. The proposed merge helper
was held before any helper files or merge operation were created.

The production prefix remains 2,887 rows, ending at ID 2,887. Its all-field
SHA256 checkpoint is recorded in
[normal-end-prefix.json](../../cache/scan-supervision/normal-end-prefix.json):

`be82bffa0852237171739a71fb6f5d7b00db1cbf19be0bd1ac631034a02d41ca`

Fusion donors must remain separately evidenced inaccessible entries, with
numeric appraisal HP and IVs unavailable. They must not become fabricated
Pokémon rows, review skips that never occurred, or additional visited positions
in the failed session.
Historical traversal offsets remain unchanged; no original storage ordinals
are inferred or shifted to fill the three-count difference.

## Completion boundary

With all three donors directly confirmed, the manual coverage reconciliation
is **2,887 saved + eight review skips + three inaccessible fusion donors =
2,898 counted entries**. This reconciles the observed inventory count; it does
not convert the final 785-of-788 machine run
into a successful scan or make unavailable HP/IV data captured.

The supervisor sealed the Normal ledger with
`completion_kind=initial_coverage_reconciled_with_unresolved_entries` and
`fully_captured=false`. Its actual traversal remains 2,895; the final session
retains its unmet target. The three donors have no appraisal positions or
database rows.

The exact `cp0-` search was verified in the native editor. Two separately
captured storage headers show **3,246 / 3,250**, leaving **348** entries outside
Normal. These were visual reads: the automated count reader returned unknown
because OCR dropped the slash. The images are
[first header](../../cache/scan-supervision/inventory-normal.png) and
[second header](../../cache/scan-supervision/inventory-second-header.png).

At 00:02:45 SAST on September 10, the stopped manager was restarted with the
tested fifteen-partition queue. The first query,
`shiny&!shadow&!dynamax&!gigantamax`, independently confirmed 62 entries and
started session `05265acb-1a14-475a-bc11-8ab1c1ac3d10`. It recovered Dragonite
CP2674 through model animation, rechecked the appraisal, and continued.
The original 2,887-row digest remains unchanged. The Mac subsequently locked;
active progress is established by the scan log and committed database batches,
without external phone input. Category tags are applied when each pass exits;
running rows may still have default flags until then.

See [queue launch](../../cache/scan-supervision/category-queue-launch.json),
[coverage ledger](../../cache/scan-supervision/state.json), and
`logs/scan_20260910_000249.log` for subsequent progress. Starting this queue is
not completion of the remaining categories. Fusion accounting changes no
scanner behavior and does not turn unavailable IVs into successful captures.

## Count OCR repair for the next restart

Both saved full-inventory headers reproduce the primary OCR output
`32463250`: the inverted crop loses the slash. A bounded fallback now retries
the same crop using normal polarity at threshold 160. Both saved images then
read `3246/3250`. This is image replay, not a new live count observation.

The fallback requires a complete parenthesized or owned/capacity header.
Independent review caught that accepting a bare fallback `3250` could mistake
the capacity for the owned count; that path and bare `3246` are now rejected.
Primary bare-positive compatibility remains unchanged. The two independent
captures, storage checks, chronology/source checks, explicit-zero rules, and
cancellation boundaries remain required. No slash or missing digits are
inferred.

Full verification: **1,008 tests and 1,828 subtests passed**, with three optional
skips and the existing Requests dependency warning. The bounded independent
review is clear after the capacity-only finding was fixed; `git diff --check`
passes. The running category queue was not interrupted and uses the code loaded
at its start. The fallback becomes active after a later restart. This work is
local and uncommitted; no publication or full-inventory completion is claimed.

At 00:08:14 SAST, category-01 reached its exact target: **62 stored, 62 visited,
zero skipped**. All 62 rows have `shiny=1`, `shadow=0`, and `is_dynamax=0`, with
positions 0–61. Storage was confirmed before category-02 began at 00:08:18.
The database contained 2,949 saved records and its original 2,887-row digest
was still unchanged. The recorded initial category union is 348; after this
first pass, 286 category entries remain to account for.
