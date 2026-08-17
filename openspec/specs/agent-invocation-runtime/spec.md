# agent-invocation-runtime Specification

## Purpose

Define the protocol-neutral Agent role registry, strict prompt rendering, static route resolution, and one-call invocation boundary that connects stage orchestration to the existing LLM provider runtime.

## Requirements

### Requirement: Agent roles and output contracts are explicit
The system SHALL represent every invocable Agent with a registered role identifier, stage association, prompt template, exact required input names, and availability rules. Each invocation SHALL bind one JSON Schema draft 2020-12 contract and one minimal valid output example before prompt rendering. The system SHALL validate both the schema and example with the draft 2020-12 dialect and SHALL reject an unknown role, malformed schema, or nonconforming example before any provider call.

#### Scenario: A valid role and contract are bound
- **WHEN** a caller selects a registered role and supplies a valid draft-2020-12 JSON Schema with a conforming minimal example
- **THEN** the system accepts the role and output contract for rendering and invocation

#### Scenario: The output example violates its schema
- **WHEN** the supplied minimal output example does not validate against the supplied draft-2020-12 JSON Schema
- **THEN** the system returns a deterministic contract error before prompt rendering or any provider call

#### Scenario: The schema is invalid under draft 2020-12
- **WHEN** the supplied output schema violates the draft-2020-12 metaschema
- **THEN** the system returns a deterministic contract error before prompt rendering or any provider call

#### Scenario: The role is not registered
- **WHEN** a caller requests an unknown role identifier
- **THEN** the system returns a deterministic role error before prompt rendering or any provider call

### Requirement: Agent routes are resolved statically from project configuration
The system SHALL resolve each Agent role through the configured role-to-tier mapping, merge tier defaults with explicit role overrides for provider, model, temperature, and maximum output tokens, and verify that the resolved tier and provider exist. PlanCritic SHALL resolve to temperature `0`; Coder and Fixer SHALL resolve to temperature no greater than `0.2`. The LLM SHALL NOT select or alter its own role, tier, provider, or model. The resolver SHALL expose configured escalation metadata to later orchestration without executing escalation itself.

#### Scenario: A role uses tier defaults
- **WHEN** a role configuration names a valid tier and omits provider, model, temperature, and token overrides
- **THEN** the resolved route contains the corresponding tier defaults

#### Scenario: A role overrides selected route fields
- **WHEN** a role configuration overrides only model and temperature
- **THEN** the resolved route contains those two overrides and inherits the remaining fields from the tier

#### Scenario: A configured tier or provider is missing
- **WHEN** route resolution references an absent tier or provider
- **THEN** the system returns a deterministic configuration error before prompt rendering or any provider call

#### Scenario: Escalation metadata is configured
- **WHEN** a role declares an escalation target, including the default Diagnoser or Fixer configuration
- **THEN** the resolved route reports that target but the Agent invocation performs no automatic escalation

#### Scenario: A role violates its sampling bound
- **WHEN** PlanCritic resolves above temperature `0`, or Coder or Fixer resolves above temperature `0.2`
- **THEN** route resolution returns a deterministic configuration error before prompt rendering or any provider call

#### Scenario: The default reviewer route is resolved
- **WHEN** the default project configuration resolves ArchitecturePlanner and PlanCritic
- **THEN** PlanCritic uses a different configured model from the producer and temperature `0`

### Requirement: Prompt rendering is strict, deterministic, and self-contained
The system SHALL render UTF-8 Jinja2 templates with undefined-variable errors enabled. Every template SHALL contain the five ordered sections Role and Goal, Inputs, Output Contract, Rules, and Counterexamples, and SHALL embed the bound JSON Schema and minimal valid output example. Every template SHALL explicitly instruct the model to trust injected artifacts and not trust remembered facts about the target protocol. Inputs SHALL be explicitly delimited and serialized deterministically. The renderer SHALL accept exactly the registered input names and SHALL reject missing or extra inputs before any provider call. The system message SHALL be the fixed framework instruction defined by this change and the rendered template SHALL be the user message.

#### Scenario: Identical inputs are rendered twice
- **WHEN** the same role, raw template bytes, input values, JSON Schema, and output example are rendered twice
- **THEN** the rendered prompt bytes and raw-template SHA-256 are identical

#### Scenario: A required input is missing
- **WHEN** invocation context omits a registered input
- **THEN** rendering fails with a deterministic input error before any provider call

#### Scenario: An undeclared input is supplied
- **WHEN** invocation context includes an input not declared by the role
- **THEN** rendering fails with a deterministic input error before any provider call

#### Scenario: An input contains instructions or log-like text
- **WHEN** an input value contains prose that resembles prompt instructions or application logs
- **THEN** the value remains inside its named input delimiter and does not alter the template sections or output contract

#### Scenario: A packaged template is inspected
- **WHEN** any of the seven M1-3 prompt skeletons is loaded
- **THEN** its Rules section contains the instruction `Trust the injected artifacts; do not trust remembered facts about the target protocol.`

#### Scenario: The LLM request messages are constructed
- **WHEN** a valid Agent prompt is prepared for invocation
- **THEN** the fixed framework instruction is placed in `LLMRequest.system` and the complete five-section rendered template is placed in `LLMRequest.user`

### Requirement: Each Agent invocation delegates one logical completion
The system SHALL expose an Agent invocation operation with explicit `role`, `inputs`, `output_schema`, `output_example`, `run_id`, `stage`, optional `task_id`, `attempt`, and `use_cache` arguments. It SHALL validate that the supplied stage is allowed for the role, construct one LLM request and call context from those values and the resolved route, and delegate exactly one call to the M1-2 `LLMClient.complete` logical-completion boundary. A successful invocation SHALL return the validated parsed JSON together with the provider response and resolved Agent metadata. The Agent layer SHALL preserve typed LLM runtime failures and SHALL NOT add repair calls, stage retries, fallback planning, escalation, or pipeline-state mutation. Provider transport retries and the single structured-output repair already owned by M1-2 MAY occur inside that one logical completion.

#### Scenario: Invocation trace identity is supplied
- **WHEN** a caller supplies a non-blank `run_id`, an allowed `stage`, optional `task_id`, and a positive `attempt`
- **THEN** the Agent invoker constructs `LLMCallContext` with those values and the statically resolved tier

#### Scenario: Invocation trace identity is invalid
- **WHEN** `run_id` is blank, `attempt` is not positive, or `stage` is not allowed for the selected role
- **THEN** the Agent invoker returns a deterministic Agent input error before rendering, cache lookup, or provider I/O

#### Scenario: An Agent invocation succeeds
- **WHEN** the provider runtime returns a schema-valid structured response
- **THEN** the Agent invocation returns that parsed response and its call metadata without issuing another logical completion

#### Scenario: The provider runtime rejects the response
- **WHEN** the M1-2 runtime returns a typed schema, provider, timeout, budget, or configuration failure
- **THEN** the Agent invocation propagates the typed failure without an Agent-layer retry or repair call

#### Scenario: M1-2 performs its bounded schema repair
- **WHEN** the first provider response requires the single structured-output repair allowed by M1-2
- **THEN** the Agent layer has still called `LLMClient.complete` exactly once and adds no further repair attempt

### Requirement: M1-3 provides the required built-in role skeletons
The system SHALL register prompt and invocation skeletons for ArchitecturePlanner, TaskPlanner, PlanCritic, FlatPlanBaseline, Coder, Diagnoser, and Fixer. Their required inputs SHALL match the role boundaries in the authoritative design: ArchitecturePlanner receives the planning index, delivery constraints, and an explicit `repair_context` that is `null` for an initial call or contains only the prior Schema-valid candidate and exact canonical `ARCH_VALIDATE` failures for a fresh semantic-repair call; TaskPlanner receives one work package, its spec slice, adjacent contracts, and test metadata; PlanCritic receives a candidate plan graph, coverage matrix, and lint report; FlatPlanBaseline receives the planning index, delivery constraints, and manifest metadata; Coder receives one task, its spec slice, and interface files; Diagnoser receives build errors and relevant code; Fixer receives a diagnosis and target files. M1-4a1 SHALL own and bind the production ArchitectureDraft Schema/example through the existing invocation-contract slot; the shared ArchitecturePlanner prompt development and production freeze remain M1-4a2 and M1-4a3 responsibilities.

#### Scenario: The built-in catalog is inspected
- **WHEN** the Agent registry is initialized
- **THEN** it contains exactly the seven role identifiers, with ArchitecturePlanner requiring `planning_index`, `delivery_constraints`, and `repair_context`, and every other role retaining its specified input names

#### Scenario: M1-4a1 supplies the production contract
- **WHEN** an M1-4a ArchitecturePlanner invocation binds the production ArchitectureDraft JSON Schema and conforming example
- **THEN** the shared M1-3 invoker uses that contract without introducing another schema registry or provider-completion path

#### Scenario: An initial architecture call is rendered
- **WHEN** ArchitecturePlanner is invoked for an initial candidate
- **THEN** `repair_context` is explicitly delimited and contains canonical `null`

#### Scenario: A semantic-repair architecture call is rendered
- **WHEN** ArchitecturePlanner is invoked as a fresh semantic-repair call
- **THEN** `repair_context` is explicitly delimited and contains only the prior Schema-valid candidate and exact canonical validator issue list supplied by the caller

### Requirement: FlatPlanBaseline is an explicit comparison strategy only
The system SHALL make FlatPlanBaseline invocable only when the resolved planning strategy is `flat`. It SHALL NOT use FlatPlanBaseline as a fallback for failed layered planning or silently switch between flat and layered strategies.

#### Scenario: Flat planning is explicitly configured
- **WHEN** `planning.strategy` is `flat` and FlatPlanBaseline is requested
- **THEN** the role is available for invocation

#### Scenario: Layered planning is configured
- **WHEN** `planning.strategy` is `layered` and FlatPlanBaseline is requested
- **THEN** the system returns a deterministic availability error before any provider call

#### Scenario: Layered planning fails
- **WHEN** a layered planning Agent returns an error
- **THEN** the Agent layer does not invoke FlatPlanBaseline

### Requirement: Shared Agent assets remain protocol and provider neutral
The Agent framework and bundled prompt skeletons SHALL NOT embed target-protocol field names, message names, request identifiers, paths, interface constants, ports, topics, subscription semantics, provider-specific instructions, or model-specific branches. Concrete protocol identifiers SHALL enter prompts only through explicitly delimited invocation inputs. Automated checks SHALL scan the shared Agent sources and render at least one synthetic non-MQTT input set.

#### Scenario: Shared Agent sources are scanned
- **WHEN** the protocol-neutrality test examines the Agent framework and bundled prompt templates
- **THEN** no prohibited MQTT-specific token, target-protocol constant, provider-specific instruction, or model-specific branch is present

#### Scenario: A synthetic non-MQTT planning input is rendered
- **WHEN** a role is rendered with explicit non-MQTT protocol identifiers
- **THEN** the rendered prompt contains those identifiers only within the supplied input delimiters and retains the generic rules and output contract
