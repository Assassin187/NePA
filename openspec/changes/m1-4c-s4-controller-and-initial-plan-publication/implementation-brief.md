# M1-4c Implementation Brief

## Inputs and Schema references

- Run-local frozen `spec/spec.json`, `inputs/target.json`, and `inputs/test_bundle.json`, their Run v3 refs, and the sealed config snapshot/hash; validate through the existing M0/RunStore paths.
- M1-4a2 handoff lineage `ee5a23a8fcbaa5dc273f36c0365707fac5a9684f050463fc32ec7fd6bc3b67a5`, including handoff, selection, assessment, owner approval, V1 bundle snapshot, and the packaged byte-identical initial/repair templates; use the existing calibration handoff Schemas.
- Existing planning index, Delivery Constraints, ArchitectureDraft/patch/validation, task-shard, PlanDraftIR, Delivery Blueprint, Plan and link-report contracts.
- New change-owned contracts: S4 commitment/checkpoint, TaskPlanner budget-aware binding, flat draft, PlanCritic result, active Plan, initial file ledger, empty revision ledger, and the S4-specific Run output shape.

## Outputs and acceptance commands

- `plan/_s4/s4_state.json`, canonical commitment, and parent-bound checkpoint/evidence chain.
- Canonical `plan/versions/plan-1.0.0.json`, `plan/file_ledger.json`, `plan/revision_ledger.json`, and `plan/active_plan.json`.
- `run.json.stages.s4=done` with Plan/active-pointer refs plus Blueprint/config SHA-256 anchors; no workspace output.
- Focused tests named in `tasks.md`, then `uv run pytest -q`; four existing `uv run nepa lint` gold commands; `openspec validate m1-4c-s4-controller-and-initial-plan-publication --strict`; `openspec validate --all --strict`; `git diff --check`.

## Required implementation signatures

- `S4Controller.run(context: StageContext) -> StageResult`
- `S4Controller.verify_completed(store: RunStore) -> None`
- `verify_m1_4a2_handoff(lineage_root, packaged_prompts) -> ApprovedArchitecturePromptBundle`
- `complete_plan_candidate(plan_draft_ir, constraints, frozen_refs, manifest, config_snapshot) -> CandidateCompletion`
- `publish_initial_plan(store, candidate_completion) -> StageResult`
- Existing role registry update: TaskPlanner adds `planning_budget`; TaskPlanner, PlanCritic, and FlatPlanBaseline bind the new/existing closed output contracts.

## Authoritative sections

- `project_docs/system_design.md` 7.1.1: §4.5, §4.7-§4.8, §5.2, §5.6.5, §6.4.1-§6.4.8, §8.3-§8.4, §8.8, §10.2 M1-4c, D1.0/D1.4/D1.6/D1.8-D1.11.
- `project_docs/pipeline_design_s4_s9.md` 1.2.0: §2, §5.1-§5.3. M1-4d/e details in §3-§4 and §6-§7 are boundary references only and are not implemented here.
