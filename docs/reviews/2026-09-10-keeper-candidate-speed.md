# Pending keeper candidate searches

<!-- TRACEWEAVER: file-role=keeper-candidate-speed-review; req=REQ-MASS-001,REQ-SCAN-003; trace=TRACE-MASS-001,TRACE-SCAN-003; ver=VER-SCAN-001 -->

Authority: the owner's request to speed up favoriting, avoid work on existing
favorites, and use HP/IV evidence before optional CP recovery. This is bounded
implementation under REQ-MASS-001, baseline REQ-BASELINE-2026-09-09-001,
semantic SHA256 `4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`.
Packaged TraceWeaver gates and publication are not claimed.

## Change

The executor now limits each flag pass to CP values required by eligible
pending keeper signatures. Disjoint groups fit within 500 characters, below
the native full-search observer's 512-character ceiling. Every occurrence
with the same CP belongs to one group. Each group receives its own occurrence
counter and finishes as soon as that counter is exhausted. Completed matches
are reconciled into the overall allowance even when a later position fails;
unvisited groups remain unmatched on error or abort.

All terms are joined with AND: an enclosing CP range excludes any gaps using
negated CP ranges. This avoids an unverified precedence assumption between
comma searches and category constraints. The query language's CP ranges,
exclusions and AND operators are documented in the
[official inventory search help](https://niantic.helpshift.com/hc/en/6-pokemon-go/faq/1486-searching-filtering-your-pokemon-inventory/).
Search narrowing never supplies CP or replaces exact live identity evidence.

The query does not depend on favorite state, so adding a star cannot change
its membership. Already-starred-only signatures contribute no target CPs.
A starred neighbor sharing a pending CP can still appear; counted identical
keeper occurrences and actual star checks are preserved. Literal `!favorite`
would need a separate navigation policy to handle membership changes.

The scanner already resolves unique HP/IV evidence before visible CP and
optional model/preview recovery. That order is unchanged. Numeric size and
caught-date observations are not persisted in current collection rows;
using those measurements as historical keeper keys is not justified by the
stored data. The existing movement proof and full transition checkpoints
remain unchanged.

## Read-only inventory estimate

The database has 3,455 rows: 1,302 KEEP, 951 recorded starred, 351 unstarred,
31 held and 320 eligible. Excluding the 214 later repeat observations, the
original inventory predicts these candidate counts:

| Pending flag group | Broad candidates | Targeted candidates |
| --- | ---: | ---: |
| Normal, non-lucky | 2,868 | 737 |
| Normal, lucky | 27 | 2 |
| Shiny, non-Dynamax | 61 | 5 |
| Shiny, Dynamax | 4 | 1 |
| Shadow | 66 | 13 |
| Total | 3,026 | 758 |

This is about 75% fewer potential visits, not a measured throughput gain.
Separate search navigation adds overhead. The model combines Dynamax and
Gigantamax in one stored flag, counted once in this estimate; execution keeps
their searches separate. Current phone inventory can differ from saved rows.

## Verification and limits

Query tests cover exact coverage, disjoint groups, deterministic ordering,
same-CP ownership, all four flags, complete length bounds and malformed CPs.
Executor tests cover starred identical neighbors, selected and unselected
flags, partial errors, aborts and batch-local exhaustion. Independent review
of the helper and integration is bounded to this change; accumulated unrelated
workspace changes are outside this review.

Live search verification found that one 499-character ADB text injection
arrived as a 229-character prefix. The full-readback guard held before ENTER.
A seven-character append succeeded, ruling out a hard 229-character limit.
Verified searches longer than 80 characters now enter bounded chunks, checking
storage and the complete prefix before and after each append. Short searches
retain their fast path. Any mismatch, cancellation or changed pause generation
prevents further typing and ENTER; the final whole-query check remains.
Executor navigation now observes pause generations during this work too.

The repaired 499-character filter passed exact native readback and returned
188 live candidates in 36.8 seconds, including navigation/count verification.
A smaller exact-set filter returned 19, equal to individual CP10/25/29 counts
of 15/2/2. The normal base filter was restored and returned 2,872. Evidence is
private under `cache/keeper-speed-proof/`. These checks only navigated storage
and edited search text; no stars, Pokemon stats or database rows were changed.

236 focused tests passed across keeper batching, action/scanner guards, both
GUI routes, verified search entry, stream capture, controller, frame buffer,
clock and launcher checks. Independent review cleared the final batching,
long-search and stream changes. Full favoriting throughput and completion
remain unverified. No publication was performed.

The idle paired app was restarted after verification. Process creation times
postdate the changed Python sources, and the current producer command confirms
the 50ms preview buffer. The GUI is ready for Connect; no scan or favorite run
was started automatically. Transient process/display IDs are intentionally
not an operational resume instruction.
