## ADDED Requirements

### Requirement: E1+ materialization events bind the accepted active revision
After an E1+ S5 instance is atomically accepted, the revision ledger SHALL append exactly one `epoch_materialized` event whose revision sequence equals the active pointer, whose epoch receipt and binding refs match the accepted S5 output, and whose referenced Plan/epoch agree with the latest accepted F3 activation. Replaying the same materialization identity and payload SHALL be idempotent; a conflicting event, a future revision, an event before Run acceptance, or an event inconsistent with the active pointer SHALL fail closed. The event SHALL advance only `event_seq` and SHALL NOT increment `revision_seq` or modify the active Plan. (Design 8.0.2: §5.6.7; pipeline design 2.0.2 §4.2-§4.4, §5.4; D1.15; M1-8.)

#### Scenario: E1 event follows its F3 activation
- **WHEN** the accepted active F3 revision has completed E1 materialization and the Run S5 instance binds its receipts
- **THEN** one matching `epoch_materialized` event is appended without changing revision sequence or active pointer

#### Scenario: An E1 event conflicts with the active revision
- **WHEN** its revision sequence, epoch receipt, binding or materialized Plan disagrees with the accepted active pointer and latest activation
- **THEN** validation rejects the event and no accepted ledger history is rewritten

#### Scenario: A missing E1 event is recovered after evidence validation
- **WHEN** the current Run S5 instance is done and its event is missing
- **THEN** the event is appended only after the complete referenced epoch, binding, manifest, map, checkpoint, ledger, workspace and evidence chain validates

### Requirement: Cross-epoch file-ledger transitions preserve realized evidence
The file-ledger transition for E1+ SHALL account for every old active or quarantined row and every new active Blueprint path. Retained realized `s6_owned` rows SHALL preserve content and historical verification fields; an ownership/version change SHALL append the declared owner history without fabricating new verification. Newly rendered frozen rows SHALL carry current S5 epoch proof, new task-owned stubs SHALL remain `slot_only`, retired realized rows SHALL become `quarantined` with their prior evidence plus the current epoch and quarantine path, and explicit re-adoption SHALL restore only the named quarantined content to its declared target while requiring later validation for changed obligations. Any dropped realized row, invented evidence, unregistered orphan, duplicate active/quarantine path or workspace/ledger disagreement SHALL fail. (Design 8.0.2: §5.6.7, §6.5; pipeline design 2.0.2 §3.3, §3.5, §5.4; D1.12; M1-8.)

#### Scenario: Realized owned content spans an epoch
- **WHEN** a realized task-owned path remains active in E1
- **THEN** its content and historical evidence are retained and only explicitly changed owner/version history is appended

#### Scenario: A realized row disappears without quarantine
- **WHEN** an old realized path is absent from both the new active paths and the declared quarantine transitions
- **THEN** the projected ledger is invalid and E1 cannot be accepted

### Requirement: F2 versions receive metadata-only bindings
For an already-accepted F2 Plan version, the system SHALL recompute the Delivery Blueprint, artifact manifest and contract map from that version and its frozen inputs, publish immutable copies and one binding receipt under `plan/bindings/<version>/`, and atomically replace only the current manifest/map copies after validating their exact refs. The F2 binding SHALL reference the existing epoch receipt and SHALL reflect current Plan owner/provider/file metadata. It SHALL NOT invoke S5 source rendering, change any workspace byte or Git ref, create an epoch checkpoint or receipt, reopen the Run S5 instance, append `epoch_materialized`, or reinterpret prior verification as proof of changed obligations. Exact replay SHALL be a zero-change operation and conflicting immutable bytes SHALL fail closed. (Design 8.0.2: §5.6.5.4, §5.6.7, §6.5; pipeline design 2.0.2 §4.1-§4.4, §6.4; M1-8.)

#### Scenario: F2 changes task ownership metadata
- **WHEN** an accepted F2 version changes owner/provider metadata without changing the structural epoch
- **THEN** its new binding and immutable manifest/map reference the existing epoch receipt while workspace and checkpoint remain byte-identical

#### Scenario: F2 rebinding is replayed
- **WHEN** the exact binding already exists and all current copies agree
- **THEN** replay returns the existing binding without changing files, events, receipts, Git refs or timestamps

#### Scenario: F2 binding would require source regeneration
- **WHEN** the proposed metadata projection cannot be represented without changing the epoch's structural workspace
- **THEN** the F2 binding is rejected rather than regenerating source or silently creating an epoch
