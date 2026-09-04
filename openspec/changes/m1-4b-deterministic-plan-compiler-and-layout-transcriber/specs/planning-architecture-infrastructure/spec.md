## MODIFIED Requirements

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
