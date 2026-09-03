## Purpose

Define the bounded, three-model M1-4a2 workflow that develops one shared protocol-neutral ArchitecturePlanner `initial`/`repair` prompt bundle through locality-constrained patch repair and produces recomputable evidence for an M1-4a3 admission decision.

## ADDED Requirements

### Requirement: Development starts only on a complete patch-capable lineage
M1-4a2 SHALL start only after the free-layout M1-4a1 slice, `arch_01`～`arch_15`, Qwen/Claude/DeepSeek logical slots at `max_tokens=65536`, the ArchitectureDraft and value-hash-free repair-patch Schemas, atomic patch application, placeholder/derived-identifier-aware path-neutrality validation, exact coupled-reference projection, failure-to-allowed-path mapping, serializer, validator, metric definitions and shared `initial`/`repair` prompt-bundle invocation contract have been frozen into one new design-6.1.0 post-fix lineage. Historical full-draft-repair trials, prior-value-hash patch trials, pre-fix value-hash-free lineages and all single-template trials SHALL NOT enter screening, fallback, selection or handoff. Within the post-fix lineage, only one stage template of the shared ArchitecturePlanner prompt bundle SHALL vary at each admitted version transition; the other template SHALL remain byte-identical. No bundle hash, per-template digest field or new hash gate SHALL be introduced. (Design: `system_design.md` §0.1, §6.4.8.1～§6.4.8.2, revision 6.1.0; `pipeline_design_s4_s9.md` §5.2.2/§5.2.4; D1.0.)

#### Scenario: Complete new lineage is admitted
- **WHEN** all frozen components, existing artifact references, logical slots, two-stage prompt selection and patch-locality definitions verify before Provider I/O
- **THEN** the workflow admits V0 and records those identities in its development protocol

#### Scenario: Historical full-draft evidence is proposed
- **WHEN** a caller offers a trial whose semantic repair returned a complete ArchitectureDraft or whose patch contract is not bound by the current lineage
- **THEN** the workflow rejects it from every denominator, comparison, selection and handoff while preserving its historical bytes

#### Scenario: Pre-fix value-hash-free evidence is proposed
- **WHEN** a caller offers evidence created before the corrected path-neutrality validator and coupled-reference projection were frozen
- **THEN** the workflow preserves its calls, costs and artifacts as audit provenance but rejects it from every post-fix denominator, prompt comparison, selection and handoff

#### Scenario: Single-template evidence is proposed
- **WHEN** a caller offers a trial whose initial and semantic-repair calls were rendered from the former shared `architecture_planner.md`
- **THEN** the workflow preserves that trial as audit provenance but rejects it from the design-6.1.0 lineage, denominator, comparison, selection and handoff

### Requirement: One shared two-stage prompt bundle is developed through V0 to V4
The workflow SHALL use one model-independent, protocol-neutral ArchitecturePlanner prompt bundle containing `architecture_planner_initial.md` and `architecture_planner_repair.md` for all three slots. V0 SHALL run first. Each of V1～V4 SHALL be admitted only when the preceding complete failing version supports one evidence-backed, falsifiable prompt-defect hypothesis distinct from earlier revisions. Each revision SHALL bind the parent bundle version, both existing prompt artifact references, exact evidence refs, one hypothesis, the single stage selected for modification, its exact diff, expected affected gates or repair depths, and stopping conclusion. Exactly one of `initial` or `repair` SHALL change per transition and the other SHALL remain byte-identical. V1～V4 provide four edits total across the bundle, not four edits per template. No model-specific prompt, protocol-specific constant, cross-version template mixing, V5, fifth edit, bundle/template digest prerequisite or open-ended tuning SHALL be admitted. (Design: `system_design.md` §0.1, §6.4.8.2, §8.8.)

#### Scenario: A failing version supports a new prompt defect
- **WHEN** the complete preceding version fails screening and its recomputable evidence supports one new declared hypothesis
- **THEN** exactly one next-version diff for either `initial` or `repair` may be admitted while the other stage and every lineage-bound non-prompt component remain unchanged

#### Scenario: An extra revision is requested
- **WHEN** V4 has already been admitted or four prompt revisions have been recorded
- **THEN** the workflow rejects any V5 or additional edit to either stage before Provider I/O

#### Scenario: A revision changes both stages
- **WHEN** one proposed V1～V4 transition changes both `initial` and `repair` bytes or mixes templates from different bundle versions
- **THEN** the workflow rejects the revision before Provider I/O

### Requirement: Every version uses coherent isolated N3 three-model evidence
Every admitted version SHALL declare exactly three fresh initial-generation trials for each of Qwen, Claude and DeepSeek under the same bundle version, byte-identical `initial`/`repair` pair and frozen post-fix lineage. Each trial and repair SHALL use a fresh no-history invocation with cross-trial cache disabled and model-isolated evidence, session, cache and trace roots. By explicit owner authorization after the validator/locality and two-stage invocation corrections, the post-fix lineage SHALL begin initial-generation accounting at zero and SHALL have a per-model ceiling of 15 across its V0～V4; semantic patch and format-repair calls SHALL be excluded from that ceiling but separately metered. Calls made under pre-fix or single-template lineages SHALL be reported separately as pre-reset audit usage and SHALL NOT reduce the post-fix ceiling or enter its versions. No extension, replacement sample, partial-slot completion or cross-attempt assembly SHALL be allowed. An infrastructure-invalid slot inside the post-fix lineage SHALL make the entire version attempt audit-only, and a retry SHALL redeclare all three N=3 slot batches while remaining subject to that lineage's 15-trial ceiling. Bundle equality SHALL be established by the existing artifact references, declared version and recorded bytes, not by a new bundle or template hash field. (Design: `system_design.md` §0.1, §6.4.8.2, §8.3～§8.4, §9.2; owner-authorized post-fix budget reset.)

#### Scenario: A version attempt starts
- **WHEN** V0～V4 is admitted
- **THEN** exactly nine initial-generation trial identities, three per logical slot, are durably declared before Provider I/O

#### Scenario: All five versions execute
- **WHEN** no earlier version passes and V0～V4 all complete
- **THEN** each model has exactly 15 initial-generation trials and all patch or format-repair calls are reported outside that count

#### Scenario: Post-fix budget is initialized
- **WHEN** the corrected validator and locality components are frozen into the replacement lineage before its V0 Provider I/O
- **THEN** each model's post-fix initial-generation counter is zero, its ceiling is 15, and the immutable reset record identifies the owner authorization, replaced lineages, their actual usage and the non-admission reason

#### Scenario: One slot is infrastructure-invalid
- **WHEN** any logical slot exhausts transport retries without a usable model response
- **THEN** no slot report from that attempt enters screening and any retry reruns complete N=3 batches for all three slots

### Requirement: Each trial uses at most two locality-constrained patch repairs
The initial invocation SHALL render only the bundle's `initial` template, return a complete ArchitectureDraft and establish p0. After a semantic failure, the first fresh repair SHALL render only the same bundle version's `repair` template with the current candidate, its exact canonical failures and the mechanically derived allowed paths, and SHALL return only a repair patch with operation-specific presence state and no prior-value hash or value digest; atomic application and full Schema plus `ARCH_VALIDATE` validation SHALL establish p1. If p1 still fails, a second fresh repair SHALL use the same `repair` template with the patched candidate and its newly recomputed failures under the same rules and SHALL establish p2. A p0 or p1 pass SHALL stop the trial. A repair SHALL NOT include or concatenate the initial template, return a full draft, modify outside allowed paths, partially apply, or fall back to full replacement. (Design: `system_design.md` §6.4.8.1～§6.4.8.2, revision 6.1.0; D1.0.)

#### Scenario: First patch closes the failures
- **WHEN** an allowed, applicable first patch produces an ArchitectureDraft that passes the full validator
- **THEN** the trial records p1 and p2 success cumulatively and issues no second semantic repair

#### Scenario: Second patch is required
- **WHEN** the first patched candidate remains Schema-valid but fails the full validator
- **THEN** the second repair receives that exact candidate and only its newly recomputed failures and allowed paths

#### Scenario: Patch attempts to rewrite a correct region
- **WHEN** any operation targets a path outside the union allowed by the current canonical failures
- **THEN** the entire patch is rejected without changing the candidate, the reason is retained, and no full-draft fallback is issued

### Requirement: Development screening requires every model to reach p2 at 0.60
A prompt-bundle version SHALL pass only when Qwen, Claude and DeepSeek each independently have complete infrastructure-valid N=3 reports, zero truncations and cumulative `p2 ≥ 0.60`, which for N=3 requires at least two passing trials per model. p0, p1, Schema rates, per-depth per-gate rates, repeated failures, patch rejection/locality, cost, latency and model-string variation SHALL remain diagnostics and SHALL NOT replace or add to the p2 rate gate. No cross-model average SHALL hide a failing slot. The first passing bundle version SHALL be selected immediately and unused initial-generation capacity SHALL be cancelled. (Design: `system_design.md` §6.4.8.2, revision 6.1.0; D1.0.)

#### Scenario: All slots pass two trials
- **WHEN** every complete N=3 slot report has at least two trials passing by p2 with zero truncation and valid infrastructure
- **THEN** the bundle version passes, is selected, and later bundle versions and their initial calls are rejected

#### Scenario: One slot passes only one trial
- **WHEN** either Qwen, Claude or DeepSeek has p2 equal to one of three while the other slots meet the gate
- **THEN** the version fails without averaging or using another slot's excess successes

### Requirement: Terminal fallback and tie use complete V0 to V4 assessments
If V4 completes with no screening-passing version, the workflow SHALL compare each complete final V0～V4 assessment lexicographically by minimum three-slot p2, then minimum three-slot p1, minimum three-slot first semantic-pass rate, minimum three-slot Schema-after-format-repair rate, and lower total cost. It SHALL store every comparison tuple and SHALL NOT use averages, undeclared samples, quality prose or another tiebreaker. A complete equality SHALL publish `PROMPT_SELECTION_TIE` without prompt selection or handoff. (Design: `system_design.md` §6.4.8.2.)

#### Scenario: One fallback tuple is uniquely maximal
- **WHEN** no version passes and one complete V0～V4 tuple is uniquely maximal in the fixed order
- **THEN** that version's complete `initial`/`repair` bundle is selected only as an M1-4a3 candidate without a production qualification claim

#### Scenario: All maximal tuples tie
- **WHEN** the fixed comparison cannot distinguish the maximal complete assessments
- **THEN** the workflow publishes `PROMPT_SELECTION_TIE`, no selection and no handoff, and does not start M1-4a2r

### Requirement: New development evidence is recomputable and scope-limited
The workflow SHALL publish canonical protocol, prompt-bundle snapshot, attempt, trial, initial-generation, patch request/response, patch application/locality, validation, report, assessment, revision, selection or tie, and optional technical-handoff artifacts under the new lineage using only the existing artifact-reference and traceability contract. The prompt-bundle snapshot SHALL contain the bundle version and both stage bytes without adding a bundle SHA-256, per-template digest field, parent-prompt hash prerequisite or new hash validation gate. A version-controlled implementation brief and preregistration SHALL precede live calls. Machine summaries and the human-readable report SHALL be derived exclusively from intact new-lineage leaves and SHALL present all three slots, fixed denominators, p0/p1/p2, all fifteen gates at each depth, patch outcomes, usage and exact terminal reasoning. Any handoff SHALL admit only M1-4a3 and SHALL NOT qualify a model, trigger recovery, create a formal Run or replace an owner signature. (Design: `system_design.md` §0.1, §6.4.8, §9.2, §10.2 D1.0/D1.11.)

#### Scenario: Complete evidence is recomputed
- **WHEN** all declared leaves and their existing artifact-reference parents are intact
- **THEN** recomputation reproduces the canonical reports, assessments, selection or tie, handoff bytes and all Markdown values

#### Scenario: Historical or hand-entered evidence is used
- **WHEN** an aggregate lacks a new-lineage leaf path/hash or copies a historical report value
- **THEN** publication fails and that value cannot support M1-4a2 completion
