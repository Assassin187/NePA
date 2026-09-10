# M1-11 implementation brief

## Inputs and Schemas

- Accepted M1-10 `revision_handoff`, selected `trigger_evaluated` entry and immutable `plan/_s4r/candidate_<event_seq>/` bundle: `revision-trigger-evaluation.schema.json`, `revision-candidate.schema.json`, `revision-patch.schema.json`, `migration-report.schema.json`.
- Current `plan/active_plan.json`, immutable Plan, Plan State, file/revision ledgers, current binding/manifest/map, Run and workspace HEAD/tree: `active-plan.schema.json`, `plan.schema.json`, `plan-state.schema.json`, `file-ledger.schema.json`, `revision-ledger.schema.json`, `binding-receipt.schema.json`, `artifact-manifest.schema.json`, `contract-map.schema.json`, `run.schema.json`.
- Frozen configuration, PlanCritic contract and existing S5/S6 materialization/verification inputs: `plan-critic-result.schema.json`, `epoch-receipt.schema.json`, `verification-pending.schema.json`.

## Outputs and Acceptance

- Candidate-local `gates.json`, F3-only `rehearsal.json`, and v2 `activation.json`; typed `candidate_rejected` or `revision_activated`; immutable successor Plan and F2 binding or F3 pending-materialization projection; migrated State/file ledger and updated active pointer/Run projection.
- Focused acceptance: `uv run pytest -q -m revision_mechanism`, affected S4/S5/S6/RunStore/config/Schema tests, strict current/all OpenSpec validation.
- Public acceptance: `uv run pytest -q tests/test_schema_examples.py`, `uv run pytest -q`, `uv run ruff check .`, `uv run mypy nepa`, public Spec/Target/Test Bundle/Plan lint, `git diff --check`.

## Intended Signatures

- Pure revision helpers in `nepa.speclib.revision_mechanism`: frozen-boundary validation, ordered gate projection, rework-cost estimation, PlanCritic delta projection, rehearsal validation, rejection/activation payload construction.
- `S6ExecutionController` internal handoff consumer: `(StageContext, selected_event_seq, candidate_ref) -> rejected | activated_f2 | activated_f3`, leaving S6 pending for the accepted next view.
- `RunStore.activate_revision(...)`: publish one fully prepared F2/F3 transaction using a v2 candidate-local WAL; `RunStore.recover_revision(...)`: reconcile all pending activation WALs by active-pointer state before Stage admission.
- Existing PlanCritic binding and S5 materialization functions retain their public call shapes; the former accepts the revision delta closure through its existing three inputs, and the latter can operate against an explicit isolated workspace root without publishing accepted artifacts.

## Governing Sections

- `project_docs/system_design.md`: §4.7, §4.8, §5.2.4, §5.6.7, §6.4.6, §6.5–§6.6, §8.3, §10.2.1–§10.2.2, §10.8, D1.12, D1.13 and D1.15.
- `project_docs/pipeline_design_s4_s9.md`: §2–§6.5 and §7.1–§7.3.
- Change design decisions 1–9 and all delta requirements. M1-12 evaluation/circuit breaking and M1-14 PlanReviser production are not part of this brief.
