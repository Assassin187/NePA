## Why

M1 cannot execute or recover S4-S6 work until NePA has a deterministic, durable run lifecycle that freezes inputs and configuration, enforces budgets, atomically records stage state, and distinguishes planned stops, controlled process exits, and internal errors. M0 is complete and its signed gold-input baseline is unchanged, so M1-1 is the first independently verifiable M1 foundation to implement now.

## What Changes

- **Milestone/work item:** implement only M1-1 from §10.2, grounded in §4.4, §4.7, §4.8, §5.4, §5.6.2, §6.9, and §8.2-§8.3.
- Add layered configuration loading and a secret-free canonical configuration snapshot suitable for sealing into Run v3.
- Add spec-run initialization that preserves the caller Spec IR bytes, freezes the validated Target Profile and canonical Test Bundle into the run directory, and records independent source/content hashes.
- Add a single durable run store for canonical JSON, atomic replacement, hash-bound output references and receipts, stage events, and Run v3 state transitions.
- Add the deterministic orchestrator core for S4-S6 sequencing, active wall-clock and provider-usage budget accounting, planned-stop finalization, controlled-exit requests, internal-error finalization, and idempotent resume after a dead controller.
- Add the minimum deterministic S9 core needed by M1 so expected S4-S6 process failures produce a Schema-valid partial Report v2 with explicit artifact availability and the exact persisted termination reason.
- Add positive, negative, and crash-window tests for configuration snapshots, input freezing, atomic persistence, stage transitions, budget boundaries, termination branches, receipt/hash verification, and resume behavior.
- Preserve the existing M0 lint commands and signed/frozen gold assets without changing their public behavior or bytes.
- **Out of scope:** M1-2 provider/LLM behavior and trace production; M1-3 agents and prompts; Plan/Plan State, Delivery Compiler, S4 planning, S5 scaffolding, S6 coding, M1-7 CLI command exposure, full Reporter behavior, protocol tests, runner/oracle/adapters, S7/S8, or any M2 capability.

## Capabilities

### New Capabilities

- `run-lifecycle`: Deterministic spec-run initialization, durable Run v3 persistence, stage and budget control, controlled termination, minimal partial reporting, and crash-safe resume for the M1 S4-S6 runtime foundation.

### Modified Capabilities

- None.

## Impact

- **Verified prerequisites:** the archived M0 change is complete; current M0 tests and all three gold lints pass; `configs/m0-default-inputs.freeze.yaml` is confirmed and its recorded hashes match the current files. The existing sandbox digest record is accepted as recorded evidence and is not rebuilt by this change.
- **Expected code paths:** new `nepa/config.py`, `nepa/run_store.py`, `nepa/orchestrator.py`, the minimum `nepa/stages/s9_report.py` path, directly required Schema/example updates under `nepa/schemas/`, and focused tests under `tests/`. Existing canonical JSON and Schema validation behavior in `nepa/speclib/` should be reused rather than duplicated.
- **Dependencies:** add only design-required runtime dependencies actually needed by this work item, notably Pydantic v2 for configuration models. Provider HTTP, prompt rendering, and console dependencies belong to their later work items unless a direct M1-1 path requires them.
- **Downstream:** M1-2 through M1-7 will consume the persisted configuration, run-store, budget, stage, termination, and resume contracts. This change supplies infrastructure only and does not claim D1.1-D1.11 or M1 completion; it provides locally testable portions later exercised by D1.4, D1.9, and D1.10.
- **Manual gates:** this change has no independent responsible-owner signature gate. It must not claim or replace the M1-4a3 production-model and budget freeze required by D1.0.
- **Existing changes/public behavior:** there are no other active OpenSpec changes. The existing uncommitted `openspec/config.yaml` update is pre-existing user work and must be preserved.
