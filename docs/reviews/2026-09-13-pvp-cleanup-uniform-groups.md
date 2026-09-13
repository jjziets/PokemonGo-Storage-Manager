# Form-ambiguous and uniformly unwanted cleanup groups

## Authority and incident

The owner reported the first grouped cleanup stopping with `batch position did
not have an exact complete identity`. They then explicitly authorized removing
both matching favorites when both are unwanted, and suggested the power-up
preview as a way to distinguish Oricorio forms. This is an authorized local fix
under REQ-MASS-001, REQ-IDENTITY-001, REQ-SCAN-004 and REQ-DATA-001. Baseline
REQ-BASELINE-2026-09-09-001 and semantic SHA256
`4b966981a29f704128139ccb4c85dfd6dcdbd456e08911d06a2b4379c2fb929a`
remain unchanged. No app restart, device interaction, production write or
publication is part of this work.

At 08:20:46, `logs/macos_launcher.log` records accepted Oricorio HP50,
IVs 0/14/15, with a numerically unique hidden CP but unresolved form. The
cleanup inventory incorrectly treated `exact_form=False` as incomplete reading,
even though the scanner explicitly supports complete generic-family records.
The reported run performed zero star changes and zero database updates.

A read-only SQLite query confirms the saved row is generic `Oricorio`, CP226,
HP50, 0/14/15, favorited and marked TRANSFER, at position 1064 of scan session
`63546a01-383c-468c-aab4-72f8a0e17b21`. Its decision does not claim an exact form.
The editable display name `Oricorio (Baile)` cannot supply that authority.

The locally cached GameMaster lists the same base stats for Baile, Pa'u,
Pom-Pom and Sensu: attack 196, defense 145, stamina 181. Their CP growth at the
same IVs and level is therefore identical. Power-up CP can constrain level, but
cannot identify these forms; no extra preview inputs are added for this case.

## Revised behavior

Complete accepted generic-family cards can participate in inventory and normal
verified advancement. They no longer fail solely because the form is unresolved.
Incomplete stats, unconfirmed position, stale frames and pause/source changes
still hold. Display names never narrow possible forms.

The stored cleanup policy permits a matching group only when every member is a
complete, favorited, selected 0–2-star TRANSFER record from the same scan session
and represents a distinct saved position. KEEP, unchecked, already-unfavorited,
incomplete or conflicting-session companions protect the group. Grouping by
CP, HP, IVs and flags is deliberately conservative across species/forms.

Live inventory must cover the entire group and agree with its recorded count.
Possible identities must all be covered by reviewed exact species/forms or
an explicitly stored generic-family decision, and a complete one-to-one
assignment must fit recorded multiplicity. A possible unreviewed form is not
discarded just because some other assignment is possible. A multi-form
observation can consume only a reviewed generic-family slot covering all of its
possible forms. Thus one generic record plus one exact record cannot authorize
two ambiguous cards; an all-generic group can cover its verified number of cards.
The whole group must
fit one selected query manifest; split groups are held.

The action sweep retains ordered identity/possibility evidence and rechecks the
current whole-group decision before each star. OFF confirmation is required for
every distinct position before an atomic group database update. Already-OFF
positions can repair a prior partial run without another tap. Partial physical
changes remain reported as unsaved; the code does not assign them arbitrarily
to indistinguishable row IDs. Unapproved generic groups remain starred while
unrelated verified groups may continue.

The review dialog treats matching selections as a unit. Unchecking one member
unchecks and protects the entire group; the group size is visible beside its
TRANSFER decision. No Pokémon are transferred and no saved species is relabeled.

## Verification and limits

Verification completed in an isolated copy with app-stream environment variables
removed, offscreen Qt and an unavailable ADB path:

- Full suite: **1,535 tests and 11,173 subtests passed; 17 existing private-capture
  or native-build skips**. The existing requests dependency warning remains.
- Focused action, retry, synchronization, policy and temporary SQLite checks:
  **323 tests and 4,519 subtests passed**.
- GUI checks: **37 tests and 30 subtests passed**, including selecting and
  protecting matching rows as one review group.
- An independent reviewer ran 10 focused group/pass regressions and compared
  the assignment helper against a brute-force oracle over **50,978** small
  adjacency graphs. No unresolved findings remain.
- Scope trace anchors report zero findings; `git diff --check` passes.

Regressions include the reported generic Oricorio CP226/HP50/0–14–15, uniformly
unwanted duplicates, generic-form capacity, protected exceptions, unsupported
forms, live-count mismatch, split query scope, later Gigantamax deferral,
whole-group rollback and partial-run repair. Existing star, pause, source and
transition guards remain covered.

Full-suite evidence:
`/var/folders/2f/ntb_0p9558v4wfcwr5_64dg00000gn/T/pokemgr-cleanup-groups-_pb0gntb/full-pytest.log`.
The copy path is also recorded in `/tmp/pokemgr-cleanup-groups-path.txt`; the
trace report is `/tmp/pokemgr-cleanup-groups-traces.md`.

No live throughput or complete-storage reliability is claimed. The running app
and production records remain untouched, apart from read-only log/SQLite
inspection. These source changes require a later agreed restart. No commit,
push or publication was performed.
