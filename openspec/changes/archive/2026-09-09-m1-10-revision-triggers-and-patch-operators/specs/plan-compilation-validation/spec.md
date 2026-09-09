## ADDED Requirements

### Requirement: Formal Plan preserves package-local task identity
The current formal Plan contract SHALL use Schema 5.0 and persist required `local_task_id` for every task. Linker SHALL derive `task_uid` from canonical `[work_package, local_task_id]`, and a formal Plan SHALL be convertible back to PlanDraftIR without guessing package-local dependency identities. Blueprint semantic projection SHALL exclude `local_task_id` so this persistence correction does not change delivery semantics. Initial S4 and revision completion SHALL call one shared completion implementation. The current implementation SHALL reject Plan 4.0 and SHALL NOT provide an in-place or historical-run conversion path; Run Schema 4.0 and other unrelated versioned contracts remain unchanged. (Design: `project_docs/system_design.md` §5.2-§5.2.5; M1-10 baseline correction.)

#### Scenario: Formal task retains its local identity
- **WHEN** Linker completes a PlanDraftIR task with one package-local id
- **THEN** the Plan 5.0 task stores that id and derives the stable uid from the package/id pair

#### Scenario: Formal Plan is restored to draft IR
- **WHEN** a valid Plan 5.0 is projected through `plan_to_draft_ir`
- **THEN** package-local task ids and dependency references are restored losslessly and relinking produces the same stable identities

#### Scenario: Plan 4.0 reaches the current implementation
- **WHEN** a formal Plan declares Schema 4.0
- **THEN** current validation rejects it without attempting compatibility conversion

### Requirement: Revision candidates use the existing deterministic completion path
A patched revision candidate SHALL pass through the same contract closure, responsibility/file partitioning, task-uid and digest derivation, stable topological numbering, coverage construction, Blueprint binding and basic/full Plan lint semantics as an initial candidate. Revision completion SHALL additionally consume the immutable commitment and explicit lineage/obligation mappings, SHALL preserve every frozen requirement and acceptance obligation, and SHALL reject any candidate that relies on task-number similarity or inferred edits to repair an incomplete patch. It SHALL remain state-free: current execution State and ledger facts may constrain migration classification and candidate eligibility but SHALL NOT be embedded as Plan execution fields. Identical source Plan, commitment, patch and lineage inputs SHALL produce byte-identical completed candidate artifacts. (Design: `project_docs/system_design.md` §5.2-§5.2.5; `project_docs/pipeline_design_s4_s9.md` §2-§3, §6.2; M1-10.)

#### Scenario: Legal revision is completed
- **WHEN** a closed patch and explicit lineage preserve the commitment, partitions, contract closure and all old obligations
- **THEN** the existing completion path emits one fully linked and lint-clean candidate with deterministic ids, digests, coverage and Blueprint binding

#### Scenario: Patch leaves a dangling reference
- **WHEN** the operation list omits a required file, contract, owner, consumer or acceptance mapping update
- **THEN** completion rejects the candidate instead of inferring the missing edit

#### Scenario: Execution facts are presented as Plan fields
- **WHEN** a patched task carries attempts, status, evidence or another runtime field
- **THEN** the candidate fails the existing state-free Plan contract

#### Scenario: Revision completion is replayed
- **WHEN** byte-identical source and patch inputs are completed twice
- **THEN** the candidate Plan and all derived artifact hashes are byte-identical

#### Scenario: Existing ownership is hidden inside package insertion
- **WHEN** `add_work_package` names a file or responsibility already owned by another task without an explicit move and lineage mapping
- **THEN** candidate completion rejects the patch instead of silently changing ownership
