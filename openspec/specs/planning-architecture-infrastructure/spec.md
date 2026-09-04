# planning-architecture-infrastructure Specification

## Purpose

Define the deterministic, protocol-neutral ArchitecturePlanner input, output-validation, lineage, and isolated trial evidence boundary used to establish the owner-approved M1-4a2 baseline before production S4 planning.

## Requirements

### Requirement: Calibration planning inputs are frozen with production S4 semantics
The system SHALL validate the selected Spec IR, closed two-field Target Profile, and canonical Test Bundle before creating calibration evidence. It SHALL freeze the Spec bytes, canonical Target Profile, and canonical Test Bundle under one lineage input root and SHALL record independent path/SHA-256 references. The S4-visible Test Manifest summary SHALL contain only bundle identity and the declared `nodeid`, `layer`, `description`, `req_ids`, `gate`, and resolved build-variant metadata; test implementations, runners, oracles, adapters, and filesystem existence SHALL NOT be read or exposed. (Design: §4.2.1, §5.3, §5.6.5.5, §6.4.3, §6.4.8.1.)

#### Scenario: Valid gold inputs are frozen
- **WHEN** the signed gold Spec IR, Target Profile, and Test Bundle pass their existing Schema, reference, role/language, coverage, and canonical-byte checks
- **THEN** the calibration input root contains hash-bound frozen copies and an S4 metadata summary derived only from those copies

#### Scenario: A frozen input is invalid
- **WHEN** any selected input has an invalid Schema, invalid reference, unsupported target role/language combination, non-canonical Test Bundle, or mismatching declared hash
- **THEN** preparation fails before a lineage, prompt batch, or provider call is published

#### Scenario: Planned test code is absent
- **WHEN** every Test Bundle nodeid names a test asset that does not yet exist during M1
- **THEN** preparation succeeds using the declarative metadata and performs no collection or execution attempt

### Requirement: Planning index and architecture Delivery Constraints are deterministic shared artifacts
The system SHALL deterministically derive `planning_index.json` from the frozen Spec, Target Profile, Test Bundle metadata, and system-declared application-layer language/role rules, and SHALL derive complete architecture-stage Delivery Constraints only from the frozen Spec, Target Profile, and those system-declared rules. The planning index SHALL retain all type/message structural dependencies, every requirement's `id`, `level`, and `text`, target roles/language, REQ-to-test metadata, and build-variant metadata while omitting every `source_ref.quote`. Delivery Constraints SHALL contain resolved role support, six-pattern naming derivation, per-key resource limits, build variants, `layout_convention_id` and its canonical reference, separated advisory/hard convention data, and the mechanical-contract/template boundary needed by ArchitecturePlanner and the later Blueprint compiler; it SHALL contain no Test Bundle metadata, fixed or architecture-derived file-slot table, and SHALL NOT be a Delivery Blueprint. Both artifacts SHALL use project canonical JSON, perform no time/random/network/workspace/environment lookup, and be produced through the one implementation path shared by calibration and production S4. (Design 7.1.0: §5.6.5.2-§5.6.5.3, §6.4.1, §6.4.3, §6.4.8.1; D1.7/D1.11.)

#### Scenario: Identical frozen inputs are prepared twice
- **WHEN** preparation is repeated with byte-identical inputs and the same deterministic rule implementation
- **THEN** the planning index and Delivery Constraints bytes and SHA-256 values are identical

#### Scenario: Requirement source evidence is projected
- **WHEN** a Spec requirement contains a `source_ref.quote`
- **THEN** the planning index retains the requirement id, level, text, and structural relationships but contains no copy of the quote

#### Scenario: Supported C99 server target is prepared
- **WHEN** the frozen Target Profile selects the supported server role and C99 language rule
- **THEN** Delivery Constraints contains the mechanically derived naming values, four per-key resource defaults, build variants, selected layout convention, and mechanical contract boundary without a fixed file layout

#### Scenario: Unsupported role or language is selected
- **WHEN** the Target Profile requests a role/language combination absent from the built-in application-layer rules
- **THEN** preparation fails deterministically before planning or Blueprint compilation

#### Scenario: Planning context exceeds a model boundary
- **WHEN** the canonical ArchitecturePlanner input plus configured output reserve and safety margin exceeds the selected model's configured context/output boundary
- **THEN** that batch is rejected before provider I/O with `PLAN_CONTEXT_TOO_LARGE` and no normative requirement is silently removed

### Requirement: ArchitectureDraft is a closed production contract
The system SHALL provide one draft-2020-12 ArchitectureDraft Schema, one conforming minimal example, and canonical serialization. A draft SHALL contain only architecture decisions and their context references, explicit assumptions, modules with responsibilities/non-goals/file and contract boundaries, internal contracts with owner/readiness/provider/consumer information, and work-package skeletons with goals, allowed files, contract sets, dependencies, acceptance outcome, context references, and primary/supporting requirement responsibilities. It SHALL NOT contain final `T-###` ids, task instructions, task shards, input or Blueprint hashes, coverage, review, run state, Plan State, S5 file contents, or coder prompts. (Design: §5.2.1-§5.2.2, §6.4, §6.4.4, §6.4.8.1.)

#### Scenario: A complete architecture draft is serialized
- **WHEN** a draft satisfies the production ArchitectureDraft Schema
- **THEN** repeated canonical serialization produces identical bytes that validate against the same Schema

#### Scenario: A downstream-only field is returned
- **WHEN** an ArchitecturePlanner response includes a task id, task instructions, coverage, runtime status, input hash, Blueprint hash, or other forbidden downstream field
- **THEN** Schema validation rejects the response before semantic architecture validation

#### Scenario: The production contract is bound to ArchitecturePlanner
- **WHEN** an M1-4a trial invokes the registered ArchitecturePlanner role
- **THEN** the invocation uses this Schema and its conforming example through the existing M1-3 output-contract slot

### Requirement: ARCH_VALIDATE is the single production S4-G2 validator
The system SHALL expose one deterministic `ARCH_VALIDATE` result over a Schema-valid ArchitectureDraft, frozen planning index, Test Manifest metadata, and Delivery Constraints. It SHALL evaluate the complete S4-G2 contract under stable gates `arch_01` through `arch_10`: identifier/reference integrity; module boundaries; contract owner/readiness/interface conditions; unique provider and declared consumers; module contract projections; work-package membership and file partition; work-package contract projection; exact contract-derived dependencies and acyclic DAG; Delivery Constraints/file-slot closure; and requirement primary/supporting responsibility plus `gate=task` work-package readiness closure. The validator SHALL return every evaluable gate result and a canonically ordered issue list rather than stop at the first semantic failure. The calibration path and later production S4 SHALL call this same validator and SHALL NOT maintain a looser experimental validator. (Design: §5.2.1-§5.2.3, §6.4.4, §6.4.8.1.)

#### Scenario: A valid architecture is checked twice
- **WHEN** the same Schema-valid draft and parent artifacts are validated twice
- **THEN** both results contain passing `arch_01` through `arch_10` gates and byte-identical canonical validation evidence

#### Scenario: Multiple independent semantic defects exist
- **WHEN** a draft violates more than one evaluable S4-G2 condition
- **THEN** the result is `fail` and contains stable gate ids plus exact code/path/message evidence for every detected defect in canonical order

#### Scenario: A task-gated test has no work-package convergence point
- **WHEN** no work package ancestor closure contains all primary/supporting work packages for a `gate=task` test's requirements
- **THEN** `arch_10` fails with `ARCH_TEST_READINESS_UNCLOSED`

#### Scenario: An experiment attempts to substitute a validator
- **WHEN** a calibration caller proposes a separately configured or weakened semantic validator
- **THEN** the trial is rejected before model output can be counted in a lineage report

### Requirement: Lineage identity freezes every non-prompt comparison variable
The system SHALL derive `lineage_id` from frozen input references, planning-index and Delivery-Constraints construction, ArchitectureDraft Schema, patch Schema, serializer, patch application/locality behavior, path normalization, coupled-reference projection, `ARCH_VALIDATE`, the one configured logical model slot and its request configuration, metric definitions and two-stage invocation contract. Prompt source bytes and versions SHALL remain existing referenced version artifacts outside lineage identity. Any non-prompt component or configured slot change SHALL create a new lineage; changing either or both prompt templates between V0～V2 SHALL remain in the same lineage and record exact diffs. Historical evidence from another lineage SHALL NOT be aggregated. (Design: §0.1, §6.4.8.1～§6.4.8.2, §8.3, §9.2.)

#### Scenario: Prompt templates change
- **WHEN** a revision changes initial, repair or both while all non-prompt components remain equal
- **THEN** it stays in the lineage and records both stage diffs

#### Scenario: Configured model slot changes
- **WHEN** the logical slot, provider endpoint, route or request parameters change
- **THEN** a new lineage is required without any design-document change

### Requirement: Configured single-model trial execution is isolated and cache-independent
The active M1-4a2 protocol SHALL execute exactly one configured logical model slot. Its three trials SHALL use fresh history-free sessions and disabled cross-trial cache. The slot name SHALL be a safe configured identifier and SHALL NOT be constrained to a provider or model name. (Design: §6.4.8.1～§6.4.8.2, §8.3.)

#### Scenario: Single-slot batch starts
- **WHEN** active preflight resolves one configured calibration slot
- **THEN** exactly three isolated trial identities are declared under that slot before provider I/O

#### Scenario: Model implementation changes
- **WHEN** a different configured model is selected for a future experiment
- **THEN** the design and prompt remain unchanged while a new lineage records the new request configuration

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

### Requirement: Planning and calibration assets remain protocol neutral
The shared planning/calibration framework, ArchitectureDraft Schema/example, validators, and ArchitecturePlanner prompt source SHALL NOT contain protocol-name branches or embedded target-protocol message names, field names, REQ ids, paths, interface constants, ports, or topic/subscription semantics. The shared ArchitecturePlanner prompt SHALL additionally contain no model/provider-specific condition. Deterministic static checks SHALL scan these assets, and at least one non-MQTT application-layer fixture SHALL pass the same input builder, Delivery Constraints compiler, ArchitectureDraft Schema, `ARCH_VALIDATE`, lineage, and prompt-rendering path without editing shared sources. Concrete protocol identifiers in generated artifacts SHALL be traceable to the frozen fixture inputs or documented language/role rules. (Design: §6.4, §6.4.8.1, §8.8, D1.11.)

#### Scenario: Shared sources are scanned
- **WHEN** the protocol-neutrality gate scans the M1-4a1 framework, Schema/example, validator, and prompt source
- **THEN** it finds no prohibited embedded target-protocol constant or protocol-identity branch, and no model/provider branch in the shared prompt

#### Scenario: A non-MQTT fixture is prepared and validated
- **WHEN** the synthetic application-layer fixture uses a supported target role/language and supplies its own protocol facts
- **THEN** the same code path produces and validates its planning artifacts with zero MQTT-specific residue

### Requirement: Calibration evidence is not a formal run or a downstream planning result
The M1-4a calibration root SHALL NOT create or mutate formal `run.json`, S4 receipts, `plan/plan.json`, Delivery Blueprint, formal Report v2, or `nepa eval runs` inputs. Calibration ArchitectureDraft candidates SHALL NOT be consumable by S5/S6 or be represented as proof that complete S4 can publish. (Design: §6.4.8, §9.1.5, §10.2 M1-4a1.)

#### Scenario: A calibration batch completes
- **WHEN** all configured fake or live model trials and reports finish successfully
- **THEN** only calibration artifacts exist and no formal run, S4 seal, Plan, Blueprint, or report is published

#### Scenario: A caller requests downstream consumption
- **WHEN** a caller attempts to use a calibration ArchitectureDraft as a sealed Plan or S5/S6 input
- **THEN** the system rejects it because calibration evidence has no S4 commit point
