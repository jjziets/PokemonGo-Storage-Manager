# Grouped PvP cleanup traversal

The later owner-authorized [uniform-group follow-up](2026-09-13-pvp-cleanup-uniform-groups.md)
supersedes this first implementation's exact-form-only inventory requirement and
blanket duplicate hold. It retains the verified two-sweep traversal.

## Scope and authority

The owner reported repeated search entry during selective PvP cleanup and
explicitly requested setting criteria once, swiping between results and
unfavoriting the Pokémon marked for cleanup. Local authority decision: Proceed
under REQ-MASS-001, REQ-SCAN-004 and REQ-DATA-001, baseline
REQ-BASELINE-2026-09-09-001, semantic SHA256
`4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`.
This changes traversal, not the selection policy: all KEEP and 3–4★ records
remain protected. The running app and device must remain undisturbed while
source changes and offline verification are prepared.

## Observed cause

`logs/macos_launcher.log` records three search applications for each normal
candidate. For Slaking CP3056 HP199, the first search began at 07:36:13 and the
third count completed at 07:36:48; appraisal was read at 07:36:55. Slowking and
Snorlax repeat the same pattern. Each query took about 11–12 seconds to enter
and verify. The first query proved a live singleton, the second narrowed to
the selected category, and the third reopened it for action. They were
equivalent queries for these ordinary candidates, with some terms reordered.

## Replacement behavior

The cleanup uses bounded CP/flag groups and the shared appraisal traversal.
It applies a group's filter and reads every result to establish exact identities
and duplicate counts. This verification sweep makes no star changes and cannot
stop early just because every target has been encountered. A later duplicate
or unreadable card could otherwise invalidate an earlier match.

The action sweep reopens the identical filter, checks the result count and
compares fresh reads with the verified order before changing any star. It
unfavorites only reviewed unique matches; current database protections, exact
appraisal checks, one-toggle readback and atomic OFF persistence still apply.
Dry run performs verification only. The UI distinguishes verification from
unfavoriting, with separate verified-card and action totals.

Filters exclude 3–4★ and establish exact flags and candidate CP sets. Neither
`favorite` nor `!favorite` is permitted: changing a star must not remove a card
from the carousel. Dynamax and Gigantamax inventories must both be checked
before their shared stored identity can authorize a match. Incomplete reads,
ambiguous position, pause or incompatible capture-source changes invalidate
the group's proof. The existing automatic pass restart is disabled for these
strict sweeps; confirmed partial outcomes remain reported.

## Limits and verification

An unchanged count alone does not prove unchanged membership. This design
requires an uninterrupted run with exclusive device control, fresh ordered
identity checks and rejection of detected changes. It cannot prove that no
external same-count inventory substitution happened later in the carousel.
Only compact identities and source receipts are retained, not every screenshot.

Offline verification completed in an isolated source copy with app-stream
environment variables removed, offscreen Qt and an unavailable ADB path:

- Full suite: **1,503 tests and 11,120 subtests passed; 17 skipped** for existing
  private-capture or native-build prerequisites. The existing requests dependency
  version warning remains.
- The 180-candidate spaced-CP regression creates four groups: **8 search entries
  for a real run, 4 for dry run**, versus 540 with the former three-per-candidate
  implementation. Query lengths are 494, 494, 494 and 174 characters.
- Regressions cover complete inventory, late duplicates, incomplete identities,
  ordered action mismatches, Dynamax/Gigantamax scope, abort/pause, unknown star
  state, unchanged query/count, and preserving confirmed partial changes.
- Review found and repaired a capture-source gap at post-star readback. Cleanup
  now checks continuity on pre-star, post-star and pre-swipe captures; source
  changes hold without another tap or an unsupported database update.
- GUI checks distinguish verification visits from action visits, keep dry-run
  counts hypothetical and preserve existing favorite progress behavior.
- Independent review closed without unresolved findings. A final positive
  Gigantamax regression confirms that canonical and narrower selected
  inventories do not double-count one specimen or broaden the action query;
  the final batch suite passes **14 tests and 11 subtests**. This extra test was
  added after the full-suite run; no runtime code changed afterward.
- Changed-file trace anchors report zero findings; `git diff --check` passes.

Full-suite log:
`/var/folders/2f/ntb_0p9558v4wfcwr5_64dg00000gn/T/pokemgr-cleanup-batches-1xz__949/full-pytest.log`.
The isolated-copy path is also recorded in `/tmp/pokemgr-cleanup-batches-path.txt`.
The final trace report is `/tmp/pokemgr-cleanup-batch-traces.md`.

No live throughput improvement, full-storage reliability, deployment or
publication is claimed. The active app, device and production database were
left untouched; these source changes require a later agreed restart.
