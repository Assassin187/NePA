# planning-architecture-infrastructure Specification

## Purpose

Define the deterministic, protocol-neutral ArchitecturePlanner input, output-validation, lineage, and isolated trial evidence boundary that M1-4a2 and M1-4a3 must reuse before production S4 planning can be qualified.

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
The system SHALL deterministically derive `planning_index.json` and the M1-4a1 Delivery Constraints projection from the frozen inputs and system-declared application-layer language/role rules. The planning index SHALL retain all type/message structural dependencies, every requirement's `id`, `level`, and `text`, the target roles/language, REQ-to-test metadata, and build-variant metadata while omitting every `source_ref.quote`. The Delivery Constraints projection SHALL contain the resolved target support, derived naming and resource constraints, build variants, expanded `s5_frozen`/`s6_owned` file slots, and required internal-interface slots needed by ArchitecturePlanner and `ARCH_VALIDATE`; it SHALL NOT be a Delivery Blueprint. Both artifacts SHALL use the project canonical JSON encoding, perform no time/random/network/workspace/environment lookup, and be produced by the same implementation entry points later extended by production S4 and M1-4b. (Design: §5.6.5.2-§5.6.5.3, §6.4.1, §6.4.3, §6.4.8.1.)

#### Scenario: Identical frozen inputs are prepared twice
- **WHEN** preparation is repeated with byte-identical inputs and the same deterministic rule implementation
- **THEN** the planning index and Delivery Constraints bytes and SHA-256 values are identical

#### Scenario: Requirement source evidence is projected
- **WHEN** a Spec requirement contains a `source_ref.quote`
- **THEN** the planning index retains the requirement id, level, text, and structural relationships but contains no copy of the quote

#### Scenario: Delivery paths are expanded
- **WHEN** an architecture-relevant file rule uses the declared `per_message` expansion
- **THEN** the Delivery Constraints file slots are derived by the documented message selection, identifier normalization, and stable ordering rules without a protocol-name branch

#### Scenario: Planning context exceeds a model boundary
- **WHEN** the canonical ArchitecturePlanner input plus configured output reserve and safety margin exceeds any selected calibration model's configured context/output boundary
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
The system SHALL derive a `lineage_id` from a canonical lineage manifest that binds the frozen input references, planning-index and Delivery-Constraints construction, ArchitectureDraft Schema, canonical serializer, `ARCH_VALIDATE`, exactly the configured Claude/Qwen/DeepSeek candidate model request configurations, and calibration statistical definitions. The shared ArchitecturePlanner prompt hash SHALL be recorded by each prompt version but SHALL NOT be part of `lineage_id`, because it is the only variable M1-4a2 may change within a lineage. Any bound component change SHALL produce a different lineage id; evidence from different lineages SHALL NOT be aggregated or compared as one prompt-only experiment. (Design: §6.4.8.1-§6.4.8.3, §8.3, §9.2.)

#### Scenario: Only the shared prompt changes
- **WHEN** a later prompt version changes prompt bytes while every lineage-bound component remains identical
- **THEN** it has a new prompt hash/version under the same lineage id

#### Scenario: A controlled component changes
- **WHEN** an input, Schema, validator, serializer, input constructor, Delivery Constraints compiler, model request configuration, or metric definition changes
- **THEN** a new lineage id is required and prior trials cannot enter the new lineage report

#### Scenario: A prompt label is reused for different bytes
- **WHEN** a prompt-version label already exists under a lineage with a different prompt SHA-256
- **THEN** publication fails as an artifact conflict rather than overwriting or mixing evidence

### Requirement: Three-model trial execution is isolated and cache-independent
The calibration driver SHALL dispatch one batch for each of the exactly configured Claude, Qwen, and DeepSeek candidates for a prompt version. All three batches SHALL consume identical frozen planning-index, Delivery-Constraints, ArchitectureDraft Schema, validator, and raw prompt hashes. Each model SHALL have an independent directory, client/session, cache root, trace stream, call sequence, and trial identity. Every trial and every semantic-repair call SHALL use a fresh no-history ArchitecturePlanner invocation with cross-trial cache reads/writes disabled; no candidate SHALL read, vote on, or combine another candidate's response. (Design: §4.6, §6.4.8.1-§6.4.8.3, §8.3-§8.4, §9.2.)

#### Scenario: A three-model batch starts
- **WHEN** a valid lineage, prompt version, and trial declaration are dispatched
- **THEN** all three model batches record the same input/schema/validator/prompt hashes and distinct model roots, sessions, traces, caches, and trial ids

#### Scenario: Two trials have identical request material
- **WHEN** two independent trials happen to render byte-identical requests
- **THEN** the second trial still performs its own logical model completion and does not consume a cache entry from the first

#### Scenario: One model produces an architecture
- **WHEN** one candidate model returns a Schema-valid or semantically valid draft
- **THEN** neither other candidate receives that draft or any derived vote, hint, or state

#### Scenario: Infrastructure retry is exhausted without a model response
- **WHEN** a trial exhausts the M1-2 transport retry policy without any model response
- **THEN** that model batch is marked `infrastructure-invalid`, no failed trial is silently replaced, and the prompt version cannot be treated as a complete comparable batch until rerun

### Requirement: Semantic repair is explicit, fresh, and bounded by the declared protocol
The M1-4a trial engine SHALL validate the first Schema-legal candidate immediately with `ARCH_VALIDATE`. If a declared development or qualification protocol permits a semantic repair, each repair SHALL be a new no-history ArchitecturePlanner invocation containing the unchanged frozen inputs, the prior Schema-valid candidate, and only the exact canonical `ARCH_VALIDATE` failure list. Schema-format repair SHALL remain solely inside the M1-2 logical completion and SHALL be counted separately. The infrastructure SHALL support recording zero, one, or two semantic repairs but SHALL NOT choose M1-4a2 prompt versions, M1-4a3 qualification disposition, or production repair budgets. (Design: §6.4.8.2-§6.4.8.3, §8.4.)

#### Scenario: The initial candidate passes
- **WHEN** the first Schema-legal candidate passes all architecture gates
- **THEN** the trial stops semantic repair and records success at `p0`

#### Scenario: One semantic repair is allowed
- **WHEN** the initial candidate fails and the declared protocol permits one semantic repair
- **THEN** the next call has no conversation history and contains only the unchanged base inputs, prior candidate, and exact ordered validator failures as repair context

#### Scenario: Schema repair occurs
- **WHEN** M1-2 repairs malformed structured output within one logical completion
- **THEN** the trial records the format-repair consumption separately and does not decrement a semantic-repair allowance

#### Scenario: No semantic repair is declared
- **WHEN** the initial candidate fails `ARCH_VALIDATE` and the batch declaration permits zero semantic repairs
- **THEN** the trial records a semantic failure without issuing another ArchitecturePlanner call

### Requirement: Trial artifacts and calibration reports are hash-bound and recomputable
Calibration evidence SHALL be stored under `runs/_calibration/s4-architecture/<lineage_id>/<prompt_version>/<model_id>/`. Each model root SHALL contain canonical `batch.json`, `trials/trial_NNN/request_ref.json`, `response_ref.json`, and `validation.json`, a dedicated trace/cache area, and `calibration_report.json`. Request/response index entries SHALL bind every initial, format-repair, and semantic-repair evidence item by path and SHA-256. Recomputing a report from only the lineage, batch, trial, validation, and trace evidence SHALL reproduce the canonical report bytes and SHALL reject missing, mutated, duplicate, cross-lineage, cross-prompt, or cross-model evidence. (Design: §5.5, §6.4.8.1, §9.1.5.)

#### Scenario: A complete model batch is recomputed
- **WHEN** all declared trial records and their referenced evidence are present and valid
- **THEN** report recomputation produces a byte-identical `calibration_report.json`

#### Scenario: Referenced evidence changes
- **WHEN** a request, response, validation, trace, or parent artifact no longer matches its recorded SHA-256
- **THEN** recomputation fails and publishes no replacement report over the existing evidence

#### Scenario: A partial batch is inspected
- **WHEN** fewer than the declared number of trials are durably complete
- **THEN** batch status remains incomplete and no headline rate is represented as a completed N-trial result

### Requirement: Calibration metrics preserve fixed denominators and failure evidence
Each complete per-model report SHALL use the declared trial count `N` as the denominator for `schema_first_pass_rate`, `schema_after_format_repair_rate`, `arch_raw_first_pass_rate`, `arch_semantic_first_pass_rate`, `p0`, `p1`, and `p2` when the corresponding repair depth was declared. `arch_raw_first_pass_rate` SHALL count raw responses that need neither format nor semantic repair; `arch_semantic_first_pass_rate` and `p0` SHALL count the first Schema-legal candidate passing without semantic repair. `p1` and `p2` SHALL be cumulative success within at most one or two semantic repairs. A second Schema failure or absent semantic candidate SHALL count as failure in the fixed denominator. Reports SHALL also include unconditional k/N results for every `arch_01`-`arch_10` gate, gate-failure co-occurrence, repair gain, per-trial and aggregate calls/tokens/cost/latency, finish reasons, truncation, format repairs, semantic repairs, full returned model/version identity, and parameter-support states. Undeclared metric depths SHALL be `null` with a machine-readable reason rather than zero. (Design: §6.4.8.2-§6.4.8.3, §9.1.5, §9.2.)

#### Scenario: A response fails Schema twice
- **WHEN** both the original structured response and M1-2 format-repair response fail the ArchitectureDraft Schema
- **THEN** that trial remains in N and contributes failure to all architecture success rates

#### Scenario: A repair succeeds
- **WHEN** a trial first passes all gates after exactly one semantic repair
- **THEN** it is excluded from `p0`, included in `p1` and `p2` when both are declared, and contributes the exact per-gate repair gain

#### Scenario: A batch declares only one semantic repair
- **WHEN** the completed batch protocol has a maximum semantic-repair depth of one
- **THEN** `p2` is `null` with an undeclared-depth reason and is not reported as zero

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
