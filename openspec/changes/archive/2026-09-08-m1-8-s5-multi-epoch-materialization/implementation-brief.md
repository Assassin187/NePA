# M1-8 implementation brief

This is a derived implementation index for `m1-8-s5-multi-epoch-materialization`.
It does not add or alter design decisions. The normative sources are
`project_docs/system_design.md`, `project_docs/pipeline_design_s4_s9.md`, this
change's proposal/specs/design, and the accepted M1-7 artifacts.

## Inputs and Schema references

- Frozen run inputs: `spec/spec.json` (Spec IR), `inputs/target.json` (Target
  Profile), `inputs/test_bundle.json` (Test Bundle), and the Run/config snapshot
  anchored by `run.schema.json`.
- Active Plan and pointer: `plan/versions/plan-<C.A.P>.json` and
  `plan/active_plan.json`, validated by `plan.schema.json` and
  `active-plan.schema.json`.
- Current execution state: `plan/plan_state.json`, `plan/file_ledger.json`,
  and `plan/revision_ledger.json`, validated by `plan-state.schema.json`,
  `file-ledger.schema.json`, and `revision-ledger.schema.json`.
- Epoch and version bindings: `plan/epochs/E<n>/receipt.json` and
  `plan/bindings/<version>/receipt.json`, validated by
  `epoch-receipt.schema.json` and `binding-receipt.schema.json`.
- E1+ admission additionally consumes the accepted F3 activation payload,
  frozen migration rows/pending groups, the immediate predecessor checkpoint,
  predecessor receipt/binding, current workspace/file facts, and the
  recomputed Delivery Blueprint.
- F2 binding consumes an accepted F2 Plan/ref, its frozen inputs, the current
  accepted epoch receipt, and unchanged workspace/file facts; it does not
  consume or create a materialization attempt.

## Outputs and acceptance commands

- Epoch staging: `plan/epochs/E<n>/pending.json`, validated by
  `s5-pending-state.schema.json`.
- Epoch evidence and receipts: epoch-scoped build/smoke results,
  `plan/epochs/E<n>/receipt.json`, version-scoped manifest/map and binding
  receipt, projected/current file ledger and manifest/map, the current Run S5
  instance, and one matching `epoch_materialized` event.
- F2 binding outputs: immutable
  `plan/bindings/<version>/{artifact_manifest,contract_map,receipt}.json` and
  the atomic current manifest/map copies; workspace, Git, epoch receipt, S5
  history and events remain unchanged.
- Acceptance: focused Schema, revision/file-ledger, materialization, F2,
  publication, recovery and replay tests; `uv run pytest -q -m s5_epoch`;
  existing S4/S5/S6 ordinary/F1 and metrics markers; configured real sandbox;
  `uv run ruff check .`; `uv run mypy nepa`; public Schema/gold/target/Plan
  lint; `openspec validate m1-8-s5-multi-epoch-materialization --strict`;
  `openspec validate --all --strict`; and `git diff --check`.

## Intended interfaces

- Generalize the existing `S5MaterializationController.run`, `reconcile`,
  and completed-instance validation path around the current `E<n>` context;
  keep the existing controller registration and E0 behavior.
- Generalize the existing pure rendering, manifest, contract-map and file-ledger
  projections to accept the active Plan/version and epoch instead of E0-only
  constants.
- Provide pure Mapping-based projections equivalent to
  `build_epoch_context(...)`, `plan_epoch_materialization(...)`,
  `project_file_ledger(...)`, `validate_completed_epoch(...)`,
  `attribute_pending_repair(...)`, and `project_version_binding(...)`.
- Generalize the existing RunStore S5 publish/recovery boundary and Git
  checkpoint helper so E1+ uses one ordinary descendant commit while E0 keeps
  the one-time initialization path.
- Generalize typed `epoch_materialized` append/validation/deduplication so it
  binds the accepted active F3 revision without changing revision sequence or
  active pointer.
- Keep S5 deterministic and protocol-neutral: no LLM, Fixer, repair-group
  execution, trigger/operator, activation producer, public CLI switch, or new
  runtime dependency.

## Governing sections

- `project_docs/system_design.md`: §§4.7–4.8, 5.2.4–5.2.5, 5.4, 5.6.5.1–5.6.5.5,
  5.6.7, 6.5, 10.2.1–10.2.2, and 10.8.
- `project_docs/pipeline_design_s4_s9.md`: §§3.1–3.5, 4.1–4.4, 5.4,
  6.4, and the M1-8 recovery/publication rules.
- This change: proposal, both delta specs, and design decisions 1–8.

The brief deliberately does not claim implementation completion, owner approval,
production calibration, automatic activation, M1-9 repair-group execution, or
any M1-10/M1-11 producer behavior.
