## Context

See `proposal.md` for motivation. The authoritative boundary is `project_docs/system_design.md` 7.1.1 §4.7-§4.8, §5.2, §6.4.1-§6.4.8 and §10.2 together with `project_docs/pipeline_design_s4_s9.md` 1.2.0 §5.1-§5.3.

The repository already has the M1-1 deterministic stage orchestrator and atomic RunStore, M1-2 provider runtime, M1-3 role registry/invoker, M1-4a1 shared planning/architecture validation path, the owner-approved M1-4a2 prompt pair, and the M1-4b/M1-4b2 deterministic Plan/Blueprint compiler and validators. It has no production S4 controller, no TaskPlanner/PlanCritic/flat output Schemas, and no initial Plan publication contracts. Run v3 currently assumes every `output_refs` value is a file reference, while the authoritative S4 seal also requires two independent hash-only anchors.

The selected V1 M1-4a2 raw template hashes are already byte-identical to the packaged `architecture_planner_initial.md` and `architecture_planner_repair.md`. M1-4c must verify that evidence; it must not copy calibration code into a second production planner or publish calibration candidates as formal Plans.

## Goals / Non-Goals

**Goals:**

- Compose existing components into one bounded `StageController` for S4a/S4b/S4c.
- Make every LLM call narrow, fresh, Schema-bound, traceable, budgeted, and replayable from parent-bound checkpoints.
- Give layered and explicit flat strategies one common deterministic completion and publication path.
- Publish exactly the initial version/ledgers/pointer defined by design 7.1.1, then make the Run update the only logical S4 commit.

**Non-Goals:**

- No task UID or obligation/guidance digest, revision migration/classification, later-version publication, ledger append, F2/F3, or PlanReviser behavior from M1-4d/e.
- No S5 materialization, S6 Plan State initialization/execution, public run/resume/status CLI, Test Bundle implementation visibility, or M2 acceptance binding.
- No new provider, route, dependency, protocol-specific path, prompt optimization, live quality claim, or owner approval.

## Decisions

### 1. Add one production S4 controller at the existing stage boundary

Add `nepa/stages/s4_planning.py` with `S4Controller`, constructed with the existing `AgentInvoker`, the verified M1-4a2 handoff root, and deterministic collaborators already exported by `speclib`. `run(StageContext) -> StageResult` owns the complete state machine; it calls `StageContext.orchestrator.admit_external_call` before each Agent invocation and relies on the existing LLM usage path for durable accounting.

The controller is the only component allowed to choose strategy, repair route, budget consumption, checkpoint reuse, or seal. Planning helpers stay pure; the generic Orchestrator continues to own stage admission, controlled-exit routing, and the final Run state transition.

Alternative considered: put S4 flow into `Orchestrator`. Rejected because it would mix stage-specific planning semantics into the generic S4-S6 lifecycle and duplicate the existing `StageController` extension point.

### 2. Verify the approved handoff, then use the packaged production templates

Admission resolves the recorded M1-4a2 lineage root and verifies `handoff.json`, selection, assessment, owner approval, bundle snapshot, and both raw templates through their recorded path/SHA-256 references. It also requires `consumer="m1-4c"` and the three true handoff assertions. The bundle's two hashes must equal the bytes loaded by the packaged ArchitecturePlanner role.

After admission, production invocation uses the ordinary role registry, route resolver, prompt renderer, and AgentInvoker. Architecture repair delegates to the existing `ArchitecturePlannerContractBinding` and patch application/validation path. No runtime path reads a calibration candidate as the architecture result.

Alternative considered: invoke templates directly from the calibration run directory. Rejected because a historical experiment directory is evidence, not a production runtime dependency; the handoff instead authorizes the already packaged byte-identical templates.

### 3. Persist a canonical commitment and reference-only checkpoint manifests

S4a writes canonical `_s4/commitment.json` containing the three frozen input refs, `config_snapshot_sha256`, the normative requirement id/level set, Test Manifest contract projection, enabled/build-variant projection, planning budgets, and layer switches. It also publishes the existing canonical planning index and Delivery Constraints. Their hashes close L-C.

Every later accepted step publishes its payload under its natural closed Schema and a small `s4-checkpoint` manifest containing `kind`, stable ordinal/target id, exact parent artifact refs, payload/report refs, and the semantic-budget counters after that step. Checkpoints contain references rather than embedding a second copy of architecture, shard, candidate, or review data. Proposed families are:

- `architecture/attempt_NNN/` for candidate, patch/application when applicable, validation, and checkpoint;
- `shards/<work_package_id>/attempt_NNN/` for task-shard result, validation, and checkpoint;
- `candidates/round_NNN/` for PlanDraftIR, link report, Blueprint, candidate Plan, full-lint report, and checkpoint;
- `reviews/round_NNN/` for the typed critic result and checkpoint.

Resume scans these stable names in state-machine order and accepts only the longest Schema-valid chain whose referenced bytes and parent hashes match. Budget use is recovered from accepted checkpoint manifests and existing LLM telemetry, so process restart cannot reset a semantic allowance. An invalid child is ignored with its descendants; an immutable path containing conflicting bytes is artifact damage, not a reason to overwrite it.

Alternative considered: maintain one mutable `_s4/state.json`. Rejected because it would create a second state authority that could disagree with the immutable artifacts and obscure crash reconciliation.

### 4. Bind each S4 role to one exact output contract

TaskPlanner adds a fifth declared input, `planning_budget`, and returns the existing closed task-shard contract. Its other four inputs remain the one work package, complete Spec responsibility slice, adjacent contracts, and applicable Test Manifest metadata.

PlanCritic returns a new closed `plan-critic-result` object with `verdict`, and issues containing `id`, `severity`, `scope`, `target_id`, `code`, `description`, `required_change`, and `context_refs`. The controller recomputes verdict consistency and derives the canonical issue signature from `(severity, scope, target_id, code)`; model text does not affect convergence identity.

FlatPlanBaseline returns a new closed state-free `flat-plan-draft` that contains architecture, work packages, and task shards but none of the controller-owned final fields. The controller normalizes it through the existing PlanDraftIR path. Flat remains available only when the sealed strategy is `flat`.

Alternative considered: allow each role to return a near-final Plan. Rejected because final ids, dependencies, coverage, Blueprint and hashes are deterministic-controller responsibilities and would fork the M1-4b path.

### 5. Use one deterministic candidate-completion function for both strategies

Add a narrow controller helper that receives semantic architecture/work packages/shards plus frozen refs and configuration, then calls the existing PlanDraftIR normalizer/Linker, Blueprint compiler, and `plan_lint(level="full")`. It publishes candidate evidence only after all S4-G0 through S4-G6 errors are zero. Both strategies and every repair round call this helper; no strategy-specific linker or lint exists.

Layered expands work packages serially by stable work-package id. A local semantic critic issue invalidates and redoes only its named shard; a global issue performs one ArchitecturePlanner repair/replan and invalidates all architecture children. Flat semantic revise discards the whole flat draft. Every admitted repair is followed by complete candidate completion and a fresh critic. Mechanical recomputation consumes no semantic budget.

### 6. Keep the initial publication contract minimal and M1-4d-free

Add closed Schemas/examples for:

- `s4-commitment` and reference-only `s4-checkpoint`;
- `plan-critic-result` and `flat-plan-draft`;
- `active-plan` with exactly `version`, `path`, `sha256`, `revision_seq`, and `epoch`;
- initial `file-ledger` with one `{path, class, state:"slot_only"}` row per expanded Blueprint path;
- `revision-ledger` with `schema_version="1.0"` and `entries=[]` for this milestone.

The existing Plan v4 and task-shard Schemas remain unchanged: M1-4c does not add `task_uid`, `obligation_digest`, or `guidance_digest`. M1-4d will extend the ledger/version contracts and linker fields when it implements real revision entries and migration.

Alternative considered: implement the complete M1-4d data model now. Rejected by the selected milestone boundary and because it would couple S4 bring-up to migration logic that has separate acceptance gates.

### 7. Make S4 output anchors stage-specific without weakening other stages

Change Run v3 so `stages.s4.output_refs`, when present, has the closed keys:

```text
plan                         -> {path, sha256}
active_plan                  -> {path, sha256}
delivery_blueprint_sha256    -> 64 lowercase hex
config_snapshot_sha256       -> 64 lowercase hex
```

Other stages keep the existing reference-map shape. `StageResult.output_refs` accepts the stage-specific values, and RunStore/Orchestrator validation dispatches by stage: it verifies file refs normally and checks hash-only anchor syntax at commit. `S4Controller.verify_completed(store)` performs the semantic reread: Plan, pointer, ledgers, frozen refs, recomputed Blueprint, and config snapshot must all agree. The Orchestrator calls this verifier for an installed production S4 controller before accepting an already-done S4 as a no-op; generic fake controllers retain existing reference-only behavior.

Alternative considered: encode hashes as fake file refs. Rejected because it changes their meaning and cannot independently anchor the in-memory config snapshot or Blueprint semantic projection.

### 8. Publish immutable artifacts before the single logical Run commit

After a final critic pass, the controller regenerates candidate evidence and reruns Schema/full lint/coverage/Blueprint checks. Publication order is:

1. idempotently publish canonical `plan/versions/plan-1.0.0.json`;
2. publish the slot-only file ledger and empty revision ledger;
3. atomically publish `active_plan.json` for version 1.0.0, revision 0, epoch E0;
4. reread and semantically verify every artifact and independent anchor;
5. return the four S4 output values so the Orchestrator atomically changes `stages.s4` to done.

Files existing before step 5 are not consumable. Resume with matching bytes completes the missing suffix; conflicting immutable bytes fail closed. Once step 5 is committed, `verify_completed` runs and S4 becomes a read-only no-op.

## Risks / Trade-offs

- **[Checkpoint graph grows across critic rounds]** → Stable directories and reference-only manifests retain auditability without duplicating payloads; budgets cap growth.
- **[Production validation accidentally diverges from calibration]** → Handoff hash checks and direct reuse of preparation, contract binding, patch, and ARCH_VALIDATE implementations are acceptance gates.
- **[Mixed S4 Run anchors weaken generic output verification]** → Give only S4 a closed output shape and require semantic `verify_completed`; leave every other stage's reference-only schema unchanged.
- **[Crash between multiple file publications exposes a partial set]** → Only the final Run transition is consumable; resume validates the candidate and every existing byte before completing the suffix.
- **[TaskPlanner or critic prompt quality is not calibrated]** → M1-4c tests controller mechanics with scripted Agent results; real complete-chain quality remains D1.3 evidence and is not claimed here.

## Migration Plan

1. Land the closed Schemas/examples and role contract updates while leaving the generic Orchestrator behavior compatible with existing reference-only stage results.
2. Add checkpoint and handoff-admission helpers, then implement S4a and S4b over the shared production/calibration functions.
3. Add layered and flat S4c loops, common deterministic completion, critic routing, and semantic-budget persistence.
4. Add initial publication and S4-specific Run output verification; register the controller only through programmatic orchestration because M1-7 owns the public CLI.
5. Validate scripted success/failure/replay/crash scenarios, protocol neutrality, all existing tests and gold lints. Rollback is removal of the unregistered production S4 controller and its new contracts; no existing published run is migrated by this change.
