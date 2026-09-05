## MODIFIED Requirements

### Requirement: Initial publication artifacts are closed and canonical
The system SHALL provide closed draft-2020-12 Schemas and conforming examples for the persisted S4 commitment, sealed architecture checkpoint, PlanCritic result, general active Plan pointer, file ledger, and revision ledger. The initial Plan SHALL be canonical `plan/versions/plan-1.0.0.json` and every task SHALL contain Linker-derived task uid, obligation digest and guidance digest. The initial active pointer SHALL name that path and hash with `version="1.0.0"`, `revision_seq=0`, and `epoch="E0"`; the file ledger SHALL use top-level `files` containing exactly the fully expanded Blueprint paths in `slot_only` state; and the revision ledger SHALL contain `entries=[]`. No migration classification, preservation result, revision entry or execution state SHALL appear during initial publication. (Design 7.2.0: §5.2, §6.4.7, §10.2 M1-4c/M1-4d; pipeline design 1.3.0 §3.1-§3.3, §4.2-§4.3, §5.3; M1-4d.)

#### Scenario: Initial artifacts conform
- **WHEN** a publishable candidate and its complete Blueprint are sealed
- **THEN** every artifact validates, uses project canonical bytes, every task contains recomputable metadata, and the file-ledger path set equals the Blueprint concrete file-rule path set

#### Scenario: Initial ledger contains a revision
- **WHEN** the initial pointer has a positive sequence or the revision ledger contains an entry
- **THEN** initial publication rejects it and does not manufacture a genesis revision

#### Scenario: Interim artifact shape is supplied
- **WHEN** a Plan task lacks the required metadata or the file ledger uses the former `entries` key
- **THEN** the current closed artifact contract rejects it without compatibility conversion

### Requirement: The S4 seal carries independent typed anchors
The S4 Run output contract SHALL contain a permanent file reference for immutable Plan 1.0.0, a file reference for the mutable active pointer, and independent SHA-256 values for the Delivery Blueprint and sealed configuration snapshot. Initial completion verification SHALL require both Plan references to identify 1.0.0. After a valid revision, completion verification SHALL keep `output_refs.plan` bound to 1.0.0 while requiring `output_refs.active_plan`, Plan State and the terminal revision entry to agree on the current version, hash, sequence and epoch. Existing non-S4 stages and M0 lint commands SHALL retain their current behavior. (Design 7.2.0: §4.8, §5.2, §6.4.7; pipeline design 1.3.0 §4.2-§4.4, §6.4; D1.8-D1.9; M1-4d.)

#### Scenario: Initial seal anchors agree
- **WHEN** Plan 1.0.0, initial pointer, Blueprint and configuration hashes agree
- **THEN** the completed S4 stage is accepted as consumable

#### Scenario: A later active pointer is proven
- **WHEN** the immutable initial Plan anchor is intact and a complete revision chain ends at the active pointer and State
- **THEN** S4 completion verification accepts the later active Plan without changing the initial Plan reference

#### Scenario: One anchor drifts
- **WHEN** a file reference, Blueprint hash, configuration hash, initial Plan ref, active-pointer target, State binding or ledger terminal entry disagrees
- **THEN** completion verification rejects the run and downstream admission does not trust the remaining agreeing values
