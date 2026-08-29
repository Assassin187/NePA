## Purpose

Define the bounded, three-model M1-4a2 workflow that develops one protocol-neutral ArchitecturePlanner prompt on the new free-layout production contract and produces a recomputable selection or explicit tie for the next authorized stage.

## ADDED Requirements

### Requirement: Development starts only on a complete new free-layout lineage
M1-4a2 SHALL start only after M1-4a1 has frozen a lineage containing `architecture.layout`, the selected layout-convention asset, `arch_01`～`arch_15`, and exactly the `qwen/claude/deepseek` logical slots at `max_tokens=65536`. Within one development lineage, only shared prompt bytes SHALL vary; a change to any other controlled component SHALL require a new lineage and invalidate comparison with prior versions. Fixed-layout, ten-gate, dual-model, development-recovery, or other historical trials SHALL NOT enter the new lineage. (Design: `system_design.md` §6.4.8.1～§6.4.8.2, revisions 4.0.0/4.2.0/5.1.0; D1.0.)

#### Scenario: New M1-4a1 lineage is complete
- **WHEN** its frozen inputs, convention, Schema/example, serializer, fifteen-gate validator, three logical slots, request parameters and statistics all verify
- **THEN** M1-4a2 may admit V0 and records those controlled identities in its development protocol

#### Scenario: Old evidence is proposed
- **WHEN** a caller offers any trial lacking free layout, all fifteen gates, or one of the three logical slots
- **THEN** development rejects it from assessments, fallback comparison, selection and handoff while leaving the historical bytes unchanged

#### Scenario: A non-prompt control changes between versions
- **WHEN** V1 or V2 proposes a Schema, validator, convention, input, slot, endpoint, request-parameter, context-bound or statistic change
- **THEN** it is rejected as a mixed-variable revision and cannot be attributed to prompt development

### Requirement: One shared protocol-neutral prompt is developed through V0, V1 and optional V2
The workflow SHALL use one model-independent ArchitecturePlanner prompt for all three logical slots. V0 SHALL run first. V1 SHALL be admitted only from a complete failing V0 and one evidence-backed, falsifiable prompt-defect hypothesis. V2 SHALL be admitted only from a complete failing V1 and a second distinct evidence-backed hypothesis. Each revision SHALL bind the preceding prompt hash, exact evidence refs, one hypothesis, exact diff, expected affected gates/diagnostics and stopping conclusion. No model-specific prompt, R3, extra edit, protocol-specific constant, or open-ended tuning SHALL be allowed. (Design: `system_design.md` §6.4.8.2, §8.8.)

#### Scenario: V0 passes screening
- **WHEN** complete V0 evidence satisfies the screening gate for all three slots
- **THEN** V0 is selected immediately and V1/V2 edits and Provider calls are rejected

#### Scenario: V0 supports one prompt defect
- **WHEN** V0 fails and exact diagnostics support one declared falsifiable hypothesis
- **THEN** one V1 prompt revision may be admitted while every non-prompt component remains unchanged

#### Scenario: A model-specific branch is proposed
- **WHEN** a candidate prompt names or branches on Qwen, Claude, DeepSeek, a Provider, or target-protocol identity
- **THEN** prompt admission fails before Provider I/O

#### Scenario: A third prompt modification is attempted
- **WHEN** V2 has already been admitted
- **THEN** no additional development version or prompt edit is allowed

### Requirement: Every development version uses coherent isolated three-model trials
Each base V0/V1/V2 attempt SHALL contain fresh N=5 batches for Qwen, Claude and DeepSeek using the same lineage, prompt hash, frozen inputs, free-layout contract, validator, temperature and `max_tokens=65536`. Every trial SHALL have no history and no cross-trial cache; each slot SHALL use distinct evidence/session/cache/trace roots. If any slot batch is infrastructure-invalid, the whole version attempt SHALL be audit-only and a retry SHALL rerun all three complete batches under a new attempt. V1/V2 may extend to N=10 only when the complete N=5 evidence meets the design's single-sample-sensitivity or metric-conflict predicate, preserving trials 001～005 and appending exactly 006～010 for all three slots. (Design: `system_design.md` §4.6, §6.4.8.2, §8.3～§8.4, §9.2.)

#### Scenario: A base attempt starts
- **WHEN** V0, V1, or V2 is admitted
- **THEN** exactly three isolated N=5 slot batches are declared before calls and no trial is replaced or shared

#### Scenario: One slot is infrastructure-invalid
- **WHEN** any slot attempt exhausts transport/provider retries without a valid model response
- **THEN** no slot report from that attempt enters screening and a new attempt reruns all three N=5 batches

#### Scenario: An N=10 extension is evidence-admissible
- **WHEN** complete V1/V2 N=5 evidence satisfies one specified ambiguity predicate
- **THEN** exactly five new trials per slot are appended under the same prompt/lineage/control identities and the N=10 assessment uses exactly trials 001～010

#### Scenario: A returned model alias changes
- **WHEN** a slot observes a different configured or returned model/version string during a coherent attempt
- **THEN** the call remains in the declared denominator and the alias is recorded without splitting or invalidating the attempt

### Requirement: Development screening is a monotonic p1 gate with diagnostic first-pass metrics
A version SHALL pass development screening only when Qwen, Claude and DeepSeek each independently have cumulative `p1 ≥ 0.80`, zero truncation, and no infrastructure-invalid batch. `schema_after_format_repair_rate`, `arch_semantic_first_pass_rate`, `p0`, per-gate first-pass rates, repeated first-pass gate failures and model-identifier variation SHALL remain required diagnostics but SHALL NOT independently reject a version whose bounded one-repair `p1` closes. No cross-model average may hide a failing slot. The screening rate SHALL remain strictly below the M1-4a3 B1 `p1 ≥ 0.90` rate and SHALL introduce no rate hard gate absent from B1. (Design: `system_design.md` §6.4.8.2, revision 5.1.0; D1.0.)

#### Scenario: Every slot has four p1 successes of five
- **WHEN** all three coherent N=5 reports have `p1=0.80`, zero truncation and valid infrastructure
- **THEN** the version passes screening even when Schema-after-format-repair, p0, first semantic-pass, or repeated-gate diagnostics are below their historical thresholds

#### Scenario: One slot has three p1 successes of five
- **WHEN** Qwen or Claude or DeepSeek has `p1=0.60` while the other two pass
- **THEN** the version fails screening without averaging across slots

#### Scenario: A response is truncated
- **WHEN** any trial in a development version is truncated
- **THEN** that version is not a valid selection candidate even if its numerical p1 would otherwise meet 0.80

#### Scenario: A stricter preliminary rate is configured
- **WHEN** development configuration attempts to require `p1 ≥ 0.90`, Schema rate 1.00, first-pass rate 0.80, or another rate hard gate absent from B1
- **THEN** protocol validation rejects the inverted/non-monotonic screening contract before trials

### Requirement: Fallback and tie resolution use only complete final assessments
The first screening-passing version SHALL be selected immediately. If V2 completes with no screening-passing version, the workflow SHALL compare each complete final V0/V1/V2 assessment lexicographically by minimum three-slot `p1`, then minimum three-slot first semantic-pass rate, then minimum three-slot Schema-after-format-repair rate, then lower total cost. It SHALL store all comparison tuples and SHALL NOT use averages, extra samples, model identity, quality prose, or another tiebreaker. A complete equality SHALL publish `PROMPT_SELECTION_TIE` with no prompt selection or M1-4a3 handoff. (Design: `system_design.md` §6.4.8.2.)

#### Scenario: One version is first to pass screening
- **WHEN** a complete version satisfies the three-slot gate before later versions are admitted
- **THEN** it is selected and no fallback ranking or later version is run

#### Scenario: No version passes and one tuple wins
- **WHEN** complete V0/V1/V2 all fail screening and one lexicographic tuple is uniquely maximal under the fixed order
- **THEN** that exact version is selected as the M1-4a3 prompt candidate without claiming production qualification

#### Scenario: All fallback tuples tie
- **WHEN** complete V0/V1/V2 comparison tuples are exactly equal
- **THEN** the workflow records `PROMPT_SELECTION_TIE`, publishes no selection/handoff, and admits only the separately owner-approved M1-4a2r branch

### Requirement: Development evidence and the new report are recomputable and scope-limited
The workflow SHALL publish canonical, hash-bound protocol, prompt snapshot, attempt, trial, validation, report, assessment, revision, optional extension, selection/tie and technical-handoff artifacts under the new lineage. A version-controlled preregistration and result report SHALL cite only this new lineage and SHALL present Qwen/Claude/DeepSeek separately with fixed denominators, all fifteen gates, repair gain, truncation/infrastructure status, cost/latency, parameter support, observed model-string sets/shares, and the exact selection/tie reason. Recomputing from leaves SHALL reproduce all machine aggregates; no old report number or model prose SHALL satisfy a gate. The handoff SHALL admit only M1-4a3 and SHALL NOT create a formal Run, S4 receipt, Plan, Blueprint, B1～B4 decision, production freeze, or owner signature. (Design: `system_design.md` §6.4.8, §9.2, §10.2 D1.0/D1.11.)

#### Scenario: Complete development evidence is recomputed
- **WHEN** every declared new-lineage leaf and hash-bound parent is intact
- **THEN** recomputation reproduces the selected assessment/tie and machine summary, and the Markdown report's numbers match those aggregates

#### Scenario: A historical report value is copied into the new result
- **WHEN** the new report contains a metric with no path/hash and recomputable source in the new lineage
- **THEN** report validation fails and the value cannot support completion

#### Scenario: Development selects a prompt normally
- **WHEN** V0/V1/V2 produces a valid selection without a tie
- **THEN** the workflow publishes the M1-4a3-only handoff and records M1-4a2r as `not_triggered` rather than running recovery

#### Scenario: Handoff is used as production qualification
- **WHEN** a caller presents development selection as an M1-4a3 B1～B4 result or formal downstream artifact
- **THEN** the use is rejected because M1-4a2 only chooses the prompt entering formal qualification
