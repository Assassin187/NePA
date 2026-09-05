# Follow-up calibration ruling draft

Status: `awaiting_owner_authorization`

This is a decision draft, not an owner approval. It contains no approval,
signature, production binding, or handoff.

## Current evidence

- Lineage: `0d8fe3c27163be64ab105da05eadffc2874641df3701cf2a9a2f91bf8e09352e`
- Diagnostic selection: `runs/_calibration/s4-architecture/0d8fe3c27163be64ab105da05eadffc2874641df3701cf2a9a2f91bf8e09352e/prompt-development/diagnostic-selection.json` (`sha256=9a5eee1e20f30a9d5f8d51063a829e71c8cac4864e0ccb116ad16e76e2b96847`)
- V0 assessment: `p2_passes=1/3`, `sha256=85427ba008a7bc5e9ef4c00b5800fa5a47f6e55dbe6336d1c67a655d42d70a49`
- V1 assessment: `p2_passes=0/3`, `sha256=7954b63270224fcf4d09366fb1f6ee87aedf294cb32ac313a430349d9dbb5876`
- V2 assessment: `p2_passes=1/3`, `sha256=565f7c1a809153a64626dff91cb3e9aec704361ac0fc155276c3a205f78298a9`
- V2 calibration report: `v2/architecture_primary/calibration_report.json` (`sha256=e4719d1c78720bfc9ed5032b1ba4e62ed4b470b4afe152ac1b69269ae6d815e6`)
- Final diagnostic: `experiments/m1-4d-v2-final-diagnostic/0d8fe3c27163be64ab105da05eadffc2874641df3701cf2a9a2f91bf8e09352e/final-diagnostic.json` (`sha256=ee31bde4944988748a177c5bc6c3d1e9b1bdd30a276fbe7e6d125c1cbd5f5f22`)

The current lineage has no qualifying 2-of-3 version. V2 is complete and
recomputable after the reader fix, but two trials remain non-passing: one
repair response has no complete JSON after bounded format repair, and one
initial response remains Schema-invalid/no-candidate. No Provider call was
made during the offline recomputation.

## Ruling for the current lineage

1. Stop the lineage at the diagnostic terminal state.
2. Do not create V3, add samples, retry trials, change the production Schema
   or validator, create owner approval, or publish an M1-4c handoff.
3. Keep the frozen inputs, component references, prompts, raw Provider
   responses, validation evidence, assessment, and diagnostic-selection bytes
   unchanged.

## Option requiring explicit owner authorization

If a production baseline is still required, authorize a separate fresh
design-7.2.0 calibration lineage. The first hypothesis should be narrowly
limited to the observed structured-output completeness failures while
preserving the current closed Schema, production validator, protocol
neutrality, and topology-preserving repair boundary. Run only its V0 N=3
batch first; continue conditionally only when the recorded V0 result is
failing and supplies evidence for the next revision. Any future prompt
optimization must use `/home/ljf/.codex/agents/architecture-prompt-optimizer.toml`
and the Sol subagent. This option is not authorization to execute it.

Owner decision: `PENDING`

Owner authorization reference: `NONE`
