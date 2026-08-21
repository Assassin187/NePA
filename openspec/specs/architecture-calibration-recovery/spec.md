# architecture-calibration-recovery Specification

## Purpose

Define a design-authorized, bounded recovery protocol after an ArchitecturePlanner prompt-development selection tie, producing a fresh isolated calibration lineage and one auditable shared prompt whose success may rely on at most one issue-local semantic repair before formal M1-4a3 qualification.

## Requirements

### Requirement: Recovery activation requires explicit design authority
The system SHALL NOT initialize an M1-4a2r recovery lineage, modify the repository ArchitecturePlanner prompt, or perform recovery Provider I/O unless an explicit responsible-owner authorization and the corresponding approved `project_docs/system_design.md` revision define the M1-4a2r entry condition, recovery screening semantics, relationship to M1-4a2/M1-4a3, and completion boundary. The recovery declaration SHALL bind the approved design revision by path and SHA-256. OpenSpec artifacts, experiment reports, code behavior, or a failed predecessor lineage SHALL NOT substitute for this authorization. (Current Design 3.1.0: §6.4.8.2.1–§6.4.8.3, §10.2 M1-4a2r, D1.0.)

#### Scenario: No approved design revision exists
- **WHEN** a caller attempts to initialize or run M1-4a2r without the responsible-owner authorization and matching approved design-document hash
- **THEN** the operation fails before prompt publication, lineage evidence creation, repository prompt modification, or Provider I/O

#### Scenario: Approved design revision is bound
- **WHEN** the responsible owner has approved the M1-4a2r protocol and the referenced system-design bytes match the recovery declaration
- **THEN** initialization may continue and every later recovery artifact retains that design path/hash identity

#### Scenario: Design authority drifts during recovery
- **WHEN** the bound system-design bytes change or the authorization identity no longer matches before a version attempt or handoff publication
- **THEN** the active attempt becomes non-selectable and no further Provider call or handoff is admitted until a new authorized recovery protocol is initialized

### Requirement: A terminal M1-4a2 tie admits only a fresh isolated recovery lineage
M1-4a2r SHALL be admitted only from a complete, immutable predecessor development root whose V0/V1/V2 evidence deterministically recomputes to `PROMPT_SELECTION_TIE` and has no selection/handoff; a stored `protocol.status = initialized` SHALL NOT by itself disqualify that root because the predecessor coordinator did not publish a tie terminal record. The recovery root SHALL publish a canonical predecessor attestation that validates the old hash graph and complete version evidence against the Schemas/component bundles frozen in that root, recomputes the three fallback tuples and tie without relying on the modified current-lineage component check, and inventories workspace-confined source paths/hashes. It SHALL treat the attestation and recovery-local snapshots of experiment/root-cause/seed evidence as provenance rather than trial input. Because the recovery protocol and metric definitions differ, M1-4a2r SHALL derive a new lineage id and SHALL NOT append to, rewrite, aggregate with, or complete the predecessor lineage. (Current Design 3.1.0: §6.4.8.1–§6.4.8.3, §9.1.5.)

#### Scenario: Valid terminal tie is supplied
- **WHEN** the predecessor root recomputes to a complete V0/V1/V2 `PROMPT_SELECTION_TIE` with intact hash-bound evidence
- **THEN** recovery initialization creates a distinct lineage and publishes an immutable local predecessor attestation plus local experiment provenance without copying any prior trial into a recovery report

#### Scenario: Predecessor has a selected prompt
- **WHEN** the supplied predecessor root already contains a valid selected prompt and handoff
- **THEN** M1-4a2r initialization is rejected because post-tie recovery is not admitted

#### Scenario: A prior candidate or trial is proposed as recovery evidence
- **WHEN** a caller attempts to count an experimental, V0, V1 or V2 request, response, candidate, validation or metric in R0/R1/R2
- **THEN** recomputation rejects the recovery batch as cross-lineage evidence

#### Scenario: Predecessor evidence changes
- **WHEN** any referenced predecessor or experiment artifact no longer matches its recorded hash
- **THEN** recovery recomputation and handoff publication fail without mutating either root

#### Scenario: Current calibration components differ from the predecessor
- **WHEN** the M1-4a2r implementation has intentionally changed lineage-controlled reporting, locality or metric components
- **THEN** predecessor admission verifies the old frozen evidence contract and recomputed tie through the local attestation instead of rejecting or rewriting the predecessor because current component hashes differ

### Requirement: Recovery uses one shared protocol-neutral exact-algorithm prompt
R0 SHALL use one immutable shared ArchitecturePlanner prompt derived from the authorized exact-algorithm seed at `experiments/m1-4a2-architecture-planner-prompt-optimization/results/phase1/artifacts/prompt-exact-algorithm.md`, SHA-256 `d5c24f1939f3a767f2cd1d7a116124d4b5ea32552391664052a93f24f1914b85`. Initialization SHALL copy those verified bytes into a recovery-local provenance snapshot before R0 admission. The prompt SHALL express protocol-neutral construction and verification procedures for requirement ownership, s5/s6 file partitions, contract/module/work-package projection equalities, contract-derived dependency DAGs, required interface-slot closure, task-readiness convergence and issue-local repair. The same raw template bytes SHALL be used for Qwen and DeepSeek, SHALL satisfy the existing prompt structure and neutrality gates, and SHALL contain no target-protocol, model or Provider-specific branch. Concrete identifiers SHALL enter only through the frozen named inputs. (Current Design: §6.4, §6.4.8.2, §8.8, D1.11; experiment Phase 1 H-PROMPT evidence.)

#### Scenario: R0 is admitted
- **WHEN** the authorized seed ref/hash, repository source bytes, immutable R0 snapshot and neutrality scan all agree
- **THEN** both model batches render from exactly that R0 snapshot and its recovery-local seed provenance with unchanged frozen inputs and output contract

#### Scenario: Prompt contains target or model specialization
- **WHEN** R0, R1 or R2 embeds a protocol name, protocol fact, REQ id, target path/interface constant, model/Provider identity or conditional branch
- **THEN** version admission fails before Provider I/O

#### Scenario: Prompt source and snapshot differ
- **WHEN** repository prompt bytes, admitted snapshot bytes, recorded prompt hash or actual Provider render cannot be reconstructed as one identity
- **THEN** the attempt is invalid and cannot publish an assessment or selection

### Requirement: Recovery prompt development is bounded to R0 and two evidence-backed revisions
The recovery sequence SHALL contain R0 and at most R1 plus optional R2. R1 SHALL be admitted only when complete valid R0 evidence misses the recovery gate and supports one falsifiable prompt or repair-locality defect hypothesis. R2 SHALL be admitted only when complete valid R1 evidence still misses the gate and supports a second, distinct falsifiable defect hypothesis. Each revision record SHALL bind the immediately preceding prompt hash, exact failure evidence, one hypothesis, exact prompt diff, expected metric/gate change and stopping conclusion. R1 and R2 SHALL change only shared prompt bytes; any lineage-bound input, Schema, validator, serializer, model request, repair budget or metric change requires another lineage and SHALL NOT be attributed to prompt optimization. The first passing version SHALL stop all later prompt edits and calls. If R2 fails, or a failing version has no admissible next hypothesis, recovery SHALL atomically restore and hash-verify the immutable pre-recovery repository prompt bytes before publishing explicit no-selection; restoration failure SHALL leave recovery nonterminal and block further calls/handoff. There is no R3, fallback ranking or additional tuning. (Current Design 3.1.0: §6.4.8.2.1.)

#### Scenario: R0 satisfies the recovery gate
- **WHEN** the complete R0 two-model assessment passes every recovery condition
- **THEN** R0 is selected immediately and all R1/R2 admission and Provider I/O are permanently rejected

#### Scenario: R0 has one evidence-backed prompt defect
- **WHEN** R0 fails and its canonical evidence supports one declared falsifiable prompt/repair-locality hypothesis
- **THEN** one R1 revision may be admitted with an immutable prompt snapshot and fresh complete two-model attempt

#### Scenario: R1 has a second distinct evidence-backed prompt defect
- **WHEN** complete R1 fails and its canonical evidence supports one declared falsifiable prompt/repair-locality hypothesis distinct from the R1 hypothesis
- **THEN** one R2 revision may be admitted with an immutable prompt snapshot and fresh complete two-model attempt

#### Scenario: A third change or failing R2 is reached
- **WHEN** R2 has already been admitted or its complete assessment fails the recovery gate
- **THEN** no R3, fallback winner or extra sample is allowed and the terminal result is explicit recovery no-selection

#### Scenario: Recovery ends without a selection
- **WHEN** complete R2 fails, failing R0 does not support an admissible R1 revision, or failing R1 does not support an admissible R2 revision
- **THEN** the controller restores the pre-recovery prompt bytes, verifies their hash and only then publishes no-selection; if restoration fails it publishes no terminal result or handoff

#### Scenario: A revision changes a controlled component
- **WHEN** an R1 or R2 proposal changes anything other than the shared prompt bytes
- **THEN** the recovery revision is rejected as a mixed-variable experiment

### Requirement: Every recovery version uses one coherent fresh two-model N=5 attempt
Each R0/R1/R2 version SHALL run exactly five fresh Qwen trials and five fresh DeepSeek trials with identical lineage-bound inputs, prompt snapshot, ArchitectureDraft contract, production `ARCH_VALIDATE`, temperature and `max_tokens = 65536`. Models, trials and repairs SHALL use isolated no-history sessions, cache-disabled execution, distinct evidence roots and stable returned identities. If either model batch is infrastructure-invalid, the whole version attempt SHALL remain audit-only and both models SHALL rerun under one fresh attempt; failed-model-only replacement and cross-attempt assembly are forbidden. (Current Design 3.1.0: §4.6, §6.4.8.1–§6.4.8.3, §8.3–§8.4.)

#### Scenario: A version attempt starts
- **WHEN** an admitted R0, R1 or R2 version is run
- **THEN** exactly one Qwen N=5 batch and one DeepSeek N=5 batch consume the same prompt/input/Schema/validator identities in isolated roots

#### Scenario: One model is infrastructure-invalid
- **WHEN** either model has a transport/provider/identity failure that makes its batch infrastructure-invalid
- **THEN** neither model report from that attempt enters assessment and a retry reruns both complete N=5 batches under a new attempt identity

#### Scenario: A failed trial is replaced
- **WHEN** a caller proposes an extra trial, failed-model-only rerun or trial from another attempt to complete a batch
- **THEN** assessment recomputation rejects the mixed or replacement evidence

### Requirement: Semantic repair is limited, issue-local and regression-free
The first Schema-valid candidate SHALL run the complete production `ARCH_VALIDATE`. A passing candidate SHALL stop at p0. A failing candidate SHALL be eligible for exactly one fresh no-history semantic-repair invocation containing the unchanged frozen inputs, the complete previous Schema-valid candidate and only the exact canonical validation issue list. The repair SHALL return the same full ArchitectureDraft contract. The controller SHALL publish canonical pre/post hashes, changed JSON paths, issue-to-change attribution and full post-repair validation; changed paths SHALL remain within the deterministic impact closure admitted for the reported issues, and no gate that passed before repair may regress. A missing, over-broad, unattributed or regressing repair SHALL count as trial failure at p1 and SHALL NOT be silently retried. (Current Design 3.1.0: §6.4.8.2.1, §8.4.)

#### Scenario: Initial candidate passes
- **WHEN** the first Schema-valid ArchitectureDraft passes all `arch_01` through `arch_10`
- **THEN** the trial records p0 success and performs no semantic-repair call

#### Scenario: One local repair succeeds
- **WHEN** the initial candidate fails and one repair changes only issue-attributed paths within the deterministic impact closure, preserves all previously passing gates and then passes complete `ARCH_VALIDATE`
- **THEN** the trial records p1 success together with canonical repair-diff and no-regression evidence

#### Scenario: Repair changes unrelated architecture decisions
- **WHEN** a repair changes any path outside the issue-derived impact closure or lacks deterministic attribution to the supplied issue list
- **THEN** the repair-locality verdict fails and the trial is not counted as p1 success even if the final draft passes `ARCH_VALIDATE`

#### Scenario: Repair regresses a passed gate
- **WHEN** complete post-repair validation fails any gate that passed on the initial Schema-valid candidate
- **THEN** the trial records the regression and fails the recovery p1 condition

#### Scenario: A second semantic repair is requested
- **WHEN** the first semantic repair remains invalid or semantically failing
- **THEN** the trial stops and no second semantic-repair Provider call is issued

### Requirement: Recovery screening prioritizes bounded repair success and retains p0 as diagnostic
A complete R0/R1/R2 assessment SHALL report each model separately with fixed N=5 denominators for Schema rates, p0, p1, every gate, issue co-occurrence, repair gain/regression/locality, calls, tokens, cost, latency, finish reason, truncation and identity. A version SHALL pass recovery screening only when each model independently has `schema_after_format_repair_rate = 1.00`, cumulative `p1 = 1.00`, zero truncation, valid infrastructure/identity, zero repair regression and complete passing repair-locality evidence for every repaired success. `p0`, repeated initial-gate failures and per-gate p0 rates SHALL remain mandatory diagnostics but SHALL NOT independently fail recovery screening. No cross-model average may hide one model. (Current Design 3.1.0: §6.4.8.2.1; normal M1-4a2 §6.4.8.2 retains its separate `p0 >= 0.80` development screen.)

#### Scenario: Both models pass after bounded local repair
- **WHEN** Qwen and DeepSeek each meet every recovery condition while one or more trials require exactly one valid local repair
- **THEN** the version passes recovery screening regardless of its p0 value and retains all first-pass failure evidence in the assessment

#### Scenario: One model misses p1 or locality
- **WHEN** either model has `p1 < 1.00`, a regressed gate, missing repair-diff evidence or a repair outside its admitted impact closure
- **THEN** the version fails recovery screening without averaging against the other model

#### Scenario: Only p0 is below the prior development threshold
- **WHEN** every recovery condition passes but one model has `p0 < 0.80`
- **THEN** the recovery assessment records that stability result but does not fail solely because of p0

### Requirement: Architecture-quality audit remains separate from mechanical validation
Every complete recovery version SHALL publish a canonical architecture-quality audit over all final Schema-valid candidates. The audit SHALL report, per model/trial, requirement-responsibility distribution and concentration, zero-responsibility work packages, task-ready contracts without consumers, task-contract interface-file shape, repair size, final gate status and the provenance of any blinded rubric review. The audit SHALL clearly distinguish deterministic measurements, reviewer judgments and unavailable downstream checks. It SHALL NOT mutate `ARCH_VALIDATE`, invent a hidden selection score, copy reviewer prose into the prompt, or claim that mechanical pass proves engineering quality. Any new normative quality gate SHALL require separate responsible-owner design authorization. (Current Design: §6.4.8 calibration boundary, §11.3; experiment Phase 0/1 evaluator findings.)

#### Scenario: A version assessment is complete
- **WHEN** all valid model trials have final candidates and validations
- **THEN** the quality audit deterministically reports every declared structural indicator and binds each source candidate/validation by path and hash

#### Scenario: Blind review is unavailable
- **WHEN** the declared independent review or downstream consumer check cannot run validly
- **THEN** the audit records it as unavailable and does not fabricate, impute or convert it into a passing score

#### Scenario: Quality red flags exist in a passing version
- **WHEN** a mechanically passing candidate has concentrated ownership, a zero-responsibility work package, a consumer-less task contract or another declared red flag
- **THEN** the handoff preserves the red flags and does not represent recovery screening as proof of production architecture quality

### Requirement: Recovery selection and handoff are unique, immutable and limited to M1-4a3 admission
The first recovery version passing the recovery gate SHALL produce exactly one immutable selection and technical handoff bound to the new lineage, approved design revision, prompt snapshot, coherent two-model attempt, reports, assessment, repair-diff set and architecture-quality audit. Repository ArchitecturePlanner prompt bytes SHALL match the selected snapshot before handoff. The handoff SHALL identify the predecessor M1-4a2 tie without changing its status and SHALL admit only M1-4a3 formal qualification. It SHALL NOT qualify a production model, select a production repair budget/call shape, satisfy N=20/B1-B4, create a formal Run/S4/Plan/Blueprint/report, or provide the responsible-owner production signature. (Current Design 3.1.0: §6.4.8.2.1–§6.4.8.3, §9.1.5, D1.0.)

#### Scenario: First recovery version passes
- **WHEN** R0, R1 or R2 is the first complete version satisfying every recovery condition and its source/snapshot/evidence hashes verify
- **THEN** selection and handoff are published once and all later recovery edits/calls are rejected

#### Scenario: No recovery version passes
- **WHEN** terminal R2 fails or a failing R0/R1 version has no evidence-admissible next revision
- **THEN** recovery publishes explicit no-selection and M1-4a3 remains blocked

#### Scenario: Handoff is presented as production qualification
- **WHEN** a caller attempts to use recovery selection as N=20 evidence, B1-B4 disposition, production model/call-shape/budget freeze or a downstream S4/S5/S6 artifact
- **THEN** the system rejects that use because the recovery handoff only admits M1-4a3

### Requirement: Recovery evidence is reproducible, secret-free and non-downstream
Recovery protocol, predecessor attestation, provenance snapshots, attempt, revision, prompt snapshot, trial, repair-diff, report, assessment, quality-audit, selection and handoff artifacts SHALL be canonical, hash-bound, append-only and fully recomputable from their declared parents. Only initialization SHALL accept closed workspace-relative, path-confined source locators for the exact predecessor root, authorization/design bytes and allowlisted experiment reports/seed; it SHALL publish a local inventory/attestation or copy the verified small inputs into recovery-local snapshots. Every trial and later aggregate SHALL use recovery-root-relative refs only. The recovery root SHALL store only fixed environment-variable names and non-secret Provider/model/request projections; it SHALL never store API key values or read shell startup/dotenv files. Recovery candidates SHALL remain calibration-only and SHALL NOT be consumable by TaskPlanner, Linker, PlanCritic, S5 or S6. (Current Design 3.1.0: §6.4.8, §8.3, §9.1.5.)

#### Scenario: Complete recovery evidence is recomputed
- **WHEN** every declared artifact and hash-bound parent is present and valid
- **THEN** recomputation reproduces the canonical reports, assessment, quality audit, selection and handoff bytes

#### Scenario: Evidence is missing or substituted
- **WHEN** an artifact is missing, mutated, duplicated or taken from another lineage/version/model/attempt
- **THEN** recomputation fails and publishes no replacement selection or handoff over the invalid root

#### Scenario: An external source is proposed after initialization
- **WHEN** a trial, repair, report, assessment, quality audit or terminal record references predecessor/experiment/workspace bytes instead of an admitted recovery-local parent
- **THEN** Schema or recomputation rejects it as cross-boundary evidence

#### Scenario: Provider credentials are prepared
- **WHEN** recovery preflight checks Qwen and DeepSeek access
- **THEN** it requires the authorized fixed process-environment variable names to be non-empty without outputting or persisting their values and does not source shell startup or dotenv files

#### Scenario: A calibration candidate is sent downstream
- **WHEN** a caller attempts to consume an R0/R1/R2 candidate as a formal plan or downstream input
- **THEN** the operation is rejected because M1-4a2r creates no formal S4 commit point
