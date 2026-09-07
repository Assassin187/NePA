## ADDED Requirements

### Requirement: F1 leases use typed non-revision ledger events
S6 SHALL append one canonical `lease_started` event before each accepted F1 invocation with deterministic `lease_id`, current task uid, lending uid/path rows, baseline commit, current execution count and authorization evidence. It SHALL append exactly one matching `lease_finished` event: failure requires a reason and associated call references, while success requires the accepted joint evidence reference, commit SHA and associated call references. Replaying a byte-equivalent lease identity/event SHALL be idempotent and a conflicting duplicate SHALL fail closed. Lease events SHALL advance only `event_seq`; they SHALL NOT increment `revision_seq`, move the active Plan pointer or appear in `revision.count_by_level`. (Design: §5.6.7, §9.1.4; pipeline §7.2, §9; M1-7.)

#### Scenario: Lease begins and succeeds
- **WHEN** an eligible F1 attempt is durably authorized and its legal joint transaction completes
- **THEN** the ledger contains one matching start and successful finish pair while active Plan version/epoch/revision sequence remain unchanged

#### Scenario: Lease validation fails
- **WHEN** an accepted lease start is followed by candidate, build or smoke failure before a legal joint commit
- **THEN** the ledger contains one failed finish with reason and call references and no revision activation

#### Scenario: Lease event is replayed
- **WHEN** recovery appends the same lease identity and byte-equivalent payload again
- **THEN** no duplicate accepted event is created

#### Scenario: Lease identity conflicts
- **WHEN** a known lease id is presented with another task, path, commit or outcome
- **THEN** ledger validation reports artifact damage and preserves accepted history

### Requirement: Joint S6 verification records every lease member
After a legal F1 joint commit exists, S6 SHALL append exactly one `verification_committed` event whose deterministic verification id, `kind=lease`, sorted member uid/evidence references, commit SHA and current revision sequence agree with Joint Evidence and the verification WAL. The event and successful `lease_finished` SHALL be published as parts of the same all-member transaction and SHALL be recoverable only forward after the legal commit. A strict subset of members, an ordinary single-task trailer or a changed revision sequence SHALL be invalid. (Design: §5.4, §5.6.7; pipeline §7.2; M1-7.)

#### Scenario: Joint verification is recorded
- **WHEN** two lease members have valid per-task evidence and one joint commit under revision sequence zero
- **THEN** one lease-kind verification event names both sorted members and leaves active Plan at 1.0.0/E0/0

#### Scenario: Verification omits the lender
- **WHEN** the event or Joint Evidence lists only the current task while the lease start identifies another member
- **THEN** validation rejects the transaction as incomplete

#### Scenario: Recovery sees commit but no finish events
- **WHEN** the trailer-valid joint commit and evidence exist but verification or lease-finished publication was interrupted
- **THEN** reconciliation appends the exact missing events once and does not create another commit
