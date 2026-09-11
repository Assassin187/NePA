## ADDED Requirements

### Requirement: Terminal revision evaluations are unique activation-bound ledger facts
The revision ledger SHALL accept `revision_evaluated` only for an earlier accepted `revision_activated` with the same positive `revision_seq`. Each activated revision SHALL have at most one terminal evaluation, and that evaluation SHALL carry a canonical non-empty affected-obligation anchor set, an evaluation-boundary identity, mutually exclusive resolved/ineffective truth, accepted evidence references, associated actual call references and non-negative actual cost. The unresolved result SHALL use `resolved=false, ineffective=false`; `resolved=true, ineffective=true` SHALL be invalid. The event SHALL advance only `event_seq` and SHALL NOT change `revision_seq`, Plan version, active pointer, State, file ledger or workspace. (Design: system §5.6.7, §9.1.4; pipeline §4.3-§4.4, §7.4; M1-12.)

#### Scenario: A resolved activation is evaluated
- **WHEN** an accepted activation's affected obligations have terminal success evidence and the original predicate has cleared
- **THEN** one `revision_evaluated` event binds that activation sequence, its canonical anchors, evidence/calls/cost and `resolved=true, ineffective=false`

#### Scenario: Evaluation references no activation
- **WHEN** a proposed evaluation has revision sequence zero, a future sequence or a sequence with no accepted activation
- **THEN** ledger validation rejects it without changing the accepted chain

#### Scenario: Evaluation claims two terminal outcomes
- **WHEN** a payload sets both `resolved` and `ineffective` true
- **THEN** ledger validation rejects the payload

### Requirement: Evaluation append and recovery are idempotent and conflict closed
Appending a byte-equivalent terminal evaluation for an already evaluated revision SHALL be a no-op. A second payload for the same revision sequence with different boundary, anchors, outcome, evidence, calls or cost SHALL be artifact damage. Resume SHALL reconstruct the pending evaluation from accepted activation/execution evidence and either append the unique expected event or reuse the accepted event; it SHALL NOT delete, rewrite or reorder prior ledger entries. (Design: system §4.8, §5.6.7; pipeline §4.3-§4.4, §7.4; M1-12.)

#### Scenario: Evaluation append is replayed
- **WHEN** resume requests the same byte-equivalent evaluation after it is already accepted
- **THEN** the complete revision ledger remains byte-equivalent and no duplicate event appears

#### Scenario: Evaluation replay conflicts
- **WHEN** the same revision sequence is replayed with a different outcome or evidence set
- **THEN** the accepted ledger is preserved and artifact damage is reported

#### Scenario: Crash occurs before the ordinary append completes
- **WHEN** terminal execution evidence exists but no valid evaluation event was accepted
- **THEN** resume recomputes the same evaluation boundary and appends the unique event once before admitting another revision

#### Scenario: A second activation precedes evaluation
- **WHEN** a ledger proposes revision activation N+1 while accepted activation N has no terminal evaluation
- **THEN** semantic validation rejects the new activation and preserves the accepted prefix

#### Scenario: State history advances after evaluation
- **WHEN** a terminal evaluation is accepted and later independent execution appends another State transition
- **THEN** the evaluation continues to reference its immutable accepted State-history snapshot and every prior evidence reference remains verifiable
