# M1-4a2 post-fix implementation brief

This directory is the replacement, post-correction experiment record for
OpenSpec change `m1-4a2-patch-calibration-rerun`.  It is intentionally separate
from the historical 6.0.0 and pre-fix 6.0.1 directories.

## Frozen authority and correction

- Change: `openspec/changes/m1-4a2-patch-calibration-rerun/`
- Normative design: `project_docs/system_design.md`
- Design revision: `6.1.0`; design baseline SHA-256: `0974a27aa49c2cfdbc598a48b4f86f330ac3ddfec603f29264a8a1208e6f7020`
- Pipeline reference SHA-256: `6ebf3c693e519fd14229b3591b4226d51c9df399380f28164d5d981d45c51af8`
- Corrected architecture validator source: `e1855371b58c6235ae21050a19b1687602a3e008d8a88f4c5eaab3372212e82b`
- Corrected patch runtime/projection source: `94d421aa381179bdedd689cfb8aa121d592dc6e2342096958e84b240f64abd97`
- Development coordinator source: `63424a3e2dd02f6a5e2f01d8c923736e8fa718cac2a6cfe8e0a8caf8d153e417`
- Agent framework/role sources: `4e97f7b8314419d775bb008740998849069a23a01e9e83d31f76a0c033db0550` / `cc5459fbad602d0337c114456344c059ee0cbbe362e1dcb27e8dfde6e8b0fb51`
- Prompt stage sources: initial `da5597feb18443b7c5152b28e3e48c7eeebd38b06ca898f306d900d45fcd17e0`, repair `272316651f611bf230a81220ed243fc56201bfacc61a6adc7f66b69fca15951e`
- Restart prompt bundle: `nepa/agents/prompts/architecture_planner_initial.md` and `nepa/agents/prompts/architecture_planner_repair.md`, captured together and not treated as prompt-improvement evidence.

The correction is non-prompt: legal `{message_id}`/`{type_id}` placeholders and
Spec-derived separator-bearing identifiers are atomic in `arch_15`, and a
layout path/path-pattern change now receives one deterministic controller
projection into exact module/work-package file-list references.  Model and
derived operations are persisted separately and applied atomically.

## Pre-fix audit provenance

The following leaves remain immutable audit evidence and are excluded from every
post-fix denominator and comparison tuple:

| lineage | status | recorded use |
| --- | --- | --- |
| `2d297e0ebd5bfdb4a88e73528428c02173be2be13a09169e7db9a0b15fb5e2c8` | infrastructure-invalid pre-fix rerun | Claude: 8 calls, 191145 input tokens, 66363 output tokens, 2652750 ms; Qwen: 9 calls, 212012 input, 115991 output, 1605694 ms; DeepSeek: 6 calls, 141790 input, 170596 output, 1354432 ms; cost recorded as 0.0 |
| `a85f28d7f96f41cc7ef615c4bd03e95cbd8d1ed134f7d60894ecb4ce7ffbc415` | complete pre-fix V0/V1 diagnostic lineage | 18 initial trials, 48 calls, 1096692 input tokens, 789340 output tokens, 4 format calls, 28 semantic calls, 0 truncations, cost recorded as 0.0 |

These records explain the known `arch_15` and coupled-locality findings.  Their
usage is reported only in a non-comparative appendix.

## Live-call rule

No Provider call is permitted until the post-fix no-I/O gate and the reset
declaration pass.  The post-fix protocol is V0–V4, one shared prompt, N=3 per
slot (`qwen`, `claude`, `deepseek`), semantic depth 2, p2 threshold 2/3 at
each slot, first-pass early stop, and at most 15 initial generations per model.
There is no extension, single-slot retry, cross-lineage mixing, recovery,
qualification, production, M1-4a2r, or M1-4a3 execution in this change. No
bundle/template digest field or new hash gate is part of the protocol; existing
artifact references and per-call trace fields remain authoritative.

## Completed no-I/O gate

The design-6.1.0 update was followed by zero external Provider calls. The
deterministic fake-provider and static suites passed before live initialization:

| check | result |
| --- | --- |
| `uv run python -m pytest -q` | `368 passed in 126.66s` |
| `openspec validate m1-4a2-patch-calibration-rerun --strict` | valid |
| `openspec validate --all --strict` | `6 passed, 0 failed` |
| four `uv run nepa lint` gold/design commands | valid, zero errors/warnings |
| `git diff --check` | passed |

The gate also verified both prompt stages, phase selection, one-role routing,
single-stage revision enforcement, cross-bundle rejection, exact coupled
closure, historical v2 readability, unchanged full-draft recovery behavior,
and absence of new bundle/template/component digest fields or gates.

## Resume audit boundary

The replacement lineage is
`58377f793cb867208c989d0ef43716bc072e8cd11eb58dca5c69b83a23776047`.
After initialization, the first live V0 attempt was interrupted after the
three `trial_001` leaves were atomically committed.  Thread-pool race output
also left non-coherent `trial_002`/`trial_003` staging or committed directories
under the same attempt.  Those directories and their immutable contents are
preserved as audit-only evidence and are excluded from every assessment and
denominator.  The attempt is therefore recorded as infrastructure-invalid and
the next live work uses a newly numbered complete three-slot attempt; no
cross-attempt trial assembly is permitted.

Attempt_002 also became infrastructure-invalid during leaf recomputation
because Claude `trial_003` had no initial candidate reference.  A fresh
`attempt_003` was then started; Qwen and DeepSeek completed their three trials,
but Claude `trial_003` remained in a long-running repair request and was
interrupted after its already committed leaves were preserved.  Attempt_002
and Attempt_003 are both audit-only and have no assessment.  The replacement
lineage has reserved 9 of its 15 initial-generation trials per model; only a
complete `attempt_004` may enter the V0 denominator.

At the owner's stop request, `attempt_004` was interrupted with exit code 130
after Qwen completed its three trials and report, while DeepSeek and Claude
had only partial committed evidence.  It is recorded as infrastructure-invalid
with no assessment; its preserved leaves remain audit-only.  The reset-lineage
counter is consequently 12 of 15 per model, and the coordinator's next action
is the still-unstarted `attempt_005`.
