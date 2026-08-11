## Purpose

Provide a deterministic and auditable M0 baseline for the MQTT gold inputs, so later NePA stages consume only validated, scope-aligned, frozen asset metadata.

## ADDED Requirements

### Requirement: M0 artifact schemas have mutually valid minimal examples
The system SHALL provide JSON Schema draft 2020-12 definitions and one minimal valid example for the M0-defined Spec IR, segments, run, spec review, merge decisions, test summary, repair log, report, Test Bundle, and Target Profile artifacts. Each schema SHALL reject undeclared fields where the Chapter 5 contract requires a closed object.

#### Scenario: Schema examples are checked
- **WHEN** the M0 schema-example validation command is invoked
- **THEN** every supplied minimal example validates against its corresponding schema and every schema is available for its M0 validator

### Requirement: M0 Schema contracts reject explicitly forbidden states
The system SHALL audit all ten M0 Schemas against the Chapter 5 contracts and SHALL reject explicitly forbidden cross-field states and formats supported by direct design evidence. The audit SHALL not add speculative constraints that are not defined by the design.

#### Scenario: Run v3 terminal conditions are enforced
- **WHEN** a Run v3 artifact uses `planned_stop` with a non-zero `exit_code` or supplies an `outcome`
- **THEN** Schema validation fails

#### Scenario: Run timestamps use the documented UTC format
- **WHEN** a Run v3 artifact uses a non-UTC-ISO8601 value such as `created_at="not-a-time"`
- **THEN** Schema validation fails

#### Scenario: Segment coverage ratio stays within its documented domain
- **WHEN** a segments artifact uses a coverage ratio outside the documented ratio domain, including `coverage_ratio=2`
- **THEN** Schema validation fails

### Requirement: Gold Spec IR and Target Profile are deterministically validated
The system SHALL validate a Spec IR according to §5.1.6 structure, reference, evidence, and permitted-derived-relation rules. It SHALL validate a Target Profile as exactly `roles` and `language`, require the selected roles to be supported by the C99 rule, and, when a Spec IR is supplied, require the roles to be its subset. Unsupported target roles SHALL fail with `TARGET_ROLE_UNSUPPORTED`; unsupported language selections SHALL fail with `TARGET_LANGUAGE_UNSUPPORTED`.

#### Scenario: Historical dual-role target is rejected
- **WHEN** a Target Profile containing only `roles=["client","server"]` and the C99 language object is linted
- **THEN** validation fails with `TARGET_ROLE_UNSUPPORTED`

#### Scenario: Default target is accepted
- **WHEN** a Target Profile contains only `roles=["server"]` and `language={"name":"C","version":"C99"}` and the Spec IR declares `server`
- **THEN** target validation succeeds

### Requirement: Test Bundle validation and canonicalization are M0-only metadata operations
The system SHALL validate the Test Bundle schema, unique nodeids, layer-to-nodeid correspondence, requirement references, gates, and C99 build-variant references. It SHALL rewrite `gold_file/test_bundle.json` as the §5 canonical JSON byte sequence with no indentation or trailing newline. It SHALL not create, collect, or execute the nodeids named by that bundle.

#### Scenario: Canonical Test Bundle is accepted
- **WHEN** a scope-aligned Test Bundle is linted against its gold Spec IR and Target Profile
- **THEN** its bytes equal the project canonical JSON encoding and validation reports no errors

#### Scenario: Invalid test metadata is rejected
- **WHEN** a Test Bundle contains an unknown requirement, duplicate nodeid, mismatched layer path, invalid gate, or unavailable build variant
- **THEN** linting fails and identifies the invalid metadata

### Requirement: Test Bundle lint enforces canonical bytes and spec coverage
The system SHALL compare the Test Bundle input's raw bytes with `canonical_json_bytes(json.loads(raw_bytes))` and SHALL reject non-canonical input. When `--spec` is supplied, the command SHALL additionally require every `MUST`/`MUST NOT` requirement to be referenced by at least one case whose gate is `task` or `s7_only`; `s5` cases SHALL NOT count as normative coverage. The check SHALL remain declarative and SHALL not collect or execute tests.

#### Scenario: Non-canonical Test Bundle bytes are rejected
- **WHEN** a semantically valid Test Bundle is supplied with non-canonical whitespace or key ordering
- **THEN** linting fails with the existing structured validation report convention and identifies the canonical-byte mismatch

#### Scenario: Spec-bound Test Bundle coverage is closed
- **WHEN** `nepa lint test-bundle <bundle> --spec <spec>` is invoked and a MUST or MUST NOT requirement is absent from all `task` and `s7_only` cases
- **THEN** linting fails with the deterministic uncovered-requirement error

#### Scenario: S5-only references do not satisfy normative coverage
- **WHEN** a requirement is referenced only by a case with `gate=s5`
- **THEN** spec-bound Test Bundle lint reports that requirement as uncovered

### Requirement: M0 lint CLI uses the documented validation exit-code contract
The M0 lint commands SHALL return exit code `0` for valid input, `20` for controlled input, Schema, metadata, canonical-byte, or coverage validation failure, and `1` only for a NePA internal error. The commands SHALL preserve their structured JSON validation reports.

#### Scenario: Controlled validation failure returns 20
- **WHEN** an M0 lint command receives invalid input that produces a structured validation error
- **THEN** the process exits with code `20` and prints the structured error report

#### Scenario: Valid lint returns 0
- **WHEN** an M0 lint command validates a correct M0 artifact
- **THEN** the process exits with code `0`

### Requirement: Gold scope and input freezes require owner approval
The system SHALL record the §7.1 MQTT-min scope and a default-input freeze record containing the date, confirmer identity, and SHA-256 for all three gold files. The scope and default-input DoD gates SHALL remain incomplete until a designated responsible person signs them. The three file hashes SHALL be SHA-256 values over their on-disk raw bytes after Test Bundle canonicalization.

#### Scenario: Unsigned freeze is not accepted
- **WHEN** the required responsible-person signature is absent from the scope or default-input freeze record
- **THEN** the corresponding D0.5 or D0.6 gate remains pending and no automated actor treats it as complete

### Requirement: Raw-byte preservation is limited to caller source assets
The system SHALL preserve the caller-provided raw bytes of `gold_file/specIR.json` and `gold_file/target.json` during M0 validation and freeze hashing. It SHALL canonicalize only `gold_file/test_bundle.json`; canonical copies of the other two assets are permitted only later in run-local frozen inputs.

#### Scenario: Source-asset hashes use original bytes
- **WHEN** the default gold inputs are frozen after Test Bundle canonicalization
- **THEN** the Spec IR and Target Profile hashes are calculated from their unchanged caller-provided bytes and the Test Bundle hash is calculated from its canonical on-disk bytes

### Requirement: Scope conflicts block gold acceptance without changing the design
The system SHALL not accept a gold Spec IR or Test Bundle that contains facts or coverage outside the approved §7.1 M0 subset. The M0 record SHALL identify the conflicting input and the responsible owner action; it SHALL not expand scope or modify the design document to accommodate that input.

#### Scenario: Excluded gold facts are found
- **WHEN** the gold Spec IR contains an excluded feature such as Will-message, username/password authentication, persistent sessions, QoS 1/2, retain, wildcard subscription behavior, or a non-M0 transport feature
- **THEN** M0-3/M0-5 acceptance is blocked pending owner reconciliation of the gold inputs to the approved scope

### Requirement: Sandbox image supplies the declared M0 tool baseline
The system SHALL build the M0 sandbox image and record its immutable digest. The image SHALL provide gcc, make, Python with pytest, mosquitto with its clients, paho-mqtt, and the sanitizer runtime required by the built-in C99 rules; default sandbox execution SHALL be network-disabled.

#### Scenario: Image capabilities are recorded
- **WHEN** the M0 sandbox image is built
- **THEN** its digest and evidence of the required C99 tool availability are recorded without executing protocol behavior tests
