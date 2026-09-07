# plan-revision-infrastructure Specification

## Purpose

Define the deterministic and crash-recoverable infrastructure that classifies preserved work, advances immutable Plan versions, and binds Plan State, file ownership, revision history, the active pointer, and Run metadata into one auditable chain.

## Requirements

### Requirement: Migration classification is complete and deterministic
Given an old active Plan, candidate Plan, old Plan State, file ledger, and optional explicit split/merge lineage, the system SHALL emit one canonical migration report accounting for every old and new task and every old `realized` file. It SHALL use only task uid, obligation/guidance digests, exact responsibility/file sets, contract export signatures, status and explicit lineage. It SHALL classify unchanged obligation as `INHERIT`; owner-only reassignment of unchanged realized content as `REVALIDATE`; a changed obligation on a previously done task with no new file and responsibility Jaccard at least 0.5 as `AMEND`; and a new task, never-done task, task with a new deliverable file, or task below that Jaccard threshold as `REGENERATE`. It SHALL NOT infer lineage from names, text similarity or file overlap. (Design 7.2.0: §5.2.2-§5.2.5; pipeline design 1.3.0 §3.1-§3.2; M1-4d.)

#### Scenario: Only guidance changes
- **WHEN** matched tasks have equal uid and obligation digest but different guidance digest
- **THEN** the task and its surviving realized files are classified `INHERIT`

#### Scenario: A task is split explicitly
- **WHEN** candidate tasks have new uids and the supplied lineage records their `derived_from` old uid
- **THEN** classification uses that declared lineage and records the old/new mapping

#### Scenario: A task resembles an old task without lineage
- **WHEN** a new uid has overlapping files or similar text but no explicit lineage
- **THEN** it is `REGENERATE` and no inferred ancestry is recorded

#### Scenario: Classification inputs are replayed
- **WHEN** byte-identical inputs are classified twice
- **THEN** the migration reports are byte-identical and contain canonically ordered task/file rows

### Requirement: Preservation rate is a file-level pre-activation fact
The migration report SHALL compute `preservation_rate` as the count of old realized files classified `INHERIT` or `REVALIDATE` divided by the total old realized-file count. Removed realized files SHALL remain in the denominator and be classified `REGENERATE`; new files SHALL not enter the old denominator. A zero denominator SHALL yield exactly `1.0`. Aggregate counts SHALL be recomputable from the report rows and SHALL NOT replace them. (Design 7.2.0: §9.1.4; pipeline design 1.3.0 §3.3-§3.4; M1-4d.)

#### Scenario: No realized file exists
- **WHEN** the old file ledger contains only slot-only rows
- **THEN** preservation rate is `1.0` and every aggregate count is recomputable

#### Scenario: A realized file loses its slot
- **WHEN** an old realized path is absent from the candidate Blueprint
- **THEN** it remains in the denominator as `REGENERATE` and is retained for later quarantine handling

### Requirement: File and revision ledgers are closed append-only contracts
The file ledger SHALL use `schema_version="2.0"`, top-level `files`, and closed conditional rows for `slot_only`, `realized`, and `quarantined` evidence. A slot-only row SHALL contain only path/class/state and SHALL not claim content or verification; an S6-owned realized row SHALL carry content/checkpoint/verification fields and non-empty owner history; an S5-frozen realized row SHALL carry content/checkpoint/verification fields plus `created_by_stage="s5"` and epoch-receipt provenance and SHALL not carry task owner history. The revision ledger SHALL use `schema_version="2.0"` and append-only `entries` with consecutive `event_seq` beginning at 1, closed `event_type`-specific payloads, and a canonical predecessor hash over the complete previous entry; the first predecessor SHALL be 64 zeroes. The supported event types SHALL be `trigger_evaluated`, `candidate_rejected`, `revision_activated`, `epoch_materialized`, `lease_started`, `lease_finished`, `verification_committed`, and `revision_evaluated`. Only `revision_activated` SHALL increment `revision_seq`; event sequence and revision sequence SHALL remain independent. An E0 `epoch_materialized` payload SHALL bind `revision_seq=0`, the accepted epoch receipt, and the accepted version binding, and SHALL not change the active pointer. Validation SHALL reject aliases, illegal state fields, missing event payload fields, unrelated payload fields, gaps, duplicate sequence, a broken hash chain, event references to future/unaccepted events, aggregate drift, or mutation of a prior entry. Runtime producers other than E0 materialization remain assigned to their later milestone work items. (Design 8.0.2: §5.6.7; pipeline design 2.0.2 §3.3, §4.2-§4.4; M1-5 and later typed-event producers.)

#### Scenario: Initial ledgers are empty
- **WHEN** fresh S4 publication completes before E0 materialization
- **THEN** both ledgers use schema version 2.0, every Blueprint path is slot-only in the file ledger, and the revision ledger has `entries=[]`

#### Scenario: E0 materialization is recorded first
- **WHEN** S5 has atomically accepted the E0 epoch and binding and no earlier event exists
- **THEN** the ledger appends event sequence 1 with 64-zero predecessor, `event_type=epoch_materialized`, `revision_seq=0`, and exact receipt/binding refs while the active pointer remains 1.0.0/E0/0

#### Scenario: E0 event append is replayed
- **WHEN** recovery requests the same `epoch_materialized` fact after it is already the accepted event for E0
- **THEN** the append is an idempotent no-op, while a conflicting event for the same epoch is rejected

#### Scenario: A non-activation event follows an activation
- **WHEN** an accepted materialization or verification event is appended after the latest `revision_activated`
- **THEN** pointer validation compares against the latest activation event rather than requiring the ledger tail itself to be an activation

#### Scenario: A prior event is changed
- **WHEN** any byte of an earlier entry changes while a later entry retains the former predecessor hash
- **THEN** chain validation fails and no active pointer, epoch, or binding is trusted

#### Scenario: S5-frozen and S6-owned files are distinguished
- **WHEN** E0 has accepted generated frozen files and task-owned stubs
- **THEN** frozen files are realized with S5/epoch proof while task-owned stubs remain slot-only without fabricated implementation evidence

### Requirement: Plan versions and epochs advance monotonically
Plan versions SHALL use C.A.P with C fixed at 1 for one run. An F2 Plan activation SHALL increment P by one and preserve epoch; an F3 Plan activation SHALL increment A by one, reset P to zero, and increment epoch from `E<n>` to `E<n+1>`. Revision sequence SHALL increment by exactly one independently of version components. Every version path SHALL be `plan/versions/plan-<C.A.P>.json`, and once activated its canonical bytes SHALL never change. F1 ledger entries, trigger evaluation, and F2/F3 candidate generation SHALL NOT be implemented by this capability. (Design 7.2.0: §4.3-§4.4; pipeline design 1.3.0 §4.1-§4.4; M1-4d/M1-4e boundary.)

#### Scenario: F2 is activated
- **WHEN** active version is 1.2.3 in E2 and an approved F2 candidate is supplied
- **THEN** the only legal target is 1.2.4 with E2 and the next revision sequence

#### Scenario: F3 is activated
- **WHEN** active version is 1.2.3 in E2 and an approved F3 candidate is supplied
- **THEN** the only legal target is 1.3.0 with E3 and the next revision sequence

#### Scenario: A version skips or regresses
- **WHEN** candidate version, sequence or epoch differs from the unique legal successor
- **THEN** activation fails before a version file or mutable control artifact is changed

### Requirement: Revision activation has one locked logical commit point
Activation SHALL require a validated candidate Plan, migration report, projected Plan State/file ledger, append candidate and expected current pointer under the existing controller lock. Before changing a live artifact it SHALL persist a canonical `_s4r/rev_NNN/activation.json` WAL binding old/new refs and hashes. It SHALL then publish the immutable Plan version, atomically replace migrated State and file ledger, atomically replace the append-only revision ledger, atomically advance `active_plan.json`, and finally update only the Run's active-pointer reference. Pointer advancement SHALL be the logical commit point; the immutable S4 `output_refs.plan` SHALL remain bound to 1.0.0. A caller SHALL NOT activate an unvalidated partial migration or bypass the lock. (Design 7.2.0: §4.8, §5.2, §6.4.7; pipeline design 1.3.0 §4.2-§4.4, §6.4; M1-4d.)

#### Scenario: Activation completes
- **WHEN** every input and expected old hash validates and every write succeeds
- **THEN** Plan, State, both ledgers, active pointer and Run reference form one self-consistent new active revision

#### Scenario: Concurrent controller attempts activation
- **WHEN** another cooperating controller holds the run lock
- **THEN** activation changes no artifact and reports the existing lock failure

#### Scenario: An input changes after rehearsal
- **WHEN** current pointer, State, file ledger, revision ledger or candidate hash differs from the WAL-bound expectation
- **THEN** activation fails closed rather than recomputing or accepting the drift

### Requirement: Crash recovery converges to one legal revision state
Recovery SHALL use the active pointer as authority and the immutable activation WAL as reconciliation evidence. If the revision ledger did not advance, recovery SHALL restore old State/file-ledger bytes and isolate an unactivated candidate version under its `_s4r/rev_NNN/` directory. If the ledger advanced but the pointer did not, recovery SHALL verify and complete the new State/file ledger and then advance the pointer. If the pointer advanced, recovery SHALL only verify all new artifacts and complete a missing Run active-pointer reference. Conflicting bytes, an absent required WAL, invalid chain, or non-monotonic version/epoch SHALL be reported as artifact damage without guessing, deleting realized content, or rolling back an activated pointer. (Design 7.2.0: §4.8, §5.2; pipeline design 1.3.0 §4.2-§4.4, §6.4; D1.13; M1-4d.)

#### Scenario: Crash occurs before ledger append
- **WHEN** version or mutable migration outputs exist but the ledger remains at the old sequence
- **THEN** recovery restores the WAL-bound old mutable bytes, isolates the candidate version, and leaves the old pointer active

#### Scenario: Crash occurs after ledger append
- **WHEN** the new entry and referenced artifacts validate but the pointer remains old
- **THEN** recovery advances the pointer and then completes the Run reference

#### Scenario: Crash occurs after pointer advancement
- **WHEN** all revision artifacts agree but Run still references the prior pointer bytes
- **THEN** recovery updates only the Run active-pointer reference

#### Scenario: Recovery sees conflicting bytes
- **WHEN** any live or WAL-bound artifact has an unexpected hash
- **THEN** recovery reports damage and performs no speculative repair

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
