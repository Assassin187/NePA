## MODIFIED Requirements

### Requirement: Plan State Schema and snapshot lint enforce one unambiguous state
The system SHALL provide a closed draft-2020-12 Plan State v2 Schema and conforming examples for initial and migrated snapshots. Snapshot lint SHALL validate the current active Plan reference, immutable S4 1.0.0 anchor, active-pointer reference, complete accepted revision chain, current binding/epoch, configuration binding, exact current task-id/uid set, configured attempt limit, run-wide `s6_attempts_used`, per-task evidence counters and all status-specific migration/group invariants. Initial state SHALL contain every 1.0.0 task exactly once as `pending/normal` with zero attempts and empty/null execution fields. A migrated task SHALL bind the exact accepted activation row: INHERIT preserves its accepted completion; REVALIDATE is pending with preserved ordinary attempts and no new success proof; AMEND is pending with preserved attempts and `amendment_used=0` even when ordinary attempts equal the limit; REGENERATE is a new pending normal generation at zero attempts. Frozen group members SHALL carry their exact `group_id`; old-major State SHALL remain read-only and SHALL NOT be upgraded in place or mixed into a new run. (Design: §5.2.4-§5.2.5, §5.6.7; pipeline §3.2, §4.2-§4.3, §5.6.1, §6.4-§6.5; D1.8-D1.9/D1.15; M1-4d/M1-9.)

#### Scenario: Initial snapshot is constructed
- **WHEN** Plan State is initialized from valid active Plan 1.0.0 and configuration
- **THEN** its task ids equal the Plan task ids and every task satisfies the unique initial-state row

#### Scenario: A migrated snapshot is checked
- **WHEN** active Plan, pointer, binding, epoch, State and complete ledger lineage agree
- **THEN** snapshot lint validates the current task set and every inherited, revalidated, amended or regenerated row against its accepted migration row

#### Scenario: Revalidation is pending after activation
- **WHEN** an activation classifies a task as REVALIDATE but no current-version verification commit exists
- **THEN** the legal row is pending/revalidate with no current success evidence rather than done

#### Scenario: Full ordinary history is preserved for AMEND
- **WHEN** a done task with four ordinary attempts is migrated as AMEND
- **THEN** its pending/amend row preserves attempts=4 and remains eligible only through `amendment_used`

#### Scenario: State still binds the initial seal
- **WHEN** the active pointer names a later valid Plan but State still references 1.0.0
- **THEN** snapshot lint rejects the artifact rather than treating the immutable S4 Plan anchor as the current Plan

#### Scenario: State fields are ambiguous
- **WHEN** a status carries an attempt, amendment, group, commit, evidence, note or error forbidden by its initial or proven migrated row
- **THEN** snapshot lint rejects the artifact as damaged

#### Scenario: State major versions are mixed
- **WHEN** a new S6 path receives an older State contract or evidence from another major version
- **THEN** validation rejects the run without an automatic migration

### Requirement: State transitions are derived from the closed event table
Transition validation SHALL accept only the design-defined event types and SHALL derive the unique legal complete next State from old State and typed proof. `attempt_started` SHALL apply only to normal mode and increment ordinary attempts and `s6_attempts_used`; an AMEND start SHALL preserve ordinary attempts, change `amendment_used` from zero to one and increment only `s6_attempts_used`; `validation_started` SHALL allocate only a new evidence sequence for pending REVALIDATE. Normal/AMEND success and `revalidation_passed` SHALL require matching accepted commit/evidence; their failure events SHALL produce blocked rows using the applicable exhausted-call or validation proof. `dependency_blocked` SHALL require the current Plan graph and blocking State, while `reopened_by_revision` SHALL require an accepted activation that changes the task or proves its blocking dependency is no longer blocked. `amended_under_lease` SHALL retain the existing F1 all-member semantics. `group_verified` SHALL atomically derive every frozen in-progress group member to done under one commit/tree and complete evidence set; group failure SHALL atomically block every unresolved member without publishing partial success. Revision projection SHALL consume one complete validated migration report: INHERIT preserves completion and historical proof, REVALIDATE/AMEND/REGENERATE create the pending forms defined above, and removed tasks leave active State while remaining auditable in the ledger. Arbitrary replacement State, premature revalidation proof, partial group transition or per-task migration without complete accepted lineage SHALL be rejected. (Design: §5.2.4-§5.2.5, §5.6.7; pipeline §3.2, §5.6.1, §6.4-§7.2; D1.6/D1.15; M1-4d/M1-6/M1-7/M1-9.)

#### Scenario: Legal normal attempt succeeds
- **WHEN** an in-progress normal task receives `attempt_succeeded` with valid current-attempt commit and evidence bindings
- **THEN** the derived state is done with no last error and unchanged unrelated task states

#### Scenario: A complete migration is projected
- **WHEN** every old and new task is accounted for by one accepted migration report
- **THEN** one deterministic new State references the new Plan and contains pending migration rows without requiring future verification results

#### Scenario: A full-history AMEND starts
- **WHEN** a pending AMEND row has attempts equal to the ordinary limit, `amendment_used=0` and global capacity
- **THEN** its start preserves attempts, sets `amendment_used=1`, increments global usage once and becomes in-progress

#### Scenario: Revalidation starts without a coding call
- **WHEN** a pending REVALIDATE row receives complete baseline, migration and evidence-sequence proof
- **THEN** it becomes in-progress without changing ordinary attempts, amendment usage or `s6_attempts_used`

#### Scenario: Revision removes a dependency block
- **WHEN** an accepted activation proves that a blocked-by-dependency task's current graph no longer contains a blocked ancestor
- **THEN** `reopened_by_revision` derives it to pending while preserving its migration mode and historical usage

#### Scenario: Caller requests an unlisted transition
- **WHEN** an event skips the table, exceeds an allowance or reopens a terminal state without designated proof
- **THEN** transition validation rejects it without mutating State

#### Scenario: Dependency blocking is asserted without proof
- **WHEN** a caller requests `dependency_blocked` for a task whose dependency closure contains no blocked task
- **THEN** transition validation rejects the event without changing State

#### Scenario: Lease result covers every member
- **WHEN** a valid F1 proof names the current task and every lending done task under one accepted commit/tree and per-member evidence set
- **THEN** `amended_under_lease` derives all member rows together, preserves lender attempts and changes no owner

#### Scenario: Group result covers every member
- **WHEN** a valid group proof names the exact frozen members, current migration refs, evidence refs and one accepted commit/tree
- **THEN** `group_verified` derives every member done in one complete State and leaves unrelated rows unchanged

#### Scenario: Reconciliation proof covers only part of a transaction
- **WHEN** a legal task or joint commit exists but the proposed State omits or disagrees with any required member evidence, ledger or event binding
- **THEN** the reconciled transition is rejected as incomplete

### Requirement: Execution lint verifies external evidence separately from snapshot shape
Before writing an attempt or validation artifact the system SHALL monotonically allocate an evidence sequence per task uid and persist it in State; gaps caused by interruption SHALL remain legal and sequences SHALL never be reused. Execution-state lint SHALL validate each done task's active Plan/version/epoch/task/uid/mode/attempt/amendment/sequence, migration lineage, evidence path/content SHA-256, commit existence and ancestry, required ordinary or joint trailers, changed-file whitelist, realized file-ledger rows, current binding/S5 anchors, workspace relation and accepted verification event. It SHALL retain the existing F1 lease checks. For a migration group it SHALL additionally validate exact frozen membership, group id, per-member Task Evidence, sorted `kind=group` Joint Evidence, shared tree/commit, group-only trailers, complete WAL/event facts, zero-call REVALIDATE and independent AMEND accounting. INHERIT MAY retain old evidence only through complete lineage; REVALIDATE MUST bind new current-version evidence and MAY use a same-tree commit with attempt zero; AMEND MUST preserve ordinary attempts while proving its single amendment; REGENERATE MUST bind its new generation. An in-progress or blocked row SHALL agree with immutable call/validation history and failure refs. Complete execution validation SHALL combine snapshot and external checks; JSON-only validation SHALL NOT claim filesystem or Git verification. (Design: §4.8, §5.2.4-§5.2.5, §5.4, §5.6.7; pipeline §3.1-§3.3, §5.6.1, §6.5-§7.2; D1.1/D1.2/D1.8/D1.15; M1-4d/M1-6/M1-7/M1-9.)

Normal, AMEND and REGENERATE evidence SHALL name a non-empty legal subset of its explicit writable set and prove real byte changes; REVALIDATE changed files SHALL be empty.

#### Scenario: Done task evidence is current
- **WHEN** a done task's State, evidence, commit tree, trailers, active Plan acceptance and stage anchors agree
- **THEN** execution lint accepts that task evidence

#### Scenario: Inherited evidence has valid lineage
- **WHEN** the current task row preserves older commit/evidence and the ledger proves an INHERIT mapping from that old task id to the same semantic uid
- **THEN** execution lint accepts the historical binding without rewriting immutable evidence

#### Scenario: Historical evidence lacks lineage
- **WHEN** an old task id or Plan hash is presented without a valid unbroken migration row
- **THEN** execution lint rejects it even if the referenced bytes and commit exist

#### Scenario: Revalidation evidence has no call
- **WHEN** current-version REVALIDATE evidence, same-tree commit, migration row and build/smoke proof agree and no coding attempt was started
- **THEN** execution lint accepts attempt zero without increasing run call usage

#### Scenario: AMEND preserves full attempts
- **WHEN** AMEND evidence binds one T1 Fixer call, `amendment_used=1` and four preserved ordinary attempts
- **THEN** execution lint accepts the result without interpreting it as a fifth normal attempt

#### Scenario: Lease evidence is complete
- **WHEN** every lease member row, evidence ref, joint evidence member, commit trailer, file-ledger row and accepted event agrees on one tree and verification id
- **THEN** execution lint accepts the lender's retained done state and current task's new done state

#### Scenario: Group evidence is complete
- **WHEN** every frozen group member, evidence ref, Joint Evidence member, commit trailer, migration ref, file-ledger row and verification event agrees on one tree
- **THEN** execution lint accepts the all-member group result

#### Scenario: Joint evidence is only partially bound
- **WHEN** one lease or group member is absent, retains an impermissible old proof, or disagrees on tree, commit, mode, sequence or migration
- **THEN** execution lint rejects the complete State rather than accepting the other members

#### Scenario: Commit or evidence binding drifts
- **WHEN** any referenced bytes, trailer, ancestry, acceptance result, stage anchor, pointer or ledger mapping disagrees
- **THEN** execution lint rejects the State instead of trusting its done label

#### Scenario: Evidence sequence is consumed by a crash
- **WHEN** a sequence is persisted and execution stops before its evidence object is completed
- **THEN** the next applicable allocation receives a greater sequence and validation accepts the unused gap

#### Scenario: Snapshot-only validation is requested
- **WHEN** only Plan and State JSON are supplied without workspace, Git and evidence stores
- **THEN** snapshot lint reports only structural/state results and does not claim external execution validity
