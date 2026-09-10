## MODIFIED Requirements

### Requirement: Ordinary tasks execute in stable dependency order with bounded role routing
S6 SHALL reconcile activation, current-epoch materialization and verification transactions in that order before selecting work, then SHALL select the first pending task in stable topological order whose dependencies are done. A normal task's first started attempt SHALL invoke Coder, later started attempts SHALL invoke Fixer, attempts one through three SHALL use T2 and attempt four SHALL use T1. Before provider I/O, S6 SHALL atomically persist the task attempt, increment the run-wide `s6_attempts_used`, allocate a non-reusable evidence sequence and bind the accepted execution baseline. It SHALL NOT exceed the per-task or frozen run-wide cap or refund an interrupted call. M1-7 MAY authorize one otherwise legal current Fixer attempt as F1 under the lease capability without creating another attempt. At a transaction-free task boundary, an accepted M1-10 F2/F3 `revision_handoff` SHALL be consumed by the M1-11 gate/activation capability before ordinary selection resumes: rejection preserves the current execution view, F2 activation selects from the migrated execution view in the same epoch, and F3 activation requires the new S5 epoch and its registered repair groups before further ordinary work. (Design: system design §4.6-§4.8, §5.2.4, §5.6.7, §6.6.1; pipeline §5.6-§5.6.1, §6.3-§7.2; D1.6/D1.13/D1.15; M1-6/M1-11.)

#### Scenario: First normal attempt starts
- **WHEN** a ready pending task has zero attempts and both local and global budgets remain
- **THEN** S6 persists attempt one and its evidence sequence before invoking Coder on T2

#### Scenario: Fourth attempt starts
- **WHEN** the same task has three failed T2 attempts and all hard budgets permit one more call
- **THEN** S6 persists attempt four before invoking Fixer on T1

#### Scenario: Resume follows an interrupted call
- **WHEN** a crash occurs after an attempt is persisted but before a successful transaction is committed
- **THEN** resume preserves the consumed attempt and never reuses its evidence sequence

#### Scenario: Global attempt capacity is exhausted
- **WHEN** starting another Coder or Fixer would exceed the frozen `s6_total_attempts_cap`
- **THEN** S6 issues no provider call and enters the designed controlled exit path

#### Scenario: Fixer attempt receives an F1 lease
- **WHEN** the next ordinary Fixer attempt satisfies every deterministic F1 authorization condition
- **THEN** the same persisted attempt may use the exact leased paths and consumes no additional local execution allowance

#### Scenario: Selected revision reaches a clean task boundary
- **WHEN** M1-10 has accepted a current F2/F3 handoff and no execution transaction is in flight
- **THEN** S6 runs M1-11 gate/reconciliation handling before selecting another ordinary task

#### Scenario: F2 activation returns to S6
- **WHEN** the pointer commits an F2 successor with a valid same-epoch binding
- **THEN** S6 selects work only from the migrated State and does not rematerialize the workspace

#### Scenario: F3 activation requires materialization
- **WHEN** the pointer commits an F3 successor whose activation is pending materialization
- **THEN** S6 performs no further ordinary selection until the new S5 epoch and affected repair-group prerequisites are accepted

### Requirement: Exhaustion and dependency blocking do not claim success
After four failed normal attempts, S6 SHALL mark the task blocked with its final immutable failure reference. It SHALL derive `blocked_by_dependency` only for not-yet-started tasks having a transitive dependency on a currently blocked task and SHALL continue any topologically independent task that can still receive complete acceptance. M1-7 MAY use an eligible F1 lease before the current Fixer attempt begins. At a legal transaction-free boundary, accepted deterministic trigger/candidate facts MAY produce an M1-11 revision handoff; a rejected candidate SHALL NOT reopen or erase any blocked fact, while an activated migration SHALL change status or mode only through its accepted migration proof. If no revision activates, static-valid unresolved execution SHALL terminate as `EXECUTION_UNRESOLVED` with degraded exit code 10; static contract invalidity SHALL use the designed failed exit code 20. (Design: system design §4.7, §5.2.4, §6.6.1; pipeline §6.1-§7.3; D1.13; M1-6/M1-11.)

#### Scenario: Four normal attempts fail
- **WHEN** attempts one through four fail and no success transaction exists
- **THEN** the task is blocked with its last error/evidence and S6 performs no fifth call

#### Scenario: A downstream task depends on a blocked task
- **WHEN** a pending task has a proven dependency path from a blocked task
- **THEN** it becomes `blocked_by_dependency` without consuming an attempt

#### Scenario: An independent branch remains
- **WHEN** another pending task shares no blocking dependency and can pass its complete build/smoke gate
- **THEN** S6 may execute that task before producing the controlled degraded result

#### Scenario: No eligible F1 or accepted revision exists
- **WHEN** the current failure cannot satisfy every lease condition and no F2/F3 candidate activates
- **THEN** S6 continues only with remaining ordinary work or the existing controlled exhaustion path

#### Scenario: Candidate rejection preserves blocking history
- **WHEN** an RG gate rejects a candidate derived from a blocked boundary
- **THEN** the original blocked and dependency-blocked facts remain accepted and no execution allowance is refreshed

#### Scenario: Activation migrates a blocked view
- **WHEN** an F2/F3 candidate activates with a complete migration mapping
- **THEN** only the WAL-bound migrated State and accepted activation proof may assign successor status, mode and group membership
