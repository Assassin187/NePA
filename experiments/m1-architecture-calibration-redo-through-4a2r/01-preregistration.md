# M1-4a2 / M1-4a2r preregistration

Change: `m1-architecture-calibration-redo-through-4a2r`

This experiment evaluates the shared protocol-neutral ArchitecturePlanner
prompt on one fresh free-layout lineage. It ends at an M1-4a3 admission
handoff, or at an explicit tie. It does not qualify a production model or
call shape and it does not claim M1-4a3 N=10, B1-B4, S4, S5, S6, or a
production freeze.

## Frozen inputs and controls

- M0 freeze: `configs/m0-default-inputs.freeze.yaml`, 2026-08-11.
- Spec, target profile, and test bundle are the three gold-file artifacts
  recorded in `00-implementation-brief.md`.
- Main design: `project_docs/system_design.md`, version 5.3.0,
  SHA-256 `49ec7593600b09a1d6c7ea7d748b9e8843eaa77c7e86e6cdb4784f720227d8e8`.
- Authorized subdocument: `project_docs/pipeline_design_s4_s9.md`, version
  1.1.0, SHA-256
  `6ebf3c693e519fd14229b3591b4226d51c9df399380f28164d5d981d45c51af8`.
- Layout convention: `c99-server-v1`; its canonical SHA-256 is recorded in
  the generated Delivery Constraints and lineage.
- The prompt receives only the named `planning_index`,
  `delivery_constraints`, and `repair_context` inputs. Protocol facts,
  provider/model branches, and convention-owned identifiers are not embedded
  in the shared prompt source.

The lineage binds the exact convention, Schema, serializer, fifteen-gate
`ARCH_VALIDATE`, planning/constraint constructors, runtime/provider
components, slot controls, context limits, metric definition, and source
hashes. Prompt bytes are versioned within the lineage and do not define its
identity. Configured and returned model strings are observations only.

## Slots and execution

Every attempt runs the same five trials independently for the ordered logical
slots `qwen`, `claude`, and `deepseek`. Each slot has its own adapter/session,
cache-disabled no-history calls, trace stream, directory, and trial leaves.
Declarations are committed before Provider I/O. An infrastructure-invalid
slot invalidates the whole attempt for selection; the next attempt reruns all
three slots without replacement sampling. A complete report is recomputed
from its hash-bound leaves before it can enter an assessment.

The declared request controls are temperature `0.0`, `max_tokens=65536`, and
the explicit per-slot context limits in
`configs/m1-4a2-context-limits.json`. The live configuration is
`configs/m1-4a2-live.yaml`; its secret-variable names are
`NEPA_QWEN_API_KEY`, `NEPA_CLAUDE_API_KEY`, and `NEPA_DS_API_KEY`. Only the
presence of those variables is checked immediately before live work. Their
values are never printed, stored, or included in a snapshot.

## Version state machine

- V0 is the frozen source prompt and one coherent N=5 attempt.
- V1 is admitted only after complete failing V0 evidence supports one
  falsifiable prompt-only hypothesis and one exact source diff.
- V2 is the sole optional second revision, admitted only after complete
  failing V1 evidence supports a distinct falsifiable prompt-only hypothesis.
- V1/V2 may receive one N=10 extension only when the typed
  `single_sample_sensitive` or `metric_conflict` predicate fires. The
  extension appends exactly trials 006-010 for all three slots under unchanged
  controls. V0 extension, partial-slot extension, cross-attempt assembly, and
  V3 are invalid.

## Screening and decision rule

Each slot must have a complete, infrastructure-valid report with zero
truncations and `p1 >= 0.80`. The 0.90 value is retained only as the strict
M1-4a3 reference relation; it is not an additional hard gate. Schema-after-
format-repair rate, p0, first semantic pass, all fifteen per-gate rates,
repeated initial failures, repair gain, latency/cost, parameter support, and
requested/returned model-string shares are diagnostics.

The first complete version for which all three slots pass is selected. If no
version passes, the fallback tuple is compared in this order using the
minimum across all three slots: minimum p1, minimum semantic first-pass rate,
minimum Schema-after-format-repair rate, then lower total cost. An exact tie
publishes `selection-tie.json` and no selection or handoff. A selected result
publishes only an M1-4a3 admission handoff and records conditional recovery as
`not_triggered`.

The total experiment budget is the configured USD 20 ceiling. The report must
be generated from new-lineage leaves, list every slot and all fifteen gates,
and state the denominator, truncation/infrastructure status, repair/locality
diagnostics, cost/latency, model-string shares, terminal decision, and all
non-scope limitations. No number is hand-entered into the Markdown report.

## Conditional recovery boundary

Recovery is not started by this preregistration. It is permitted only if the
new development lineage produces a complete recomputable exact tie and a
separate responsible-owner authorization is supplied for that exact
predecessor graph and current design hash. The implementer must not approve
that authorization. If development selects normally, no recovery lineage or
recovery Provider call is created.
