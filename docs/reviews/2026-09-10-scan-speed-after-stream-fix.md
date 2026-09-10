# Scan speed after the stream scheduling fix

Authority: the owner's September10 request to improve multi-scan and favorite
scanning speed. Requirements and baseline remain unchanged:
REQ-SCAN-002/003, REQ-MASS-001 and REQ-STREAM-001 under
REQ-BASELINE-2026-09-09-001, semantic SHA256
`4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`.

This is bounded local implementation and verification. The packaged TraceWeaver
workflow runtime is unavailable; packaged authority/profile gates, release
acceptance and publication are not claimed. Direct owner authorization covers
the work. Existing baseline, stored inventory and decision rules are preserved.

## Bounded work

- Reuse a retained independent settled pair for exact visible-CP confirmation
  when an animation was not observed. Fall back to the existing fresh capture
  on any incomplete, conflicting or invalid pair evidence.
- Start star readback immediately on a paired stream; preserve the existing
  delayed attempts if that first observation is too early. Keep one tap,
  complete identity checking and legacy capture timing.
- Reduce repeated pixel processing without changing any color, geometry or
  settling thresholds.

CP recovery, tuple matching, occurrence counts, display binding, source
freshness, pause/abort and pre-input checks must retain their existing meaning.
Do not introduce a negative keeper prefilter in this change: its interaction
with the following position's exact CP checkpoint needs separate treatment.

## Verification and validation

Focused regression scope: settled-pair reuse, stream appraisal settling,
specimen transitions, mass-action readback, pause/abort, keeper occurrences,
bar detection and stability. Independent review covers only this turn's delta.

Validation question: does the bounded app-stream path perform less redundant
work while returning the same complete Pokémon identities and preserving all
input guards? Compare paired live runs on the same verified query and ordered
Pokémon. Favorite measurements are dry runs; no production database or stars
are modified. Navigation/setup and recovery costs must be distinguished from
steady scanning. Synthetic timing is not a live throughput claim.

## Results

270 targeted tests passed, including the shared scanner, multi-pass routing,
keeper actions, timing controls, source freshness, pause/abort and pixel tests.
An older resource-cleanup fixture was completed with the CP and category flags
now required by keeper planning; its cleanup assertions are unchanged.
Independent review found no actionable issues and matched old/new bar results
on 120 randomized/synthetic images across six resolutions.

Alternating local replay of a 968×2376 appraisal (150 measured calls per
implementation after warmup) gave median bar detection 3.773→1.050 ms and
region differences 3.455→1.873 ms. These are CPU measurements, not a GPU or
end-to-end throughput claim.

The paired app stream used the existing 300 ms calibrated swipe and 150–250 ms
source observation interval. Before/after runs used the same verified filter
and the same ordered five Pokémon: Durant2099, Komala2098, Magmar2098,
Hitmonlee2095 and Durant2095. Species, CP, HP and IVs agreed in both modes.

| Five-position measurement | Before | After |
| --- | ---: | ---: |
| Shared scan acquisition and four forward advances | 8.272 s | 7.559 s |
| Keeper acquisition, dry-run star guards and four advances | 9.315 s | 9.817 s |

The scan sample took 8.6% less time. Favorite dry-run timing did not demonstrate
a throughput gain: display-validation time alone varied from 3.036 to3.448 s.
The immediate star-readback improvement does not run in dry mode; regression
tests demonstrate removal of the initial100 ms delay when the first fresh
post-input observation confirms the star, retaining all delayed attempts.
The visible-CP pair shortcut is conditional and was covered by regression
tests; no calls to that new helper occurred in these five-position runs.

These short measurements exclude search/navigation setup, database writes and
actual star inputs, and must not be extrapolated to complete-storage speed.
An earlier eight-position diagnostic held after adjacent identical Lunatone
because no distinct previous checkpoint was available for a failed-swipe retry.
That preexisting reliability limit remains; faster pixels do not waive it.

Private evidence: `cache/scan-speed-post-jitter/`, including exact pre-change
source copies, before/after rows and timing spans, replay measurements and the
held longer diagnostic. Timing phase totals are nested and must not be added.
No production collection rows or favorite stars were changed by validation.
The full `cp0-` storage search was restored after testing. The updated paired
manager is open and idle with the physical panel dark; the GUI shows
"Not connected" and awaits its Connect button. No scan was started.

Trace anchor authoring was attempted through the installed helper for the new
pixel test; it reported `trace_absent_from_matrix` against the existing matrix
format and made no changes. Anchor/matrix gate closure is therefore not claimed;
this advisory tooling limitation does not change the executable test results.

## Next measured opportunity

Native OCR averaged about20–30 ms per request in the bounded phone runs;
display validation averaged about100 ms per call and a calibrated swipe about
500 ms including its input check. The next useful experiment is a verified
appraisal-arrow tap, preserving swipe fallback and all subsequent transition
checks. Offline arrow feasibility is not a production implementation or live
reliability result. The old fixed close-appraisal coordinate is unsuitable.
