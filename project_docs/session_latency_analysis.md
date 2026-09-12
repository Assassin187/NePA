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
