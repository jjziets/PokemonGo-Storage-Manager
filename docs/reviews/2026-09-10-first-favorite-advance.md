# First favorite advance recovery

The owner reported a stopped real keeper pass after Meditite CP370, HP69,
IV10/12/15 was starred. The next acquisition observed the same complete tuple
and four agreeing specimen measurements. The reverse/restore recovery requires
a distinct earlier checkpoint, which does not exist after the first item.
The recorded favorite succeeded; the failure concerns navigation.

Authority is the owner's request to fix this failed favorite action, under
unchanged REQ-MASS-001, REQ-SCAN-003 and REQ-SCAN-004, baseline
REQ-BASELINE-2026-09-09-001 with semantic SHA256
`4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`.
This is bounded local implementation and verification. Packaged TraceWeaver
runtime/gate closure and publication are not claimed.

## Correction and boundaries

Use a right-arrow target observed in the already checked appraisal frame for
ordinary forward navigation, with a calibrated swipe when the detector is
uncertain. Select exactly one gesture; do not send a speculative tap followed
by a swipe. Fixed legacy arrow coordinates are unsuitable because the appraisal
panel moves with the professor text and layout. Keep all post-input source,
identity, specimen and occurrence checks.

For an unconfirmed keeper transition without a distinct prior checkpoint,
allow one full traversal restart using the same verified query and count.
Re-evaluate occurrences from the first grid item; do not assume that a tuple
or unchanged count identifies the same individual or sorting position.
Preserve actual successful favorite changes, reset traversal allowances only
after the query/count is verified, and bound cumulative changes per signature.
Already-starred replays must not receive another toggle. Dry-run counts reset
because they describe hypothetical changes in the current traversal.

The restart resets traversal only after the original count is independently
confirmed. Review caught that the older count reader accepted one loose OCR
number; both initial keeper opening and restart now use the existing strict
two-observation count reader. An uncertain count holds before opening a tile;
an explicit verified zero is handled as empty. Category actions retain their
existing count path.

## Verification

332 targeted tests passed across new arrow and restart cases, keeper batching,
mass actions, multi-pass, transition/specimen guards, verified count/search,
pause/abort, resource cleanup and both GUI action routes. Independent review
closed the count-verification finding and found no remaining actionable issue
within this change. Tests cover detector rejection, a single input, pause/abort
before and after input, changed/reordered occurrences, already-starred replay,
cumulative per-signature limits, partial failures and the one-restart cap.

Live checks on the paired phone stream:

- Read the original Meditite CP370/HP69/IV10/12/15 as already favorited.
- Two observed-arrow advances reached Tandemaus CP370/IV14/13/11, then Shieldon
  CP370/IV14/12/12, with exact acquisition and no star input.
- A dry keeper pass independently verified the original 188-result filter and
  traversed 20 positions without errors or restarts. Meditite appeared fifth
  in that later traversal and its existing star was left alone. This confirms
  why recovery must not assume identical tie ordering after reopening.
- The dry run identified five potential new favorites and stopped deliberately
  after position 20. Its `aborted: true` is the diagnostic's explicit limit,
  not a failure. All production collection rows matched the read-only baseline.
- Elapsed time was 89.95 seconds including full 499-character filter entry and
  navigation. Positions 2–20 took 47.21 seconds, including a roughly 12-second
  Nidoran acquisition. This is not a complete favoriting throughput guarantee.

No diagnostic changed stars or stored Pokémon. The bounded restart itself is
covered by regression tests; it was not needed during the 20-position live run.
The 31 preexisting ambiguous keeper records remain separately held for review.
Private evidence and exact pre-turn source copies are under
`cache/favorite-advance-fix/`. No publication was performed.

The updated app was reopened through the standard paired-stream launcher.
Native UI verification confirmed the phone connected through the app-only
stream, with status Ready and no scan running.
