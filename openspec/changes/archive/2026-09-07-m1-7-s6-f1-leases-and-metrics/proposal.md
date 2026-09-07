## Why

M1-6 can complete an ordinary task only within that task's own files, so a local consistency repair that necessarily touches a completed neighbor still cannot be accepted without losing either ownership or evidence integrity. M1-7 is the next serial work item: it must add the bounded F1 joint-verification path and deterministic M1 metric calculations before multi-epoch and revision work consumes those facts.

## What Changes

- Add controller-owned F1 repair leases for the current Fixer attempt, limited to at most two `s6_owned` files of another already-done task in the same work package or a direct contract provider, with unchanged export declarations and both lease and run-wide budgets available.
- Persist `lease_started` before provider I/O, extend only that Fixer invocation's file/context boundary, and preserve the current task's normal attempt accounting while leaving both tasks' owners and the neighbor's ordinary attempts unchanged.
- Validate the current task and lending neighbor on one candidate tree with all required builds and smoke checks, publish per-task and joint evidence, create one joint commit, and atomically advance both State/file-ledger projections plus `verification_committed` and successful `lease_finished` events through the existing verification WAL.
- On validation failure or a pre-commit interruption, restore the shared baseline, keep the neighbor done, retain the current task's consumed Fixer attempt and immutable failure evidence, and append one failed `lease_finished`; after a legal joint commit, recover forward for all members or fail closed.
- Add pure code-generation metric calculation over Plan/State lineage, immutable receipts/evidence, revision/lease events and associated telemetry, including final and `@r0` task rates, historical blocking/first-pass rates, M1 build/smoke values, revision summaries and lease count/success/pending/external-file percentiles with the prescribed null-reason envelopes.
- Preserve a sequential, non-hash-chained State history for recovery and metrics, bind its immutable S6 snapshot in the receipt, and associate model costs by stable trace `output_path`.
- Extend the existing S6 status/lint/CI path for leases and expose the metric functions for later M2 `eval runs` reuse, without adding the M2 evaluation CLI.
- Add deterministic `pytest.mark.s6_lease` success, failure and crash fixtures and `pytest.mark.metric_contract` fixed formula/lineage/empty-data fixtures.
- **Out of scope:** TR-1 through TR-9 trigger evaluation and patch operators (M1-10); F2/F3 activation; E1+ materialization (M1-8); AMEND, REVALIDATE and repair groups (M1-9); circuit breakers (M1-12); M2 Test Bundle assets, S7/S8 metrics, batch aggregation or `nepa eval runs`; production calibration; and edits to the selected ArchitecturePlanner lineage. The user-authorized State-history/call-reference clarification is the only `project_docs/` change.

## Capabilities

### New Capabilities

- `s6-f1-lease-execution`: Bounded neighbor-file lease authorization, joint validation/publication, failure rollback and crash reconciliation on top of the ordinary S6 loop.
- `code-generation-metrics`: Pure, deterministic M1 code-generation and lease/revision metric calculations with lineage-aware denominators and explicit availability results.

### Modified Capabilities

- `s6-f0-execution`: Permit an otherwise ordinary Fixer attempt to execute under one controller-authorized F1 scope and complete through the joint path without granting a new attempt.
- `agent-invocation-runtime`: Allow the F1 Fixer context to include only the authorized neighbor files and lease evidence while preserving the existing closed response and visibility contracts.
- `plan-state-validation`: Add the `amended_under_lease` joint transition and execution-lint proof that keeps the lending task done and binds both members to one tree, commit and evidence set.
- `plan-revision-infrastructure`: Produce and validate the existing typed lease events and multi-member `verification_committed` transaction without advancing the Plan revision sequence.

## Impact

- **Milestone/work item:** M1-7 only. Governing sections are `project_docs/system_design.md` §5.2.4, §5.4, §5.6.7, §6.6, §9.1.4, §10.2.1-§10.2.2 and `project_docs/pipeline_design_s4_s9.md` §6.5, §7.2 and §9.
- **Prerequisites:** the M1-6 change is archived as `2026-09-06-m1-6-s6-f0-execution` with completed task records; its current focused regression state remains an implementation-time verification gate. No new owner-signature gate is defined for M1-7, and this change does not substitute for the later M1-13/M1-14 parameter/prompt approvals or M1-15 sign-off.
- **Affected paths:** the existing S6 controller/context/candidate/publication/reconciliation path; Plan State, task/joint evidence, verification-WAL and revision-ledger validators/Schemas; status/lint projections; a reusable metrics module; frozen fixtures and CI markers.
- **Frozen behavior:** sealed Plan/Spec/Target/Test Bundle/Blueprint/E0 and accepted M1-6 evidence remain immutable. F1 changes neither task ownership nor Plan version/epoch/revision sequence, and shared code/templates remain protocol/provider/model neutral.
- **Dependencies:** reuse the current Agent, budget, sandbox, Git, RunStore, ledger and verification-WAL implementations. Add no runtime dependency unless implementation proves an authoritative contract cannot be satisfied with existing assets.
- **Acceptance/DoD:** `uv run pytest -m s6_lease`, `uv run pytest -m metric_contract`, the M1-6 regression marker, public CI from §10.8, strict OpenSpec validation and `git diff --check`. Required fixtures cover two-member success, validation failure, every joint-publication crash side, fixed §9.1.4 lineage/formula cases, legal empty ledgers and unavailable inputs.
