## Context

See `proposal.md` for motivation. M1-4a1 is archived at `c949c21` and already supplies the production-shaped input builder, shared ArchitectureDraft contract, `ARCH_VALIDATE`, isolated Qwen and DeepSeek workers, immutable trial evidence, and per-model report recomputation. Its current ArchitecturePlanner template is intentionally only a skeleton, and its lineage projection currently treats `trial_count` and `semantic_depth` as statistical identity.

M1-4a2 must exercise the authoritative §6.4.8.2 sequence: V0 N=5, one focused V1, optional focused V2, and an evidence-gated expansion of V1/V2 from N=5 to N=10. M1-4a3 must later use N=20 and two semantic repairs while retaining the controlled lineage selected in M1-4a2. The user explicitly authorized resolving that conflict by treating sample count and repair depth as immutable batch-protocol controls rather than `lineage_id` inputs. The authorized `project_docs/system_design.md` v3.0.0 update defines Qwen/DeepSeek as the calibration set and fixes `max_tokens = 65536`; this change must implement that new lineage-controlled configuration.

The live prompt content after V0 cannot be planned in advance: V1 and V2 are permitted only when actual per-gate evidence supports one testable defect hypothesis. Deterministic code owns admission, evidence binding, sample budgets, screening, selection, persistence, and stopping. The implementation agent or responsible developer supplies the human-readable hypothesis and prompt edit; no new LLM role is introduced to optimize or judge the prompt.

## Goals / Non-Goals

**Goals:**

- Correct the lineage projection without weakening immutable batch/report binding.
- Make the real-calibration entry point executable only from an explicit complete non-secret configuration, explicit two-model context limits, and the two non-empty §8.3 fixed key variables in the current process environment.
- Reuse the exact M1-4a1 trial and validation path for every development sample.
- Make V0/V1/optional-V2 ordering, N=5/N=10 budgets, one-repair depth, revision evidence, screening, and fallback selection mechanically auditable.
- Make every base N=5 run one attempt-qualified two-model unit with deterministic invalidation, retry, and resume.
- Render every provider request from one immutable admitted prompt snapshot and detect source/snapshot/trace drift.
- Preserve the first five trials when an authorized N=10 expansion is needed.
- Finish with repository prompt bytes equal to the selected evidence-bound candidate.
- Produce complete real Qwen/DeepSeek development evidence before M1-4a2 can be marked complete.

**Non-Goals:**

- Do not add a public NePA CLI; M1-7 still owns CLI work. A narrow Python API or module runner for calibration development is sufficient.
- Do not change ArchitectureDraft, `ARCH_VALIDATE`, input construction, model routing, production call shape, or metric formulas to improve observed results.
- Do not invoke TaskPlanner, PlanCritic, FlatPlanBaseline, Linker, formal S4, or downstream stages.
- Do not run N=20, perform B1-B4 qualification, select a production model, freeze production budgets/call shape, or create an owner signature.

## Decisions

### 1. Remove batch protocol controls from lineage identity, not from evidence

`build_lineage_manifest` will continue to hash the frozen inputs/artifacts, Schema/example, serializer, planning/Delivery/validator/Agent code, metric implementation, explicit resolved provider/pricing projections, the two exact requested model/parameter configurations, and their explicit context-window declarations. Its `statistics` projection will identify the metric-definition contract and implementation hash, but will no longer contain `trial_count` or `semantic_depth`.

Every immutable `batch.json` still requires `trial_count`, `semantic_depth`, exact trial ids, requested parameters, lineage id, prompt version/hash, provider, and model. Batch/report reload continues to verify those values against the actual committed trials. A normal model report aggregates exactly one batch. The prompt-development layer is the only M1-4a2 path allowed to combine the base N=5 batch with its explicitly bound five-trial extension; it never combines semantic depths or unrelated batches.

Changing the lineage projection invalidates reuse of any lineage produced by the old projection. Existing roots remain untouched for audit; V0 starts by creating a new lineage with the corrected projection. There is no migration or reinterpretation of old evidence.

Alternative considered: keep N/depth in lineage and compare V0/V1/V2 across multiple lineages. Rejected because M1-4a1 forbids treating cross-lineage data as a prompt-only experiment and M1-4a3 is required to reuse the controlled lineage. Alternative considered: exclude all statistical controls. Rejected; metric formulas, screening definitions, and their implementation remain controlled and hash-bound.

### 2. Add one narrow prompt-development module over the M1-4a1 primitives

Add `nepa/calibration/s4_prompt_development.py` rather than forking the trial engine or expanding formal orchestration. It will:

- validate and publish the development protocol;
- admit a base version or an authorized extension;
- call the existing ArchitectureCalibrationDriver/trial binding for provider work;
- recompute every referenced M1-4a1 report before use;
- produce cross-model version assessments;
- validate revision and ambiguity records;
- apply screening and final selection; and
- publish immutable development records.

The module exposes a narrow internal `python -m nepa.calibration.s4_prompt_development` runner for `init`, `run-version`, `record-revision`, `expand`, and `recompute` operations so the live protocol has reproducible commands. `init` requires an explicit resolved-config source and an explicit context-limits source; it has no implicit `configs/default.yaml` fallback. This is calibration tooling scoped to the gitignored evidence root, not a public `nepa` CLI surface; M1-7 remains unchanged.

Shared metric accumulation should be factored only as needed so base N=5 reports and N=10 development reports use the same formulas. No parallel validator, candidate parser, trace loader, or provider path is allowed. The new module's source hash and its closed Schemas are recorded in `prompt-development/protocol.json`; changing them after V0 invalidates that development sequence.

Alternative considered: drive the sequence with an unvalidated shell notebook and manually inspect two reports. Rejected because version ordering, evidence selection, sample budgets, and the stop rule would not be machine-recomputable. Alternative considered: put the coordinator in the formal Run orchestrator. Rejected because calibration is explicitly outside formal S1-S9 runs.

### 3. Use append-only development records below the existing lineage root

The development evidence layout is:

```text
runs/_calibration/s4-architecture/<lineage_id>/
├── lineage.json
├── v0|v1|v2/<model_id>/...          # attempt 001 M1-4a1 model roots
├── v0|v1|v2/attempt_NNN/<model_id>/... # later whole-version attempts
└── prompt-development/
    ├── protocol.json
    ├── versions/
    │   └── v0|v1|v2/
    │       ├── prompt.md
    │       ├── version.json
    │       ├── revision.json         # V1/V2 only
    │       ├── attempts/
    │       │   └── attempt_NNN/
    │       │       ├── declaration.json
    │       │       └── outcome.json
    │       ├── assessment-n005.json
    │       ├── extension.json        # only when N=10 is admitted
    │       ├── extensions/
    │       │   └── n010/<model_id>/
    │       │       ├── batch.json
    │       │       ├── trials/trial_006..010/...
    │       │       └── calibration_report.json
    │       ├── assessment-n010.json  # only after the extension
    │       └── outcome.json
    └── selection.json
```

`protocol.json` fixes the lineage, canonical configuration/context-limit refs, provider/pricing/model/parameter projections, model ids, permitted versions, development semantic depth, N=5/N=10 rules, screening definitions, lexicographic fallback order, and component/schema refs before V0. `version.json` binds the immutable raw prompt ref/hash. Each attempt declaration binds that version/prompt/protocol and exactly two model roots; its outcome binds the two results and terminal state. `assessment-n005.json` references exactly one complete valid attempt. `revision.json` is published before V1/V2 calls; the version-level `outcome.json` records the post-run conclusion separately so an immutable pre-run revision never needs rewriting. The pair satisfies the required modification record.

All paths are relative, confined, and SHA-256 bound. Files are canonical except raw `prompt.md` and the exact unified prompt diff. A record is staged, fsynced, and atomically renamed/published only after all parents verify. Replaying byte-identical content is a no-op; conflicting content fails. `selection.json` is the final development commit point. Resume scans only committed records, verifies every parent and attempt state, and returns exactly one of: resume the unique incomplete non-invalid attempt, create the next attempt after a committed invalid attempt, admit the next version/extension, or report a terminal selection. Orphan staging or provider trace evidence remains non-normative and is never used for screening.

Alternative considered: update one mutable `development.json`. Rejected because a crash or later edit could rewrite the hypothesis and stopping rationale after seeing results.

### 4. Preserve N=5 and append trials 006-010 for an authorized N=10 assessment

Every version first uses the normal M1-4a1 N=5 batch. If V1 or V2 has an admissible ambiguity, `extension.json` binds the two base batches/reports, the unchanged prompt hash, and the exact reason:

- `single_sample_sensitive`: recomputed leave-one-trial-out results change a numeric screening pass/fail conclusion; or
- `metric_conflict`: the declared numeric screening metrics yield conflicting pass/fail indications for at least one model.

The extension then runs exactly five fresh samples per model with ids 006-010, the same lineage/prompt/configuration, `semantic_depth=1`, independent sessions, and cache disabled. An extension model report references both the original 001-005 evidence and new 006-010 evidence, rejects duplicate hashes/ids or replacements, and recomputes all headline/gate/usage fields with denominator ten. The final version assessment uses the two N=10 reports and supersedes N=5 only for screening/selection; it does not erase the N=5 trigger evidence.

V0 is never expanded because §6.4.8.2 authorizes ambiguity expansion only for V1/V2. An infrastructure-invalid extension invalidates the two-model extension attempt and follows the same explicit attempt declaration/outcome and whole-two-model rerun rule as a base N=5 attempt; no individual replacement sample is drawn and no extension report crosses attempts.

Alternative considered: discard N=5 and run a fresh N=10 batch. Rejected because that performs fifteen samples per model, loses the meaning of “expand,” and creates an avoidable opportunity to cherry-pick. Alternative considered: mutate the N=5 `batch.json` to N=10. Rejected because M1-4a1 batch declarations are immutable.

### 5. Separate deterministic admission from evidence-based prompt judgment

V0 uses the current shared template as baseline, but admission first snapshots its exact bytes under the development root and every call renders from that snapshot. After a complete assessment:

1. If it passes screening, publish `selection.json` for V0 and stop.
2. Otherwise, V1 requires one supplied hypothesis tied to exact failing trials/gates/metrics. The coordinator computes the unified diff and both prompt hashes, verifies that only `architecture_planner.md` template bytes changed, runs neutrality checks, and publishes `revision.json` before provider calls.
3. Assess V1 at N=5, expand to N=10 only under Decision 4, then either select it or admit V2 from one second distinct evidence-backed hypothesis.
4. Apply the same assessment/optional-expansion logic to V2. If it passes, select it; otherwise apply the fixed fallback ranking.

The hypothesis is a single closed record with one statement, evidence refs, and expected affected gates/metric names. Deterministic code can verify cardinality, refs, diff scope, and that cited failures exist; it does not claim to prove that natural-language causal reasoning is true. The later observed outcome reports whether the expected gates/metrics improved and records the required stopping conclusion.

No optimization LLM is invoked. The only provider calls remain fresh ArchitecturePlanner trials plus the M1-2 format-repair path and the one allowed semantic repair.

### 6. Recompute screening from trial evidence, independently for each model

For each version, the assessment first identifies one complete valid two-model attempt, then reloads that attempt's final sample set and recomputes rather than trusting copied summaries. Cross-attempt report or trial refs are rejected. Each model passes only if:

- `schema_after_format_repair_rate == 1.00`;
- cumulative `p1 == 1.00`;
- `arch_semantic_first_pass_rate >= 0.80`;
- aggregate truncation count is zero;
- the base/extension batch is complete and not infrastructure-invalid; and
- no `arch_01`-`arch_10` gate fails on the first Schema-valid candidate in at least two trials for the same model/version.

The last condition is the explicitly authorized M1-4a2 interpretation of “the same hard gate repeatedly fails systematically”: “repeated” means at least two observed first-candidate failures for one model/version. It is evaluated before semantic repair, because final gate failure is already excluded by `p1 == 1.00`. The assessment stores per-model booleans and exact failing trial ids; no cross-model mean can substitute for an individual result.

Full returned provider/model/version identity and parameter-support state are compared across V0/V1/V2 for each configured model. A change during the experiment invalidates prompt-only attribution and blocks screening/selection; it is not hidden by identical requested model names.

The first final assessment that passes both models publishes selection immediately. Subsequent revision, extension, or trial admission checks for `selection.json` and fails before provider I/O.

### 7. Apply only the authoritative fallback tuple after a complete failing V2

If no version passed and V2 is complete, compare each version's final assessment using the exact tuple:

```text
(
  min_model_p1,                         # maximize
  min_model_arch_semantic_first_pass,   # maximize
  min_model_schema_after_format_repair, # maximize
  -total_two_model_cost               # maximize == lower cost wins
)
```

No averages, confidence claims, significance tests, extra weights, or extra sampling enter the decision. The selected tuple and every competing tuple are written to `selection.json`. If the complete tuple is exactly tied, the coordinator emits `PROMPT_SELECTION_TIE` and stops without inventing a fifth criterion; this is a controlled unresolved design case rather than a silent reinterpretation.

The repository copy of `nepa/agents/prompts/architecture_planner.md` must be byte-identical to the selected `prompt.md` before M1-4a2 completion. If fallback selects an earlier version, restore that exact evidence-bound content through the normal source edit and verify the hash. Intermediate template bytes remain available under the gitignored evidence root.

### 8. Keep the shared prompt protocol-neutral and contract-shaped

The optimized prompt retains the fixed five-section M1-3 structure and the two M1-4a1 input delimiters. It may make the ArchitectureDraft responsibilities, dependency/file/contract consistency, requirement ownership, and self-check order clearer, but it cannot change input names, Schema/example, repair-context semantics, or `AgentInvoker` behavior without creating a new lineage and invalidating the sequence.

Static scans cover the raw template, not rendered injected data. They reject protocol names/constants and all configured provider/model names. The existing non-MQTT fixture renders every candidate through the same production contract; concrete identifiers may appear only inside delimited frozen-input sections. An abstract example, if evidence justifies one, must use generic synthetic ids and pass the same scans.

### 9. Completion is a technical evidence gate, not M1-4a3 approval

Unit/fake-provider tests prove the state machine, persistence, neutrality, tamper rejection, extension, screening, and ranking. They do not complete M1-4a2. Completion additionally requires actual V0 and every conditionally required V1/V2/extension batch from both configured candidates, recomputable reports, a final `selection.json`, and source prompt bytes matching its hash.

The final record hands M1-4a3 only lineage id, selected logical version, prompt ref/hash, final development assessment, and selection reason. It contains no production model, B1-B4 outcome, production semantic-repair budget, split ARCHITECT shape, freeze, or signature. M1-4a3 remains responsible for N=20 and owner approval.

### 10. Resolve and freeze real calibration configuration before provider I/O

The internal `init` operation requires two explicit filesystem inputs:

- `--config`, supplied through task variable `NEPA_M1_4A2_CONFIG`, names the calibration configuration to resolve with the existing configuration loader; and
- `--context-limits`, supplied through `NEPA_M1_4A2_CONTEXT_LIMITS`, names a canonical JSON object with exactly `qwen` and `deepseek` positive integer context-window limits.

Initialization resolves exactly the two `calibration_models`, their provider definitions, requested models, temperature/max-token parameters, and exact provider/model pricing entries. Both calibration requests MUST use `max_tokens = 65536`; any other value fails preflight and changes the lineage-controlled parameter projection. Their `api_key_env` fields are not caller-selectable: configuration preflight requires Qwen and DeepSeek to map respectively and exactly to the §8.3 constants `NEPA_QWEN_API_KEY` and `NEPA_DS_API_KEY`. Missing/extra candidates, a non-authorized `api_key_env`, missing provider or pricing entries, invalid parameters, or missing/non-positive/extra context limits fail before lineage/protocol publication and before network I/O. No limit or price is inferred from a model name, provider response, capability probe, or network lookup.

After validating configuration and context inputs, preflight reads only the current process environment and requires both fixed key variables to exist and contain non-empty values. A missing/empty variable produces a controlled pre-I/O error naming only that variable. The program does not read or parse `.bashrc`, `.profile`, dotenv files, or any other shell configuration, and no CLI option or configuration field accepts a key value.

The lineage and `protocol.json` bind canonical non-secret configuration, pricing, context-limit projections, the fixed key-variable-name mapping, and source hashes. Before each version/attempt admission, the coordinator reloads both explicit inputs, revalidates the fixed mapping and current-process presence, and rejects drift or absence before provider I/O. Key values and value hashes are excluded from canonical/hash inputs, JSON artifacts, lineage, protocol, traces, logs, diagnostics, and exceptions; only the two fixed environment-variable names may appear. Provider construction receives values through the existing in-memory environment secret boundary.

Alternative considered: keep `configs/default.yaml` as an implicit live default and fill its empty pricing/context data from known model names. Rejected because the current file is not a complete live calibration declaration and §8.3 forbids implicit model behavior. Alternative considered: ask the user to supply API-key variable names or values through the runner/configuration. Rejected because §8.3 already fixes the names and confines values to the current process environment. Alternative considered: load shell startup or dotenv files for convenience. Rejected because §8.3 explicitly forbids programmatic shell-configuration parsing. Alternative considered: persist or hash resolved secrets for replay. Rejected because replay binds only non-secret configuration and fixed names while credentials remain environment-only.

### 11. Treat every base N=5 run as an attempt-qualified two-model unit

Each V0/V1/V2 base version starts with `attempt_001`. The immutable attempt declaration binds the lineage, version, prompt snapshot ref/hash, N=5/depth-one protocol, configuration/context projections, and exactly two model-root refs. Attempt 001 may use the existing M1-4a1 direct model-root layout; attempt 002 and later use the existing `attempt_NNN/<model_id>` layout. The development attempt record makes the mapping explicit rather than inferring attempt identity from directory shape.

If any model exhausts transport retries without a response, the coordinator commits the attempt outcome as `infrastructure-invalid`. All artifacts remain audit evidence, but none of that attempt's model reports or trials may enter an assessment. A retry increments the attempt id and reruns both models with identical lineage/version/prompt/configuration/batch protocol. It never reruns only the failed model and never imports a successful report from an invalid attempt.

Assessment requires both reports to bind the same attempt declaration. Reconciliation of a non-invalid interrupted attempt may resume only its uniquely bound missing work through M1-4a1's committed-trial rules. Once an attempt outcome is infrastructure-invalid, resume can only create the next complete two-model attempt. Multiple competing incomplete attempts, a skipped attempt id, or cross-attempt refs are controlled state errors. The extension path applies the same attempt-unit rule to trials 006-010.

Alternative considered: keep successful model reports and replace only the infrastructure-failed model. Rejected because provider conditions and timing would differ and the resulting two-model assessment would not represent one attempt. Alternative considered: delete invalid attempts. Rejected because §6.4.8 requires auditable failure evidence and forbids replacement sampling.

### 12. Snapshot the candidate before admission and render every call from it

Version admission first reads the repository `architecture_planner.md`, performs structure/neutrality checks, and atomically publishes the exact bytes as `versions/<version>/prompt.md`. `version.json` binds that ref/hash before an attempt declaration is allowed. The prompt renderer/trial binding receives the snapshot bytes explicitly; no model worker or semantic-repair call dynamically reloads the repository template. Both batch declarations and traces must carry the snapshot raw-template hash, while effective prompts continue to differ only by the existing deterministic inputs/repair context.

The coordinator compares repository source and snapshot at admission, immediately before each further provider call, and again before committing an attempt outcome or assessment. It also reconstructs the actual provider prompt from the snapshot and frozen inputs when validating trace evidence. A mismatch stops subsequent provider I/O. Already committed calls remain audit evidence, but the attempt receives a non-valid source-drift outcome and cannot enter assessment/selection. Snapshot ref/hash mutation fails admission, resume, and recomputation without falling back to source bytes.

This closes the time-of-check/time-of-use gap while retaining the existing Agent system instruction, five-section template contract, input delimiters, output Schema/example, fresh repair context, and one logical Agent call shape. Prompt bytes remain the only permitted model-input change between versions; snapshotting is an evidence mechanism, not a new prompt variant.

Alternative considered: hash the repository template at admission but let each worker load it again. Rejected because concurrent edits could make the two models consume different bytes under one declared hash. Alternative considered: ignore source changes because calls use the snapshot. Rejected because the checked-out source is the candidate being developed and completion must prove it matches the selected evidence.

## Risks / Trade-offs

- [Live provider credentials, availability, or cost may block completion] → Require the two §8.3 fixed variables to be non-empty in the current process environment and name only missing variables in controlled pre-I/O failures; keep deterministic implementation tests separate from the actual-evidence tasks and do not mark M1-4a2 complete until all required live reports exist. Infrastructure exhaustion reruns the whole two-model version/extension attempt without replacement sampling.
- [An explicit live configuration can be incomplete or drift after initialization] → Validate pricing/provider/model/parameters/context limits before I/O, bind their non-secret projections, and recheck them at every attempt admission.
- [A repository edit can race provider execution] → Render only from the immutable snapshot, recheck source/snapshot/trace identity around every provider boundary, and exclude any drifted attempt from assessment.
- [A provider may change the returned model version mid-sequence] → Record and compare full returned identities and parameter-support states; invalidate prompt-only selection instead of attributing drift to the prompt.
- [An N=10 extension adds persistence/report complexity] → Limit extension to one append-only five-trial segment for V1/V2 and reuse the existing trial/trace/metric functions; do not introduce a generic arbitrary sampling framework.
- [Natural-language prompt hypotheses cannot be mechanically proven causal] → Mechanically enforce one hypothesis, exact prior evidence, diff scope, predicted gates, and post-run observation; make no stronger causal claim.
- [Fallback versions can tie after all authorized criteria] → Stop with explicit tie evidence and request an authoritative decision; do not silently add a selector.
- [Final Git diff contains only the selected prompt, not intermediate edits] → Preserve every intermediate raw template and exact diff immutably in the calibration root and bind them in revision records.

## Migration Plan

1. Change the lineage projection and Schemas/tests so N/depth remain batch-bound but no longer affect `lineage_id`; verify old roots are rejected rather than rewritten.
2. Add closed prompt-development protocol/version/attempt/revision/extension/assessment/outcome/selection Schemas and generic examples.
3. Add explicit calibration-config/context-limit preflight, enforce the §8.3 fixed `api_key_env` mapping/current-process presence boundary, and bind only non-secret resolved projections and fixed variable names to the corrected lineage/protocol.
4. Add the prompt-development coordinator, prompt snapshot binding, whole-two-model attempt state, common metric reuse, atomic evidence publishing, resume/recompute, N=10 extension, screening, and fallback selection with fake providers.
5. Extend configuration failure/drift, fixed-key presence/mapping and sentinel non-disclosure, attempt invalid/retry/resume, prompt snapshot/TOCTOU, neutrality, and source/hash tests while preserving the M1-3 five-section prompt contract.
6. Start a fresh corrected lineage from explicit complete inputs and run V0; execute only the evidence-authorized V1/N=10/V2 branches until one selection is committed.
7. Ensure the repository prompt equals the selected evidence bytes, run all focused/full validation, and leave M1-4a3 gates explicitly unsatisfied.

Rollback removes the M1-4a2 coordinator/Schemas and restores the pre-change prompt and lineage projection. New gitignored evidence remains non-consumable because its protocol/component hashes no longer match; no formal Run or workspace migration is involved.
