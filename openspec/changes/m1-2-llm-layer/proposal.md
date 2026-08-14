## Why

M1-1 now provides the durable run, budget, and termination boundary, but M1-3 and later stages still have no provider-neutral way to call the configured Claude, Qwen, and DeepSeek models or to produce auditable usage evidence. M1-2 is therefore the next independently verifiable M1 work item and must establish that shared LLM boundary before any agent or planning behavior is added.

## What Changes

- **Milestone/work item:** implement only M1-2 from §10.2, grounded in §5.5 and §8.2-§8.4, while integrating with the M1-1 configuration, run-store, and budget APIs.
- Add the typed `LLMRequest`, `LLMResponse`, provider protocol, and client orchestration used by later agent roles without defining those roles or prompts.
- Add the two built-in provider adapters required for the configured models: OpenAI-compatible endpoints for Qwen/DeepSeek and the Claude-specific adapter. The Anthropic adapter must send to the configured `providers.anthropic.base_url` as the complete request URL and must never substitute or append another path.
- Add the common structured-output path: native JSON/schema mode when supported, prompt-schema fallback with first-JSON extraction and `jsonschema` validation otherwise, and at most one schema-repair call before returning a typed failure upward.
- Add bounded exponential backoff for network, rate-limit, and server failures; infrastructure retries remain distinct from stage retry budgets while every actual provider attempt is observable and its returned usage is charged.
- Add deterministic response caching keyed by provider, model, all request parameters, and full prompt content; cache hits report `cached: true` and zero incremental cost.
- Add model-price configuration and telemetry that records full prompt/output artifacts, one metadata record per provider call in `trace/llm_calls.ndjson`, token counts, calculated cost, latency, validation state, requested parameters, and honest parameter-support evidence.
- Add cache-disabled capability probes that record request acceptance and provider evidence without inferring that an accepted parameter was applied.
- Add mock-transport tests for both adapters, exact Anthropic URL routing, structured-output fallback/repair, retry limits, cache identity, pricing, budget integration, capability evidence, and durable trace content. No acceptance test will make a paid or live provider request.
- **Out of scope:** M1-3 agent registration, prompt templates, role/tier routing policy, stage-specific repair loops, S4 compiler metadata semantics, M1-4 calibration batches or model selection, CLI commands, live credential verification, and any S5-S9 behavior.

## Capabilities

### New Capabilities

- `llm-provider-runtime`: Provider-neutral completion, structured-output validation and repair, bounded transport retry, deterministic cache, capability evidence, pricing, budget usage, and auditable LLM traces for the configured Claude/Qwen/DeepSeek endpoints.

### Modified Capabilities

- None.

## Impact

- **Verified prerequisites:** M1-1 implementation and archive commits are present, its runtime tests previously passed, and the current worktree had no active OpenSpec change before this proposal. M1-1 exposes the secret-free provider configuration, `RunStore`, `Orchestrator.admit_external_call`, and `Orchestrator.record_external_usage` boundaries consumed here.
- **Expected code paths:** new `nepa/llm/client.py`, `nepa/llm/providers/openai_compat.py`, `nepa/llm/providers/anthropic.py`, `nepa/llm/cache.py`, and `nepa/llm/telemetry.py`; narrow additions to `nepa/config.py`, `nepa/run_store.py`, and `nepa/orchestrator.py` only where the M1-2 price, durable trace/cache, or usage boundary requires them; focused tests under `tests/`.
- **Dependencies:** add only the design-required `httpx` runtime dependency. Existing Pydantic and `jsonschema` paths are reused; Jinja and agent-template dependencies remain deferred to M1-3.
- **Frozen/public behavior:** `project_docs/system_design.md`, gold inputs, freeze records, M0 lint behavior, and M1-1 lifecycle semantics remain unchanged. Credentials are resolved from the configured environment-variable names only at call time and are never written to configuration snapshots, cache entries, prompts, outputs, or traces.
- **Downstream:** M1-3 and M1-4 consume this completion interface and evidence path. This change does not claim M1 completion or any D1.0-D1.11 milestone gate; it supplies the LLM trace and structured-output primitives later exercised by D1.0, D1.6, D1.9, and D1.10.
- **Manual gates:** M1-2 has no independent responsible-owner signature gate and cannot substitute for the M1-4a3 production-model, prompt, or budget freeze.
