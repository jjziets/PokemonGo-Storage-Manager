# Stream scanner and keeper decisions — publication review

Scope: accumulated work since the initial `dev` commit `bf3c8d8`, authorized by the owner's current requests and “commit/pr and merge”. The current advisory baseline is REQ-BASELINE-2026-09-09-001. This review does not claim deployment or a restarted GUI process.

## Behavior

- Appraisal HP/IV-first exact CP resolution, shared native frame OCR and bounded obscured-CP recovery.
- App-only stream, source/display freshness checks, physical-screen controls, native RGB ring, timing and process CPU/RSS reporting.
- Verified storage occurrences, nickname-independent species, candy-family constraints and recoverable favorite-for-review fallback.
- Shared Mass Actions/Decisions acquisition, full native query verification, exact keeper counts, one-toggle readback and cooperative Pause/Stop.
- Exact PvP form IDs and CP eligibility; independent Highest CP keeper and every perfect15/15/15 occurrence retained by default.
- Clear Database refuses while any device worker runs, including a worker starting while its confirmation dialog is open.

## Review

PUB-REVIEW-SCAN-20260909: independent read-only scanner/native boundary audit reused prior accepted reviews and found no new P0/P1 correctness issues. Source, patch, build helper and native input hashes matched local builds. Packaging findings were repaired: repository-relative launcher, neutral configurable optional profile path, private live captures excluded.

PUB-REVIEW-ACTIONS-20260909: independent GUI/action/decision audit found the Clear Database ownership gap. Added before/after-confirmation guards and four action-spy regressions;18 focused cleanup tests pass. The test package marker and Python3.11+ requirement address clean-checkout portability. The reviewer found the updated advisory requirements/trace/intent mapping faithful to explicit owner requests, with a matching semantic hash and existing implementation/test links.

PUB-REVIEW-KEEPERS-20260909: scoped reviews of highest-CP and all-perfect-IV rules passed. Exact same-session keeper occurrences are counted separately; exact-form and cross-session uncertainty remains held.

## Verification

VER-PUB-20260909 records final portable and native command outcomes below. TRACE-PUB-20260909 is the current advisory requirements/matrix/intent mapping reviewed above. The initial fresh-copy run exposed an unmocked GameMaster network dependency; the tests now use a pinned public species snapshot instead of mutable network data. Local account screenshots, databases, logs, calibration files and compiled binaries are excluded from the PR.

- Full local pytest: **767 passed,3 skipped,585 subtests passed** in18.14s. The skips are optional local profile/capture checks.
- Clean-copy pytest with no runtime caches/private captures/native build outputs: **754 passed,16 skipped,578 subtests passed** in13.74s. Skips cover optional image and native-library checks; these are separately exercised locally where available.
- Focused offline catalog verification:82 tests and59 subtests pass with zero HTTP attempts and no runtime cache creation. All1710 public parsed species entries are pinned with source hashes in `tests/fixtures/README.md`.
- The local Python environment emits one Requests dependency-version warning; test outcomes above pass.
- Project-local scrcpy build: passed and input hashes match the packaged source.
- Native C bridge: passed wire metadata,0600/O_EXCL, unmodified timestamps, Python monotonic-clock compatibility, pixel equality,30-frame wrap, close/error states, YUV conversion and concurrent cross-process copying.
- Swift/Vision smoke: a generated synthetic frame containing CP1234 returned that text with the requested frame identity; no phone input.
- Android clock/search helpers: both compiled successfully with Java17, Android36 and D8; no helper was executed on the phone during publication.
- Shell syntax and git diff whitespace checks: passed.

## Bounded prior live evidence

The earlier M4Pro/Fold6 test stored10 mixed Pokemon with0skips in22.565s, including separate same-stat Lunatone occurrences. This is a small-sample rate, not a full-storage guarantee. Both Dragonite CP4287/2624 and hidden-CP Zygarde were verified in earlier bounded work. Exact full search text and Exeggcute candy-family extraction were checked. Local raw evidence remains in ignored cache/log directories and is not published as private account data.

## Known limits

- The old local PvP cache is not version-validated or rebuilt in this change; its stored calculations can differ from current math. Cache coverage1–100 also differs from the rule's cutoff200.
- Decisions does not evaluate evolution potential, moves, team composition or complete collection preferences. Existing stars do not automatically protect a record, and rerunning overwrites manual decisions.
- TRANSFER is a rule-based review suggestion. Automatic transfers and resource spending are not implemented or authorized.
- GPU usage is not measured; the UI says unavailable. Full-storage reliability, performance on other devices and a live GUI refresh are not claimed.

Publication performs no phone input, real favorite actions, decision rerun or user database clear.
