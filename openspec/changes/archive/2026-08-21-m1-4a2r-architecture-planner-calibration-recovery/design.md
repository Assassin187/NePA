## Context

M1-4a2 completed its authorized V0/V1/V2 prompt-development sequence but produced no selectable version. All three fallback tuples were `[0,0,1,0]`, so final selection deterministically raised `PROMPT_SELECTION_TIE` and published neither `selection.json` nor a handoff. The predecessor `protocol.json` therefore still says `status = initialized`; its failed-development terminal meaning comes from the complete V0/V1/V2 evidence and recomputed tie, not a stored terminal-state mutation. The immutable predecessor lineage is `daa917e4c0362d5bce575df3e1ef7436f35942aa0075ba21e3f432ca4ce48772`.

The follow-up experiments established two useful boundaries:

- A protocol-neutral exact-algorithm prompt reached final `p1 = 1.00` for both Qwen and DeepSeek in fresh N=5 samples, although Qwen first-pass stability remained `p0 = 0.40`.
- Fixed three-stage generation did not improve Qwen initial validity and increased calls, input tokens and latency. Passing mechanical validation also left visible architecture-quality red flags, so validator success and architecture quality must remain separate claims.

The authoritative design now preserves M1-4a2's V0/V1/V2, `p0 >= 0.80` development screen and existing fallback/tie result, while version 3.1.0 adds M1-4a2r as a conditional post-tie recovery branch. Implementation, prompt publication and Provider I/O remain gated on the recorded responsible-owner authorization and the exact approved `project_docs/system_design.md` bytes/hash.

The intended implementation extends the existing calibration path rather than creating another generation stack:

- `nepa/calibration/s4_prompt_development.py` provides the current prompt-version coordinator, screening and selection evidence.
- `nepa/calibration/s4_architecture.py` provides fresh trials, one-step semantic repair, canonical trial evidence and report recomputation.
- `nepa/speclib/architecture.py` and the existing ArchitectureDraft/validation Schemas remain the production contract and validator path.
- `nepa/agents/prompts/architecture_planner.md` remains the only repository prompt source.

## Goals / Non-Goals

**Goals:**

- Recover from the terminal M1-4a2 tie through a fresh, isolated and reproducible calibration lineage.
- Optimize for reliable success after at most one issue-local semantic repair, while retaining first-pass behavior as a mandatory diagnostic.
- Bound prompt development to R0 and, only when supported by complete failure evidence, at most two revisions: R1 and optional R2.
- Make repair behavior auditable through deterministic pre/post diffs, issue attribution, full revalidation and no-regression checks.
- Inspect final architecture quality independently of `ARCH_VALIDATE` without silently changing validator semantics.
- Produce one immutable handoff that admits only M1-4a3 formal qualification.

**Non-Goals:**

- Editing or weakening `ARCH_VALIDATE`, its ten gates, ArchitectureDraft, frozen gold inputs, Delivery Constraints, model set, temperature or `max_tokens`.
- Claiming high first-pass stability, production architecture quality, production model qualification or downstream readiness from an M1-4a2r pass.
- Using the fixed three-stage B3 strategy, more than one semantic repair, model-specific prompts, fallback ranking among failures or open-ended tuning.
- Reusing any V0/V1/V2 or experiment request, response, candidate or validation as a recovery trial.
- Running TaskPlanner, Linker, PlanCritic, S5/S6, formal N=20 qualification or production S4 commits.
- Broadening the responsible owner's minimal design authorization beyond the recorded M1-4a2r delta.

## Decisions

### 1. Activation is a hard design-authorization boundary

The recovery coordinator starts with a `design_blocked` state. Initialization requires an authorization record that identifies the responsible owner, approved recovery protocol revision and SHA-256 of `project_docs/system_design.md`. The code verifies those exact bytes before it creates lineage evidence, changes the prompt or contacts a Provider.

The authorized design revision explicitly defines:

- the M1-4a2r entry after a terminal M1-4a2 tie;
- R0/R1/R2 limits and stop conditions;
- `p0` as a recovery diagnostic rather than a hard selection gate;
- repair-locality and no-regression requirements;
- the relationship of M1-4a2r to M1-4a3 and D1.0.

This is intentionally fail-closed. An OpenSpec artifact, experiment report, code constant or user's API environment does not stand in for the design authorization. If the bound design hash changes mid-run, the current attempt becomes non-selectable and no further call or handoff is permitted.

### 2. The old and new changes form a failed-predecessor/recovery-successor chain

M1-4a2 remains an immutable terminal-by-evidence failed-development predecessor. Its 34/36 task state, `status = initialized`, V0/V1/V2 trials, assessments, recomputed tie and missing handoff are not rewritten or marked complete.

Recovery initialization publishes a canonical local `predecessor-attestation.json`. A dedicated legacy-evidence verifier checks the workspace-confined predecessor inventory and hash graph against the Schemas/component bundles frozen inside that root, requires complete V0/V1/V2 assessments and outcomes, recomputes each stored fallback tuple and the exact tie, and proves `selection.json` is absent. It must not call the ordinary current-lineage loader's live-component check: repair/locality/reporting changes in M1-4a2r intentionally make the current component hashes differ from the old lineage. The attester does not rerun the current validator, rewrite the old root or make old trials eligible for recovery statistics.

M1-4a2r owns all R0/R1/R2 records under a distinct recovery evidence root. No old or experimental sample contributes to an R0/R1/R2 denominator. A successful M1-4a2r handoff becomes the only prompt-development input admitted to M1-4a3; if recovery produces no selection, M1-4a3 remains blocked.

This relationship preserves the reason the earlier change could not finish instead of retroactively making it look successful.

### 3. A genuinely changed evidence protocol creates the new lineage

A new directory name or prompt revision alone is not a valid lineage change. R0/R1/R2 share a new lineage because the controlled evidence protocol changes in three material, hash-bound ways:

1. recovery screening makes `p0` diagnostic and requires final one-repair success, repair locality and zero regression;
2. each repair publishes deterministic candidate diffs and issue-impact closure evidence;
3. each assessment includes a separately typed architecture-quality audit and corrected repair-outcome recomputation.

The recovery protocol record includes hashes of the Schema, validator, serializer, input builder, repair-impact policy, reporting code, model/request configuration and metric definitions. R1 and R2 may change only prompt bytes inside this lineage. A change to any other bound component requires a different lineage.

### 4. R0 is seeded from the validated algorithm, but all recovery evidence is fresh

The experiment's exact-algorithm prompt is hypothesis evidence, not a recovery candidate. Its authorized source is `experiments/m1-4a2-architecture-planner-prompt-optimization/results/phase1/artifacts/prompt-exact-algorithm.md`, whose required SHA-256 is `d5c24f1939f3a767f2cd1d7a116124d4b5ea32552391664052a93f24f1914b85`. After design authorization, initialization copies those exact bytes and the declared root-cause reports into recovery-local provenance snapshots. R0 is created by applying that locally snapshotted algorithm to the repository prompt source, then checking:

- source bytes equal the immutable admitted snapshot;
- the prompt passes existing structure and protocol-neutrality scans;
- Qwen and DeepSeek render from the same template bytes;
- every concrete requirement, file, protocol and interface identifier comes only from named frozen inputs.

The recovery root also snapshots the authorization and approved design bytes. Source locations are workspace-relative, allowlisted and hash-bound; after snapshot publication all R0/R1/R2 records refer only to recovery-local provenance. It never copies an experimental candidate, response or trial metric. Both models generate all ten R0 candidates through new Provider calls.

### 5. Recovery is a three-version state machine, not fallback ranking

The deterministic state sequence is:

```text
design_blocked
  -> initialized
  -> r0_declared -> r0_running -> r0_assessed
       -> selected_r0
       -> r1_revision_declared -> r1_running -> r1_assessed
            -> selected_r1
            -> r2_revision_declared -> r2_running -> r2_assessed
                 -> selected_r2
                 -> rollback_pending -> no_selection
            -> rollback_pending -> no_selection  (when no R2 is evidence-admissible)
       -> rollback_pending -> no_selection  (when no R1 is evidence-admissible)
```

R0 runs first. If its coherent two-model assessment passes, selection is final and later revisions are forbidden. If R0 fails, R1 is admitted only after an immutable revision declaration cites exact R0 evidence, states one falsifiable prompt/repair defect, shows the exact prompt diff and predicts the affected metric or gate. If complete R1 also fails, R2 is admitted only when a second immutable declaration cites exact R1 evidence and states a distinct falsifiable defect hypothesis. Each admitted revision reruns both models with fresh N=5 trials. R2 failure, or a failing version without an admissible next hypothesis, first enters `rollback_pending`. The controller atomically restores the immutable pre-recovery repository prompt snapshot and verifies its hash before publishing `no_selection`; a failed restoration leaves recovery nonterminal and blocks calls/handoff. There is no R3 and no ranking among failed versions.

The old fallback tuple remains valid historical evidence for M1-4a2. It is neither recalculated nor reused in M1-4a2r.

### 6. Each version assessment is one coherent two-model attempt

For R0, R1 or R2, one attempt contains Qwen N=5 and DeepSeek N=5 with identical bound inputs, prompt, contract, validator, temperature and `max_tokens = 65536`. Every initial call and repair uses an isolated, cache-disabled, no-history session. Trial ids, returned model/provider identities, finish reasons, raw usage and evidence paths are unique and stable.

Infrastructure validity is assessed at the attempt level. If either model batch has a Provider, transport or identity failure that invalidates its denominator, neither side is eligible for assessment. A retry creates a new attempt identity and reruns both complete batches. Old attempts remain immutable audit evidence; successful trials are not cherry-picked across attempts and a failed model is not replaced alone.

This costs more when one Provider fails, but it prevents model comparisons and selection metrics from being assembled from different operating conditions.

### 7. The existing full-draft repair call is retained and audited after the fact

The repair output remains a complete ArchitectureDraft. The change does not introduce model-authored JSON Patch, partial merge behavior or a second validator. The repair request remains no-history and contains only the frozen inputs, prior Schema-valid draft and exact canonical `ARCH_VALIDATE` issue list.

After the single repair, the controller deterministically computes:

- canonical SHA-256 of the pre- and post-repair drafts;
- sorted changed JSON Pointer paths, with array comparison keyed by stable schema identifiers rather than incidental order where the contract defines set-like identity;
- the issue-derived admitted impact closure;
- per-change attribution to one or more supplied issues;
- full before/after results for every `arch_01` through `arch_10` gate;
- improved, unchanged and regressed gates.

The impact closure is defined by a versioned, closed `repair-impact-v1` policy maintained beside the recovery protocol. Each canonical issue code/path maps to the draft fields read by the failing gate plus the explicitly declared cross-projection fields needed to restore the same invariant. Closure expansion follows only declared gate dependencies; it never expands from arbitrary model edits. The policy and its implementation hash are lineage-bound and unit-tested against `arch_01`–`arch_10`, including the recurring `arch_03`, `arch_04`, `arch_09`, `arch_10` and `ARCH_TEST_READINESS_UNCLOSED` cases.

A repair is a p1 success only when the final full validator passes, every changed path is inside the admitted closure, every change has issue attribution and no initially passing gate regresses. Missing or over-broad evidence fails the trial even if the final draft mechanically validates. No further repair is attempted.

### 8. The recovery gate measures bounded recoverability; p0 remains visible

Reports keep fixed N=5 denominators per model and include Schema rates, `p0`, cumulative `p1`, every gate at each stage, issue co-occurrence, repair gains and regressions, repair locality, calls, tokens, cost, latency, finish reasons, truncation and returned identities.

A version passes only if Qwen and DeepSeek each independently have:

- `schema_after_format_repair_rate = 1.00`;
- cumulative `p1 = 1.00`;
- zero truncation and valid infrastructure/identity;
- zero repair regression;
- complete passing locality evidence for every repaired success.

`p0`, per-gate initial pass rates and repeated initial failures are required diagnostics. A low `p0` does not alone fail recovery, because M1-4a3 will separately test formal N=20 qualification and production repair-budget choices. No cross-model mean can hide a failing model.

### 9. Architecture-quality review is a parallel diagnostic, not a hidden validator

For every final Schema-valid candidate, a canonical audit reports deterministic structural indicators:

- requirement ownership counts and concentration;
- zero-responsibility work packages;
- task-ready contracts with no declared consumer;
- task contract/interface/file-slot shape;
- repair changed-path count and proportion;
- final validator result.

An optional blinded review is stored separately with reviewer identity/model, rubric version, prompt/input hashes and raw judgment provenance. Reviewer prose does not feed R1 or R2, and unavailable review is recorded as unavailable. Neither deterministic red flags nor reviewer judgments change recovery selection in this change. They are carried into the M1-4a3 handoff so a later, explicitly authorized design decision can decide whether any should become normative.

### 10. Evidence publication is append-only, atomic and resumable

The new recovery namespace contains canonical closed records for:

- authorization and protocol;
- predecessor attestation/inventory and recovery-local authorization, design, experiment-report and seed snapshots;
- prompt snapshots and optional R1/R2 revision declarations;
- version/attempt declarations;
- per-model batches, trials, requests, responses, candidates and validations;
- repair diffs/locality verdicts;
- reports, assessments and quality audits;
- terminal selection/no-selection and optional M1-4a3 handoff.

Declarations are published before Provider calls. Each completed child is written atomically and referenced by recovery-root-relative path plus SHA-256. The only external source locators are closed workspace-relative provenance inputs admitted during initialization: the exact predecessor root, authorization/design source and allowlisted experiment artifacts. They are checked for path confinement and hashes, then represented by a local inventory/attestation or copied local snapshots; trials and all later records cannot use external refs. Aggregates are derived only after all declared children are complete and are recomputed from leaves; stored outcomes cannot override recomputation. Resume skips only already complete hash-valid children and never changes a declared attempt's denominator or identity. Conflicting bytes, incomplete children, source drift or hash mismatch fail closed while preserving the evidence root for diagnosis.

Provider credentials are read only from the fixed process-environment variable names approved by the design. Preflight checks presence without logging values. The recovery implementation never sources a shell startup file or dotenv file and persists no secret.

### 11. Selection changes only the prompt and admits only M1-4a3

The first passing R0/R1/R2 version publishes exactly one selection after the repository prompt bytes equal its immutable snapshot. The handoff binds the authorization/design hash, new lineage, predecessor tie, selected prompt, coherent attempt, reports, assessment, repair diffs and quality audit.

The handoff's consumer is only M1-4a3. It does not count as N=20 evidence, B1-B4 disposition, production model/call-shape/budget freeze, responsible-owner production signature or a formal S4/S5/S6 artifact. Recovery candidates remain calibration-only.

## Risks / Trade-offs

- **A one-repair p1 target can mask weak initial construction.** Required p0/per-gate reporting and later M1-4a3 N=20 qualification preserve visibility; this change deliberately does not claim first-pass stability.
- **Repair locality can be defined too narrowly or too broadly.** A versioned, closed impact policy with gate-specific tests and complete changed-path evidence makes the boundary reviewable. Changing that policy after calls forces a new lineage.
- **Full-draft repair may rewrite harmless formatting or ordering.** Canonical keyed comparison avoids false changes where the Schema gives stable identities; true semantic field changes remain visible.
- **Attempt-level reruns increase Provider cost after one-sided infrastructure failure.** The cost buys coherent denominators and avoids cross-attempt cherry-picking.
- **N=5 can still overfit the gold set.** M1-4a2r is only development recovery; M1-4a3 remains the independent N=20 qualification boundary.
- **Mechanical passes may still be poor architectures.** The separate quality audit exposes this limitation without silently changing the validator or selection rule.
- **The recovery protocol adds evidence complexity.** Reusing existing calibration execution/recomputation paths and adding only recovery-specific state, locality and audit records limits duplication.
- **The bound design may drift or authorization may be revoked.** In that case preflight fails closed and no repository prompt, code-path execution or Provider state is changed under the stale protocol.

## Migration Plan

1. Keep M1-4a2 and its lineage unchanged in terminal tie state.
2. Preserve the recorded responsible-owner authorization and the exact minimal `system_design.md` 3.1.0 delta as the recovery design baseline.
3. Bind the approved design path/hash in the recovery protocol; any later design drift blocks implementation calls and handoff until separately authorized.
4. Extend the existing prompt-development and architecture-calibration paths with the recovery state machine, Schemas, recomputation, locality and quality-audit evidence.
5. Add deterministic unit/integration tests, then run the non-live regression suite.
6. Materialize R0 from the exact authorized seed path/hash and run one fresh coherent Qwen/DeepSeek attempt. Admit R1 only if R0 evidence satisfies the first revision rule, and R2 only if complete failing R1 evidence satisfies a second distinct revision rule.
7. On the first pass, publish one recovery selection/handoff and leave M1-4a3 work unsatisfied. On terminal failure, publish no-selection and stop.

Rollback is evidence-preserving: before a selection, the repository prompt is restored to its immutable pre-recovery bytes after any staged-update failure and before terminal `no_selection`; the incomplete or failed new root remains non-selectable audit evidence. `no_selection` cannot be published until restoration hash verification succeeds. After a valid selection/handoff, rollback means revoking downstream admission through a new authorized record, not mutating the immutable recovery evidence. The old M1-4a2 root is never a rollback target because it is never changed.
