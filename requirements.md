---
id: REQ-BASELINE-2026-09-09-001
status: stakeholder_authorized_scope
traceweaver_mode: advisory
---

# Storage manager requirements

This baseline records the owner's explicit requests in the current Codex conversation on September8–9,2026. It supersedes the bounded August24 scan-only baseline. In particular, the later requests authorize verified favorite-for-review fallback, same-stat occurrence handling and favorite/unfavorite tools; the earlier no-star-mutation restriction does not govern those requested features.

Baseline semantic SHA256: `4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`. Hash basis: canonical sorted-key compact JSON of baseline_id and ordered (requirementID, statement) pairs in the intent contract. Historical source records remain locally under `.traceweaver/archive/2026-08-24/` and are not included in publication.

## Direct stakeholder authority

- Scan order: “iv and hp on the apprise page”; if ambiguous, OCR then animation recovery; if still unresolved, “favourt it and mvoe on”.
- Stream and controls: use the app stream, physical screen darkening with a UI screen-on button, and a30-frame OCR window.
- Operations: apply improved scanning to Mass Actions and Decisions; add process CPU/GPU usage, showing unavailable readings honestly.
- Keep selection: retain highest CP alongside best IV/PvP, and “We should also favor all perfect IVs”.
- Publication: “commit/pr and merge”. Target resolved from the repository's sole/default branch: `dev` at `jjziets/PokemonGo-Storage-Manager`; implementation branch `codex/stream-scanning-and-keepers`.

## Requirements

| ID | Authorized behavior | Verification |
| --- | --- | --- |
| REQ-SCAN-001 | Preserve CP only when complete species/form, HP and IV evidence validates it exactly. | Exact CP, resolver and snapshot tests. |
| REQ-SCAN-002 | Read appraisal HP and IVs first; accept a unique calculated CP with independent confirmation, otherwise try OCR and optional bounded rotated-model animation or canceled preview recovery. | HP-first, CP window, model, preview and settled-pair tests. |
| REQ-SCAN-003 | Count verified storage positions independently, preserving same-stat adjacent specimens when transition evidence proves advancement; reject stale or unobserved transitions. | Stable-loop, transition, database-position and stream freshness tests. |
| REQ-SCAN-004 | Never transfer Pokemon or spend resources. Only the requested favorite/unfavorite actions or verified favorite-for-review fallback may change stars; preserve identity checks and one-tap readback. | Favorite fallback, star state and action mutation spies. |
| REQ-STREAM-001 | Provide an app-only stream with physical-screen controls, a bounded 30-frame native buffer and fresh display-bound frames; share frame OCR and report process CPU/RSS honestly, with unavailable GPU measurement labeled. | Stream lifecycle, native wire, OCR and process-monitor tests. |
| REQ-MASS-001 | Use shared validated acquisition in Mass Actions and Decisions phone actions; verify full search text and exact keeper occurrences, and expose pause, stop, partial and unresolved results. | Search-text, mass-action, worker-cleanup and Decisions routing tests. |
| REQ-IDENTITY-001 | Use caught species rather than editable nicknames; use candy only as a separate evolution-family constraint, never as the active evolved species. | Candy-family, name, exact-form and keeper matching tests. |
| REQ-DECISION-001 | Keep selected best-IV, highest-current-CP and PvP picks per stored species/form, plus every exact15/15/15 occurrence by default. Respect unchecked rules and league CP limits; retain last-of-species protection. | Decision identity, highest-CP, perfect-IV and widget tests. |
| REQ-DATA-001 | Preserve one record per scan-session position, validated calibration geometry and cooperative resource cleanup; do not clear user data during publication. | Database, calibration and stream cleanup tests. |

## Validation and limits

The procedure and operator controls are documented in `docs/runbooks/phone-scan.md`. `docs/reviews/2026-09-09-publication.md` records local verification and bounded live evidence. Passing unit tests does not establish unattended full-storage reliability, full device compatibility or comprehensive transfer advice.

Known debt retained in this publication: rebuild/version cached PvP rankings; evaluate evolution potential and broader keeper preferences; preserve manual choices across reruns. The existing cache is local and is not published. TRANSFER remains a rule-based suggestion requiring review. No automatic transfer or spending is implemented or authorized.

Verification includes regression tests written both before and after their corresponding fixes; no blanket test-first claim is made. Publication is based on the reviewed resulting behavior and passing reproducible checks, under the owner's explicit current instruction. No new phone run, data clear or account action is part of publication.
