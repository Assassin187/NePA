## MODIFIED Requirements

### Requirement: Ordinary tasks execute in stable dependency order with bounded role routing
S6 SHALL select the first pending task in stable topological order whose dependencies are done. A normal task's first started attempt SHALL invoke Coder, later started attempts SHALL invoke Fixer, attempts one through three SHALL use T2 and attempt four SHALL use T1. Before provider I/O, S6 SHALL atomically persist the task attempt, increment the run-wide `s6_attempts_used`, allocate a non-reusable evidence sequence and bind the accepted execution baseline. It SHALL NOT exceed the per-task or frozen run-wide cap or refund an interrupted call. M1-7 MAY authorize one otherwise legal current Fixer attempt as F1 under the lease capability, but the lease SHALL NOT create another attempt; F2/F3 activation remains unavailable. (Design: §4.6-§4.7, §5.2.4, §6.6.1; pipeline §6.5/§7.1-§7.2; D1.6; M1-6/M1-7.)

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

### Requirement: Candidate application and acceptance are controller-owned
Each model response SHALL satisfy the closed full-file output contract and contain a non-empty subset of the effective whitelist. For an ordinary attempt that whitelist is the current task's declared `s6_owned` deliverable files; an accepted F1 lease MAY add only its exact external paths for that one Fixer invocation. Before modifying the live workspace, S6 SHALL reject unknown, duplicate, unsafe, frozen or out-of-whitelist paths and SHALL reject a candidate whose exported declarations disagree with the sealed contract map. S6 SHALL assemble the candidate from its persisted baseline, run all required default build variants and executable smoke checks, and SHALL run no M1 Test Bundle tests. A candidate is successful only if every required build and smoke result passes; an F1 candidate also requires the joint acceptance and publication contract. (Design: §5.5, §6.6.1, §6.6.3; pipeline §7.2; M1-6/M1-7.)

#### Scenario: Candidate passes the task gate
- **WHEN** a schema-valid candidate changes only allowed task files, preserves declared exports, and all required builds and smoke checks pass
- **THEN** the candidate tree is eligible for evidence publication and commit

#### Scenario: Candidate writes another task or frozen file
- **WHEN** a response names a path outside the task's owned files and any exact accepted lease paths
- **THEN** S6 rejects the candidate before changing the live workspace

#### Scenario: Contract declaration drifts
- **WHEN** candidate source changes a sealed exported declaration or cannot prove the declared implementation binding
- **THEN** S6 records a failed attempt and does not publish a success commit

#### Scenario: Test implementations exist
- **WHEN** M1 S6 executes a task in a repository containing Test Bundle-related files
- **THEN** S6 neither includes those implementations in an Agent context nor invokes their nodeids

### Requirement: Exhaustion and dependency blocking do not claim success
After four failed normal attempts, S6 SHALL mark the task blocked with its final immutable failure reference. It SHALL derive `blocked_by_dependency` only for not-yet-started tasks having a transitive dependency on a currently blocked task and SHALL continue any topologically independent task that can still receive complete acceptance. M1-7 MAY use an eligible F1 lease before the current Fixer attempt begins but SHALL keep trigger evaluation and F2/F3 revision activation closed. Static-valid unresolved execution SHALL terminate as `EXECUTION_UNRESOLVED` with degraded exit code 10; static contract invalidity SHALL use the designed failed exit code 20. (Design: §4.7, §5.2.4, §6.6.1; pipeline §7.1-§7.3; M1-6/M1-7.)

#### Scenario: Four normal attempts fail
- **WHEN** attempts one through four fail and no normal or lease success transaction exists
- **THEN** the task is blocked with its last error/evidence and S6 performs no fifth call

#### Scenario: A downstream task depends on a blocked task
- **WHEN** a pending task has a proven dependency path from a blocked task
- **THEN** it becomes `blocked_by_dependency` without consuming an attempt

#### Scenario: An independent branch remains
- **WHEN** another pending task shares no blocking dependency and can pass its complete build/smoke gate
- **THEN** S6 may execute that task before producing the controlled degraded result

#### Scenario: No eligible F1 repair exists
- **WHEN** the current failure cannot satisfy every lease condition or the run lease allowance is exhausted
- **THEN** S6 continues only with remaining ordinary attempts or the existing controlled exhaustion path and does not activate a revision
