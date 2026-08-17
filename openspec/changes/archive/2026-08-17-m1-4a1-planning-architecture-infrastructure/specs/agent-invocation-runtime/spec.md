## MODIFIED Requirements

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
