## Purpose

Define the deterministic, auditable gate sequence that either rejects a non-authoritative revision candidate without changing accepted execution state or atomically activates it and routes the accepted F2/F3 successor into its existing downstream execution path.

## ADDED Requirements

### Requirement: Revision gates consume one frozen authoritative boundary
The system SHALL admit a revision candidate only when its candidate id, selected trigger event, trigger signature and level, source Plan, current active pointer, Plan State, file ledger, revision-ledger prefix, workspace commit/tree, Blueprint, contract map, migration, and content hashes all agree with the accepted M1-10 handoff. Before every external call or persistent publication boundary, it SHALL re-read those authoritative inputs under the run lock and SHALL fail closed on drift. Gate evaluation SHALL begin only at an S6 task boundary with no attempt, lease, verification, repair group, materialization, or activation transaction in flight. (Design: system design §5.6.7; pipeline §4.2-§4.4, §5.6, §6.3-§6.4; D1.13; M1-11.)

#### Scenario: Accepted handoff is admitted
- **WHEN** the selected event, candidate and every current artifact/hash equal the accepted handoff and no transaction is in flight
- **THEN** gate evaluation starts from that one frozen boundary

#### Scenario: Candidate ancestry is stale
- **WHEN** the active pointer, ledger prefix, workspace tree or any candidate-bound reference changes after candidate publication
- **THEN** no gate result, rejection, formal Plan version, binding, State migration or activation is accepted from that stale candidate

#### Scenario: Another transaction is pending
- **WHEN** activation, materialization or verification reconciliation has not completed
- **THEN** revision-gate admission is refused until the earlier transaction reaches its unique legal state

### Requirement: RG-1 through RG-5 are evaluated in fixed order
The system SHALL evaluate the five gates in the order RG-1 TRIGGER, RG-2 INVARIANT, RG-3 BUDGET, RG-4 CRITIC, and RG-5 REHEARSAL and SHALL stop at the first failure. RG-1 SHALL require the trigger predicate and evidence to remain valid, the signature/level not to have been tried, and the signature not to have an accepted activation. RG-2 SHALL require INV-1/2/3, the allowed operator set, Linker/full lint, Blueprint, migration mapping and, for F3, affected architecture gates to pass. RG-3 SHALL require the level's actual preservation rate to meet its frozen `rho_min`, estimated task/group rework cost to be no more than half the remaining global cost budget, and both the level activation limit and remaining run-wide execution-call capacity to be sufficient. Every evaluated result SHALL be persisted with evidence refs in the candidate area; unevaluated later gates SHALL be explicitly distinguishable from pass/fail. (Design: system design §4.7, §8.3; pipeline §3.4, §6.3, §6.5; D1.13; M1-11.)

#### Scenario: RG-1 fails
- **WHEN** the trigger no longer holds or its signature/level was already tried
- **THEN** RG-1 is the failed gate and RG-2 through RG-5 are not evaluated

#### Scenario: Invariant evidence is invalid
- **WHEN** an otherwise current candidate weakens a commitment, ownership, acceptance or lineage obligation
- **THEN** RG-2 fails and no budget, critic or rehearsal result is treated as accepted

#### Scenario: Rework estimate exceeds its share
- **WHEN** recomputed rework cost is greater than half the remaining global cost budget
- **THEN** RG-3 fails even if preservation and activation-count limits pass

#### Scenario: One level has no activation allowance
- **WHEN** the candidate's F2 or F3 successful-activation limit is zero or exhausted
- **THEN** RG-3 fails for that level without consuming an allowance from the other level

### Requirement: RG-4 uses the existing independent PlanCritic contract
For RG-4, the system SHALL submit only the candidate delta and its complete affected closure, recomputed coverage and lint report to the existing PlanCritic route at temperature zero. It SHALL validate the closed critic response and SHALL pass RG-4 only when the verdict contains no blocker or major issue; minor issues MAY remain as candidate review metadata. PlanCritic SHALL NOT change the candidate, select a patch or level, claim execution success, activate a Plan, see test implementations, or cause ArchitecturePlanner, TaskPlanner or PlanReviser to run. The invocation and its actual usage SHALL remain charged even when RG-4 or a later gate rejects the candidate. (Design: system design §4.5-§4.7, §6.4.6, §8.8; pipeline §6.2.1-§6.3; D1.6/D1.11/D1.13; M1-11.)

#### Scenario: Frozen critic response passes
- **WHEN** a Schema-valid stub or frozen PlanCritic response reports pass with no blocker or major issue
- **THEN** RG-4 passes and the exact response/call evidence is bound to the gate result

#### Scenario: Critic reports a major issue
- **WHEN** the response contains any blocker or major issue
- **THEN** RG-4 fails and no repair or alternate planning role is invoked

#### Scenario: Critic output is invalid
- **WHEN** the response is missing required fields, contradicts its issue severities or attempts to return a replacement Plan
- **THEN** RG-4 fails through the existing typed Agent failure path and the candidate is not activated

### Requirement: RG-5 rehearses only structural revisions
For an F3 candidate, RG-5 SHALL rehearse the declared structural difference in an isolated temporary copy derived from the accepted workspace, using the same deterministic S5 rendering/materialization, build and smoke contracts as the live path. It SHALL pass only when the candidate structure closes, repeating the rehearsal is byte/tree idempotent, existing realized owned content is preserved or explicitly quarantined/re-adopted, and every build failure is completely attributable to a registered pending repair group; any unrelated or partially mapped failure SHALL fail the gate. For F2, RG-5 SHALL be recorded as `not_applicable` and SHALL NOT render, modify or checkpoint a workspace. Rehearsal SHALL never change the live workspace, file ledger, epoch, binding, Plan version or active pointer. (Design: system design §5.6.7, §6.5, §10.2.2; pipeline §3.3-§3.5, §5.4, §6.3; D1.12/D1.13; M1-11.)

#### Scenario: F3 rehearsal is closed and idempotent
- **WHEN** two isolated rehearsals produce the same complete tree and all failures map exactly to registered groups
- **THEN** RG-5 passes with evidence for the tree, builds, smoke, difference and group attribution

#### Scenario: F3 rehearsal has an unrelated failure
- **WHEN** a build or smoke failure is outside the registered affected-group closure
- **THEN** RG-5 fails and the live workspace remains byte- and HEAD-identical

#### Scenario: F2 reaches RG-5
- **WHEN** RG-1 through RG-4 pass for an F2 candidate
- **THEN** RG-5 is `not_applicable`, no S5 rehearsal runs, and the candidate may proceed to activation

### Requirement: A failed gate produces one rejection and no activation
When any gate fails, the system SHALL atomically append exactly one idempotent `candidate_rejected` event containing the candidate id, selected trigger event sequence, level, first failed gate, reason and evidence refs. Rejection SHALL advance only `event_seq`; it SHALL NOT create a formal version or binding, append `revision_activated`, increment `revision_seq`, advance `active_plan.json`, change Plan State or file ledger, modify the Run active reference, touch the workspace, or refund consumed model/build budget. Replaying the same candidate and rejection payload SHALL be a no-op, while a conflicting payload for that candidate SHALL fail as artifact damage. (Design: system design §5.6.7; pipeline §4.3, §6.1.1, §6.3; D1.13; M1-11.)

#### Scenario: Candidate is rejected at RG-3
- **WHEN** RG-1 and RG-2 pass but the frozen budget gate fails
- **THEN** one RG-3 `candidate_rejected` event is accepted and every authoritative Plan/execution/workspace artifact remains unchanged

#### Scenario: Rejection is replayed
- **WHEN** recovery or S6 presents the same candidate and byte-equivalent rejection again
- **THEN** the ledger remains byte-equivalent and no second rejection event is appended

#### Scenario: Rejection payload conflicts
- **WHEN** the same candidate id is presented with another failed gate, reason or evidence set
- **THEN** the accepted ledger is preserved and artifact damage is reported

### Requirement: Successful gates route the activated level to its existing consumer
When all applicable gates pass and activation commits, the system SHALL return S6 to pending against the new active revision without sealing an S6 receipt. An F2 activation SHALL preserve the current epoch and enter the already-defined INHERIT/REVALIDATE/AMEND/REGENERATE execution view using its new metadata binding. An F3 activation SHALL increment the epoch, make the corresponding S5 instance pending, complete that epoch through the existing multi-epoch materialization contract, and execute registered affected repair groups before ordinary S6 work can continue. A rejection SHALL resume the same pre-activation S6 execution view without fabricating completion. (Design: system design §5.2.4, §5.6.7, §6.5-§6.6; pipeline §4.4, §5.4-§5.6.1, §6.4; D1.13/D1.15; M1-8/M1-9/M1-11.)

#### Scenario: F2 activation commits
- **WHEN** an F2 candidate passes every applicable gate and the pointer commits
- **THEN** S6 resumes the accepted migrated modes in the unchanged epoch using the F2 binding

#### Scenario: F3 activation commits
- **WHEN** an F3 candidate passes every gate and the pointer commits
- **THEN** the new S5 epoch becomes the next required instance and S6 cannot continue until its materialization and registered group handling are complete

#### Scenario: Gate failure returns to S6
- **WHEN** a candidate rejection is durably accepted
- **THEN** S6 remains pending on the unchanged active Plan and continues only work legal under that pre-activation view
