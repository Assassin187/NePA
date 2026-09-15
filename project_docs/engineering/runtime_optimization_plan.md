# NePA runtime optimization execution record

Status: P0/P1 implementation complete; live promotion remains blocked/not admitted. This record is derived from
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
- 2026-09-15: froze candidate commit `50dea22`, runtime/config/input/image
  fingerprints, and started the first paid staged MQTT/HTTP candidate batch at
  `runs/protocol-expansion/6e05a18d10c54f6b832b7126e2a7dfb1` from a clean
  detached worktree. The protocols run independently, so one failure does not cancel
  the other.
- 2026-09-15: MQTT candidate run `20260915T090928Z-dc874827` was rejected before
  provider I/O (zero calls and zero cost). Its configured campaign root contains
  Run6 CNY evidence plus zero-cost Run7 invalid records, while
  `runs/mqtt-e2e-cny` contains separate Run7 charged evidence. The Run6 executor
  correctly refused to reinterpret or omit incompatible campaign records. Preserve
  this failed sample. Selecting a compatible root is blocked on resolving which
  existing ledger is authoritative; do not create a fresh ledger or exclude charged
  history merely to make the test run.
- 2026-09-15: HTTP candidate run `20260915T090928Z-6f4e0a83` succeeded. All 10
  tasks, 27 primary claims, release/sanitizer clean builds, in-run acceptance,
  exported-copy checks and a second independent rebuild/acceptance passed. The run
  used 205 provider attempts, 10 sessions with no continuation, 1,197.509 API
  seconds, 46 strict rejections (22.44%, 120.622 API seconds), 3,364,172 input and
  179,166 output tokens, and CNY4.120943. Independent rebuild plus acceptance took
  8.401 seconds. The total wall time was 1,558.299 seconds.
- 2026-09-15: compared with successful HTTP baseline
  `runs/baseline-10cb-http/20260914T151924Z-92347b6d`, the candidate reduced calls
  8.89%, strict rejects 9.80%, rejected-response API time 17.43%, input tokens 5.76%,
  actions 8.62%, and continuation count from one to zero. At a common peak-price
  normalization, cost fell from CNY5.231968 to CNY4.120943 (21.24%). Actual charged
  cost is not directly comparable because the baseline ran fully off-peak and the
  candidate fully at peak. Wall time increased 15.66%, API time 7.36%, output tokens
  19.90%, failed actions from four to six, and agent-command time 66.78%. This single
  candidate therefore does not meet the performance promotion gate despite complete
  stability checks.
- 2026-09-15: 40 of the 46 HTTP rejections contained a DSML/native wrapper. The
  strict accepted set remains unchanged. Commit `5e1ec44` adds a deterministic
  wrapper subtype, exact correction text, an end-of-decision outer-JSON reminder,
  and independently persisted real diagnostics so a format error cannot hide a
  build/verification failure after process re-entry. This post-run refinement passed
  186 tests with one paid-live test skipped, Ruff, mypy and package build. The paid
  HTTP run predates this commit and is screening evidence only.
- Paid cost in this optimization iteration is CNY4.120943 settled; the MQTT
  infrastructure rejection used zero calls and zero cost. There are no unknown
  reservations from the completed batch.
- Pending: resolve the MQTT campaign-version ambiguity without changing or bypassing
  budget evidence, then run the final commit's paired candidate screening and the
  preregistered alternating A/B repetitions. No candidate has met the live promotion
  gate.

## Resume checklist

On continuation, record the current commit and config/runtime hashes, completed item,
targeted/full test results, paid experiment cost, confirmed findings, open risks and
the next action here. Preserve historical runs and the user's unrelated worktree
changes.
