# M1-4a2 patch-calibration-rerun implementation brief

Status: implementation and deterministic verification complete; live V0 completed; the earlier optimizer-label blocker was cleared by the owner on 2026-08-29, but prompt iteration has not yet resumed.
Date: 2026-08-29
Change: `m1-4a2-patch-calibration-rerun`

`project_docs/system_design.md` is the controlling design source. This brief records verified repository facts and explicitly labels historical claims; it does not change the design or admit historical evidence into the new lineage.

## 1. Verified design and frozen inputs

The current repository bytes were checked with SHA-256 before implementation:

| artifact | bytes | sha256 |
| --- | ---: | --- |
| `project_docs/system_design.md` (design 6.0.0) | 336382 | `6da0a4918de3e78379e78b6fa371080d5ca6fc225940fac3e0dbb36b02b97d14` |
| `project_docs/pipeline_design_s4_s9.md` | 76145 | `6ebf3c693e519fd14229b3591b4226d51c9df399380f28164d5d981d45c51af8` |
| `gold_file/specIR.json` | 70939 | `a0ec9616eb06c206416a93220e1ea630d04166eb17e102bc9d9476fe2694aa09` |
| `gold_file/target.json` | 93 | `efa8dc8fc0914d5b563a1da1aeaad1a7a277b4b161b89893b8efa75d6818b49b` |
| `gold_file/test_bundle.json` | 8263 | `8f77eb4c5a15ef0ee02979240fbcd4eebdf585d5ad023d130de9468e791d343c` |
| `gold_file/architecture-draft.json` | 5477 | `edb6be655de41055219342dee51cffdcf254086ef437971bd66e0cc2c02e394b` |
| `configs/m1-4a2-live.yaml` | 2307 | `74699b7ce39ffd80f5872214e9acaf025f53b03125ca0e081d919b06fc9535a2` |
| `configs/m1-4a2-context-limits.json` | 50 | `44ac70a01c185392466512c3783cfd7c2bfb3965a8b83484b50c30ef88e1093e` |

The three logical calibration slots and `max_tokens=65536` are present in the checked configuration. The ArchitectureDraft, fifteen-gate validator, serializer, free-layout planning/delivery path, three-slot driver and existing prompt-development/recovery coordinator were inspected as the migration target. Their pre-change source hashes are:

| component source | sha256 |
| --- | --- |
| `nepa/agents/base.py` | `2083d35ad416c4df8057aa4ac11c0c735e20b56bf35e9624fb6875d3f7619990` |
| `nepa/agents/roles.py` | `eaeff1e2c2e83f8ac327d8172a14c20f89affffb75688cf8bc8a809788d7a556` |
| `nepa/agents/prompts/architecture_planner.md` | `cb34eacad305d20962a413dd9a83fe149f0a375fa3fc5526a74935b6093e5e7f` |
| `nepa/calibration/s4_architecture.py` | `8aabbf3ed4b8364495347cf1fb55e43ecdab5cbb054b17cbf603d3053681b865` |
| `nepa/calibration/s4_prompt_development.py` | `14e43420ae8e609206bf910b3a1e7973e6e0f89d45385fdab99647d58c376c97` |
| `nepa/speclib/architecture.py` | `fdf950c9170489ce1322dbb0e087cc57ce15cec56e63138ccea2a69f202f759b` |
| `nepa/speclib/planning.py` | `4e265385c7c7d26272d126829e21897dc96b716aa7f65dafe44698842ad6b836` |
| `nepa/speclib/delivery.py` | `394e713cf382629c79aa59fba8de52d4d6e02b37fb584569a320e3adc6205539` |
| `nepa/speclib/lint.py` | `87181cfefb9dbc24d3185600bdc854f5caa1ee451e9ad8b1b2104f0b872dfc62` |
| `nepa/schemas/architecture-draft.schema.json` | `53c936e83f7bf4cf53011438b4df153334ac58aa22ad484859bd0c6a651e43d6` |

Confirmed pre-change defect: `nepa/calibration/s4_architecture.py` still expects an older `project_docs/system_design.md` hash, while the checked document is design 6.0.0 with the hash recorded above. This is a code baseline update required for the entry gate; `system_design.md` itself is not modified.

## 2. Historical-only inventory

The following are provenance anchors only. They are not copied, resumed, joined, screened, ranked, selected or handed off by this change. The directory manifest is the canonical JSON list of each relative file path, byte length and SHA-256, hashed with compact UTF-8 JSON.

| historical directory | files | manifest sha256 |
| --- | ---: | --- |
| `experiments/m1-4a2-architecture-planner-prompt-optimization/` | 463 | `aaf750a1e64f31946a9dc543d3938ff13494df62f7b4bb24093d06b3daaf81cd` |
| `experiments/m1-architecture-calibration-redo-through-4a2r/` | 8 | `e8b32475e1b8c50c4448db9cd1b23c15ce1ad1e589103a83e6d9498a3bb9fc45` |

Existing calibration lineage inventories, likewise read-only, were recorded as:

| lineage | files | manifest sha256 |
| --- | ---: | --- |
| `49cce2766275f525761fd2a4035df00b20754538f37f9c62bdba061f3bfdbd2a` | 149 | `b9fd66e35991dc7db9700682eb4e900d86d52619247e8d875ac22b61a8843830` |
| `586aef1c0fd52eee0f10e828bf7552a1d2d45a18f6ea119877e24f053a5a7bb7` | 327 | `8a7a5e92b70ed88d9a2aff62787d61fc2ee912a18e21dc226953923ff46de9d3` |
| `756d74f239a07559f40d6c503ef87df9e677bedcbc7db765cae1c4357b69f810` | 278 | `4138b01cf440273141656956fa5398dae2eb58021aff867faf005da0f182253a` |
| `8e0336e9ba795fb913604961a9559aae3433f5e44d81e1571089a1f4785b6d13` | 185 | `08e81e6d7b1474282b43e202ba9473b109fa0fe0b5197a2317a72d4fdc0156b0` |
| `966954e7865b97339a123d30f558fe882a09bac8d00dcf30ead1ea2edae434bf` | 970 | `003fae5965928731264aa8164e2959f6e45ebb18b3da83c02d0e6d1720020fec` |
| `b651aa5c2118add34551a8814859e352c01268548a86b4d704add63b33011ebf` | 491 | `9676d242f4fa4924d9d9d645bb907094422e4c47d5168a220c489e6263dfc548e` |
| `daa917e4c0362d5bce575df3e1ef7436f35942aa0075ba21e3f432ca4ce48772` | 458 | `c95e8fe29c1f96774dc7d3d1b8d236f00b1fc0046da3bbc9d2bc2128ebb860a9` |

The `966954...` lineage and any prior prompt-optimization material remain historical even if their contents resemble the requested protocol. A new lineage is required because design 6.0.0 changes semantic repair to patch mode and changes the development sample/selection protocol.

## 3. Scope, non-secrets and baseline command record

- Only the new experiment directory and the current change are in scope; no historical directory was edited.
- API key values were not read or recorded. The live preflight must record only the required environment-variable names and presence.
- Before this artifact was added, `git status --short` reported only the untracked current change directory. The new experiment directory is intentionally the current change's controlled evidence destination.
- Before the fresh V0 command, no Provider I/O had occurred; the no-I/O preflight and complete task-group-5 gate were therefore satisfied before live execution. The fresh V0 Provider calls are recorded only under the new lineage below.

## 4. Deterministic pre-live gate

The following commands were run after implementation and before any live command:

| command | result |
| --- | --- |
| `uv run python -m pytest -q` | `356 passed in 126.76s` |
| `uv run nepa lint spec gold_file/specIR.json` | valid, zero errors/warnings |
| `uv run nepa lint spec gold_file/specIR.json --gold --manifest gold_file/test_bundle.json` | valid, zero errors/warnings |
| `uv run nepa lint target gold_file/target.json --spec gold_file/specIR.json` | valid, zero errors/warnings |
| `uv run nepa lint test-bundle gold_file/test_bundle.json --spec gold_file/specIR.json` | valid, zero errors/warnings |
| `openspec validate --all --strict` | 6 passed, 0 failed |
| `git diff --check` | passed |

The focused task-group-5 suites also passed: `92 passed` for architecture calibration, patch/locality, lineage, artifacts, reporting, Agent invocation and historical recovery; `24 passed` for current prompt development and protocol/surface tests; `26 passed` for schema examples and patch tests. The repository gold inputs were independently checked by the four `nepa lint` commands above.

The no-I/O preflight is recorded in `02-preflight.json`. It resolved all three fixed logical slots, context limits, gold inputs and current prompt neutrality, and observed `NEPA_CLAUDE_API_KEY`, `NEPA_DS_API_KEY` and `NEPA_QWEN_API_KEY` as present without recording values. The preflight constructed and validated the frozen architecture inputs but created no provider client and made zero provider calls.

The fresh live lineage initialization command was:
`./run_m1_calibration.sh init --config configs/m1-4a2-live.yaml --context-limits configs/m1-4a2-context-limits.json --runs-root runs --spec gold_file/specIR.json --target gold_file/target.json --test-bundle gold_file/test_bundle.json`
It produced lineage `2656c887f54b02c3ec8f7f63a0622d2fb0f6bcb29a366fac51735c5be717d7f0`. The independent initialization audit is recorded in `03-lineage-initialization.json`: patch contract and all ten controlled component references verify, the v0 prompt bytes match the repository source, no trial leaves existed before V0, and no historical directory was admitted.

## 5. Fresh V0 live result and stop fact

The one permitted fresh live attempt was a coherent V0 N=3 attempt for all three fixed slots. It completed with three trials per slot, stable lineage/model identity, zero truncations and no infrastructure-invalid slots. All three model reports are independently recomputable, but screening failed because each slot had `p2=0.0` (the fixed threshold is `0.60`). The canonical leaf recomputation and compact evidence are recorded at:

- `runs/_calibration/s4-architecture/2656c887f54b02c3ec8f7f63a0622d2fb0f6bcb29a366fac51735c5be717d7f0/prompt-development/versions/v0/assessment-n003.json`
- `experiments/m1-4a2-patch-calibration-rerun/04-v0-assessment.json`

The cross-slot failures are patch-contract failures, not validator, protocol or infrastructure failures: p0 candidates were schema-valid, p1 patch applications reported numeric-array addressing or stale preconditions, locality failures were zero, and all provider calls were non-truncated. The current prompt hash remained `6a9456c8d876590532a5f6cace2661468023e95bac731a5dcb498fc8da8fc6a8`.

Because this complete valid round showed a prompt-impactable failure, the one allowed `architecture_prompt_optimizer` was launched with the configured `gpt-5.6-sol` role. The agent reported its resolved model as `GPT-5`, which is not the required exact model string, and stopped before reading materials or changing files. Iteration is consequently stopped; no Luna fallback optimization, V1 API attempt, terminal selection/fallback, downstream handoff, M1-4a3 qualification or production claim is admitted. The blocker is recorded in `05-optimizer-model-blocker.json`.

On 2026-08-29, the owner changed only the optimizer-subagent orchestration policy: an explicit Sol assignment is sufficient, while a self-reported model label is diagnostic and need not equal `gpt-5.6-sol`. This clears the operational blocker recorded in `05-optimizer-model-blocker.json` without rewriting that historical record or changing any lineage-bound experiment control. The update is recorded in `07-optimizer-model-policy-update.json`. It does not itself modify the prompt, admit V1, or start Provider I/O.

## 6. V1 prompt revision and terminal stop fact

After the explicit owner policy update, the same single `architecture_prompt_optimizer` agent was resumed. It supplied one evidence-backed prompt-only hypothesis from the complete V0 assessment. Luna reviewed and admitted exactly one V1 revision, retaining the V0 prompt and its hash as the parent:

- parent V0 prompt: `6a9456c8d876590532a5f6cace2661468023e95bac731a5dcb498fc8da8fc6a8`;
- corrected V1 prompt: `a7374e3019ea8f15efa4fd0f3e3bd6fa957bb86bb373889009e4afdc78ca5b6e`;
- revision record: `runs/_calibration/s4-architecture/2656c887f54b02c3ec8f7f63a0622d2fb0f6bcb29a366fac51735c5be717d7f0/prompt-development/versions/v1/revision.json`;
- optimizer review and complete parent diff: `08-v1-prompt-optimization-review.md`.

The V1 source and snapshot are byte-identical. The optimizer did not issue API calls, edit the validator, alter the protocol, or overwrite V0 evidence. Its reported runtime model was `GPT-5`; the owner explicitly authorized continuation under the configured Sol assignment, and no exact self-reported model string is used as an experiment metric.

The complete V1 attempt used the same fresh lineage and fixed three slots, with N=3 for Qwen, Claude and DeepSeek. All reports and the assessment recomputed successfully from immutable leaves:

| slot | model string | p0 / p1 / p2 | Schema after format repair | patch attempts / rejected | truncations | report SHA-256 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Qwen | `qwen3.7-max-2026-06-08` | `0 / 0 / 0` | `1.0` | `3 / 3` | `0` | `f5f7e61d47e1d0933bca0519d1559c1f6a5a5bf72c6fce581058ec38f2f74597` |
| Claude | `claude-opus-5` | `0 / 0 / 0` | `1.0` | `3 / 3` | `1` | `3a2f8f71adf4cbbe9b63db9e0c0f4372e6c41f44bc32b7158861cefbece99a7a` |
| DeepSeek | `deepseek-v4-flash` | `0 / 0 / 0` | `1.0` | `3 / 3` | `0` | `badb97bec659ccc40727dd6e092eee06eace872db680b951b30bd8ace1254980` |

V1 therefore failed the independent three-slot `p2 >= 0.60` screen. Locality failures remained zero and no infrastructure-invalid slot occurred. The nine p1 outcomes were all rejected: eight stale-precondition rejections and one implicit-key array rejection; no p1 candidate reached p2. The V1 assessment is `runs/_calibration/s4-architecture/2656c887f54b02c3ec8f7f63a0622d2fb0f6bcb29a366fac51735c5be717d7f0/prompt-development/versions/v1/assessment-n003.json`, and its attempt outcome is `runs/_calibration/s4-architecture/2656c887f54b02c3ec8f7f63a0622d2fb0f6bcb29a366fac51735c5be717d7f0/prompt-development/versions/v1/attempts/attempt_001/outcome.json`.

This execution stops after the one allowed custom optimizer and its one admitted V1 hypothesis because the hypothesis was not supported and the owner instruction permits only that single optimizer; Luna does not perform a second prompt optimization. V2～V4 were not issued, so the design-required V0～V4 terminal fallback/tie cannot be published. Tasks 6.3, 6.4 and 7.1 remain open for that reason. No prompt selection, handoff, M1-4a3 qualification, M1-4a2r authorization or production claim is made.
