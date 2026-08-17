## Context

See `proposal.md` for motivation and the two delta-spec areas for behavior. The verified repository boundary is M1-1 Run/config/store/orchestration, M1-2 provider/structured-output/cache/trace, and M1-3 role/render/invocation skeletons. There is no planning-index builder, Delivery Compiler, planning Schema, S4 validator, or calibration driver yet. `ResolvedConfig.calibration_models` already carries the three requested provider/model/temperature/max-output-token bindings, and `runs/` is already gitignored.

M1-4a1 is not a formal stage controller. It builds the deterministic ArchitecturePlanner slice shared by later production S4 and the isolated prompt/calibration workflow. It may call only ArchitecturePlanner through the M1-3/M1-2 boundary, the M1-2 format repair, and production `ARCH_VALIDATE`; it must not produce TaskPlanner shards, Linker output, PlanCritic reviews, a Blueprint, a sealed Plan, or downstream workspace state.

One existing implementation mismatch must be corrected before binding the new production contract: `project_docs/system_design.md` mandates JSON Schema draft 2020-12, while `LLMClient` and `PromptRenderer` currently instantiate `Draft7Validator`. M1-4a1 will switch those two generic validation points to `Draft202012Validator`. This follows the existing authoritative dialect; it is not a design-document change and does not alter M1-2's one-format-repair policy.

The known §4.6 versus §4.5/S6/S8 escalation-role conflict is unrelated. This change neither interprets nor implements automatic Coder/Diagnoser/Fixer escalation.

## Goals / Non-Goals

**Goals:**

- Produce one deterministic planning-input/Delivery-Constraints path that both calibration and production S4 can call.
- Freeze an exact production ArchitectureDraft Schema/example and a complete, gate-addressable `ARCH_VALIDATE` implementation.
- Make every trial and aggregate number derivable from immutable, hash-bound evidence.
- Run the three configured candidates with the same comparison inputs and prompt while isolating all mutable/session/cache/trace state.
- Give M1-4a2 a driver in which the only within-lineage variable is the shared ArchitecturePlanner template bytes.
- Keep generated protocol facts attributable to frozen inputs and documented application-layer language/role rules.

**Non-Goals:**

- No V0/V1/V2 prompt diagnosis, edits, thresholds, winner selection, N=20 batch, B1-B4 branch, production route change, or owner signature.
- No complete Delivery Blueprint, Plan/Plan State Schema, PlanDraftIR, Linker, basic/full `plan_lint`, task-state validation, TaskPlanner, PlanCritic, flat experiment, S4 checkpoint state machine, seal, or formal receipt.
- No public calibration CLI. The initial API is Python-callable and testable; user-facing commands belong to the work item that owns them.
- No test implementation lookup, collection, runner/oracle/adapter generation, or protocol behavior execution.
- No new provider, cache, format-repair, pricing, budget, canonical-JSON, or formal Run-store semantics.

## Decisions

### 1. Split pure S4 planning logic from calibration orchestration

Deterministic planning logic will live under `nepa/speclib/` and have no dependency on providers, prompts, calibration directories, or clocks:

- `planning.py` validates/parses the three inputs and builds the Test Manifest summary and planning index;
- `delivery.py` owns `compile_delivery_constraints(spec, target_profile)` and is the future home of M1-4b's `compile_delivery_blueprint(...)` extension;
- `architecture.py` loads/serializes ArchitectureDraft and runs `ARCH_VALIDATE`.

The isolated experiment workflow will live in `nepa/calibration/s4_architecture.py`. It composes the pure functions, existing Agent/LLM boundary, filesystem artifact primitives, and report aggregation. `nepa/stages/` remains untouched because calibration is explicitly not a Run or S4 stage commit.

This separation makes the “experiment and production use the same implementation” rule structural: calibration imports the production-oriented `speclib` functions; it cannot inject a validator callback. Later S4 imports those same functions.

Alternative considered: place the validator and constraints compiler inside the calibration package. Rejected because M1-4b/M1-4c would either import experimental code or create the prohibited second production path.

### 2. Prepare semantic inputs in memory, compute lineage, then publish one immutable input root

`prepare_architecture_inputs` will reuse the current `lint_spec`, `lint_target`, and `lint_test_bundle` behavior. It preserves Spec IR source bytes, emits the validated Target Profile as canonical two-field bytes, and requires Test Bundle bytes to already equal the canonical encoding. It then returns parsed objects plus byte hashes without writing.

The driver builds the Test Manifest summary, planning index, Delivery Constraints, Schema/example hashes, code-component hashes, statistical-definition hash, and three model-target projections in memory. `lineage_id` is SHA-256 over the canonical semantic projection of this manifest, excluding only `lineage_id` itself and every prompt label/hash. After computing the id, the driver creates:

```text
runs/_calibration/s4-architecture/<lineage_id>/
├── lineage.json
├── inputs/
│   ├── spec.json
│   ├── target.json
│   └── test_bundle.json
├── planning_index.json
├── test_manifest_metadata.json
└── delivery_constraints.json
```

All files are immutable: replay is allowed only for byte-identical content. Every reference inside `lineage.json` is relative to the lineage root and has a lowercase SHA-256. The manifest id is recomputed after every load before any trial is admitted.

This avoids putting temporary files under a path whose name depends on their own eventual hashes, and avoids copying M1-1 `run.json` into a non-Run workflow.

Alternative considered: initialize a formal spec-run and store calibration under it. Rejected because §6.4.8 explicitly forbids `run.json`, S4 receipts, formal reports, and downstream consumption for this workflow.

### 3. Define one compact, lossless planning index for the architecture decision

`planning_index.json` has a closed `schema_version: "1.0"` shape:

```text
protocol                 # name/version/roles from Spec IR
target_profile           # the canonical roles/language object
transport                # structured transport facts and req_ids, if present
types[]                  # structural definitions and req_ids
messages[]               # structural definitions, sender/receiver roles, fields, req_ids
requirements[]           # exactly id/level/text
reference_graph          # type/message/field dependencies and element->REQ links
tests[]                  # nodeid/layer/description/req_ids/gate/resolved build variants
build_variant_ids[]      # stable ids from the language rule
```

Input array order is normalized only where order is semantically a set; message wire/field order is preserved. Maps and set-like arrays use deterministic ordering. `source_ref` is absent from the requirement projection, so `source_ref.quote` cannot leak past PREPARE, while every normative requirement remains present verbatim in `text`. Test `build_variant_ids` is resolved from the per-test field or the bundle default before inclusion. The builder validates all REQ and build-variant references rather than dropping unknown entries.

Alternative considered: pass the full Spec IR to ArchitecturePlanner. Rejected because it carries source quotes that §6.4.3 explicitly removes and makes a later “planning index” name misleading.

### 4. Implement the narrow Delivery Constraints projection as the first half of the shared Delivery Compiler

`compile_delivery_constraints(spec, target_profile)` is a pure function. For M1-4a1 it returns a closed `schema_version: "1.0"` projection containing:

- canonical target roles/language and the applicable application-layer rule;
- the §5.6.5.2 naming derivation and six resolved patterns;
- the documented role resource limits;
- the language-rule build variants;
- fully expanded file slots with rule id, path, kind, producer, mutability, expansion source, and purpose;
- required internal-interface slots and the generic server ABI constraints needed to evaluate contract/interface closure.

It implements the target support gate, identifier normalization/collision rules, current resource merge rules, `none`/`per_message` expansion, path safety/uniqueness, and stable ordering. Protocol-specific names can appear only after applying these rules to Spec fields. The current supported target remains C99/server as already designed; unsupported combinations fail with `TARGET_LANGUAGE_UNSUPPORTED` or `TARGET_ROLE_UNSUPPORTED`.

This artifact deliberately omits Plan-dependent owners, deliverable/build-artifact/link-source-set resolution, layout-template execution, mechanical output, and Blueprint hashing. M1-4b will extend this same module and consume the same constraints rather than replace it.

Alternative considered: represent only a flat list of allowed paths. Rejected because ArchitecturePlanner also needs naming, resource, build-variant, mutability, and internal-interface constraints to make its assumptions and contract/file allocation mechanically checkable.

### 5. Freeze a task-free ArchitectureDraft contract with explicit module and work-package projections

`nepa/schemas/architecture-draft.schema.json` uses draft 2020-12, `additionalProperties: false` at every object boundary, and `schema_version: "1.0"`. The conforming example contains generic synthetic identifiers rather than the default protocol. Its semantic shape is:

```json
{
  "schema_version": "1.0",
  "decisions": [{"id": "...", "topic": "...", "statement": "...", "context_refs": []}],
  "assumptions": ["..."],
  "contracts": [{
    "id": "...", "purpose": "...", "owner": "s5-or-module-id",
    "interface_files": ["..."], "ready_gate": "s5-or-task",
    "provider": "s5-or-module-id", "consumers": ["module-id"]
  }],
  "modules": [{
    "id": "...", "name": "...", "purpose": "...",
    "responsibilities": ["..."], "non_goals": ["..."],
    "owns_files": ["..."], "provides_contracts": ["..."],
    "consumes_contracts": ["..."]
  }],
  "work_packages": [{
    "id": "...", "title": "...", "goal": "...", "module": "...", "kind": "...",
    "context_refs": [],
    "requirement_responsibilities": [{"req_id": "...", "role": "primary-or-supporting"}],
    "allowed_files": ["..."], "provides_contracts": ["..."],
    "consumes_contracts": ["..."], "depends_on": ["..."],
    "acceptance": {"outcome": "..."}
  }]
}
```

`context_refs` use closed `{kind, id}` objects, where `kind` is `requirement`, `message`, `type`, or `interface_file`; this avoids parsing ad-hoc string prefixes and gives `ARCH_VALIDATE` one reference resolver. A task-ready contract's scalar `provider` is its owning module; exactly one work package in that module must refine it through `provides_contracts`. An S5-ready contract uses `owner=provider="s5"` and no work package provides it. `consumers` is the module-level projection; work-package `consumes_contracts` must refine it exactly.

There are no task objects or placeholder task ids. The later Linker can convert the one provider work package into the unique provider task only after TaskPlanner expansion. Schema checks shape/enums/closed fields; cross-object set equality, ownership, DAG, and readiness remain semantic validator work.

Alternative considered: put `provider_task_id` in ArchitectureDraft. Rejected because ArchitecturePlanner is forbidden to create task ids and the provider task does not exist until M1-4b/M1-4c work-package expansion/linking.

Alternative considered: omit provider/consumer projections and infer them only from work packages. Rejected because §6.4.8.3's possible three-step ARCHITECT form must validate module/contract ownership and provider/consumer structure before the work-package step.

### 6. Map the authoritative S4-G2 contract onto ten stable, non-short-circuit gates

`validate_architecture(draft, planning_index, manifest_metadata, constraints)` accepts only a Schema-valid draft. It evaluates gates in id order and returns:

```json
{
  "schema_version": "1.0",
  "verdict": "pass-or-fail",
  "parent_refs": {"architecture_draft": {}, "planning_index": {}, "manifest_metadata": {}, "delivery_constraints": {}},
  "gates": [{"id": "arch_01", "verdict": "pass-or-fail", "issue_codes": []}],
  "issues": [{"gate": "arch_01", "code": "...", "path": "...", "message": "...", "context_refs": []}]
}
```

The mapping supplies the stable numbering implied by §6.4.8.3 without changing any gate semantics:

| Gate | Deterministic checks |
| --- | --- |
| `arch_01` | unique decision/module/contract/work-package ids; all module, contract, work-package, context, requirement, and dependency references resolve |
| `arch_02` | module purpose/responsibility/non-goal boundaries are present; module file declarations are unique, disjoint, and within `s6_owned` slots |
| `arch_03` | contract owner/ready-gate/interface conditions: S5 contracts are owned by S5 and reference only S5-ready interfaces; task contracts are module-owned and reference their owner's mutable implementation boundary |
| `arch_04` | each contract has one valid provider and valid consumer modules; task provider equals owner; S5 provider is exactly `s5` |
| `arch_05` | module `provides_contracts`/`consumes_contracts` exactly equal the contract-level provider/consumer projection |
| `arch_06` | each work package belongs to one module; allowed files are nonempty, mutually exclusive, within the module, and partition module-owned files |
| `arch_07` | work-package provide/consume sets refine module sets exactly; each task-ready contract has one provider work package; no work package provides an S5-ready contract |
| `arch_08` | `depends_on` equals cross-work-package task-ready contract dependencies, has no free ordering edge/self edge, and forms a DAG |
| `arch_09` | all `s6_owned` file slots are completely partitioned; no `s5_frozen` slot is owned; required internal-interface slots have exactly one compatible contract; all paths/kinds/mutability agree with Delivery Constraints |
| `arch_10` | every non-DEFINITION requirement has exactly one primary work package; supporting assignments are valid; every task-gated test's requirement work packages share at least one descendant closure, else `ARCH_TEST_READINESS_UNCLOSED` |

Each issue code is stable and gate-owned (`ARCH_ID_DUPLICATE`, `ARCH_REFERENCE_UNKNOWN`, `ARCH_MODULE_FILE_INVALID`, `ARCH_CONTRACT_GATE_INVALID`, `ARCH_CONTRACT_PROVIDER_INVALID`, `ARCH_MODULE_CONTRACT_SET_MISMATCH`, `ARCH_WORK_PACKAGE_FILE_PARTITION`, `ARCH_WORK_PACKAGE_CONTRACT_SET_MISMATCH`, `ARCH_DEPENDENCY_MISMATCH`, `ARCH_DAG_CYCLE`, `ARCH_DELIVERY_CONSTRAINT_VIOLATION`, `ARCH_INTERFACE_SLOT_UNCLOSED`, `ARCH_REQUIREMENT_PRIMARY_INVALID`, `ARCH_RESPONSIBILITY_INVALID`, and `ARCH_TEST_READINESS_UNCLOSED`). Issues sort by `(gate, code, path, message, canonical context_refs)`.

Gates continue after a defect when their required references remain evaluable. A gate that cannot evaluate a dependent relation because a referenced id is missing records the relevant reference issue rather than raising an internal exception. This enables honest unconditional k/N gate statistics and co-occurrence matrices.

Alternative considered: one pass/fail validator with free-form messages. Rejected because M1-4a2/3 require per-gate rates, exact repair feedback, and failure co-occurrence.

### 7. Upgrade the two generic structured-contract checks to draft 2020-12 once

`PromptRenderer._validate_contract`, `LLMClient._validate_schema`, and `structured_validation_errors` will use `Draft202012Validator` and its metaschema. The Agent preflight still happens before rendering/provider work; LLM preflight still happens before cache/provider work. Error ordering and the existing M1-2 typed `LLMRequestError`/`StructuredOutputError` boundary remain unchanged.

No compatibility shim or dual-dialect flag is added. The project already declares 2020-12 as the sole artifact dialect, and all existing bundled schemas declare that dialect. Existing cache identity remains the canonical schema bytes; a schema/value pair that was valid under both dialects produces the same key and behavior.

Alternative considered: validate ArchitectureDraft separately with 2020-12 but leave M1-2/M1-3 on Draft 7. Rejected because the provider response returned to calibration could then be accepted by a different dialect than production `ARCH_VALIDATE` expects.

### 8. Add a required repair-context input without adding an Agent-layer retry

The ArchitecturePlanner role registration changes from two inputs to three: `planning_index`, `delivery_constraints`, and `repair_context`. The template adds one explicit delimiter in its Inputs section. Initial calls pass canonical `null`. A semantic repair passes exactly:

```json
{
  "previous_candidate": {"...": "Schema-valid ArchitectureDraft"},
  "validation_issues": [{"gate": "arch_..", "code": "...", "path": "...", "message": "...", "context_refs": []}]
}
```

The driver, not `AgentInvoker`, decides whether another semantic call is allowed. Every call still invokes `AgentInvoker.invoke` once; `LLMClient.complete` alone retains the optional single format-repair call. No hidden message history is carried between semantic calls.

This edit is structural scaffolding required by M1-4a1. It does not add a few-shot example, model-specific instruction, gate-threshold advice, or any V0/V1/V2 optimization. M1-4a2 may edit the shared template text but cannot change the input names without creating a new lineage.

Alternative considered: hide the prior candidate and issues inside `planning_index`. Rejected because it would corrupt the meaning and hash of a frozen production input and make initial versus repair context unauditable.

### 9. Route each calibration candidate by an immutable derived configuration

For each key in the exact `calibration_models` set (`claude`, `qwen`, `deepseek`), the driver creates a deep, validated public-config copy and overrides only `roles.architecture_planner.provider/model/temperature/max_tokens` with that calibration target. The original `ResolvedConfig`, tier bindings, formal production role route, provider endpoint, and all other roles remain unchanged. A separate `LLMClient`, provider adapter set, `AgentInvoker`, telemetry instance, and model-root artifact store are constructed per model.

The batch declaration additionally supplies a positive `context_window_tokens` for each exact returned model target. These limits are comparison inputs recorded in lineage; they are not inferred from model names or the network. Preflight renders the initial prompt with ArchitectureDraft Schema/example and uses `len(prompt_utf8_bytes)` as a conservative input-token upper bound. It requires:

```text
input_byte_upper_bound + requested_max_output_tokens
    <= floor(context_window_tokens * (1 - context_safety_margin_ratio))
```

Using one byte as at most one token is intentionally conservative and provider-neutral; it can reject an otherwise fitting prompt but cannot justify silent truncation. The exact provider-reported token count remains the measurement in trial evidence.

Alternative considered: infer context limits from provider/model names. Rejected because the design forbids implicit model behavior and provider versions can change. Alternative considered: add model-specific tokenizer dependencies. Rejected for M1-4a1 because they would introduce three non-equivalent estimators and a large new dependency surface; a future change may replace the conservative estimator only by creating a new lineage.

### 10. Reuse M1-1 artifact primitives without creating a formal Run store

Each lineage/model root uses the confined atomic/immutable JSON/bytes and trace primitives already implemented by `RunStore`, but never calls `initialize_spec_run`, `load_run`, `replace_run`, formal stage transitions, budget policy, or frozen-run verification. No `run.json` is created. This allows the unchanged `LLMTelemetry` and `LLMCache` interfaces to write under a model-specific root without duplicating path confinement, hashing, `fsync`, or call evidence.

`batch.json` is an immutable declaration: lineage/prompt/model refs, logical prompt version, trial count, allowed semantic depth, context limit, and trial ids. A trial is assembled in a sibling staging directory after all of its LLM trace evidence exists; `request_ref.json`, `response_ref.json`, and `validation.json` are written and fsynced there, then the complete `trial_NNN` directory is atomically renamed into place. The directory rename is the trial commit point. A committed trial is reusable on restart only after all refs and parent hashes verify. Orphan prompt/output/trace evidence from an interrupted uncommitted trial is retained for audit but ignored by aggregation.

After all declared trials are committed and none is infrastructure-invalid, `calibration_report.json` is published immutably. Re-running aggregation must reproduce its bytes. No timestamp or current environment value enters lineage, batch, validation, or aggregate JSON; measured provider latency/model metadata remain fixed because they are read from the committed trace.

If infrastructure failure invalidates a batch, its evidence remains immutable and no report qualifies it as complete. A mandated whole-batch rerun uses a new attempt-qualified directory key while preserving the logical V0/V1/V2 label and raw prompt hash; evidence from the invalid attempt is never moved into the replacement report.

Alternative considered: add a second bespoke calibration filesystem store. Rejected because it would duplicate the M1-1 persistence path. Alternative considered: write directly into final trial directories. Rejected because a crash could leave a directory that looks complete but lacks validation or response refs.

### 11. Parallelize only the three model batches and keep trials serial within each model

The driver submits exactly three top-level model workers. Each worker owns its client/store and executes trial ids in ascending order; it never shares an object that carries session, cache, call-sequence, or mutable trace state. The three workers may overlap wall time, satisfying the calibration meaning of “parallel,” while trials inside one model remain serial for simple rate-limit behavior and deterministic evidence indexing.

Every Agent call uses:

- `stage="S4"` (the registered role stage, not a formal stage receipt);
- a calibration-qualified `run_id` containing lineage/prompt/model identity;
- `task_id="trial_NNN"`;
- `attempt=semantic_depth + 1`;
- `use_cache=False`.

Format repair stays inside the same logical trace row. Semantic repair creates the next trace row with the same trial id and incremented attempt. A worker reads only its own latest committed trace row to build request/response indexes.

Alternative considered: run all trials concurrently. Rejected because M1-4a1 requires only three-model isolation, while within-model concurrency would add rate-limit scheduling and ordering complexity without changing the measured experiment.

### 12. Make trial validation the only source for report aggregation

`validation.json` records, for the initial call and each permitted semantic repair:

- whether the raw response was Schema-valid;
- whether M1-2 format repair was used and whether a Schema-legal candidate exists;
- the candidate ref and complete `arch_01`-`arch_10` result ref;
- provider call count, token/cost/latency, returned model identity, finish reason/truncation, parameter-support states;
- first passing semantic depth or terminal failure classification.

The aggregator never re-asks a model or infers success from prose. It revalidates refs, optionally reruns deterministic Schema/`ARCH_VALIDATE` to prove the recorded result, and calculates:

- raw Schema and post-format-repair rates;
- raw architecture first-pass, semantic first-pass, and cumulative `p0/p1/p2`;
- unconditional gate k/N and failure co-occurrence;
- format/semantic repair consumption and marginal gains;
- call/token/cost/latency and model/parameter/finish-reason distributions.

All success denominators are the declared N. Schema double-failure is a model failure, not a missing sample. Transport exhaustion with no model response is an infrastructure-invalid batch, not a zero-quality trial. If the batch declared at most one semantic repair, `p2` is `{value: null, reason: {code: "SEMANTIC_DEPTH_NOT_DECLARED"}}`.

`arch_semantic_first_pass_rate` and `p0` are intentionally equal by definition: both mean the first Schema-legal candidate passes before semantic repair. Both keys are emitted because §6.4.8 requires the named semantic first-pass metric and the cumulative p-series.

Alternative considered: aggregate directly from `llm_calls.ndjson`. Rejected because trace owns provider-call facts but does not encode the deterministic architecture gate outcome or trial-level semantic-repair chain.

### 13. Enforce neutrality at source and derived-artifact boundaries

The existing Agent neutrality test is expanded to the new ArchitecturePlanner `repair_context`. A planning-specific scanner checks shared planning/calibration Python, all planning/calibration Schema/examples, and the ArchitecturePlanner template for the §8.8 prohibited protocol vocabulary and protocol-identity branches. The template scan additionally forbids provider/model names. Configuration files are not mistaken for prompt source: they necessarily contain the three model bindings.

A compact synthetic application-layer fixture uses the existing supported server/C99 rule but supplies non-default protocol/type/message/requirement names and no target-protocol constants. It passes input preparation, planning-index build, Delivery Constraints compile, a valid ArchitectureDraft, all ten gates, lineage generation, and prompt rendering through the same functions used for the signed gold input. Assertions prove any concrete symbol/path in output can be replayed from fixture fields or documented language/role rules.

This fixture proves source neutrality and mechanical attribution only. It does not claim end-to-end support for another protocol, which remains M6 work.

Alternative considered: token scan alone. Rejected because it can catch embedded constants but cannot prove that derived paths and prompt identifiers came through the input path.

### 14. Keep the formal Run, report, and downstream admission boundaries unchanged

Calibration paths are explicitly excluded from `nepa eval runs` discovery and never have a `run.json`. There is no conversion operation from ArchitectureDraft to Plan in this change. Later M1-4b consumes the Schema/validator/constraints functions as code dependencies, not an arbitrary calibration candidate file. Later M1-4c alone may create `_s4` checkpoints and seal a Plan after all required stages/gates.

No formal Run/Report Schema needs migration. Existing M1-2 cache files and trace records remain readable; draft-dialect behavior changes only when validating a supplied schema/value and is covered by regression tests.

## Derived Implementation Brief (system design §10.8)

1. **Input artifacts and Schema references:** Spec IR v3 (`nepa/schemas/specs-requirements.schema.json`, §5.1); closed Target Profile (§5.6.5.1 plus existing target lint); Test Bundle v3 (`nepa/schemas/test-bundle.schema.json`, §5.3); resolved configuration `calibration_models`, provider/pricing, planning margin (§8.3); raw ArchitecturePlanner template and explicit repair context (§8.8, §6.4.8).
2. **Output artifacts and acceptance commands:** `lineage.json`, frozen inputs, `planning_index.json`, `test_manifest_metadata.json`, `delivery_constraints.json`, ArchitectureDraft/validation/calibration Schemas and examples, model `batch.json`, committed trial indexes/validation, trace/cache evidence, and recomputable `calibration_report.json`; verify with focused pytest files for planning/constraints/architecture/calibration/neutrality plus `uv run pytest -q` and `openspec validate --all --strict`.
3. **Required function/class signatures:** `prepare_architecture_inputs(spec, target_profile, test_bundle) -> PreparedArchitectureInputs`; `build_test_manifest_metadata(bundle, constraints) -> dict`; `build_planning_index(prepared, manifest_metadata, constraints) -> dict`; `compile_delivery_constraints(spec, target_profile) -> dict`; `validate_architecture(draft, planning_index, manifest_metadata, constraints) -> dict`; `build_lineage_manifest(...) -> dict`; `recompute_calibration_report(model_root) -> dict`; `ArchitectureCalibrationDriver.run(declaration) -> Mapping[str, ArtifactRef]`.
4. **Referenced chapters:** §0.1/§0.3, §4.2/§4.5/§4.6, §5.1/§5.2.1-§5.2.3/§5.3/§5.5/§5.6.5, §6.4/§6.4.1/§6.4.3/§6.4.4/§6.4.8, §8.1-§8.4/§8.8, §9.1.5/§9.2, §10.2 M1-4a1 and D1.0/D1.11, §10.8, §11.3.

## Risks / Trade-offs

- [The conservative byte-count preflight may reject a prompt that a specific tokenizer could fit] → Prefer an honest false rejection to silent truncation; record the estimator and context limits in lineage so a later approved estimator change cannot mix data.
- [The exact internal-interface constraint projection may expose a contradiction between §5.6.5 and the current C99/server rule while implementing] → Stop before changing `project_docs/system_design.md`; report the concrete field/path conflict and obtain explicit authorization if the authoritative design itself must change.
- [Hashing component source bytes creates a new lineage for comment-only or non-semantic edits] → Accept conservative invalidation; it prevents accidental mixing and does not alter runtime behavior.
- [Using generic `RunStore` artifact primitives under a non-Run root relies on callers avoiding Run-specific methods] → Keep construction private to the calibration module, expose only the driver, and test that no `run.json`/stage transition method is called.
- [Three concurrent provider batches can encounter rate limits at different times] → Reuse M1-2 bounded retries, keep within-model trials serial, and classify retry exhaustion as infrastructure-invalid rather than changing sample membership.
- [The M1-4a1 structural template edit could be mistaken for prompt optimization] → Limit it to the new delimited input and generic repair-context rule; acceptance scans forbid model/protocol tuning and this change runs no quality-selection protocol.
- [Draft-2020-12 validation may reject a schema that Draft 7 previously accepted] → This is the authoritative dialect correction; run all M1-2/M1-3 regressions and do not add dual-dialect fallback.
- [Calibration evidence can be confused with formal S4 success] → No `run.json`, receipt, Plan, Blueprint, Report v2, CLI run outcome, or downstream conversion exists in this change.

## Migration Plan

1. Switch the two generic Schema validation paths to draft 2020-12 and run the existing M1-2/M1-3 structured-output and rendering regressions.
2. Add ArchitectureDraft, validation, lineage, batch/trial, and calibration-report Schemas with generic minimal examples.
3. Add pure planning-index, manifest-summary, Delivery Constraints, and `ARCH_VALIDATE` modules and validate them against gold plus the synthetic non-MQTT fixture.
4. Add the ArchitecturePlanner `repair_context` registration/template boundary and prove initial/repair rendering without any prompt-quality change.
5. Add lineage publication, per-model derived routing/store/client isolation, atomic trial commits, restart verification, and report recomputation with fake providers.
6. Run focused tests, the full repository suite, protocol-neutral scans, Schema/example checks, and strict OpenSpec validation; inspect the diff for any a2/a3/4b/4c behavior or protocol constant.

Rollback removes only the new planning/calibration modules/Schemas and the ArchitecturePlanner repair-context input, and restores the prior generic validator imports if the entire change is abandoned. Because M1-4a1 publishes no formal run or downstream artifact and calibration roots are gitignored, no production data migration is required. Any calibration evidence created by a reverted implementation remains non-consumable and its lineage prevents reuse after code restoration.
