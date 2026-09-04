## Why

M1-4a1 supplies the production-shaped planning inputs, free-layout ArchitectureDraft contract, and `ARCH_VALIDATE`, but the repository still lacks the deterministic compiler that turns an accepted architecture and task decomposition into the complete Delivery Blueprint, candidate Plan data, and mechanically validated Plan State required downstream. M1-4b and M1-4b2 must therefore be delivered together: a Blueprint compiler without the approved free-layout transcriber cannot close its input-to-output contract, while a standalone transcriber has no independently consumable output outside that compiler.

## What Changes

- Implement the M1-4b deterministic compilation boundary defined by authoritative design 7.1.0: complete the built-in application-layer/C99 delivery rules, six naming patterns, per-key resource merge, role support gate, `none`/`per_message`/`per_type` expansion, and complete Delivery Blueprint compilation.
- Implement M1-4b2 inside that same compiler path: transcribe every `architecture.layout.files[]` entry and `layout.build_graph` entry without loss, using the approved eight-row `kind`/`producer` derivation table in `pipeline_design_s4_s9.md` 1.2.0 and rejecting every table-external combination.
- Add closed Plan and Plan State Schemas with conforming examples, a deterministic `PlanDraftIR`, Linker, coverage construction, Blueprint binding, and stable task ordering/identifier assignment.
- Add `plan_lint` basic/full validation plus snapshot, transition, and execution-state validation, exposed through `nepa lint plan` without adding a parallel compiler or validator path.
- Preserve protocol neutrality: compiler behavior may consume only frozen structured inputs and system-declared language/role rules, never protocol identity, filenames, suffixes, environment, workspace state, time, randomness, or network access.
- Keep M1-4b and M1-4b2 as separately traceable work items in tasks and acceptance evidence even though this change closes them as one inseparable end-to-end compilation boundary.

## Capabilities

### New Capabilities

- `delivery-blueprint-compilation`: Deterministically compile complete Blueprint naming, resource, deliverable, build-graph, mechanical-generation, and free-layout file-rule projections.
- `plan-compilation-validation`: Build PlanDraftIR, link stable tasks and coverage into a Blueprint-bound Plan, and enforce basic/full plan lint.
- `plan-state-validation`: Validate Plan State snapshots, legal state transitions, and execution-state evidence against the bound Plan.

### Modified Capabilities

- `planning-architecture-infrastructure`: Extend the existing shared Delivery Constraints/compiler boundary from the M1-4a1 architecture slice to the complete M1-4b/M1-4b2 deterministic inputs and outputs consumed by Plan compilation.

## Impact

- **Milestone and design:** covers M1-4b and M1-4b2 from `project_docs/system_design.md` 7.1.0 §5.2, §5.6.5.2-§5.6.5.3, §6.4.1, §6.4.5 and §10.2, plus `project_docs/pipeline_design_s4_s9.md` 1.2.0 §5.2.2 and §5.2.5. The approved derivation-table decision is already recorded in both authoritative documents.
- **Verified prerequisites:** archived M0 and M1-4a1 changes are present; the current M0 gold lint suite and full repository tests passed during the immediately preceding M1-4a2 closure. M1-4a2 is archived with an owner-approved handoff, but it is not an implementation dependency of this deterministic compiler. These records do not substitute for rerunning this change's acceptance checks after implementation.
- **Affected implementation:** extend `nepa/speclib/delivery.py`; add focused Plan and Plan State validation modules under `nepa/speclib/`; extend the existing `nepa/cli.py` lint command; add closed schemas/examples and focused unit, negative, determinism, and protocol-neutrality fixtures under `nepa/schemas/` and `tests/`. No new dependency or parallel framework is planned.
- **Downstream:** M1-4c may consume this work only after both work-item task groups and their machine gates pass. This change publishes no S4 controller, checkpoint, PlanCritic invocation, active-plan seal, S5 materialization, S6 execution driver, or revision infrastructure.
- **Acceptance boundary:** completion requires strict OpenSpec validation, schema/example mutual validation, positive and negative compiler/lint/state tests, byte-identical deterministic replay, table-complete layout-transcription tests, MQTT/non-MQTT protocol-neutrality checks, the full repository suite, and `git diff --check`. No new owner signature is defined for M1-4b/M1-4b2; existing M0 freezes and the M1-4a2 handoff remain unchanged.
