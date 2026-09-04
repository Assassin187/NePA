## ADDED Requirements

### Requirement: Production S4 roles use closed stage-specific contracts
TaskPlanner SHALL receive exactly one work package, its complete responsibility-bearing Spec slice, adjacent contract summaries, applicable Test Manifest metadata, and its sealed planning budget, and SHALL return one state-free task shard using local semantic ids. PlanCritic SHALL receive only the compact complete candidate graph, deterministic coverage, and lint report and SHALL return a closed verdict plus issue list whose issues contain stable id, severity, scope, target, code, required change, and context refs. FlatPlanBaseline SHALL return one complete state-free semantic draft suitable for deterministic PlanDraftIR normalization. None of the three roles SHALL return a final Plan, Blueprint, hash, execution state, or publication instruction. (Design 7.1.1: §4.5, §6.4.4-§6.4.6, §8.8; M1-4c.)

#### Scenario: A TaskPlanner call is admitted
- **WHEN** all five declared inputs and the task-shard Schema/example are valid for one work package
- **THEN** the Agent invocation returns one validated local shard without global ids or state

#### Scenario: A PlanCritic returns a replacement Plan
- **WHEN** a critic response supplies an alternative Plan or omits its typed verdict/issue fields
- **THEN** structured-output validation rejects it and the controller cannot publish it

### Requirement: Production role visibility and strategy remain controller-owned
Each S4 Agent call SHALL be fresh, history-free, bound to stage S4 and the existing configured role route, and limited to the declared context for that role. Test visibility SHALL stop at manifest metadata and SHALL exclude test implementations, runners, oracles, and adapters. FlatPlanBaseline SHALL be available only for an explicitly sealed flat strategy; no Agent SHALL select a strategy, model, repair route, budget, or checkpoint. (Design 7.1.1: §4.5-§4.7, §6.4, §6.4.7; D1.6/D1.8/D1.11; M1-4c.)

#### Scenario: A layered run requests FlatPlanBaseline
- **WHEN** the sealed strategy is layered
- **THEN** invocation fails before rendering or provider I/O

#### Scenario: Test code exists on disk
- **WHEN** a production S4 context is assembled in a repository that contains test implementations
- **THEN** no S4 Agent input contains those implementation bytes or filesystem-derived test facts
