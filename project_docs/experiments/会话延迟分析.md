# First continuation: time-cost analysis

User instruction (2026-09-12): finish the current experiment without interruption;
before another experiment, analyze its time cost and optimize configuration/mechanism
without sacrificing requirements, implementation quality or end-to-end acceptance.
Do not immediately launch the two repetitions when the first run finishes.

## Preliminary live evidence (not the final run totals)

Run: `20260912T112242Z-56f67d18`. Snapshot through response000707 (706 completed
responses; call278 is the preserved HTTP402 error). Run was still processing
requirements:009. Recompute at termination before deciding next candidate.
Evidence sources: immutable evidence/calls/*.request.json and *.response.json,
action results, and run.json. API durations use recorded elapsed_s, not a guess
from token counts. Command durations use actual duration_ms. Source reads and state
publication do not expose separate timers; do not report their time as zero.

| Segment | Responses | API seconds | Median seconds | Invalid actions | Invalid-action seconds |
|---|---:|---:|---:|---:|---:|
| Before recharge, Pro | 277 | 3509.5 | 5.11 | 24 | 654.5 |
| Continuation, Pro | 145 | 2264.2 | 11.90 | 27 | 426.7 |
| Continuation, Flash | 284 | 1168.2 | 2.24 | 118 | 646.1 |

Invalid means strict JSON parsing or the current action schema failed; no tool was
executed. Flash invalid rate118/284=41.5%; Pro continuation27/145=18.6%.
These are different tasks and retry contexts, not a controlled model comparison.
The 162 recorded command tools took111.827s; finish checks took13.099s. Docker/build
execution is not the dominant recorded cost. Most elapsed active execution is API
waiting; the recharge/development pause is separate, not model generation time.

Flash's first sessions on requirement batches004-008 reached40 decisions, with
13-21 invalid responses per session. Subsequent Pro sessions completed those tasks.
This proves format errors consume the same limited decision budget before model
escalation. It does not prove Flash would finish without Pro if errors disappeared.
No counterfactual speedup or quality guarantee can be inferred from this one run.

## Mechanism and next-candidate priority

The adapter sends schema/prompt instructions in ordinary text while omitting
response_format. Prompts already forbid XML/DSML/invoke tags and provide valid JSON
examples, yet real responses repeatedly contain those wrappers. More prompt warnings
alone have not eliminated the mismatch. Do not execute loose XML or scrape nested
objects from malformed output to count those calls as successful actions.

Official JSON Output documentation, directly checked2026-09-12:
https://api-docs.deepseek.com/guides/json_mode/
supports response_format={"type":"json_object"}; still requires JSON examples,
sufficient output allowance, and may return empty content. Add explicit configured
JSON output to the existing request/adapter path and preserve strict host schema
validation. Wire sizing, request evidence and budget reservation must include the
actual field. Test serialized requests and malformed/empty-response rejection.

Official thinking-mode documentation, checked the same day:
https://api-docs.deepseek.com/guides/thinking_mode/
says thinking defaults to high effort and ignores temperature. Current temperature0
is therefore not evidence of deterministic generation. Do not blindly disable or
lower reasoning on complex coding tasks to claim a speed gain. First remove the
evidenced output-contract waste while leaving reasoning, output/context capacity,
all110 requirements, task/session limits, checkpoints, builds and independent oracle
unchanged. Native tools would require a different conversation/reasoning contract;
do not introduce that larger rewrite unless the smaller documented JSON mode fails.

Current experiment remains on its existing production runtime. Implementation and
final measurements follow its terminal result. Any changed candidate needs fresh
empty-project verification; the mixed-version first run is not its stability proof.

## Final first-run result and timing baseline

The run completed2026-09-12T13:50:04UTC with23/23 tasks accepted, all110 primary
requirement declarations, actual CLI exit0, release/san build and minimum interaction
checks passed. A separately copied export also passed make clean/release/san and both
independent interaction variants, with server/client exit0 and no sanitizer report.
Evidence: runs/e2e/_acceptance/first-continuation-20260912/batch.json and its
independent-checks-1; the generated delivery remains unchanged in the original run.

Reproduction of timing audit (read-only, no API):
`PYTHONPATH=. .venv/bin/python runs/_refactor/audit_latency.py runs/e2e/20260912T112242Z-56f67d18`.
The audit script is preserved with local experiment artifacts, not a production import.

| Measurement | Final value |
|---|---:|
| Original creation to successful publication | 147.36 minutes |
| HTTP402-to-resume pause (evidence file timestamps) | 18.58 minutes |
| Wall time excluding that pause | 128.78 minutes |
| Completed API calls | 820 responses + one402 error |
| API elapsed time of completed responses | 125.35 minutes |
| Invalid action responses (no tool executed) | 205/820 =25.0% |
| API time spent producing invalid actions | 31.28 minutes |
| Flash invalid actions | 149/347 =42.9%,12.59 API minutes |
| Pro invalid actions, including pre-recharge | 56/473 =11.8%,18.69 API minutes |
| Measured command tools + finish checks | 155.991 seconds |
| Run conservative cost | USD21.78629862 |
| Campaign cumulative cost | USD70.80887574 / USD300 |

The remaining unknown call278 reservationUSD0.29382936 is included, not cleared.
Provider/cache/off-peak discounts are not deducted. This is accounted cost, not an
invoice. API latency includes provider processing/network and is not decomposable
into inference versus network from existing evidence. Models handled different
tasks/contexts, so medians are descriptive, not an A/B quality/speed comparison.

Decision before next experiment: enable documented JSON-object mode in the existing
configuration/request/context/provider path. Do not alter the tool language, parse
DSML permissively, lower reasoning/output limits, increase task budgets, shrink
context, skip requirements or remove builds. Keep Flash/Pro route unchanged so the
largest remaining measured defect can be evaluated without confounded changes.
The next fresh run must still satisfy all gates; compare its invalid-action rate,
API and wall time, task-session escalation, cost and independent acceptance. A
reduction in formatting waste is a hypothesis until real run evidence confirms it.

## Completed optimized repetitions

Candidate0d93e4cc8a54e602cefbad383a4ea4b2091d9208; batch
runs/e2e/_acceptance/7266ae1ab1d642e9be3424b141c05859/batch.json passed.
Both fresh projects completed23/23 tasks and110 primary declarations, real CLI0,
independent clean release/san builds and all mandatory interactions; both variants'
server/client exits0, no sanitizer findings. The baseline was independently rechecked
before launching these two, not reused as their source or response cache.

| Run | Wall minutes | API minutes | Calls/responses | Invalid actions | Invalid API minutes | Accounted USD |
|---|---:|---:|---:|---:|---:|---:|
| Baseline56f67d18 | 147.36 (128.78 excluding pause) | 125.35 | 821/820 | 205 | 31.28 | 21.7863 |
| Optimized4c036768 | 76.76 | 67.54 | 785/784 | 185 | 12.09 | 10.3946 |
| Optimized22021a32 | 75.38 | 66.06 | 723/723 | 180 | 18.28 | 6.9263 |

Relative to the baseline excluding the recharge/development pause, observed wall
time decreased40.4% and41.5%. Do not attribute the entire gain to JSON mode: unlike
the mixed baseline, both new runs used Flash from the initial ordinary tasks, and
generated different projects/diagnostics. This is end-to-end observation, not a
controlled per-mechanism A/B experiment or a universal success-rate guarantee.

Flash schema-invalid rates were183/686=26.7% and179/668=26.8%, versus149/347=42.9%
in the baseline; Pro rates2/98 and1/55, versus56/473. JSON-object mode was verified
in actual request evidence, but returned text still sometimes contained malformed
JSON, trailing DSML or whitespace. The setting therefore does not guarantee valid
actions in observed service behavior; strict host validation remains essential.
Never report this as zero errors or execute malformed responses permissively.

Measured command+finish times increased to493.795s and516.991s (from155.991s), as
the independently generated agents performed different diagnostics/tests. No build
or check was removed for speed. No output/context/decision/reasoning limit was cut.
There were no manual edits of generated projects between or during these runs.
The first optimized run's unresolved call reservation remains accounted, just as
the baseline402 reservation does. Total historical campaignUSD88.12981374 / USD300.
Specifically, optimized call102 had a10.013s ConnectTimeout; itsUSD0.03310470
reservation remains, the bounded client retried and the run completed normally.

Conclusion: the requested baseline-then-optimize-then-two-repetitions workflow passed
the configured build/minimum-interaction contract. Only the two optimized runs share
the final candidate; this is not three unchanged-candidate successes, full MQTT
conformance, proof of every requirement, or validation of arbitrary protocols.
