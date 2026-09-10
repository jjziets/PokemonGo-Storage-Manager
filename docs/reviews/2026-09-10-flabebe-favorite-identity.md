# Flabébé pre-star identity recovery

The owner reported that the real favorite action stopped with 191 checked,
53 newly favorited and 297 unmatched records. The final visible progress rows
were Magnemite and Torchic. The retained launcher output identifies the next
position as Flabébé, HP84 and IVs13/14/15, followed by the immediate
`appraisal identity changed before star input` exception.

Authority: REQ-MASS-001 and REQ-SCAN-004 under
REQ-BASELINE-2026-09-09-001, semantic SHA256
`4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`.
This local fix is explicitly requested. The baseline is unchanged and packaged
TraceWeaver gate closure/publication is not claimed.

## Findings

- The existing pre-star guard raised on the first disagreement. Its loop only
  retried an unreadable star, so one name OCR variation or unsettled frame could
  stop the pass. It did not save the disagreeing pair or log differing fields.
- A reproducible comparison bug exists for default labels such as Flabébé:
  GameMaster validates the canonical species as `Flabebe`, while the reader's
  caught authority remains `Flabébé`. Accent variations in the raw displayed
  name trigger a fallback that compares raw and resolved species and may reject
  the same Pokémon. The existing corrected-species log hides raw-name differences.
- Twelve read-only frames of the stopped appraisal consistently returned raw
  display `Flabebé`, caught `Flabébé`, HP84, IVs13/14/15 and unique CP595. The star
  was OFF, and all consecutive identity ROIs were below 0.41 mean difference.
  These later frames do not reconstruct the original failed pair; its exact
  differing field remains unproven. The observed label pattern matches the
  independently reproduced defect.

## Changes

- Compare accent/case normalization only for default species labels whose two
  displayed and detected names all equal the same exact caught authority after
  normalization. Keep exact caught text, numerical stats, flags and pixel guards.
  Never modify the validated keeper species, form, CP or database lookup key.
- Retain the first matching-read fast path. A mismatch permits at most three
  fresh captures in total, 100 ms apart, and requires two subsequent matching
  observations before any input. Each retry edge requires finite, nonoverlapping
  capture intervals and compatible ordered source evidence. Copied/reused frames,
  wrong clock continuity and moving IV bars cannot supply the pair.
- Keep the original accepted snapshot authoritative and use the newest star
  observation. Preserve the visible-CP contradiction check and one-tap post-input
  readback. Pause discards the retry evidence; abort prevents further input.
- Log raw field differences, freshness and identity-region differences. On
  exhaustion retain original, newest and differing frames when distinct. An
  unreadable star is reported separately from an identity mismatch.

## Verification

New regression suites: `tests/test_action_prestar_retry.py` and
`tests/test_action_name_identity.py`. Existing pre-star fixtures now provide
three distinct observations for persistent mismatch/unknown-star scenarios.
The final combined run passed 430 targeted tests in 3.35 seconds;
`git diff --check` passed.

Five live, input-disabled dry checks accepted the current Flabébé through the
complete pre-star path. Each preserved the OFF star. No diagnostic used a
production database, sent a phone input or changed any favorite. The earlier
53 successful stars are not inferred into database rows from progress text;
subsequent confirmed live observations can repair their previously stale flags.

Independent review found a legacy-copy freshness hole and a misleading final
unknown-star diagnostic. Both were fixed and independently verified; no actionable
findings remained. Private evidence is under `cache/favorite-identity-fix/` and
includes a truncated retained launcher output, current-frame inspection and dry
verification receipts. No complete real favorite pass was run for this fix.

The preceding database synchronization fix remains included. Reload was awaiting
manual Mac unlock when the UI tool reported the Mac locked; no lock bypass or
blind process termination was attempted.
