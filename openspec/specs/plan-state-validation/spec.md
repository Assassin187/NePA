# plan-state-validation Specification

## Purpose

Define closed Plan State data and deterministic snapshot, transition, and execution-evidence checks without conflating mutable execution bookkeeping with the immutable Plan.

## Requirements

### Requirement: Plan State Schema and snapshot lint enforce one unambiguous state
The system SHALL provide a closed draft-2020-12 Plan State Schema and conforming examples for initial and migrated snapshots. Snapshot lint SHALL validate the current active Plan reference, immutable S4 1.0.0 anchor, active-pointer reference, complete revision chain, configuration binding, exact current task-id set, configured attempt limit, and all status-specific invariants. Initial state SHALL contain every 1.0.0 task exactly once as `pending` with zero attempts and empty/null execution fields. A migrated nonzero-attempt pending state SHALL be legal only when the latest applicable ledger task row proves an `AMEND` reopen and records its original attempts. (Design 7.2.0: §5.2.4-§5.2.5, §6.4.7; pipeline design 1.3.0 §3.2, §4.2-§4.3, §6.4-§6.5; D1.8-D1.9; M1-4d.)

#### Scenario: Initial snapshot is constructed
- **WHEN** Plan State is initialized from valid active Plan 1.0.0 and configuration
- **THEN** its task ids equal the Plan task ids and every task satisfies the unique initial-state row

#### Scenario: A migrated snapshot is checked
- **WHEN** active Plan, pointer, State and complete ledger lineage agree
- **THEN** snapshot lint validates the current task-id set and each inherited, revalidated, amended or regenerated row against its migration evidence

#### Scenario: State still binds the initial seal
- **WHEN** the active pointer names a later valid Plan but State still references 1.0.0
- **THEN** snapshot lint rejects the artifact rather than treating the immutable S4 Plan anchor as the current Plan

#### Scenario: State fields are ambiguous
- **WHEN** a status carries an attempt count, commit, evidence, note or error forbidden by its initial or proven migrated row
- **THEN** snapshot lint rejects the artifact as damaged

### Requirement: State transitions are derived from the closed event table
Transition validation SHALL accept only the design-defined event types and SHALL derive the unique legal next task state from complete old State and event. Ordinary attempt, success, exhaustion, dependency and reconciliation behavior SHALL remain unchanged. Revision projection SHALL consume a validated migration report as one complete task-set operation: `INHERIT` rewrites position ids while preserving state/attempts/commit/evidence, `REVALIDATE` requires typed successful build evidence and preserves attempts, `AMEND` reopens a completed unexhausted task while preserving attempts, and `REGENERATE` creates a clean pending/0 row. Removed tasks SHALL leave active State but remain auditable in the ledger. Arbitrary caller-authored replacement State or per-task migration without the complete report SHALL be rejected. (Design 7.2.0: §5.2.4-§5.2.5; pipeline design 1.3.0 §3.2, §6.4-§6.5; M1-4d.)

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

### Requirement: Execution lint verifies external evidence separately from snapshot shape
Execution-state lint SHALL validate each done task's commit existence and ancestry, required commit trailers, evidence path/content SHA-256, task/attempt identity, Plan acceptance, S5 anchors, workspace relation, active pointer and revision lineage. For a same-version or newly completed task, commit/evidence SHALL bind the current task id and Plan. For `INHERIT` or `REVALIDATE`, an older task id, commit or evidence binding MAY be accepted only when the validated ledger proves the old/new uid lineage and the classification permits preservation; a bare id or hash match SHALL NOT suffice. Complete execution validation SHALL combine snapshot and execution checks; JSON-only validation SHALL NOT claim filesystem or git verification. (Design 7.2.0: §4.8, §5.2.4-§5.2.5; pipeline design 1.3.0 §3.1-§3.3, §4.3; D1.1/D1.2/D1.8; M1-4d.)

#### Scenario: Done task evidence is current
- **WHEN** a done task's state, evidence, commit tree, trailers, active Plan acceptance and stage anchors agree
- **THEN** execution lint accepts that task evidence

#### Scenario: Inherited evidence has valid lineage
- **WHEN** the current task row preserves older commit/evidence and the ledger proves an `INHERIT` mapping from that old task id to the same semantic uid
- **THEN** execution lint accepts the historical binding without rewriting immutable evidence

#### Scenario: Historical evidence lacks lineage
- **WHEN** an old task id or Plan hash is presented without a valid unbroken migration row
- **THEN** execution lint rejects it even if the referenced bytes and commit exist

#### Scenario: Commit or evidence binding drifts
- **WHEN** any referenced bytes, trailer, ancestry, acceptance result, stage anchor, pointer or ledger mapping disagrees
- **THEN** execution lint rejects the state instead of trusting its `done` label

### Requirement: Validation is deterministic and side-effect free
Snapshot, transition, and execution validation SHALL return canonically ordered issues and SHALL not mutate Plan, State, workspace, evidence, receipts, or configuration. Repeating a validation with identical inputs SHALL produce an identical result. These validators SHALL not initialize S6, write Plan State, reconcile commits, advance active Plan, or append revision ledgers. (Design 7.1.0: §5.2.5, §6.4.5; M1-4b non-scope.)

#### Scenario: Validation is replayed
- **WHEN** the same complete validation inputs are supplied twice
- **THEN** both result objects and issue order are identical and no input is changed
