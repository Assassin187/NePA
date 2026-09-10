## MODIFIED Requirements

### Requirement: Revision activation has one locked logical commit point
Activation SHALL require a candidate that passed every applicable RG-1 through RG-5 gate, its validated Plan and migration report, projected Plan State/file ledger, expected append-only revision ledger, and expected current pointer under the existing controller lock. Before changing a live artifact it SHALL persist a canonical activation WAL in the candidate directory binding the old and new pointer, Plan State, file ledger, complete revision ledger, current copies, successor Plan, F2 binding or F3 pending-materialization intent, Run active reference, and all content hashes. It SHALL publish the immutable successor Plan; for F2 it SHALL publish a metadata-only binding to the current epoch receipt and new immutable manifest/map, while for F3 `binding_ref` SHALL be null and `pending_materialization=true` until S5 publishes the next epoch. It SHALL then replace the migrated State, file ledger and revision ledger with the WAL-bound new values, atomically advance `active_plan.json` as the sole logical commit point, and only afterward complete the Run active reference and current deterministic copies. The immutable S4 `output_refs.plan` SHALL remain bound to 1.0.0. A caller SHALL NOT activate a partial, failed, stale, unlocked, non-successor or already rejected candidate. (Design: system design §4.8, §5.2, §5.6.7; pipeline §4.1-§4.4, §6.4; D1.13; M1-11.)

#### Scenario: F2 activation completes
- **WHEN** an F2 candidate passes every applicable gate and every WAL-bound write succeeds
- **THEN** the immutable successor Plan, migrated State/file ledger, activation entry, unchanged-epoch binding, active pointer and Run reference form one self-consistent new active revision

#### Scenario: F3 reaches the activation commit point
- **WHEN** an F3 candidate passes every gate and `active_plan.json` advances
- **THEN** the activation entry has no binding, declares pending materialization in the next epoch, and downstream admission requires S5 to publish that epoch and binding

#### Scenario: Concurrent controller attempts activation
- **WHEN** another cooperating controller holds the run lock
- **THEN** activation changes no artifact and reports the existing lock failure

#### Scenario: An input changes after rehearsal
- **WHEN** the pointer, State, file ledger, revision ledger, workspace, candidate or gate-evidence hash differs from the WAL-bound expectation
- **THEN** activation fails closed rather than recomputing or accepting the drift

#### Scenario: Candidate version skips its legal successor
- **WHEN** the proposed version, epoch or revision sequence is not the unique F2/F3 successor
- **THEN** no formal Plan, binding or mutable control value is published

### Requirement: Crash recovery converges to one legal revision state
Recovery SHALL run before every Stage admission and SHALL use the active pointer as the sole activation commit authority and the immutable activation WAL as reconciliation evidence. If the pointer still equals the WAL-bound old value, recovery SHALL restore the exact old Plan State, file ledger, revision ledger, Run active reference and current copies regardless of whether a new activation entry or other new transaction bytes were prewritten, and SHALL isolate the unactivated successor Plan/binding inside the candidate area. If the pointer equals the WAL-bound new value, recovery SHALL validate every new Plan/State/ledger/binding-or-pending-materialization reference and SHALL only move forward to complete missing Run/current-copy publication and continue S5 or S6. If the pointer equals neither value, or any required live/WAL byte, hash chain, version, epoch or sequence conflicts, recovery SHALL report artifact damage without guessing, deleting realized content, rolling back an activated pointer, or admitting another transaction. (Design: system design §4.8, §5.6.7; pipeline §4.2-§4.4, §6.4; D1.13/D1.15; M1-11.)

#### Scenario: Crash follows successor Plan publication
- **WHEN** the successor Plan exists but the active pointer remains the old value
- **THEN** recovery restores every old mutable/current artifact, isolates the unactivated Plan, and leaves the old revision active

#### Scenario: Crash follows activation-entry prewrite
- **WHEN** the new revision ledger and migrated State/file ledger exist but the active pointer remains the old value
- **THEN** recovery restores the complete old ledger and old mutable/current values and does not infer activation from the prewritten entry

#### Scenario: Crash follows pointer advancement
- **WHEN** the pointer is the WAL-bound new value and all new referenced bytes validate but Run/current copies are incomplete
- **THEN** recovery preserves the activated revision and only completes the missing forward publications before routing to S5 or S6

#### Scenario: Recovery sees a third pointer value
- **WHEN** the active pointer equals neither the WAL-bound old nor new value
- **THEN** recovery reports artifact damage and changes no accepted artifact

#### Scenario: Recovery sees conflicting bytes
- **WHEN** any required live or WAL-bound artifact has an unexpected hash, chain, version, epoch or sequence
- **THEN** recovery reports damage and performs no speculative repair
