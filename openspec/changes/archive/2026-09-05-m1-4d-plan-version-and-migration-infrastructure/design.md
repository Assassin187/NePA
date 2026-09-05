## Context

See `proposal.md` for motivation and milestone scope. The current M1-4c path already has one deterministic Linker, Plan/Plan-State validators, atomic file replacement under the run controller lock, and initial S4 publication. Its persisted contracts are deliberately initial-only: architecture contracts do not expose signatures, linked tasks do not carry revision identity, the file ledger uses the interim `entries` shape, the revision ledger is empty-only, and completion verification assumes the active Plan is always 1.0.0.

M1-4d must extend those existing paths without introducing a second compiler or state machine. `project_docs/system_design.md` 7.2.0 and `project_docs/pipeline_design_s4_s9.md` 1.3.0 are the governing inputs; they were updated before this change and are not implementation outputs of it. The delta specs in this change define the externally observable requirements. All migration and activation decisions are deterministic controller work. LLM participation remains limited to the already bounded ArchitecturePlanner baseline development and later candidate generation outside this milestone.

The implementation spans Schema contracts, pure semantic functions, persisted run artifacts, prompt lineage evidence, and S4 verification. It must preserve the immutable S4 `output_refs.plan` anchor while allowing the separately referenced active Plan to advance.

## Goals / Non-Goals

**Goals:**

- Extend the existing architecture-to-Plan path so closed export signatures produce controller-derived, replay-stable task identity and digests.
- Provide one pure migration classifier and one whole-State projector whose outputs fully account for task and realized-file preservation.
- Generalize the initial ledgers and pointer into closed, hash-bound revision contracts and activate them through the existing run lock and atomic-write machinery.
- Make interrupted activation recover to the unique state selected by the active pointer and WAL, without guessing or mutating immutable evidence.
- Keep initial S4 publication and all existing non-revision behavior on the same public paths, with 1.0.0 remaining the permanent S4 Plan anchor.

**Non-Goals:**

- This design does not generate revision candidates, evaluate F1/F2/F3 triggers, apply patches, enforce revision budgets, or run S5/S6/S9 behavior.
- It does not infer split/merge lineage, rewrite historical evidence, migrate interim pre-7.2.0 artifacts, or add protocol-specific compatibility behavior.
- It does not change provider routing, calibration policy, task execution semantics, or the ownership of controller decisions.
- It does not modify `project_docs/`; those documents are inputs to this change, not part of its file scope.

## Decisions

### 1. Extend the existing Linker and architecture validator

`nepa/speclib/architecture.py` remains the sole semantic validator for both calibration and production. Architecture Schemas, examples, patch contracts, flat variants, and the ArchitecturePlanner initial/repair prompts gain the exact closed `exports[]` shape. Export file membership, uniqueness, and symbol attribution are checked alongside the existing complete gate result rather than by a new validation service.

`nepa/speclib/plan.py` remains the sole Linker. After stable topology, id rewriting, build-variant injection, and responsibility expansion, it derives:

- `task_uid` from canonical `[work_package.id, local_task_id]`;
- `obligation_digest` from the design-defined sorted obligation fields and consumed export signatures;
- `guidance_digest` from the design-defined sorted guidance fields.

The Delivery Blueprint compiler receives an explicit semantic projection that omits all three derived values. This preserves the existing no-hash-cycle boundary and makes it testable that metadata cannot influence Blueprint bytes. A collision check is performed once across the final task set before returning a candidate.

Alternative considered: compute metadata in S4 publication. Rejected because repaired candidates, critic inputs, validation, and publication could then observe different Plan meanings, creating two identity paths.

### 2. Isolate pure revision semantics in one focused speclib module

Add `nepa/speclib/plan_revision.py` for pure, filesystem-free operations: deriving and validating migration rows, computing file preservation, projecting Plan State/file-ledger values, checking C.A.P successors, constructing revision entries, and validating the full hash chain. Public operations accept complete artifact values and return complete canonicalizable values; they do not read current run state or perform writes.

The classifier first establishes task correspondence by equal uid or explicit lineage. It then applies the ordered design predicates for `INHERIT`, `REVALIDATE`, `AMEND`, and `REGENERATE`. It emits canonically sorted task rows and file rows plus recomputable counts and preservation rate. Every old/new task and old realized file must be consumed exactly once; absence, duplication, implicit ancestry, or inconsistent aggregates is an error.

`nepa/speclib/plan_state.py` delegates revision projection to this complete report while retaining its existing ordinary event transition table. Projection is one whole-State operation so a caller cannot preserve selected rows while omitting removed or regenerated work. Required typed build proof is an input for `REVALIDATE`; no proof is synthesized.

Alternative considered: add migration cases to the ordinary event reducer. Rejected because revision projection changes the complete task set atomically, while ordinary execution events change one row under a fixed Plan.

### 3. Treat Schemas as the closed persistence boundary

Update the existing Plan, Plan State, file-ledger, revision-ledger, active-pointer, architecture, patch, validation, S4 checkpoint/commitment Schemas and their examples in place. Add a closed migration-report Schema/example because its rows are persisted in activation evidence and revision entries. Conditional file-ledger rows express only the fields legal for `slot_only`, `realized`, and `quarantined`; no permissive compatibility union retains `entries`.

Schema validation establishes shape. Speclib validation establishes cross-artifact meaning: recomputed task metadata, exact task/path sets, digest and hash equality, migration counts, predecessor hashes, version/epoch succession, and active-pointer/State/Run agreement. Canonical JSON is reused for every digest and ledger-entry hash.

Alternative considered: accept both `entries` and `files` during a transition. Rejected because no deployed pre-7.2.0 revision chain is in scope, and dual shapes would make the active contract ambiguous.

### 4. Put persistence and reconciliation in RunStore under the existing lock

`nepa/run_store.py` owns the side-effecting activation and recovery APIs because it already owns run-relative paths, atomic replacement, locking, and Run metadata. The caller supplies a fully validated candidate bundle and expected current hashes. Under the controller lock, RunStore revalidates those expectations before any live mutation.

Activation persists canonical `_s4r/rev_NNN/activation.json` first. The WAL binds old and new artifact refs, hashes, exact old mutable bytes needed for pre-commit restoration, and the proposed terminal revision entry. Writes then occur in this order:

1. publish the immutable version file;
2. atomically replace Plan State;
3. atomically replace the file ledger;
4. atomically replace the revision ledger with the append;
5. atomically advance `active_plan.json` (logical commit);
6. update only the Run active-pointer reference.

The API refuses to overwrite an existing activated version with different bytes. `output_refs.plan` is never an activation target. The activation result is a typed receipt containing the committed pointer/ref hashes; downstream code need not infer success from file presence.

Alternative considered: one large temporary directory rename. Rejected because the current public artifact paths and immutable version history span stable locations, and the Run record remains a separate mutable anchor.

### 5. Recovery follows pointer authority and validates before completing work

Recovery runs under the same lock and reads the WAL, live ledger, pointer, and bound hashes. It has only the three specified convergence branches:

- ledger old: restore WAL-bound old State/file-ledger bytes, leave the old pointer active, and keep the candidate isolated in its revision workspace;
- ledger new and pointer old: verify or restore the WAL-bound new State/file ledger, then advance the pointer;
- pointer new: verify the complete new chain and artifacts, then repair only a missing/stale Run active-pointer reference.

Any state that matches none of these branches is reported as artifact damage. Recovery neither deletes realized files nor rolls back a committed pointer. Repeated recovery is idempotent because each completed branch revalidates the same hashes.

Alternative considered: choose whichever side has more new files. Rejected because file presence is not a commit record and would permit ambiguous recovery.

### 6. Preserve initial publication while generalizing verification

`nepa/stages/s4_planning.py` continues to publish initial artifacts through its current sealing path. It now requires derived task metadata, emits file-ledger `files` rows in `slot_only`, and creates the general pointer and empty general revision ledger. It does not create a genesis revision or migration report.

Completion verification distinguishes the immutable initial Plan reference from the mutable active-pointer reference. Sequence zero must resolve both to 1.0.0. A later sequence must prove an unbroken revision chain whose terminal entry, pointer, current Plan, State, file ledger, and Run active reference agree, while the S4 Plan output remains 1.0.0.

Alternative considered: rewrite the S4 Plan output on every activation. Rejected because it destroys the immutable stage seal and conflicts with the independent active-pointer anchor.

### 7. Refresh ArchitecturePlanner evidence as an explicit independent gate

The Schema/validator/prompt change creates a new design-7.2.0 lineage. Existing calibration machinery is reused with one configured logical slot, V0-V2, N=3, first version reaching at least 2/3, and the existing neutrality checks. Historical evidence remains readable but is excluded from the new denominator and selection.

Owner approval is a separately recorded, non-automatable prerequisite for production handoff. Implementation and machine validation may finish before approval, but the production binding must fail closed until a signature references the selected bundle, neutrality result, lineage, and recomputable evidence.

Alternative considered: carry forward the former approval because only the output Schema changed. Rejected because the approved contract did not include exports and therefore did not approve the output now required by production.

## Risks / Trade-offs

- [A truncated `task_uid` can theoretically collide] → Reject any collision within a Plan and retain full obligation/guidance SHA-256 values for semantic decisions; do not invent a fallback uid.
- [Schema changes invalidate interim local artifacts] → Make the break explicit, update all repository examples/fixtures together, and require regeneration from frozen inputs rather than ambiguous compatibility conversion.
- [Cross-artifact activation can crash between atomic file replacements] → Persist a hash-bound WAL first, define the pointer as the sole logical commit, inject failures at every boundary, and make recovery idempotent.
- [A correct classifier can still preserve work against bad explicit lineage] → Require lineage to be supplied explicitly by the future revision controller and validate completeness/uniqueness; M1-4d does not attempt semantic inference.
- [The generalized S4 verifier may accidentally weaken initial checks] → Keep a dedicated sequence-zero branch and run all existing S4 publication/resume regressions in addition to later-revision cases.
- [Prompt baseline work depends on external model results and owner availability] → Keep it as an independent blocking gate, preserve all generated evidence, and never encode approval in automated task completion.

## Migration Plan

1. Land closed Schema/example changes and pure architecture/Plan metadata logic; regenerate repository fixtures and prove canonical replay plus protocol neutrality.
2. Create the new ArchitecturePlanner lineage with the frozen Schema, validator, serializer, patch contract, prompt variants, slot and request configuration. Run the bounded V0-V2 policy and record the result. Production handoff remains disabled pending owner approval.
3. Add the pure revision classifier, State/file-ledger projection, ledger-chain and version successor validation. These functions are initially unreachable from production activation and can be validated against examples and deterministic tests.
4. Add RunStore WAL activation/recovery and S4 generalized publication/verification, then exercise failure injection at each write boundary and complete-chain resume tests.
5. Obtain and record owner approval for the selected new-lineage bundle. Only then update the production prompt binding/handoff and run the full suite plus gold and protocol-neutrality lint.

Rollback before pointer advancement restores the WAL-bound old mutable artifacts and leaves the old pointer/Run reference active. After pointer advancement, rollback is not permitted: the active revision is immutable history, so remediation must be a later valid revision. Code deployment can be reverted only if the stored active artifacts remain readable by that code; because no compatibility shim is provided, environments that have activated a later revision must retain the M1-4d reader/validator path.
