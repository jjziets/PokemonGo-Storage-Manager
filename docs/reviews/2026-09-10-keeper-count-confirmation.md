# Keeper count confirmation

<!-- TRACEWEAVER: file-role=keeper-count-review; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001 -->

Authority: the owner's two count-dialog screenshots and mismatch report;
REQ-MASS-001 / TRACE-MASS-001, baseline REQ-BASELINE-2026-09-09-001,
semantic SHA256 `4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`.
Advisory authority decision: Proceed with the local confirmation/count fix.
Scope excludes decision-rule changes, record reconciliation, phone actions,
runtime restart and publication. Packaged TraceWeaver runtime gates are not
claimed; this records the bounded local authority and trace check.

The decision engine reports all KEEP records. The old favorite confirmation
reported only those not recorded as favorited, without explaining that subset
or the executor's ambiguity holds. A read-only database snapshot reproduces
1,302 KEEP = 951 recorded favorites + 351 unstarred; 351 = 31 held + 320 eligible
before pass and live checks. The database contains 3,455 rows. Two subsequent
category scans added 214 rows to the previous 3,241-record scan result, so these
counts must not be described as unique physical Pokemon. No data was changed.

`Executor.plan_keeper_favorites` now supplies both the confirmation breakdown
and the executor's original occurrence Counter. Already-starred identical
neighbors remain in that Counter; pending totals exclude them. Invalid keys,
conflicting decisions and indistinguishable cross-session occurrences retain
their existing holds. Pass generation and live identity/star guards are
unchanged. The confirmation labels recorded state and conditional eligibility.

Verification: 59 tests passed with
`.venv/bin/python -m unittest tests.test_mass_action_scanning tests.test_mass_action_gui tests.test_decision_action_routing tests.test_action_worker_cleanup`.
Added cases cover reconciled totals, starred neighbors, holds, no pending rows,
and cancelling dry/real confirmations in both tabs without creating a worker.
An independent read-only review compared the old and new logic across 101
inventories and 3,333 pass-selection cases without a mismatch. The offscreen Qt
preview used actual database rows through a read-only SQLite connection and
mocked device objects. Its five counts match the user evidence and its text
fits the dialog. No real favorite action or live GUI reload was performed.

Local traceability check: Pass for this bounded change. Implementation and
regressions carry REQ-MASS-001 / TRACE-MASS-001 anchors and are mapped in the
existing matrix row. Review found no actionable issue in these new changes;
other accumulated working-tree changes were outside this review. Full-storage
uniqueness, reconciliation of later rescans, current phone favorite state,
release readiness and runtime reload remain unverified by this fix.
