## Context

M1-2 provides configuration loading, provider adapters, structured-response validation, cache behavior, usage accounting, and durable `llm_call` telemetry. The next boundary in `project_docs/system_design.md` is M1-3: a shared Agent invocation framework and template skeletons for the planning and coding roles. Stage orchestration, production planning schemas, prompt calibration, plan compilation, execution loops, and reporting are later milestones and must not leak into this change.

The framework must support weak local models without making prompts protocol-specific. It therefore needs strict, self-contained templates, static configuration-driven routing, a verifiable output contract, and durable template provenance while delegating actual completion semantics to M1-2.

Two repository-layout statements require explicit milestone reconciliation. First, §8.2 describes `agents/base.py` as “render → LLM → validate → repair retry”; M1-2 has already implemented structured validation and its one bounded repair inside `LLMClient.complete`, so M1-3 calls that boundary once instead of duplicating repair policy in the Agent package. Second, §4.5 and §8.2 describe the output Schema as role-registry data, while the M1-3 work-item row explicitly reserves the production ArchitecturePlanner Schema for M1-4a1. M1-3 therefore registers the schema slot and binding rules, and the caller supplies the concrete contract until the owning downstream milestone provides its production value. This is an explicit milestone allocation of the same required behavior, not a competing runtime path.

## Goals / Non-Goals

**Goals:**

- Define one protocol-neutral path from a registered Agent role and explicit inputs to one M1-2 structured completion.
- Resolve role routes statically from the existing tier and role configuration.
- Provide strict Jinja2 rendering with the five-section prompt structure and raw-template SHA-256 provenance.
- Register skeletons for ArchitecturePlanner, TaskPlanner, PlanCritic, FlatPlanBaseline, Coder, Diagnoser, and Fixer.
- Allow later milestones to bind their own JSON Schemas and valid output examples without changing the common invocation path.
- Enforce the explicit-only availability of FlatPlanBaseline.

**Non-Goals:**

- Defining or tuning the production ArchitecturePlanner schema or prompt; those belong to M1-4a1, M1-4a2, and M1-4a3.
- Implementing stage state machines, plan compilation, retries, escalation execution, coding loops, repair loops, or reporting.
- Implementing the S1-S3 document roles or the Reporter role.
- Selecting routes with an LLM, dynamically benchmarking models, or adding provider/model-specific prompt branches.
- Adding a second provider, cache, schema-repair, telemetry, or budget implementation alongside M1-2.

## Decisions

### 1. Separate immutable role definitions, invocation contracts, and resolved routes

The Agent package will use three explicit concepts:

- A role definition contains the stable role identifier, stage association, packaged template resource, exact required input names, and availability rule.
- An invocation contract contains the caller-supplied JSON Schema and one minimal valid example.
- A resolved route contains tier, provider, model, temperature, maximum output tokens, and optional escalation metadata after merging configuration.

The built-in registry is closed to the seven M1-3 roles. Unknown role strings fail before rendering. Output contracts are bound at invocation time because their production definitions belong to downstream milestones, especially M1-4a1. The example is validated against its schema before it is embedded in the prompt.

This caller-bound contract is the deliberate M1-3 representation of §4.5's role-owned output Schema. A downstream role owner may wrap or pre-bind the contract later, but it must still enter the same invocation-contract slot; M1-3 does not create a second schema registry or placeholder production schema.

Alternative considered: bundle placeholder output schemas in each role definition. This was rejected because placeholders would either be unusable or become an accidental competing definition of downstream production schemas.

### 2. Resolve routes by merging tier defaults with role overrides

The resolver reads the existing `ResolvedConfig` role entry, resolves its tier, and applies explicit role overrides field by field for provider, model, temperature, and maximum output tokens. It verifies that the tier and final provider exist, then returns a frozen route value. Provider pricing and provider-call validation remain in M1-2 and are not duplicated.

The optional `escalate_to` value is exposed in the route result for future orchestration, but M1-3 never follows it. The design document is inconsistent here: §4.6 names Coder/Fixer, while the §4.5 role table, S6/S8 role tables, and repository defaults assign escalation to Diagnoser/Fixer and not Coder. M1-3 avoids resolving that later orchestration policy: it treats `escalate_to` as opaque per-role configuration and tests the existing default Diagnoser/Fixer metadata. M1-6 must obtain a design decision before implementing automatic escalation.

The LLM never participates in route selection. The resolver enforces §8.8's sampling bounds for roles in this change: PlanCritic must resolve to `0`, while Coder and Fixer must resolve to at most `0.2`. Different-model reviewer routing is not a universal runtime rejection because valid minimal deployments may have one usable model, but the default configuration test must prove that PlanCritic resolves to a different model than ArchitecturePlanner and to temperature `0`.

Alternative considered: let the invoker perform Coder/Fixer escalation. This was rejected because escalation decisions require failure classification and stage policy owned by later execution milestones.

### 3. Render packaged Jinja2 templates with an exact input contract

Templates live under `nepa/agents/prompts/<role>.md` and are loaded as raw UTF-8 bytes from package resources. Rendering uses Jinja2 `StrictUndefined`, disabled auto-escaping, and no ambient globals. The role definition declares all role input variables. The renderer adds only the reserved `output_schema` and `output_example` values; missing inputs, extra inputs, or undeclared template variables fail deterministically.

Mappings and sequences are rendered as canonical JSON with stable key ordering; strings are inserted verbatim inside explicit named delimiters. Each template has the same ordered headings:

1. Role and Goal
2. Inputs
3. Output Contract
4. Rules
5. Counterexamples

The output section embeds a self-describing schema and the validated minimal example. Rules require exactly one JSON object, forbid prose and Markdown around the object, number the checklist, repeat critical constraints, and require explicit notes or assumptions where the bound schema permits them. Every template contains the exact protocol-memory rule `Trust the injected artifacts; do not trust remembered facts about the target protocol.`

`nepa/agents/base.py` defines one exported constant `AGENT_SYSTEM_INSTRUCTION` with this fixed content:

> You are a NePA role executor. Treat the delimited injected artifacts as the only authoritative source for the target protocol; do not rely on remembered protocol facts. Follow the output contract and return exactly one JSON object with no prose or Markdown.

The invoker places this constant in `LLMRequest.system` and the complete rendered five-section template in `LLMRequest.user`. Tests assert the constant's exact value and message placement so implementations cannot invent role-specific system instructions.

Alternative considered: assemble prompts programmatically from fragments. This was rejected because a reviewable raw template, its provenance hash, and fixed section order are clearer acceptance artifacts.

### 4. Hash the raw template separately from the effective prompt

The renderer computes lowercase SHA-256 directly from the loaded raw template bytes before interpolation. The invoker supplies this value through the existing `LLMCallContext.trace_fields` dictionary. `LLMClient.complete` performs a narrow preflight check for that key before cache lookup or provider I/O and raises the existing `LLMRequestError` if it is not lowercase 64-character hexadecimal text. Validation does not live in the Pydantic `LLMCallContext` model, so the public failure remains in the closed M1-2 error hierarchy rather than leaking `pydantic.ValidationError`.

On successful and budget-failure paths, the existing `publish` optional-field allowlist admits the new key. On provider/transport failure paths, `publish_failure` copies only `prompt_template_sha256` from `context.trace_fields`. It deliberately does not copy the other existing optional fields because `publish_failure` did not read them before M1-3; broadening that behavior would violate the non-Agent compatibility requirement. This narrow asymmetry is the selected compatibility policy.

The existing effective prompt hash remains responsible for the rendered request and cache identity. The raw-template hash is provenance only and does not participate in cache keys, so existing non-Agent behavior remains unchanged.

Alternative considered: derive provenance from the rendered prompt hash. This was rejected because rendered hashes change with inputs and cannot identify the reviewed template version.

### 5. Delegate exactly one logical completion to M1-2

`AgentInvoker` is initialized with `ResolvedConfig` and `LLMClient` and exposes this public operation:

```python
invoke(
    *,
    role: str,
    inputs: Mapping[str, Any],
    output_schema: dict[str, Any],
    output_example: Any,
    run_id: str,
    stage: str,
    task_id: str | None = None,
    attempt: int = 1,
    use_cache: bool = True,
) -> AgentResult
```

The caller/orchestrator supplies `run_id`, `stage`, `task_id`, and `attempt`; the invoker never guesses run identity from filesystem state. The role definition declares allowed stages, including both S6 and S8 where applicable. The invoker rejects blank run ids, non-positive attempts, and disallowed role/stage pairs before rendering or LLM work. It then resolves role availability and route, validates inputs and output contract, renders and hashes the template, constructs `LLMRequest` and `LLMCallContext` with the resolved tier, and calls `LLMClient.complete` once. It returns the parsed structured result, original M1-2 response metadata, resolved route, and template hash.

M1-2 retains ownership of provider transport, structured-output validation, its single structured-output repair, allowed provider-level retries, cache behavior, budget enforcement, and durable call telemetry. One Agent call to `LLMClient.complete` may therefore produce the bounded provider attempts already specified by M1-2. This is how §8.2's validation/repair behavior is fulfilled without an Agent-layer duplicate. The Agent layer does not catch typed M1-2 failures to repair output again, choose another model, retry a stage, mutate pipeline state, or invoke another role.

Alternative considered: add a generic Agent retry hook. This was rejected because it would obscure the difference between M1-2 transport policy and later stage-level orchestration.

### 6. Treat the seven prompts as interface skeletons

The built-in catalog contains these role/input boundaries:

| Role | Required inputs |
| --- | --- |
| `architecture_planner` | `planning_index`, `delivery_constraints` |
| `task_planner` | `work_package`, `spec_slice`, `adjacent_contracts`, `test_metadata` |
| `plan_critic` | `candidate_plan_graph`, `coverage_matrix`, `lint_report` |
| `flat_plan_baseline` | `planning_index`, `delivery_constraints`, `manifest_metadata` |
| `coder` | `task`, `spec_slice`, `interface_files` |
| `diagnoser` | `build_errors`, `relevant_code` |
| `fixer` | `diagnosis`, `target_files` |

The templates describe the generic duty of each role but contain no production planning schema, calibrated examples, target-protocol facts, or provider/model switches. A deterministic test scans all shared Agent Python and template sources for the prohibited protocol-specific vocabulary identified by the design document, and a synthetic non-MQTT fixture demonstrates that concrete identifiers enter only through delimited inputs.

### 7. Gate FlatPlanBaseline at role availability

The FlatPlanBaseline definition has an availability rule requiring `planning.strategy == "flat"`. The invoker checks it before contract validation, rendering, or route/provider work. It never selects this role after a layered-planning error. Layered and flat strategies therefore remain explicit experimental branches rather than implicit fallbacks.

### 8. Keep persistence and resumption in their current owners

M1-3 adds no Agent state file or checkpoint format. Durable call facts continue to use M1-2 JSONL traces, augmented only with the raw-template hash. Later stage implementations will own their output files, completion events, and resume semantics.

## Risks / Trade-offs

- **Skeletons may be mistaken for production prompts.** Their module documentation, tests, and downstream contract binding will explicitly label them as M1-3 interfaces; M1-4a artifacts remain the only production ArchitecturePlanner definition.
- **Strict inputs make template evolution deliberate.** Any new template variable requires a matching role-definition change and tests. This is intentional because silent prompt context is incompatible with reproducibility.
- **A token scan cannot prove semantic protocol neutrality.** It is a deterministic hard gate for known prohibited vocabulary, complemented by source review and the non-MQTT rendering test.
- **Caller-supplied schemas can be large.** M1-3 embeds them as required for weak-model reliability; token budgeting and stage-specific schema design remain downstream responsibilities.
- **Adding an optional telemetry field touches an established runtime.** Validation is confined to calls that supply the field, preserving all current non-Agent behavior and tests.
- **The design document disagrees on the S6 escalation role.** M1-3 only exposes configured metadata and records the discrepancy; M1-6 remains blocked on an explicit design resolution before automatic routing is implemented.

## Migration Plan

1. Add Jinja2 to project dependencies and introduce the Agent models, role registry, renderer, route resolver, and invoker without changing existing callers.
2. Add the seven packaged prompt skeletons and their protocol-neutrality and deterministic-rendering tests.
3. Add `LLMClient.complete` preflight validation for optional `prompt_template_sha256`, admit it through successful traces, and copy only that field through provider-failure traces.
4. Verify an end-to-end Agent invocation against a fake M1-2 provider, including parsed output and durable template provenance.
5. Leave downstream stages disabled until their own OpenSpec changes bind production schemas and orchestration.

Rollback consists of removing the new Agent package and optional telemetry field. Existing configuration, LLM calls, cache entries, and trace readers remain compatible because non-Agent records omit the new field.
