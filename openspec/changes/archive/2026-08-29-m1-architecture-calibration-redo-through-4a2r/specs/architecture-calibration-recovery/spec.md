## MODIFIED Requirements

### Requirement: Recovery activation requires explicit design authority
The system SHALL NOT initialize M1-4a2r, modify the repository ArchitecturePlanner prompt, or perform recovery Provider I/O unless the new free-layout M1-4a2 predecessor has complete V0/V1/V2 evidence recomputing to `PROMPT_SELECTION_TIE` and a responsible-owner authorization binds that predecessor plus the current approved `project_docs/system_design.md` path/SHA-256. OpenSpec artifacts, historical tie/recovery roots, reports, code behavior, or available credentials SHALL NOT substitute for authorization. If new M1-4a2 selects a prompt normally, recovery SHALL remain unexecuted and publish or retain a recomputable `not_triggered` decision. (Design: `system_design.md` §6.4.8.2～§6.4.8.2.1, §10.2 M1-4a2r, D1.0.)

#### Scenario: New development selected a prompt
- **WHEN** the new free-layout M1-4a2 root has a valid selection/handoff instead of `PROMPT_SELECTION_TIE`
- **THEN** recovery initialization and Provider I/O are rejected and the conditional result is `not_triggered`

#### Scenario: No matching owner authorization exists
- **WHEN** a caller presents a valid new tie but no responsible-owner authorization matching its predecessor and current design hash
- **THEN** recovery fails before prompt publication, lineage creation, repository modification, or Provider I/O

#### Scenario: Approved recovery is bound
- **WHEN** the responsible owner has approved recovery for the exact new predecessor and the referenced design bytes match
- **THEN** initialization may continue and every later artifact retains both identities

#### Scenario: Design or predecessor authority drifts
- **WHEN** the bound design bytes, predecessor hash graph, or authorization identity changes before an attempt or handoff
- **THEN** the attempt becomes non-selectable and no further Provider call or handoff is admitted under that recovery lineage

### Requirement: A terminal M1-4a2 tie admits only a fresh isolated recovery lineage
M1-4a2r SHALL accept only a complete, immutable predecessor produced by this free-layout, fifteen-gate, three-slot M1-4a2 workflow. It SHALL recompute V0/V1/V2 assessments, fixed fallback tuples and `PROMPT_SELECTION_TIE`, prove selection/handoff absent, and publish a canonical local predecessor attestation. Recovery SHALL create a distinct lineage that freezes its own prompt-excepted controls and repair-locality evidence definition; it SHALL NOT append to, rewrite, complete, or aggregate with the predecessor. Historical fixed-layout, ten-gate, dual-model ties and recoveries SHALL be provenance only and SHALL NOT admit recovery. (Design: `system_design.md` §6.4.8.1～§6.4.8.2.1, §9.2; D1.0.)

#### Scenario: Valid new terminal tie is supplied
- **WHEN** the new predecessor recomputes to a complete V0/V1/V2 tie with free layout, fifteen gates and all three logical slots
- **THEN** recovery creates a distinct lineage and immutable predecessor attestation without copying any prior trial into an R0/R1/R2 denominator

#### Scenario: Historical tie is supplied
- **WHEN** the predecessor lacks the current layout convention, `architecture.layout`, any of `arch_11`～`arch_15`, or the Claude logical slot
- **THEN** recovery rejects it as an obsolete predecessor without mutating its bytes

#### Scenario: A predecessor trial is proposed as recovery evidence
- **WHEN** a caller attempts to count any V0/V1/V2 request, response, candidate, validation or metric in R0/R1/R2
- **THEN** recomputation rejects the recovery batch as cross-lineage evidence

#### Scenario: Predecessor has a selection
- **WHEN** the supplied predecessor already contains a valid selected prompt and handoff
- **THEN** recovery initialization is rejected because the conditional branch was not triggered

### Requirement: Recovery uses one shared protocol-neutral exact-algorithm prompt
R0 SHALL use one immutable shared prompt derived from owner-authorized, new-lineage predecessor diagnostics and protocol-neutral experience, not from an old trial or fixed historical seed contract. It SHALL express the free-layout construction and verification procedure for requirements, modules, contracts, work-package partitions, `architecture.layout`, build-graph closure, layering, path neutrality, test readiness, and issue-local repair. Qwen, Claude and DeepSeek SHALL render from identical raw template bytes with no target-protocol, model or Provider branch; concrete identifiers SHALL enter only from frozen named inputs and the selected protocol-neutral convention. (Design: `system_design.md` §6.4, §6.4.8.2～§6.4.8.2.1, §8.8; `pipeline_design_s4_s9.md` §5.2; D1.11.)

#### Scenario: R0 is admitted
- **WHEN** the authorized recovery input, repository source bytes, immutable R0 snapshot and neutrality scan all agree
- **THEN** all three model slots render from exactly that snapshot with unchanged frozen inputs and free-layout output contract

#### Scenario: Prompt contains target or model specialization
- **WHEN** R0/R1/R2 embeds a protocol fact, REQ id, target path/interface constant, model/Provider identity or conditional branch
- **THEN** version admission fails before Provider I/O

#### Scenario: Prompt source and snapshot differ
- **WHEN** repository prompt bytes, admitted snapshot bytes, recorded hash or actual Provider render cannot be reconstructed as one identity
- **THEN** the attempt is invalid and cannot publish an assessment or selection

### Requirement: Recovery prompt development is bounded to R0 and two evidence-backed revisions
The recovery sequence SHALL contain R0 and at most R1 plus optional R2. R1 SHALL require complete failing R0 evidence and one falsifiable prompt/locality hypothesis; R2 SHALL require complete failing R1 evidence and a second distinct hypothesis. Each revision SHALL bind the immediately preceding prompt hash, exact evidence, one hypothesis, exact diff, expected effect and stopping conclusion, and SHALL change only prompt bytes. The first passing version SHALL stop later edits/calls. A failing R2 or a failure without an admissible next hypothesis SHALL restore/hash-check the pre-recovery prompt before publishing `no_selection`; restoration failure SHALL remain nonterminal. No R3, recursive recovery, N=10 extension, or fallback ranking is allowed. (Design: `system_design.md` §6.4.8.2.1.)

#### Scenario: R0 satisfies the recovery gate
- **WHEN** the complete R0 three-model assessment passes every recovery condition
- **THEN** R0 is selected immediately and all R1/R2 admission and Provider I/O are rejected

#### Scenario: R0 supports one prompt defect
- **WHEN** complete R0 fails and exact evidence supports one falsifiable hypothesis
- **THEN** one R1 revision may be admitted with a fresh complete three-model attempt

#### Scenario: R1 supports a distinct second defect
- **WHEN** complete R1 fails and exact evidence supports a second hypothesis distinct from R1's
- **THEN** one R2 revision may be admitted with a fresh complete three-model attempt

#### Scenario: Recovery ends without selection
- **WHEN** R2 fails or a failing R0/R1 has no admissible next hypothesis
- **THEN** no extra version is allowed and `no_selection` is published only after verified restoration of the pre-recovery prompt

### Requirement: Every recovery version uses one coherent fresh three-model N=5 attempt
Each R0/R1/R2 attempt SHALL run exactly five fresh trials for each logical slot Qwen, Claude and DeepSeek with identical lineage-bound inputs, prompt snapshot, free-layout ArchitectureDraft, fifteen-gate validator, temperature and `max_tokens=65536`. Trials and repairs SHALL be isolated, no-history and cache-disabled. If any slot is infrastructure-invalid, the whole attempt SHALL remain audit-only and a retry SHALL rerun all three complete batches; replacement or cross-attempt assembly is forbidden. Configured/provider-returned model strings SHALL be recorded but variation SHALL NOT split or invalidate a batch. (Design: `system_design.md` §4.6, §6.4.8.1～§6.4.8.2.1, §8.3～§8.4, §9.2.)

#### Scenario: A version attempt starts
- **WHEN** R0, R1, or R2 is admitted
- **THEN** one Qwen N=5, one Claude N=5 and one DeepSeek N=5 batch consume the same controlled identities in isolated logical-slot roots

#### Scenario: One slot is infrastructure-invalid
- **WHEN** any slot fails to obtain a model response after bounded transport/provider retries
- **THEN** no report from that attempt enters assessment and a retry reruns all three N=5 batches under a new attempt identity

#### Scenario: A model string changes
- **WHEN** a slot observes different configured/returned model strings during the attempt
- **THEN** every call remains under the stable slot and the assessment reports the strings/shares without invalidating the attempt

#### Scenario: A failed trial is replaced
- **WHEN** a caller proposes an extra trial, one-slot rerun, or trial from another attempt to complete a batch
- **THEN** recovery recomputation rejects the mixed evidence

### Requirement: Semantic repair is limited, issue-local and regression-free
The first Schema-valid candidate SHALL run complete production `ARCH_VALIDATE` across `arch_01`～`arch_15`. A passing candidate SHALL stop at p0. A failing candidate may receive exactly one fresh no-history repair using unchanged frozen inputs, the full prior candidate and only exact canonical failures. The repair SHALL return the same full free-layout ArchitectureDraft. The controller SHALL publish canonical pre/post hashes, changed paths, issue attribution, admitted locality closure and full revalidation. A missing, over-broad, unattributed, regressing, or still-failing repair SHALL be a p1 failure without another repair. (Design: `system_design.md` §6.4.8.2.1; `pipeline_design_s4_s9.md` §5.2.4.)

#### Scenario: Initial candidate passes all fifteen gates
- **WHEN** the first Schema-valid draft passes `arch_01` through `arch_15`
- **THEN** the trial records p0 success and performs no semantic repair

#### Scenario: One local repair succeeds
- **WHEN** one repair changes only issue-attributed paths inside its closed policy and the final draft passes all fifteen gates
- **THEN** the trial records p1 success with recomputable diff/locality/full-validation evidence

#### Scenario: Repair changes an unrelated layout or decision
- **WHEN** a repair changes any path outside the issue-derived closure or lacks attribution
- **THEN** locality fails and the trial is not counted as p1 success even if the final draft otherwise validates

#### Scenario: A second semantic repair is requested
- **WHEN** the first semantic repair is invalid or still fails validation
- **THEN** the trial stops and no second repair call occurs

### Requirement: Recovery screening prioritizes bounded repair success and retains first-pass diagnostics
A complete R0/R1/R2 assessment SHALL report each logical slot separately with fixed N=5 denominators for Schema rates, p0, p1, every one of fifteen gates, issue co-occurrence, repair gain/regression/locality, calls, tokens, cost, latency, finish reason, truncation, parameter support and model strings/shares. A version SHALL pass only when Qwen, Claude and DeepSeek each independently have `p1 ≥ 0.80`, zero truncation, no infrastructure-invalid batch, complete recomputable repair diff/locality evidence, and a handoff candidate that passes full `ARCH_VALIDATE`. `schema_after_format_repair_rate`, p0, per-gate first-pass rates, repeated first-pass failures, quality audit and model-string variation SHALL be diagnostics only and SHALL NOT add another rate hard gate. No cross-slot average may hide failure. (Design: `system_design.md` §6.4.8.2～§6.4.8.2.1, revision 5.1.0; D1.0.)

#### Scenario: Every slot has four p1 successes of five
- **WHEN** all three slots have `p1=0.80`, zero truncation, valid infrastructure/locality evidence, and complete passing final candidates
- **THEN** the version passes even if Schema-after-format-repair, p0, or repeated first-pass diagnostics are below historical thresholds

#### Scenario: One slot misses p1
- **WHEN** any slot has `p1 < 0.80`
- **THEN** the version fails screening without averaging against the other slots

#### Scenario: Locality evidence is incomplete
- **WHEN** a repair used for p1 lacks a recomputable diff/attribution/locality record
- **THEN** that trial cannot contribute p1 success and the version is assessed accordingly

#### Scenario: An extra rate hard gate is proposed
- **WHEN** recovery configuration requires Schema rate 1.00, p0, first-pass rate, repeated-gate rate, or a p1 threshold at/above B1
- **THEN** protocol validation rejects the non-monotonic recovery contract before trials

### Requirement: Architecture-quality audit remains separate from mechanical validation
Every complete recovery version SHALL publish a canonical architecture-quality audit over final Schema-valid candidates, including responsibility distribution, empty-responsibility work packages, consumer-less task contracts, interface/layout shape, repair size and final gate status, with typed provenance or explicit unavailability for any blinded review. Audit values SHALL NOT mutate `ARCH_VALIDATE`, alter screening, enter the prompt, or claim that a mechanical pass proves production quality. (Design: `system_design.md` §6.4.8.2.1, §11.3.)

#### Scenario: A version assessment is complete
- **WHEN** all valid slot trials have final candidates and validation evidence
- **THEN** the audit reports each declared structural indicator and binds its sources by path/hash

#### Scenario: Quality red flags exist in a passing version
- **WHEN** a mechanically passing candidate contains a diagnostic red flag
- **THEN** the handoff preserves it without changing recovery selection

### Requirement: Recovery selection and handoff are unique, immutable and limited to M1-4a3 admission
The first R0/R1/R2 version passing recovery SHALL publish exactly one immutable selection and handoff bound to the current design authorization, new predecessor tie, recovery lineage, prompt snapshot, coherent three-model attempt, reports, assessment, repair evidence and quality audit. Repository prompt bytes SHALL match the selected snapshot. The handoff SHALL admit only M1-4a3's three-model N=10 formal qualification and SHALL NOT satisfy B1～B4, production model/call-shape/budget freeze, formal Run/S4/Plan/Blueprint/report, or responsible-owner production signature. (Design: `system_design.md` §6.4.8.2.1～§6.4.8.3, D1.0.)

#### Scenario: First recovery version passes
- **WHEN** R0, R1 or R2 is the first complete version satisfying every recovery condition
- **THEN** selection/handoff are published once and all later recovery edits/calls are rejected

#### Scenario: No recovery version passes
- **WHEN** recovery reaches a valid terminal `no_selection`
- **THEN** M1-4a3 remains blocked and no historical prompt is substituted

#### Scenario: Handoff is presented as production qualification
- **WHEN** a caller attempts to use recovery as N=10/B1～B4 evidence, a production freeze, or downstream S4 artifact
- **THEN** the use is rejected because the handoff only admits M1-4a3

### Requirement: Recovery evidence is reproducible, secret-free and non-downstream
Recovery protocol, predecessor attestation, local provenance, attempt, revision, prompt, trial, repair, report, assessment, quality-audit, selection/no-selection and handoff artifacts SHALL be canonical, hash-bound, append-only and recomputable. Later artifacts SHALL use recovery-root-relative refs. Evidence SHALL store only fixed environment-variable names and non-secret slot/provider/request projections; it SHALL never store credentials or read shell/dotenv files. The result report SHALL cite only new recovery lineage leaves and SHALL present all three slots separately, including model-string sets/shares. Recovery candidates SHALL not be consumable by TaskPlanner, Linker, PlanCritic, S5 or S6. (Design: `system_design.md` §6.4.8, §8.3, §9.2; D1.0.)

#### Scenario: Complete recovery evidence is recomputed
- **WHEN** every declared artifact and hash-bound parent is present and valid
- **THEN** recomputation reproduces reports, assessment, quality audit, selection/no-selection and handoff bytes, and the version-controlled result report agrees

#### Scenario: Evidence is missing or substituted
- **WHEN** an artifact is missing, mutated, duplicated or taken from another lineage/version/slot/attempt
- **THEN** recomputation fails and publishes no replacement selection or handoff

#### Scenario: Provider credentials are prepared
- **WHEN** recovery preflight checks access for Qwen, Claude and DeepSeek
- **THEN** it requires the three design-declared process environment variable names without outputting/persisting their values or sourcing shell/dotenv files

#### Scenario: A calibration candidate is sent downstream
- **WHEN** a caller attempts to consume R0/R1/R2 as a formal plan or S5/S6 input
- **THEN** the operation is rejected because M1-4a2r creates no formal S4 commit point
