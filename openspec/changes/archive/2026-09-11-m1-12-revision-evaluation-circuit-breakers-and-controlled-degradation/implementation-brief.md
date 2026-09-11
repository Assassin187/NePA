# M1-12 implementation brief

## Input artifacts and Schemas

- Accepted active Plan v5, Plan State v2 and state-history v1; immutable predecessor/successor Plans and the active pointer.
- Revision ledger v2, revision candidate/gate/rehearsal artifacts, migration report v1, epoch/binding receipts, verification WAL and task/joint evidence.
- Frozen Run v4 configuration (`revision_f2_limit`, `revision_f3_limit`, global time/cost and `s6_total_attempts_cap`), Delivery Blueprint/contract map/file ledger and canonical LLM trace `output_path` rows.

## Outputs and acceptance commands

- One activation-bound, idempotent `revision_evaluated` ledger fact; pure revision availability; S6 controlled termination through the existing request/S9 path; unchanged metrics formula.
- Local selectors from `tasks.md`, then `uv run pytest -q -m revision_mechanism`, `uv run pytest -q -m metric_contract`, and the explicitly listed affected test modules only. An unselected repository-wide pytest command is forbidden by the accepted M1-12 execution constraint.
- `uv run ruff check .`, `uv run mypy nepa`, public Spec/gold/Target/Test Bundle/Plan lints, strict current/all OpenSpec validation and `git diff --check`.

## Intended function and class signatures

- `project_revision_availability(revision_ledger, config_snapshot) -> dict[str, Any]`
- `derive_revision_obligation_scope(from_plan, to_plan, activation) -> dict[str, list[str]]`; the anchors helper is a thin projection of this result.
- `project_revision_evaluation(*, activation, trigger, obligation_anchors, affected_task_uids, state, state_history, current_signatures, hard_budget_exhausted, evidence_refs, call_rows, ledger_prefix_sha256) -> dict[str, Any] | None`
- `append_revision_evaluated(ledger, *, revision_seq, evaluated_at, obligation_anchors, resolved, ineffective, evidence_refs, call_refs, cost_usd) -> dict[str, Any]`
- Existing `S6ExecutionController` gains only private evaluation-readiness, independent-work and call-cap queries; public Stage/CLI/config interfaces remain unchanged.

## Governing sections

- `project_docs/system_design.md` §4.7, §4.8, §5.2.4, §5.6.7, §6.6, §9.1.2, §9.1.4, §10.2.1–§10.2.2, §10.8 and D1.10/D1.13/D1.16.
- `project_docs/pipeline_design_s4_s9.md` §4.3–§4.4, §5.6–§5.6.1, §6.1.1, §6.3–§6.5, §7.1, §7.3–§7.4 and §9.
- This change's proposal, four delta specs and design decisions 1–8. M1-13 through M1-15, PlanReviser calls, TR-9, S7/S8/M2 and historical migration are excluded.
