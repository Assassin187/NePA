# s4-initial-plan-publication Specification

## Purpose

Define the initial S4 publication transaction and durable artifacts that make Plan version 1.0.0 independently verifiable and safe for later stages to consume or resume.

## Requirements

### Requirement: Initial publication artifacts are closed and canonical
The system SHALL provide closed draft-2020-12 Schemas and conforming examples for the persisted S4 commitment, sealed architecture checkpoint, PlanCritic result, active Plan pointer, initial file ledger, and revision ledger. The initial Plan SHALL be canonical `plan/versions/plan-1.0.0.json`; the active pointer SHALL name that path and hash with `version="1.0.0"`, `revision_seq=0`, and `epoch="E0"`; the file ledger SHALL contain exactly the fully expanded Blueprint file paths with `state=slot_only`; and the revision ledger SHALL contain `entries=[]`. No M1-4d task uid, obligation/guidance digest, migration, preservation, revision activation, or revision-entry field SHALL be added by this change. (Design 7.1.1: §5.2, §6.4.7, §10.2 M1-4c/M1-4d; pipeline design 1.2.0 §4.2, §5.3.)

#### Scenario: Initial artifacts conform
- **WHEN** a publishable candidate and its complete Blueprint are sealed
- **THEN** every initial artifact validates, uses project canonical bytes, and the file-ledger path set equals the Blueprint concrete file-rule path set

#### Scenario: Later-revision data is supplied
- **WHEN** an initial pointer or ledger contains a positive revision sequence, a revision entry, migration data, task uid, or migration digest
- **THEN** the M1-4c artifact contract rejects it

### Requirement: Initial Plan publication has one logical commit point
The controller SHALL publish the immutable Plan version, initial file ledger, and empty revision ledger before atomically publishing the active pointer. It SHALL then reread and validate every artifact, Plan input reference, Blueprint SHA-256, pointer target, ledger path set, and configuration binding before the final atomic Run update marks S4 done. A downstream stage SHALL treat only that final Run update as the logical S4 commit point and SHALL NOT consume artifacts merely because some publication files exist. Any later replay or resume of a Run with S4 done SHALL perform the same seal verification before returning an existing terminal result. (Design 7.1.1: §4.8, §6.4.7; pipeline design 1.2.0 §5.3; M1-4c.)

#### Scenario: Complete publication succeeds
- **WHEN** all candidate gates pass and every published artifact rereads with its expected bytes and bindings
- **THEN** one final Run update marks S4 done and exposes a complete independently anchored output set

#### Scenario: A crash leaves an active pointer without a done stage
- **WHEN** the process exits after publishing some or all initial files but before committing `stages.s4=done`
- **THEN** S5 cannot consume them and resume must reconcile them against the validated canonical candidate, complete only a byte-identical missing suffix, or fail closed on conflict

### Requirement: The S4 seal carries independent typed anchors
The S4 Run output contract SHALL contain file references for the immutable Plan and active pointer plus independent SHA-256 values for the Delivery Blueprint and sealed configuration snapshot. Stage completion verification SHALL validate reference targets and SHALL compare the two hash anchors to independently recomputed values rather than treating them as artifact paths. Existing stages and M0 lint commands SHALL retain their current behavior. (Design 7.1.1: §4.8, §6.4.7; D1.8-D1.9; M1-4c.)

#### Scenario: Seal anchors agree
- **WHEN** the Plan and active pointer files exist and the recomputed Blueprint/configuration hashes match the S4 output anchors
- **THEN** the completed S4 stage is accepted as consumable

#### Scenario: One anchor drifts
- **WHEN** a file reference, Blueprint hash, configuration hash, Plan input ref, or active-pointer target disagrees
- **THEN** S4 completion is rejected and downstream admission does not trust the remaining agreeing values

### Requirement: Initial publication is immutable and replay-safe
Publishing the same canonical initial artifacts more than once SHALL be an idempotent no-op. Existing different bytes at the immutable Plan path, a different path/hash under the initial active pointer, a non-empty initial revision ledger, or an incompatible initial file ledger SHALL be treated as publication conflict or artifact damage. S4 done replay SHALL not rewrite any published byte. (Design 7.1.1: §4.8, §6.4.7; M1-4c.)

#### Scenario: Publication is replayed after a safe crash
- **WHEN** the on-disk initial artifacts are byte-identical to the validated candidate but the S4 Run commit is absent
- **THEN** resume accepts the idempotent files, revalidates them, and completes only the missing logical commit

#### Scenario: The immutable Plan path conflicts
- **WHEN** `plan/versions/plan-1.0.0.json` already exists with different bytes
- **THEN** the controller reports artifact damage or an internal publication conflict and does not overwrite it
