## Why

M1-4c can publish only the initial Plan and placeholder ledgers; it cannot yet preserve completed work while task position ids, ownership, interfaces, or Plan versions change. M1-4d is required now to make later F2/F3 revision safe, deterministic, auditable, and crash-recoverable before M1-4e is allowed to activate any production revision.

This change follows `project_docs/system_design.md` 7.2.0 and `project_docs/pipeline_design_s4_s9.md` 1.3.0. Those documents were revised and finalized before this change; modifying either design document is explicitly outside this change.

## What Changes

- **BREAKING** Require every internal architecture contract to declare closed `exports[]` interface signatures, and rebuild the ArchitecturePlanner `initial`/`repair` baseline in a new lineage before the updated S4 contract may be used in production.
- **BREAKING** Extend every linked Plan task with controller-derived `task_uid`, `obligation_digest`, and `guidance_digest`, while proving those fields do not enter the Delivery Blueprint semantic projection.
- **BREAKING** Replace the initial-only active pointer, file ledger, and revision ledger contracts with their general version-chain forms; the file ledger uses the authoritative `files` key rather than the interim `entries` key.
- Add deterministic task/file migration classification for `INHERIT`, `REVALIDATE`, `AMEND`, and `REGENERATE`, including explicit split/merge lineage, Plan State projection, and `preservation_rate`.
- Add append-only revision-chain validation, C.A.P version and epoch rules, a single locked activation transaction, and WAL-driven crash recovery around Plan/State/ledger/pointer/Run updates.
- Update S4 initial publication and state/execution lint so the immutable S4 Plan anchor remains 1.0.0 while the independently hash-bound active pointer and Plan State may advance through a proven revision chain.
- Add schema/example, deterministic replay, tamper, protocol-neutrality, and fault-injection acceptance coverage for the complete M1-4d boundary.

## Capabilities

### New Capabilities

- `plan-revision-infrastructure`: Deterministic migration classification, Plan State/file-ledger projection, append-only revision-chain validation, version/epoch activation, and crash recovery.

### Modified Capabilities

- `planning-architecture-infrastructure`: Internal contracts must declare closed, mechanically attributable exported interface signatures.
- `architecture-prompt-development`: The changed ArchitecturePlanner output contract requires a new bounded V0-V2 baseline lineage, protocol-neutrality evidence, and owner approval before production handoff.
- `plan-compilation-validation`: The Linker must derive stable task identity and obligation/guidance digests without changing Blueprint semantics.
- `plan-state-validation`: Snapshot and execution validation must bind the current active Plan and accept inherited/revalidated evidence only through a valid revision lineage.
- `s4-initial-plan-publication`: Initial publication must emit the M1-4d-complete Plan and general ledger/pointer contracts while preserving 1.0.0 as the immutable S4 anchor.

## Impact

- **Milestone and authority:** M1-4d; authoritative sections are system design 7.2.0 §4.3-§4.4, §5.2, §6.4.5-§6.4.7, §10.2 M1-4d and pipeline design 1.3.0 §3, §4, §5.3, §6.4-§6.5.
- **Verified prerequisites:** archived M1-4b/M1-4b2 provide the existing Linker, Blueprint compiler, Plan/State validators and schemas; archived M1-4c provides initial S4 publication, typed seals and resume behavior. Their focused regression baseline currently passes.
- **Unverified gate:** the former M1-4a2 owner approval covers the older architecture contract. A new lineage reaching at least 2/3 for one V0-V2 version, passing protocol-neutrality checks, and receiving a new recorded owner approval is an independent blocking task in this change; automation must not mark it approved.
- **Why the baseline refresh is inseparable:** without updated `exports[]` output, production S4 cannot produce the interface signatures required by M1-4d digests or migration decisions. The refresh changes no model routing or calibration policy and exists only to close the revised M1-4d contract.
- **Affected paths:** architecture/Plan/ledger/event schemas and examples; architecture validation and prompt bindings; `nepa/speclib/plan.py`, `nepa/speclib/plan_state.py`, a focused revision speclib module, `nepa/run_store.py`, and `nepa/stages/s4_planning.py`; focused and end-to-end tests.
- **No new dependency:** use existing canonical JSON, jsonschema, filesystem atomic-write, lock, and hashing paths.
- **Non-scope:** no `project_docs/` edits; no TR-1-TR-9 evaluator, PlanReviser, patch application, RG-1-RG-5 orchestration, revision budget policy, F1 lease controller, S5 materialization/quarantine operation, S6 task execution loop, S9 metrics, CLI, protocol-specific branch, or compatibility shim for pre-7.2.0 interim artifacts.
- **Acceptance:** schema/example mutual validation, deterministic identity/migration/chain tests, crash injection at every activation boundary, existing S4/Plan/State regressions, full pytest and gold lint, protocol-neutrality scans, strict current/all OpenSpec validation, diff inspection, and the separate owner-signature gate above.
