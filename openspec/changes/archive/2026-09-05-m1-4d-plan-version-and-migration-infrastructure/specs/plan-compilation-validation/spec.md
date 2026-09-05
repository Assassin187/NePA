## MODIFIED Requirements

### Requirement: Plan and task-shard contracts are closed and state-free
The system SHALL provide closed draft-2020-12 Schemas and conforming examples for Plan v4 and the normalized task-shard/PlanDraftIR input. Plan SHALL contain the three frozen input refs, Blueprint SHA-256, architecture, work packages, final tasks, deterministic coverage, and final review; every final task SHALL additionally contain controller-derived `task_uid`, `obligation_digest`, and `guidance_digest`. Plan SHALL NOT contain execution status, attempts, notes, scaffold tasks, S5 file contents, or mutable run state. Task shards SHALL use local semantic ids and SHALL NOT supply final `T-###` ids, derived identity/digests, hashes, coverage, or global state. (Design 7.2.0: §5.2.1-§5.2.2, §6.4.4-§6.4.5; pipeline design 1.3.0 §3.1-§3.2, §5.3; D1.8-D1.9; M1-4d.)

#### Scenario: Conforming artifacts are validated
- **WHEN** minimal Plan and PlanDraftIR examples contain exactly their declared fields
- **THEN** both validate under draft 2020-12 and canonical serialization is stable

#### Scenario: A task shard supplies a derived field
- **WHEN** a task shard contains `task_uid`, `obligation_digest`, or `guidance_digest`
- **THEN** Schema or normalization rejects it rather than trusting Agent-authored identity

#### Scenario: Runtime state appears in Plan
- **WHEN** a candidate Plan includes status, attempts, notes, commit state, or another undeclared runtime field
- **THEN** Schema validation rejects it

### Requirement: Final task identifiers use stable topological order
The Linker SHALL topologically order tasks with Kahn's algorithm and choose each ready item by the UTF-8 dictionary order of `(work_package.id, local_task_id)`, independent of shard array order. It SHALL assign sequential `T-###` ids, rewrite all local references, inject exact build variants and responsibility-derived context refs, then derive `task_uid` as the first 16 lowercase hexadecimal characters of SHA-256 over canonical JSON `[work_package.id, local_task_id]`. It SHALL reject a uid collision within one Plan. It SHALL derive the obligation and guidance digests from the exact sorted fields and contract-interface signature digests defined by design, without model judgment. (Design 7.2.0: §5.2.2, §6.4.5; pipeline design 1.3.0 §3.1-§3.2, §5.3; S4-G4; M1-4d.)

#### Scenario: Shard arrays are permuted
- **WHEN** semantically identical task shards differ only in array order
- **THEN** final task ids, dependencies, task uids, obligation/guidance digests and canonical linked Plan are identical

#### Scenario: Guidance alone changes
- **WHEN** only title, goal, instructions, kind or context refs change
- **THEN** the guidance digest changes while task uid and obligation digest remain unchanged

#### Scenario: A consumed signature changes
- **WHEN** one consumed contract export signature changes without an implementation body being supplied
- **THEN** the consumer obligation digest changes deterministically

#### Scenario: Task uid collision is detected
- **WHEN** two linked task identities produce the same truncated uid
- **THEN** linking fails and assigns no misleading partial Plan

### Requirement: Final Plan binds the exact Blueprint and frozen inputs without a hash cycle
After all semantic task fields and coverage are determined, the compiler SHALL build the Delivery Blueprint from Delivery Constraints, final architecture, work packages, and a task semantic projection that excludes `task_uid`, `obligation_digest`, and `guidance_digest`; it SHALL then derive those metadata fields for the persisted Plan, compute the canonical Blueprint SHA-256, and inject that hash plus the controller-supplied Spec, Target Profile, and Test Bundle refs. Neither the Blueprint hash nor M1-4d metadata SHALL be an input to Blueprint compilation. Repeated linking of identical inputs SHALL produce byte-identical link evidence and candidate Plan. (Design 7.2.0: §5.2.1-§5.2.2, §6.4.5; pipeline design 1.3.0 §3.1-§3.2, §5.3; S4-G0/S4-G1; M1-4d.)

#### Scenario: Identical draft inputs are linked twice
- **WHEN** all normalized drafts, constraints, frozen refs, manifest metadata, exports and configuration are identical
- **THEN** Blueprint hash, derived task metadata, linked Plan content, and link report are identical

#### Scenario: Only derived task metadata is projected
- **WHEN** Blueprint compilation is repeated with and without the three correctly derived metadata fields on otherwise identical tasks
- **THEN** the canonical Blueprint bytes are identical

#### Scenario: Blueprint projection drifts
- **WHEN** the supplied or recomputed Blueprint differs from the final Plan semantic projection
- **THEN** full validation fails before any Plan can be treated as publishable

### Requirement: Candidate completion remains state-free and derives M1-4d metadata
The common M1-4c completion and critic loops SHALL preserve state-free Plan and PlanDraftIR contracts while invoking the one Linker path that derives task uid, obligation digest and guidance digest for every completed candidate. They SHALL NOT calculate migration classification, revision entries, execution status, attempts, evidence, workspace state, or task test acceptance before M2-0. Every repair SHALL recompute the derived metadata, and the metadata SHALL NOT affect Blueprint semantics. (Design 7.2.0: §5.2, §6.4.5-§6.4.7, §10.2 M1-4c/M1-4d; pipeline design 1.3.0 §5.3; M1-4d.)

#### Scenario: A repaired candidate is relinked
- **WHEN** an admitted semantic repair changes architecture, exports or a shard
- **THEN** the candidate receives freshly derived identity/digests but remains free of migration and execution state

#### Scenario: A strategy attempts to inject metadata
- **WHEN** layered or flat Agent output supplies any M1-4d derived field
- **THEN** the common path rejects it rather than maintaining a strategy-specific identity implementation

## RENAMED Requirements

- FROM: `Candidate completion remains state-free and M1-4d-free`
- TO: `Candidate completion remains state-free and derives M1-4d metadata`
