## Context

See `proposal.md` for motivation and the inseparability of M1-4b/M1-4b2. The authority for this design is `project_docs/system_design.md` 7.1.0 §5.2, §5.6.5.2-§5.6.5.3, §6.4.1, §6.4.5 and §10.2, together with `project_docs/pipeline_design_s4_s9.md` 1.2.0 §5.2.2/§5.2.5.

Confirmed repository facts:

- `nepa/speclib/delivery.py` already owns identifier normalization, Target validation, layout-convention selection, and the M1-4a1 `compile_delivery_constraints` path. It emits the current C99/server naming, resource, build-variant, convention, ABI, and mechanical-contract projection.
- `nepa/speclib/architecture.py` already validates and expands the free-layout ArchitectureDraft without creating a Blueprint. M1-4b must consume that accepted shape rather than duplicate `ARCH_VALIDATE`.
- No complete Delivery Blueprint compiler, Plan/Plan State Schema, deterministic Linker, plan lint module, state validator, or `nepa lint plan` command exists.
- M0 and M1-4a1 are archived. M1-4a2 is also archived with an owner-approved M1-4c handoff, but this deterministic change does not consume a model or make provider calls.

The change spans schemas, compiler code, validators, CLI, and tests because its smallest usable boundary is input contracts → deterministic compilation → validated outputs. It introduces no external dependency and no stateful stage controller.

## Goals / Non-Goals

**Goals:**

- Complete the single Delivery Compiler path from frozen constraints plus accepted free layout to a canonical Blueprint.
- Implement the approved eight-row M1-4b2 layout-to-file-rule mapping without heuristic inference.
- Normalize task shards into PlanDraftIR, deterministically link the final task graph and coverage, then bind the exact Blueprint and frozen inputs into Plan v4.
- Provide closed schemas/examples and side-effect-free basic/full Plan plus snapshot/transition/execution-state validators.
- Preserve deterministic replay, current validation report conventions, controlled-error semantics, and protocol neutrality.

**Non-Goals:**

- ArchitecturePlanner, TaskPlanner, PlanCritic, or FlatPlanBaseline invocation and repair loops.
- S4 checkpoint/resume, plan version publication, `active_plan.json`, revision-ledger genesis, or S4 receipt sealing; those belong to M1-4c/M1-4d.
- M1-4d-owned `task_uid`, `obligation_digest`, and `guidance_digest` calculation or migration semantics; this change leaves the later Linker extension point within the same module.
- Plan State mutation/persistence, S6 admission, commit reconciliation, Coder/Fixer execution, or revision/lease controllers.
- S5 templates/materialization, artifact manifest, contract map, workspace git commits, build/smoke execution, or M2-0 public test binding.
- New language/role support, new resource-to-Spec mappings, new placeholders, new Blueprint fields, new hash fields, or a protocol-specific compatibility path.

## Decisions

### 1. Extend the existing Delivery Compiler instead of introducing a second compiler

`nepa/speclib/delivery.py` remains the sole owner of `compile_delivery_constraints` and gains the complete `compile_delivery_blueprint(constraints, architecture, work_packages, tasks)` half. Existing naming, resource, Target, and layout-convention helpers are reused. Small private functions may isolate table lookup, path expansion, build-graph projection, and mechanical-contract closure only where each is reused or materially clarifies the compiler; no generic compiler framework is introduced.

The Blueprint operation accepts already parsed mappings and returns one in-memory object. It does not persist files or read the run/workspace. Its only repository-owned knowledge is the existing built-in application-layer rule material already projected through Delivery Constraints. Error values extend the existing deterministic delivery error family with stable codes and canonical paths.

Alternative considered: create a separate layout-transcriber package and merge its output later. Rejected because M1-4b2 has no valid standalone output, would duplicate validation boundaries, and could permit a Blueprint path that bypasses the approved mapping.

### 2. Make the approved tuple table the only `kind`/`producer` decision point

The transcriber indexes a literal closed table by `(render_rule, class, contract_id is not null, build_role)`. The eight values are copied exactly from pipeline design 1.2.0 §5.2.2. A miss is an error; no fallthrough, suffix inspection, path classification, or module/protocol lookup exists.

For each slot the compiler then derives:

- `id` from `slot_id`;
- `mutability` from `class`;
- `path_pattern` from the sole non-null path field;
- `expansion` from the exact path/expand-over triple;
- `kind` and `producer` from the closed tuple table;
- owner/contract associations from the already linked architecture/tasks, never from filenames.

The implementation first validates and normalizes all slots, then checks the complete concrete-path set, then produces the sorted `file_rules[]`; it returns no partial object on failure. Table-complete parameterized tests cover all eight legal rows and representative invalid rows explicitly named by the design.

Alternative considered: derive by `render_rule` first and refine by file extension. Rejected by design 7.1.0 because `mechanical` and `source_stub` are intentionally non-unique and extensions are not semantic inputs.

### 3. Keep expansion and build-graph projection inside the same atomic Blueprint calculation

Path expansion reuses `normalize_identifier` and the Spec-derived ids already present in Delivery Constraints. Literal, per-message, and per-type forms are closed cases. Message selection follows target-role sender/receiver intersection; source ids are ordered by UTF-8 bytes before replacement. Type expansion uses all declared types in the same ordering. A normalized-id collision, empty required domain, invalid placeholder count, unsafe path, or duplicate concrete output fails the whole compile.

The validated layout graph is transcribed into the fixed deliverable → build artifact → link source set shape. Logical ids are explicit; collection output is sorted by id, while each source-set membership is sorted by referenced file-rule id. Entry files and link sources must resolve through the slot map. The compiler verifies one-to-one file transcription, build-output uniqueness, deliverable coverage, source-set non-emptiness, and mechanical-rule-to-contract uniqueness before canonical output.

Alternative considered: trust `ARCH_VALIDATE` and copy fields without checking. Rejected because S4-G1 independently proves faithful Blueprint transcription and catches implementation drift without re-deciding architecture quality.

### 4. Represent PlanDraftIR as normalized data and keep the Linker purely deterministic

Add `nepa/speclib/plan.py` as the focused home for task-shard normalization, linking, coverage construction, Blueprint binding, and Plan lint. PlanDraftIR is a closed, state-free intermediate mapping built from one accepted architecture plus exactly one valid shard per work package. It retains local task ids until linking and cannot contain final ids, coverage, hashes, or execution state.

The Linker runs in this order:

1. validate schemas and exact architecture/work-package/shard membership;
2. check responsibility, contract, and file set equalities;
3. resolve unique task-ready contract providers and add only provable edges;
4. reject both DAG cycles, then Kahn-sort tasks with `(work_package.id, local_task_id)` UTF-8 ordering;
5. assign `T-###`, rewrite references, and inject configured build variants plus responsibility context refs; M1-4d later extends this same step with task identity/migration digests;
6. generate coverage from Spec responsibilities, final DAG, Test Manifest, and layer switches;
7. require empty task test acceptance before M2-0;
8. call the one Blueprint compiler with the fully injected final task view;
9. inject controller-supplied input refs and the canonical Blueprint hash, validate the resulting Plan, and return Plan plus a deterministic link report.

No Agent is called and no semantic repair occurs. Failures return stable, canonically ordered issues to a future controller, which owns any rerouting.

Alternative considered: allocate ids per shard before merging. Rejected because array/input order would leak into ids and contract-injected cross-package edges could change the valid topology.

### 5. Separate basic Plan lint from full stage readiness

`plan_lint` exposes an explicit level rather than silently weakening missing checks:

- basic consumes Plan, Spec, Test Manifest, and config snapshot and checks the shape/reference/DAG/responsibility/contract/coverage subset defined in §5.2.5;
- full additionally consumes Target Profile, Delivery Constraints, and Delivery Blueprint and checks faithful layout projection, file ownership/class, build variants, provider ancestry, readiness, and configured context/output budgets across S4-G0 through S4-G6.

Reports use the existing `{valid, errors, warnings}` envelope with stable code/path/message entries. Sorting is by gate, code, path, and message. The CLI delegates directly to this API. `nepa lint plan <plan>` performs basic lint with explicit companion inputs; `--run-dir` resolves the frozen companions required for full lint. Invalid user artifacts return the existing controlled validation exit 20; unexpected implementation failures remain exit 1.

Alternative considered: auto-upgrade to full when some files happen to exist. Rejected because it would make the claim depend on ambient filesystem state and could label a partial check as S4 acceptance.

### 6. Keep Plan State validation in a separate, side-effect-free module

Add `nepa/speclib/plan_state.py` for three functions matching §5.2.5:

- snapshot lint checks the closed Plan State object, Plan/seal/config bindings, task set, attempt limit, and per-status field table;
- transition validation receives complete old State plus a typed event and derives/compares the unique next State; it never accepts an arbitrary caller-authored replacement;
- execution lint receives explicit workspace/evidence/receipt adapters or paths and verifies commits, trailers, ancestry, evidence bytes, acceptance, and S5 anchors without mutating them.

This module may validate the revision/lease event shapes required by the current state table, but it does not implement M1-4d/M1-4e controllers, ledger append, active-version changes, locks, atomic writes, or resume. Tests use temporary repositories/evidence stores only to supply read-only execution facts.

Alternative considered: combine all checks in one JSON validator. Rejected because snapshot validation cannot honestly assert git, filesystem, or receipt facts and would blur the trust boundary.

### 7. Add only the contracts required by this closed loop

Add draft-2020-12 schemas and matching minimal examples for Delivery Blueprint, task shard/PlanDraftIR as required by the Linker boundary, Plan v4, Plan State v1, and any typed Linker/state-event report that is persisted by this work. Each schema is closed with `additionalProperties: false` where the design defines a closed object and uses existing shared shapes when they already exist. This change does not add schemas for future controller checkpoints, revision ledgers, S5 manifests, or task evidence beyond the references Plan State must validate.

Schema/example registration extends the current mutual-validation test rather than creating a new registry. The implementation must not add a digest field that is absent from design; existing required content refs and the Blueprint hash are implemented exactly where specified.

## Risks / Trade-offs

- **[The Linker and Blueprint compiler can form an accidental hash cycle]** → Compile the fully injected semantic task view first, hash the resulting Blueprint once, then inject that hash into Plan; never pass the Plan's Blueprint hash back into the Blueprint compiler.
- **[Layout validation may be duplicated and drift from `ARCH_VALIDATE`]** → Reuse accepted architecture structures and check only S4-G1 transcription/closure facts; do not reimplement advisory/hard architecture quality gates.
- **[Stable task ids can depend on incidental array order]** → Normalize sets before edge construction and use the exact Kahn ready-queue key with permutation tests.
- **[Basic lint can be mistaken for publication readiness]** → Put the level in the report and make full readiness unavailable unless every full input is explicit.
- **[State validators could pull M1-4d/M1-4e into scope]** → Validate current contract/event shapes only; no version migration, ledger mutation, lease issuance, persistence, or orchestration is implemented.
- **[The change necessarily spans more than three files]** → The spread is contract-driven: separate schema/example assets, the existing Delivery Compiler, two cohesive validation modules, CLI wiring, and their tests are the minimum end-to-end boundary. No unrelated refactor or dependency is included.

## Migration Plan

1. Add and validate schemas/examples without changing existing runtime behavior.
2. Extend Delivery Constraints only where design-required fields are missing, preserving current callers and outputs otherwise.
3. Add Blueprint compilation and prove the complete mapping/expansion/build-graph path with deterministic fixtures.
4. Add PlanDraftIR/Linker/coverage/Plan lint, then Plan State validators.
5. Wire the CLI after the APIs are stable and run focused, gold, neutrality, strict OpenSpec, and full regression gates.

There is no persisted production data migration because no formal Plan/Plan State has yet been published by the repository. Rollback is removal of these new unconsumed contracts and restoration of the Delivery Constraints extension; no existing run or historical calibration evidence is rewritten.
