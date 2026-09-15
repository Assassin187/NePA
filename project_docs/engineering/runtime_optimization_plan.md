# NePA runtime optimization execution record

Status: implementation in progress. This record is derived from
`../experiments/mqtt_runtime_latency_analysis_20260914.md` and the current runtime.
It does not replace `../system_design.md`.

## Objective and invariants

Reduce end-to-end latency and model cost without reducing generation completion,
clean release/sanitizer builds, required behavior, independent acceptance or evidence
quality. Keep the serial single-writer task graph, current model route, decision and
session budgets, 180000-byte wire limit, strict action validation, all task build
gates, integration verification and final exported-copy verification.

## Confirmed baseline

| Metric | MQTT | HTTP |
|---|---:|---:|
| Approximate wall time | 5249.9 s | 1347.3 s |
| LLM calls / API time | 839 / 4679.5 s | 225 / 1115.4 s |
| Tasks / sessions | 23 / 29 | 10 / 11 |
| Strictly rejected actions | 207 (24.67%), 803.5 s | 51 (22.67%), 146.1 s |
| Recorded command time | 414.1 s | 194.9 s |
| Task builds / integration verification | 20.2 s / 44.4 s | 9.0 s / 7.5 s |
| Final export gate | 48.3 s | 9.6 s |
| Cost estimate | CNY13.2430 | CNY2.6160 |

Both historical runs completed all tasks and claims, clean release/sanitizer builds,
and final acceptance. They are descriptive evidence, not a success-rate estimate.
The current strict decoder, rather than the older report heuristic, defines rejected
actions. The historical candidate and current HEAD have different runtime hashes, so
future A/B runs require a freshly frozen control.

## Work items

| Priority | Item | State | Admission condition |
|---|---|---|---|
| P0 | Reproducible performance summary and phase timing | implemented; offline verified | Historical totals reproduce and outcome semantics are unchanged |
| P0 | Classified strict-action correction feedback and resume persistence | implemented; offline verified | Same accept/reject set; malformed responses execute nothing |
| P1 | Persistent line-range reads | implemented; offline verified | Exact content/SHA/evidence and unchanged capacity protections |
| P1 | Deterministic requirement-to-structure navigation | implemented; offline verified | Same tasks, ordering and primary requirement ownership |
| P1 | Refresh already-observed selections after successful edits | implemented; offline verified | Host reread only; commands/deletes/failures still invalidate |
| P1 | State the fixed checks performed by finish | implemented; offline verified | No build or acceptance gate removed |
| P2 | Carry verified observations across tasks | deferred | P0/P1 leave measurable cross-task reread cost and replay is safe |
| P2 | Parallel isolated release/sanitizer verification | deferred | Worth more than copy/resource overhead; interruption cleanup proven |

## Validation and A/B policy

Each behavior change is independently reviewable and runs only its affected tests
during development. Before a paid run, freeze inputs, config, image, runtime and A/B
order. Count failures, interruptions, budget exhaustion and unknown reservations.
Compare wall/API time, calls, sessions, task spans, rejected and failed actions,
commands, builds, verification, tokens, cache use, cost, generation completion,
release/sanitizer compilation and every required acceptance result. No candidate is
admitted after a stability regression. The final candidate needs three consecutive
independent successful MQTT runs and three HTTP runs; this is engineering admission
evidence, not statistical proof of an unchanged population success rate.

## Execution log

- 2026-09-15: read-only audit completed. Current JSON-object mode, one-time action
  schema injection, hash-validated observations and complete transaction eviction are
  already implemented. Native tools are not promoted: the DeepSeek study failed its
  gates; the separate Qwen study used a different runtime/window and failed one short
  session.
- 2026-09-15: existing context and plan-compiler targeted tests passed (17 tests).
- 2026-09-15: added `nepa analyze RUN_DIR [--output PATH]`. Historical replay
  reproduced MQTT 839 calls, 207 strict rejections, 803.509 rejected API seconds,
  29 sessions, 20.208 task-build seconds and 44.350 integration-verification
  seconds; HTTP replay reproduced 225 calls, 51 rejections and 146.078 rejected
  API seconds.
- 2026-09-15: P0/P1 implementation passed 184 tests with one paid-live test
  skipped, Ruff, mypy and `uv build` for sdist/wheel. No real model call was made.
- 2026-09-15: implementation commits are `52a14b8` (source observation and
  deterministic navigation) and `982e322` (measurement and strict-action feedback).
- P2 remains deferred. Cross-task carry-over needs post-P1 live evidence; parallel
  verification has an MQTT upper bound below one percent of wall time and still
  requires isolated workspaces plus multi-container interruption cleanup.
- Pending: freeze the control/candidate experiment assets and run paid staged A/B.
- Paid model experiments have not been started by this optimization iteration.

## Resume checklist

On continuation, record the current commit and config/runtime hashes, completed item,
targeted/full test results, paid experiment cost, confirmed findings, open risks and
the next action here. Preserve historical runs and the user's unrelated worktree
changes.
