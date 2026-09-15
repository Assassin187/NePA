# NePA runtime optimization execution record

Status: P0/P1 implementation complete; HTTP stability passed, end-to-end performance remains inconclusive, and experiments are stopped before MQTT. This record is derived from
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
- 2026-09-15: user directed HTTP-first admission and delegated routine waiting to a
  low-cost read-only monitor. Frozen final candidate `b260238` (runtime
  `77bf581f40b54607cda1afd4fdad6005a22270b06d3de98c6c61a7a9a33daea2`)
  started HTTP run `20260915T095356Z-b66a6802` in batch
  `runs/protocol-expansion/runtime-opt-http-68941bb02f024e73b78792bcc4d1bf0f`.
  The compatible Run6 HTTP campaign had CNY6.409243 recorded before launch,
  including a preserved CNY0.236486 unknown reservation from an older successful
  run. Do not start MQTT unless repeated HTTP evidence meets the stability and
  performance admission gates.
- 2026-09-15: final-candidate HTTP run `20260915T095356Z-b66a6802` passed all 10
  tasks, 27 claims, clean release/sanitizer builds, all in-run/export acceptance and
  the separate independent rebuild/check. Against baseline `20260914T151924Z-92347b6d`,
  wall time fell 16.74% (1,347.259 to 1,121.686 seconds), provider attempts 24.44%
  (225 to 170), API time 9.23%, strict rejects 52.94% (51 to 24), input tokens 28.63%,
  actions 16.67%, failed actions 50%, and model command time 66.05%. There were no
  continuations. Peak-normalized cost fell 31.05% (CNY5.231968 to CNY3.607308);
  actual CNY2.616588 crossed off-peak and peak periods and is not the normalized
  comparison. Two provider `length` responses consumed 126.618 rejected API seconds,
  so rejected-response cumulative time increased despite fewer rejections. One
  provider attempt failed and was retried under the unchanged transport policy.
  Derived evidence is in
  `runs/protocol-expansion/runtime-opt-http-68941bb02f024e73b78792bcc4d1bf0f/`.
- Decision: this is a promising first exact-final-candidate result, not sufficient
  promotion evidence. Run two more consecutive HTTP samples at the same frozen
  commit/config/input/image. Start no MQTT experiment unless all three remain fully
  successful and the paired median meets the declared performance gate.
- 2026-09-15: HTTP repetition 2 started as run `20260915T101457Z-5c86d860`, batch
  `runs/protocol-expansion/runtime-opt-http-r2-f6ce3c835178489eb371fb9e8bcdc561`,
  under the identical frozen candidate. A low-cost read-only monitor owns routine
  progress polling; the main task retains diagnosis and admission decisions.
- 2026-09-15: HTTP repetition 2 passed all stability gates and independent checks.
  It used 183 attempts, 10 sessions/no continuation and CNY2.432041 actual off-peak
  cost. Against the same baseline, calls fell 18.67%, strict rejects 35.29%, command
  time 60.49%, input tokens 10.74% and peak-normalized cost 7.03%. Wall time was
  effectively unchanged (+0.06%, 1,348.047 seconds) and API time rose 10.93%; output
  tokens rose 33.37%. This run alone does not pass the time gate, but two exact
  candidate runs remain fully successful and their provisional wall-time midpoint is
  better than baseline. Complete repetition 3 before calculating the declared median.
- Monitoring note: the low-cost monitoring agent exhausted its separate usage quota
  after this run without touching the experiment. The main task resumed lightweight
  polling; experiment execution and evidence were uninterrupted.
- 2026-09-15: HTTP repetition 3 started as run `20260915T133734Z-c3747f61`, batch
  `runs/protocol-expansion/runtime-opt-http-r3-08ae447b1b0c49358312f98c2693cc79`,
  with the same `b260238` fingerprint. MQTT remains gated on its result and the
  three-run HTTP median.
- Control preparation: repository commit `73cdc4b` exactly reproduces the historical
  control runtime hash `cf4b589a6dadd872890bcaba8428a50edfc4d11992b8d844c3a0a3bec70d5b35`.
  Historical HTTP controls `20260912T164202Z-d0b839c4` and
  `20260914T151924Z-92347b6d` also match its config, input and sandbox-image hashes
  and both passed. If candidate repetition 3 passes, run one fresh `73cdc4b` HTTP
  control to obtain three control samples before the aggregate admission decision.
  Report the non-alternating historical order as a limitation.
- 2026-09-15: HTTP repetition 3 passed every stability and independent verification
  gate with 199 attempts, no continuation and CNY2.412583 actual off-peak cost. Its
  wall time was 1,426.359 seconds. Across the three exact `b260238` candidates, wall
  times were 1,121.686 / 1,348.047 / 1,426.359 seconds (median 1,348.047), attempts
  170 / 183 / 199 (median 183), strict rejects 24 / 33 / 31 (median 31), and all
  outputs passed. Existing exact-fingerprint controls are 1,184.610 and 1,347.259
  seconds, so no possible third control can make the candidate median at least 5%
  faster: the maximum possible control median is 1,347.259 seconds. Do not spend on
  the prepared third control and do not advance this candidate to MQTT.
- Root-cause decision: candidate calls and rejects decreased, but median output tokens
  rose and API time did not. Full-file `write_file` responses and two 16k-token
  truncated responses are material contributors. The next conservative candidate
  keeps all tools and validation, but adds a per-decision preference for existing
  `replace_text` on localized edits to already observed files. `write_file` remains
  available for new files and substantial rewrites; exact-match failure remains
  non-mutating. This is commit `9929bf2`; it passed 186 tests with one paid-live test
  skipped, Ruff and mypy. Re-run HTTP screening before any MQTT or fresh control.
- 2026-09-15: output-efficiency HTTP screen started as run
  `20260915T140512Z-e68d5761`, batch
  `runs/protocol-expansion/runtime-opt-http-edit-5242486d591f4e958a159c39234d9a40`,
  runtime `165c9d910bd50ac58611cf45f22293f8f470dba3f5d94add4f315ea70293a35a`.
  Treat this as an elimination sample; repeat only if all stability gates pass and
  full writes/output tokens/wall time improve.
- 2026-09-15: the first `9929bf2` output-efficiency screen passed all 10 tasks,
  27 claims, clean release/sanitizer builds, in-run/export acceptance and the
  independent rebuild/check. It used 185 attempts, 27 strict rejects, no
  continuation, 1,038.045 API seconds, 3,325,920 input and 161,870 output tokens,
  and CNY2.165906 actual off-peak cost (CNY4.331812 at the frozen peak-price
  normalization). Wall time was 1,273.893 seconds. Against the three-run `b260238`
  candidate median, wall time fell 5.50%, API time 12.63%, output tokens 7.95%,
  rejected-response API time 61.08% and normalized cost 10.22%; attempts rose
  1.09% and input tokens 4.38%. The model used `replace_text` 17 times versus the
  preceding median of 7, while `write_file` remained at 13 versus 12, so the prompt
  changed editing behavior but did not eliminate full rewrites.
- Risk diagnosis: all 17 `replace_text` actions succeeded. The seven failed actions
  were `run_command` results, so exact replacement did not introduce a demonstrated
  non-mutating failure in this sample. One manually composed acceptance command did
  not reap its background server and consumed the 120.2-second sandbox timeout;
  this accounts for the command-time increase from the preceding 76.995-second
  median to 200.048 seconds. Excluding that isolated timeout leaves about 79.8
  seconds. Keep the sample and repeat the identical frozen HTTP candidate twice;
  it is promising but one result does not confirm a repeatable time improvement.
- 2026-09-15: `9929bf2` HTTP repetition 2, run
  `20260915T143023Z-038b2c0f` in batch
  `runs/protocol-expansion/runtime-opt-http-edit-r2-88ed519932f5454fa3fa1f0377c3ef3a`,
  passed all generation and independent stability gates. It used 160 attempts,
  30 strict rejects, 10 sessions/no continuation, 860.892 API seconds, 2,479,293
  input and 142,930 output tokens, and CNY1.513857 actual off-peak cost. Wall time
  was 1,089.340 seconds. There were 46 reads, 10 `replace_text` actions and 11
  `write_file` actions. One replacement failed safely because its exact old text no
  longer matched; the model recovered without a new session. A second manually
  composed command using non-interactive shell job notation consumed the
  120.2-second timeout; without it, command time was about 73.8 seconds.
- Decision after two repetitions: both passed, with wall times 1,273.893 and
  1,089.340 seconds. Run the third identical HTTP sample. If it passes, the
  candidate wall median is at most 1,273.893 seconds, which is 5.45% below the
  maximum possible three-control median of 1,347.259 seconds. This can meet the
  declared HTTP time gate, but promotion still requires the third stability result
  and non-degrading normalized cost. The repeated 120-second model-command timeout
  remains a separately addressable general shell-guidance issue; do not mix a fix
  into the frozen three-run series.
- 2026-09-15: `9929bf2` HTTP repetition 3, run
  `20260915T145012Z-08a64d8b` in batch
  `runs/protocol-expansion/runtime-opt-http-edit-r3-ffa7735869074beb97a84b4d09fbb6f7`,
  passed all 10 tasks, 27 claims, clean release/sanitizer builds, every configured
  acceptance check and the separate independent rebuild/check. It used 236 calls,
  10 sessions/no continuation, 1,568.884 API seconds, 36 strict rejects, 5,109,606
  input and 257,097 output tokens, and CNY3.219037 actual off-peak cost. Wall time
  was 1,743.031 seconds. This slow result is retained without exclusion.
- Three-run HTTP candidate result: all three runs passed every stability gate. The
  candidate medians are 1,273.893 wall seconds, 185 LLM calls, 1,038.045 API
  seconds, 10 sessions/no continuation, 30 strict rejects (15.25%), 90.907 rejected
  API seconds, 158 executable actions, 3 failed actions, 3,325,920 input and
  161,870 output tokens, and CNY4.331812 at the frozen peak-price normalization.
  Median task build, integration acceptance, export build and export acceptance
  were 8.566 / 7.240 / 2.247 / 7.139 seconds. Full per-run, per-task and scenario
  evidence is recorded in
  `runs/protocol-expansion/runtime-opt-http-edit-aggregate.json`.
- The HTTP stability gate passes, but the preregistered performance gate remains
  inconclusive because only two exact-fingerprint controls exist and they vary
  materially. The candidate median is 5.45% faster and 17.20% cheaper than A2, but
  7.54% slower and 5.56% more expensive than A1. Relative to the arithmetic
  midpoint of those two controls, calls fell 14.55%, API time 3.82%, strict rejects
  41.75%, rejected API time 52.09%, input tokens 2.60% and normalized cost 7.20%;
  wall time rose 0.63% and output tokens 2.13%. The two-control midpoint is not a
  substitute for the required three-control median. The earlier upper-bound
  calculation established that the candidate *could* meet the threshold against a
  sufficiently slow third control; it did not establish that it must meet it.
- The candidate therefore demonstrates a repeatable reduction in invalid model
  actions and a lower median standardized model cost than the two-control midpoint,
  with 3/3 successful deliveries. It does not yet establish a repeatable end-to-end
  time reduction against the variable control. Agent command time remains noisy:
  two candidate runs each lost about 120 seconds to a manually composed
  background-process command. A future experiment may test general shell process
  guidance as a separate candidate, without weakening host checks.
- User directed the work to stop after the current HTTP experiment. No MQTT run was
  started, no fresh control was purchased, and no further paid experiment is
  running. Settled cost generated during this implementation iteration is
  CNY18.480956; the earlier MQTT campaign-root rejection remained zero-call and
  zero-cost. All completed and failed evidence remains preserved.

## Resume checklist

On continuation, record the current commit and config/runtime hashes, completed item,
targeted/full test results, paid experiment cost, confirmed findings, open risks and
the next action here. Preserve historical runs and the user's unrelated worktree
changes.
