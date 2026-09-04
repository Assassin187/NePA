## Purpose

Define the bounded, protocol-neutral controller contract that turns one validated spec-run input set into a fully reviewed S4 Plan candidate while preserving deterministic ownership, recoverability, and honest failure semantics.

## ADDED Requirements

### Requirement: S4 progresses through three closed planning phases
The S4 controller SHALL execute S4a commitment preparation, S4b architecture and layout planning, and S4c task decomposition and review in that order. S4a SHALL perform no LLM call; S4b SHALL invoke only ArchitecturePlanner; layered S4c SHALL invoke TaskPlanner once per admitted work package before deterministic linking and SHALL invoke PlanCritic only after a complete candidate passes full lint. Every id, reference, dependency, coverage row, Blueprint field, hash, and publication decision owned by deterministic code SHALL be absent from the closed Agent-output contracts; a response that supplies one of those forbidden fields SHALL fail structured-output validation. S4 SHALL NOT write the generated workspace. (Design 7.1.1: §6.4-§6.4.6; pipeline design 1.2.0 §5.1-§5.3; M1-4c.)

#### Scenario: Layered planning reaches the critic
- **WHEN** valid frozen inputs produce an accepted architecture and one valid shard for every work package
- **THEN** the controller builds one normalized PlanDraftIR, runs the deterministic Linker and full S4-G0 through S4-G6 lint, and invokes PlanCritic only on the resulting complete candidate

#### Scenario: A draft attempts to supply mechanical fields
- **WHEN** an Agent response includes final task ids, hashes, coverage, Blueprint fields, review state, or execution state
- **THEN** closed-Schema validation rejects the response and no candidate is published from it

### Requirement: Layered and flat strategies are isolated
The production default SHALL be `layered`. The `flat` strategy SHALL run only when explicitly selected for the A9 comparison arm and SHALL invoke FlatPlanBaseline instead of ArchitecturePlanner and TaskPlanner. Both strategies SHALL normalize to the same state-free PlanDraftIR and SHALL use the same deterministic Linker, Blueprint compiler, full lint, PlanCritic, and publication gate. The controller SHALL NOT switch strategies after admission or use flat as a fallback for layered failure. (Design 7.1.1: §6.4, §6.4.2, §6.4.6; D1.8; M1-4c.)

#### Scenario: Flat is explicitly selected
- **WHEN** the sealed configuration selects `planning.strategy=flat`
- **THEN** exactly the flat planning role produces the semantic draft and the common deterministic completion path processes it

#### Scenario: Layered planning fails
- **WHEN** any layered architecture or shard operation exhausts its allowed repair or cannot produce a valid output
- **THEN** S4 fails in the layered strategy and FlatPlanBaseline is not invoked

### Requirement: Semantic repair is targeted and bounded
The controller SHALL allow at most the sealed configuration budgets: one ArchitecturePlanner semantic repair, one semantic shard redo per work package across the whole S4 run, two PlanCritic repair rounds, and one global replan. A layered local critic issue SHALL invalidate only its target shard; a layered global issue SHALL return to ArchitecturePlanner and invalidate every checkpoint whose parent architecture hash changes; a flat semantic issue SHALL invalidate and regenerate the whole flat draft while consuming both a critic-repair and global-replan unit. Mechanical recomputation SHALL NOT consume semantic repair budgets. After each semantic repair the controller SHALL rerun complete linking, full lint, and critic review. Repeating the same canonical issue signature SHALL stop as non-convergent. (Design 7.1.1: §4.7, §6.4.4-§6.4.6; M1-4c.)

#### Scenario: One work package has a local critic issue
- **WHEN** PlanCritic returns a valid blocker or major scoped to one layered work package and its redo budget remains
- **THEN** only that package is re-expanded before the complete candidate is relinked, relinted, and reviewed

#### Scenario: An issue signature repeats
- **WHEN** a blocker or major with the same normalized scope, target, and code recurs after its admitted repair
- **THEN** S4 stops with a controlled non-convergence failure and publishes no Plan

### Requirement: S4 checkpoints are parent-bound and resumable
Every persisted `_s4` commitment, architecture, shard, link, lint, candidate, and review checkpoint SHALL validate against its closed contract and record the hashes of the authoritative parent inputs from which it was produced. Resume SHALL reuse only the longest valid prefix whose parent hashes still match, continue from the first missing or invalid checkpoint, and never treat `_s4` as a downstream semantic source. Once the S4 seal is valid and `stages.s4=done`, replay SHALL verify the seal and return as a read-only no-op. (Design 7.1.1: §4.8, §6.4.2, §6.4.7; D1.4; M1-4c.)

#### Scenario: Resume follows a shard crash
- **WHEN** a process exits after valid architecture and earlier shard checkpoints but before all shards exist
- **THEN** resume reuses the matching prefix and starts with the first missing work package without repeating accepted earlier Agent calls

#### Scenario: A parent input drifts
- **WHEN** a checkpoint is structurally valid but its recorded parent hash differs from the current frozen input, commitment, architecture, or predecessor candidate
- **THEN** that checkpoint and its descendants are not reused and no downstream stage consumes them

### Requirement: Expected S4 failures are controlled and publish no partial Plan
Frozen-input drift, context overflow, invalid or truncated structured output after the existing one format repair, exhausted semantic budget, invalid shard or architecture after its allowed repair, critic non-convergence, and full-lint failure SHALL preserve diagnostic checkpoints, mark S4 failed through the existing controlled-exit path, and publish no consumable Plan or active pointer. Internal invariant violations and deterministic tool defects SHALL remain `internal_error`, not controlled planning failures. (Design 7.1.1: §4.7-§4.8, §6.4.3-§6.4.7; D1.10; M1-4c.)

#### Scenario: Context preflight exceeds the model boundary
- **WHEN** a complete required planning context plus output reserve and safety margin exceeds the selected route's configured limit
- **THEN** S4 records `PLAN_CONTEXT_TOO_LARGE` before provider I/O and publishes no partial Plan

#### Scenario: A deterministic invariant fails
- **WHEN** the controller detects an impossible internal state or its publication tool cannot preserve the required atomicity invariant
- **THEN** the run terminates as an internal error rather than reporting an Agent or protocol failure
