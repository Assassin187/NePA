# M1-4a2 patch calibration rerun preregistration

Status: frozen before Provider I/O.
Date: 2026-08-29
Change: `m1-4a2-patch-calibration-rerun`
Design authority: `project_docs/system_design.md` 6.0.0, SHA-256 `6da0a4918de3e78379e78b6fa371080d5ca6fc225940fac3e0dbb36b02b97d14`.

## Objective and scope

Develop one shared, model-independent and protocol-neutral `ArchitecturePlanner` prompt for the M1-4a3 admission boundary. This experiment may publish only an M1-4a3 technical admission handoff or `PROMPT_SELECTION_TIE`; it does not qualify a production model, authorize M1-4a2r, create a formal Run, assert B1-B4, freeze production settings, or provide an owner signature.

The new evidence must use one fresh patch-capable lineage. Historical full-draft-repair trials, reports, lineages and prompt-optimization material are provenance only and cannot enter any denominator, fallback tuple, selection or handoff.

## Frozen protocol

- Versions are exactly `v0`, `v1`, `v2`, `v3`, and `v4`; `v0` runs first.
- Each admitted version declares exactly three fresh trials for each logical slot `qwen`, `claude`, and `deepseek` (N=3 per slot, nine initial trial identities declared before Provider I/O).
- Each trial starts with one complete `ArchitectureDraft` generation. M1-4a2 semantic repairs are patch-only, fresh and no-history, with semantic depth exactly two maximum. A trial stops after p0 or p1 success; p2 is attempted only when p1 still fails.
- Initial-generation accounting is depth-zero only. Each model has a hard ceiling of 15 initial-generation trials across v0-v4. Patch calls and structured-output format repairs are separately metered and excluded from that ceiling.
- A provider/network failure that exhausts transport retries makes the whole version attempt infrastructure-invalid/audit-only. Any retry redeclares a complete three-slot N=3 batch for all three models. No partial-slot completion, replacement sample, single-slot retry, N=10 extension or cross-attempt assembly is admitted.
- Every trial uses a fresh no-history invocation with cross-trial cache disabled and model-isolated evidence, session, cache and trace roots. The logical slots are fixed; returned provider model strings are recorded as observations and are not lineage identity.
- Only the shared ArchitecturePlanner prompt bytes may change within a lineage. Schema, validator, serializer, input construction, locality mapping, model-slot request controls, context limits and metric definitions remain frozen.

## Screening and progression

A version is complete and screening-valid only when all three model reports are complete, infrastructure-valid, have zero truncations, and independently satisfy cumulative `p2 >= 0.60` (at N=3, at least 2/3 trials) for each model. No cross-model average can hide a failing slot. The first passing version is selected immediately and no later version is run.

If a complete failing version supports a prompt defect that is demonstrable from its evidence, exactly one distinct, falsifiable, evidence-backed prompt-only hypothesis may admit the next version. At most four revisions are allowed (`v1` through `v4`); no v5 or open-ended tuning is allowed. Each revision records its parent prompt hash, exact evidence references, one hypothesis, expected gates/depths, complete diff and stopping conclusion. Model/provider/protocol-specific branches and non-prompt control changes are forbidden.

If no version passes through v4, compare only complete v0-v4 assessments by this fixed lexicographic order:

1. maximize the minimum p2 across the three model slots;
2. maximize the minimum p1 across the three model slots;
3. maximize the minimum semantic-first-pass rate across the three model slots;
4. maximize the minimum Schema-after-format-repair rate across the three model slots;
5. minimize total cost.

All comparison tuples and complete assessment references are persisted. Exact equality publishes `PROMPT_SELECTION_TIE`, with no selected prompt and no handoff. A unique fallback is only an M1-4a3 prompt candidate.

## Evidence and validation

Before any live call, the implementation must verify the design/input/component hashes in `00-implementation-brief.md`, the three fixed logical slots, `max_tokens=65536`, context limits, prompt neutrality, gold inputs, required environment-variable presence without reading secret values, and all deterministic focused suites. Every version, attempt and trial declaration precedes dependent Provider I/O.

For every attempted depth, persist immutable request/response/patch/application/candidate/validation/locality/trace references and separate initial, format-repair and patch usage. Recompute all fifteen gates at p0/p1/p2, fixed-denominator rates, gate transitions/regressions, patch locality/rejections, truncation, finish reasons, cost, latency, model identity and parameter support from leaves. Markdown is generated only from the canonical machine summary.

## Stop conditions

Stop immediately at the first complete screening pass, after terminal v4 fallback/tie publication, at the initial-generation ceiling, at any budget limit, or at a non-prompt blocker (validator, schema, protocol, lineage, provider infrastructure or evidence-integrity failure). Infrastructure-invalid evidence remains audit-only and must never be described as a complete assessment or quality-gate success.

