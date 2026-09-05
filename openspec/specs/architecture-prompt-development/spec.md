# architecture-prompt-development Specification

## Purpose

Define a small, configuration-driven workflow that develops and selects one usable ArchitecturePlanner prompt bundle without treating prompt development as cross-model production qualification.

## Requirements

### Requirement: Development uses one configured model and at most three versions
M1-4a2 SHALL use exactly one safe logical model slot from resolved configuration. V0 SHALL run first and V1/V2 SHALL run only while no earlier version meets the minimum. Every version SHALL declare exactly three fresh initial trials, and the lineage SHALL permit at most nine initial trials. Model/provider names SHALL be recorded observations rather than fixed design gates. (Design: §6.4.8.1～§6.4.8.2, §8.3; D1.0.)

#### Scenario: Current configuration selects one model
- **WHEN** preflight resolves exactly one calibration model slot
- **THEN** the protocol records that slot and its request configuration without requiring a particular slot, provider or model string

#### Scenario: Configuration contains zero or multiple slots
- **WHEN** active M1-4a2 preflight receives anything other than one calibration model slot
- **THEN** it fails before provider I/O

#### Scenario: Version budget is exhausted
- **WHEN** V0, V1 and V2 have each declared three trials
- **THEN** the coordinator rejects V3 and every further initial-generation call

### Requirement: Trial failures are isolated
Each trial SHALL preserve its own completed leaves and outcome. Ordinary model failure, Schema failure, truncation or exhausted transport attempts SHALL affect only that trial and SHALL NOT invalidate or rerun another trial or the whole version. In addition to the shared provider retry policy, at most two further attempts SHALL target the same trial identity; exhausted infrastructure SHALL count as a non-pass in the fixed N=3 denominator. (Design: §6.4.8.2, §8.4; D1.0.)

#### Scenario: One answer fails
- **WHEN** one trial ends in semantic or Schema failure
- **THEN** the other declared trials continue and every already completed trial remains byte-identical

#### Scenario: Transport retries are exhausted
- **WHEN** one trial exhausts provider retries and two additional same-trial attempts
- **THEN** that trial records infrastructure-invalid, counts as not passing and does not invalidate the version

### Requirement: Two of three passing trials select the baseline
A version SHALL meet minimum usability only when at least two of its three trials pass the complete `ARCH_VALIDATE` suite within at most two successfully applied patch repairs. The first qualifying version SHALL be selected and later versions SHALL be refused. The selection SHALL make no cross-model stability or production-quality claim. (Design: §6.4.8.2; D1.0.)

#### Scenario: Two trials pass by the second repair
- **WHEN** two of three trials have cumulative p2 success
- **THEN** the version is selected as the baseline and no later version may call a provider

#### Scenario: Only one trial passes
- **WHEN** a version has one cumulative p2 success
- **THEN** it remains diagnostic and the next available version may be admitted

### Requirement: Terminal failure produces a diagnostic reference only
If V2 completes without a qualifying version, the workflow SHALL identify one diagnostic reference by comparing p2 pass count, p1 pass count, p0 pass count, Schema-valid count, lower total cost and earlier version in that order. It SHALL NOT publish a usable-baseline selection, a recovery branch or an M1-4c handoff. (Design: §6.4.8.2.)

#### Scenario: No version reaches two passes
- **WHEN** V2 completes and every version is below 2/3
- **THEN** one reference version and its ranking evidence are reported without any downstream admission claim

### Requirement: Prompt revisions may update either or both stages
Each V1/V2 revision SHALL bind its parent bundle, evidence, reason, exact initial-template diff and exact repair-template diff. Either diff MAY be empty, but at least one SHALL be non-empty. Schema, validator, model configuration and other lineage components SHALL remain unchanged. (Design: §6.4.8.1～§6.4.8.2.)

#### Scenario: Both templates need correction
- **WHEN** evidence identifies missing construction instructions in initial and missing actionable repair guidance
- **THEN** one revision may change both templates and records both diffs before provider I/O

### Requirement: Owner approval gates M1-4c handoff
After a qualifying selection under the current ArchitectureDraft Schema lineage, the workflow SHALL require a recorded owner approval that references the selected bundle, protocol-neutrality result and recomputable 2/3 evidence before publishing an M1-4c handoff. A handoff created for an earlier ArchitectureDraft contract that lacks required internal-contract exports SHALL NOT admit production S4 after design 7.2.0. The replacement handoff SHALL state that actual quality remains subject to D1.3 complete-chain observation. (Design 7.2.0: §6.4.8.2, §10.2; pipeline design 1.3.0 §3.2; D1.0/D1.3; M1-4d.)

#### Scenario: Machine selection has no owner approval
- **WHEN** a current-lineage version reaches 2/3 but no valid owner approval exists
- **THEN** the selection may be reported but no M1-4c handoff is published

#### Scenario: Owner approves the replacement baseline
- **WHEN** the selected bundle, neutrality result, current Schema lineage and evidence are intact and the owner approval references them
- **THEN** the workflow publishes an M1-4c-only handoff without a quality or cross-model claim

#### Scenario: An older approved handoff is presented
- **WHEN** its lineage binds an ArchitectureDraft Schema without the required export contract
- **THEN** production S4 stops before provider I/O and does not treat the historical approval as approval of the new contract

### Requirement: New evidence is isolated from historical protocols
New development reports and decisions for the export-bearing ArchitectureDraft SHALL be recomputable only from a new design-7.2.0 lineage using the existing single-slot V0-V2, N=3 and 2/3 selection policy. Historical fixed-model, recovery, formal-calibration, single-template, pre-fix patch, and pre-export ArchitectureDraft artifacts SHALL remain immutable and readable through their legacy contracts but SHALL NOT enter the new denominator, ranking, selection or handoff. (Design 7.2.0: §0.1, §6.4.8, §9.2; pipeline design 1.3.0 §3.2; M1-4d.)

#### Scenario: Historical report is offered to the new selector
- **WHEN** evidence belongs to a legacy three-model or removed recovery/formal-calibration protocol
- **THEN** the selector rejects it while legacy recomputation remains available

#### Scenario: Pre-export evidence is offered to the new selector
- **WHEN** evidence belongs to a lineage whose ArchitectureDraft Schema omits `contracts[].exports`
- **THEN** the selector rejects it while legacy recomputation remains available

#### Scenario: The replacement lineage is developed
- **WHEN** the current Schema, validator, serializer, patch contract, model slot and request configuration are frozen
- **THEN** V0 runs first and V1/V2 remain conditional under the unchanged bounded development policy
