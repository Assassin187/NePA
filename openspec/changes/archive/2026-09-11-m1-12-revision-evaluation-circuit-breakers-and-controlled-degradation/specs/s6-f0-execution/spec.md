## ADDED Requirements

### Requirement: S6 evaluates each accepted activation before another revision cycle
At a transaction-free S6 boundary, the system SHALL identify the latest activated revision lacking a terminal evaluation and SHALL freeze its affected obligation/lineage anchors from the accepted activation. It SHALL append the evaluation only after those obligations are all terminal or a hard budget prevents continued validation, and SHALL complete that evaluation before selecting another revision candidate. Pending or in-progress migration status immediately after activation SHALL NOT be treated as an outcome. (Design: system §5.2.4, §5.6.7, §6.6; pipeline §5.6, §6.4, §7.4; M1-12.)

#### Scenario: Migrated work is still pending
- **WHEN** F2/F3 activation has reopened affected execution units and at least one remains pending or in-progress with legal budget
- **THEN** S6 continues the bounded migrated execution path and accepts no terminal evaluation yet

#### Scenario: Affected work reaches terminal evidence
- **WHEN** all affected units are done, blocked or dependency-blocked and no transaction is in flight
- **THEN** S6 evaluates the original anchored problem once before considering any later revision trigger

#### Scenario: Resume observes an unevaluated completed traversal
- **WHEN** accepted execution evidence closes the affected set but the process stopped before the evaluation append
- **THEN** resume appends or reuses the unique evaluation before ordinary trigger selection

#### Scenario: Dependency propagation closes the final affected task
- **WHEN** dependency propagation changes the last pending affected task to a terminal dependency-blocked state
- **THEN** S6 appends or reuses the terminal evaluation before requesting termination, finalizing, or selecting another candidate

### Requirement: S6 continues only completely verifiable independent branches
After a task or registered repair group becomes exhausted, S6 SHALL preserve its accepted baseline and failure evidence, propagate blocking only through proven task/build/runtime dependencies, and select remaining work only when its complete build, smoke and execution acceptance can still run independently. It SHALL NOT label a branch independent merely because its task-DAG edge is absent when the failed group prevents the shared default build or runtime gate. (Design: system §6.6; pipeline §5.6.1, §7.3; M1-12.)

#### Scenario: Independent work can still be fully checked
- **WHEN** a failed group has no task, build-artifact or runtime dependency on another ready branch
- **THEN** S6 may execute that branch using its unchanged budgets and complete acceptance gate

#### Scenario: Shared build remains broken
- **WHEN** the failed group makes a default build variant unavailable to a nominally disjoint task
- **THEN** S6 does not invoke the task and requests the controlled unresolved exit

### Requirement: S6 hard exhaustion uses the existing controlled exit
S6 SHALL check synchronized global time/cost before every model call and SHALL atomically consume the run-wide S6 call allowance before every Coder/Fixer call. When a hard limit is exhausted, when all legal revision routes are locked, or when an exhausted repair group leaves no completely verifiable independent work, S6 SHALL retain all accepted state and request `EXECUTION_UNRESOLVED` or the existing global budget reason as applicable. The orchestrator SHALL route the request through S9 and finalize a static-valid run as degraded with exit code 10. Neither `--until s6` nor resume SHALL turn that path into planned_stop, refresh attempts, or start another call. (Design: system §4.7-§4.8, §6.6, §9.1.2; pipeline §6.5, §7.3-§7.4; D1.10; M1-12.)

#### Scenario: Revision paths close with unfinished static-valid work
- **WHEN** S6 has unresolved tasks, no legal revision path and no independently executable branch while authoritative contracts remain valid
- **THEN** the run enters S9 and finishes degraded with `EXECUTION_UNRESOLVED` and exit code 10

#### Scenario: Global budget is exhausted before a call
- **WHEN** wall-clock or cost usage reaches its frozen limit before an S6 model invocation
- **THEN** no invocation starts, the budget termination request is persisted, and S9 completes with enforcement disabled

#### Scenario: Until-s6 run encounters circuit breaking
- **WHEN** a run configured with `--until s6` reaches an M1-12 controlled unresolved condition
- **THEN** it exits through controlled degradation rather than publishing an S6 receipt or planned_stop
