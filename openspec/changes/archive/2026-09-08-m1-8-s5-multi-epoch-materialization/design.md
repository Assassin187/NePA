## Context

See `proposal.md` for motivation and the two delta specs for normative behavior. The repository already has a deterministic E0-only `S5MaterializationController`, pure rendering/manifest/map/file-ledger helpers, epoch and binding receipt Schemas, build/smoke sandbox execution, Git checkpoint helpers, RunStore publication/recovery, typed `epoch_materialized` support, and M1-6/M1-7 realized-file and verification facts. Those paths are currently specialized around `E0`, `plan-1.0.0`, a fresh workspace and `materialization_status=ready`.

M1-8 must extend those same paths rather than create a parallel materializer. Its test inputs are frozen, artificially activated F3/F2 states because automatic triggers, patch production, revision gates and activation orchestration belong to M1-10/M1-11. M1-9 owns repair-group execution and is not required for accepting an E1 `pending_repair` checkpoint. The archived M1-7 record marks its tasks complete, but its code and main-spec deltas are still uncommitted in the current worktree and no M1-8 baseline run has yet verified them; implementation starts by proving that baseline and preserves all unrelated changes.

## Goals / Non-Goals

**Goals:**

- Turn the existing E0 implementation into one epoch-keyed deterministic S5 path while retaining E0 behavior.
- Make the old/new Blueprint and frozen migration facts the sole authority for workspace actions and ledger transitions.
- Preserve accepted task implementation and history across E1+, including explicit retirement and re-adoption.
- Publish and recover one auditable E1+ checkpoint/receipt/binding/event chain, including registered `pending_repair` outcomes.
- Provide the metadata-only binding operation required by future F2 activation without making that activation reachable.

**Non-Goals:**

- Do not implement migration classification, patch operators, trigger routing, revision gates or active-pointer advancement; fixtures supply their already-validated outputs.
- Do not execute AMEND, REVALIDATE, REGENERATE or pending repair groups and do not publish new task success evidence.
- Do not add a CLI switch, LLM role, retry budget, compatibility reader, runtime dependency, test assets or S7/S8 behavior.
- Do not edit authoritative design documents, architecture prompts, calibration lineage, frozen accepted artifacts or prior epoch/version receipts.

## Decisions

### 1. Generalize the existing S5 controller around an explicit epoch context

Replace E0 constants in the S5 controller, RunStore materialization transaction and completed validator with one immutable epoch context assembled after reconciliation. The context contains the active Plan/ref, active pointer, target epoch, accepted activation/migration refs, prior epoch receipt/binding/checkpoint, old/new expanded Blueprints, current State/file ledger/revision ledger, constraints and frozen inputs. E0 uses the same context with no predecessor and its existing fresh-run restrictions; E1+ requires an accepted F3 activation and the exact predecessor chain.

The public stage remains S5. `run.stages.s5` is the current `E<n>` projection and a completed prior instance is not reopened: activation supplies a new pending instance id before S5 admission, while immutable prior receipts retain history. M1-8 tests install a complete frozen post-activation state through internal fixture helpers; production M1-8 code never advances the pointer or manufactures that state.

Alternative considered: add a separate `S5EpochController`. Rejected because rendering, build, publication and recovery invariants would fork, and the design defines S5 as one epoch-keyed stage.

### 2. Compute one pure materialization plan before any workspace write

Add a filesystem-free projection, conceptually `plan_epoch_materialization(...)`, that consumes old/new expanded Blueprints, the new rendering view/bytes, file ledger and explicit migration rows and emits a canonical action plan. Actions are closed and path-sorted: render/replace an `s5_frozen` path, create a new `s6_owned` stub, preserve an existing realized owned path, retire a slot-only path, quarantine a realized path, or re-adopt one explicitly named quarantine path. Every old ledger row and new active path is consumed exactly once.

The projector rejects unsafe/colliding paths, implicit renames, missing migration rows, attempts to replace retained realized owned bytes, a quarantine target collision, an absent/mismatched re-adoption source, and any final active/quarantine set inconsistent with the new Blueprint. It also projects the post-checkpoint file ledger but leaves checkpoint/receipt refs as explicit late-bound inputs so no evidence is fabricated before commit.

For existing retained task-owned files, the renderer may derive the new metadata view but its generated stub bytes are ignored; preserved workspace bytes are the materialization input. New and changed frozen files use the existing deterministic templates. No action branches on protocol identity, suffix or free text.

Alternative considered: walk the workspace and decide per file while applying changes. Rejected because a partial write could influence later decisions and because undeclared files would become an accidental source of truth.

### 3. Treat retirement and re-adoption as explicit reversible moves

A realized retirement is represented by one source-to-quarantine move to `_orphan/<target-epoch>/<old-path>` and a `quarantined` ledger row retaining its prior content/checkpoint/verification and owner history plus `quarantined_in_epoch` and `quarantine_path`. Slot-only retirement removes only the known stub path and records no invented historical row. The active source-tree closure excludes only `.git`, declared build outputs and quarantine paths present in the projected ledger.

`re_adopt` must name the prior quarantine path, destination slot and new owner in the frozen migration input. Materialization moves the exact preserved bytes to the destination, removes the inactive quarantine identity from the current projection, records the explicit new owner/version lineage required by the migration, and leaves completion/verification pending. It cannot copy prior proof into a changed obligation or silently act as rename.

Alternative considered: leave retired bytes in their original paths and have build tools ignore them. Rejected because active-tree/Blueprint closure would become false and stale sources could still enter undeclared builds.

### 4. Classify build outcomes only after structural closure

Apply the complete action plan to an isolated/staged candidate rooted at the prior legal checkpoint, derive the current manifest/map, and validate path, declaration, implementation and build-graph closure before builds. Run every default build variant through the existing sandbox. If all builds pass, run the existing smoke checks and require all to pass for `ready`.

If a build fails, a pure attribution function compares structured compiler/linker evidence and changed contract/file ownership against the exact affected closures and group ids frozen in the activation. `pending_repair` is legal only when every failure maps uniquely into those declared closures; it stores all failing build refs, sorted unique pending group ids and no successful smoke claim. Ambiguous or out-of-closure errors are controlled materialization rejection. Template, sandbox, Git or deterministic-tool failures remain internal errors. S5 performs one materialization/build pass and no Fixer loop.

Alternative considered: treat every E1 build failure as pending repair. Rejected because it would hide template/framework failures and violate the requirement that the incompatibility be registered before materialization.

### 5. Reuse the E0 transaction shape with epoch-keyed staging and one new commit

Generalize the S5 pending Schema/path to `plan/epochs/E<n>/pending.json`. Before workspace mutation it records predecessor commit/tree, the epoch context refs, canonical action plan, expected before/after hashes or absence for every touched path, projected active/quarantine sets, build/smoke results, expected checkpoint and output refs. Installation checks every preimage before each confined write/move; unrelated files are never cleaned broadly.

The ordered E1+ transaction is:

1. reconcile activation/materialization/verification in the prescribed order, validate admission and persist the pending action plan;
2. install exactly the planned difference, validate tree closure, run builds and conditionally smoke, clean declared build outputs, and persist all observations;
3. create one ordinary checkpoint descendant with the existing fixed NePA identity and Plan/Epoch trailers; do not run `git init`;
4. publish immutable result evidence, epoch-scoped manifest/map, epoch receipt, version-scoped manifest/map and binding receipt, then atomically replace the file ledger and current manifest/map copies;
5. return epoch/binding refs for the Orchestrator's atomic current-S5 `done` update;
6. append/deduplicate the matching `epoch_materialized` event and remove pending state.

The workspace checkpoint is the materialization commit point; the Run update is epoch acceptance. The epoch receipt is built before the binding and does not reference a value that refers back to it. `pending_repair` changes only status/result/group fields in this publication shape; it is not a successful build receipt.

Alternative considered: commit only ready epochs and leave pending-repair work uncommitted. Rejected because M1-9 requires one stable E1 baseline and immutable failure evidence for group repair and recovery.

### 6. Recover against the current epoch and never roll back accepted history

Before checkpoint, recovery verifies the pending record and predecessor checkpoint, then reverses only action-listed paths whose current bytes/location match an expected intermediate state. It may recreate the candidate from the predecessor after preserving accepted call/evidence artifacts; any unrelated or conflicting path is artifact damage. It never resets to E0 when a later predecessor exists and never removes an accepted quarantine object.

After checkpoint, recovery verifies exact commit/tree/trailers and reconstructs only missing canonical evidence, receipt, binding, ledger/current-copy, Run or event suffixes. It does not rerender, rebuild, smoke or commit again. Once Run accepts the epoch, a missing materialization event is appended only after all accepted refs validate. Completed replay snapshots source/quarantine files, Git refs, artifacts, event count and timestamps and must observe zero change.

Alternative considered: use `git reset` to the predecessor for all pre-checkpoint failures. Rejected because it can erase untracked staged evidence or quarantine content and is broader than the recorded transaction boundary.

### 7. Implement F2 rebinding as a separate pure metadata transaction

Add a binding projector, conceptually `project_version_binding(...)`, over the accepted F2 Plan/ref, frozen inputs, its recomputed Blueprint/rendering metadata and the current accepted epoch receipt. It builds manifest/contract-map values with the new Plan version, owner/provider and Blueprint hash while sourcing file hashes from the unchanged accepted workspace. It validates that structural file paths/classes/build graph and epoch remain compatible with metadata-only rebinding; a structural difference is not legal F2 input.

Publication writes immutable manifest/map copies and receipt under `plan/bindings/<version>/`, then atomically refreshes the root current copies. It does not touch the S5 instance, epoch area, workspace, Git, file content, `epoch_materialized` events or historical binding. The future activation transaction owns when this helper is called and how its binding ref enters `revision_activated`; M1-8 exposes and directly tests the deterministic operation only. Exact existing bytes are idempotent; conflicting immutable bytes fail closed.

Alternative considered: route F2 through S5 with a no-op file diff. Rejected because it would falsely create or reopen an epoch instance and blur the distinct binding and materialization authorities defined by §5.6.7.

### 8. Freeze post-M1-7 multi-epoch fixtures without exposing a public activation seam

Extend the existing MQTT and non-MQTT S5 fixture families with version-controlled packages containing accepted E0 plus real M1-6/M1-7-shaped State, ledger, evidence and workspace history, followed by deterministic artificial F2/F3 Plan/activation/migration inputs. A developer generator may assemble these via existing Plan/migration/activation helpers in temporary run directories; CI consumes frozen artifacts and never regenerates them silently.

Cases include no-op replay, added frozen/owned slots, retained realized content, owner change, retirement, later re-adoption, immediately ready E1, registered pending-repair E1, unregistered failure, F2 rebinding and each pre/post-checkpoint publication fault. The test-only setup is not wired into CLI or application routing. Existing E0, S6 normal/F1 and metric markers remain regression gates.

Alternative considered: hand-author minimal JSON per test. Rejected because it could bypass the producer contracts M1-8 must consume and conceal cross-object binding defects.

### 9. Bind repair groups and re-adoption inside the canonical migration object

`revision_activated.migration` is the sole structural migration input. Its optional `pending_groups[]` rows use canonical `group_id`, sorted unique member task uids, affected paths, affected symbols and build artifact ids. Its optional `re_adopt[]` rows bind the quarantine path, target path, target task uid/id and preserved content hash. Both arrays are closed, sorted and duplicate-free; F2 forbids them. S5 consumes these exact rows and supports no aliases or activation-top-level fallback.

This concretizes the authoritative rule that group boundaries and re-adoption are frozen in the activation migration record without adding another artifact or changing `project_docs/`. Alternative considered: infer them from `patch_ops` or add a separate migration attachment. Rejected because either choice would introduce a second authority and a broader publication contract.

### 10. Reverse every pre-checkpoint action from recorded predecessor facts

Move recovery always reverses `target_path` to `source_path`, for both quarantine and re-adoption. A retired slot and a replaced file are restored from the recorded immediate predecessor checkpoint and checked against `before_sha256`; newly rendered or stub paths whose predecessor state was absent are removed only when their bytes match the recorded after hash. Preserve is a no-op. Recovery accepts only an exact recorded before or after state and verifies the resulting predecessor workspace closure before deleting pending state.

### 11. Validate the accepted chain before event repair or replay

Completed-instance validation has a read-only mode that allows only the materialization event to be absent. It verifies stage refs, active F3 lineage, checkpoint, epoch receipt, binding receipt, the version-scoped files named by the binding, epoch-scoped copies, current copies, file ledger, workspace and result evidence. Reconciliation may append a missing event only after that validation, then reruns validation with the event required. No pending record is removed on validation failure.

### 12. Attribute only complete structured compiler/linker diagnostics

The attribution projector returns a structured decision with `publishable`, sorted `group_ids` and a stable rejection reason. It rejects timeout, missing/nonzero-exit inconsistencies, sandbox/cleanup/command/template failures and unparsed error diagnostics. Every parsed compiler path or linker symbol must map to exactly one frozen group closure; one zero-match or multi-match diagnostic rejects the whole epoch.

## Risks / Trade-offs

- [The current M1-7 working tree may not match its archived acceptance record] → Run Schema, ledger, S5/S6, Git, marker and real-sandbox gates before M1-8 edits; report any pre-existing failure instead of folding unrelated repair into this change.
- [A compiler/linker diagnostic may not map deterministically to one registered group] → Fail the epoch rather than broaden attribution or use model interpretation; only exact structured path/symbol/closure evidence permits `pending_repair`.
- [A diff bug could overwrite valuable realized implementation] → Compute and validate the whole action plan first, require preimage hashes, preserve realized owned paths by default, and fault-test every action boundary.
- [Quarantine paths could accumulate across epochs] → Keep them because deletion is forbidden and history is auditable; only explicit `re_adopt` changes their active status.
- [Generalizing E0 code could regress fresh-run behavior] → Keep E0 as the no-predecessor branch of the same context and retain all existing E0 fixtures, byte/replay tests and error routing.
- [F2 binding metadata could disagree with unchanged source content] → Validate structural compatibility and declarations before publication; changed obligations remain pending and no old evidence is rewritten.
- [The change spans more than three files] → The breadth is required to close one persistence transaction across controller, pure projections, Schemas, RunStore and recovery; no new architectural layer or dependency is introduced.

## Migration Plan

1. Verify and record the current M1-7 baseline, then write the required M1-8 implementation brief from the authoritative sections and frozen interfaces.
2. Generalize epoch-related Schemas/examples and pure validators while keeping all E0 tests green.
3. Add the pure materialization action/file-ledger projector and frozen multi-epoch fixtures; prove preservation, quarantine and re-adoption without stage side effects.
4. Add F2 binding projection/publication and prove metadata changes with byte-identical workspace/checkpoint/epoch receipt.
5. Generalize S5 admission, staged application, build attribution, checkpoint and receipt publication for E1+.
6. Add every recovery fault window, zero-change replay and current-instance Orchestrator reconciliation.
7. Run `s5_epoch`, focused S4/S5/S6/revision/RunStore/Git regressions, real sandbox, full CI, strict OpenSpec checks and diff inspection, confirming no M1-9+ producer or `project_docs/` edit entered the implementation.
8. Apply the post-review remediation decisions 9-12, regenerate the frozen fixture packages, and replace the superseded acceptance record only after every focused and full gate passes.

There is no in-place format conversion or destructive rollback. Existing accepted runs and epoch/version artifacts remain immutable. Before an E1+/F2 artifact is accepted, the implementation can be reverted normally; afterward that run must be read by a compatible implementation, and recovery follows its persisted transaction rather than rewriting it into E0-only form.
