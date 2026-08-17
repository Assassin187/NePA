## MODIFIED Requirements

### Requirement: Provider-independent structured output
For a request with a JSON Schema, the runtime SHALL require a schema valid under JSON Schema draft 2020-12 before cache lookup or provider I/O. It SHALL prefer a provider-native JSON/schema mode when that adapter has explicit support, otherwise SHALL embed the Schema in the prompt, extract the first JSON value from the returned text, and validate it with the draft-2020-12 dialect. Native and fallback paths SHALL expose the same validated result contract and SHALL retain the existing single bounded format-repair behavior.

#### Scenario: Native structured output validates
- **WHEN** the selected adapter explicitly supports native structured output and returns a value conforming to the requested draft-2020-12 Schema
- **THEN** the runtime returns that value in `parsed` and marks validation `pass`

#### Scenario: Fallback extracts the first JSON value
- **WHEN** native structured output is unavailable and the response contains surrounding prose followed by a value conforming to the requested draft-2020-12 Schema
- **THEN** the runtime extracts the first complete JSON value, validates it with the draft-2020-12 dialect, and returns it in `parsed` with validation `pass`

#### Scenario: Invalid schema is rejected before runtime side effects
- **WHEN** the request carries a JSON Schema that is invalid under the draft-2020-12 metaschema
- **THEN** the runtime returns a typed request error before cache lookup, provider I/O, or durable call evidence

#### Scenario: One repair succeeds
- **WHEN** the first structured response cannot be parsed or fails draft-2020-12 Schema validation
- **THEN** the runtime makes at most one repair call containing the validation error list, returns the repaired Schema-valid object, and marks the logical completion `repaired`

#### Scenario: Repair remains invalid
- **WHEN** the single repair response also cannot be parsed or fails draft-2020-12 Schema validation
- **THEN** the runtime records validation `fail` and raises a typed structured-output failure with the validation errors for the caller to route upward
