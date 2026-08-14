## Context

See `proposal.md` for motivation and `specs/llm-provider-runtime/spec.md` for the behavior contract. M1-1 is implemented and archived: `nepa/config.py` owns the closed, secret-free provider/model snapshot; `RunStore` owns run-local filesystem mutation; and `Orchestrator.admit_external_call` plus `record_external_usage` own global budget persistence. The repository has no `nepa/llm/` package and does not yet depend on `httpx`.

The authoritative implementation constraints are §5.5, §8.1-§8.4, and §10.2 M1-2. M1-2 is an L2/L4 transport and evidence layer. It must not acquire the M1-3 responsibility for rendering role prompts or the stage responsibility for retry counters, fallback models, termination reasons, or S9 routing.

Two details are not fully enumerated by the design document and are handled conservatively:

- The configured Anthropic URL is a complete `/v1/chat/completions` gateway URL. The adapter will use the corresponding chat-completions message/response envelope while remaining a separate adapter for its exact URL and provider-specific metadata handling; live gateway behavior is not inferred beyond that configured contract.
- §8.4 requires model prices in configuration but supplies no price values. The implementation will add the price-table shape with no invented production rates; tests provide fixture rates and a real call requires an explicit price for its canonical provider/model.

## Goals / Non-Goals

**Goals:**

- Provide one typed logical-completion path shared by later agents, independent of provider wire shape.
- Keep all external-call admission and returned usage connected to the existing M1-1 budget commits.
- Make structured validation, one repair, transport retry, caching, pricing, capability evidence, and trace publication deterministic and testable with injected clocks/sleep/HTTP transport.
- Preserve complete prompt/output evidence without credentials and ensure a trace row never points to unpublished data.
- Keep current provider capability claims honest: absent explicit provider evidence, requested parameters remain `unknown`.

**Non-Goals:**

- No role registry, role-to-tier resolution, prompt templates, context slicing, or agent loop.
- No provider/model fallback, escalation, calibration batch, statistical capability inference, or production-model selection.
- No stage error-code mapping, Run transition, S9 routing, or modification of stage repair budgets.
- No live endpoint, credential, price, latency, or output-quality acceptance test.
- No S4-specific interpretation of compiler metadata; telemetry only preserves optional caller-supplied fields for later consumers.

## Decisions

### 1. Separate the logical client from provider wire adapters

`nepa/llm/client.py` will own the public Pydantic request/response types, typed error hierarchy, provider protocol, logical completion sequence, structured-output strategy, transport-retry policy, and capability-probe entry point. A selected adapter owns only credential lookup, request encoding, one HTTP attempt, response decoding, explicit provider evidence, and transport metadata.

The client receives an already resolved provider/model/tier plus call context (`stage`, optional task/attempt, and optional trace extensions). It does not resolve a role from `config.roles`; M1-3 owns that policy. The provider registry is keyed by the configured provider name and validates that its adapter kind matches `ProviderConfig.kind`.

This keeps one structured-output/cache/evidence implementation across both provider kinds. Duplicating validation and repair inside adapters was rejected because it would produce provider-dependent agent behavior and evidence.

### 2. Keep the two endpoint contracts explicit

`openai_compat.py` appends the standard `chat/completions` resource to its configured base prefix with exactly one separator and uses the chat-completions request/response envelope. `anthropic.py` uses its configured `base_url` directly as the final request URL, with no URL join, default host, or model-based rewrite. The current configured gateway path is itself `/v1/chat/completions`, so the dedicated adapter uses that envelope while retaining a separate location for gateway-specific headers and explicit capability metadata.

Both adapters receive an injected `httpx.Client`/transport for tests and resolve the API key through an injected environment lookup immediately before request construction. Sanitized errors include status and provider name, not authorization headers or response data known to contain the credential. Missing keys fail before HTTP.

Using an Anthropic SDK was rejected: it adds an unnecessary dependency and would encourage the forbidden official `/v1/messages` routing. Treating the Anthropic `base_url` as a prefix was rejected by §8.3.

### 3. Add a canonical model-price table without invented rates

`nepa/config.py` will add a closed `pricing.models` mapping keyed by canonical `<provider-name>/<model-name>`. Each entry contains non-negative `input_usd_per_million_tokens` and `output_usd_per_million_tokens`. `_DEFAULTS` and `configs/default.yaml` include an empty mapping so existing configuration loads and snapshots remain explicit; live callers must supply entries for the models they invoke.

Before credential lookup or budget admission, the client resolves the exact price entry. The telemetry pricing function calculates:

```text
cost_usd = tokens_in / 1_000_000 * input_rate
         + tokens_out / 1_000_000 * output_rate
```

Provider-reported token counts are authoritative; prompt-length estimates are not used. Float values match the existing Run/LLM contracts, with tests using approximate comparisons. A missing entry raises a typed configuration error before HTTP instead of silently treating a paid model as free.

Adding guessed current prices to the repository was rejected because the authoritative document supplies none and prices are externally mutable. Provider-reported billing was rejected as the primary path because §8.4 assigns conversion to the configured price table.

### 4. Charge each successful provider response at the M1-1 boundary

The client calls `Orchestrator.admit_external_call(store)` immediately before every actual HTTP attempt that could reach a provider, including the structured repair request but excluding cache hits. Retry sleep occurs only after the prior failed attempt and before the next admission check.

After an HTTP success is decoded and priced, the client calls `record_external_usage` before parsing/Schema validation, repair admission, cache publication, or returning. If that call raises `BudgetExhausted`, the client publishes the raw completed-response evidence with validation `fail` and re-raises; it never starts a repair. Ordinary network/HTTP failures have no returned token usage to charge.

The final `LLMResponse` aggregates token and cost from the initial and optional repair responses. The client will not also return a `StageResult.usage` for the same calls; later stages use the immediate per-response accounting path to avoid double charging.

Charging only once at stage completion was rejected because a crash or post-call budget overrun could lose usage. Charging after structured validation was rejected because invalid model output still incurs provider cost.

### 5. Treat transport retry and schema repair as different bounded loops

The client transport wrapper invokes the selected adapter for the initial HTTP attempt plus at most three exponential-backoff retries for connection/timeouts, 429, and 5xx. It runs external-call admission immediately before each adapter attempt. Delay calculation and sleeping are injected; tests assert attempt count without wall-clock waits. Each adapter performs exactly one HTTP attempt and returns a typed retryable or non-retryable result; other 4xx responses and malformed successful responses fail without infrastructure retry.

The logical client independently permits exactly one structured repair request. The repair prompt contains the requested Schema, the invalid output, and the deterministic `jsonschema` error list. The repair request itself may use the same bounded transport retry, but it cannot recursively trigger another schema repair.

This preserves §4.7's distinction: transport retry counters are provider metadata, structured repair is local P8 behavior, and neither touches a stage's S2/S4/S6 repair counters.

### 6. Use explicit native-structured capability, otherwise deterministic fallback

The provider protocol exposes whether the selected adapter has explicit native JSON/schema support. The client uses native mode only for an explicit supported state; `unknown` follows the portable prompt fallback rather than experimenting during a production call. Provider rejection of a native request is a provider failure, not an undocumented compatibility fallback.

Fallback rendering is a fixed, protocol-neutral instruction containing the canonical JSON Schema. Extraction uses a deterministic scanner/decoder to locate the first complete JSON value; it does not greedily slice from the first `{` to the last `}`. Validation errors are normalized and sorted before entering the one repair prompt and typed failure.

Assuming every OpenAI-compatible service supports one `response_format` dialect was rejected because §8.4 explicitly requires a fallback for unsupported providers. Repeated free-form repair was rejected by the one-repair bound.

### 7. Cache only final successful logical responses

`nepa/llm/cache.py` computes SHA-256 over canonical JSON containing provider name, provider kind, model, temperature, max tokens, optional Schema, and the exact original system/user strings. Role/stage/task metadata is excluded because it does not affect provider output; any actual prompt content is included. Cache entries contain the successful normalized response and the original validation status, but never credentials.

Entries live under `cache/llm/<sha256>.json` and are published immutably through `RunStore`. M1-2 adds the smallest public hash-verifying read method needed for cache lookup instead of using `RunStore._confined` or opening a second filesystem path. Byte-identical replay is idempotent; a conflicting entry is an internal storage error. Failed calls are not cached.

On a hit, the client returns the stored text/parsed/model/parameter metadata with `cached: true` and `cost_usd: 0`; no external admission or usage charge occurs. Telemetry still records the logical call and its cache status. Capability probes forcibly bypass both cache lookup and publication.

A process-global memory cache was rejected because it cannot support resume/replay. Caching raw first responses was rejected because it could replay invalid structured output and bypass the required repair semantics.

### 8. Represent capability claims as evidence, not inference

Adapters initialize every requested possibly ignored parameter, currently temperature, to `unknown`. They may change it to `reported_applied` or `reported_ignored` only when provider-owned response/capability metadata explicitly names the parameter and state. Ordinary chat-completions responses from the current adapters therefore remain `unknown` unless such metadata is present.

The client capability probe constructs a minimal unstructured request, disables the cache, and returns a typed probe record with provider/model/parameter/requested value, accepted flag, returned model, usage/cost, latency, sanitized error, state, and evidence kind. Success without explicit evidence uses `request_accepted_only`; explicit metadata uses `provider_report`; failure remains `unknown` with no positive evidence. The same telemetry sink persists the probe record alongside call evidence without claiming calibration qualification.

Inferring support from HTTP 2xx, deterministic-looking output, or statistical differences was rejected directly by §8.4. Adding model-name allowlists was rejected because they would silently replace provider evidence with repository assumptions.

### 9. Commit trace evidence data-first and one row per logical completion

`nepa/llm/telemetry.py` owns call sequencing, pricing helpers, immutable prompt/output transcripts, and canonical NDJSON rows. A logical completion covers the original request plus its optional one repair request and any transport attempts; this makes §5.5's `validation: pass|repaired|fail` and aggregate call economics unambiguous. Attempt details remain in the transcript/provider metadata rather than becoming partial logical rows.

For each logical completion telemetry:

1. Allocates the next numeric call id by considering committed rows and existing numeric prompt/output artifacts, so an orphan cannot cause path reuse after a crash.
2. Publishes immutable prompt and output transcripts under `trace/prompts/` and `trace/outputs/` through `RunStore`. Transcripts preserve every effective provider prompt/output and sanitized attempt metadata.
3. Computes `prompt_sha256` from the exact original effective-prompt bytes and verifies all artifact references.
4. Appends one canonical line to `trace/llm_calls.ndjson` only after referenced artifacts are durable.

The row contains the fixed §5.5 fields, cache status and transport/repair counts, plus optional caller metadata. The telemetry layer allowlists the documented optional S4 field names but does not interpret them. A crash before the append can leave unreferenced immutable files and a sequence gap; a committed row never points forward to missing data. Since v1 execution is serial under the M1-1 controller lock, no second trace lock or WAL is introduced.

Appending the row before artifacts was rejected because a crash could create false evidence. Overwriting one mutable trace JSON document was rejected because it weakens append-only audit and crash behavior.

### 10. Return typed failures and preserve ownership boundaries

The public error hierarchy distinguishes invalid request/configuration, transport exhaustion, non-retryable provider response, response decoding, structured-output failure, and evidence/cache storage failure. It includes sanitized provider/status/validation context needed by a later agent or stage but no Run termination code.

M1-3's agent caller will decide whether a typed structured-output error becomes a role-level failure; stage controllers will decide whether it maps to a documented controlled reason; M1-1 remains the only owner of stage and S9 transitions. The LLM layer never selects a different provider/model or consumes escalation budgets.

Mapping all failures directly to `ControlledStageFailure` was rejected because the same provider primitive is used by multiple stages and calibration contexts with different failure semantics.

## Interfaces and Ownership

The following shapes define the intended boundary; exact helper names may change without changing ownership or the spec:

```python
# nepa/llm/client.py
class LLMRequest(BaseModel): ...
class LLMResponse(BaseModel): ...
class LLMCallContext(BaseModel): ...
class CapabilityProbeResult(BaseModel): ...

class Provider(Protocol):
    def complete(self, request: LLMRequest, *, model: str, native_schema: bool) -> LLMResponse: ...

class LLMClient:
    def complete(
        self,
        request: LLMRequest,
        *,
        provider_name: str,
        model: str,
        context: LLMCallContext,
        use_cache: bool = True,
    ) -> LLMResponse: ...

    def probe_parameter(...) -> CapabilityProbeResult: ...

# nepa/run_store.py (narrow M1-2 extensions)
class RunStore:
    def read_verified_bytes(self, relative_path: str, expected_sha256: str | None = None) -> bytes: ...
    def append_llm_trace(self, event: Mapping[str, object]) -> None: ...
```

`LLMClient` owns logical completion and aggregate results; provider adapters own wire translation; `LLMCache` owns key/value semantics; telemetry owns pricing and evidence; `RunStore` remains the sole filesystem mutator; `Orchestrator` remains the sole global-budget and lifecycle owner.

## Persistent and Failure Sequence

```text
complete:
  validate request/target/price
  -> cache lookup
     -> hit: publish trace evidence -> return cached response at zero incremental cost
  -> admit external call
  -> provider HTTP with <= 3 transport retries
  -> decode tokens/model and calculate price
  -> persist returned usage through M1-1
  -> validate structured output
     -> invalid and budget remains: admit one repair call, charge its response, revalidate
  -> publish successful cache entry
  -> publish prompt/output transcripts
  -> append one logical trace row
  -> return aggregate normalized response

provider response crosses budget:
  usage commit raises BudgetExhausted
  -> publish completed-response evidence with validation=fail
  -> re-raise without repair/cache publication

terminal structured failure:
  charge all returned usage
  -> publish transcripts and validation=fail row
  -> raise StructuredOutputError upward
```

## Validation Strategy

- Use `httpx.MockTransport`, injected environment lookup, clocks, monotonic timer, sleeper, and M1-1 fake budget hooks; no live endpoint or paid request.
- Contract-test both adapters for request encoding, response normalization, status handling, secret redaction, and parameter state. Assert the Anthropic URL equals the configured complete URL exactly.
- Table-test structured native/fallback parsing, first-JSON extraction, normalized validation errors, one repair only, aggregate usage, and failure evidence.
- Test initial plus three transport retries for network/429/5xx, immediate failure for other 4xx, no stage-counter mutation, and pre-attempt budget admission.
- Test cache-key sensitivity for every provider-affecting input, immutable conflict, cache-disabled probes, cache-hit zero cost, and no failed-response entry.
- Test price calculation and missing-price preflight with fixture prices; production price values remain deliberately unverified.
- Test capability records for accepted-only, explicit provider report through an injected provider fixture, failed request, and no statistical upgrade.
- Fault-inject before/after prompt/output publication and trace append to verify no committed row references incomplete evidence; verify trace required fields and hashes.
- Run focused M1-2 tests, the full repository suite, existing M0 lint commands, signed gold hash verification, `git diff --check`, and strict OpenSpec validation.

## Risks / Trade-offs

- [The configured Anthropic gateway's live headers or response envelope may differ from the `/v1/chat/completions` contract implied by the authoritative URL] → Keep encoding isolated in `anthropic.py`, validate exact routing with mock transport, make no live-compatibility claim, and stop for a design clarification rather than silently switching to `/v1/messages`.
- [No production price values exist in the authoritative workspace] → Ship only the closed price schema and deterministic calculator; require explicit per-model entries before I/O and do not claim live-call readiness until rates are supplied through configuration.
- [An append-only trace can leave orphan transcript files after a crash] → Allocate sequence numbers across both committed rows and existing artifacts; treat only NDJSON rows as committed facts and never infer a call from an orphan file.
- [OpenAI-compatible providers differ in native structured-output extensions] → Use native mode only with explicit adapter capability and keep the portable schema-in-prompt path as the default unknown-capability behavior.
- [Budget exhaustion is raised immediately after usage commit] → Catch only long enough to persist evidence for the completed response, then re-raise without starting ordinary follow-up work.
- [Trace metadata is not governed by a standalone JSON Schema in §5.5] → Validate a closed M1-2 Pydantic record for fixed fields and a small allowlist of documented optional fields before canonical append.

## Migration Plan

1. Extend the sealed configuration with the empty price-table shape and add `httpx`, preserving all existing defaults and lint commands.
2. Add typed client/provider primitives and mock-tested adapters without wiring any stage or CLI.
3. Add structured output, retry, pricing/budget hooks, cache, probes, and telemetry in that order, keeping all writes through `RunStore`.
4. Run the focused and full validation sets and inspect the complete diff for M1-3+ behavior.

Rollback reverts only the M1-2 package, its narrow configuration/store extensions, dependency lock update, and tests. Existing M1-1 runs without LLM cache/trace rows remain readable because the new configuration field has a default and no Run/Report Schema semantics are changed.
