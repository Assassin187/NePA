# M1-12 focused acceptance record

Date: 2026-09-11

Status: responsible-owner acceptance and post-approval final revalidation are complete; M1-12 is approved for archive. Earlier results below are retained as historical evidence and are superseded by the latest corrective record in this document.

## Entry and scope

- M1-11's archived acceptance record contains the superseding responsible-owner approval dated 2026-09-10.
- `openspec list --json` showed M1-12 as the only active change before implementation.
- Entry baselines were `18 passed, 0 failed` for strict all-item OpenSpec validation and `89/783` for the `revision_mechanism` collection.
- Production `revision_f2_limit` / `revision_f3_limit` remain `0/0` in both `configs/default.yaml` and `nepa/config.py`.
- No file under `project_docs/`, archived changes, prompts, calibration assets or the public CLI/config/Stage surface was changed.

## Requirement closure

| Requirement | Schema-valid evidence | Projection / producer | Consumer / exit | Replay / regression evidence |
|---|---|---|---|---|
| Activation-bound terminal evaluation | tightened revision-ledger v2 Schema and non-empty example | obligation-anchor derivation and three-outcome pure evaluator | S6 evaluates pending activation before new trigger selection | unique activation, canonical ordering, hash-chain, replay and conflict tests |
| Pure availability and breakers | accepted trigger/rejection/activation/evaluation entries | `project_revision_availability` derives attempted pairs, independent counts/streaks/closures and lock | trigger selection, RG-1 and RG-3 share the projection | zero/equality limits, cross-level non-reset/reset, F5 and ineffective lock tests |
| Evidence and cost identity | sorted unique evidence/call refs and positive revision identity | SHA-256 boundary identity, output-path deduplication and exact non-negative accumulation | append-only `revision_evaluated`; metrics key by `revision_seq` | resolved/ineffective/unresolved, duplicate call and zero-cost regressions |
| S6 hard limits and degradation | State/history, group results and termination request remain authoritative | pre-allocation call-cap check; group failure preserves baseline, attempts and all refs | only fully independent DAG/contract/build branch continues; otherwise `EXECUTION_UNRESOLVED` | degraded/10, termination/resume and partial-report suites |
| Protocol neutrality | MQTT and non-MQTT fixtures bind checked-in source hashes | one generator and one protocol-neutral core path | existing S5/S6 consumers | two temporary generations were byte-identical and matched checked-in output |

## Focused machine acceptance

- `uv run pytest -q -m revision_mechanism` — `95 passed, 698 deselected in 68.32s`.
- `uv run pytest -q -m metric_contract` — `9 passed, 782 deselected in 1.72s`.
- Explicit Schema/config/ledger, activation/S5/S6/RunStore matrix — `277 passed in 416.71s`.
- Explicit budget/orchestrator/termination/runtime-resume/S9/CLI/Plan-lint matrix — `39 passed in 8.93s`.
- Post-matrix focused additions: ledger/candidate recovery `4 passed`; cap/group-independence `4 passed`; Schema revision selector `1 passed`; Plan revision/State `18 passed`.
- Final affected S6 module rerun after usage accounting and publication rechecks — `101 passed in 272.32s`.
- `uv run ruff check .` — passed.
- `uv run mypy nepa` — passed with no issues in 4 source files.
- Public Spec, gold-coverage Spec, Target and Test Bundle CLI lints — valid with zero errors and warnings. `tests/test_plan_lint.py` passed in the explicit public-contract matrix using a freshly linked matched Plan/Spec/manifest set.
- `openspec validate m1-12-revision-evaluation-circuit-breakers-and-controlled-degradation --strict` — valid.
- `openspec validate --all --strict` — `18 passed, 0 failed`.
- `git diff --check` — passed.

No unselected repository-wide pytest command was run. In particular, neither `uv run pytest -q`, `uv run pytest -q tests`, nor an equivalent full-suite command was executed. Therefore this record does not claim that the full repository test suite is green.

## Scope and limitations

- revision-ledger remains v2; no evaluation WAL, writable lock/counter field, stage, dependency, CLI option or configuration key was added.
- F4/F5 remain diagnostic facts; M1-12 adds no automatic F5 producer.
- PlanReviser, TR-9, M1-13/M1-14/M1-15 policy, S7/S8/M2 behavior and historical migration remain outside this change.
- A direct CLI lint of the checked-in non-MQTT Plan with its companion files retains the documented predecessor `PLAN_INPUT_REF_DRIFT` fixture-harness distinction. The public Plan-lint implementation is covered by the matched `_linked()` regression and was not changed to hide that fixture mismatch.

## Corrective focused acceptance — 2026-09-11

- Closed the reviewed defects: Agent usage now has one production owner (`LLMClient`); obligation scope is derived once and includes dependency/contract closure; dependency propagation processes evaluation before exit; evaluation evidence/cost is activation-bound; artifact damage maps to failed/20 while invariant errors remain internal_error/1; `evaluated_at` includes the ledger prefix; and a second activation before evaluation is invalid.
- `uv run pytest -q -m revision_mechanism` — `101 passed, 699 deselected in 73.80s`.
- `uv run pytest -q -m metric_contract` — `9 passed, 791 deselected in 1.71s`.
- Schema/config/ledger matrix — `53 passed in 2.27s`.
- Activation/S5/S6/RunStore matrix — `228 passed in 415.76s` after correcting predecessor fixtures to insert the now-required terminal evaluation between activations.
- Final affected S6 module rerun after the last evidence-association checks — `103 passed in 273.77s`.
- Budget/LLM/orchestrator/termination/resume/S9 matrix — `30 passed in 5.24s`.
- Public CLI/Plan-lint matrix — `14 passed in 3.86s`.
- Focused defect regressions cover the real `AgentInvoker -> LLMClient -> Orchestrator` accounting chain, dependency/contract closure, post-propagation evaluation ordering, exact cross-level RG-3 allowance, activation-bound call/evidence collection, telemetry fallback/conflict and artifact/internal-error classification.
- The revision fixture generator was run into two independent temporary directories; both outputs were byte-identical and matched the MQTT/non-MQTT checked-in fixtures.
- `uv run ruff check .`, `uv run mypy nepa`, public Spec/gold/Target/Test Bundle lints, current/all strict OpenSpec validation (`18 passed, 0 failed`) and `git diff --check` passed.
- Final scope scan found no changes under `project_docs/`, archived changes, prompts, public CLI/config, `configs/default.yaml` or `nepa/config.py`; production revision limits remain `0/0`.
- No unselected repository-wide pytest command was run. This corrective record does not claim that the full repository test suite is green.

## Review-remediation acceptance — 2026-09-11

- Replaced the mutable `plan/state_history.json` evaluation ref with a deterministic immutable per-revision snapshot published only after terminal evaluation readiness. A later State-history append leaves the accepted evaluation ref verifiable; pending evaluation publishes no snapshot, and immutable publication conflicts retain the existing artifact-damage route.
- Corrected activation-bound call association so each migration-derived `(task_id, attempt)` consumes only the last accepted S6 trace row at the stable boundary. A pre-activation row with the same reset attempt is excluded; critic, group, AMEND and zero-call behavior remain on the existing identities.
- Corrected metric fallback semantics so explicit `cost_usd=0` is checked against supplied telemetry instead of being treated as an absent fallback. Missing telemetry remains ledger-authoritative and the metric formula is unchanged.
- `uv run pytest -q tests/test_s6_execution.py` — `103 passed in 270.60s`.
- `uv run pytest -q -m revision_mechanism` — `101 passed, 699 deselected in 74.20s`.
- `uv run pytest -q -m metric_contract` — `9 passed, 791 deselected in 1.72s`.
- `uv run pytest -q -m s5_epoch` — `52 passed, 748 deselected in 103.89s`.
- `uv run pytest -q -m s6_execution` — `85 passed, 715 deselected in 254.94s`.
- Complete repository test suite `uv run pytest -q` — `800 passed in 735.00s`.
- `uv run pytest -q tests/test_schema_examples.py` — `20 passed in 1.53s`; public CLI/Plan-lint matrix — `14 passed in 4.00s`.
- `uv run ruff check .`, `uv run mypy nepa`, public Spec/Target/Test Bundle lints, Docker sandbox build, current/all strict OpenSpec validation (`18 passed, 0 failed`) and `git diff --check` passed.
- The public CI workflow contained a stale direct validation of archived change `m1-6-s6-f0-execution`; that redundant failing step was removed while retaining `openspec validate --all --strict`, which validates the active change and all main specs.
- Final scope scan confirms no change under `project_docs/`, archived changes, prompts, public CLI/config, `configs/default.yaml` or `nepa/config.py`; production revision limits remain `0/0`. No M1-13/M1-14/M1-15, PlanReviser, TR-9, S7/S8/M2 or historical migration behavior was added.

## Responsible-owner gate

On 2026-09-11, the responsible owner explicitly approved acceptance of the final corrected M1-12 diff and its recorded machine-verification package. The reviewed scope includes terminal revision evaluation, ledger-derived circuit breakers, S6 independent-branch/controlled-degradation behavior, immutable evaluation evidence, activation-bound call association, metric cost consistency, protocol-neutral fixtures, the public-CI correction, and the review-remediation results recorded above. No further correction was requested.

This approval satisfies task 7.8 for M1-12 implementation acceptance only. It does not select M1-13 parameters, enable non-zero production revision limits, approve M1-14/M1-15 behavior, or waive the task 7.9 post-approval final diff and complete-CI verification.

## Post-approval final acceptance — 2026-09-11

- Inspected the complete final diff after owner approval; no additional correction was requested or required.
- `uv run pytest -q` — `800 passed in 720.97s`.
- `uv run pytest -q tests/test_s6_execution.py` — `103 passed in 286.59s`.
- `uv run pytest -q -m revision_mechanism` — `101 passed, 699 deselected in 75.96s`.
- `uv run pytest -q -m metric_contract` — `9 passed, 791 deselected in 1.91s`.
- `uv run pytest -q -m s5_epoch` — `52 passed, 748 deselected in 103.81s`.
- `uv run pytest -q -m s6_execution` — `85 passed, 715 deselected in 257.19s`.
- Schema examples — `20 passed in 1.53s`; public CLI/Plan-lint matrix — `14 passed in 4.09s`.
- `uv run ruff check .`, `uv run mypy nepa`, public Spec/gold/Target/Test Bundle lints, Docker sandbox build, current-change strict validation, all-item strict OpenSpec validation (`18 passed, 0 failed`) and `git diff --check` passed.
- Final scope inspection found no changes under `project_docs/`, previously archived changes, prompts, `configs/default.yaml` or `nepa/config.py`; public CLI/config surfaces and production revision limits `0/0` remain unchanged. M1-13/M1-14/M1-15, PlanReviser calls, TR-9, S7/S8/M2 and historical migration remain outside the implementation.
- Tasks 7.8 and 7.9 are complete. The final corrected M1-12 change is accepted and ready for spec synchronization and archive, without implying production enablement.
- On 2026-09-11, the responsible owner explicitly selected archive without synchronizing the four delta-spec capability files into the main OpenSpec specs. The delta specs remain preserved inside this archived change; main specs are intentionally unchanged by the archive operation.
