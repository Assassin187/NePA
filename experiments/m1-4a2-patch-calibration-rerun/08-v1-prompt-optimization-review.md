# ArchitecturePlanner V1 Prompt Optimization Review

- change: `m1-4a2-patch-calibration-rerun`
- agent role: `architecture_prompt_optimizer`
- actual model string: `GPT-5`
- reviewed lineage: `2656c887f54b02c3ec8f7f63a0622d2fb0f6bcb29a366fac51735c5be717d7f0`
- parent prompt: V0 snapshot below, retained unchanged as the baseline for this one-shot optimization; the static correction is within V1
- parent prompt SHA-256: `6a9456c8d876590532a5f6cace2661468023e95bac731a5dcb498fc8da8fc6a8`
- V0 snapshot retained at: `runs/_calibration/s4-architecture/2656c887f54b02c3ec8f7f63a0622d2fb0f6bcb29a366fac51735c5be717d7f0/prompt-development/versions/v0/prompt.md`
- V1 prompt source: `nepa/agents/prompts/architecture_planner.md`
- V1 prompt snapshot: `experiments/m1-4a2-patch-calibration-rerun/prompt-development/versions/v1/prompt.md`
- prior V1 prompt SHA-256 before this static correction: `608d83eeced02c04753e23cf9b3391604a11a93fcb223a9b5240dd5f939267ab`
- V1 prompt SHA-256: `a7374e3019ea8f15efa4fd0f3e3bd6fa957bb86bb373889009e4afdc78ca5b6e`
- snapshot/source byte comparison: passed; the V1 snapshot is byte-identical to the modified source prompt and both hash to `a7374e3019ea8f15efa4fd0f3e3bd6fa957bb86bb373889009e4afdc78ca5b6e`
- static correction: the added rule 4(c) now uses generic contract-required canonical value encoding and hash algorithm wording; the single hypothesis, rule order, and behavior are unchanged

## Single falsifiable hypothesis

The patch-repair instruction is semantically correct but operationally underspecified: it does not explicitly order the use of controller-canonical `allowed_paths` over raw diagnostic paths, explain how to resolve stable array selectors from the current candidate, or require computing each precondition hash from that exact current value. Models therefore copy numeric diagnostic indexes, replace broad collections, and emit fabricated or example hashes. Making this one repair procedure explicit will increase applicable p1 patches and make p2 reachable without changing the validator or patch protocol.

## Evidence supporting the hypothesis

The complete V0 assessment is `experiments/m1-4a2-patch-calibration-rerun/04-v0-assessment.json`. The three complete model reports are:

- Qwen: `runs/_calibration/s4-architecture/2656c887f54b02c3ec8f7f63a0622d2fb0f6bcb29a366fac51735c5be717d7f0/v0/qwen/calibration_report.json`; p0/p1/p2 = `0/0/0`, Schema-after-format-repair = `1.0`, patch attempts/rejections = `3/3`.
- Claude: `runs/_calibration/s4-architecture/2656c887f54b02c3ec8f7f63a0622d2fb0f6bcb29a366fac51735c5be717d7f0/v0/claude/calibration_report.json`; p0/p1/p2 = `0/0/0`, Schema-after-format-repair = `1.0`, patch attempts/rejections = `3/2`.
- DeepSeek: `runs/_calibration/s4-architecture/2656c887f54b02c3ec8f7f63a0622d2fb0f6bcb29a366fac51735c5be717d7f0/v0/deepseek/calibration_report.json`; p0/p1/p2 = `0/0/0`, Schema-after-format-repair = `1.0`, patch attempts/rejections = `3/3`.

All nine p0 candidates were Schema-valid after any format repair and produced concrete `ARCH_VALIDATE` issues. The common p0 gate failures across the three slots were `arch_02` (`ARCH_MODULE_FILE_INVALID`), `arch_06` (`ARCH_WORK_PACKAGE_FILE_PARTITION`) and `arch_09` (`ARCH_DELIVERY_CONSTRAINT_VIOLATION`), which are the ownership/partition failures the p1 repairs attempted to address. Additional p0 diagnostics varied by candidate and are preserved in each `validations/trial_*_p0.json` leaf.

The nine trial validation indexes under the three slot roots record these p1 outcomes:

| slot/trial | p1 evidence | patch/application artifact |
| --- | --- | --- |
| qwen/trial_001 | `patch_rejected=true`; `stale patch precondition: /layout/files` | `qwen/patches/trial_001_p1.json`; `qwen/applications/trial_001_p1.json` |
| qwen/trial_002 | `patch_rejected=true`; `numeric or append array addressing is forbidden: /contracts/0/consumers` | `qwen/patches/trial_002_p1.json`; `qwen/applications/trial_002_p1.json` |
| qwen/trial_003 | `patch_rejected=true`; `numeric or append array addressing is forbidden: /modules/0/owns_files` | `qwen/patches/trial_003_p1.json`; `qwen/applications/trial_003_p1.json` |
| claude/trial_001 | `patch_rejected=true`; `stale patch precondition: /layout/files/types-declarations` | `claude/patches/trial_001_p1.json`; `claude/applications/trial_001_p1.json` |
| claude/trial_002 | `schema_valid=false`; no application formed | no patch/application artifact |
| claude/trial_003 | `patch_rejected=true`; `stale patch precondition: /modules/module-types/owns_files` | `claude/patches/trial_003_p1.json`; `claude/applications/trial_003_p1.json` |
| deepseek/trial_001 | `patch_rejected=true`; `numeric or append array addressing is forbidden: /modules/4/owns_files/1` | `deepseek/patches/trial_001_p1.json`; `deepseek/applications/trial_001_p1.json` |
| deepseek/trial_002 | `patch_rejected=true`; `stale patch precondition: /modules/module-types/owns_files` | `deepseek/patches/trial_002_p1.json`; `deepseek/applications/trial_002_p1.json` |
| deepseek/trial_003 | `patch_rejected=true`; `numeric or append array addressing is forbidden: /modules/0/owns_files` | `deepseek/patches/trial_003_p1.json`; `deepseek/applications/trial_003_p1.json` |

Thus, of nine p1 repair calls, eight were mechanically rejected and one was schema-invalid before application; four used numeric/append array addressing, four used stale preconditions, and no trial reached an applied p1 candidate or p2. Qwen's patches include whole `/layout/files`, `/modules` and `/work_packages` replacements with all-zero hashes; other patches use repeated `a…`, `b…`, `c…` or similarly fabricated hashes. The p1 prompts contained raw numeric validator paths alongside stable canonical `allowed_paths`, while the V0 prompt only said to address both `validation_issues` and `allowed_paths`.

The trial schema has no standalone p1 error-code field. The rejection evidence is therefore reported using `patch_rejected` and deterministic `rejection_reason` text; the p0 validator leaves separately carry the `ARCH_*` codes. No new error code is inferred here.

## Attribution boundary

This is prompt-impactable evidence, not a validator, protocol or infrastructure failure:

- Validator/applier: the deterministic patch and locality test suites recorded by the change passed. The implementation deterministically rejects numeric selectors and stale hashes according to the frozen contract; `locality_failures=0` in all three reports. No validator change is proposed.
- Experiment protocol: each slot has its declared three trials under the same V0 prompt hash and lineage, with isolated trial/trace roots. The reports are complete and use fixed N=3 denominators. No cross-slot, cross-lineage or fallback result is used.
- Infrastructure: all reports are `complete`; all provider calls have non-truncated stop results, `infrastructure_invalid=false`, and the assessment records zero truncation and zero locality failures. There is no transport or provider failure explaining the shared repair shape.
- Prompt: the same V0 repair rule was rendered in all repair prompts, while the model outputs independently exhibit the same numeric-path and fabricated/stale-hash behavior. The failure occurs after valid p0 candidates and before application, where clearer operational prompt instructions can affect model output.

## Exact prompt change

Only rule 4 of `nepa/agents/prompts/architecture_planner.md` changed. It now gives one ordered repair procedure: use `allowed_paths` as the legal target source; treat issue paths as diagnostic; resolve object arrays with candidate-provided stable keys and scalar arrays at their containing field; prefer specific descendants and preserve unrelated subtrees; compute precondition hashes from canonical bytes of the exact candidate value; reject example/zero/guessed hashes; and self-check locality, numeric selectors, preconditions and overlap before returning. Initial-draft rule 3 and full-draft repair rule 5 are unchanged.

## Target gates and metrics

Primary targets are p1 applicability and downstream p2 reachability, with diagnostics for:

- p1 and p2 cumulative success rates per slot, each retaining N=3 and the fixed denominator;
- patch attempts, `patch_rejected` count/rate, and rejection reasons;
- numeric/append-array rejection count/rate and stale-precondition rejection count/rate;
- `schema_valid=false` repair responses and Schema-after-format-repair rate;
- p0→p1 and p1→p2 transitions, with `locality_failures` remaining zero;
- `arch_02`, `arch_06`, `arch_09` per-depth results and all fifteen gate results, including regressions.

The next version can pass screening only if each of Qwen, Claude and DeepSeek independently reaches p2 ≥ 2/3 with complete valid infrastructure and zero truncation. A lower rejection rate or a fallback-selected candidate is diagnostic improvement only and is not a gate pass.

## Expected improvement

The falsifiable expectation is fewer than the V0 baseline's 4/9 numeric-array rejections and 4/9 stale-precondition rejections, ideally zero in each category, with accepted p1 applications and observable p2 calls. The revision is not expected to guarantee p2 screening; applied patches must still pass the unchanged Schema and all fifteen unchanged gates.

## Regression risks

- Models may over-avoid a legal container-field repair when the locality mapping intentionally exposes only a container path, reducing p1 applicability.
- A model may select the wrong stable key or confuse an object-array selector with a scalar-array field; this could produce valid-looking but semantically ineffective patches or later gate regressions.
- The additional procedural text may increase prompt length and repair latency/cost, or compete with the injected candidate context.
- The rule is conditional on the patch contract, so it could not improve initial p0 drafts; this is intentional. Full-draft repair behavior remains unchanged.

## Verification method

Run exactly one fresh V1 N=3 attempt per logical slot under this same lineage and the unchanged protocol, only if Provider I/O is separately authorized. Recompute every report from immutable leaves, then compare V1 with V0 using the fixed per-slot denominators and no mixed lineage. Check the listed p1/p2, rejection, numeric-array, stale-precondition, Schema, locality and per-gate metrics. Stop immediately on the first three-slot p2 screening pass; otherwise admit at most the next evidence-backed revision permitted by the change, never treating fallback as a quality-gate pass.

## Stopping conclusion

One and only one prompt-only revision hypothesis was admitted and implemented. This record does not claim p1/p2 success, screening success, fallback quality, M1-4a3 qualification or production qualification. No Provider/API experiment was started by this optimizer. No validator, schema, experiment protocol, model configuration, lineage component, or `project_docs/system_design.md` file was modified. No V0 artifact was overwritten, deleted or mixed with another lineage or slot.

## Complete diff from parent prompt

```diff
--- parent-prompt.md
+++ v1-prompt.md
@@ -44,7 +44,11 @@
    f. Make contract provider-to-consumer edges agree with the convention layer order and contain no reverse edge or cycle. Keep every path segment and every purpose token within the general responsibility vocabulary or the derived identifier set.
    g. Re-run the complete ordered checks for all fifteen architecture gates before returning the object.
 3. When `repair_context` is null, return a complete ArchitectureDraft.
-4. When `repair_context` is non-null and the caller-supplied contract is the repair-patch contract, return only a closed ordered `patch_ops` array. Use the complete `candidate` in `repair_context` as the immutable baseline, address only its exact `validation_issues` and `allowed_paths`, and use `add`, `replace`, or `remove` with the required presence and prior-value hash preconditions. Never return a complete draft, replace the root, use numeric array indexes, weaken a validator rule, or change an unlisted path.
+4. When `repair_context` is non-null and the caller-supplied contract is the repair-patch contract, return only a closed ordered `patch_ops` array and execute this procedure mechanically:
+   a. Treat `allowed_paths` as the only source of legal patch targets. `validation_issues[*].path` is a diagnostic source path; do not copy a numeric array segment from it. Copy a canonical stable-id path from `allowed_paths` instead: when a source issue is `/contracts/0/consumers`, read the object at `candidate.contracts[0]` and use the corresponding literal path already present in `allowed_paths`, never `/contracts/0/consumers`.
+   b. Walk the complete `candidate` to resolve every chosen path. In an array of objects, address the object with the stable key already present in that object (`id`, `slot_id`, `artifact_id`, `req_id`, or another key explicitly used by that array); never use a decimal index or `-`, and never invent a key. In an array of scalar values, target the allowed containing field rather than an indexed element. Prefer the most specific allowed descendant that fixes the current issue, preserve its siblings and all unrelated subtrees, and do not replace a whole top-level collection merely because that collection path is allowed.
+   c. For every `replace` or `remove`, set `expected_presence` to `present` and compute `expected_value_sha256` from the exact current value at that path in `candidate` using the contract-required canonical value encoding and hash algorithm. For every `add`, set `expected_presence` to `absent` and omit `expected_value_sha256`. Never copy the minimal example's `aaaa...` hash, a zero hash, a guessed hash, or a hash of the whole candidate.
+   d. Before returning, verify that every operation uses an exact path in or below `allowed_paths`, resolves without numeric or append array addressing, has a precondition matching the current `candidate`, and does not overlap another operation. Repair only issues that can be expressed this way; never broaden an unresolved issue into a whole-array or root replacement, weaken a validator rule, or return a complete draft.
 5. When `repair_context` is non-null and the caller-supplied contract is the full-draft contract, use its complete prior candidate as the baseline and change only fields needed by the exact validation issues. Preserve passing fields, then rerun all fifteen checks. Never solve an issue by adding an undeclared fact or by weakening a projection.
 6. State assumptions only in the schema's assumptions array and only when they are not already determined by the named inputs.
```
