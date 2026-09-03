# M1-4a2 value-hash-free patch calibration preregistration

Status: frozen before Provider I/O.
Date: 2026-08-29
Change: `m1-4a2-patch-calibration-rerun`
Design authority: `project_docs/system_design.md` 6.0.1, SHA-256 `543fe7c37d34ecb4616399b05175e6d6deea34f8057f937a00c87e2aebec8f7d`.

## Scope

Develop one shared, model-independent and protocol-neutral
`ArchitecturePlanner` prompt for the M1-4a3 technical admission boundary.
This experiment cannot qualify a production model, authorize M1-4a2r, assert
B1-B4, create a formal Run, or provide an owner signature.

Only a fresh design-6.0.1 patch lineage is admissible. Historical fixed-layout,
full-draft-repair and 6.0.0 value-hash patch evidence is provenance only.

## Frozen protocol

- Versions are exactly `v0` through `v4`.
- Every admitted version runs exactly N=3 fresh trials in each logical slot:
  `qwen`, `claude`, and `deepseek`.
- Each trial begins with a complete `ArchitectureDraft`. Semantic repairs are
  fresh, no-history, patch-only and have maximum depth two. Stop after p0 or
  p1 success; run p2 only when p1 still fails.
- A patch contains only ordered JSON-Pointer `add`, `replace`, and `remove`
  operations. `add` requires an absent target; `replace` and `remove` require a
  present target. The model must not calculate or return a prior-value hash,
  digest, or equivalent field. The controller atomically applies the complete
  patch, reruns the draft Schema and all fifteen `ARCH_VALIDATE` gates, and
  preserves the parent candidate on rejection.
- Initial-generation accounting is depth-zero only. Each model has a hard
  ceiling of 15 initial trials across V0-V4. Patch and format-repair calls are
  reported separately and excluded from that ceiling.
- Any exhausted provider/network retry makes the complete version attempt
  infrastructure-invalid/audit-only. Retry by redeclaring all three N=3 slot
  batches; no single-slot retry, replacement sample, extension, or cross-slot
  assembly is admitted.
- Only prompt bytes may vary within a lineage. Schema, validator, serializer,
  input construction, locality mapping, model controls, context limits and
  metric definitions remain frozen.

## Screening and terminal rule

A version is valid only when all three reports are complete, infrastructure
valid, have zero truncations, and each independently has `p2 >= 0.60` (at least
2/3 trials). p0, p1, Schema rates, per-gate/depth diagnostics, locality,
usage, model strings and parameter support do not replace this gate.

The first passing version stops the experiment immediately. If V0-V4 all fail,
the complete assessments use the fixed fallback order:

1. maximize minimum model p2;
2. maximize minimum model p1;
3. maximize minimum semantic-first-pass rate;
4. maximize minimum Schema-after-format-repair rate;
5. minimize total cost.

Exact equality publishes `PROMPT_SELECTION_TIE` with no handoff. A unique
fallback can only produce an M1-4a3 technical admission candidate; it is not a
quality or production qualification.

## Evidence and stop conditions

Every version, attempt and trial declaration precedes Provider I/O. Immutable
leaves must bind request, response, patch, application, candidate, validation,
locality, trace and usage at each attempted depth. Reports and Markdown are
derived only from intact new-lineage leaves and show all three slots, fixed
denominators, fifteen gates, patch outcomes, usage, model strings and the exact
terminal reason.

Stop at the first screening pass, terminal V4 fallback/tie, the initial-trial
ceiling, any budget limit, or a non-prompt blocker. Infrastructure-invalid
evidence remains audit-only and must never be presented as a quality-gate
success.
