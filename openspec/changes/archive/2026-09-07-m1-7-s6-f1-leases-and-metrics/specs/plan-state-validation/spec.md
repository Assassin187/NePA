## MODIFIED Requirements

### Requirement: Accepted State transitions have sequential history
The controller SHALL append each accepted State transition and resulting complete State to `plan/state_history.json` with a monotonic event sequence. The history SHALL not introduce an additional hash chain. In stable execution the mutable State SHALL agree semantically with the history tail; recovery MAY complete a lagging State projection from a durable attempt or WAL fact. S6 SHALL bind an immutable history snapshot in its receipt. (Design: §5.2; M1-7 repair.)

#### Scenario: Attempt publication is interrupted
- **WHEN** the attempt intent exists but history, lease event or current State publication is incomplete
- **THEN** resume completes the same attempt allocation without refunding or duplicating its attempt, evidence sequence or lease

### Requirement: State transitions are derived from the closed event table
Transition validation SHALL accept only the design-defined event types and SHALL derive the unique legal next task state from complete old State and event. Starting a normal attempt SHALL increment its task attempts and global `s6_attempts_used` exactly once before external execution. Success SHALL require matching accepted commit and Task Evidence; exhaustion SHALL require final failed-attempt evidence; dependency blocking SHALL require the current Plan graph and blocking State; reconciliation SHALL require complete verification-WAL proof. `amended_under_lease` SHALL be one atomic multi-row transition from a current in-progress normal task and one or more lending done tasks to all done rows, preserving each lender's attempts and owner history while binding every member to one joint tree, commit and evidence set. Revision projection SHALL consume a validated migration report as one complete task-set operation: `INHERIT` rewrites position ids while preserving state/attempts/commit/evidence, `REVALIDATE` requires typed successful build evidence and preserves attempts, `AMEND` reopens a completed unexhausted task while preserving attempts, and `REGENERATE` creates a clean pending/0 row. Removed tasks SHALL leave active State but remain auditable in the ledger. Arbitrary caller-authored replacement State, partial lease-member transition or per-task migration without the complete proof SHALL be rejected. (Design: §5.2.4-§5.2.5, §5.6.7; pipeline §3.2, §6.4-§7.2; D1.6; M1-4d/M1-6/M1-7.)

#### Scenario: Legal attempt succeeds
- **WHEN** an in-progress task receives `attempt_succeeded` with valid current-attempt commit and evidence bindings
- **THEN** the derived state is `done` with no last error and unchanged unrelated task states

#### Scenario: A complete migration is projected
- **WHEN** every old and new task is accounted for by one validated migration report and required revalidation proofs exist
- **THEN** one deterministic new State references the new Plan and contains exactly its task ids

#### Scenario: An exhausted task is classified AMEND
- **WHEN** AMEND would preserve attempts equal to the total task limit
- **THEN** migration projection rejects the report because the task cannot be legally reopened under AMEND

#### Scenario: Caller requests an unlisted transition
- **WHEN** an event attempts to skip the table, exceed attempts, or reopen a terminal state without designated controller proof
- **THEN** transition validation rejects it without mutating State

#### Scenario: An attempt is allocated
- **WHEN** a pending or in-progress normal task has local and global capacity and receives `attempt_started`
- **THEN** only its attempt count and the global started-call count increment and its status becomes in-progress

#### Scenario: Dependency blocking is asserted without proof
- **WHEN** a caller requests `dependency_blocked` for a task whose dependency closure contains no blocked task
- **THEN** transition validation rejects the event without changing State

#### Scenario: Lease result covers every member
- **WHEN** a valid joint proof names the current task and every lending done task under one accepted commit/tree and per-member evidence set
- **THEN** `amended_under_lease` derives all member rows together, preserves lender attempt counts and changes no owner

#### Scenario: Reconciliation proof covers only part of a transaction
- **WHEN** a legal task or joint commit exists but the proposed State omits or disagrees with any required member evidence, ledger or event binding
- **THEN** the reconciled transition is rejected as incomplete

### Requirement: Execution lint verifies external evidence separately from snapshot shape
Before writing an attempt artifact the system SHALL monotonically allocate an evidence sequence per task uid and persist it in State; gaps caused by interruption SHALL remain legal and sequences SHALL never be reused. Execution-state lint SHALL validate each done task's Plan/version/epoch/task/uid/attempt/sequence, evidence path/content SHA-256, commit existence and ancestry, required commit trailers, changed-file whitelist, realized file-ledger rows, Plan acceptance, S5 anchors, E0 ancestry, workspace relation and revision lineage. For an F1 result it SHALL additionally validate every member's newly allocated lease evidence, sorted Joint Evidence membership, shared tree/commit, joint-only trailers, accepted lease start/finish and verification events, exact external paths, unchanged owner history and preserved lender attempts. For a same-version or newly completed task, commit/evidence SHALL bind the current task id and Plan. For `INHERIT` or `REVALIDATE`, an older task id, commit or evidence binding MAY be accepted only when the validated ledger proves the old/new uid lineage and the classification permits preservation; a bare id or hash match SHALL NOT suffice. It SHALL reject an in-progress or blocked row whose immutable attempt history contradicts its counters or last-error reference. Complete execution validation SHALL combine snapshot and execution checks; JSON-only validation SHALL NOT claim filesystem or git verification. (Design: §4.8, §5.2.4-§5.2.5, §5.4, §5.6.7; pipeline §3.1-§3.3, §7.2; D1.1/D1.2/D1.8; M1-4d/M1-6/M1-7.)

#### Scenario: Done task evidence is current
- **WHEN** a done task's state, evidence, commit tree, trailers, active Plan acceptance and stage anchors agree
- **THEN** execution lint accepts that task evidence

#### Scenario: Lease evidence is complete
- **WHEN** every lease member row, evidence ref, joint evidence member, commit trailer, file-ledger row and accepted event agrees on one tree and verification id
- **THEN** execution lint accepts the lender's retained done state and current task's new done state

#### Scenario: Lease evidence is only partially bound
- **WHEN** one member retains an older proof, is absent from Joint Evidence, or disagrees on tree, commit, lease path, attempt or evidence sequence
- **THEN** execution lint rejects the complete State rather than accepting the other members

#### Scenario: Inherited evidence has valid lineage
- **WHEN** the current task row preserves older commit/evidence and the ledger proves an `INHERIT` mapping from that old task id to the same semantic uid
- **THEN** execution lint accepts the historical binding without rewriting immutable evidence

#### Scenario: Historical evidence lacks lineage
- **WHEN** an old task id or Plan hash is presented without a valid unbroken migration row
- **THEN** execution lint rejects it even if the referenced bytes and commit exist

#### Scenario: Commit or evidence binding drifts
- **WHEN** any referenced bytes, trailer, ancestry, acceptance result, stage anchor, pointer or ledger mapping disagrees
- **THEN** execution lint rejects the state instead of trusting its `done` label

#### Scenario: Snapshot-only validation is requested
- **WHEN** only Plan and State JSON are supplied without workspace, Git and evidence stores
- **THEN** snapshot lint reports only structural/state results and does not claim external execution validity
