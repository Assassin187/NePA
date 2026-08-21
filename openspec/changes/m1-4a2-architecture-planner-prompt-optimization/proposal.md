## Why

M1-4a1 has been implemented and archived at `c949c21`, providing the frozen lineage, isolated trial driver, production `ArchitectureDraft`/`ARCH_VALIDATE` path, and recomputable per-model reports required by M1-4a2. The authorized calibration candidate set is now exactly Qwen and DeepSeek. The next M1 critical-path item is the bounded development of one protocol-neutral, model-independent `ArchitecturePlanner` prompt before any N=20 qualification or production-model decision is permitted (Design §6.4.8.2, §8.8; M1-4a2).

## What Changes

- Replace the M1-4a1 ArchitecturePlanner skeleton text with one shared protocol-neutral prompt developed under the mandatory V0/V1/optional-V2 protocol; the Schema, validator, serializer, frozen inputs, two-model configuration, call shape, and metric definitions remain lineage-bound and unchanged within the experiment.
- Correct the M1-4a1 lineage boundary authorized for this change: `trial_count` and `semantic_depth` are immutable, hash-bound batch-protocol controls, not inputs to `lineage_id`. This permits the designed N=5→N=10 development expansion and M1-4a2→M1-4a3 repair-depth transition under the same controlled lineage without permitting reports with different batch protocols to be aggregated as one result.
- Add a deterministic prompt-development coordinator and closed evidence contracts that admit exactly two model reports per version, enforce V0/V1/optional-V2 ordering and sample budgets, bind prompt bytes/hashes and lineage identity, and record the required previous hash, failure evidence, single testable hypothesis, exact prompt diff, expected gate improvement, and stopping conclusion.
- Require `init` to consume an explicit calibration configuration path plus explicit positive context-window declarations for exactly Qwen and DeepSeek. Resolve and validate provider/model/parameters, pricing, and context limits before provider I/O; require both models to use `max_tokens = 65536`, require `api_key_env` to match the §8.3 fixed mapping (`NEPA_QWEN_API_KEY`, `NEPA_DS_API_KEY`), and require both variables to be non-empty in the current process environment. Bind only canonical non-secret projections and fixed variable names to evidence; never infer inputs from model names or the network, read shell startup/dotenv configuration, or persist secret values.
- Run V0 with Qwen and DeepSeek at N=5 and one semantic-repair allowance; permit V1 only from one evidence-backed prompt hypothesis, permit a same-bytes expansion to N=10 only when the N=5 conclusion is ambiguous, and permit V2 only when V1 fails the screening gate and its evidence supports one second distinct hypothesis.
- Make each V0/V1/V2 base N=5 run an explicit two-model attempt. If either model is infrastructure-invalid, retain that attempt for audit, exclude both reports from assessment/selection, and require a fresh attempt-qualified two-model rerun with the same version, prompt hash, lineage, and batch protocol; never splice models or trials across attempts.
- Publish `versions/<version>/prompt.md` immutably before version admission and make every real model invocation render from that evidence-bound snapshot. Bind both batch raw-template hashes to the same prompt ref/hash and reject source/snapshot drift.
- Recompute a per-version two-model assessment and apply the exact screening gate independently to Qwen and DeepSeek: `schema_after_format_repair_rate = 1.00`, `p1 = 1.00`, `arch_semantic_first_pass_rate >= 0.80`, no truncation or infrastructure invalidity, and no same `arch_01`-`arch_10` gate failing on the first Schema-valid candidate in at least two trials for that model/version.
- Select the first version satisfying the screening gate and stop further prompt edits. If V2 is reached without a passing version, deterministically select the sole M1-4a3 candidate by maximizing the minimum per-model `p1`, then minimum first semantic-pass rate, then minimum Schema rate, then total two-model cost.
- Extend static prompt checks to forbid protocol-specific constants, protocol identity branches, and model/provider-specific names or conditionals, while allowing only generic responsibility guidance, constraint expression, output ordering, self-checks, and protocol-neutral abstract examples.
- Add deterministic fake-provider/unit coverage and retain actual V0/V1/optional-V2 evidence under the gitignored calibration lineage root. Technical completion requires complete, recomputable two-model development evidence; it does not constitute production qualification or owner sign-off.
- **Out of scope:** M1-4a3 N=20 qualification, `p2` qualification, `model_comparison.json`, B1-B4 disposition, production route/model selection, production prompt/Schema/validator/call-shape/budget freeze, responsible-owner signature, M1-4b compiler assets, M1-4c complete S4, or any S5/S6 behavior.

## Capabilities

### New Capabilities

- `architecture-prompt-development`: Bounded, evidence-driven V0/V1/optional-V2 development and deterministic selection of the one shared protocol-neutral ArchitecturePlanner prompt that M1-4a3 will qualify.

### Modified Capabilities

- `planning-architecture-infrastructure`: Refine lineage identity so it binds metric definitions and all non-prompt comparison components while batch-specific `trial_count` and `semantic_depth` remain immutable evidence fields outside the lineage hash; incompatible batch protocols still cannot be aggregated into one report.

## Impact

- **Milestone/dependency:** Covers only M1-4a2. Its verified prerequisite is the archived M1-4a1 implementation at `c949c21`; M1-4a3 remains blocked until this change has selected one prompt candidate with complete evidence.
- **Likely code/assets:** `nepa/agents/prompts/architecture_planner.md`, lineage/config/batch/prompt-snapshot binding in `nepa/calibration/s4_architecture.py`, a narrow prompt-development layer, closed Schemas/examples, and focused calibration/prompt-neutrality tests. Existing M1-4a1 entry points are reused rather than forked; `configs/default.yaml` is not assumed to be a runnable live-calibration declaration.
- **Persistent evidence:** Adds only gitignored calibration-development records below `runs/_calibration/s4-architecture/<lineage_id>/`; it creates no formal Run, S4 receipt, Plan, Blueprint, Report v2, production freeze, or downstream-consumable architecture.
- **Validation/DoD:** OpenSpec strict validation, full unit/regression tests, explicit-config/pricing/context/max-token preflight tests, fixed `api_key_env` mapping/current-process presence tests, sentinel-secret non-disclosure tests, attempt/retry/resume/cross-attempt rejection, prompt snapshot/source-drift/TOCTOU tests, deterministic replay/tamper/ordering/budget tests, prompt protocol/model-neutral scans, and complete recomputable two-model V0 plus any evidence-permitted V1/V2 batches. No responsible-owner signature is due in M1-4a2; the mandatory production decision and signature remain exclusively M1-4a3.
