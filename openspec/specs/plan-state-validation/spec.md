# plan-state-validation Specification

## Purpose

Define closed Plan State data and deterministic snapshot, transition, and execution-evidence checks without conflating mutable execution bookkeeping with the immutable Plan.

## Requirements

### Requirement: Plan State Schema and snapshot lint enforce one unambiguous state
The system SHALL provide a closed draft-2020-12 Plan State Schema and conforming example. Snapshot lint SHALL validate the active Plan reference, S4 seal and configuration binding, exact task-id set, configured attempt limit, and all status-specific field invariants for `pending`, `in_progress`, `done`, `blocked`, and `blocked_by_dependency`. Initial state SHALL contain every Plan task exactly once as `pending` with zero attempts and empty/null execution fields. A nonzero-attempt pending state SHALL be legal only when proven by the specified revision-ledger reopening event. (Design 7.1.0: §5.2.4-§5.2.5; D1.9; M1-4b.)

#### Scenario: Initial snapshot is constructed
- **WHEN** Plan State is initialized from a valid active Plan and configuration
- **THEN** its task ids equal the Plan task ids and every task satisfies the unique initial-state row

#### Scenario: State fields are ambiguous
- **WHEN** a status carries an attempt count, commit, evidence, note, or error forbidden by its state row
- **THEN** snapshot lint rejects the artifact as damaged

### Requirement: State transitions are derived from the closed event table
Transition validation SHALL accept only the design-defined event types and SHALL derive the unique legal next task state from the complete old state and event. Attempt starts SHALL increment attempts; success and reconciliation SHALL require their typed commit/evidence inputs; exhaustion SHALL require the total attempt limit; dependency blocking SHALL be proven from Plan dependencies and current State. Terminal states SHALL remain closed to ordinary S6 callers; revision and lease events SHALL require their designated controller evidence. Arbitrary caller-supplied replacement states SHALL be rejected. (Design 7.1.0: §5.2.4-§5.2.5; M1-4b.)

#### Scenario: Legal attempt succeeds
- **WHEN** an in-progress task receives an `attempt_succeeded` event with valid current-attempt commit and evidence bindings
- **THEN** the derived state is `done` with no last error and unchanged unrelated task states

#### Scenario: Caller requests an unlisted transition
- **WHEN** an event attempts to skip the table, exceed attempts, or reopen a terminal state without the required controller proof
- **THEN** transition validation rejects it without mutating State

### Requirement: Execution lint verifies external evidence separately from snapshot shape
Execution-state lint SHALL validate each done task's commit existence and ancestry, required commit trailers, evidence path/content SHA-256, task and attempt identity, Plan acceptance, S5 output anchors, and workspace relation using the supplied workspace, evidence store, and stage receipts. Complete execution validation SHALL combine snapshot and execution checks; a JSON-only snapshot check SHALL NOT claim filesystem or git verification. (Design 7.1.0: §4.8, §5.2.4-§5.2.5; D1.1/D1.2; M1-4b.)

#### Scenario: Done task evidence is coherent
- **WHEN** a done task's state, evidence file, commit tree, trailers, Plan acceptance, and stage anchors all agree
- **THEN** execution lint accepts that task evidence

#### Scenario: Commit or evidence binding drifts
- **WHEN** any referenced evidence bytes, commit trailer, commit ancestry, acceptance result, or S5 anchor disagrees
- **THEN** execution lint rejects the state instead of trusting its `done` label

### Requirement: Validation is deterministic and side-effect free
Snapshot, transition, and execution validation SHALL return canonically ordered issues and SHALL not mutate Plan, State, workspace, evidence, receipts, or configuration. Repeating a validation with identical inputs SHALL produce an identical result. These validators SHALL not initialize S6, write Plan State, reconcile commits, advance active Plan, or append revision ledgers. (Design 7.1.0: §5.2.5, §6.4.5; M1-4b non-scope.)

#### Scenario: Validation is replayed
- **WHEN** the same complete validation inputs are supplied twice
- **THEN** both result objects and issue order are identical and no input is changed
