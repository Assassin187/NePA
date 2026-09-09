# M1-10 Implementation Brief

## 1. Inputs and Schema references

- Accepted prerequisite: archived M1-9 change at `openspec/changes/archive/2026-09-09-m1-9-s6-migration-joint-verification/`; implementation/finalization baseline `d77bc83`, with the focused pre-change command completing as `174 passed in 434.63s`.
- Immutable design inputs: `project_docs/system_design.md` §5.2-§5.2.5, §5.6.7, §6.6, §8.3, §10.2.1-§10.2.2 and §10.8; `project_docs/pipeline_design_s4_s9.md` §2-§4, §5.6, §6.1-§6.5 and §7.2.
- Existing persisted inputs: current active formal Plan and Plan State, Delivery Blueprint and contract map, revision ledger, file ledger, workspace commit/tree, S6 attempt/WAL/build/diagnosis evidence and frozen revision thresholds.
- Changed/new Schema inputs: formal Plan Schema 5.0; closed `revision-trigger-evaluation` 1.0; closed `revision-patch` 1.0 (including explicit build-artifact bindings for added/re-adopted link-source slots); closed `revision-candidate` 1.0; closed singular `trigger_evaluated` payload in the revision-ledger Schema.
- Concrete baseline mismatch: formal Plan 4.0 does not persist `local_task_id`, although `task_uid` is derived from `[work_package, local_task_id]`. A formal Plan therefore cannot losslessly reconstruct package-local dependency references for PlanDraftIR revision. The minimum correction is required Plan 5.0 `local_task_id`, with no v4 conversion and no change to Run Schema 4.0 or unrelated contracts.

## 2. Outputs and acceptance commands

- Formal Plan 5.0 output, lossless Plan-to-PlanDraftIR projection and one shared initial/revision candidate-completion path.
- One canonical trigger evaluation containing authority anchors and ordered hits; one singular typed ledger event per hit, appended as one atomic idempotent batch.
- One closed frozen F2/F3 patch and, when supplied, one event-scoped immutable candidate bundle under `plan/_s4r/candidate_<event_seq>/` with `candidate.json` as commit marker.
- Candidate bundle references completed Plan, Blueprint, manifest, contract map, lineage/obligation mapping, INV-1/2/3 reports, full lint and migration classification. Its flow ends in an internal `revision_handoff`; it creates no formal version, rejection, gate or activation.
- Focused acceptance: `uv run pytest -q -m revision_mechanism`; affected Schema, Plan/Linker/Delivery, revision/State, RunStore/S5/S6 collections.
- Public acceptance: `uv run pytest -q`; `uv run ruff check .`; `uv run mypy nepa`; public Spec/gold/Target/Test Bundle/Plan lint; `openspec validate m1-10-revision-triggers-and-patch-operators --strict`; `openspec validate --all --strict`; `git diff --check`.

## 3. Intended function and class signatures

- `plan_to_draft_ir(plan: dict[str, object]) -> dict[str, object]`
- `project_revision_boundary(...) -> dict[str, object]`
- `evaluate_revision_triggers(boundary: dict[str, object], revision_ledger: dict[str, object]) -> dict[str, object]`
- `append_trigger_batch(ledger: dict[str, object], evaluation: dict[str, object]) -> dict[str, object]`
- `apply_revision_patch(source_ir: dict[str, object], patch: dict[str, object]) -> dict[str, object]`
- `complete_revision_candidate(...) -> dict[str, object]`
- `RunStore.stage_revision_candidate(...)`, `RunStore.commit_revision_candidate(...)`, `RunStore.reconcile_revision_candidate(...)`
- `StagePause(kind: Literal["revision_handoff"], selected_event_seq: int, candidate_ref: str | None = None)`; `StageResult.pause: StagePause | None`
- `S6ExecutionController(..., revision_patch_provider: Callable[..., dict[str, object] | None] | None = None)` and matching optional `build_orchestrator(...)` argument. The provider is an internal frozen-patch seam and does not invoke an Agent.

## 4. Governing flow and section mapping

- S6 accepted boundary/WAL facts → `project_revision_boundary` → `evaluate_revision_triggers`: system design §5.6.7 and §10.2.2; pipeline §5.6 and §6.1-§6.1.1.
- Evaluation → singular `trigger_evaluated` event batch → revision ledger: system design §10.2.1; pipeline §4.3 and §6.1.
- Selected trigger + frozen patch → `apply_revision_patch` → shared Plan completion → INV-1/2/3 → `classify_migration`: system design §5.2-§5.2.5 and §10.2.1-§10.2.2; pipeline §2-§3 and §6.2.
- Candidate stage/hash → ledger append → candidate commit/reconcile → M1-9 migration consumer-compatible artifacts: pipeline §4.1-§4.4 and §6.3-§6.5.
- S6 selected result → `StagePause("revision_handoff", ...)` → orchestrator S6 `pending`: system design §5.6.7 and §10.2.2. M1-11 RG/rejection/activation, M1-12 circuit breaker and M1-14 PlanReviser/Agent production are not outputs of this implementation.
