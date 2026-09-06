## Why

M1-4d stops after S4 publishes an immutable Plan, an active pointer, and slot-only ledgers; NePA still cannot turn that sealed contract into a buildable workspace. M1-5 is the next serial work item and must provide the deterministic, protocol-neutral S5 E0 materialization and recovery boundary before M1-6 may execute any coding task.

## What Changes

- Add S5 admission that requires a completed and valid S4 seal, rereads the frozen Spec/Target/Test Bundle, recomputes Delivery Constraints and the Blueprint through the existing compiler, and runs basic/full Plan lint before the first workspace side effect.
- Derive a canonical S5 rendering view without rewriting the sealed Plan: parse the supported C99 declarations, resolve every interface and function implementation slot uniquely from declared contracts/tasks/Blueprint links, order declaration dependencies, and reject missing or ambiguous inputs with structured diagnostics.
- Materialize the E0 workspace through one protocol-neutral template path: render frozen declarations/mechanical files/build files, create `s6_owned` stubs that return the derived NOT_IMPLEMENTED result, and generate the unique Blueprint-bound POSIX entry loop that survives until SIGTERM.
- Run all default C99 build variants and per-variant executable smoke checks in the configured sandbox. E0 may be sealed only when compilation is warning-free, sanitizer output is clean, and every executable satisfies the dwell/SIGTERM contract.
- Publish the initial git checkpoint, artifact manifest, contract map, file-ledger realization, immutable epoch receipt, immutable version binding, current manifest/map copies, S5 Run output refs, and one idempotent `epoch_materialized` ledger event through a crash-recoverable transaction.
- **BREAKING** Move fresh-run Run/file-ledger/revision-ledger persistence to the current design contracts needed by S5 (Run v4 and v2 ledgers with typed events). Existing runs remain historical inputs for their matching implementation version and are not upgraded or mixed with fresh runs.
- Add the `s5_epoch` acceptance suite and first M1 schema/ruff/mypy/pytest CI path, covering two frozen S4-produced protocol fixtures, deterministic replay, protocol neutrality, negative renderability cases, build/smoke failure, and interruption before/after checkpoint and receipt publication.

## Capabilities

### New Capabilities

- `s5-e0-materialization`: Deterministic renderability preflight, E0 workspace generation, sandboxed build/smoke validation, manifest/map and receipt publication, idempotence, and crash recovery.

### Modified Capabilities

- `plan-revision-infrastructure`: Replace the initial revision-only ledger contract with the v2 typed event chain used by the first `epoch_materialized` producer, and complete v2 file-ledger semantics for S5-frozen realized files versus S6-owned slot-only stubs.
- `s4-initial-plan-publication`: Fresh S4 publication must emit the current empty v2 ledgers and typed Run anchors that S5 can consume, while preserving 1.0.0/E0/revision-sequence 0 and emitting no materialization event before S5 succeeds.

## Impact

- **Milestone/work item:** M1-5 only. Governing sections are `project_docs/system_design.md` 8.0.2 §5.2.1, §5.6.5.2-§5.6.7, §6.5, §7.4, §8.3, §8.5, §10.2.1-§10.2.2 and `project_docs/pipeline_design_s4_s9.md` 2.0.2 §3.3, §4.2-§4.4, §5.2, §5.4-§5.5.
- **Prerequisite status:** M1-4d is recorded as completed in the archived `m1-4d-plan-version-and-migration-infrastructure` change. This proposal does not re-audit that baseline; implementation must run its focused regressions before relying on it and must report any concrete principle-level blocker under §10.2.1.
- **Affected paths:** the existing orchestration/RunStore/S4 handoff path; C99 config and sandbox execution; a focused S5 stage and rendering/tool path; Run, file/revision-ledger, manifest/map, receipt, build/smoke evidence Schemas/examples; frozen MQTT and non-MQTT S4 fixture artifacts; focused and CI tests.
- **Dependencies:** add no runtime dependency; reuse canonical JSON, jsonschema, Jinja2, subprocess/docker, git, the existing Delivery Compiler, stage lock/staging conventions, and atomic file publication. If absent from the current project metadata, add only the design-mandated development/CI tools ruff and mypy as locked development dependencies.
- **Upstream behavior:** no new S4 gate, no change to the selected ArchitecturePlanner bundle or its input/output contract, no new architecture experiment, and no mutation of a sealed Plan. The only S4 change is the minimum fresh-run persistence handoff required by the first v2 consumer.
- **Downstream behavior:** M1-6 receives only a `ready` E0 binding/checkpoint with S6-owned rows still slot-only. Plan State initialization and all Coder/Fixer execution remain downstream.
- **Non-scope:** E1+ differential materialization/quarantine/pending-repair, F2 metadata-only rebinding, F3 activation, S6 task execution or leases, PlanReviser/triggers/RG gates, Test Bundle assets or protocol tests, new CLI parameters, production parameter calibration, and any `project_docs/` edit.
- **Acceptance:** `uv run pytest -m s5_epoch`; focused schema, S4 handoff, RunStore and protocol-neutrality regressions; public CI checks from §10.8; `openspec validate m1-5-s5-e0-materialization --strict`; `openspec validate --all --strict`; and `git diff --check`. No owner signature is required for M1-5; the existing ArchitecturePlanner handoff record is preserved rather than reapproved.
