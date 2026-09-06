## MODIFIED Requirements

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
