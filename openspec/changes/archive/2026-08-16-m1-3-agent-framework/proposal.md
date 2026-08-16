## Why

M1-2 now provides the provider-neutral, budgeted, and auditable LLM boundary, but later planning and coding work still lacks a deterministic layer that resolves configured roles, renders bounded templates, binds output contracts, and invokes that boundary consistently. M1-3 is the next independently verifiable M1 work item and must establish this Agent framework before any production planning Schema, prompt optimization, calibration, or stage controller is implemented.

## What Changes

- **Milestone/work item:** implement only M1-3 from §10.2, grounded in §4.5-§4.6 and §8.2/§8.8, on top of the committed and archived M1-1/M1-2 runtime contracts.
- Add a generic Agent invoker that accepts one registered role, one explicit context package, caller-supplied run/stage identity, a JSON Schema, and a minimal valid example; it renders the template, resolves the configured provider/model/tier/parameters, and delegates exactly one logical completion to the M1-2 client.
- Add deterministic static routing resolution for tier defaults plus role overrides. The framework never lets an LLM choose a model and does not execute escalation policy.
- Add a closed role registry and non-production registration interfaces/template skeletons for `ArchitecturePlanner`, `TaskPlanner`, `PlanCritic`, A9-only `FlatPlanBaseline`, `Coder`, `Diagnoser`, and `Fixer`.
- Gate `FlatPlanBaseline` to explicit `planning.strategy=flat`; it is never a fallback for layered planning.
- Add Jinja2 rendering with strict missing/extra context handling, canonical Schema/example embedding, explicit input delimiters, and the fixed five-section prompt structure required by §8.8.
- Hash the raw template source, validate that trace field at the M1-2 client preflight boundary, and persist it for Agent success and failure traces alongside the already persisted effective prompt/output evidence. The failure path admits only this new field and does not begin copying pre-existing optional context fields.
- Add deterministic protocol-neutral checks for the new framework and prompt sources, including forbidden embedded protocol identifiers/facts and model/provider-specific branches in the shared planning skeleton.
- Add fixture-only tests for role resolution, rendering, output-contract binding, LLM delegation, trace provenance, flat-only gating, and protocol-neutrality. No live provider call, production prompt tuning, or calibration run is part of acceptance.
- **Out of scope:** production ArchitectureDraft or other planning Schemas (M1-4a1); ArchitecturePlanner shared-prompt development (M1-4a2); three-model calibration/model selection or responsible-owner freeze (M1-4a3); Plan Compiler/S4 controller (M1-4b/M1-4c); S6 attempt routing/escalation and coding loop (M1-6); S1-S3/Reporter role implementations; CLI behavior; and any live model-quality claim.

## Capabilities

### New Capabilities

- `agent-invocation-runtime`: Deterministic role registration, static configured routing, strict protocol-neutral template rendering, output-contract binding, flat-only role gating, and delegation to the existing LLM runtime.

### Modified Capabilities

- `llm-provider-runtime`: Agent-originated calls add the raw prompt-template SHA-256 to durable LLM trace evidence without changing provider, cache, retry, pricing, or budget semantics.

## Impact

- **Verified prerequisites:** M1-1 and M1-2 implementation/archives are committed; M1-2 main specs are synced; the complete repository suite and strict OpenSpec validation passed before this proposal; there are no active changes.
- **Expected code paths:** new `nepa/agents/base.py`, `nepa/agents/roles.py`, package initializers, and seven skeletons under `nepa/agents/prompts/`; a narrow template-provenance preflight/trace extension in `nepa/llm/client.py` and `nepa/llm/telemetry.py`; focused tests under `tests/`.
- **Dependencies:** add only the design-required Jinja2 dependency. Existing Pydantic configuration, canonical JSON, M1-1 budget/store, and M1-2 LLM/cache/trace behavior are reused rather than duplicated.
- **Frozen/public behavior:** `project_docs/system_design.md`, gold inputs, freeze records, M0/M1-1 behavior, provider routing, credentials, prices, retry/cache semantics, and Run lifecycle remain unchanged. New template content must contain no target-protocol facts; concrete identifiers may appear only through caller-supplied context.
- **Downstream:** M1-4a1 will bind the production ArchitecturePlanner Schema/validator and M1-4a2 will develop/freeze the shared prompt using these interfaces; M1-4c and M1-6 will supply stage-specific contexts, retry/escalation policy, and output ownership. This change does not claim M1 completion or D1.0-D1.11.
- **Manual gates:** M1-3 has no independent responsible-owner signature gate and cannot substitute for the M1-4a3 production-model/prompt/Schema/validator/budget freeze.
- **Design reconciliation:** §8.2's Agent-level “validation and repair retry” is implemented by the already-completed M1-2 `LLMClient.complete` logical-call boundary, while §4.5's role-owned output Schema is represented in M1-3 by an explicit per-invocation contract slot whose production ArchitecturePlanner value is delivered in M1-4a1. These are milestone allocations, not removal of either required behavior.
