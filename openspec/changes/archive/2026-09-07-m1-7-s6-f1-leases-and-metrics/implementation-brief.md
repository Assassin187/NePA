# M1-7 implementation brief

This is a derived implementation index for `m1-7-s6-f1-leases-and-metrics`; it does not add or alter design decisions. The authoritative inputs remain `project_docs/system_design.md`, `project_docs/pipeline_design_s4_s9.md`, the archived M1-6 artifacts, and this change's proposal/specs/design.

## Inputs and schemas

- Active Plan, Plan State v2, file ledger v2, revision ledger v2, E0/binding receipts, frozen config and S6 attempt/evidence artifacts.
- New/updated schemas: `lease-authorization.schema.json`, `joint-evidence.schema.json`, `verification-pending.schema.json`, `s6-attempt.schema.json`, `task-evidence.schema.json`, `revision-ledger.schema.json`.
- Accepted stage/build/smoke receipts, immutable candidate/evidence refs, Git baseline/tree and controller lease authorization input.

## Outputs and acceptance

- Lease authorization, per-member Task Evidence, Joint Evidence v2, lease-aware verification WAL, typed lease/verification events, joint commit and all-member State/file-ledger projections.
- Pure M1 metrics object and read-only run-directory adapter; no `eval runs` command and no run mutation.
- Acceptance: `uv run pytest -q`; `uv run pytest -q -m s6_execution`; `uv run pytest -q -m s6_lease`; `uv run pytest -q -m metric_contract`; ruff; mypy; schema examples; gold/target lint; strict current/all OpenSpec validation; `git diff --check`.

## Required signatures

- `validate_lease_authorization(authorization, *, plan, state, file_ledger, revision_ledger, config_snapshot, baseline_commit, baseline_tree) -> dict`.
- `project_s6_context(..., lease_authorization=None, leased_files=None) -> tuple[dict[str, str], dict[str, int]]`.
- `normalize_candidate(value, task, file_ledger=None, *, leased_paths=()) -> dict[str, bytes]`.
- `append_lease_started(ledger, *, task_uid, leased_uids, leased_paths, baseline_commit, execution_count, authorization_ref) -> dict` (derives `lease-<event_seq>`).
- `append_lease_finished(ledger, *, lease_id, success, reason, joint_evidence_ref, commit_sha, call_refs) -> dict`.
- Generalized `append_verification_committed` accepting normal or lease member/evidence bindings.
- `compute_m1_metrics(inputs: Mapping[str, Any]) -> dict[str, Any]` and `compute_run_metrics(run_dir: Path) -> dict[str, Any]`.
- `S6ExecutionController(..., lease_authorization_provider: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None)` and matching `build_orchestrator` injection seam.

## Governing sections

`project_docs/system_design.md` §§4.7–4.8, 5.2.4–5.2.5, 5.4, 5.6.7, 6.6, 9.1.4, 10.2.1–10.2.2, 10.8; `project_docs/pipeline_design_s4_s9.md` §§3.1–3.4, 5.6, 6.5, 7.1–7.3, 9; this change decisions 1–9 and all six delta specs.

The brief deliberately contains no TR/F2/F3/E1+/M2 producer, ArchitecturePlanner change, public lease option, or new acceptance gate.
