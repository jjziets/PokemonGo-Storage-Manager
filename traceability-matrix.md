# Traceability — stream scanning and keeper decisions

Baseline: `REQ-BASELINE-2026-09-09-001`; semantic SHA256 `4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`. Authority is the current stakeholder conversation as recorded in `requirements.md`; August records are historical. Publication includes accumulated scanner, native stream, action and decision work, with the limits below.

## Traceability Matrix

| Trace ID | Requirement | Implementation | Verification | Validation |
| --- | --- | --- | --- | --- |
| TRACE-SCAN-001 | REQ-SCAN-001 | `pokemgr/indexer/state_machine.py`, `pokemgr/reader/native_ocr.py`, `pokemgr/indexer/snapshot.py`, `pokemgr/pvp/resolver.py`, `pokemgr/pvp/calculator.py`, `pokemgr/pvp/cpm_table.py` | `tests/test_snapshot_policy.py`, `tests/test_snapshot_resolver.py`, `tests/test_cpm_precision.py`, `tests/test_gym_snapshot.py`, `tests/test_native_gym.py`, `tests/test_nidoran_identity.py`, `tests/test_nidoran_acquisition.py` | Exact hidden/visible CP examples in publication evidence |
| TRACE-SCAN-002 | REQ-SCAN-002 | `pokemgr/indexer/state_machine.py`, `pokemgr/config.py`, `pokemgr/gui/widgets/scan_control.py`, `pokemgr/reader/powerup.py`, `pokemgr/reader/bars.py`, `pokemgr/reader/screen.py` | `tests/test_hp_iv_first.py`, `tests/test_cp_model_recovery.py`, `tests/test_powerup_recovery.py`, `tests/test_settled_pair_reuse.py`, `tests/test_bar_stability.py`, `tests/test_bar_reacquisition.py`, `tests/test_stream_appraisal_settle.py` | Bounded Zygarde/Dragonite recovery; Pikachu bar animation replay and exact live resume |
| TRACE-SCAN-003 | REQ-SCAN-003 | `pokemgr/indexer/state_machine.py`, `pokemgr/indexer/multi_pass.py`, `pokemgr/indexer/scan_queue.py`, `pokemgr/gui/main_window.py`, `pokemgr/gui/app.py`, `pokemgr/adb/navigator.py`, `pokemgr/reader/native_ocr.py`, `pokemgr/reader/screen.py`, `pokemgr/reader/gender.py`, `pokemgr/execution/executor.py` | `tests/test_transition_retry.py`, `tests/test_stable_scan_loop.py`, `tests/test_navigator_recovery.py`, `tests/test_verified_filtered_count.py`, `tests/test_multi_pass.py`, `tests/test_scan_worker.py`, `tests/test_scan_queue.py`, `tests/test_scan_queue_launch.py`, `tests/test_scan_recovery.py`, `tests/test_specimen_transition.py`, `tests/test_native_specimen_markers.py`, `tests/test_specimen_reader.py`, `tests/test_gender_evidence.py`, `tests/test_gym_favorite.py` | Same-stat Lunatone sequence; repeated Plusle weight/height readings across independent live frames in checkpoint-recovery evidence |
| TRACE-SCAN-004 | REQ-SCAN-004 | `pokemgr/indexer/state_machine.py`, `pokemgr/execution/executor.py`, `pokemgr/reader/icons.py` | `tests/test_favorite_unresolved.py`, `tests/test_favorite_state.py`, `tests/test_mass_action_scanning.py`, `tests/test_gym_favorite.py`, `tests/test_native_gym.py`, `tests/test_caught_name_review.py` | Verified one-toggle readback, zero mutation during publication |
| TRACE-STREAM-001 | REQ-STREAM-001 | `pokemgr/adb/controller.py`, `pokemgr/adb/frame_buffer.py`, `pokemgr/adb/clock_sync.py`, `pokemgr/adb/stream_capture.py`, `pokemgr/reader/native_ocr.py`, `pokemgr/reader/screen.py`, `pokemgr/reader/ocr.py`, `scripts/stream_pokemon.py`, `scripts/scrcpy_frame_sink/build.py`, `scripts/scrcpy_frame_sink/pokemgr_activity.m`, `scripts/scrcpy_frame_sink/pokemgr_activity.h`, `scripts/scrcpy_frame_sink/pokemgr_frame_sink.c`, `scripts/scrcpy_frame_sink/pokemgr_frame_sink.h`, `scripts/scrcpy_frame_sink/scrcpy-v4.1.patch`, `scripts/android_clock/`, `scripts/native_ocr.swift`, `pokemgr/gui/resource_monitor.py`, `pokemgr/timing.py` | `tests/test_clock_sync.py`, `tests/test_stream_capture.py`; other stream/native/clock/resource/timing tests under `tests/`; VER-STREAM-ACTIVITY-001: `scripts/scrcpy_frame_sink/test_native.py`, `scripts/scrcpy_frame_sink/test_bridge.c`, `scripts/scrcpy_frame_sink/test_build_migration.py`, `tests/test_stream_backend_launcher.py` | Bounded M4Pro/Fold6 live sample, dark physical display; background timing comparisons in `docs/reviews/2026-09-10-stream-background-scheduling.md` |
| TRACE-MASS-001 | REQ-MASS-001 | `pokemgr/execution/executor.py`, `pokemgr/execution/keeper_queries.py`, `pokemgr/indexer/state_machine.py`, `pokemgr/gui/`, `pokemgr/adb/navigator.py`, `pokemgr/adb/search_text.py`, `scripts/android_search/`, `run.py` | `tests/test_search_text.py`, `tests/test_verified_long_search.py`, `tests/test_keeper_queries.py`, `tests/test_keeper_batch_execution.py`, `tests/test_mass_action_scanning.py`, `tests/test_action_preswipe_retry.py`, `tests/test_mass_action_gui.py`, `tests/test_decision_action_routing.py`, `tests/test_action_worker_cleanup.py` | Exact full query readback, disjoint pending CP coverage and read-only keeper sample |
| TRACE-IDENTITY-001 | REQ-IDENTITY-001 | `pokemgr/reader/candy.py`, `pokemgr/reader/nidoran.py`, `pokemgr/reader/name_matcher.py`, `pokemgr/pvp/gamemaster.py`, `pokemgr/pvp/fingerprint.py`, `pokemgr/pvp/resolver.py` | `tests/test_candy_family.py`, `tests/test_native_reader_integration.py`, `tests/test_nidoran_identity.py`, `tests/test_nidoran_acquisition.py` | Exeggcute candy constrains Exeggutor without replacing species |
| TRACE-DECISION-001 | REQ-DECISION-001 | `pokemgr/decision/engine.py`, `pokemgr/decision/rules.py`, `pokemgr/gui/widgets/decision_review.py` | `tests/test_decision_identity.py`, `tests/test_decision_highest_cp.py`, `tests/test_decision_perfect_ivs.py`, `tests/test_decision_action_widget.py` | Read-only Garchomp/highestCP and duplicate-perfect Mewtwo examples |
| TRACE-DATA-001 | REQ-DATA-001 | `pokemgr/data/database.py`, `pokemgr/calibration/`, `pokemgr/gui/workers.py`, `scripts/macos_launcher.zsh` | `tests/test_database_positions.py`, `tests/test_calibration_workflow.py`, `tests/test_stream_resource_cleanup.py`, `tests/test_stream_launcher.py` | User data retained, cooperative cleanup |

## Evidence and held claims

Publication bookkeeping: `docs/reviews/2026-09-13-cleanup-publication.md` records
the current commit/push target and reused verification. `pokemgr/data/models.py`
and `tests/test_model_star_rating.py` implement and verify REQ-MASS-001's exact
appraisal-star cleanup eligibility (TRACE-MASS-001, VER-SCAN-001).
`pokemgr/reader/appraisal_navigation.py` and
`tests/test_checkpoint_arrow_recovery.py` implement and verify REQ-SCAN-003's
observed checkpoint navigation (TRACE-SCAN-003, VER-SCAN-001). These links and
source/test comments repair navigability without changing behavior or authority.

REQ-MASS-001 / REQ-IDENTITY-001 / REQ-SCAN-004 / REQ-DATA-001 uniform cleanup
group follow-up: `pokemgr/execution/pvp_cleanup.py` and
`pokemgr/execution/executor.py` preserve accepted generic-family observations,
require unanimous reviewed group authority, complete possibility coverage and
live counts, then conditionally save whole-group OFF through
`pokemgr/data/database.py`. `pokemgr/gui/widgets/pvp_cleanup.py` protects review
groups as a unit. Tests in `tests/test_pvp_cleanup_plan.py`,
`tests/test_pvp_cleanup_execution.py`, `tests/test_pvp_cleanup_batches.py`,
`tests/test_database_reviewed_actions.py` and `tests/test_pvp_cleanup_gui.py`
link the September13 owner clarification and Oricorio incident to
`docs/reviews/2026-09-13-pvp-cleanup-uniform-groups.md`.

REQ-MASS-001 / REQ-SCAN-004 / REQ-DATA-001 grouped PvP cleanup follows the
owner's September13 request to swipe through filtered candidates rather than
search per Pokémon. The executor verifies complete group inventories before
selective action traversals; duplicate, incomplete, stale and changed-position
evidence cannot authorize a star. `tests/test_pvp_cleanup_execution.py`,
`tests/test_pvp_cleanup_batches.py`,
`tests/test_pvp_cleanup_gui.py` and `pokemgr/gui/widgets/keeper_progress.py`
link execution and phase feedback through `pokemgr/gui/widgets/decision_review.py`
and `pokemgr/gui/widgets/mass_actions.py` to
`docs/reviews/2026-09-13-pvp-cleanup-batches.md`.

REQ-SCAN-001/002/003 appraisal settle follow-up: the shared acquisition in
`pokemgr/indexer/state_machine.py` retries only read-only `appraisal_not_stable`
exhaustion within its existing three-attempt budget, retaining all validation,
transition and pause guards. `tests/test_appraisal_settle_retry.py` covers retry
success, terminal typed holds and diagnostic-only failed frames. Incident evidence,
verification and unproved live behavior are recorded in
`docs/reviews/2026-09-12-appraisal-settle-retry.md`.

REQ-MASS-001 / REQ-SCAN-004 / REQ-DATA-001 / REQ-DECISION-001 selective PvP
cleanup follow-up: `pokemgr/execution/pvp_cleanup.py` plans only reviewed,
unambiguous 0–2★ favorited TRANSFER records, preserving all KEEP and 3–4★.
`pokemgr/execution/executor.py` performs exact selective OFF actions and the GUI
shares a preview/dry run through `pokemgr/gui/widgets/pvp_cleanup.py` and
`pokemgr/gui/workers.py`. Verification entry points are
`tests/test_pvp_cleanup_plan.py`, `tests/test_pvp_cleanup_execution.py` and
`tests/test_pvp_cleanup_gui.py`, `tests/test_database_reviewed_actions.py` and
`tests/test_model_star_rating.py`. Owner scope, evidence and remaining model limits
are recorded in `docs/reviews/2026-09-12-pvp-cleanup.md`.

REQ-STREAM-001 / REQ-DATA-001 fixed-canvas follow-up: `pokemgr/config.py`,
`scripts/stream_pokemon.py` and `pokemgr/adb/controller.py` request and validate
968×2376/420 app geometry. `pokemgr/calibration/profile.py` and `evidence.py`
reuse compatible coordinates while retaining device/density identity and
independent verification. Evidence: `tests/test_shared_calibration.py`,
`tests/test_calibration_workflow.py`, `tests/test_stream_launcher.py`,
`tests/test_stream_backend_launcher.py`, `tests/test_stream_display.py`.
REQ-SCAN-003 setup epoch repair is verified by
`tests/test_multi_pass_setup_epoch.py`. Current publication evidence is
`docs/reviews/2026-09-10-standard-canvas-publication.md`; the active scan is
untouched, and live validation on a second device remains outstanding.

REQ-MASS-001 / REQ-DATA-001 / REQ-SCAN-004 nonfavorite traversal and gym skip
follow-up: `pokemgr/execution/executor.py` excludes phone favorites, bounds
changes by eligible unstarred records and verifies shrinking counts between
traversals. `pokemgr/execution/favorite_sync.py` preserves only confirmed
OFF-to-ON evidence across those traversals. Explicit gym defenders may authorize
skip-only navigation with hidden HP. Evidence: `tests/test_nonfavorite_filter.py`,
`tests/test_nonfavorite_carousel.py`, `tests/test_favorite_change_sync.py`,
`tests/test_action_identity_acquisition.py`, `tests/test_keeper_gym_skip.py` and
`docs/reviews/2026-09-10-nonfavorite-gym-actions.md`. Live speed and a complete
favorite run remain unverified for this change.

REQ-MASS-001 / REQ-SCAN-004 pre-star recovery follow-up: default-label accent
normalization and bounded independent rereads in `pokemgr/execution/executor.py`
are verified by `tests/test_action_name_identity.py`,
`tests/test_action_prestar_retry.py` and
`docs/reviews/2026-09-10-flabebe-favorite-identity.md`. The exact field that differed
in the original failed pair is unknown; later read-only live checks and offline
reproductions establish the comparison defect and repaired path.

REQ-MASS-001 / REQ-DATA-001 / REQ-SCAN-004 favorite-state persistence follow-up:
`pokemgr/execution/favorite_sync.py`, `pokemgr/execution/executor.py`,
`pokemgr/data/database.py`, `pokemgr/indexer/state_machine.py` and action GUI
completion handlers save confirmed stars and report unresolved synchronization.
Evidence: `tests/test_favorite_db_sync.py`, `tests/test_scan_star_sync.py`,
`tests/test_action_db_sync_gui.py` and
`docs/reviews/2026-09-10-favorite-database-sync.md`. Interrupted CP-free category
passes do not establish exact per-record favorite state. No active app restart
or live validation was performed for this fix.

REQ-MASS-001 keeper progress follow-up: Decisions and Mass Actions share selected
pass X/Y, upcoming categories, cumulative keeper/check counts, and separately
labelled CP batch/refresh-round progress. `tests/test_favorite_progress.py` checks
observational counters, lazy query ordering and partial outcomes;
`tests/test_keeper_progress_widget.py` checks both panels through refresh, pause,
stop, completion and dry runs. Worker/routing tests cover stale signals and cleanup.
These checks are offline and do not claim verification against the running phone.

REQ-SCAN-003/004 / REQ-STREAM-001 / REQ-DATA-001 tablet recovery follow-up:
observed navigation departures, appraisal-arrow checkpoints and bounded fresh
confirmation pairs are covered by `tests/test_storage_navigation_settle.py`,
`tests/test_checkpoint_arrow_recovery.py` and
`tests/test_transition_pair_revalidation.py`. Capture fallback reasons are
covered by `tests/test_stream_capture.py` and `tests/test_stream_controller.py`.
Transactional clearing of the active database connection, rollback, WAL and
worker guards are covered by `tests/test_database_clear.py` and
`tests/test_stream_resource_cleanup.py`. Authorized backup-and-prune recovery
preserved all 786 tablet rows and removed 3,455 older phone rows. Evidence and
manual-resume tagging limits: `docs/reviews/2026-09-11-tablet-recovery.md`.
The 1,402-test offline suite passed; the owner authorized restart and the updated
GUI/stream reopened with 786 records. The owner then began live keeper favoriting;
the new multipass display was observed, but full-run reliability remains unverified.

Current verification, review identities and local command results are summarized in `docs/reviews/2026-09-09-publication.md`. Tests linked above are representative entry points; publication runs the complete discovered suite. Native and private-image checks can be unavailable on a fresh checkout and are reported separately from reproducible checks. Historical inline VER-SCAN-001 anchors refer to the original test work and do not imply blanket acceptance of later features.

REQ-STREAM-001 background scheduling follow-up: `scripts/scrcpy_frame_sink/`
holds a process-local user-initiated activity during native export. Pre-change
timed failures, controlled activity trials, native lifecycle/protocol checks,
and integrated live results are recorded in
`docs/reviews/2026-09-10-stream-background-scheduling.md`. Performance failure
evidence preceded implementation; cleanup regressions are checked after the
implementation. This bounded verification does not close full-device,
unattended reliability or zero-stutter claims.

This is an advisory project baseline recording already-authorized work. No test-first history, complete transfer advice, full-storage throughput, GPU measurement, universal device compatibility or deployment is claimed. Known decision/PvP model debt is explicitly retained in `requirements.md` and the README. Publication authorization is independent of future phone mutations or database changes.
