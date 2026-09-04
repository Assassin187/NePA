## Why

M1-4a2 now provides the owner-approved ArchitecturePlanner `initial`/`repair` prompt bundle, and M1-4b/M1-4b2 provide the deterministic Delivery Compiler, PlanDraftIR Linker, Blueprint compiler, Plan lint, and state validators. The repository still lacks the M1-4c controller that composes those assets into the bounded S4a/S4b/S4c workflow and atomically publishes the first consumable Plan version.

## What Changes

- Implement the complete S4 controller state machine: deterministic S4a commitment preparation, S4b ArchitecturePlanner generation/one bounded semantic repair and `ARCH_VALIDATE`, then S4c serial TaskPlanner shard expansion, deterministic linking, full lint, PlanCritic, bounded targeted repair, and seal.
- Consume and verify the existing owner-approved M1-4a2 handoff and the byte-identical packaged ArchitecturePlanner prompt pair through the existing role routing and Agent invocation boundary.
- Keep `layered` as the production default and support `flat` only as the explicitly configured A9 comparison arm; both normalize to the same PlanDraftIR and use the same Linker, full lint, critic, and seal path, with no fallback between strategies.
- Persist `plan/_s4/s4_state.json` plus parent-hash-bound `plan/_s4` checkpoints and implement deterministic resume/reconciliation for architecture, shard, critic, and publication crash windows without exposing draft artifacts downstream.
- Publish canonical `plan/versions/plan-1.0.0.json`, an initial slot-only file ledger, an empty revision ledger, and `active_plan.json` at `revision_seq=0`/`epoch=E0`; only after revalidation atomically mark S4 done with the required independent Run anchors.
- Add only the closed persisted and Agent-output contracts required by this controller, and extend the Run S4 output contract so file references and the Blueprint/configuration SHA-256 anchors remain distinct and verifiable.
- Route budget exhaustion, invalid structured output, and non-convergent repair as controlled S4 failure with no partially consumable Plan; checkpoint/seal damage and immutable publication conflicts remain internal errors.

This change does not implement M1-4d task UIDs, obligation/guidance digests, version migration, revision append/activation, or crash recovery for later revisions. It also excludes M1-4e F2/F3, S5/S6 behavior, M1-7 public run/resume/status CLI, M2 test binding, live provider qualification, and any design expansion beyond design 7.1.1.

## Capabilities

### New Capabilities

- `s4-controller-runtime`: Bounded, protocol-neutral S4a/S4b/S4c orchestration, strategy isolation, targeted repair, checkpointing, controlled failure, and resume.
- `s4-initial-plan-publication`: Canonical initial Plan/ledger/active-pointer publication and the independent S4 seal that makes the Plan consumable.

### Modified Capabilities

- `agent-invocation-runtime`: Bind the existing TaskPlanner, PlanCritic, and FlatPlanBaseline roles to their production S4 output contracts and strategy-specific invocation rules.
- `planning-architecture-infrastructure`: Admit the owner-approved prompt handoff into production S4 while preserving the shared preparation, prompt, patch, and `ARCH_VALIDATE` paths used by calibration.
- `plan-compilation-validation`: Make the existing PlanDraftIR Linker and full lint the mandatory controller publication gate and define how validated critic results feed deterministic relinking.

## Impact

- **Milestone/design:** M1-4c under `project_docs/system_design.md` 7.1.1 §4.7-§4.8, §6.4.1-§6.4.8 and §10.2, plus `project_docs/pipeline_design_s4_s9.md` 1.2.0 §5.1-§5.3.
- **Verified prerequisites:** M1-4a2 is archived with an owner-approved M1-4c handoff; its selected V1 prompt hashes equal the packaged production prompt bytes. M1-4b/M1-4b2 is implemented, machine-validated, synced, committed, and archived.
- **Affected code:** the existing orchestrator/RunStore stage boundary, planning and architecture helpers, Agent role contracts, deterministic Plan/Blueprint compiler, Run and new S4 artifact Schemas/examples, plus focused controller/publication/resume tests.
- **Public behavior:** no new CLI command. The internal `StageController` integration gains a production S4 implementation, and Run v3 gains a closed S4-specific output-ref shape without changing existing M0 lint behavior.
- **DoD:** scripted layered and flat fixture runs must reach the same deterministic publication gates; S4-G0 through S4-G6 must pass; PlanCritic must have no unresolved blocker/major; initial artifacts and Run anchors must reread consistently; crash-window resume and idempotent replay must pass; all existing tests, four gold lints, strict OpenSpec validation, protocol-neutrality checks, and `git diff --check` must remain green.
- **Human gates:** M1-4c defines no new owner signature. It consumes the existing M1-4a2 approval and must not manufacture or alter that approval.
