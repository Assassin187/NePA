## MODIFIED Requirements

### Requirement: Production S4 roles use closed stage-specific contracts
TaskPlanner SHALL receive exactly one work package, its complete responsibility-bearing Spec slice, adjacent contract summaries, applicable Test Manifest metadata, and its sealed planning budget, and SHALL return one state-free task shard using local semantic ids. For initial planning, PlanCritic SHALL receive only the compact complete candidate graph, deterministic coverage, and lint report. For an M1-11 RG-4 review, the same PlanCritic role and route SHALL instead receive only the revision candidate's compact delta and complete affected closure, recomputed coverage and lint report, with no unrelated Plan/workspace content or test implementation. PlanCritic SHALL return the same closed verdict plus issue list whose issues contain stable id, severity, scope, target, code, required change, and context refs. FlatPlanBaseline SHALL return one complete state-free semantic draft suitable for deterministic PlanDraftIR normalization. None of the three roles SHALL return a final Plan, Blueprint, hash, execution state, patch, level choice or publication instruction, and RG-4 SHALL NOT invoke ArchitecturePlanner, TaskPlanner, FlatPlanBaseline or PlanReviser. (Design: system design §4.5-§4.7, §6.4.4-§6.4.6, §8.8; pipeline §6.2.1-§6.3; D1.6/D1.11/D1.13; M1-4c/M1-11.)

#### Scenario: A TaskPlanner call is admitted
- **WHEN** all five declared inputs and the task-shard Schema/example are valid for one work package
- **THEN** the Agent invocation returns one validated local shard without global ids or state

#### Scenario: Initial PlanCritic receives the full compact graph
- **WHEN** S4 performs its initial publication review
- **THEN** PlanCritic receives the complete compact candidate graph, coverage and lint report under its existing contract

#### Scenario: Revision PlanCritic receives a delta closure
- **WHEN** an M1-11 candidate reaches RG-4
- **THEN** PlanCritic receives only that candidate's delta, complete affected closure, coverage and lint evidence through the same closed output contract and configured reviewer route

#### Scenario: A PlanCritic returns a replacement Plan
- **WHEN** a critic response supplies an alternative Plan, patch, activation instruction or omits its typed verdict/issue fields
- **THEN** structured-output validation rejects it and the controller cannot publish or activate it

#### Scenario: Revision review attempts another planning role
- **WHEN** RG-4 receives a revise verdict or invalid response
- **THEN** the candidate is rejected without invoking ArchitecturePlanner, TaskPlanner, FlatPlanBaseline or PlanReviser
