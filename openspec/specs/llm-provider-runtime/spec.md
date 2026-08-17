# llm-provider-runtime Specification

## Purpose

Provide one provider-neutral, budget-aware, and auditable completion boundary for NePA's configured Claude, Qwen, and DeepSeek models before agent and stage-specific behavior is introduced.

## Requirements

### Requirement: Typed provider-neutral completion contract
The LLM runtime SHALL accept a role, system text, user text, optional JSON Schema, requested temperature, and positive output-token limit, and SHALL return text, an optional validated object, input/output token counts, USD cost, returned model identity, cache status, parameter-support states, and provider metadata. Provider-specific wire formats SHALL not escape this boundary. (Design: §8.4 unified interface.)

#### Scenario: Unstructured completion succeeds
- **WHEN** a valid request without a JSON Schema is sent to a configured provider and the provider returns a successful response
- **THEN** the runtime returns the response text with `parsed` unset and the normalized usage, model, cache, parameter-support, and provider metadata fields populated

#### Scenario: Invalid request is rejected before I/O
- **WHEN** a request has an invalid token limit, sampling value, provider binding, or model binding
- **THEN** the runtime returns a typed request/configuration error before any external request or durable cache entry is made

### Requirement: Configured provider routing and credential isolation
The runtime SHALL support the built-in `openai_compat` and `anthropic` provider kinds, resolve only the selected provider/model from the sealed configuration, and read the selected provider's API key from its configured environment-variable name at call time. It MUST NOT read shell configuration files or persist credential values in configuration snapshots, prompts, outputs, cache entries, metadata, errors, or traces. (Design: §8.3; §8.4 item 1.)

#### Scenario: OpenAI-compatible target is selected
- **WHEN** a request targets the configured Qwen or DeepSeek provider
- **THEN** only that provider's OpenAI-compatible adapter, endpoint, model, and environment-key name are used

#### Scenario: Credential is unavailable
- **WHEN** the selected provider's configured environment variable is absent
- **THEN** the call fails with a typed configuration error before HTTP I/O and the environment-variable value is not inferred from any other source

#### Scenario: Credential never enters evidence
- **WHEN** a provider call succeeds or fails with a credential present
- **THEN** no durable prompt, output, cache, trace, or error text contains the credential value

### Requirement: Exact Anthropic request destination
The `anthropic` adapter SHALL treat `providers.anthropic.base_url` as the complete request URL and SHALL use it byte-for-byte as configured, without replacing its host or appending `/v1/messages`, `/chat/completions`, or any other path. (Design: §8.3; §8.4 item 1; §10.2 M1-2.)

#### Scenario: Configured Claude gateway is called exactly
- **WHEN** the Anthropic provider is configured with `https://www.sotamodel.net/v1/chat/completions`
- **THEN** the outgoing request target is exactly `https://www.sotamodel.net/v1/chat/completions`

#### Scenario: Alternate complete URL is preserved
- **WHEN** the Anthropic provider has an explicitly overridden complete request URL
- **THEN** that exact URL is used without provider-name or model-name inference

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

### Requirement: Bounded transport retry
The runtime SHALL retry network failures, HTTP 429 responses, and HTTP 5xx responses with exponential backoff no more than three times after the initial attempt. It SHALL NOT treat those transport retries as stage/model repair attempts, and SHALL NOT retry other HTTP 4xx responses. (Design: §8.4 item 3; §4.7.)

#### Scenario: Transient server failure recovers
- **WHEN** a request receives a retryable failure and a later attempt succeeds within three retries
- **THEN** the logical call succeeds, records the transport-attempt count, and consumes no stage repair attempt

#### Scenario: Retry limit is exhausted
- **WHEN** the initial request and all three permitted retries fail with retryable errors
- **THEN** the runtime stops after four total HTTP attempts and raises a typed transport failure

#### Scenario: Non-retryable client error occurs
- **WHEN** the provider returns an HTTP 4xx response other than 429
- **THEN** the runtime raises a typed provider failure without an infrastructure retry

### Requirement: Deterministic response cache
The runtime SHALL derive each cache key as SHA-256 over a canonical representation of provider identity, model identity, all provider-affecting request parameters, and the complete system/user prompt content. It SHALL publish only successful logical responses as immutable cache entries. A hit SHALL return `cached: true`, perform no provider request, and add zero incremental provider cost. (Design: §8.4 item 5; §4.8.)

#### Scenario: Identical request hits cache
- **WHEN** two calls use identical provider, model, parameters, optional Schema, system text, and user text with caching enabled
- **THEN** the second call reuses the immutable cached response, makes no HTTP request, and reports zero incremental cost

#### Scenario: Provider-affecting input changes
- **WHEN** any provider identity, model, temperature, token limit, Schema, system text, or user text changes
- **THEN** the cache key changes and the prior entry is not reused

#### Scenario: Failed result is not cached
- **WHEN** transport, provider, parsing, or final Schema validation fails
- **THEN** no successful cache entry is published for that logical request

#### Scenario: Capability probe bypasses cache
- **WHEN** a sampling-parameter capability probe is run
- **THEN** it neither reads nor writes the response cache

### Requirement: Price-based usage and M1-1 budget integration
The runtime SHALL calculate USD cost from configured per-model input/output prices per million tokens and SHALL require a price entry for the selected canonical provider/model before external I/O. Before every external provider request it SHALL invoke the M1-1 external-call admission boundary, and after every successful provider response it SHALL persist actual token/cost usage before validation, repair, cache publication, or returning control. Cache hits SHALL add no provider usage. (Design: §8.4 item 6; §4.7.)

#### Scenario: Usage cost is calculated
- **WHEN** a provider response reports input and output token counts and the selected model has configured prices
- **THEN** the runtime calculates cost from those counts and rates and returns and records that cost without estimating from prompt length

#### Scenario: Model price is missing
- **WHEN** the selected canonical provider/model has no configured price entry
- **THEN** the call fails before HTTP I/O rather than recording an invented or zero cost

#### Scenario: Repair usage is fully charged
- **WHEN** structured-output validation triggers one repair call
- **THEN** usage from both successful provider responses is persisted and the logical response reports their aggregate tokens and cost

#### Scenario: Budget is exhausted after a response
- **WHEN** recording a returned provider response crosses the global budget
- **THEN** the response usage is durable, evidence for the completed provider response is retained, no repair or later provider request is started, and the M1-1 budget exception is re-raised to the stage controller

### Requirement: Honest parameter capability evidence
For every possibly ignored sampling parameter, the runtime SHALL use only `reported_applied`, `reported_ignored`, or `unknown`. It SHALL emit either reported state only when the provider response or provider-specific capability endpoint explicitly supplies that evidence; request acceptance or output statistics MUST NOT upgrade `unknown`. (Design: §8.4 item 4; §5.5.)

#### Scenario: Ordinary acceptance has no explicit evidence
- **WHEN** a provider accepts a requested temperature but does not explicitly report whether it was applied
- **THEN** `parameter_support.temperature` remains `unknown`

#### Scenario: Provider explicitly reports support state
- **WHEN** provider-owned response metadata explicitly states that a requested parameter was applied or ignored
- **THEN** the matching `reported_applied` or `reported_ignored` value is recorded with evidence kind `provider_report`

#### Scenario: Statistical differences are observed
- **WHEN** repeated outputs differ or remain identical under different requested values without provider-owned evidence
- **THEN** the parameter-support state remains `unknown`

### Requirement: Cache-disabled capability probe record
The runtime SHALL expose a capability probe that sends a minimal unstructured request with caching disabled and records the provider/model, parameter and requested value, request acceptance, returned model, tokens, cost, latency, error, support state, and evidence kind. A failed request SHALL leave the support state `unknown`; acceptance alone SHALL use `request_accepted_only`. (Design: §8.4 item 4.)

#### Scenario: Probe request is accepted without provider report
- **WHEN** a probe succeeds but the provider supplies no explicit parameter evidence
- **THEN** the probe records `accepted: true`, evidence kind `request_accepted_only`, and support state `unknown`

#### Scenario: Probe request fails
- **WHEN** the minimal probe request fails
- **THEN** the probe records the error, `accepted: false`, and support state `unknown` without inferring a capability

### Requirement: Durable LLM call evidence
Every logical completion, including cache hits and terminal failures after an external response, SHALL durably publish the complete effective prompt and all returned output text before appending one canonical row to `trace/llm_calls.ndjson`. The row SHALL contain the §5.5 run/stage/role/tier/model context, requested parameters, parameter-support states, prompt hash/path, output path, tokens, cost, latency, and validation state; task/attempt and later S4 fields SHALL be preserved when supplied by the caller. A committed row MUST NOT reference missing prompt/output artifacts or a prompt whose bytes differ from `prompt_sha256`. (Design: §5.5; §8.4 item 6.)

#### Scenario: Successful call is traced
- **WHEN** a logical provider completion succeeds
- **THEN** its prompt and output texts are durably addressable and exactly one trace row references them with `validation: pass` and the actual model/usage/latency metadata

#### Scenario: Repair call is traced as one logical completion
- **WHEN** a first invalid response is corrected by the single repair request
- **THEN** the trace evidence preserves both provider prompts and outputs, aggregates their usage, records transport/repair attempt metadata, and uses `validation: repaired`

#### Scenario: Final structured failure is traced
- **WHEN** the repair response remains invalid
- **THEN** the raw outputs and validation errors remain durable and the trace row uses `validation: fail` before the typed failure is returned upward

#### Scenario: Evidence publication is interrupted
- **WHEN** a process stops after immutable prompt/output publication but before the NDJSON append
- **THEN** no trace row claims an incomplete call; orphan immutable evidence may remain but cannot be treated as a committed trace fact

### Requirement: LLM failures remain subordinate to stage control
The LLM runtime SHALL return typed request, configuration, transport, provider, and structured-output failures and SHALL not mutate stage status, consume stage repair counters, select a fallback model, request S9, or create a controlled-exit reason. Those decisions remain with the deterministic agent/stage/orchestrator layers. (Design: §4.7; §8.4; §10.2 M1-2/M1-3.)

#### Scenario: Structured output fails twice
- **WHEN** both the original and repair outputs fail validation
- **THEN** the LLM runtime returns its typed failure without directly changing Run stage or termination state

#### Scenario: Provider request fails permanently
- **WHEN** a provider or transport failure cannot be recovered within this layer's retry rules
- **THEN** no alternate provider/model is selected and the typed failure is returned to the caller

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
