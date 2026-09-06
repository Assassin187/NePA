## MODIFIED Requirements

### Requirement: Initial publication artifacts are closed and canonical
The system SHALL provide closed draft-2020-12 Schemas and conforming examples for the current fresh-run contract, including Run v4, the persisted S4 commitment, sealed architecture checkpoint, PlanCritic result, general active Plan pointer, file ledger v2, and typed revision ledger v2. The initial Plan SHALL remain canonical `plan/versions/plan-1.0.0.json` in the already delivered S4 Plan shape and every task SHALL contain Linker-derived task uid, obligation digest and guidance digest. The initial active pointer SHALL name that path and hash with `version="1.0.0"`, `revision_seq=0`, and `epoch="E0"`; the file ledger SHALL contain exactly the fully expanded Blueprint paths in `slot_only` state; and the revision ledger SHALL contain `entries=[]`. No rendering view, materialization event, migration classification, preservation result, execution state, epoch receipt, or binding receipt SHALL appear during S4 publication. Historical runs using older Schema versions SHALL not be upgraded or mixed with this fresh-run contract. (Design 8.0.2: §5.2, §5.6.7, §6.4.7, §10.2.1-§10.2.2; pipeline design 2.0.2 §4.2-§4.4, §5.3; M1-5 fresh-run handoff.)

#### Scenario: Fresh initial artifacts conform
- **WHEN** a publishable S4 candidate and its complete Blueprint are sealed for a new run
- **THEN** every artifact validates under the current fresh-run Schemas, uses project canonical bytes, every task contains recomputable metadata, and the v2 file-ledger path set equals the Blueprint concrete path set

#### Scenario: Initial ledger contains a materialization fact
- **WHEN** S4 publication is asked to emit any typed event or a file row with realized evidence
- **THEN** publication rejects it because only successful S5 may append E0 materialization and realization facts

#### Scenario: An old run is presented for automatic upgrade
- **WHEN** a persisted run uses a pre-M1-5 Run or ledger Schema
- **THEN** the current fresh-run path refuses to rewrite or combine it and requires the matching historical implementation to read it

### Requirement: The S4 seal carries independent typed anchors
The S4 Run output contract SHALL contain a permanent file reference for immutable Plan 1.0.0, a file reference for the mutable active pointer, and independent SHA-256 values for the Delivery Blueprint and sealed configuration snapshot. Initial completion verification SHALL require both Plan references to identify 1.0.0. After typed ledger events exist, completion verification SHALL keep `output_refs.plan` bound to 1.0.0 and SHALL derive the active Plan only from the most recent accepted `revision_activated` event; if no activation exists, the active pointer SHALL remain 1.0.0/E0/revision-sequence 0 even when accepted `epoch_materialized` or other non-activation events follow. When an activation exists, `output_refs.active_plan`, Plan State, the most recent activation payload, current epoch/binding facts, and all referenced immutable artifacts SHALL agree. Existing non-S4 stages and M0 lint commands SHALL retain their behavior except where the fresh-run typed contract is their direct input. (Design 8.0.2: §4.8, §5.2, §5.6.7, §6.4.7; pipeline design 2.0.2 §4.2-§4.4; D1.8/D1.16; M1-5.)

#### Scenario: E0 materialization follows the initial seal
- **WHEN** the ledger contains a valid E0 `epoch_materialized` event but no `revision_activated` event
- **THEN** S4 completion verification accepts the unchanged 1.0.0/E0/0 pointer while validating the materialization refs separately

#### Scenario: A non-activation event is the ledger tail
- **WHEN** one or more accepted non-activation events follow the latest valid activation
- **THEN** S4 completion verification uses the latest activation for pointer/version/epoch comparison and still validates the complete event hash chain

#### Scenario: One anchor drifts
- **WHEN** a file reference, Blueprint hash, configuration hash, initial Plan ref, active-pointer target, activation payload, epoch/binding fact, State binding, or ledger link disagrees
- **THEN** completion verification rejects the run and downstream admission does not trust the remaining agreeing values
