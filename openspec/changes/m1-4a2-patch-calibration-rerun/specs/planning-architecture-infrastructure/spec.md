## MODIFIED Requirements

### Requirement: Lineage identity freezes every non-prompt comparison variable
The system SHALL derive `lineage_id` from frozen input references, planning-index and Delivery-Constraints construction, ArchitectureDraft Schema, patch Schema, serializer, patch application/locality behavior, path normalization, coupled-reference projection, `ARCH_VALIDATE`, the one configured logical model slot and its request configuration, metric definitions and two-stage invocation contract. Prompt source bytes and versions SHALL remain existing referenced version artifacts outside lineage identity. Any non-prompt component or configured slot change SHALL create a new lineage; changing either or both prompt templates between V0～V2 SHALL remain in the same lineage and record exact diffs. Historical evidence from another lineage SHALL NOT be aggregated. (Design: §0.1, §6.4.8.1～§6.4.8.2, §8.3, §9.2.)

#### Scenario: Prompt templates change
- **WHEN** a revision changes initial, repair or both while all non-prompt components remain equal
- **THEN** it stays in the lineage and records both stage diffs

#### Scenario: Configured model slot changes
- **WHEN** the logical slot, provider endpoint, route or request parameters change
- **THEN** a new lineage is required without any design-document change

### Requirement: Semantic repair is explicit, fresh, and bounded by the declared protocol
The M1-4a2 trial engine SHALL use the initial template for depth zero and the repair template for at most two successfully applied semantic transitions. Before rendering repair, it SHALL normalize numeric validator issue positions to stable identifier paths in the current candidate. A patch MAY change multiple concrete leaves when every leaf is authorized by at least one current failure. Patch operations SHALL remain presence-checked, conflict-free, atomically applied and fully revalidated. If an admitted layout identity changes, the controller SHALL derive only exact old-to-new substitutions in module ownership and work-package allowed-file references; unrelated model operations that are independently allowed SHALL remain admissible. One rejected payload at each semantic depth MAY receive one correction call, and no rejected payload SHALL mutate the candidate, consume effective depth or trigger full-draft replacement. (Design: §6.4.8.1～§6.4.8.2, §8.4, §8.8.)

#### Scenario: Numeric issue path is rendered
- **WHEN** a validator issue points through an array index whose item has a stable id
- **THEN** the repair request presents the equivalent stable identifier path and no numeric array path

#### Scenario: Patch fixes multiple current failures
- **WHEN** all operations map to currently allowed concrete paths
- **THEN** they may apply atomically even when one operation also triggers exact layout-reference projection

#### Scenario: Patch needs correction
- **WHEN** the first payload at a depth is rejected for format, path or application semantics
- **THEN** one correction call may run against the unchanged candidate and a successful corrected patch establishes that depth's candidate

### Requirement: Trial artifacts and calibration reports are hash-bound and recomputable
Calibration evidence SHALL remain under `runs/_calibration/s4-architecture/<lineage_id>/<prompt_version>/<model_slot>/`. Each declared trial SHALL retain every request attempt, response, validation, patch, application and correction record. Completed trial leaves SHALL be immutable. Recompute SHALL reproduce the single-slot report and reject missing, mutated, duplicate, cross-lineage or cross-bundle evidence. Historical artifact contracts SHALL remain available for read-only recomputation. (Design: §0.1, §5.5, §6.4.8.1～§6.4.8.2, §9.1.5.)

#### Scenario: One trial exhausts infrastructure attempts
- **WHEN** its final attempt records no usable response
- **THEN** recomputation records that trial as infrastructure-invalid while retaining other completed trials in the version

#### Scenario: Historical batch is recomputed
- **WHEN** a legacy lineage declares the former fixed-slot contract
- **THEN** the legacy reader recomputes it without admitting any leaf to the new selector

### Requirement: Calibration metrics preserve fixed denominators and failure evidence
The active single-slot report SHALL use N=3 as the denominator for Schema rates, p0, p1 and p2. Schema failure, semantic failure, truncation, rejected/exhausted patch repair and infrastructure-invalid SHALL remain non-passes in that denominator. Reports SHALL retain per-gate results, repair gain, rejection reasons, attempts, tokens, cost, latency, finish reason, requested/returned model identity and parameter support. (Design: §6.4.8.2, §9.1.5, §9.2.)

#### Scenario: Two trials pass by p2
- **WHEN** exactly two of three trials pass within two effective repairs
- **THEN** p2 is 2/3 and the report supplies sufficient evidence for minimum-usability selection

#### Scenario: One trial is infrastructure-invalid
- **WHEN** two trials pass and one exhausts infrastructure attempts
- **THEN** p2 remains 2/3 and the infrastructure failure remains visible rather than invalidating the report

## REMOVED Requirements

### Requirement: Three-model trial execution is isolated and cache-independent
**Reason**: ArchitecturePlanner prompt development no longer evaluates cross-model stability and uses one configuration-selected logical slot.

**Migration**: Preserve historical three-model evidence under legacy readers. New protocols record one `model_slot`; changing its configured model creates a new lineage.

## ADDED Requirements

### Requirement: Configured single-model trial execution is isolated and cache-independent
The active M1-4a2 protocol SHALL execute exactly one configured logical model slot. Its three trials SHALL use fresh history-free sessions and disabled cross-trial cache. The slot name SHALL be a safe configured identifier and SHALL NOT be constrained to a provider or model name. (Design: §6.4.8.1～§6.4.8.2, §8.3.)

#### Scenario: Single-slot batch starts
- **WHEN** active preflight resolves one configured calibration slot
- **THEN** exactly three isolated trial identities are declared under that slot before provider I/O

#### Scenario: Model implementation changes
- **WHEN** a different configured model is selected for a future experiment
- **THEN** the design and prompt remain unchanged while a new lineage records the new request configuration
