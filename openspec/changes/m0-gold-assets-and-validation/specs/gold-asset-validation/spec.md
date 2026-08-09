## Purpose

Provide a deterministic and auditable M0 baseline for the MQTT gold inputs, so later NePA stages consume only validated, scope-aligned, frozen asset metadata.

## ADDED Requirements

### Requirement: M0 artifact schemas have mutually valid minimal examples
The system SHALL provide JSON Schema draft 2020-12 definitions and one minimal valid example for the M0-defined Spec IR, segments, run, spec review, merge decisions, test summary, repair log, report, Test Bundle, and Target Profile artifacts. Each schema SHALL reject undeclared fields where the Chapter 5 contract requires a closed object.

#### Scenario: Schema examples are checked
- **WHEN** the M0 schema-example validation command is invoked
- **THEN** every supplied minimal example validates against its corresponding schema and every schema is available for its M0 validator

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
