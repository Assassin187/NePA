## ADDED Requirements

### Requirement: Ordinary S6 verification appends the existing typed ledger fact
After a legal ordinary task commit exists, S6 SHALL append exactly one `verification_committed` revision-ledger entry whose deterministic `verification_id`, `kind`, member task uid/evidence reference, commit SHA and current revision sequence agree with the verification WAL and accepted State/file-ledger transaction. Replaying the same verification id and payload SHALL be idempotent; a conflicting duplicate SHALL fail closed. Ordinary F0 verification SHALL NOT increment `revision_seq`, move the active Plan pointer, create a revision candidate, or append lease/trigger/activation events. (Design: §5.6.7; pipeline §5.6; M1-6.)

#### Scenario: Ordinary task verification is recorded
- **WHEN** one normal task commit and its evidence are accepted under E0 revision sequence zero
- **THEN** the ledger appends one `verification_committed` event for that member and leaves the active Plan at 1.0.0/E0/0

#### Scenario: Verification append is replayed
- **WHEN** recovery requests the same verification id with byte-equivalent payload
- **THEN** the ledger remains byte-equivalent and no second event is added

#### Scenario: Verification identity conflicts
- **WHEN** an existing verification id is replayed with another commit, member or evidence reference
- **THEN** validation reports artifact damage and does not alter the accepted ledger

#### Scenario: F0 failure occurs
- **WHEN** an ordinary candidate fails validation or exhausts its attempts without a legal commit
- **THEN** S6 appends no successful verification event and does not generate a revision or lease event
