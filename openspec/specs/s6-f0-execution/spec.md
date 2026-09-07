# s6-f0-execution Specification

## Purpose

Define the deterministic S6 ordinary-task execution boundary that turns a ready E0 workspace into validated task commits, auditable Plan State and a sealed S6 receipt while preserving bounded failure and crash-recovery semantics.

## Requirements

### Requirement: S6 admission accepts only one coherent ready execution baseline
S6 SHALL reconcile pending verification transactions before admission and SHALL validate the active Plan, frozen inputs and configuration, current ready epoch/binding receipts, manifest/map, file and revision ledgers, workspace HEAD/tree and clean status. For a fresh run it SHALL initialize Plan State exactly once only when no State exists and HEAD is the accepted E0 checkpoint; missing State beside any task commit SHALL fail closed. (Design: §4.8, §5.2.4-§5.2.5, §5.6.7, §6.6; M1-6.)

#### Scenario: Fresh ready E0 is admitted
- **WHEN** S5 is done, E0 is ready, every binding is valid, workspace HEAD is exactly the E0 checkpoint and Plan State does not exist
- **THEN** S6 publishes the unique all-pending Plan State before checking the execution-attempt budget

#### Scenario: State is missing after execution began
- **WHEN** Plan State is absent but workspace history contains a task commit after E0
- **THEN** S6 reports artifact damage without reconstructing task completion from Git history

#### Scenario: Admission facts drift
- **WHEN** the Plan, configuration, binding, receipt, ledger, workspace tree or current pointer disagrees with its accepted anchor
- **THEN** S6 changes no execution state and fails through the designed controlled or corruption route

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
- **WHEN** M1-6 executes a task in a repository containing Test Bundle-related files
- **THEN** S6 neither includes those implementations in an Agent context nor invokes their nodeids

### Requirement: Failed candidates remain auditable and feed the next Fixer
For every failed attempt, S6 SHALL persist the exact candidate files/tree, model output reference, matching validation evidence and normalized error before restoring the accepted execution baseline. The next Fixer SHALL receive the most recent failed candidate for that task plus its matching errors and any controller-requested Diagnoser conclusion; it SHALL NOT receive only the restored stub with unrelated new errors. Failed attempt artifacts SHALL remain immutable across retries and resume. (Design: §6.6.1-§6.6.2; pipeline §5.6; M1-6.)

#### Scenario: Build failure is followed by Fixer
- **WHEN** a Coder candidate fails a required build and another attempt remains
- **THEN** the failed full files and that build evidence are persisted, the baseline is restored, and the next Fixer context contains the persisted candidate and matching failure

#### Scenario: Smoke failure consumes F0
- **WHEN** a candidate builds but exits early or fails the required smoke termination contract
- **THEN** its smoke evidence is preserved as the current-attempt failure and the task may use only its remaining F0 attempts

#### Scenario: Recovery sees a failed-candidate transaction
- **WHEN** execution stops after candidate or diagnostic persistence but before the next attempt
- **THEN** resume restores the bound baseline without deleting the immutable failed-attempt artifacts

### Requirement: Successful normal execution has one recoverable commit boundary
For a successful normal task, S6 SHALL allocate and publish immutable Task Evidence bound to Plan/version/epoch, task id/uid, attempt/evidence sequence, candidate tree, build/smoke results and changed file hashes. It SHALL create exactly one commit containing only the accepted task changes and required task/evidence trailers, then use one verification WAL to publish the matching Plan State, S6-owned file-ledger rows and one idempotent `verification_committed` revision event. The legal commit is the transaction commit point; a crash after it SHALL only complete missing State/ledger/event outputs, while conflicting facts SHALL fail closed. (Design: §5.2.4, §5.6.7, §6.6.1; M1-6.)

#### Scenario: One task succeeds
- **WHEN** a task candidate passes every acceptance gate
- **THEN** one task commit, one immutable evidence object, one done State row, matching realized file rows and one verification event bind the same tree and attempt

#### Scenario: Crash occurs before commit
- **WHEN** evidence or candidate bytes exist but no legal task commit has been created
- **THEN** recovery does not mark the task done and resumes from the WAL-bound baseline

#### Scenario: Crash occurs after commit before State
- **WHEN** a trailer-valid task commit and exact evidence exist but State, file ledger or event publication is incomplete
- **THEN** reconciliation completes only the missing outputs without another model call or commit

#### Scenario: Accepted facts conflict
- **WHEN** commit tree, trailers, evidence, State, file ledger or verification event disagree
- **THEN** reconciliation reports artifact damage instead of selecting or rewriting one version

### Requirement: Exhaustion and dependency blocking do not claim success
After four failed normal attempts, S6 SHALL mark the task blocked with its final immutable failure reference. It SHALL derive `blocked_by_dependency` only for not-yet-started tasks having a transitive dependency on a currently blocked task and SHALL continue any topologically independent task that can still receive complete acceptance. M1-7 MAY use an eligible F1 lease before the current Fixer attempt begins but SHALL keep trigger evaluation and F2/F3 revision activation closed. Static-valid unresolved execution SHALL terminate as `EXECUTION_UNRESOLVED` with degraded exit code 10; static contract invalidity SHALL use the designed failed exit code 20. (Design: §4.7, §5.2.4, §6.6.1; pipeline §7.1-§7.3; M1-6/M1-7.)

#### Scenario: Four normal attempts fail
- **WHEN** attempts one through four fail and no success transaction exists
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

### Requirement: S6 seals only a complete exit validation result
After no executable task remains, S6 SHALL rerun every default build variant, every Blueprint executable smoke check and complete snapshot/execution State lint against the final clean HEAD. If every Plan task is done and every exit check passes, S6 SHALL publish an immutable receipt binding the active Plan, current binding, Plan State, file ledger, immutable revision-ledger prefix, workspace HEAD, build results and smoke results, and SHALL atomically mark Run S6 done with exact output refs. Replaying completed S6 SHALL be read-only. A final validation failure SHALL produce `S6_EXIT_VALIDATION_FAILED`/degraded and SHALL NOT reopen done tasks. (Design: §5.5, §5.6.7, §6.6.1; M1-6.)

#### Scenario: S6 completes successfully
- **WHEN** every task is done and final build, smoke and execution lint pass
- **THEN** Run accepts one S6 receipt and `run --until s6` exits 0 as `planned_stop` without running S7 or S9

#### Scenario: Final smoke fails
- **WHEN** all task commits are already done but final exit smoke fails
- **THEN** S6 records `S6_EXIT_VALIDATION_FAILED`, returns controlled degraded exit code 10 and does not reopen a task

#### Scenario: Completed S6 is resumed
- **WHEN** `resume` or stage replay observes a valid accepted S6 receipt and all bound facts remain unchanged
- **THEN** it performs no provider call, build, smoke, commit or artifact rewrite

### Requirement: The M1 S6 CLI surface is observable and deterministic
The existing CLI SHALL support `run --until s6`, `resume <run_id>`, `status <run_id>`, Plan basic/full lint and Plan-State snapshot/execution lint for the M1-6 artifact versions without adding an S6-specific user parameter. Status SHALL reconstruct current S6 task progress and consumed budget from durable Run/S4/Plan-State artifacts rather than process memory. Exit codes SHALL remain 0 for success/planned stop, 10 for controlled degraded, 20 for controlled failed and 1 for internal NePA error. (Design: §8.6-§8.7, §10.2.2; M1-6.)

#### Scenario: Status is requested after interruption
- **WHEN** a run stops during an S6 attempt and a new process invokes status
- **THEN** output reflects the persisted task status, attempt count and run budget without resuming execution

#### Scenario: Full Plan-State lint is requested
- **WHEN** the CLI receives Plan State, Plan and run directory containing complete accepted execution evidence
- **THEN** it checks both snapshot and external execution bindings and exits according to the deterministic lint result
