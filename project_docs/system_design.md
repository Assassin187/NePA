# NePA System Design 10.0

Status: approved architecture. Actual implementation/acceptance progress is in
`engineering/refactor_plan.md`. Replaces 8.0.3 and the separate S4–S9 pipeline design.

## 1. Success contract

Inputs are manually curated Spec 3.0 (including all original requirements), Target
1.0 and independent Acceptance 1.0 assets. Initial implementation scope: Linux
x86_64, C99, server. MQTT is an acceptance input, never a production special case.

Success requires all tasks completed, clean release and ASan/UBSan builds, mandatory
protocol interactions passed, and independently buildable sources, binaries and
Report 4.0 published. JSON validity, stub compilation, process survival or an agent's
finish declaration are not success. Optional observations remain non-gating and
never establish full protocol conformance.

Formal acceptance: three consecutive independent empty-project runs with identical
NePA commit, prompts, input/configuration and image, real API calls, no response
cache, imported solution or manual generated-code edits. Changing these inputs
restarts the batch. Retain debug/failed runs. Approved campaign budget: USD300
including debug and failures; each run USD100 and four hours from creation, never
reset on resume. Unknown usage keeps its pre-call reservation.
The user raised the per-run ceiling from USD20 to USD100 on 2026-09-12;
then raised the cumulative campaign ceiling to USD300. Both include prior costs;
this is not a reset or an additional USD300 allocation.
The Linux CLI installs a run deadline alarm, including provider/tool calls; deadline
interruption retains unknown-call reservations and cleans active tool containers.
Scheduling (latest user instruction): first complete one real end-to-end run and
its independent export checks. Only after it passes, launch two additional empty
projects with that same frozen candidate; these two may execute concurrently.
A first-run failure does not launch stability repetitions. All three must pass.
The user subsequently authorized continuing the payment-interrupted first run with
a changed Flash/Pro model configuration. That development run may have multiple
recorded code/configuration versions; it is not a fixed-candidate stability sample.
After that first successful run, the user requested time-cost analysis and optimization
before another experiment. Preserve its independent baseline evidence; reverify it
against its own recorded runtime/configuration, then run two fresh projects with the
same optimized candidate. Reports distinguish one earlier development baseline plus
two optimized-candidate repetitions, never three unchanged-candidate runs.

The next iteration explicitly supersedes that scheduling: expand MQTT checks,
evaluate action interfaces, freeze the candidate, then launch the new MQTT and HTTP
fixed-length-subset generations concurrently (latest user authorization). Each needs
one success and independent export rebuild/checks; a failure does not cancel or gate
the other protocol. Each process uses its own campaign root and run limits.
This is feasibility evidence, not stability or full conformance. Changes to shared
generation code/prompts require revalidation of already-passed protocols on the final
candidate. Preserve all historical deliveries and reports; audit copies only.
The MQTT campaign retains its USD300 cumulative limit and all historical costs
(USD88.12981374 at authorization). The action comparison has a USD10 sublimit charged
to that campaign. HTTP has a separately authorized USD300 campaign, in a separate
runs root. Both retain USD100/four hours per run, including failures and reservations.

The latest user authorization replaces the earlier USD budget policy for this round:
use two NEW CNY campaigns, runs/mqtt-e2e and runs/http-e2e, each capped at CNY300.
Old runs, costs and unknown reservations remain intact in their historical root and
are explicitly excluded from these new limits. Each generation is capped at CNY20
and four hours. The interface study has a fixed CNY10 total sublimit within the new
MQTT campaign, including failures/reservations; retries do not replenish it.
Config2.0 uses CNY prices/limits and Run6.0 records CNY costs; reject attempts to mix
legacy USD runs into a new CNY campaign or reinterpret old USD amounts as CNY.
Use the official domestic DeepSeek price snapshot (2026-09-13): Flash peak cache-hit /
cache-miss input / output = CNY0.04/2/8 per million tokens, Pro = CNY0.30/9/27.
Off-peak is half price. Peak is Asia/Shanghai Monday-Friday [09:00,12:00) and
[14:00,18:00); all other times are off-peak. Record UTC request start and selected
period/rates, and provider cache-hit/miss usage. Request-start time is the local
estimation convention because the public schedule does not specify boundary-call
settlement; reports remain estimates, not invoices. Reserve at peak/cache-miss rates
before calls, settle from actual token usage at the recorded start period, and retain
unknown-call reservations. Missing cache usage is explicitly estimated as all misses.

## 2. Deterministic planning

Freeze original input bytes. Validate schemas, unique IDs and structural references,
not natural-language semantics. Requirements have one original source: Spec
requirements[]. Keep input array order. Generate a serial Plan 6.0:

1. bootstrap: real project, build/start entrypoints, initial interfaces and README.
   This task establishes a listening process and clean shutdown, not all message
   or behavior implementation; the complete pipeline overview is supplied up front.
2. shared-wire: transport and builtin/custom wire types.
3. One message:<id> task per message. Decode for receiver target roles, encode for
   sender roles, both if applicable. Explicitly review scope for irrelevant messages.
4. requirements:<n> batches of at most 12 original requirements, including DEFINITION.
5. final-integration: integrate and run clean builds and mandatory interactions.

Current gold: 8 types, 10 messages, 110 requirements, 23 tasks. Every requirement has
exactly one primary batch. Codec/type req references provide context, not completion.
Resolve type closure through fields, encoding members/item_type/length_type/base_type.
Keep constraints, bits, presence, derived values and source references intact.
Bootstrap and shared-wire receive the complete requirement texts referenced by
transport/types, just as message tasks do; those facts are support, not primary claims.
All tasks may read the full input/current project. Batches are not filesystem or
semantic boundaries. Never infer behavior ownership from protocol names or ID prefixes.

Each batch reports exactly its requirements: implemented/already_present with code
locations and explanations, or not_applicable with original requirement and target
scope justification. Missing/duplicate/out-of-batch claims fail validation.
Unsupported/not_implemented/deferred cannot complete a task; absence from minimum
tests does not justify not_applicable. Claims are not independently verified behavior.

A field constraint is not a universal error policy. Preserve sufficient parsed
information for behavior-specific rejection responses rather than silently dropping
every constant mismatch. Ordinary implementation and actual tests determine this.

## 3. Tool-using coding session

One writer at a time. Sources, shared headers, main and build files are editable.
Code and compiler are interface authority: no frozen ABI, file-owner leases, module
vocabulary or shadow signature JSON. Fix affected callers when changing interfaces.

Reuse current provider adapters and one action executor:
list_files/read_file/search/write_file/replace_text/run_command/finish/request_followup.
Carry executed actions as actual assistant messages and tool results as subsequent
user messages in JSON mode, or matched tool messages in native mode. Context assembly
uses complete action/result transactions; session transitions are request metadata,
never additional unpaired messages. Retain complete calls/actions in evidence.
Keep task facts, deduplicated current file observations and the latest diagnostic
in the model request. A successful read observation contains its exact selected
content, original file SHA256 and evidence reference. Before each request, compare
observed file hashes with the actual allowed workspace/input files; invalidate
changed/deleted/escaped paths. This also handles edits through arbitrary commands
without discarding unchanged source observations after a harmless build.
Read results appear once in the observation set; transcript receipts refer to them
and durable evidence. Only older whole transactions may be evicted for space, not
the current working observations or the latest action/result. If those required
parts exceed the configured actual-wire limit, fail with an explicit capacity
diagnostic before another paid call instead of silently entering a reread loop.
This is disposable model context, not a second authoritative project/run state.
Retries preserve validated observations and latest diagnostics without breaking
transaction pairing; resumed processes re-read actual files as necessary.
Config2.0 replaces coder.json_output with coder.action_format: json_object or
tool_calls. Native function parameter schemas come from AgentAction1.0. The same
strict local validation and executor apply to both formats. Native responses preserve
tool IDs, argument fragments and reasoning_content, including across subsequent calls.
Use tool_choice=auto without changing thinking mode. Zero/multiple/unknown/incomplete
calls execute no action; return matched error receipts where IDs exist. Never extract
XML, inner JSON, or switch interfaces automatically. Evict complete transactions;
count reasoning/tool definitions/results in actual-wire capacity and reservations.
Serialize the action schema once,
budget actual wire requests including corrections, and honor explicit coder config.
Use configured JSON-object output for providers that support it: the actual request
includes response_format={"type":"json_object"}. This constrains syntax only;
strict local action schema/claim checks and all host build/oracle gates remain.
Empty, malformed or schema-invalid responses execute no tool and consume their
normal decision/cost budget. Do not scrape DSML/XML or execute nested fragments.
The first complete run spent31.3 API minutes on205 invalid action responses;
see experiments/session_latency_analysis.md. Keep reasoning effort, full task scope and all
existing resource/output/context limits unchanged while evaluating this correction.
Include concrete JSON action examples, reject XML pseudo-tool calls with corrective
feedback, and show the remaining decision budget. Search accepts regular expressions.

Compare identical 24 Flash/8 Pro format samples per mode (MQTT and HTTP contexts),
plus four real short tool sessions per mode covering file operations, compiler repair
and finish. Freeze samples and thresholds before API calls. Native promotion requires
Flash invalid rate <=5% and >=50% relative reduction, no increase in Pro invalid count,
all four short sessions passing, and mean elapsed time/cost per valid action no more
than 110% of JSON control. Incomplete/budget-limited studies establish no improvement.
Strict Beta is a capability probe only, never a reason to weaken the local schema.

Default: configured deepseek/deepseek-v4-pro, temperature 0, max output 16000. This is
a starting configuration, not a proven model ranking. Restore the actual-wire
window to 180000 bytes after the evidenced 60000-byte source-eviction regression;
the working-set invariants above, not the larger number alone, fix the mechanism.
Optional coder.fast_model uses the same configured provider and requires an explicit
price. Bootstrap, message and requirement tasks use it for their first session;
shared-wire, integration, follow-up and repair/retry sessions use coder.model (Pro).
Selection is based on task kind and observed session exhaustion, not protocol names
or requirement prefixes. The selected model must drive the actual wire request,
context sizing and usage/reservation pricing; record route reasons in call context.
Maximum 40 decisions per
session, three sessions per task; retries carry real prior diagnostics and consume
the same run budgets. Full evidence is durable; file/log tools paginate outputs.
Retain task/target/index and recent transcript in context; older evidence remains
readable by reference. Read current code rather than trust stale summaries.

Only generated project files are writable. Input and oracle are available as
read-only inputs/checks paths and /inputs and /checks container mounts; runtime state
and Git metadata are not mounted. File pagination offsets are characters, not array
indices; use JSON pointers for specific facts. Reject traversal and symbolic-link escapes.
Commands run in a network-disabled, resource-limited container without host secrets,
NePA source, old answer fixtures, cached answers or installed protocol servers.
Host tools never execute generated commands outside that sandbox.
finish requests host checks; it cannot mark success itself.

## 4. Independent build and interaction checks

Target fixes argv build/run commands and output paths. The initial C99 target uses
make release/make san and separate build/release/protocol-server and
build/san/protocol-server outputs. Both require -std=c99 -Wall -Wextra -Werror;
san additionally uses ASan/UBSan. Agent-editable build files must honor this contract.
The Linux sandbox san target also requires -fno-pie -no-pie. A minimal instrumented
program failed 5/20 PIE startups versus 0/20 non-PIE startups in this environment.
This addresses observed toolchain address-layout failures without weakening checks.

Each task passes actual builds and output checks before acceptance. Final checks
clean-build the exported project. A generic supervisor starts its binary and the
trusted client in one network-disabled container on loopback with a dynamic port.
Oracle code is read-only, performs readiness/interaction assertions and returns real
results. Capture actual server/client exit codes, timeouts and logs; stop the server
and reject sanitizer failures or unexpected termination. Never normalize failure.

MQTT-specific checks exist only in sample acceptance assets: valid CONNECT/CONNACK,
PINGREQ/PINGRESP, unsupported-level CONNACK 0x01 followed by EOF, then a subsequent
valid connection. Extend independent cases to publish/subscribe/unsubscribe, client
isolation and CleanSession=1 reset, QoS0 delivery and subscription QoS downgrade,
fragmentation/coalescing, framing boundaries, malformed packets and keep-alive.
The 110 original requirements stay unchanged; QoS1/2 delivery and persistent-session
expansion are excluded. Map each check only to assertions actually made, never infer
whole-requirement proof from a normal example or a client-side action.

HTTP input is a manually curated Spec3.0, using byte fields and requirement text for
request lines, headers and bodies, with no HTTP-specific compiler/runtime branches.
RFC9110/9112 define the selected semantics; explicitly labeled application rules are
GET / -> 200 with "nepa\n", HEAD / -> matching metadata without body, POST /echo ->
200 with identical bytes, unknown routes ->404 and unimplemented methods ->501.
Check Host, case-insensitive header names, Content-Length, binary/empty bodies,
persistence, ordered pipelining, fragmentation, Connection: close, invalid/conflicting
lengths and truncation. Transfer-Encoding is rejected with close as a subset policy.
Chunked, TLS, proxies, upgrade, caching and HTTP2 are excluded. Because RFC9112
requires chunked decoding, never describe this subset as a conforming full HTTP1.1
receiver. Protocol knowledge lives only in input/oracle assets.
Vary identifiers and inputs; run both variants. Oracle unit-test doubles do not count
as real generation. Remaining protocol behavior is explicitly unverified.

## 5. State, repair and recovery

One atomic Run 5.0 run.json is authoritative: input/config refs, immutable active
plan ref, task/session counters, accepted Git checkpoint, budgets, current operation
and terminal result. Independent immutable traces are evidence, not shadow state.
Single-controller lock; status is read-only.

Allocate never-reused call ID and worst-case budget reservation before provider I/O.
Persist response before settling actual usage. Lost/unknown calls retain reservation.
Persist task result/check evidence, create checkpoint, then atomically accept task
and checkpoint. Orphan checkpoints cannot authorize completion. On interruption,
preserve the incomplete tree and create a fresh working copy from accepted code.
Unexpected manual changes must be preserved, not silently overwritten.
An explicitly requested development resume can change active configuration/runtime
using resume --config PATH --accept-runtime-change --change-reason TEXT. Before the
change, validate unchanged input/plan/project evidence and preserve the previous
state/report/config/runtime in immutable evidence; publish the new active values
and a history reference atomically. Include these changes in reports. This never
resets cost, call numbers, task/session counters, original creation time, accepted
checkpoint or pending-call reservations. Ordinary resume still rejects runtime
drift; legacy Run4 and completed deliveries cannot be migrated through this path.

Repair in the current task or append a small follow-up with problem, requirement
and diagnostic refs. Maximum three follow-ups per run, inserted before final
verification in a new immutable plan version. They cannot erase tasks, change
inputs/oracles or evade an exhausted task budget. Final check failure allows up to
three repair sessions, charged to the original run. No CAP/F1–F3, leases, migration
classes, preservation ratios or rehearsal DSL.

## 6. Interfaces and publication

CLI: nepa run --spec PATH --target PATH --acceptance PATH --config PATH --runs-root DIR.
Preserve resume RUN_ID and status RUN_ID with --runs-root. Add lint acceptance;
retire --test-bundle, --until and ledger-specific lint. Unsupported old runs/config
fail explicitly; reproduce them with the baseline, never implicitly convert.

Interfaces: compile_plan → ExecutionPlan; CodingSession.run → TaskResult;
BuildRunner.run → BuildResult; VerificationRunner.run → VerificationResult;
RunStore.accept → RunState; Orchestrator.run/resume → FinalRunResult.
Contracts: Spec3.0, Target1.0, Acceptance1.0, AgentAction1.0, Plan6.0, Run5.0,
Config2.0 and Report4.0. Old configuration/runtime snapshots require their old code.

Report4.0 retains every primary claim and joins Acceptance check IDs/req_ids to the
current final-export evidence, variant, outcome and reference. Status is
scenarios_passed, failed, incomplete or unverified. Optional observations do not
establish verification; absent checks are explicit gaps. Missing variants/checks,
supervisor failure and interrupted checks cannot pass. Prior attempts never fill
gaps in final evidence. Even scenarios_passed is not full semantic proof.

Export sources, build files, README, both executables and manifest. Clean-rebuild and
verify the exported copy independently of the working directory. Report input/config/
code hashes, actual models/calls, claims versus verified behavior, all check results,
costs, artifact hashes, commands and limitations. Initialized failures also report.

Generation/resume returns 0 only after all tasks, checks, export and report succeed.
Other codes: 1 internal error, 2 execution/verification failure, 3 budget exhaustion,
20 invalid input/config/unsupported version, 130 interruption. Status exit 0 means
status was read, not generation passed; JSON exposes the actual outcome.

## 7. Migration and scope

Original worktree/user changes and raw runs remain untouched. Baseline Git preserves
old code and tracked fixtures. Use isolated stage commits and recoverable attempt
snapshots. Retire obsolete files only after consumer checks and replacement coverage
are recorded. No automatic old-run migration or long-lived dual runtime.

Retain provider/SSE, redaction, budget, schema/reference, path, atomic-publication,
lock and sandbox tests. Add actual compiler repair, CLI wiring, checkpoint windows,
independent oracle, wheel-install and paid opt-in live tests. CI builds sandbox
before dependent tests and type-checks all retained production modules.

Research v2 stays unchanged. Adopt fact indexing, deterministic small tasks and real
feedback. Defer OPIR, macro DSL, solvers, automatic extraction/test generation,
additional languages and broad protocol validation. Historical calibration is not
production admission. Evidence may justify within-scope design changes if reason,
impact and replacement tests are recorded. Do not weaken acceptance, increase
budgets, overwrite user work, push, merge or deploy without separate authorization.
