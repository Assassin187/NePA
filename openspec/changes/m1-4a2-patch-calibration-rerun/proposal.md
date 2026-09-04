## Why

The active M1-4a2 workflow treats prompt development as a strict three-model qualification exercise. One bad answer or one exhausted infrastructure call invalidates a whole version, while the split prompt lost important construction instructions. This prevents the project from reaching its actual near-term goal: selecting one usable ArchitecturePlanner prompt bundle so the complete framework can be built and its real quality observed there.

## What Changes

- **BREAKING** Make M1-4a2 the only ArchitecturePlanner baseline-prompt stage; remove M1-4a2r, M1-4a3, cross-model qualification, B1～B4 and their handoff paths.
- **BREAKING** Run one configuration-selected logical model slot through V0～V2, N=3 per version and at most nine initial generations. The initial configuration resolves to Claude, but no model name is a design or Schema gate.
- Select the first bundle with at least 2/3 trials passing all architecture gates within at most two effective patch repairs. Owner approval then permits only an M1-4c handoff.
- Keep ordinary failures, truncation and exhausted transport retries local to one trial; never rerun completed trials or invalidate the whole version.
- Allow a revision to change either or both stage templates, with evidence and exact diffs recorded.
- Rebuild both prompts from the strongest historical algorithmic instructions while preserving free-layout and protocol-neutral constraints.
- Normalize validator paths to stable identifiers before repair rendering, allow one patch to fix multiple currently allowed regions, keep exact controller-authored layout-reference projection, and allow one correction for a rejected patch at each semantic depth.
- Preserve all historical experiments and legacy evidence contracts as read-only provenance. New evidence uses a fresh design-7.0.0 lineage.

## Capabilities

### New Capabilities

- `architecture-prompt-development`: Defines single-model V0～V2 baseline-prompt development, per-trial failure isolation, selection and M1-4c handoff.

### Modified Capabilities

- `planning-architecture-infrastructure`: Makes model selection configuration-driven and updates patch locality, correction and per-trial reporting behavior.
- `agent-invocation-runtime`: Keeps the two-stage ArchitecturePlanner contract while making repair requests self-contained and allowing one rejected-patch correction.

## Impact

- **Milestone:** M1-4a2 now closes baseline prompt development and replaces the removed M1-4a2r/M1-4a3 gates. M1-4c consumes the owner-approved bundle.
- **Code and data:** the architecture calibration driver, prompt-development coordinator, calibration Schemas/examples, prompt templates, configuration and focused tests change together.
- **Compatibility:** existing three-model and recovery artifacts remain immutable and recomputable under their legacy contracts but cannot enter new selection.
- **Validation:** deterministic tests cover the single configured slot, local failure handling, V0～V2 selection, patch correction/locality, prompt completeness and historical evidence isolation. No live provider calls are part of implementation validation.
