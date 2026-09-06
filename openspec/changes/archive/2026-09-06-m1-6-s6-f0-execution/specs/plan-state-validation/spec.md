## ADDED Requirements

### Requirement: Fresh S6 State uses the complete version-two execution contract
For new M1-6 runs, Plan State SHALL use `schema_version="2.0"`, bind the active Plan path/hash/version/epoch/revision sequence, contain exactly one row for every active task, retain all historical uid evidence counters and record run-wide `s6_attempts_used`. A fresh row SHALL be `pending/normal` with attempts and amendment use zero and empty commit, error, notes, migration, group and acceptance evidence fields. Old major-version State SHALL remain read-only and SHALL NOT be upgraded in place or mixed into a new run. (Design: §5.2.4, §5.6.7; M1-6.)

#### Scenario: Initial State is projected from ready E0
- **WHEN** a fresh M1-6 S6 admission initializes State from the active E0 Plan
- **THEN** every Plan task appears once in the unique pending normal row and both task and run counters are zero

#### Scenario: State major versions are mixed
- **WHEN** a new S6 path receives an older State contract or evidence from another major version
- **THEN** validation rejects the run without an automatic migration

### Requirement: Ordinary F0 transitions allocate attempts and derive terminal fields
The typed Plan-State event API SHALL derive `attempt_started`, `attempt_succeeded`, `attempts_exhausted`, `dependency_blocked` and `reconciled_commit` transitions from complete old State and proof. Starting a normal attempt SHALL increment that task's attempts and global `s6_attempts_used` exactly once before external execution. Success SHALL require a matching accepted commit and Task Evidence; exhaustion SHALL require the final failed-attempt evidence; dependency blocking SHALL require the current Plan graph and blocking State; reconciliation SHALL require the complete verification WAL proof. Callers SHALL NOT submit arbitrary replacement State or alter unrelated task rows. (Design: §5.2.4-§5.2.5, §5.6.7; M1-6.)

#### Scenario: An attempt is allocated
- **WHEN** a pending or in-progress normal task has local and global capacity and receives `attempt_started`
- **THEN** only its attempt count and the global started-call count increment and its status becomes in-progress

#### Scenario: A task success is derived
- **WHEN** an in-progress task receives exact commit/evidence/verification proof for its current attempt
- **THEN** only that task becomes done with matching notes, commit and evidence and no last error

#### Scenario: Dependency blocking is asserted without proof
- **WHEN** a caller requests `dependency_blocked` for a task whose dependency closure contains no blocked task
- **THEN** transition validation rejects the event without changing State

#### Scenario: Reconciliation proof covers only part of a transaction
- **WHEN** a legal task commit exists but the proposed State omits or disagrees with its evidence, ledger or event binding
- **THEN** the reconciled transition is rejected as incomplete

### Requirement: Evidence counters and execution lint close the ordinary task chain
Before writing an attempt artifact the system SHALL monotonically allocate an evidence sequence per task uid and persist it in State; gaps caused by interruption SHALL remain legal and sequences SHALL never be reused. Execution lint SHALL verify each done task's Plan/version/epoch/task/uid/attempt/sequence, evidence bytes, commit ancestry/tree/trailers, changed-file whitelist, realized file-ledger rows, E0 ancestry and current clean workspace relation. It SHALL also reject an in-progress or blocked row whose immutable attempt history contradicts its counters or last-error reference. (Design: §5.2.4-§5.2.5, §5.6.7; M1-6.)

#### Scenario: Evidence sequence is consumed by a crash
- **WHEN** a sequence is persisted and execution stops before its evidence object is completed
- **THEN** the next attempt receives a greater sequence and validation accepts the unused gap

#### Scenario: Done State matches external facts
- **WHEN** State, Task Evidence, task commit, file ledger and workspace ancestry bind the same accepted tree
- **THEN** complete execution lint accepts the done task

#### Scenario: Task evidence points at a different tree
- **WHEN** a done row's evidence, commit or file hashes disagree
- **THEN** execution lint rejects the State rather than trusting the done label
