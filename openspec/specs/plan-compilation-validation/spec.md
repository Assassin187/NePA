# plan-compilation-validation Specification

## Purpose

Define deterministic construction and validation of a closed Plan from architecture and work-package task shards, including stable linking, coverage, Blueprint binding, and basic/full lint levels.

## Requirements

### Requirement: Plan and task-shard contracts are closed and state-free
The system SHALL provide closed draft-2020-12 Schemas and conforming examples for Plan v4 and the normalized task-shard/PlanDraftIR input. Plan SHALL contain the three frozen input refs, Blueprint SHA-256, architecture, work packages, final tasks, deterministic coverage, and final review; it SHALL NOT contain execution status, attempts, notes, scaffold tasks, S5 file contents, or mutable run state. Task shards SHALL use local semantic ids and SHALL NOT supply final `T-###` ids, hashes, coverage, or global state. (Design 7.1.0: §5.2.1-§5.2.2, §6.4.4-§6.4.5; D1.9; M1-4b.)

#### Scenario: Conforming artifacts are validated
- **WHEN** minimal Plan and PlanDraftIR examples contain exactly their declared fields
- **THEN** both validate under draft 2020-12 and canonical serialization is stable

#### Scenario: Runtime state appears in Plan
- **WHEN** a candidate Plan includes status, attempts, notes, commit state, or another undeclared runtime field
- **THEN** Schema validation rejects it

### Requirement: Linker closes contracts, ownership, responsibilities, and DAGs
The deterministic Linker SHALL verify exact module-to-work-package-to-task set equalities for contracts, requirement responsibilities, and `s6_owned` file partitions. It SHALL resolve the unique provider task for each task-ready contract, add provider-to-consumer dependencies, preserve valid shard-local dependencies, reject unproven cross-package edges, and reject cycles. It SHALL NOT invent tasks, owners, contracts, requirements, files, or semantic dependencies. (Design 7.1.0: §5.2.1-§5.2.2, §6.4.5; S4-G3/S4-G4; M1-4b.)

#### Scenario: Contract dependencies close
- **WHEN** every consumed task-ready contract has one legal provider task
- **THEN** each consumer depends transitively on that provider and work-package dependencies contain only cross-package provider relations

#### Scenario: A set equality or dependency is unprovable
- **WHEN** files, contracts, responsibilities, providers, or cross-package edges do not close exactly
- **THEN** linking fails with no semantic repair or inferred task

### Requirement: Final task identifiers use stable topological order
The Linker SHALL topologically order tasks with Kahn's algorithm and choose each ready item by the UTF-8 dictionary order of `(work_package.id, local_task_id)`, independent of shard array order. It SHALL then assign sequential `T-###` ids, rewrite all local references, inject exact build variants, and add responsibility-derived requirement context references. M1-4d-owned task identity and migration-digest calculation SHALL remain outside this change. (Design 7.1.0: §5.2.2, §6.4.5, §10.2 M1-4b/M1-4d; S4-G4; M1-4b.)

#### Scenario: Shard arrays are permuted
- **WHEN** semantically identical task shards differ only in array order
- **THEN** the final task ids, dependencies, M1-4b-derived fields, and canonical linked task graph are identical

#### Scenario: Task graph contains a cycle
- **WHEN** no ready task remains before all tasks are ordered
- **THEN** linking fails and assigns no misleading partial final plan

### Requirement: Coverage is generated from authoritative responsibilities and manifest metadata
The Linker SHALL generate exactly one coverage row for every Spec requirement and one row for every Test Manifest entry. Non-DEFINITION requirements SHALL have one primary work package and one primary task; MUST/MUST NOT requirements SHALL retain at least one qualifying behavior-test association. Test `enabled` SHALL derive only from its layer switch. A `gate=task` test SHALL bind to the earliest stable-topology task whose ancestor closure contains all primary/supporting implementation tasks for its requirements; `s5` and `s7_only` tests SHALL have no task id. Before M2-0, task `acceptance.tests` SHALL remain empty. (Design 7.1.0: §5.2.3, §6.4.5; S4-G5; M1-4b.)

#### Scenario: Earliest readiness point exists
- **WHEN** a task-gated test's complete requirement implementation set converges in one or more task ancestor closures
- **THEN** coverage selects the earliest legal task in stable topological order

#### Scenario: Readiness never converges
- **WHEN** no task ancestor closure contains the test's complete primary/supporting implementation set
- **THEN** linking fails rather than binding the test early or creating an integration task

### Requirement: Final Plan binds the exact Blueprint and frozen inputs without a hash cycle
After all final task fields and coverage are injected, the compiler SHALL build the Delivery Blueprint from Delivery Constraints, final architecture, work packages, and tasks; compute its canonical SHA-256; and inject that hash plus the controller-supplied Spec, Target Profile, and Test Bundle refs into Plan. The Blueprint hash SHALL NOT be an input to Blueprint compilation. Repeated linking of identical inputs SHALL produce byte-identical link evidence and candidate Plan. (Design 7.1.0: §5.2.1, §6.4.5; S4-G0/S4-G1; M1-4b/M1-4b2.)

#### Scenario: Identical draft inputs are linked twice
- **WHEN** all normalized drafts, constraints, frozen refs, manifest metadata, and configuration are identical
- **THEN** Blueprint hash, linked Plan content, and link report are identical

#### Scenario: Blueprint projection drifts
- **WHEN** the supplied or recomputed Blueprint differs from the final Plan semantic projection
- **THEN** full validation fails before any Plan can be treated as publishable

### Requirement: Plan lint distinguishes basic shape from full S4 readiness
Basic lint SHALL validate Plan Schema, frozen refs, ids/references, both DAGs, responsibility refinement, contract consistency, coverage recomputation, manifest entries, and test-enabled derivation using Plan, Spec, Test Manifest, and configuration. Full lint SHALL additionally require Delivery Constraints, Delivery Blueprint, and frozen Target Profile and validate path classes, file partition, build variants, provider/consumer ancestry, test readiness, context/output budgets, and faithful free-layout transcription across S4-G0 through S4-G6. A basic-only success SHALL NOT be reported as full S4 acceptance. (Design 7.1.0: §5.2.5, §6.4.5; D1.7-D1.9; M1-4b/M1-4b2.)

#### Scenario: Basic lint lacks run artifacts
- **WHEN** a valid Plan is checked without the run directory inputs needed for Blueprint and Target validation
- **THEN** basic results may pass but full readiness remains explicitly unavailable

#### Scenario: Full lint finds a hard gate violation
- **WHEN** any S4-G0 through S4-G6 invariant fails
- **THEN** lint returns a deterministic nonzero-error report and the Plan is not publishable

#### Scenario: CLI requests plan lint
- **WHEN** `nepa lint plan <plan>` is invoked with basic inputs or with `--run-dir` for full reconstruction
- **THEN** the CLI returns the matching structured lint level and existing validation exit-code semantics

### Requirement: The controller uses one deterministic candidate-completion path
After either strategy produces a complete semantic draft, the controller SHALL normalize it to the existing PlanDraftIR, run the existing deterministic Linker and Delivery Blueprint compiler, inject only controller-owned frozen refs, run full plan lint across S4-G0 through S4-G6, and preserve the resulting link/lint evidence. A candidate SHALL be critic-eligible only after this complete path returns zero errors, and publishable only after a subsequent PlanCritic pass and one final complete deterministic recomputation. No controller branch SHALL maintain a second dependency, coverage, Blueprint, or lint implementation. (Design 7.1.1: §6.4.5-§6.4.7; pipeline design 1.2.0 §5.3; M1-4c.)

#### Scenario: Layered and flat drafts are semantically equal
- **WHEN** both strategies supply equivalent normalized architecture, work packages, and local task shards
- **THEN** the common completion path produces byte-identical candidate Plan, Blueprint, coverage, and link evidence

#### Scenario: Basic lint passes but full input is absent
- **WHEN** a candidate passes basic shape checks but cannot reconstruct its constraints, Blueprint, Target Profile, or budget inputs
- **THEN** it is not critic-eligible or publishable

### Requirement: Critic results are validated and deterministically routed
The controller SHALL recompute a critic verdict from the validated issue list: any blocker or major requires `revise`, while no blocker or major requires `pass`; minor issues MAY coexist with `pass` and SHALL be normalized into the final Plan review. Mechanical issues SHALL be corrected only by deterministic recomputation when the source semantics are already valid. In layered mode a task/work-package-local semantic issue SHALL invalidate only the named shard and a global architecture issue SHALL invalidate the architecture and all child shards; in flat mode any semantic revise SHALL invalidate the complete flat draft. After repair, all deterministic completion gates and a fresh critic invocation SHALL rerun. (Design 7.1.1: §6.4.6; M1-4c.)

#### Scenario: Critic verdict contradicts its issues
- **WHEN** a critic reports `pass` while its issue list contains a blocker or major
- **THEN** the controller treats the review as invalid and does not publish the candidate

#### Scenario: Critic returns only minor issues
- **WHEN** the issue list contains no blocker or major and the verdict is `pass`
- **THEN** the controller normalizes the unresolved minor issues into final Plan review while preserving the complete review history only in `plan/_s4`

### Requirement: Candidate completion remains state-free and M1-4d-free
The M1-4c completion and critic loops SHALL preserve the existing state-free Plan and PlanDraftIR contracts. They SHALL NOT calculate or inject task uid, obligation digest, guidance digest, migration classification, revision entry, execution status, attempts, evidence, workspace state, or task test acceptance before M2-0. Those fields SHALL not affect Blueprint semantics or initial publication. (Design 7.1.1: §5.2, §6.4.5-§6.4.7, §10.2 M1-4c/M1-4d; M1-4c.)

#### Scenario: A repaired candidate is relinked
- **WHEN** an admitted semantic repair changes architecture or a shard
- **THEN** the recomputed Plan still contains only the current M1-4b-derived static fields and no M1-4d or execution data
