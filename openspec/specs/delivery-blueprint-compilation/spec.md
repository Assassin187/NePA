# delivery-blueprint-compilation Specification

## Purpose

Define the deterministic compilation contract that converts an accepted free-layout architecture and final task graph into the complete protocol-neutral Delivery Blueprint consumed by later stages.

## Requirements

### Requirement: Every declared layout file is transcribed exactly once
The Blueprint compiler SHALL produce one `file_rules[]` entry for every `architecture.layout.files[]` entry and no others. It SHALL copy the stable slot id, purpose, path or path pattern, class-derived mutability, and expansion derived from `path`/`path_pattern` plus `expand_over`; it SHALL preserve the validated contract and task-owner relationships required downstream. Missing, duplicated, discarded, or invented rules SHALL be a controlled compilation failure. (Design 7.1.0: §5.6.5.3; pipeline design 1.2.0 §5.2.2/§5.2.5; M1-4b2.)

#### Scenario: Valid free layout is transcribed
- **WHEN** a validated architecture contains N distinct layout file slots
- **THEN** the Blueprint contains N corresponding file rules with a reversible one-to-one slot mapping

#### Scenario: A transcriber would omit or invent a slot
- **WHEN** the proposed file-rule set is not a bijection with the layout file slots
- **THEN** compilation fails and publishes no Blueprint

### Requirement: File kind and producer follow the approved complete derivation table
For each layout file the compiler SHALL derive `kind` and `producer` only from the tuple `(render_rule, class, contract_id presence, build_role)` using the eight legal rows in pipeline design 1.2.0 §5.2.2: contract header to `header/layout_template`; task-owned contract stub to `header/s6_task`; task-owned link stub to `source/s6_task`; task-owned entry stub to `app/s6_task`; build file to `build/layout_template`; documentation to `documentation/layout_template`; contract mechanical output to `header/mechanical_spec`; and link mechanical output to `source/mechanical_spec`. Every other tuple SHALL fail without filename, suffix, module-name, or protocol inference. (Design 7.1.0: §5.6.5.3; pipeline design 1.2.0 §5.2.2; M1-4b2.)

#### Scenario: Each approved row is compiled
- **WHEN** one valid layout slot is supplied for each of the eight approved tuples
- **THEN** every produced `kind` and `producer` equals the corresponding table row

#### Scenario: A table-external tuple is supplied
- **WHEN** a slot uses any unlisted combination, including mechanical entry point, unbound header, linked build/documentation file, or contract-bound linked source stub
- **THEN** compilation fails with no inferred fallback

### Requirement: Path expansion is closed, safe, and deterministic
The compiler SHALL map a literal `path` with null `expand_over` to `expansion=none`; a `path_pattern` with `expand_over=messages` to `per_message`; and a `path_pattern` with `expand_over=types` to `per_type`. Only one matching `{message_id}` or `{type_id}` placeholder SHALL be accepted for its declared domain. Expansion SHALL use the design-defined Spec selection, identifier normalization, and UTF-8 byte ordering, and all concrete paths SHALL be unique, workspace-relative, and non-escaping. Any unmatched domain, empty required domain, normalization collision, duplicate path, or other placeholder SHALL fail. (Design 7.1.0: §5.6.5.2-§5.6.5.3; D1.7/D1.11.)

#### Scenario: Message pattern is expanded
- **WHEN** a valid `per_message` slot applies to multiple target-role messages
- **THEN** the compiler emits the normalized concrete paths in source message-id UTF-8 byte order

#### Scenario: Expansion is ambiguous or unsafe
- **WHEN** expansion has a wrong placeholder, empty domain, collision, duplicate path, absolute path, or traversal outside the workspace
- **THEN** compilation fails and emits no partial Blueprint

### Requirement: Layout build graph is transcribed into the required three segments
The compiler SHALL transcribe the validated layout build graph into `deliverables[]`, `build_artifacts[]`, and `link_source_sets[]`. Every deliverable SHALL be referenced by at least one artifact; every artifact SHALL reference exactly one deliverable, one unique output path, and one link source set; every link source set SHALL contain a non-empty set of existing link-source file-rule ids. The transcription SHALL preserve entry-point and link-source closure and SHALL NOT infer links from filenames or suffixes. (Design 7.1.0: §5.6.5.3, §6.4.1; pipeline design 1.2.0 §5.2.5; M1-4b2.)

#### Scenario: Valid server build graph is compiled
- **WHEN** the architecture declares the valid server delivery form with one entry point and a closed artifact graph
- **THEN** the three Blueprint segments cross-reference exactly and every artifact output path is unique

#### Scenario: A graph reference is missing
- **WHEN** an artifact, deliverable, source set, entry slot, or link-source slot cannot be resolved exactly
- **THEN** compilation fails instead of guessing or dropping the reference

### Requirement: Complete Blueprint fields are deterministic and closed
The compiler SHALL emit the complete Blueprint naming, resource limits, deliverables, build artifacts, link source sets, file rules, layout templates, and mechanical generation contracts required by design. Naming SHALL follow the six fixed patterns, resource keys SHALL merge independently by the documented minimum rule, and each mechanical file rule SHALL belong to exactly one allowed mechanical contract. The semantic projection SHALL be exactly Delivery Constraints plus accepted architecture, final work packages, and final tasks; identical canonical inputs SHALL yield byte-identical canonical Blueprint output. The compiler SHALL perform no time, randomness, network, environment, or workspace lookup and no protocol-name branch. (Design 7.1.0: §5.6.5.2-§5.6.5.3, §6.4.1; D1.7/D1.11; M1-4b.)

#### Scenario: Compilation is replayed
- **WHEN** byte-equivalent semantic inputs are compiled twice
- **THEN** both complete Blueprint objects and canonical bytes are identical

#### Scenario: Mechanical ownership is incomplete
- **WHEN** a mechanical file rule has zero or multiple producing mechanical contracts, or a contract consumes a disallowed input kind
- **THEN** compilation fails without publishing a partial Blueprint
