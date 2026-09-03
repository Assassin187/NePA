# Post-fix preregistration (design 6.1.0)

## Scope

This is a fresh replacement lineage after the validator/locality correction.
The prior lineages are audit-only.  All post-fix reports, gates, hashes,
lineage checks, selection, and handoff decisions must be recomputable solely
from the replacement lineage.

## Fixed protocol

- Prompt versions: V0, then at most V1–V4 sequentially.
- Prompt unit: one shared `initial`/`repair` bundle, using `architecture_planner_initial.md` at depth zero and `architecture_planner_repair.md` at every semantic repair depth.
- Models/slots: exactly Qwen, Claude, and DeepSeek.
- Samples: exactly N=3 complete trials per model per admitted version.
- Semantic depth: p0, p1, p2; at most two patch repairs per trial.
- Screening: every slot must have p2 >= 2/3; p0/p1 and gate/locality fields are diagnostic.
- Initial-generation ceiling: 15 per model in this reset lineage.
- Retry: a failed slot requires a complete three-slot replacement attempt; no partial assembly.
- Stop: first screening pass; otherwise only one evidence-backed, prompt-only hypothesis may admit the next version.
- Revision: exactly one stage changes per V1–V4 transition; the other stage is byte-identical. Four edits total are available across the bundle, not four edits per stage. Cross-version stage mixing is invalid.
- Fallback/tie: only after a complete V0–V4 assessment and never represented as quality-gate attainment.
- Bundle identity: existing source/artifact references and recorded bytes only; no bundle digest, per-template digest, parent-prompt hash prerequisite or new hash gate.

## Restart baseline

V0 uses the current two-stage ArchitecturePlanner bundle from the repository.
The initial and repair files are captured together in the V0 snapshot. This is
not recorded as a prompt improvement: the restart and stage split are caused by
corrected non-prompt components and the design-6.1.0 invocation contract.

## Frozen non-prompt controls

The lineage must bind the current design baselines, provider/model/parameter
controls, planning and delivery inputs, ArchitectureDraft and patch schemas,
serializer, validator, patch applier, locality policy, coupled projection
policy, and all application/derived-operation evidence.  Any drift creates a
new lineage and cannot be repaired by rewriting an old leaf. Existing generic
artifact references remain the only bundle/source binding mechanism.

## Admission and boundary

Only a complete valid post-fix round may support an evidence-backed prompt
hypothesis.  Removal of the known validator/locality defects is not a prompt
improvement.  A normal terminal selection can only produce the owner-only
technical M1-4a3 admission handoff described by the change; a tie produces no
handoff.  This record authorizes neither M1-4a2r nor production qualification.
