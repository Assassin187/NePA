# M1-13 focused acceptance record

Date: 2026-09-11
Status: superseded by the ArchitecturePlanner 65,536-token configuration correction; new-group preregistration, execution, focused acceptance, and responsible-owner decision pending.

## Post-baseline configuration correction

- On 2026-09-11, the responsible owner directed production configuration to resolve `architecture_planner.max_tokens` to `65536` while leaving the shared T1 limit at `16000`.
- `configs/default.yaml` and the Python fallback now carry the same role-level override. No other role receives the larger budget.
- `uv run pytest -q tests/test_config.py` passed with `12 passed`; it verifies ArchitecturePlanner=`65536`, TaskPlanner=`16000`, and equality between the checked-in YAML and fallback role configuration.
- The prior 16K experiment and focused transcript below remain historical evidence only. They are not final acceptance for the new configuration and must not be pooled into the new experiment's denominator.

## Entry and execution record

- M1-12 responsible-owner approval and its post-approval verification were confirmed before implementation.
- This change was the only active OpenSpec change at entry; production revision limits remained F2/F3=`0/0`; the initial `runs/` inventory contained only `_calibration`.
- The original Claude configuration used the declared $5/1M input and $25/1M output prices. The $0.5/1M cached-input price remained reference-only because no trustworthy cached-token split was returned.
- Claude runs `20260911T074906Z_MQTT_spec-run` and `20260911T080027Z_MQTT_spec-run` finalized `internal_error`. The owner then directed the batch to stop and restart with `deepseek-v4-pro`; both Claude runs are retained as excluded evidence.
- The amended DeepSeek Pro protocol passed `--protocol-check` before new provider I/O. Its complete g03 N=5 group comprises runs `20260911T083325Z_MQTT_spec-run`, `20260911T084237Z_MQTT_spec-run`, `20260911T085312Z_MQTT_spec-run`, `20260911T090028Z_MQTT_spec-run`, and `20260911T090748Z_MQTT_spec-run`.
- All five admitted runs finalized `controlled_exit` at S4 after structured-output validation failure. They published no Plan, Plan State, F0/F1 task, build, or smoke artifact. The frozen natural-failure sample inventory is therefore empty; D1.14 and production enablement are not established, and all ten parameter decisions are `insufficient_evidence`.
- DeepSeek Pro admitted-run telemetry totals 245,600 input tokens, 160,000 output tokens, and $0.302333 recorded USD cost under the study estimate. DeepSeek Pro pricing is explicitly an estimate based on the provided high-period DeepSeek reference, not a provider billing quote.
- Accounting conversion is frozen at `1 USD = ¥7.2`. Qwen is ¥12/1M input and ¥36/1M output; DeepSeek reference is ¥1.5/¥4.5 off-peak and ¥3/¥9 peak. Costs are descriptive only and do not affect admission, classification, parameter selection, or acceptance.
- Read-only monitor identity: `/root/m1_13_readonly_monitor` (`gpt-5.6-luna`). It had no file ownership or launch/retry/resume/classification/acceptance authority. Its notification was independently checked against Run artifacts and was not treated as scientific evidence.

## Exact formal run command

The following command was executed sequentially once for each admitted DeepSeek Pro logical run:

```text
uv run nepa run --spec gold_file/specIR.json --target gold_file/target.json --test-bundle gold_file/test_bundle.json --config experiments/m1-13-natural-failure-root-cause-study/study-config-deepseek-pro.yaml --until s6 --runs-root runs
```

## Focused verification

The final focused verification commands and results are recorded below. Repository-wide pytest, unrelated test modules, `nepa eval runs`, and `openspec validate --all --strict` were intentionally not run by user direction.

```text
uv run pytest -q tests/test_m1_13_root_cause_study.py
21 passed in 0.38s

uv run python experiments/m1-13-natural-failure-root-cause-study/scripts/audit_root_causes.py --study-root experiments/m1-13-natural-failure-root-cause-study --runs-root runs --check
passed (exit 0, no output)

uv run ruff check experiments/m1-13-natural-failure-root-cause-study/scripts/audit_root_causes.py tests/test_m1_13_root_cause_study.py
All checks passed!

uv run mypy experiments/m1-13-natural-failure-root-cause-study/scripts/audit_root_causes.py
Success: no issues found in 1 source file

openspec validate m1-13-natural-failure-root-cause-study-and-parameter-selection --strict
Change 'm1-13-natural-failure-root-cause-study-and-parameter-selection' is valid

git diff --check
passed (exit 0, no output)
```

The separately affected provider contract was checked with `uv run pytest -q tests/test_llm_providers.py` (26 passed) before the formal batch. No full test suite was run.

## Scope and limitations

- At the time of the baseline transcript, no production configuration was changed. That statement is now superseded only by the explicit `architecture_planner.max_tokens=65536` role override; no public CLI/API, dependency, shared metric formula, `project_docs/` design document, PlanReviser behavior, M1-14/M1-15 work, TR-5/TR-9 enablement, S7/S8/M2 path, archived change, or raw Run artifact was added to the tracked change.
- The only runtime correction is the explicitly authorized provider identity-observation fix: exact returned identity or absence is recorded in metadata while the existing public response contract is preserved.
- The N=5 result is descriptive. All admitted runs stopped before S6, so it supplies no natural F0/F1 failure sample and cannot support root-cause proportions or parameter selection.
- `06-owner-decision.md` remains pending. Machine checks cannot substitute for the responsible owner's explicit dated decision.
