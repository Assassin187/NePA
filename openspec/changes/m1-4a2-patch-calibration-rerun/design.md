## Context

See `proposal.md`. The current uncommitted implementation contains useful two-stage prompt, patch application, validator and evidence-recompute work, but hard-codes three slots, V0～V4, whole-version invalidation and an M1-4a3 handoff. Existing post-fix attempts contain no successfully applied patch and are diagnostic-only.

The authoritative baseline is `project_docs/system_design.md` 7.0.0. Model configuration, prompt protocol, retry semantics and patch application behavior are lineage-controlled, so new live evidence must start in a fresh lineage. Historical bytes remain unchanged.

## Goals / Non-Goals

**Goals:**

- Select one usable, protocol-neutral `initial`/`repair` prompt bundle with one configured model slot and a 2/3 N=3 minimum.
- Isolate every model, transport and patch failure to its trial.
- Make repair input actionable through stable paths, multi-region allowed fixes and one rejected-patch correction.
- Preserve deterministic validation, atomic patching, evidence recomputation and legacy read-only evidence.

**Non-Goals:**

- Measuring cross-model stability or final architecture quality.
- Running complete S4, S5, S6 or making live provider calls during implementation.
- Weakening ArchitectureDraft Schema, `ARCH_VALIDATE`, protocol neutrality or evidence integrity.

## Decisions

### 1. One configured logical model slot

The active protocol accepts exactly one logical slot from `calibration_models`. The slot name is an arbitrary safe identifier; its provider, model, parameters and API-key environment come from resolved configuration. Default calibration configuration contains one slot resolving to Claude. Changing it creates a new lineage, not a design edit.

Legacy readers keep recognizing historical fixed-slot artifacts. The active coordinator and new Schemas never require the strings Qwen, Claude or DeepSeek.

### 2. Three small versions with a minimum usability result

V0, V1 and V2 each declare three initial trials. The first version with at least two p2 successes is selected and later calls are refused. A revision may change initial, repair or both templates, and records both diffs.

If V2 still misses 2/3, the coordinator publishes a diagnostic reference version ranked by p2 count, p1 count, p0 count, Schema-valid count, lower cost and earlier version. It publishes no M1-4c handoff. There is no tie or recovery state.

### 3. Trial-local failure and retry

Trials execute independently. Ordinary semantic or Schema failure, truncation and infrastructure exhaustion remain failures in the fixed denominator but never invalidate another trial. The provider's normal retry policy applies first; the driver may make two additional attempts for that same trial identity. All attempts are retained. Completed trial leaves are immutable and are never rerun.

### 4. Effective patch depths and one correction

The semantic budget counts successfully applied candidate transitions, not malformed or inapplicable patch payloads. At each of the two depths, one rejected patch may be followed by one fresh correction request containing the unchanged candidate, original current failures, normalized allowed paths and the exact rejection reason. A second rejection ends that trial depth.

Validator issue paths are converted to stable identifier paths before rendering. A model patch may touch multiple concrete paths when each is allowed by a current failure. For layout identity changes, the controller still derives only exact matching ownership/work-package substitutions. Those derived changes do not forbid unrelated allowed model operations in the same patch.

### 5. Prompt reconstruction

The initial template restores the historical construction algorithm: primary requirement assignment, contract ownership/readiness, layout ledger, module and work-package partitioning, dependency construction and final invariant checks. The repair template repeats every rule needed in a fresh call and includes abstract stable-path examples; it never refers to unseen initial instructions. Both remain model- and protocol-neutral.

### 6. New active artifacts, legacy read-only evidence

Active development artifacts use a new major schema contract with one `model_slot`, V0～V2 and trial-local outcomes. Selection records prove only baseline usability. An owner-approval reference is required before publishing the M1-4c handoff.

Old three-model, recovery and M1-4a3-oriented Schemas remain available only for historical validation and recomputation. Existing experiment directories are not modified.

## Risks / Trade-offs

- **A 2/3 result may not predict production quality** → D1.3 complete-chain runs are the only quality observation point.
- **One configured model narrows evidence** → the model is deliberately a configuration choice and can be changed with a new lineage without changing design.
- **Patch correction adds calls** → limit it to one correction per semantic depth and report every call and cost.

## Migration Plan

1. Update authoritative design and all active change artifacts.
2. Introduce the new single-slot artifact contract while keeping legacy readers.
3. Change coordinator and trial driver behavior, then rebuild prompts and tests.
4. Mark current lineages diagnostic-only in version-controlled experiment notes; do not alter run leaves.
5. Initialize a fresh design-7.0.0 lineage at zero of nine initial trials only after all no-I/O checks pass.
6. Live V0 execution and owner approval remain explicit pending tasks.
