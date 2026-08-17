## Why

M1-3 provides a generic Agent invocation boundary, but M1-4a2 cannot fairly optimize a shared ArchitecturePlanner prompt until the frozen planning inputs, production ArchitectureDraft contract, semantic validator, isolated three-model trial storage, and recomputable statistics are fixed. M1-4a1 is therefore the next required M1 work item and establishes only that protocol-neutral narrow slice before any prompt optimization or qualification batch begins.

## What Changes

- **Milestone/work item:** implement only M1-4a1 from §10.2, grounded in §4.2, §5.3, §5.6.5, §6.4.1, §6.4.3-§6.4.4, §6.4.8.1, §8.3-§8.4, §8.8, §9.1.5, and D1.0/D1.11.
- Add deterministic freezing and validation for the calibration Spec IR, Target Profile, and Test Bundle; derive the same S4-visible Test Manifest metadata summary, planning index, and narrow-slice Delivery Constraints that production S4 will reuse.
- Add the production ArchitectureDraft JSON Schema, canonical example/serializer, and one shared `ARCH_VALIDATE` implementation covering every `S4-G2` architecture sub-gate, with stable `arch_01`-`arch_10` results and exact machine-readable issues.
- Align the existing M1-2 structured-output validator and M1-3 contract/example preflight with the project-mandated JSON Schema draft 2020-12 dialect before binding ArchitectureDraft; preserve all existing logical-call, format-repair, provider, cache, budget, and trace semantics.
- Add a lineage manifest whose id binds every comparison-controlled element except the deliberately variable shared prompt: frozen inputs, input construction, Delivery Constraints compiler, ArchitectureDraft Schema, serializer, validator, three calibration model configurations, and statistical definitions.
- Add an isolated batch/trial driver for the exactly configured Qwen, Claude, and DeepSeek candidates. All three receive the same frozen inputs and prompt hash but use separate sessions, clients, caches, traces, directories, and trial identities; cross-trial cache reuse is disabled and no model can vote on or consume another model's output.
- Add canonical calibration artifacts under `runs/_calibration/s4-architecture/<lineage_id>/<prompt_version>/<model_id>/`, including batch metadata, per-trial request/response indexes and validation evidence, and per-model reports that are reproducible from the trial records.
- Refine the ArchitecturePlanner skeleton input contract only as required for the M1-4a semantic-repair protocol: every call receives an explicitly delimited repair context that is `null` for an initial call or contains only the prior candidate and exact `ARCH_VALIDATE` failures for a fresh repair call. This is an interface refinement, not prompt optimization.
- Add deterministic protocol-neutral source/prompt scanning and a non-MQTT application-layer fixture proving that protocol facts enter the planning artifacts and rendered prompt only through frozen inputs.
- Add Schema, validator, compiler, lineage, artifact-store, aggregation, isolation, and neutrality tests using deterministic fixtures/fake providers; no live-model quality result is an acceptance condition for this change.
- **Out of scope:** M1-4a2 V0/V1/optional V2 prompt development or prompt selection; M1-4a3 N=20 qualification batches, B1-B4 disposition, production-model selection, or owner signature; M1-4b Plan/Plan State/Blueprint/PlanDraftIR/Linker/full lint; M1-4c TaskPlanner, PlanCritic, flat baseline execution, S4 state machine, repair budgets, seal, or publication; S5/S6; and the unresolved M1-6 escalation-role decision.

## Capabilities

### New Capabilities

- `planning-architecture-infrastructure`: Deterministic M1-4a planning inputs and Delivery Constraints, the production ArchitectureDraft contract and `ARCH_VALIDATE`, lineage identity, isolated three-model trial persistence, recomputable calibration reports, and protocol-neutrality gates.

### Modified Capabilities

- `agent-invocation-runtime`: Refine only the ArchitecturePlanner input boundary so initial and fresh semantic-repair calls carry an explicit, delimited repair context while preserving static routing, one logical M1-2 completion per Agent invocation, and all existing role behavior.
- `llm-provider-runtime`: Use the project-mandated JSON Schema draft 2020-12 dialect for schema preflight and structured-response validation so the production ArchitectureDraft contract is enforced by the same dialect end to end.

## Impact

- **Verified prerequisites:** the M1-1, M1-2, and M1-3 implementation/archive commits named in the handoff exist in repository history; their main specs are present and there are no other active changes. The handoff reports 198 passing tests and strict OpenSpec validation; this proposal does not treat that report as a substitute for rerunning acceptance after implementation. The M0 default-input freeze is signed and its recorded paths/hashes remain read-only.
- **Expected code paths:** focused additions under `nepa/speclib/` for planning/Delivery Constraints/architecture validation, `nepa/schemas/` for ArchitectureDraft and calibration artifacts, and one calibration driver/store package; a narrow ArchitecturePlanner registry/template interface update plus draft-2020-12 contract validation under `nepa/agents/`; the matching dialect correction in `nepa/llm/client.py`; focused tests and non-MQTT fixtures under `tests/`. The cross-cutting file count is inherent to the one M1-4a1 work item because its required closed loop is input → Schema/validator → isolated trial evidence → recomputed report.
- **Reuse boundary:** existing canonical JSON, M0 lints, M1-1 input semantics, M1-2 provider/structured-repair/cache/trace behavior, M1-3 rendering/invocation, and configured `calibration_models` are reused. No second provider client, schema-format repair path, production S4 validator, or Delivery Compiler path may be introduced.
- **Frozen/public behavior:** `project_docs/system_design.md`, gold inputs/freeze records, formal Run v3, report/eval-runs semantics, existing non-ArchitecturePlanner Agent roles, provider endpoints, and production static role routing remain unchanged. Calibration directories are gitignored non-Run evidence and must not create `run.json`, S4 receipts, formal reports, or consumable Plans.
- **Acceptance:** focused tests must prove identical canonical inputs produce identical planning artifacts/lineage, all `arch_01`-`arch_10` gates are recomputable, reports reproduce exactly from trial evidence, the three model roots never share cache/trace/session state, and protocol-neutral scans plus the non-MQTT fixture pass. Final change acceptance also runs the full repository suite and `openspec validate --all --strict`.
- **Downstream/manual gates:** M1-4a2 must reuse the frozen lineage and vary only the shared prompt; M1-4a3 must reuse the same infrastructure for formal N=20 batches. M1-4b must extend the same Delivery Compiler and validator path rather than fork it. M1-4a1 has no independent owner-signature gate and does not satisfy D1.0; the production-model/prompt/Schema/validator/call-shape/budget freeze and responsible-owner signature remain exclusively M1-4a3.
