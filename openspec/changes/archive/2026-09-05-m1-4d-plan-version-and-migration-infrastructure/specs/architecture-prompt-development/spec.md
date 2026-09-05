## MODIFIED Requirements

### Requirement: Owner approval gates M1-4c handoff
After a qualifying selection under the current ArchitectureDraft Schema lineage, the workflow SHALL require a recorded owner approval that references the selected bundle, protocol-neutrality result and recomputable 2/3 evidence before publishing an M1-4c handoff. A handoff created for an earlier ArchitectureDraft contract that lacks required internal-contract exports SHALL NOT admit production S4 after design 7.2.0. The replacement handoff SHALL state that actual quality remains subject to D1.3 complete-chain observation. (Design 7.2.0: §6.4.8.2, §10.2; pipeline design 1.3.0 §3.2; D1.0/D1.3; M1-4d.)

#### Scenario: Machine selection has no owner approval
- **WHEN** a current-lineage version reaches 2/3 but no valid owner approval exists
- **THEN** the selection may be reported but no M1-4c handoff is published

#### Scenario: An older approved handoff is presented
- **WHEN** its lineage binds an ArchitectureDraft Schema without the required export contract
- **THEN** production S4 stops before provider I/O and does not treat the historical approval as approval of the new contract

#### Scenario: Owner approves the replacement baseline
- **WHEN** the selected bundle, neutrality result, current Schema lineage and evidence are intact and the owner approval references them
- **THEN** the workflow publishes an M1-4c-only handoff without a quality or cross-model claim

### Requirement: New evidence is isolated from historical protocols
New development reports and decisions for the export-bearing ArchitectureDraft SHALL be recomputable only from a new design-7.2.0 lineage using the existing single-slot V0-V2, N=3 and 2/3 selection policy. Historical fixed-model, recovery, formal-calibration, single-template, pre-fix patch, and pre-export ArchitectureDraft artifacts SHALL remain immutable and readable through their legacy contracts but SHALL NOT enter the new denominator, ranking, selection or handoff. (Design 7.2.0: §0.1, §6.4.8, §9.2; pipeline design 1.3.0 §3.2; M1-4d.)

#### Scenario: Pre-export evidence is offered to the new selector
- **WHEN** evidence belongs to a lineage whose ArchitectureDraft Schema omits `contracts[].exports`
- **THEN** the selector rejects it while legacy recomputation remains available

#### Scenario: The replacement lineage is developed
- **WHEN** the current Schema, validator, serializer, patch contract, model slot and request configuration are frozen
- **THEN** V0 runs first and V1/V2 remain conditional under the unchanged bounded development policy
