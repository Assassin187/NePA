## Why

M1-9 can execute and atomically verify a supplied migration, but NePA still has no deterministic producer that converts accepted S6 machine facts into revision-trigger events or applies the closed F2/F3 operator set to form a complete migration candidate. M1-10 is the next serial work item and must close that boundary before M1-11 may gate, rehearse, or activate any candidate.

## What Changes

- Evaluate TR-1 through TR-8 from validated machine facts at legal S6 boundaries, record every hit and selection decision, and keep TR-5 record-only, TR-8 at the provider submission boundary, and TR-9 outside M1 execution.
- Derive stable trigger signatures from the normalized issue identity rather than volatile evidence details; enforce per-signature selection, rejection-at-level, and prior-activation deduplication without consuming version allowance for record-only observations.
- Define and validate the closed F2/F3 patch-operation contract from pipeline design §6.2, including the rule that F3 candidates may contain the F2 operations necessary to close their structural change while forbidden or implicit operations remain unavailable.
- Apply an ordered patch atomically to the supplied active Plan/architecture slice, rerun the existing deterministic Linker/full-lint and Blueprint derivation path, and produce one complete candidate Plan, explicit lineage/obligation mapping, and migration report.
- Correct the formal Plan persistence boundary by upgrading the current producer/consumer set to Plan Schema 5.0: persist each task's `local_task_id`, continue deriving `task_uid` from `[work_package, local_task_id]`, and recover PlanDraftIR losslessly for revision patching. This change intentionally provides no Plan 4.0 conversion path.
- Verify INV-1 commitment immutability, INV-2 monotonic requirement coverage, and INV-3 obligation/acceptance preservation on the complete candidate; invalid operations or candidates fail without modifying the active Plan, State, ledgers, workspace, or formal version chain.
- Add protocol-neutral synthetic MQTT and non-MQTT fixtures plus a `revision_mechanism` pytest marker covering every M1 trigger and operator with positive and negative cases.
- **Out of scope:** M1-11 RG-1 through RG-5 orchestration, PlanCritic invocation, S5 rehearsal, candidate rejection publication and atomic activation; M1-12 circuit breakers/effectiveness; M1-13 production root-cause evidence; M1-14 PlanReviser prompt, Agent call, calibration or production enablement; TR-9 execution; S7/S8/M2 assets; public CLI switches; edits to `project_docs/`, architecture prompts, calibration lineage, or archived changes.

## Capabilities

### New Capabilities

- `revision-trigger-and-patch-operators`: Deterministic TR-1 through TR-8 evaluation, stable signature/selection semantics, the closed F2/F3 operator language, atomic patch application, and complete invariant-valid migration-candidate construction.

### Modified Capabilities

- `plan-revision-infrastructure`: Give `trigger_evaluated` its complete M1 producer and deduplication contract, and represent an unactivated candidate's trigger, patch, lineage and migration outputs without treating candidate construction as activation.
- `plan-compilation-validation`: Reuse the deterministic candidate-completion, Linker, full-lint and Blueprint projection path for a patched revision candidate while preserving frozen commitments and state-free Plan compilation.

## Impact

- **Milestone/work item:** M1-10 only. Governing authority is `project_docs/system_design.md` §10.2.1-§10.2.2 and its persisted Plan/revision contracts, plus `project_docs/pipeline_design_s4_s9.md` §2, §3, §6.1-§6.2 and §7.2. M1-10 supplies the trigger/operator portion later composed into D1.13; it does not claim the M1-11 gate/activation/crash-recovery remainder of that DoD.
- **Verified prerequisite:** `m1-9-s6-migration-joint-verification` is archived at `openspec/changes/archive/2026-09-09-m1-9-s6-migration-joint-verification/`; its corrected-diff owner review records approval on 2026-09-09 and its replacement acceptance records 693 passing tests, focused S5/S6 results, public lint and strict OpenSpec validation. Implementation must recheck the committed current baseline without rewriting that archive.
- **Affected paths:** the current Plan Schema and fresh-run Plan producers/consumers/fixtures; the existing revision speclib and typed revision-ledger Schema; Plan/architecture compilation, lineage and migration helpers; S6 boundary facts and provider-submission validation; new closed trigger/candidate/patch Schemas or examples only where the existing contracts cannot express the required output; protocol-neutral fixture generation and focused tests. Run Schema 4.0 and unrelated versioned contracts remain unchanged.
- **Public contracts:** persisted trigger events and internal candidate/patch artifacts gain closed M1 producer semantics. The user-facing CLI and Agent role surface remain unchanged, and no new dependency is introduced.
- **Preserved behavior:** the active Plan/version chain, Plan State, file/revision ledgers, workspace, accepted M1-9 evidence, ordinary F0/F1 execution, migration execution, S5 materialization and all frozen commitments remain unchanged during trigger evaluation and candidate construction.
- **Acceptance:** `uv run pytest -q -m revision_mechanism` must cover every TR/operator positive and negative case, stable-signature deduplication and INV-1/2/3; affected Schema/Plan/revision/S6 regressions, the public §10.8 CI, strict current/all OpenSpec validation and `git diff --check` must pass. A separate responsible-owner review of the final M1-10 implementation and evidence is required before archive; automation must not mark that approval complete.
