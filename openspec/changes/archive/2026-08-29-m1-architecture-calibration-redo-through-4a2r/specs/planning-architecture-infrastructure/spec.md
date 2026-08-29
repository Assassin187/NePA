## MODIFIED Requirements

### Requirement: Planning index and architecture Delivery Constraints are deterministic shared artifacts
The system SHALL deterministically derive `planning_index.json` and the M1-4a1 Delivery Constraints projection from the frozen inputs, the selected protocol-neutral layout-convention asset, and system-declared application-layer language/role rules. The planning index SHALL retain all type/message structural dependencies, every requirement's `id`, `level`, and `text`, the target roles/language, REQ-to-test metadata, and build-variant metadata while omitting every `source_ref.quote`. Delivery Constraints SHALL contain resolved target support, derived naming and resource constraints, build variants, `layout_convention_id`, the convention's canonical SHA-256, its separated `advisory`/`hard` projections, mechanical-contract input bounds, and template roots; it SHALL NOT predeclare a fixed project file tree, expanded `file_slots`, or a Delivery Blueprint. Both artifacts SHALL use project canonical JSON, perform no time/random/network/workspace/environment lookup, and use the same production entry points later consumed by S4/S5. (Design: `system_design.md` §5.6.5.2～§5.6.5.3, §6.4.1, §6.4.3, §6.4.8.1; `pipeline_design_s4_s9.md` §5.1～§5.2.3; D1.0/D1.11.)

#### Scenario: Identical frozen inputs and convention are prepared twice
- **WHEN** preparation is repeated with byte-identical inputs, the same selected layout-convention bytes, and the same deterministic rule implementation
- **THEN** the planning index and Delivery Constraints bytes and SHA-256 values are identical

#### Scenario: Requirement source evidence is projected
- **WHEN** a Spec requirement contains a `source_ref.quote`
- **THEN** the planning index retains the requirement id, level, text, and structural relationships but contains no copy of the quote

#### Scenario: Layout convention bytes change
- **WHEN** the mechanically selected layout-convention id is unchanged but its canonical bytes no longer match the recorded SHA-256
- **THEN** preparation fails before prompt publication or Provider I/O and no lineage may reuse the stale constraint artifact

#### Scenario: Planning context exceeds a model boundary
- **WHEN** the canonical ArchitecturePlanner input plus configured output reserve and safety margin exceeds any of the Qwen/Claude/DeepSeek slot's configured context/output boundaries
- **THEN** that version attempt is rejected before Provider I/O with `PLAN_CONTEXT_TOO_LARGE` and no normative requirement or convention rule is silently removed

### Requirement: ArchitectureDraft is a closed production contract
The system SHALL provide one draft-2020-12 ArchitectureDraft Schema, one conforming minimal example, and canonical serialization. A draft SHALL contain architecture decisions and context references, explicit assumptions, modules with responsibilities/non-goals/file and contract boundaries, internal contracts with owner/readiness/provider/consumer information, work-package skeletons with goals, allowed files, contract sets, dependencies, acceptance outcome and requirement responsibilities, plus one complete `architecture.layout` declaration containing roots, file slots and the build graph required by the current authoritative layout contract. A file slot SHALL use either a static `path` with no expansion or one `path_pattern` containing exactly one `{message_id}` or `{type_id}` bound respectively to `expand_over=messages` or `expand_over=types`; mixed, repeated, unknown or mismatched placeholder/domain combinations SHALL be rejected. Module `owns_files`, work-package `allowed_files`, contract `interface_files`, layout file ownership/class and build-graph references SHALL be mutually closed. It SHALL NOT contain final `T-###` ids, task instructions/shards, input or Blueprint hashes, coverage, review, runtime state, S5 file contents, or coder prompts. (Design: `system_design.md` §5.2.1～§5.2.2, §5.6.5.3, §6.4, §6.4.4, §6.4.8.1; `pipeline_design_s4_s9.md` §5.2.1～§5.2.2; D1.0.)

#### Scenario: A complete free-layout draft is serialized
- **WHEN** a draft contains a complete layout declaration and satisfies the production ArchitectureDraft Schema
- **THEN** repeated canonical serialization produces identical bytes that validate against the same Schema

#### Scenario: Layout is missing or incomplete
- **WHEN** an ArchitecturePlanner response omits `architecture.layout`, leaves a required layout field absent, or declares a file/build reference outside the closed Schema
- **THEN** Schema validation rejects the response before semantic architecture validation

#### Scenario: A message or type slot declares its resolved expansion pair
- **WHEN** a layout file uses `{message_id}` with `expand_over=messages` or `{type_id}` with `expand_over=types`
- **THEN** the Schema accepts the pair and the deterministic downstream projection is respectively `per_message` or `per_type`

#### Scenario: A placeholder and expansion domain are mixed
- **WHEN** a layout file uses `{message_id}` with `types`, `{type_id}` with `messages`, both placeholders, a repeated placeholder, or another `{...}` token
- **THEN** Schema or semantic layout validation rejects it before calibration evidence can count

#### Scenario: A downstream-only field is returned
- **WHEN** an ArchitecturePlanner response includes a task id, task instructions, coverage, runtime status, input hash, Blueprint hash, or other forbidden downstream field
- **THEN** Schema validation rejects the response before semantic architecture validation

#### Scenario: The production contract is bound to ArchitecturePlanner
- **WHEN** an M1-4a trial invokes the registered ArchitecturePlanner role
- **THEN** the invocation uses this free-layout Schema and its conforming example through the existing M1-3 output-contract slot

### Requirement: ARCH_VALIDATE is the single production S4-G2 validator
The system SHALL expose one deterministic `ARCH_VALIDATE` result over a Schema-valid ArchitectureDraft, frozen planning index, Test Manifest metadata, and Delivery Constraints. It SHALL evaluate all stable gates `arch_01` through `arch_15`: the existing id/reference, module, contract, projection, file-partition, dependency/DAG, requirement responsibility and test-readiness conditions in `arch_01`～`arch_10`, followed by `arch_11 LAYOUT_SAFETY`, `arch_12 LAYOUT_CLASS`, `arch_13 BUILD_GRAPH`, `arch_14 LAYERING`, and `arch_15 PATH_NEUTRALITY`. It SHALL return all evaluable gate results and a canonically ordered exact issue list without short-circuiting. Calibration and later production S4 SHALL call this same validator and SHALL NOT maintain a looser experimental validator. (Design: `system_design.md` §6.4.4, §6.4.8.1; `pipeline_design_s4_s9.md` §5.2.4; D1.0/D1.11.)

#### Scenario: A valid architecture is checked twice
- **WHEN** the same Schema-valid free-layout draft and parent artifacts are validated twice
- **THEN** both results contain passing `arch_01` through `arch_15` gates and byte-identical canonical validation evidence

#### Scenario: Multiple independent semantic and layout defects exist
- **WHEN** a draft violates more than one evaluable S4-G2 condition across existing and new layout gates
- **THEN** the result is `fail` and contains stable gate ids plus exact code/path/message evidence for every detected defect in canonical order

#### Scenario: An unsafe path or an unlisted path/purpose token is declared
- **WHEN** a layout path is unsafe, or any `path`/`path_pattern` segment or `purpose` token falls outside the general-responsibility whitelist union the Spec-derived identifier set
- **THEN** the corresponding `arch_11` or `arch_15` gate fails with recomputable evidence naming the exact offending segment or token

#### Scenario: An experiment attempts to substitute a validator
- **WHEN** a calibration caller proposes a separately configured, ten-gate, fixed-layout, or otherwise weakened semantic validator
- **THEN** the trial is rejected before model output can be counted in a lineage report

### Requirement: Lineage identity freezes every non-prompt comparison variable
The system SHALL derive a `lineage_id` from a canonical manifest binding frozen input references, planning/constraint construction, layout-convention id and hash, ArchitectureDraft Schema/example, canonical serializer, fifteen-gate `ARCH_VALIDATE`, the exact logical model-slot set, each slot's provider endpoint/route/request parameters/context bound, and calibration statistical definitions. The shared prompt hash SHALL be recorded by each version but excluded from `lineage_id`. Configured or provider-returned model identifier strings SHALL be recorded as observations but excluded from lineage equality. Any other bound component change SHALL produce a new lineage; evidence across lineages SHALL NOT be aggregated as one prompt-only experiment. (Design: `system_design.md` §6.4.8.1～§6.4.8.3, §9.2 rule 1, revisions 4.0.0～5.1.0; D1.0.)

#### Scenario: Only the shared prompt changes
- **WHEN** a later prompt version changes prompt bytes while every lineage-bound component remains identical
- **THEN** it has a new prompt hash/version under the same lineage id

#### Scenario: A model identifier string changes
- **WHEN** a logical slot resolves to a different configured alias or the Provider returns a different model/version string while its endpoint, route and request parameters remain equivalent
- **THEN** the trial records the observed string without creating a new lineage, splitting the batch, or invalidating existing trials

#### Scenario: A controlled component changes
- **WHEN** an input, layout convention, Schema, validator, serializer, input constructor, constraint compiler, logical slot set, provider endpoint, route, request parameter, context bound, or metric definition changes
- **THEN** a new lineage id is required and prior trials cannot enter the new lineage report

#### Scenario: Historical fixed-layout evidence is offered
- **WHEN** a caller proposes a trial collected without `architecture.layout`, `arch_11`～`arch_15`, the convention asset, or the required three logical slots
- **THEN** it is retained only as historical provenance and rejected from every new denominator, fallback tuple, selection, or handoff

### Requirement: Three-model trial execution is isolated and cache-independent
The calibration driver SHALL dispatch one batch for each stable logical slot `qwen`, `claude`, and `deepseek`. All three batches SHALL consume identical frozen planning-index, Delivery Constraints, layout-convention, ArchitectureDraft Schema, validator, and raw-prompt hashes. Each slot SHALL have an independent directory, client/session, cache root, trace stream, call sequence, and trial identity. Every initial or semantic-repair call SHALL use a fresh no-history invocation with cross-trial cache disabled; no slot SHALL read, vote on, or combine another slot's response. Directory identity SHALL use the stable logical slot, never a returned model string. (Design: `system_design.md` §4.6, §6.4.8.1～§6.4.8.3, §8.3～§8.4, §9.2.)

#### Scenario: A three-model batch starts
- **WHEN** a valid lineage, prompt version, and trial declaration are dispatched
- **THEN** all three slot batches record the same controlled hashes and distinct roots, sessions, traces, caches, and trial ids under `qwen/`, `claude/`, and `deepseek/`

#### Scenario: Two trials have identical request material
- **WHEN** two independent trials happen to render byte-identical requests
- **THEN** the second trial still performs its own logical completion and does not consume the first trial's cache entry

#### Scenario: A returned model string drifts inside a batch
- **WHEN** one slot observes more than one returned model/version string across its trials
- **THEN** the batch remains eligible and reports the complete string set plus each value's call share

#### Scenario: Infrastructure retry is exhausted without a model response
- **WHEN** a slot exhausts the M1-2 transport retry policy without any model response
- **THEN** that slot batch is `infrastructure-invalid`, no failed trial is silently replaced, and the prompt version cannot become a valid development/recovery candidate until a coherent rerun completes

### Requirement: Trial artifacts and calibration reports are hash-bound and recomputable
Calibration evidence SHALL be stored under `runs/_calibration/s4-architecture/<lineage_id>/<prompt_version>/<model_slot>/`. Each slot root SHALL contain canonical batch metadata, declared trial request/response/validation evidence, its own trace/cache area, and a per-slot calibration report. References SHALL bind every initial, format-repair and semantic-repair item by path/SHA-256. A report recomputed only from lineage, batch, trial, validation and trace leaves SHALL reproduce canonical bytes and SHALL reject missing, mutated, duplicate, cross-lineage, cross-prompt, cross-slot, or old-contract evidence. Reports SHALL separately expose requested and returned model strings without using them as artifact-directory identity. (Design: `system_design.md` §5.5, §6.4.8.1, §6.4.8.3, §9.2; D1.0.)

#### Scenario: A complete slot batch is recomputed
- **WHEN** all declared trial records and their referenced evidence are present and valid
- **THEN** report recomputation produces a byte-identical per-slot calibration report

#### Scenario: Referenced evidence changes
- **WHEN** a request, response, validation, trace, layout-convention parent, or other bound artifact no longer matches its recorded SHA-256
- **THEN** recomputation fails and publishes no replacement report over the existing evidence

#### Scenario: A returned alias is used as a directory key
- **WHEN** an artifact path attempts to replace the declared logical slot with a configured or provider-returned model identifier
- **THEN** publication/recomputation rejects the artifact as a slot-identity violation

#### Scenario: A partial batch is inspected
- **WHEN** fewer than the declared number of trials are durably complete
- **THEN** batch status remains incomplete and no headline rate is represented as a completed N-trial result

### Requirement: Calibration metrics preserve fixed denominators and failure evidence
Each complete per-slot report SHALL use declared `N` as denominator for Schema rates, first-pass architecture rates, `p0`, `p1`, and `p2` when the corresponding depth is declared. A second Schema failure or absent semantic candidate SHALL remain a failure in N. Reports SHALL include unconditional k/N results for every `arch_01`～`arch_15` gate, gate co-occurrence, repair gain, calls/tokens/cost/latency, finish reasons, truncation, format/semantic repairs, requested/returned model strings and parameter-support states. Undeclared metric depths SHALL be `null` with a machine-readable reason. Model-string drift SHALL be summarized, not treated as a missing/failed trial. (Design: `system_design.md` §6.4.8.2～§6.4.8.3, §9.2; D1.0.)

#### Scenario: A response fails Schema twice
- **WHEN** both the original structured response and the one format-repair response fail the ArchitectureDraft Schema
- **THEN** that trial remains in N and contributes failure to all architecture success rates

#### Scenario: A repair succeeds on the full validator
- **WHEN** a trial first passes all fifteen gates after exactly one semantic repair
- **THEN** it is excluded from `p0`, included in `p1` and `p2` when declared, and contributes exact per-gate repair gain

#### Scenario: A batch declares only one semantic repair
- **WHEN** the completed batch protocol has maximum semantic-repair depth one
- **THEN** `p2` is `null` with an undeclared-depth reason and is not represented as zero

#### Scenario: A slot observes multiple model strings
- **WHEN** a completed slot has calls reporting more than one model/version string
- **THEN** the report remains valid and lists the string set and call proportions in its reproducibility evidence

### Requirement: Planning and calibration assets remain protocol neutral
The shared planning/calibration framework, layout-convention assets, ArchitectureDraft Schema/example, validators, and ArchitecturePlanner prompt source SHALL NOT contain protocol-name branches or embedded target-protocol message names, fields, REQ ids, paths, interface constants, ports, or topic/subscription semantics. The shared prompt SHALL additionally contain no model/provider-specific condition. Static checks SHALL scan these assets, and MQTT plus at least one non-MQTT application-layer fixture SHALL pass the same constraint, free-layout Schema, fifteen-gate validator, lineage and rendering path without editing shared sources. Concrete paths and identifiers SHALL be attributable to frozen inputs, derived naming, or the selected protocol-neutral convention. (Design: `system_design.md` §6.4, §6.4.8.1, §8.8, D1.11; `pipeline_design_s4_s9.md` §5.2.3～§5.2.4.)

#### Scenario: Shared sources are scanned
- **WHEN** neutrality scanning covers the framework, convention asset, Schema/example, validator and prompt source
- **THEN** it finds no prohibited embedded target-protocol constant/branch and no model/provider branch in the shared prompt

#### Scenario: MQTT and non-MQTT fixtures are prepared and validated
- **WHEN** both fixtures use supported target role/language declarations and supply their own protocol facts
- **THEN** the same code path produces complete free-layout drafts that pass all fifteen gates with zero cross-protocol residue

## ADDED Requirements

### Requirement: The resolved design baseline is an implementation entry control
The system SHALL implement the owner-resolved `system_design.md` 5.2.0 layout rule: `path_pattern` allows only `{message_id}` and `{type_id}`, bound respectively to `expand_over=messages` and `expand_over=types`, while a static `path` has no expansion; the later Blueprint projection SHALL be respectively `per_message`, `per_type`, or `none`. It SHALL further implement the owner-resolved `system_design.md` 5.3.0 gate criteria, which adopt the authorized subdocument's §5.2.4 wording: `arch_13` SHALL require three-segment reference closure, exactly one artifact per `link_source` slot, unique artifact output paths, the exact `entry_point` count required by `delivery_form`, and an acyclic build graph; `arch_15` SHALL require every `path`/`path_pattern` segment and every `purpose` token to belong to the general-responsibility whitelist union the Spec-derived identifier set, with that whitelist implemented once on the validator side, shared with the D1.11 naming-source audit, bound to the lineage, and absent from the convention asset's `advisory` and `hard` sections. `kind`/`producer` derivation SHALL remain unimplemented and SHALL NOT be invented locally. Implementation preflight SHALL record the approved main-design and subdocument path/SHA-256 and verify that both still agree before production Schema/validator work or Provider I/O. OpenSpec text or current code SHALL NOT substitute for that baseline. (Design: `system_design.md` §0.1, §5.6.5.3, §6.4.4, §10.2, §10.8, §11.3, §12.5; `pipeline_design_s4_s9.md` §5.2.2～§5.2.4, §14.)

#### Scenario: The synchronized baseline is verified
- **WHEN** implementation preflight observes the approved 5.3.0 main-design bytes and the 1.1.0 authorized subdocument exposing the same placeholder/domain pairs and the same `arch_13`/`arch_15` criteria
- **THEN** the former document-consistency blocks are closed and layout Schema/validator implementation may proceed

#### Scenario: A gate criterion is taken from the superseded main-document wording
- **WHEN** an implementation validates `arch_13` against "one artifact per app slot" or `arch_15` as a protocol-token blacklist over paths only
- **THEN** it is rejected as a superseded criterion, because both gates resolve to the subdocument's §5.2.4 wording and the difference changes measured `p0`/`p1`

#### Scenario: The two design documents conflict
- **WHEN** implementation preflight detects incompatible allowed placeholders/expansion domains or another shared Schema rule between the main and authorized subdocument
- **THEN** it stops before production Schema edits or Provider I/O and reports the exact conflicting clauses
