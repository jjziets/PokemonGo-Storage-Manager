# First inventory scan supervision

The owner requested a check every thirty minutes through the first complete
approximately 3,500-Pokemon inventory scan. The active thread heartbeat is
`supervise-pok-mon-inventory-scan`; its local checkpoint and coverage ledger are
`cache/scan-supervision/state.json`. Completion requires actual pass coverage,
not merely a row count. Skips remain separately reported unresolved entries.

## Immediate raid-defender failure

The first check found session `e2abb877-cd37-43fb-91dc-b99fee168e0d` stopped after
136 stored, 141 visited and five verified skips, leaving 1,202 saved records.
The next Pikachu (IV9/12/9) was defending a gym during a raid. Its screen replaced
GO TO GYM with the full raid status sentence. The button-only detector returned
false, HP OCR returned -2, and review favorite handling rejected missing HP.

Native fields now recognize the complete raid sentence in the measured status
region on the same frame. The two observed lines have fast-Vision confidence
0.5; exact wording, whole bounds and coherent line geometry are required.
Partial, conflicting, misplaced or lower-confidence text cannot set the flag.
The acquisition path short-circuits button detection and HP reads only for an
explicit native true. The existing gym policy rejects motivation CP and uses
repeated appraisal identity, static pixels and star readback to verify a skip.
No additional OCR request or broad missing-HP exception was added.

Authority: advisory baseline REQ-BASELINE-2026-09-09-001, hash
`4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`,
REQ-SCAN-001/004 and the owner's explicit supervision/favorite-and-skip requests.
The local authority decision is Proceed for this bounded screen variant.
No publication or formal packaged TraceWeaver acceptance is claimed.

The full suite passed **890 tests and 978 subtests**, three optional skips, in
22.00 seconds, with one pre-existing Requests dependency warning. Independent
review found no actionable issue; changed-file trace checks passed with zero
findings and diff checks were clean. A live diagnostic with all phone input
forbidden confirmed Pikachu9/12/9, recognized the gym state and preserved the
existing gold star with no database writes. Private evidence:
`cache/resume-903/live-raid-gym-review-proof.json`.

## Remaining pass coverage

The current Normal scan excludes shiny, shadow, dynamax and gigantamax. Its
original observed count was 2,898. Current cumulative visited positions are
1,207, leaving 1,691, including the unsaved raid defender. Scan from Here does
not automatically run later passes.

The stock GUI's remaining category filters overlap, while the database only
deduplicates within one session and position. Custom GUI passes also omit the
required category tags. The ledger therefore lists fifteen nonzero Boolean
partitions over those four predicates, each with explicit positive/negative
AND filters and corresponding shiny/shadow/is_dynamax tags. They are disjoint
and cover exactly the complement of Normal regardless of whether the game's
Dynamax/Gigantamax predicates overlap. Lucky stays outside this partition plan
because ordinary Lucky was already included in Normal.

Before executing those remaining partitions, the supervisor must support the
explicit tagged queue and positively distinguish empty results from unreadable
counts. The existing multipass code proceeds when count is zero and can abort
on an empty partition. Reset skip, max and resume fields for the new queue;
verify full applied search text and exact counts. A session completion timestamp
alone does not prove success because it is also written on failure. Preserve
session/position coverage and never replay a completed prefix into a new session.

## Verified continuation

After a clean restart, read-only verification again confirmed the unsaved
Pikachu9/12/9 and its existing favorite. Session
`d896f984-69df-40d8-8871-517dd089f4a6` resumed at Normal offset1,207 with target
1,691, skip zero and unfavorite disabled. The raid defender was skipped once,
then Sinistea638 and Shellos637 were stored. Twenty new rows committed, bringing
the database to1,222; all original1,202 complete rows matched their pre-restart
SHA256 digest. Two verified skips in the new segment remain separately tracked.
Evidence is `cache/scan-supervision/raid-resume-verified.json`. The scanner was
still running at this observation; this does not establish whole-inventory
completion. The heartbeat and ledger retain the remaining work.

## Tagged continuation queue

After Normal completes, the explicit queue launch reads the supervision ledger
and uses the existing GUI scan worker. All fifteen non-Normal Boolean masks
must remain present, each exactly once; completed partitions are retained and
excluded from the pending queue. Partial/running sessions, missing masks,
conflicting tags, duplicate query literals and an empty pending queue hold the
launch before device connection. Tags come from the exact query predicates,
with Dynamax or Gigantamax mapping to the existing `is_dynamax` field. The loader
does not mutate the ledger or infer completion from a session timestamp.

The queue clears skip, cap and resume targets and keeps unfavorite disabled.
It uses the normal visible Pause/Stop controls. CLI default scanning and queue
launch are mutually exclusive. The app-only runner forwards the ledger path
as one argument, and the GUI revalidates it after connection.

Every multipass filter now requires complete native search-editor readback.
The strict count reader checks storage on each fresh capture and requires two
independent agreeing observations. Explicit zero skips a partition without
opening a Pokemon; unreadable or conflicting counts hold the queue. A missed
first-card recovery must reverify the same query and count. A pass cannot claim
completion without reaching its exact verified position target, including
separately counted verified skips. Partial stored rows retain their pass tags.

Independent review found a Stop handoff gap between final appraisal detection
and state-machine construction, plus cancellation misreported as a navigation
failure. Both boundaries now recheck cancellation, close an unstarted reader
when necessary, and avoid success callbacks after Stop. Mock regressions cover
these races without phone input. This is local implementation under the
existing REQ-SCAN-003/REQ-DATA-001 baseline and explicit full-inventory supervision
authority. Live category execution is held until Normal completes and its
ledger is reconciled; the active scan is not restarted to load this queue work.

Review also found that Pause did not cover between-pass navigation and Stop
could be lost during scanner construction. Worker and scanner controls now
latch across both construction handoffs. Navigation waits on the scan thread
while GUI controls remain responsive; Stop releases that wait. A pause epoch
invalidates any storage-screen classification captured before or during the
pause, requiring fresh detection before branch input. Independent replay
confirmed both the original gaps and their fixes.

Final local suite: **1000 tests and 1812 subtests passed**, three optional skips,
one existing Requests dependency warning. Local implementation anchors pass
with zero findings and diff checks pass. A pure loader check holds the actual
still-running Normal ledger; a temporary completed-Normal simulation validates
its fifteen pending partitions without modifying the real ledger or touching
the phone. No live category execution, packaged acceptance or publication is
claimed. The active Nidoran recovery has passed over400 further Pokemon at
about27/min while these queue changes were tested.
