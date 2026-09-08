## Why

M1-7 can preserve and verify implemented task-owned files within the initial E0 workspace, but the runtime still has no legal way to materialize an already-activated structural Plan into E1+, preserve prior implementation, or bind an F2 metadata-only Plan version without rewriting its epoch. M1-8 is the next serial work item and must close that deterministic multi-epoch materialization and binding boundary before M1-9 can validate migration repair groups.

## What Changes

- Generalize the existing S5 instance boundary from E0-only execution to an epoch-keyed controller that admits an already-activated legal F3 Plan and frozen migration inputs, recomputes its Blueprint, and materializes only the structural difference from the preceding accepted epoch.
- Deterministically render new or changed `s5_frozen` files and new `s6_owned` stubs while preserving every retained realized task-owned byte; move retired realized files to the ledger-declared `_orphan/<epoch>/...` path instead of deleting them, and support explicit `re_adopt` without claiming completion before later revalidation.
- Build every default variant for E1+. Publish `ready` only when all required checks pass; publish `pending_repair` only when every observed incompatibility maps mechanically to a frozen registered migration group, retaining the failing build refs and pending group ids. Reject unregistered, ambiguous, template, tool, or structural failures rather than treating them as expected repair work.
- Publish one ordinary (non-`git init`) checkpoint, immutable epoch receipt, current-version binding, manifest/map, file-ledger projection, Run S5 instance, and idempotent `epoch_materialized` event for each accepted E1+ instance, with forward-only crash reconciliation and zero-change replay.
- Add a pure F2 metadata-binding path that recomputes owner/provider, Blueprint, manifest, contract-map and Plan refs for the new version while reusing the accepted epoch receipt and leaving workspace bytes, git checkpoint, epoch receipt and S5 stage history unchanged.
- Extend the existing `s5_epoch` acceptance fixtures for MQTT and non-MQTT inputs to cover ready and pending-repair E1+, preservation, retirement/quarantine, `re_adopt`, metadata-only F2 rebinding, unregistered failure, idempotence and publication crash windows.
- **Out of scope:** M1-9 AMEND/REVALIDATE/REGENERATE execution and joint repair-group evidence; M1-10 trigger evaluation and patch operators; M1-11 candidate gates or automatic F2/F3 activation; M1-12 circuit breakers; M2 test assets/S7/S8; production calibration; public CLI switches; edits to `project_docs/` or the selected ArchitecturePlanner lineage.

## Capabilities

### New Capabilities

- `s5-multi-epoch-materialization`: Deterministic E1+ Blueprint-difference materialization, realized-file preservation, retirement/re-adoption, ready versus registered pending-repair classification, epoch publication, recovery and replay.

### Modified Capabilities

- `plan-revision-infrastructure`: Produce and validate E1+ materialization events, cross-epoch file-ledger transitions and metadata-only F2 version bindings without rewriting an epoch or workspace.

## Impact

- **Milestone/work item:** M1-8 only. Governing sections are `project_docs/system_design.md` §5.6.5.4, §5.6.7, §6.5, §10.2.1-§10.2.2 and `project_docs/pipeline_design_s4_s9.md` §3.3, §3.5, §4.1-§4.4, §5.4 and §6.4.
- **Prerequisites:** the M1-7 archive exists with completed task records, but its implementation is still present as uncommitted workspace changes and its current Schema/S5/S6/ledger/Git/real-sandbox baseline has not been re-run in this change. Those checks are the first implementation gate. M1-8 has no independent owner-signature gate and does not satisfy the later M1-13/M1-14/M1-15 approvals.
- **Affected paths:** the existing S5 controller and materialization projections, RunStore S5 publication/recovery, Run/S5 pending/epoch/binding/file-ledger Schemas and examples, typed revision-event helpers, application/orchestrator S5 instance handling, and frozen `s5_epoch` fixtures/tests.
- **Public contracts:** Run's current S5 instance becomes `E<n>` rather than E0-only; epoch receipts admit `ready` or `pending_repair`; file-ledger quarantine/re-adoption and E1+ event/binding consistency become active behavior. No new public CLI or LLM interface is added.
- **Frozen behavior:** accepted Plan versions, prior epoch receipts/bindings/evidence, prior realized task-owned bytes and M1-7 ownership/lease semantics remain immutable. The renderer, diff classifier and fixtures remain protocol neutral and use only frozen structured inputs and declared migration facts.
- **Dependencies:** reuse the current Delivery Compiler, materialization renderer, build/smoke sandbox, Git checkpoint, canonical Schema/ref helpers, typed ledger and stage-lock paths. Add no runtime dependency unless implementation proves the authoritative contract cannot be met with existing assets.
- **Acceptance/DoD:** satisfy M1 D1.12's multi-epoch idempotence and path/ledger consistency plus the M1-8 task-card checks: `uv run pytest -q -m s5_epoch`, focused multi-epoch/F2/recovery regressions, full public CI, strict current/all OpenSpec validation and `git diff --check`. Required cases include zero-change replay, no lost old file, unregistered-failure rejection and F2 receipt reconciliation.
