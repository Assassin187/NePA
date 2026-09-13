# NePA System Design 11.0

Status: approved architecture. Actual implementation/acceptance progress is in
`research/nepa-p0-p2-plan.md` and `research/p0-p2-progress.md` (current P0–P2),
`protocol_expansion.md` and `refactor_plan.md` (historical evidence). Design changes
are authorized by the user’s autonomous P0–P2 goal on 2026-09-13; no OpenSpec.

## 1. Success contract

Inputs are manually curated Spec 3.0 (including all original requirements), Target
1.0 and independent private Acceptance 1.0 assets. Initial implementation scope: Linux
x86_64, C99, server. MQTT is an acceptance input, never a production special case.

Success requires all tasks completed, clean release and ASan/UBSan builds, mandatory
protocol interactions passed, and independently buildable sources, binaries and
Report 5.0 published. JSON validity, stub compilation, process survival or an agent's
finish declaration are not success. Optional observations remain non-gating and
never establish full protocol conformance.

P0 preserves the first DeepSeek success and the completed MQTT/HTTP expansion as
legacy-readable-oracle feasibility evidence. Their original runs, USD/CNY costs,
reservations and exports are never migrated, overwritten or counted in the new
campaign. See research/p0-serial-baseline.md for measured time/cost and gaps.

P1 requires physical private-check separation and useful sanitized feedback before
new model proof runs. P2 uses only qwen3.7-plus-2026-05-26 and
qwen3.7-flash-2026-07-15, after official/account capability and price verification.
New runs/qwen-e2e campaign: CNY300 cumulative, each generation CNY20/four hours from
creation, including probes, failures, resumes, retries and unknown reservations.
Capability and public-tool phases initially cap at CNY5 each inside this campaign;
use phase-tagged RunStore reservations under the existing campaign lock. No response
cache, imported generated projects, manual generated-code fixes or provider fallback.

Freeze code, prompts, config/capabilities/prices, inputs, public/private checks,
harness, dependencies and image IDs before the first countable empty-project MQTT
run. After it succeeds, two more fresh MQTT runs on that candidate must pass before
a bounded three-run stability claim. Then one fresh HTTP subset run uses the same
runtime/model configuration with its separately frozen inputs. Independent empty
projects may run concurrently after the first success; each project remains serial.
A shared candidate change restarts the three-run MQTT cohort. Preserve failed runs.
Unrelated user documentation changes do not invalidate a scoped candidate manifest;
never remove those changes to satisfy a whole-worktree clean check.

The Linux deadline alarm covers provider/tools; unknown usage retains reservation,
and owned containers are cleaned on interruption. Record measured model wait, tools,
builds/checks, tokens, settled CNY, reservations, decisions, retries and route reasons
per task. Separate later independent export audits from generation wall time.
Unmeasured historical overhead remains a declared gap, not fabricated timing.

Config3.0 and Run7.0 reject legacy execution/resume; reproduce old runs with their
old runtime. Different schema/currency campaigns cannot be silently combined.
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
Config3.0 retains coder.action_format, which replaced coder.json_output with coder.action_format: json_object or
tool_calls. Native function parameter schemas come from AgentAction1.0. The same
strict local validation and executor apply to both formats. Native responses preserve
tool IDs, argument fragments and reasoning_content, including across subsequent calls.
Use tool_choice=auto without changing thinking mode. Zero/multiple/unknown/incomplete
calls execute no action; return matched error receipts where IDs exist. Never extract
XML, inner JSON, or switch interfaces automatically. Evict complete transactions;
count reasoning/tool definitions/results in actual-wire capacity and reservations.
The selected provider prepares the wire payload used by context sizing, reservation
and sending; no consumer hardcodes the OpenAI/DeepSeek payload. Config profiles
explicitly describe stream/JSON/tool capabilities, thinking fields, output/context
limits, temperature/stop restrictions, identity and usage accounting. Reject unsupported
combinations before I/O. Qwen uses enable_thinking, preserve_thinking when configured,
max_completion_tokens (including reasoning and body), and parallel_tool_calls=false.
Reserve the documented extra ten completion tokens. Keep strict local validation.
See research/qwen-capability-audit.md for dated official/account facts and tier rates.
Serialize the action schema once,
budget actual wire requests including corrections, and honor explicit coder config.
Use configured JSON-object output for providers that support it: the actual request
includes response_format={"type":"json_object"}. This constrains syntax only;
strict local action schema/claim checks and all host build/oracle gates remain.
Empty, malformed or schema-invalid responses execute no tool and consume their
normal decision/cost budget. Do not scrape DSML/XML or execute nested fragments.
The first complete run spent31.3 API minutes on205 invalid action responses;
see session_latency_analysis.md. Keep reasoning effort, full task scope and all
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
A failed build, private check, or workspace command promotes subsequent repair decisions to the configured
strong model within the same existing decision/session budget. Record the transition.
Nonzero/timed-out command results conservatively route as tool_failure_repair; this
covers compiler errors without guessing compiler wrappers from command strings.
Selection is based on task kind and observed failure/session exhaustion, not protocol names
or requirement prefixes. The selected model must drive the actual wire request,
context sizing and usage/reservation pricing; record route reasons in call context.
Maximum 40 decisions per
session, three sessions per task; retries carry real prior diagnostics and consume
the same run budgets. Full evidence is durable; file/log tools paginate outputs.
Retain task/target/index and recent transcript in context; only published sanitized evidence remains
readable by reference. Read current code rather than trust stale summaries.

Only generated project files are writable. Run inputs contain spec.json, target.json,
index.json and explicitly public development assets. Acceptance1.0 is snapshotted to
private/acceptance.json and private/assets, never an agent-visible root. A logical
evidence/ path maps only to host-published agent-evidence, not raw evidence/calls,
actions, checks or run state. File/list/search/hash refresh resolves every descendant
inside an allowed root; reject absolute paths, traversal and symlink escapes.

Coding/build containers mount only project, public inputs and optional published
safe evidence, with network disabled. They never mount /checks, the private tree,
raw evidence, source repository, host Docker socket or credentials. Hide Docker host
argv/mount paths from model-facing command results. Paths in errors are logical;
no host tracebacks. Publish one sanitized action/diagnostic view at the host boundary,
then use only that view for observations, history, resumed last_feedback, follow-ups,
export repair and evidence pagination. A raw valid evidence hash does not authorize
agent access. finish requests host validation; it cannot mark success itself.

## 4. Independent build and interaction checks

Target fixes argv build/run commands and output paths. The initial C99 target uses
make release/make san and separate build/release/protocol-server and
build/san/protocol-server outputs. Both require -std=c99 -Wall -Wextra -Werror;
san additionally uses ASan/UBSan. Agent-editable build files must honor this contract.
The Linux sandbox san target also requires -fno-pie -no-pie. A minimal instrumented
program failed 5/20 PIE startups versus 0/20 non-PIE startups in this environment.
This addresses observed toolchain address-layout failures without weakening checks.

Each task passes actual builds before acceptance. Final checks clean-build and verify
the exported copy. Verification uses separate server and checker containers: server
has network=none and a read-only project mount; checker joins only that server's
loopback network namespace and read-only private assets/trusted worker mounts.
No shared filesystem, PID/IPC namespace or private writable volume reaches the
server. Checker does not import/mount generated code. No host networking, published
ports or shared-/checks fallback. Drop unneeded capabilities and privilege escalation;
partition the existing CPU/memory envelope across the pair. Host owns both lifecycles,
records IDs before running checks, and cleans its recorded containers after
interruption/recovery. Keep complete per-check timeout/exit/sanitizer/shutdown checks.
Malformed/incomplete checker results, missing checks, early exit or forced stop fail.

Acceptance1.0 remains unchanged: isolation is a host execution property, recorded in
Run7/Report5 together with verifier/randomization versions. Categories come from the
trusted structured oracle result. CLI --acceptance remains host-only private final;
public development consists of visible project tests/builds and frozen public tool
fixtures. No new public-check CLI or unused public manifest contract is required.
Spec3, Target1, Plan6 and AgentAction1 remain unchanged. Preserve all MQTT20/HTTP12
assertions and mandatory boundary examples with an explicit migration map.

Each verification attempt gets a host-generated seed, recorded before I/O. Derive
independent port and per-case streams, never a seed from a public port/run ID. Private
oracles use the recorded seed and stable generator version for legal IDs, payloads,
fragment cuts, coalescing groups and bounded 2–4 exchanges where applicable. Preserve
all original cuts, malformed vectors, boundary values and keep-alive windows. Both
variants use distinct ports/streams; tests demonstrate distinct-seed variation and
same-seed input replay. Do not add three complete repetitions of each timing-sensitive
case merely as a planning default: one full suite per variant with the frozen varied
exchange policy satisfies this iteration. Record actual send/receive bytes, connection
IDs, chunks, half-close, timeouts and timestamps in host-only evidence. Replay means
same bytes/schedule, not identical OS/TCP timing. No seed or transcript is exported.

Private scripts emit a bounded structured semantic diagnostic separately from full
host logs. Host forwards only repair-diagnostic/1 fields: check ID, category, variant,
status, expected/observed protocol outcome/type/length/order, build/sanitizer/server
status and safe project-source diagnostics. No private filenames, argv, tracebacks,
seeds, raw vectors or raw host refs enter model context. Generated server logs can
echo private vectors; keep raw logs host-only and publish only safe bounded compiler/
sanitzer source diagnostics and classified observations, with explicit omission of
unclassifiable server output. Do not use a mere path/string replacement as isolation.
Useful safe diagnostics must support an actual controlled repair session.

Offline admission requires actual-container access-denial tests for every tool,
absolute and symlink paths, host history and generated-server filesystem reads;
correct full-suite exports pass, and wrong responses, sleep-only, single-ID/fixed-port,
early-exit and actual instrumented sanitizer fixtures fail. Check safe context in JSON
and native mode, context trimming/resume/export repair and final archive contents.

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

Completed capability/public-tool samples use study_complete, never production
success; their unexecuted Plan tasks remain unexecuted. Such study records cannot
be resumed as a production generation. Their costs and reservations remain charged.

One atomic Run 7.0 run.json is authoritative: input/config refs, immutable active
plan ref, task/session counters, accepted Git checkpoint, budgets, current operation
and terminal result. Independent immutable traces are evidence, not shadow state.
Single-controller lock; status is read-only.

Allocate never-reused call ID and worst-case budget reservation before provider I/O.
Persist response before settling actual usage. Lost/unknown calls retain reservation.
Record requested and returned model identity separately, never synthesize observation.
Missing/mismatched identity or invalid/missing usage yields no executable action and
retains reservation with raw response evidence. Optional missing cache detail is
explicitly all-miss; optional reasoning detail is unavailable, never invented.
Qwen prompt_tokens_details.cached_tokens and completion_tokens_details.reasoning_tokens
are normalized; reasoning is already part of completion_tokens and is not double billed.
Tier choice uses total input tokens. Qwen has flat-time tiered prices, not DeepSeek
peak/off-peak discounts. Reserve applicable worst-case tiers/all misses plus output;
known liability above reservation is recorded and blocks further calls, never clipped.
Retry only bounded transport/429/5xx classes; no request/identity/usage error fallback.
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
Contracts: Spec3.0, Target1.0, Acceptance1.0, AgentAction1.0, Plan6.0, Run7.0,
Config3.0 and Report5.0. Old configuration/runtime snapshots require their old code.

Report5.0 retains every primary claim and joins Acceptance check IDs/req_ids to the
current final-export evidence, variant, outcome and reference. Status is
scenarios_passed, failed, incomplete or unverified. Optional observations do not
establish verification; absent checks are explicit gaps. Missing variants/checks,
supervisor failure and interrupted checks cannot pass. Prior attempts never fill
gaps in final evidence. Even scenarios_passed is not full semantic proof.
Report5 separates public development builds/checks from private final checks, labels
legacy_readable versus private_isolated evidence, and preserves the original claim.
Host report includes private evidence refs. The distributable projection includes
aggregate private suite/verifier hashes, check/category/variant/status and safe
diagnostics, never private filenames, vectors, conversations or readable raw refs.

Export sources, build files, README, both executables and a public manifest/report.
Do not package private inputs/assets, seeds, transcripts, raw calls or the run root. Clean-rebuild and
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
