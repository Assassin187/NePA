## MODIFIED Requirements

### Requirement: S6 admission accepts only one coherent ready execution baseline
S6 SHALL reconcile activation, current-epoch materialization and verification transactions in that order before admission, then validate the active Plan, frozen inputs and configuration, current epoch/binding receipts, immutable and current manifest/map, file and revision ledgers, Plan State, workspace HEAD/tree and clean status. When F2 reuses an epoch receipt, the current Blueprint SHALL bind the active Plan/current manifest/map while the receipt Blueprint SHALL remain bound to its `materialized_plan_ref`; Blueprint equality SHALL be required only when that ref is the active Plan. The latest activation SHALL be F2 in the same epoch, the current binding SHALL point to the reused receipt, and its checkpoint SHALL remain an ancestor. A fresh run SHALL initialize Plan State exactly once only when no State exists and HEAD is the accepted ready E0 checkpoint; missing State beside any later task or epoch history SHALL fail closed. A resumed or revised run SHALL admit the current accepted F2/F3 Plan and binding when its lineage is complete. A current E1+ receipt with `materialization_status=pending_repair` SHALL be admissible only to process its exact frozen repair groups before ordinary execution; `ready` SHALL remain required before ordinary work proceeds. Admission SHALL NOT require the current Plan to remain 1.0.0/E0/0, compare mutable current metadata to an obsolete binding, or skip pending migration work. (Design: §4.8, §5.2.4-§5.2.5, §5.6.7, §6.6; pipeline §5.6-§5.6.1; D1.15; M1-6/M1-9.)

#### Scenario: Fresh ready E0 is admitted
- **WHEN** S5 is done, E0 is ready, every binding is valid, workspace HEAD is exactly the E0 checkpoint and Plan State does not exist
- **THEN** S6 publishes the unique all-pending Plan State before checking the execution-attempt budget

#### Scenario: Accepted F2 state is admitted
- **WHEN** the active F2 Plan, metadata-only binding, unchanged epoch receipt, migrated State, ledgers and workspace all agree after reconciliation
- **THEN** S6 admits the pending migration modes without reopening S5 or requiring an E0 pointer

#### Scenario: Pending-repair F3 epoch is admitted narrowly
- **WHEN** the current accepted F3 epoch is `pending_repair` and its receipt, activation and State agree on the frozen pending groups
- **THEN** S6 admits only group processing and does not select ordinary work until those groups succeed

#### Scenario: State is missing after execution began
- **WHEN** Plan State is absent but workspace history contains a task or later-epoch commit after E0
- **THEN** S6 reports artifact damage without reconstructing task completion from Git history

#### Scenario: Admission facts drift
- **WHEN** the Plan, configuration, activation lineage, binding, receipt, ledger, workspace tree or current pointer disagrees with its accepted anchor
- **THEN** S6 changes no execution state and fails through the designed controlled or corruption route
