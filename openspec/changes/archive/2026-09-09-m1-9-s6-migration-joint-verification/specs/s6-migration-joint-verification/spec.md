## Purpose

Define deterministic S6 execution of accepted Plan migrations and frozen F3 repair groups, including bounded mode-specific work, whole-group validation, atomic evidence publication, dependency handling and crash recovery.

## ADDED Requirements

### Requirement: S6 processes accepted migration work before ordinary execution
After reconciliation, S6 SHALL derive migration work only from the current accepted activation, active Plan, Plan State, current binding and epoch receipt. An F2 activation SHALL enter its pending REVALIDATE, AMEND and REGENERATE tasks without rematerializing source; an F3 epoch with `materialization_status=pending_repair` SHALL process exactly the sorted pending group ids frozen in the activation and epoch receipt before any ordinary task. A ready current epoch MAY still contain pending migration tasks and SHALL process them before ordinary pending work. S6 SHALL NOT synthesize migration rows, group membership or readiness from workspace contents, Agent output or an unaccepted candidate. (Design: system §5.2.4, §5.6.7, §6.6; pipeline §5.6, §6.4; M1-9.)

#### Scenario: Pending-repair epoch enters its frozen group
- **WHEN** the active F3 Plan, accepted E1+ receipt and activation all name the same pending group
- **THEN** S6 enters that group before selecting any ordinary pending task

#### Scenario: F2 migration has no new epoch
- **WHEN** an accepted F2 binding leaves the current epoch unchanged and its State contains pending migration modes
- **THEN** S6 executes those modes against the existing epoch without invoking S5 or changing the epoch receipt

#### Scenario: Group facts are not accepted history
- **WHEN** a caller or workspace suggests a task or path that is absent from the accepted activation group
- **THEN** S6 rejects it before allocating work or modifying the workspace

### Requirement: Migration execution modes have distinct bounded semantics
REVALIDATE SHALL allocate a new non-reusable evidence sequence, invoke no LLM, consume neither ordinary attempts nor `s6_attempts_used`, and run the required current build and smoke gates. AMEND SHALL preserve the prior ordinary attempt count, atomically change `amendment_used` from zero to one before exactly one T1 Fixer invocation, increment `s6_attempts_used` once, and remain legal even when the preserved ordinary attempt count equals its limit. REGENERATE SHALL be a new normal generation beginning at zero attempts and SHALL use the ordinary Coder/Fixer schedule and run-wide cap. Every mode SHALL bind the current Plan/version/epoch, migration event, baseline and applicable file ownership; no mode SHALL refresh an unrelated allowance or reuse historical evidence as proof of changed obligations. (Design: system §5.2.4, §5.4, §6.6; pipeline §5.6.1, §6.5; D1.6/D1.15; M1-9.)

#### Scenario: Revalidation passes without an Agent
- **WHEN** a pending REVALIDATE task's unchanged content passes every current required build and smoke gate
- **THEN** it may receive new current-version evidence with zero ordinary attempts and no LLM call

#### Scenario: Fully spent task receives AMEND
- **WHEN** an accepted migration classifies a previously done task with four ordinary attempts as AMEND and its amendment/global allowance remains
- **THEN** S6 persists `amendment_used=1` and performs exactly one T1 Fixer call without creating a fifth ordinary attempt

#### Scenario: Regenerated task starts a new generation
- **WHEN** an accepted migration classifies a new task generation as REGENERATE
- **THEN** its first started call is Coder/T2 at normal attempt one while historical generation usage remains auditable through migration lineage

#### Scenario: Revalidation fails
- **WHEN** the deterministic current gate fails for a REVALIDATE task and no other group member can legally repair the attributed failure
- **THEN** the task or containing group is blocked with its validation proof and no coding allowance is invented

### Requirement: F3 repair groups assemble candidates without intermediate success
For each non-empty frozen group, S6 SHALL use the accepted epoch checkpoint commit/tree as an immutable ancestry anchor and the clean accepted current HEAD/tree as that group's transaction baseline and commit parent, order members by the active Plan topology, and build one cumulative candidate tree. A group MAY contain one member; a lease SHALL still contain at least two. Each member's writable set SHALL be exactly `deliverable_files ∩ affected_paths`. AMEND and REGENERATE members SHALL have a non-empty writable set before allocation and receive only those writable current files plus read-only accumulated/interface context; REVALIDATE members SHALL have an empty writable set and receive no model call. A non-REVALIDATE response MAY change any non-empty legal subset and SHALL make at least one real byte change. Its candidate manifest SHALL mark the completely persisted response used by recovery. After each bounded traversal S6 SHALL run all default build variants, applicable M1 acceptance tests (empty), and smoke checks for the complete group tree. Failure attribution SHALL use deterministic frozen path-owner, contract symbol/provider and Blueprint build-artifact mappings; only implicated members with remaining mode-specific allowance MAY be called again, while smoke, timeout or non-strict diagnostics SHALL make the whole group suspect. No intermediate candidate, partial build success or temporary provider readiness SHALL produce a success commit, a done State row or formal readiness. (Design: system §6.6.1; pipeline §5.6.1; M1-9.)

#### Scenario: Two incompatible members converge together
- **WHEN** two group members require sequential candidate repairs before their shared build artifact succeeds
- **THEN** S6 retains both candidates in one group tree and publishes no member success until the complete tree passes

#### Scenario: Temporary provider readiness is used internally
- **WHEN** an earlier group member's candidate is needed to assemble a later member's context
- **THEN** S6 may use that candidate in group-topological order without marking the provider done or ready in persisted State

#### Scenario: Failure cannot be localized
- **WHEN** a group validation failure cannot be mapped to a strict member subset using the frozen group closure
- **THEN** every member remains suspect and only members with a legal remaining allowance may be retried

### Requirement: Successful migration verification publishes one complete transaction
When a singleton migration or complete F3 group passes, S6 SHALL publish one immutable Task Evidence object per member bound to the current Plan/version/epoch, migration ref, evidence sequence, shared accepted tree, build/smoke refs and actual changed files. A group SHALL also publish one sorted `kind=group` Joint Evidence object and exactly one joint commit carrying only the verification-id and joint-evidence trailers. A singleton or all-REVALIDATE result with no source change SHALL use a legal same-tree evidence commit rather than fabricate a changed file. The commit SHALL be the transaction commit point; one verification WAL SHALL then publish all member State rows, realized file-ledger facts and exactly one current-revision `verification_committed` event. (Design: system §5.2.4, §5.4, §5.6.7; pipeline §5.6.1; D1.15; M1-9.)

#### Scenario: Group succeeds atomically
- **WHEN** every group build and smoke gate passes on one candidate tree
- **THEN** every member evidence, Joint Evidence, one joint commit, all done State rows, file-ledger facts and one group verification event bind that same tree

#### Scenario: Pure revalidation changes no source
- **WHEN** one or more REVALIDATE members pass on a tree identical to their accepted baseline
- **THEN** the evidence commit retains the same tree while current-version evidence and migration lineage prove the new completion

#### Scenario: Publication omits a member
- **WHEN** evidence, Joint Evidence, State, file ledger, WAL or event covers only a strict subset of the frozen group
- **THEN** the transaction is invalid and no subset is accepted as done

### Requirement: Group failure and recovery preserve one legal baseline or result
If a group cannot pass and no member has an applicable remaining allowance, S6 SHALL restore that group's transaction baseline without removing prior accepted group commits, mark every unresolved member blocked with `GROUP_VALIDATION_EXHAUSTED`, retain spent calls, evidence-sequence gaps, candidates and failure refs, and derive downstream dependency blocks. It SHALL continue only a branch that shares no blocking dependency or required build/runtime closure and can pass its complete validation; otherwise it SHALL take the existing controlled degraded route. Recovery before a legal joint commit SHALL restore the WAL-bound transaction baseline and deterministically reassemble manifest-listed candidate subsets without refunding work or repeating Agent calls. Recovery after a legal commit SHALL verify it and complete every missing evidence/State/ledger/event suffix without another Agent call, validation run or commit. Conflicting accepted bytes, epoch anchors, transaction baselines, trees, trailers or partial member facts SHALL fail as artifact damage. (Design: system §4.7-§4.8, §5.6.7, §6.6.1; pipeline §5.6.1, §7.3; D1.15; M1-9.)

#### Scenario: Group exhausts before commit
- **WHEN** its final legal traversal still fails and no member has remaining applicable allowance
- **THEN** the workspace returns to the group baseline, no member is done, all unresolved members are blocked and the failure artifacts remain auditable

#### Scenario: Crash occurs with persisted candidates
- **WHEN** execution stops after one or more group candidates are recorded but before a legal joint commit
- **THEN** resume preserves consumed allowances and reassembles the same candidate set from the baseline before continuing

#### Scenario: Crash occurs after the group commit
- **WHEN** a trailer-valid joint commit and exact evidence exist but State, file ledger or event publication is incomplete
- **THEN** resume completes all missing member outputs once without rerunning an Agent, validation or Git commit

#### Scenario: An independent branch remains executable
- **WHEN** a failed group does not share a blocking dependency or required build/runtime closure with another pending branch
- **THEN** S6 may continue that branch only if its complete build and smoke acceptance can still pass
