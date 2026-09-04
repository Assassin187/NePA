## MODIFIED Requirements

### Requirement: M1-3 provides the required built-in role skeletons
The system SHALL register ArchitecturePlanner, TaskPlanner, PlanCritic, FlatPlanBaseline, Coder, Diagnoser and Fixer with their authoritative inputs and output contracts. ArchitecturePlanner SHALL remain one role and route but use `architecture_planner_initial.md` for a null repair context and `architecture_planner_repair.md` for a non-null context. Initial calls SHALL return a complete ArchitectureDraft. M1-4a2 repair calls SHALL return only the lineage-frozen patch payload. Every repair call SHALL be fresh, history-free and self-contained; it SHALL NOT concatenate or rely on the initial template. If a patch is rejected for payload format, path or application semantics, the same semantic depth MAY issue one fresh correction call containing the unchanged candidate, current failures, normalized allowed paths and exact rejection reason. A successfully applied candidate transition, not a rejected payload, SHALL consume semantic depth. The existing trace SHALL record every call without adding bundle or template digest fields. (Design: §4.5, §6.4.8, §8.8.)

#### Scenario: Initial call is rendered
- **WHEN** ArchitecturePlanner receives a null repair context
- **THEN** only the initial template is rendered and ArchitectureDraft is the output contract

#### Scenario: Repair call is rendered
- **WHEN** ArchitecturePlanner receives a repair context
- **THEN** only the self-contained repair template is rendered with current candidate, failures and normalized allowed paths, and patch is the output contract

#### Scenario: First patch is rejected
- **WHEN** a repair patch is rejected for format, path or application semantics and no correction has been used at this depth
- **THEN** one fresh correction call receives the exact rejection reason while the candidate and semantic depth remain unchanged

#### Scenario: Corrected patch is also rejected
- **WHEN** the one allowed correction at a semantic depth is rejected
- **THEN** that depth ends without candidate mutation or full-draft fallback

#### Scenario: Prompt trace is recorded
- **WHEN** any initial, repair or correction call completes
- **THEN** existing trace fields identify the rendered request, stage and usage without a new prompt-bundle hash gate
