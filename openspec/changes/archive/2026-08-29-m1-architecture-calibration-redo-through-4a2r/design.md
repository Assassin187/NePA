## Context

See `proposal.md` for motivation. The existing M1-4a code is one connected path: `prepare_architecture_inputs` freezes three inputs; `compile_delivery_constraints` emits fixed slots; `build_planning_index` and the ArchitecturePlanner renderer construct context; the closed ArchitectureDraft Schema and `validate_architecture` implement ten gates; `s4_architecture.py` stores trials/recomputes reports; `s4_prompt_development.py` owns V0～V2 and R0～R2 state. This path is worth migrating rather than replacing, because it already supplies confined atomic artifacts, no-history calls, bounded format/semantic repair, canonical hashes, recomputation and fake-provider tests.

The current implementation diverges from the 5.2.0 baseline in four coupled dimensions:

- Delivery Constraints still publish ten fixed `file_rules/file_slots`; ArchitectureDraft has no `layout`, and `ARCH_VALIDATE`/its result Schema stop at `arch_10`.
- the live coordinator, internal defaults, context declaration and most calibration Schemas use two slots; `configs/default.yaml` names three candidates but gives calibration values `max_tokens=16000`, while the calibration protocol requires 65536;
- lineage verification binds model identifier strings and screening rejects identifier drift, although §9.2 now makes identifiers record-only;
- development/recovery code still implements the old zero-tolerance rate gates and all existing live reports were collected under a now-invalid contract.

M1-1/M1-2/M1-3 remain the reusable substrate. The change does not redesign Run v3, Provider adapters, AgentInvoker, structured format repair, telemetry, or generic role registration. It changes only their calibration configuration/binding inputs and adds regressions proving the prior public behavior remains intact.

The former layout-domain inconsistency is resolved by the responsible owner's decision and `system_design.md` 5.2.0. Both authoritative documents now admit only `{message_id}` and `{type_id}` in `path_pattern`, bound respectively to `expand_over=messages` and `expand_over=types`; the Blueprint projection is `per_message` and `per_type`, while static `path` projects to `none`. Apply records and verifies the resolved main-design path/hash but is no longer blocked by this issue.

A second conflict in the same pair of documents is resolved the same way by `system_design.md` 5.3.0 and `pipeline_design_s4_s9.md` 1.1.0: the `arch_13` and `arch_15` criteria both take the authorized subdocument's §5.2.4 wording. `arch_13` is three-segment closure plus one artifact per `link_source` slot, unique artifact output paths, an exact `delivery_form` `entry_point` count and an acyclic graph — the main document's former "each app slot" phrasing belonged to Blueprint-level `file_rules[].kind` and had no counterpart in the layout's `build_role` enum. `arch_15` is the whitelist form over `path`/`path_pattern` segments and `purpose` tokens, and its general-responsibility whitelist is declared a lineage-bound validator-side implementation shared with D1.11, not convention `advisory`/`hard` content. Both had to be settled before the lineage freeze because each changes measured `p0`/`p1`.

## Goals / Non-Goals

**Goals:**

- Preserve one production-shaped deterministic path from frozen inputs through free-layout ArchitectureDraft validation to recomputable calibration evidence.
- Make the layout convention, Schema, all fifteen gates, logical model slots, screening semantics and report definitions explicit lineage controls.
- Reuse existing artifact/retry/recompute mechanics while making historical fixed-layout and dual-model evidence mechanically ineligible.
- Finish at one new M1-4a2 prompt selection, or—only after a new tie and owner approval—one new M1-4a2r selection/no-selection.
- Produce a concise, version-controlled report whose numbers can be reconstructed from the referenced new-lineage leaves.

**Non-Goals:**

- Implement the second Delivery Compiler function, Blueprint translation, Plan/Linker, complete S4, S5/S6 or any plan revision machinery.
- Run M1-4a3 N=10, make B1～B4 decisions, or freeze a production model/call shape/budget.
- Migrate historical calibration roots in place, reinterpret their metrics, or mark the old active M1-4a2 change complete.
- Add a model-drift invalidation heuristic, quality-review hard gate, compatibility reader for old calibration Schemas, or any new dependency.

## Decisions

### 1. Treat the resolved design baseline and milestone entry as hard preflight controls

Implementation begins with the §10.8 four-part implementation brief and verifies the M0 entry records plus the archived M1-1/M1-2/M1-3 baseline. It records the approved `system_design.md` 5.2.0 path/hash and checks that both authoritative documents still expose the same two-placeholder/two-domain rule before production Schema/validator changes or live calls.

The choice itself is no longer delegated to implementation: `{message_id}`/`messages` and `{type_id}`/`types` are the complete allowed pairs, and the deterministic Blueprint mapping is `none/per_message/per_type`. The `arch_13`/`arch_15` criteria are likewise fixed to the subdocument's §5.2.4 wording. Any later drift again fails the existing §11.3 consistency preflight rather than being guessed in code.

Two consequences of that resolution are recorded rather than implemented here. `kind`/`producer` cannot be derived from a single `render_rule` value, so §5.6.5.3 now defers their derivation table to M1-4b2 together with the transcriber, and implementers are forbidden from inventing the mapping. Conversely, the convention asset, `layout_convention_id` derivation, `architecture.layout` Schema and `arch_11`～`arch_15` move from M1-4b2 to M1-4a1 in §10.2, because §6.4.8.1 and D1.0 make them prerequisites for collecting any calibration batch on a free-layout lineage; this change therefore delivers them under M1-4a1 rather than silently absorbing another milestone's scope.

### 2. Upgrade affected JSON contracts instead of accepting old shapes

Adding required `architecture.layout`, expanding the validation gate set, and changing fixed model objects are breaking Schema changes. Affected ArchitectureDraft/validation and calibration artifact Schemas will receive new major `schema_version` values and examples. New loaders accept only the new version in this workflow; old roots are read only by an explicitly historical inventory/reporter path when provenance is needed, never by the current assessment path.

This avoids compatibility branches in the production validator. Keeping `schema_version=1.0` or auto-filling layout would hide a breaking semantic change and permit old evidence into a new denominator.

### 3. Make the layout convention the deterministic input and the layout the LLM output

The first compiler entry point remains the shared `compile_delivery_constraints(spec, target_profile)` path. It mechanically derives the convention id from language/delivery form, loads the version-controlled protocol-neutral asset, verifies/canonicalizes it, and returns its id/hash plus `advisory` and `hard` projections alongside naming, resource limits, build variants, mechanical-input bounds and template roots. It no longer returns the old fixed file table. The free-layout contract accepts only static `path`, `{message_id}` with `messages`, or `{type_id}` with `types`; mixed, repeated, unknown or mismatched placeholder/domain combinations fail mechanically.

The ArchitecturePlanner output Schema carries complete `architecture.layout`. `advisory` is rendered as data inside the named Delivery Constraints delimiter, not copied into prompt instructions. `hard` is consumed by deterministic gates. This preserves the document's boundary that general layout knowledge is versioned data, not hidden prompt knowledge.

The complete layout-to-Blueprint translator remains M1-4b2. M1-4a1 only implements enough normalization/expansion to validate layout safety, class/owner closure, build-graph shape, layering and neutrality and to feed calibration. It must not create a parallel Blueprint implementation.

### 4. Extend the existing validator to fifteen gates in one pass

`nepa/speclib/architecture.py` retains the current canonical issue sorting, parent refs and non-short-circuit structure. Gates 01～10 are adapted from fixed-slot lookup to layout-derived sets where required; gates 11～15 are appended as separate deterministic functions consuming the draft, convention and frozen derived identifiers. The validation envelope lists exactly fifteen gates.

The same `validate_architecture` symbol remains the trial engine's semantic authority. Tests use table-driven positive/negative fixtures for every new gate and multi-defect stable ordering. A separate experimental validator or a boolean “layout valid” shortcut is rejected because it would sever production/calibration identity.

### 5. Keep free-layout prompt knowledge data-driven

`architecture_planner.md` keeps the existing five-section template and the exact named inputs `planning_index`, `delivery_constraints`, and `repair_context`. Its algorithm is rewritten to enumerate and reconcile layout declarations, module/file ownership, contracts, build graph, layering, path neutrality, responsibilities and test readiness, but it does not embed suggested filenames, interface names, layer names that the convention owns, protocol facts, or model branches. The prompt tells the model how to consume `advisory/hard`; the asset contains the actual convention values.

The repair context remains the full prior Schema-valid draft plus exact canonical failures in a fresh call. Repair-impact policy tables are extended through `arch_15`, including layout cross-projections, so M1-4a2r locality evidence remains recomputable.

### 6. Use stable logical model slots and exclude model strings from lineage identity

One ordered constant/set of slots—`qwen`, `claude`, `deepseek`—drives config preflight, Schema objects, task declarations, directories, loops and aggregates. Each binds Provider adapter/name, complete endpoint, secret-variable name, route/tier if present, temperature, `max_tokens=65536`, context limit and pricing. The three secret names remain `NEPA_QWEN_API_KEY`, `NEPA_CLAUDE_API_KEY`, and `NEPA_DS_API_KEY`; values are only checked for presence immediately before live work and are never persisted or printed.

Configured and returned model strings are stored per call/trial and summarized by set/share in reports, but are absent from the canonical lineage-id projection and never form directory keys. Lineage verification compares the controlled projection, not a whole config object containing `model`. This is the smallest implementation of §9.2 rule 1 and avoids adding the unapproved O-19 behavioral invalidation rule.

The alternative—treating every alias change as a new lineage or requiring one stable returned value—would recreate the 4.1.0 failure mode. Omitting model strings entirely would lose required reproducibility disclosure.

### 7. Preserve append-only evidence and coherent attempt semantics

The current confined atomic publication and immutable replay path remains. New lineage publication freezes the three inputs, convention, planning artifacts, new Schema/example, serializer/validator/metric components, controlled slot projection and implementation hashes before prompt versions begin. Declarations commit before Provider calls. A trial directory commits only after all referenced request/response/validation/trace leaves are durable and hash-valid.

Each version attempt is coherent across all three slots. One infrastructure-invalid slot makes the attempt audit-only; a retry declares a new attempt and reruns all three batches. Resume reuses only complete hash-valid leaves inside the declared attempt and never splices attempts. Old roots are neither moved nor edited; their ineligibility follows from lineage controls and new Schema versions.

### 8. Encode the 5.1.0 screening funnel directly in typed protocol artifacts

Development/recovery protocol Schemas declare `p1_threshold=0.80`, `max_truncations=0`, required valid infrastructure, and the M1-4a3 reference threshold 0.90 used solely for a monotonicity assertion. The screening function uses only p1/truncation/infrastructure plus recovery-specific locality/full-validator conditions. Schema-after-format-repair rate, p0, first semantic pass, per-gate rates, repeated first failures and model strings remain in assessment/report objects but are absent from the hard-gate object.

For V1/V2 N=10 extension, the existing evidence-qualified single-sample/metric-conflict state remains, generalized to three slots. The fixed post-V2 fallback tuple continues to use the minimum across all three slots in the documented order. No change in fallback ordering is attributed to the new threshold.

This representation makes an inverted gate fail Schema/protocol preflight, rather than relying on comments in selection code.

### 9. Generalize recovery; do not replay the historical recovery

The current R0～R2 coordinator/state machine, repair-diff evidence, quality audit, rollback and handoff machinery is reused, but hard-coded predecessor lineage, old seed hash, two-slot objects, ten-gate policies and old thresholds are removed. Initialization accepts only the new M1-4a2 root, recomputes its new-contract tie and binds an explicit owner authorization for that exact predecessor/design hash.

R0 may incorporate protocol-neutral conclusions from the new development report, but no previous candidate/trial enters its denominator. Every recovery version is three-slot N=5 with one repair. The first version meeting per-slot p1 ≥ 0.80 plus validity/locality/full-validator conditions stops. If normal development selects a prompt, no recovery root is created; the experiment result records `not_triggered`. This honors conditionality and avoids spending calls to recreate an event that did not occur.

### 10. Separate machine evidence from the human-readable experimental report

Tracked experiment material lives under `experiments/m1-architecture-calibration-redo-through-4a2r/`: the implementation brief, preregistration, non-secret config/context provenance, machine summary and concise Markdown result. Raw calls/trials remain in gitignored `runs/_calibration/...` and are cited by lineage-relative path/hash. The report generator/recompute command reads only new-lineage leaves, emits each slot separately, includes all fifteen gates and model-string shares, and records selection, tie, conditional recovery status and limitations.

The report is not Report v2 and is not an M1-4a3 comparison. Hand-entered numbers are prohibited; Markdown is checked against the machine summary. This is preferable to rewriting the old experiment directory, whose numbers remain useful historical evidence under the old contract.

### 11. Keep public runtime behavior stable

No public `nepa` calibration command is added. The existing internal module runner remains explicit-path and task-local. `run_m1_calibration.sh` is the mandatory task-local launcher for every credential-requiring calibration command: it starts an interactive Bash so `~/.bashrc` refreshes `NEPA_CLAUDE_API_KEY` from `CLAUDE_API`, `NEPA_QWEN_API_KEY` from `ALI_API`, and `NEPA_DS_API_KEY` from `DS_API`, then `exec`s the unchanged module runner. This happens before the calibration process starts; calibration code still reads only its three declared process environment variables and never reads shell/dotenv files, persists credentials, or prints them. Run v3, formal stage receipts, Provider request routing, AgentInvoker logical-call semantics, cache keys, telemetry records and non-ArchitecturePlanner roles remain unchanged. Configuration changes are limited to making the documented three calibration slots internally consistent at 65536 and adding the explicit live/context/pricing values required by the new experiment.

## Persistent State and Failure Semantics

The durable sequence is:

1. verify design/M0/dependency gates and write the tracked implementation brief;
2. finish deterministic Schema/asset/validator/config tests without Provider I/O;
3. preregister and freeze the new lineage root atomically;
4. publish prompt-version and attempt declarations before calls;
5. atomically commit each complete trial, then per-slot reports and one version assessment;
6. publish first-pass selection, fixed fallback selection, or tie exactly once;
7. publish a technical handoff only after source/snapshot hashes match; if tied, wait for owner recovery authorization;
8. conditionally run recovery and publish its selection/no-selection, or publish tracked `not_triggered` evidence;
9. generate machine summary and Markdown report from committed leaves.

Hash mismatch, mixed lineage/slot/attempt evidence, non-canonical artifacts, invalid controlled configuration, context overflow, missing credential name/value at live preflight, truncation, or infrastructure invalidity fail closed with the distinctions specified in the specs. No calibration failure creates a formal `termination_request` or Report v2. Failed/incomplete roots remain audit evidence and are never deleted or overwritten.

## Risks / Trade-offs

- **[The resolved design rule could drift again]** → Bind `system_design.md` 5.2.0 path/hash in the implementation brief and lineage, test both legal placeholder/domain pairs plus illegal mixed/unknown pairs, and fail preflight if the authorized documents diverge later.
- **[Free layout greatly enlarges output and lowers p0]** → Run three-slot context preflight before calls, report p0/gate failures, keep one bounded repair, and stop under the prescribed V0～V2/R0～R2 limits.
- **[Fifteen-gate locality closure can be wrong]** → Treat the policy as lineage-bound data/code, add gate-specific changed-path tests, and require complete final validator evidence; changing policy forces a new lineage.
- **[Alias drift can conceal a true model change]** → Preserve requested/returned strings and call shares plus variance diagnostics in reports; do not invent an invalidation threshold before O-19 is decided.
- **[Three-model coherent reruns raise cost]** → Retain attempt-level comparability and preregister the rerun rule; no partial reuse or replacement sampling.
- **[A selected p1=0.80 prompt may be weak]** → State that M1-4a2/4a2r only screen/choose a prompt; M1-4a3 N=10 B1～B4 remains the qualification boundary.
- **[Old and new active changes confuse status]** → Leave old bytes/status untouched, name the superseding change and new report explicitly, reject old roots mechanically rather than relying on narrative labels, and archive the superseded change with `--skip-specs` before implementation so its dual-model requirement set never merges into the same capability baseline as this change's three-model set.
- **[`arch_01` loses its external anchor under free layout]** → Today `arch_01` resolves `interface_file` context refs against `constraints.file_slots`/`internal_interface_slots`, an input the model does not author. Once Delivery Constraints stop publishing a file table, that set comes from the draft's own `architecture.layout`, so the check becomes self-referential: it still catches internal inconsistency but can no longer catch a fabricated interface file. This is accepted as inherent to free layout — `arch_11`/`arch_12`/`arch_15` constrain the layout itself instead — but per-gate tests must assert the narrowed guarantee rather than assume the old one, and the report should not present `arch_01` as evidence of externally anchored file identity.

## Migration Plan

1. Record the completed owner resolution, verify the synchronized 5.2.0 design baseline and close the remaining M0/dependency entry checks; produce the §10.8 implementation brief.
2. Add/version layout convention assets and migrate Delivery Constraints, ArchitectureDraft/validation Schemas/examples and the shared validator; land deterministic MQTT/non-MQTT tests first.
3. Migrate calibration artifact Schemas, configuration and coordinator loops to three logical slots; implement record-only model strings and fixed screening protocol; extend all recomputation/locality tests.
4. Freeze the new M1-4a1 lineage only after the focused and full deterministic suites pass. Do not import historical trials.
5. Write preregistration, run the bounded live V0/V1/optional V2 sequence, recompute selection/tie and generate the new development report.
6. If selected, publish the M1-4a3-only handoff and `M1-4a2r=not_triggered`. If tied, stop until the separate owner authorization is present, then execute bounded R0/R1/optional R2 and report selection/no-selection.
7. Run full acceptance, inspect the complete diff/evidence graph, and leave M1-4a3 and all downstream work unsatisfied.

Rollback never rewrites evidence. Before a valid selection, restore the repository prompt to the frozen pre-development/pre-recovery bytes and abandon the new root as non-selectable audit evidence. After a valid selection, revocation is a new append-only authorization/status record; neither old nor new lineage leaves are mutated. Code rollback may revert the implementation commit, but historical roots remain distinguishable by Schema/lineage identities and are not auto-migrated.
