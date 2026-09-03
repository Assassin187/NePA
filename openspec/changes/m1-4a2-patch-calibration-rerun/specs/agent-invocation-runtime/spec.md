## MODIFIED Requirements

### Requirement: M1-3 provides the required built-in role skeletons
The system SHALL register prompt and invocation skeletons for ArchitecturePlanner, TaskPlanner, PlanCritic, FlatPlanBaseline, Coder, Diagnoser, and Fixer. Their required inputs SHALL match the role boundaries in the authoritative design: ArchitecturePlanner receives the planning index, delivery constraints, and an explicit `repair_context` that is null for an initial call or contains the current Schema-valid candidate, exact canonical `ARCH_VALIDATE` failures and any controller-derived repair scope required by the declared protocol; TaskPlanner receives one work package, its spec slice, adjacent contracts, and test metadata; PlanCritic receives a candidate plan graph, coverage matrix, and lint report; FlatPlanBaseline receives the planning index, delivery constraints, and manifest metadata; Coder receives one task, its spec slice, and interface files; Diagnoser receives build errors and relevant code; Fixer receives a diagnosis and target files. ArchitecturePlanner SHALL remain one role with one model route but SHALL use the shared two-stage prompt bundle: a null `repair_context` selects only `architecture_planner_initial.md`, while every non-null semantic-repair context selects only `architecture_planner_repair.md`. The renderer SHALL NOT concatenate, inherit or recover the initial template through conversation history during a repair call. Initial calls SHALL bind and return the production ArchitectureDraft contract. M1-4a2 semantic-repair calls SHALL bind and return only the lineage-frozen patch contract without requiring any model-authored prior-value hash or value digest; a full ArchitectureDraft repair response SHALL be rejected rather than treated as a replacement. M1-4a2r and formal-calibration repairs SHALL select the same repair template while retaining their declared full-ArchitectureDraft output contracts. Deterministic coupled-reference projections caused by an admitted M1-4a2 layout-path edit SHALL be controller-authored and SHALL NOT appear as additional model-editable paths or require the model to rewrite complete ownership/work-package collections. M1-4a1 SHALL own the production ArchitectureDraft and patch infrastructure contracts, while shared two-stage prompt development and production freeze remain M1-4a2 and M1-4a3 responsibilities. The split SHALL reuse the existing per-call trace prompt record and SHALL NOT add bundle hashes, per-template digest fields or new hash validation gates. (Design: §0.1, §4.5, §6.4.4, §6.4.8.1～§6.4.8.3, §8.8.)

#### Scenario: Built-in catalog is inspected
- **WHEN** the Agent registry is initialized
- **THEN** it contains exactly the seven role identifiers and one ArchitecturePlanner role exposes explicit initial-draft and M1-4a2 repair-patch invocation contracts without changing the other roles

#### Scenario: Initial architecture call is rendered
- **WHEN** ArchitecturePlanner is invoked for an initial candidate
- **THEN** `repair_context` is canonical null, only `architecture_planner_initial.md` is rendered, and the bound output contract is ArchitectureDraft

#### Scenario: M1-4a2 semantic repair is rendered
- **WHEN** ArchitecturePlanner is invoked for a first or second M1-4a2 semantic repair
- **THEN** only `architecture_planner_repair.md` is rendered with the current candidate, exact canonical issue list and allowed patch paths, and the bound output contract accepts presence-checked patch operations without a prior-value digest but not a complete ArchitectureDraft

#### Scenario: Repair rendering is isolated from initial planning
- **WHEN** any declared ArchitecturePlanner semantic repair is rendered
- **THEN** no initial-template body or hidden conversation history appears in the repair request and the role/model route remains the same as depth zero

#### Scenario: Full-draft repair protocol is rendered
- **WHEN** M1-4a2r or formal calibration declares a full-ArchitectureDraft semantic repair
- **THEN** the call uses `architecture_planner_repair.md` while retaining the declared full-draft output contract rather than switching to the M1-4a2 patch contract

#### Scenario: Prompt trace is recorded
- **WHEN** either stage invokes ArchitecturePlanner
- **THEN** the existing per-call prompt trace fields describe the actually rendered request without creating a bundle digest, second template-hash field or new hash precondition

#### Scenario: Full draft is returned during patch repair
- **WHEN** an M1-4a2 repair response attempts to return a complete ArchitectureDraft
- **THEN** output-contract validation rejects it and the Agent layer does not apply it as a replacement

#### Scenario: Layout path repair has coupled references
- **WHEN** the model returns an allowed patch changing a layout path identity
- **THEN** the Agent response remains limited to that model-editable path and any exact ownership/work-package substitutions are derived and recorded by the controller rather than requested as broad model edits
