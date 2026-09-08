## MODIFIED Requirements

### Requirement: Joint S6 verification records every lease member
After a legal F1 lease or F3 repair-group joint commit exists, S6 SHALL append exactly one `verification_committed` event whose deterministic verification id, kind, sorted member uid/evidence references, commit SHA and current revision sequence agree with Joint Evidence and the verification WAL. A lease result SHALL use `kind=lease`, bind its accepted lease id/start/finish facts and preserve owner/attempt history. A migration-group result SHALL use `kind=group`, bind its accepted revision and exact frozen group id/membership, and require no lease events. Each event SHALL be published as part of its corresponding all-member transaction and SHALL be recoverable only forward after the legal commit. A strict subset of members, an ordinary single-task trailer, the wrong kind/group/lease identity or a changed revision sequence SHALL be invalid. (Design: §5.4, §5.6.7; pipeline §5.6.1, §7.2; D1.15; M1-7/M1-9.)

#### Scenario: Lease joint verification is recorded
- **WHEN** two lease members have valid per-task evidence and one legal joint commit
- **THEN** one lease-kind verification event names both sorted members and binds the accepted lease facts

#### Scenario: Group joint verification is recorded
- **WHEN** every frozen F3 group member has current migration evidence and one legal joint commit
- **THEN** one group-kind verification event names the exact sorted group and current revision without a lease event

#### Scenario: Verification omits a member
- **WHEN** the event or Joint Evidence lists only a strict subset of the accepted lease or group members
- **THEN** validation rejects the transaction as incomplete

#### Scenario: Recovery sees commit but no event
- **WHEN** the trailer-valid joint commit and evidence exist but verification publication was interrupted
- **THEN** reconciliation appends the exact missing event once and does not create another commit

## ADDED Requirements

### Requirement: Migration verification WAL binds every pre-commit and post-commit fact
The verification WAL SHALL represent normal, lease and migration-group transactions as closed discriminated forms. A migration-group WAL SHALL bind the group id, current activation/migration refs, exact sorted non-empty member ids/uids/modes/evidence sequences, immutable epoch checkpoint commit/tree, per-group transaction baseline commit/tree, persisted candidate and failure refs, build/smoke refs, expected joint evidence and commit trailers, and complete old/allocated/new State, file-ledger and revision-ledger snapshots. Lease membership SHALL retain its minimum of two. Before a legal commit the WAL SHALL be recovery information only and SHALL NOT make candidate evidence or partial State accepted. After a legal commit it SHALL be sufficient to validate and publish every member and the one verification event without replaying model calls or validation. Exact replay SHALL be idempotent; any conflicting live or WAL-bound fact SHALL fail closed. (Design: system §5.6.7; pipeline §5.6.1; D1.15; M1-9.)

#### Scenario: Group WAL exists before commit
- **WHEN** some group calls, candidates or evidence are persisted but no trailer-valid joint commit exists
- **THEN** none of those artifacts constitutes accepted member completion and recovery returns to the recorded baseline

#### Scenario: Group commit exists before State
- **WHEN** the legal joint commit and exact member/joint evidence exist but one or more mutable projections are old
- **THEN** reconciliation derives and writes only the complete new State, file ledger and verification event

#### Scenario: WAL replay conflicts with accepted history
- **WHEN** a member sequence, migration ref, candidate tree, evidence hash, commit trailer or projected snapshot differs from the accepted transaction
- **THEN** reconciliation reports artifact damage without choosing one version or publishing a subset
