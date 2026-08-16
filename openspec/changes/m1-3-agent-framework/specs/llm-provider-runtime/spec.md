## ADDED Requirements

### Requirement: Agent template provenance is durable
The LLM provider runtime SHALL accept a caller-supplied `prompt_template_sha256` in `LLMCallContext.trace_fields` for Agent-originated calls. `LLMClient.complete` SHALL validate it as a lowercase 64-character hexadecimal SHA-256 value before cache lookup or provider I/O and SHALL raise the existing `LLMRequestError` on invalid input. Success traces SHALL admit the field through the existing optional-field path. Failure traces SHALL copy only `prompt_template_sha256` from context and SHALL NOT begin admitting other pre-existing optional context fields. The runtime SHALL persist a valid value unchanged in the same `llm_call` trace record as the effective prompt hash. The raw-template hash SHALL remain distinct from the effective rendered-prompt hash and SHALL NOT change cache identity or non-Agent call behavior.

#### Scenario: An Agent call supplies a valid template hash
- **WHEN** an Agent invocation supplies the SHA-256 of the raw template bytes
- **THEN** the LLM runtime writes that exact value to `prompt_template_sha256` in the corresponding durable trace record

#### Scenario: An Agent call supplies an invalid template hash
- **WHEN** `prompt_template_sha256` is not a lowercase 64-character hexadecimal value
- **THEN** `LLMClient.complete` raises `LLMRequestError` before cache lookup, appending an `llm_call` record, or calling a provider

#### Scenario: Effective prompt and template hashes are recorded
- **WHEN** a template is rendered with invocation-specific inputs and sent to a provider
- **THEN** the trace record contains both the raw-template SHA-256 and the existing effective prompt hash as separate fields

#### Scenario: A non-Agent call omits template provenance
- **WHEN** an existing non-Agent caller does not supply `prompt_template_sha256`
- **THEN** its validation, cache behavior, provider call, and trace behavior remain unchanged

#### Scenario: An Agent provider call fails
- **WHEN** a provider failure is traced with valid `prompt_template_sha256` and any pre-existing optional context fields
- **THEN** the failure trace contains `prompt_template_sha256` but continues to omit the other optional context fields as it did before M1-3
