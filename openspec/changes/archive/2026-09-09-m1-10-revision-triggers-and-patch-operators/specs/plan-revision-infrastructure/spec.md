## ADDED Requirements

### Requirement: Trigger hits are accepted typed ledger facts
The revision ledger SHALL accept an M1 `trigger_evaluated` fact only when its deterministic boundary key, current Plan reference, TR code, stable hit signature, selected state, reason and canonically ordered evidence references agree with a validated trigger evaluation. Every hit at one boundary SHALL be represented exactly once; at most one hit at that boundary SHALL be selected for an F2/F3 candidate, while record-only and local-route hits SHALL remain unselected. Replaying an identical boundary/signature fact SHALL be idempotent and a conflicting duplicate SHALL fail closed. Trigger facts SHALL advance only `event_seq`; they SHALL NOT advance `revision_seq`, move the active pointer or count as an activation. (Design: `project_docs/pipeline_design_s4_s9.md` §4.3, §6.1-§6.1.1; M1-10.)

#### Scenario: Several hits share a boundary
- **WHEN** one accepted boundary evaluation contains record-only, local-route and F2/F3-selectable hits
- **THEN** every hit is recorded once with common boundary identity and no more than one F2/F3 selection

#### Scenario: Trigger event is replayed
- **WHEN** the same boundary key, signature and byte-equivalent payload are appended again
- **THEN** the revision ledger remains byte-equivalent and does not add a duplicate event

#### Scenario: Trigger event conflicts
- **WHEN** an accepted boundary/signature is replayed with another Plan ref, selection, reason or evidence set
- **THEN** validation reports artifact damage and preserves the accepted ledger

### Requirement: An unactivated candidate is bound but non-authoritative
An M1-10 candidate SHALL bind its candidate id to the selected trigger event sequence and signature, source Plan/ref/revision, requested F2/F3 level, canonical closed patch, candidate Plan/ref, derived Blueprint/manifest/contract map, explicit lineage/obligation mappings, complete migration report and invariant/lint results. Its identity and content hashes SHALL be deterministic and immutable once supplied to M1-11. The revision infrastructure SHALL validate candidate ancestry and exact agreement with the selected trigger while continuing to treat the current active pointer and accepted version chain as authority until a later legal activation. A candidate SHALL NOT reserve a formal version number, append an activation/rejection event or alter activation budgets merely by existing. (Design: pipeline §4.1-§4.3, §6.2-§6.4; M1-10/M1-11.)

#### Scenario: Candidate matches its trigger
- **WHEN** every source, trigger, patch, derived artifact, migration and invariant reference agrees
- **THEN** the candidate is accepted as a complete non-authoritative input for M1-11

#### Scenario: Candidate points at another source Plan
- **WHEN** the candidate source Plan or revision differs from the selected trigger event and active slice used to build it
- **THEN** validation rejects the candidate without changing the active version chain

#### Scenario: Candidate exists before activation
- **WHEN** a valid candidate is present under `_s4r` but no later activation event exists
- **THEN** readers continue to use the prior active Plan, State, binding and revision sequence

#### Scenario: Candidate commit is interrupted after ledger acceptance
- **WHEN** the selected trigger is accepted and the matching candidate remains in its pending directory
- **THEN** reconciliation validates its hashes, completes the final-directory rename exactly once and preserves all authoritative execution artifacts
