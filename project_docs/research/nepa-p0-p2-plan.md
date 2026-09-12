# NePA P0–P2 implementation plan and completion evidence

Date: 2026-09-13. Status: implementable planning proposal; implementation and live
acceptance remain separate gates. This planning task writes only this file.

Requirements: [next-stage requirements](nepa-next-stage-requirements.md), sections
4–6 and 10. Current authority: [System Design 10.0](../system_design.md). Historical
results: [protocol expansion](../protocol_expansion.md). No OpenSpec workflow.

## Accepted implementation decisions (Astra Ultra review, 2026-09-13)

Design11 is now the implementation authority; the detailed proposal below is retained
as planning history where these explicit simplifications supersede it:

- Keep Acceptance1.0. Isolation/version metadata belongs to Run7/Report5; categories
  come from trusted structured observations. No public-check CLI is needed.
- Four public provider-neutral tool sessions per model; compile repair includes
  cross-file consistency. Add one genuine private-diagnostic repair loop with paid
  model actions; mocks or manual fixture repair cannot substitute.
- Preserve every existing assertion/boundary, then add bounded random exchanges;
  omit the proposed blanket three extra full repetitions per timing-sensitive case.
- Project safe structured observations and source diagnostics. Omit unclassifiable
  raw server logs instead of assuming string replacement sanitizes encodings.
- Official Qwen facts/account IDs have been verified: qwen-capability-audit.md.
  P0 measured inventory exists; fixture/suite freeze and complete profiling closure
  remain outstanding. Actual model/tool contracts are not yet verified.
- Freeze a scoped candidate manifest, preserving unrelated user document changes.
- High implementation owners have disjoint files and agree public diagnostics,
  private asset roots and exact provider payload APIs before editing shared callers.

## 1. Scope, authorization, and baseline

The latest user instruction authorizes reasonable P0–P2 design and implementation
decisions without another approval round. It adopts a separate Qwen CNY300 campaign,
CNY20/four hours per generation, with probes, failures, resumes, and unknown-call
reservations charged. This supersedes the requirements draft's pending-budget
confirmation. This planning assignment explicitly excludes implementation, paid
calls, live/API research, and editing the authoritative design. The implementation
owner must first publish the design changes listed below under that authorization;
this plan does not silently amend System Design 10.0.

Preserve the eventual RFC → approved Spec → Code goal. Keep Spec 3.0, original
source references, requirements and coverage gaps intact. Private acceptance remains
independently maintained and never becomes a generated answer or a downstream test
generator. P3 scheduling, P4 weaker models, and P5 extraction are outside this plan.
Production task execution remains serial with one writer and accepted checkpoints.

User document organization under `protocol_document/`, existing deletions/untracked
files, other agents' edits, historical runs, and delivery archives must remain
untouched. No push, merge, deployment, historical cleanup, or in-place migration.

Confirmed repository facts from this planning inspection:

| Area | Current behavior and implication |
|---|---|
| Recorded baseline | HEAD `de5fb5e` publishes MQTT/HTTP successes produced by candidate `10cb987178c329cefb909c8651a2daa9e9c548b5`; runtime package SHA256 `cf4b589a6dadd872890bcaba8428a50edfc4d11992b8d844c3a0a3bec70d5b35`. These are readable-oracle feasibility samples, not private-acceptance or stability evidence. |
| Coverage | MQTT retains 110 requirements, 20 mandatory scenarios in each release/san variant, with 51 mapped requirements and 59 gaps. HTTP retains 27 requirements, 12 mandatory scenarios per variant, with 26 mapped and one scope-definition gap. Preserve assertions as well as these counts. |
| Storage/tools | `RunStore.initialize` copies acceptance and scripts into `inputs/acceptance.json` and `inputs/checks`. `WorkspaceTools` exposes `inputs` and the entire `evidence` tree; commands mount `/inputs` and `/checks`. |
| Verification | `VerificationRunner` mounts scripts, verification payloads, and the tools directory into the same container in which `verification_worker.supervise` starts generated code. Read-only `/checks` can still be read and printed by that code. |
| Feedback | `CodingSession.run` feeds raw verification/action results and `complete_result_ref` into context. `Orchestrator` supplies raw `final_checks` to export-repair sessions. Context observations and resumed `last_feedback` can retain them. |
| Provider assumptions | `CodingContext` and `LLMClient` calculate wire size using `OpenAICompatibleProvider._payload`. The adapters currently stream chat-completions; there is no explicit per-model capability profile. |
| Identity/usage | SSE parsing rejects malformed usage and changing stream identity, but substitutes the requested model when returned identity is absent. `LLMClient` does not enforce requested-versus-returned identity before settlement/action execution. |
| Pricing/accounting | `ModelPrice` and `price_usage` apply the DeepSeek peak/off-peak convention universally. `RunStore.reserve_call`, `.campaign.lock`, settlement, retained reservations, run deadlines and checkpoints are already the correct foundation. |
| Fixtures | The prior format study used historical protocol context and server-oriented prompts for small terminating C fixtures; compiler-repair fixtures expanded into servers. Preserve those results, but create clean public tool-fixture inputs for subsequent comparisons. |

The main agent owns the detailed P0 evidence inventory and official/account Qwen
fact verification. This document specifies their required handoffs; it does not
repeat those audits or claim independently to have reverified historical artifacts.
During plan review, the main agent published [the Qwen capability audit](qwen-capability-audit.md).
Section 7 incorporates its local findings with attribution; real inference identity,
usage and action behavior still require metered probes.

## 2. Versions and frozen-candidate identity

Apply the following version changes coherently, including schemas, examples,
runtime validation, CLI consumers, packaging and live harnesses. Do not create a
second orchestrator, RunStore, action loop, accounting ledger, or model-specific
protocol planner.

| Contract | Planned version | Change |
|---|---|---|
| System Design | 11.0 | Replace readable oracle/shared verification filesystem, unrestricted historical evidence reads, uniform provider payload/pricing assumptions, and this campaign's scheduling/budget text. Preserve other success and recovery invariants. |
| Spec / Target / Plan / AgentAction | 3.0 / 1.0 / 6.0 / 1.0 | Unchanged. Target still describes C99/server; claims, action validation, deterministic serial planning and task budgets remain. |
| Acceptance | 2.0 | Require `visibility: private_final` or `public_development`, a `validator_version`, `randomization_version`, and per-check `category`; preserve asset list, check IDs, req_ids, required flags, timeouts and argv semantics. No seed values in a public manifest. Public fixtures use the same asset/check contract where applicable. |
| Run | 7.0 | Public input refs separated from private acceptance refs; sanitized evidence publication refs; verification operation identity/lifecycle; timing, experiment-purpose/batch tags and model/usage provenance. One authoritative atomic `run.json`. |
| Report | 5.0 | Separate public development and private final results; exposure classification; host evidence versus distributable projection; candidate fingerprint, profiling and provider-specific pricing basis. |
| Config | 3.0 | Auditable per-model capabilities and verified CNY pricing policies, explicit thinking/action mode, existing resource/budget settings. No Qwen numeric price until verified. |
| Diagnostic payload | `repair-diagnostic/1` | Versioned result object validated at the existing verifier/session boundary, not a new service or general-purpose diagnostic framework. |
| Frozen experiments | `candidate/1`, `profile/1`, `public-tools/1`, `random-inputs/1` | Immutable JSON evidence records inside existing experiment/run evidence paths; these are evidence formats, not new authoritative runtime state. |

New runtime rejects Run6.0/Config2.0 for execution/resume and Acceptance1.0 for new
private runs with an explicit baseline-runtime message. Historical reports remain
readable as files. New P1 validation records use a fresh root; Qwen uses its own new
root. Keep historical CNY/USD totals and reservations in their original roots;
never reinterpret old records as Run7 or silently mix them in a campaign scanner.

A candidate record freezes: generation Git commit, package and prompt hashes,
serialized action schemas, source/runtime file hashes, resolved config and capability
profiles, Spec/Target/index/initial Plan bytes, public fixture/check assets, private
manifest/scripts/assertion mapping, randomization policy, verifier/diagnostic version,
experiment harness and dependency lock hashes, and immutable Docker image IDs.
Record host/kernel/compiler versions and resource limits as comparison conditions.
Fingerprint private assets in host-only records. Public reports use the aggregate
private-suite digest and verifier version without script filenames or internal paths.

Random seeds, dynamic ports, actual request IDs, timestamps, billing periods and run
IDs vary by design and belong to the run/attempt record, not the candidate hash.
This exception is permitted only for the frozen randomization policy; changing its
distribution, case count, assertions or time limits creates another candidate.
Different MQTT/HTTP Spec and suite hashes are declared protocol inputs under one
frozen runtime/config/image candidate, not falsely described as identical inputs.

Freeze before a countable first MQTT run, then seal that candidate after its success.
Any subsequent runtime, prompt, routing, capability, input, image, price-policy or
oracle change starts a new three-run MQTT cohort. An earlier success on another
candidate is development feasibility evidence only. Keep every failed attempt and
its cost. A documentation-only commit must be explicitly separated from the recorded
generation commit; it cannot silently relabel an experiment.

Because the shared worktree contains unrelated user changes, freeze a scoped package
and experiment-input manifest or use an isolated checkout made from the intended
commit. Do not enforce a whole-worktree cleanliness condition by deleting, committing,
stashing or reverting unrelated changes. A sample with unrecorded candidate-file drift
is invalid; unrelated document organization does not invalidate a scoped manifest.

## 3. P0: evidence handoff and baseline profiling

Consume the main agent's inventory for the original DeepSeek development success
and both fresh expanded-oracle runs. Required entries are original path, run ID,
input/model/config/runtime versions, task/call/claim records, build and protocol
results, export/independent rebuild refs, costs including retained reservations,
scope, and artifact digests. Record mixed development configurations as such.

The expansion document identifies fresh MQTT
`20260912T164202Z-e4b27709` and HTTP `20260912T164202Z-d0b839c4` as passed under the
readable-oracle policy. The historical success is not promoted to a frozen or private
sample. Publish the P0 inventory and derived profiling in new experiment artifacts,
referencing originals; never rewrite their `run.json`, reports or archives.

Define one profiling vocabulary for P0 and new P1/P2 runs:

- Run creation → terminal report publication wall time; generation, final export
  verification and later independent audit time separately. Interruption/offline
  resume gaps are reported separately from active execution time.
- Per task and session: provider attempt wait, retry backoff, context preparation,
  workspace commands, build, private verification, checkpoint/export/publication
  time, total decisions, invalid decisions, reads/re-reads, retry/repair counts.
- Per call: task/session/decision/attempt ID, route reason, requested/observed model,
  start/end and duration, token categories, settled CNY, unknown reservation,
  failure class, and evidence refs. Attribute transport retries separately from
  invalid-action decisions and new coding/repair sessions.
- Per run: final quality/coverage, public versus private results, wall time, token
  totals, settled cost plus outstanding reservations, failures, and the largest
  task/call/retry contributors by time and CNY. Do not report only aggregate cost.

Derive historical metrics from existing request/response/error/action evidence and
`ExecResult.duration_ms`. Compute nonoverlapping categories; a build nested in a
finish action is not charged twice. Publish known duration, unattributed residual,
missing intervals and the method used. Do not use filesystem mtime to invent precise
session or wall-clock timings. If records cannot identify an important component,
mark it unavailable and add start/end spans to the existing call/action/checkpoint
records for future runs. Baseline completion requires the major observed bottlenecks
to be attributable, even if explicitly bounded residual overhead remains.

Freeze the new public fixture suite and migrated private MQTT/HTTP suite contracts
before comparing providers. The P0-era historical numbers remain an exposure-labeled
baseline; they do not retroactively become private results. A copied historical
export passing P1 is verifier-regression evidence, not a fresh generation success.

## 4. P1: storage and access contract

Use an explicit public tree inside each new run and a physically separate host-only
private tree. All paths below are proposed workspace-local destinations; no storage
outside `/home/ljf/NePA` is needed.

```text
runs/qwen-e2e/                          # one Qwen campaign and .campaign.lock
  <run-id>/
    run.json                           # host state; never a coder tool root
    inputs/
      spec.json, target.json, index.json
      public-checks/                   # optional public manifest/assets only
    project/                           # sole writable coder project
    agent-evidence/                     # host-published safe action/diagnostic views
    private/
      acceptance.json, assets/         # immutable private Acceptance2 snapshot
    evidence/
      calls/, actions/                 # complete host records, secret redacted
      verification/<attempt-id>/       # payloads, seed, transcript, logs, raw result
      profiling/, exports/
    checkpoints.git, plans/, interrupted/, export-attempts/
    delivery/                          # project artifacts, no private inputs/logs
    report.json                        # host Report5 with host-only references
```

Repository `gold_file/mqtt` and `gold_file/http` acceptance assets remain trusted
host source assets, with their versioned content edited by the acceptance owner.
The repository, historical archives, other runs and NePA source are never agent
mounts. New private-only run snapshots live under `private/`, not under any prefix
accepted by `WorkspaceTools`. No requirement to delete or hide historical Git objects
from human maintainers is implied; isolation concerns the coder and generated program.

Keep `--acceptance PATH` as the host-only final manifest. Add an optional
`--public-checks PATH` for an explicitly `public_development` manifest, snapshotting
only that manifest's declared assets. Reject a private manifest supplied as public.
Ordinary builds are public checks even when no public protocol script is configured.
Public scripts can be invoked through existing command tools; label their action
results as public development evidence, without adding another task-acceptance stage.
RunStore initialization freezes all raw bytes and validates resolved source paths;
record public refs and private refs separately. `store.inputs()` may keep returning
the host's Spec/Target/private manifest triple for existing consumers, but session
context must not serialize that triple or the private refs.

`WorkspaceTools` keeps its existing actions. Its `inputs/` maps solely to the public
tree and its logical `evidence/` maps solely to `agent-evidence/`. There is no tool
mapping to the raw `evidence/` tree. Publish normal action receipts, paginated build
diagnostics and sanitized verification diagnostics there using existing immutable
write/hash conventions; preserve their corresponding raw records only on the host.
Use one explicit host publication method at the action-result boundary, not scattered
redaction in every tool or a denylist over the whole run directory.

The following access rules apply consistently to list/read/search/hash refresh,
write/replace, commands, context construction, continuation and export:

1. Resolve against the selected allowlisted root. Check each returned/read descendant,
   including paths traversing symlinked directories, before stat/read/search. Reject
   symlink escapes and absolute/parent traversal; a failed path lookup returns a stable
   code and logical path, never a host traceback or resolved private pathname.
2. Commands run only in `SandboxExecutor`, with `/workspace` writable, `/inputs`
   read-only, and optionally the sanitized view at `/evidence` read-only. No `/checks`,
   private tree, raw logs, parent run directory, Git object store, runtime source,
   Docker socket or provider credentials. File-action and command visibility agree.
3. Sanitize `ExecResult` before exposing it: command results must not contain the
   outer Docker argv and its host bind paths. Report the user command, container-logical
   cwd, status, duration and bounded stdout/stderr; retain infrastructure argv privately.
4. Server/build commands cannot reach private assets during generation or verification.
   Commands finish and containers are cleaned before host file tools resume; prevent
   leftover child containers from modifying path targets during host observations.
5. Never rehydrate raw host results into `initial_feedback`, `last_feedback`, current
   observations, native tool messages, JSON receipts, follow-up diagnostic refs or
   evicted-history reads. Follow-ups may resolve only published diagnostic refs; an
   arbitrary valid host hash/path is insufficient authorization to read an artifact.
6. Export from the accepted project and explicit public deliverable list. Verify
   archive members and symlink targets remain within that export. Do not bundle the
   run root, private manifest/assets, seeds, payloads, raw checks or raw conversations.
   Historical bundles that contain readable checks remain unchanged and labeled legacy.

## 5. P1: verification execution and diagnostics

### 5.1 Separate checker and generated-program filesystems

Deleting `/checks` only from coding commands is insufficient: generated C code can
open it later if the checker and server share a verification container. Therefore
replace the existing same-container supervisor with two containers managed by the
existing `VerificationRunner` and `SandboxExecutor` lifecycle:

| Environment | Mounts and communication | Excluded |
|---|---|---|
| Build/coding | Existing compiler image, generated project, public inputs/development checks; network `none` | All private assets and host evidence |
| Server under test | Fresh container with `--network none`; clean-built candidate mounted read-only at `/workspace`; private writable temporary space if needed | Checker assets, checker output volumes, payload/seed, host run/evidence, shared PID/IPC namespaces |
| Trusted checker | Separate root/mount/PID/IPC namespace; private assets at `/oracle:ro`, trusted worker at a narrowly scoped mount; `--network container:<server-id>` joins only the server's isolated loopback network | Generated project mount or generated code on Python import/PATH, shared temporary files, Docker socket, coder evidence mount |
| Host controller | Starts/stops both, captures separate output streams, persists private bytes/results, publishes sanitized diagnostics | Execution of generated build commands or server binaries directly on the host |

The pair shares only the loopback network namespace, with no external interface,
published host port, host networking or external egress. Both use nonprivileged
execution, no added namespace/ptrace/mount capabilities, dropped unnecessary Linux
capabilities and no privilege escalation. These settings close the explicit access
boundary; no broader external security-testing system is introduced. Docker setup
must prove this topology on the actual host before P1 admission. If it is unsupported,
record an infrastructure failure; never fall back to shared `/checks` or host networking.

Host chooses the randomized port for a fresh namespace, passes host/port/artifact
only to the server's Target argv, and passes check configuration privately to the
checker. A bind/readiness failure is preserved, not converted into a success. The
server necessarily sees its own port and received protocol bytes; it never receives
the seed, PRNG state, upcoming vector sequence, checker argv or implementation.

Refactor `verification_worker.py` into trusted client execution and result collection;
it must no longer launch the generated binary in its filesystem. Start the server
with the Target's argv through the existing Docker executor. Preserve readiness,
per-check timeout, client return codes, server early-exit detection, SIGTERM grace,
forced-stop failure and sanitizer checks for both variants. Keep the server alive
through all checks in a variant, as today. Treat checker result shape/ID/required
coverage mismatches, truncated or invalid output, lost checker exit status and
incomplete server supervision as incomplete/failed, never passing observations.

Spool complete raw logs and interaction records to host-only attempt evidence, then
close/hash them before publishing refs. A checker output directory, if required for
transcripts, is a unique writable mount visible only to that checker and host; the
server never receives it. Keep model-facing results bounded and paginated independently
of raw evidence capture. A raw evidence limit or write failure makes the attempt
incomplete instead of silently truncating a supposedly complete transcript.

Use host-controlled attempt IDs and record both container IDs before advancing the
verification operation. On interruption/deadline/resume, inspect and clean only that
run's recorded containers, preserve logs and the incomplete attempt, and rerun the
whole verification against the accepted candidate. A partial check result never
advances a task or fills final-export gaps. Reuse the existing pending-operation and
checkpoint discipline; do not introduce a separate container-state authority. Keep
the existing total CPU/memory envelope by budgeting the pair within it and record
the partition in the frozen sandbox configuration and profiling.

### 5.2 Sanitized repair contract

Host stores full checker stdout/stderr, stack traces, argv, seed and exact transcript.
Private scripts emit a structured semantic observation in addition to their host
debug detail. Project only an allowlisted `repair-diagnostic/1` object:

```json
{
  "schema_version": "repair-diagnostic/1",
  "check_id": "opaque-stable-check-id",
  "category": "stream-framing",
  "variant": "san",
  "status": "failed",
  "observation": {
    "operation": "receive-complete-frame",
    "expected": {"outcome": "wait-for-complete-input"},
    "actual": {"outcome": "early-response", "response_length": 2}
  },
  "build": {"status": "passed"},
  "sanitizer": {"status": "clean"},
  "server": {"early_exit": false, "exit_code": 0},
  "server_logs_ref": {"path": "evidence/diagnostics/opaque-id.json", "sha256": "..."}
}
```

`operation`, expected and observed protocol values come from trusted scenario data;
the generic runner only validates/forwards the approved structure, never infers MQTT
or HTTP semantics. Useful diagnostics include status/packet type, expected close or
timeout, length/order differences, byte-offset mismatch, target-source locations,
sanitizer class and relevant server logs. They exclude private input bytes, literal
random client IDs/topics/body content, seeds, check source, full test logic, checker
tracebacks/argv, host absolute paths and raw-evidence references.

Server logs are an output boundary too: generated code may echo every input byte.
Publish parsed compiler/sanitizer events and explicitly allowlisted operational
fields, normalizing source paths to project-relative locations and replacing actual
dynamic values with typed placeholders. Keep arbitrary free text, hex/base64 dumps
and unknown fields host-only with an omission marker; substring replacement alone
cannot sanitize encoded input dumps. Never attach raw server output or a raw-log
read link. Retain useful source/line/error information and bounded semantic protocol
results. Apply the same projection to exception messages and sanitizer excerpts.

The sanitized object and safe log pages are durable and readable by reference for
root-cause repair. Test both JSON and native action histories, failed finish, export
repair, context eviction and resume. A canary in a private script/manifest/seed/log
must never appear in any resulting model request, tool result, public archive or
agent-visible file. A separate repair fixture must demonstrate a real root-cause
fix using this feedback; a generic “verification failed” is insufficient.

### 5.3 Repeatable private inputs and coverage preservation

Create a 256-bit seed on the host for every verification attempt. Persist it before
launching checks. Freeze a deterministic generator version and materialize the
complete generated vector/fragment schedule into host-only `random-inputs/1` records;
checker subprocesses consume these private records. Preserve complete send/receive
bytes, direction, connection/order, chunk boundaries, actual byte counts, half-close,
timeouts and timestamps. Partial sends/receives are recorded as observed, not inferred
from a planned packet. Host replay uses the recorded bytes and schedule; timing/OS
variation is acknowledged, so replay is deterministic input reproduction, not a
guarantee of identical wall time or TCP packetization.

Generate port selection and case vectors from separate domain-separated streams,
using stdlib HMAC-SHA256 over the secret seed and version/domain/check/repetition/
counter labels. Materialize bytes and use rejection sampling for bounded choices;
do not use a state-recoverable generator whose output payload reveals future vectors.
Never derive the seed from a public run ID, timestamp, hash or visible port. Persist
the private seed/digest and suite version in host evidence, never the coder context
or export. Freeze this implementation and its runtime version, so replay does not
depend on ambient `uuid` calls or global random state.

For every release and san verification, retain every baseline mandatory assertion
and fixed boundary example, then run three materialized randomized repetitions per
applicable check, varying ordinary legal exchange counts from 2–4. Each baseline or
randomized repetition is a separate invocation with its original per-check timeout;
the current MQTT/HTTP 20-second limits cannot cover several keep-alive repetitions
combined. Compute the aggregate verification deadline from the expanded invocation
list plus bounded startup/shutdown, within the remaining run deadline. Retain required
timed exchange counts and all keep-alive observation windows. Measure this schedule
before live runs. Do not drop repetitions to fit the remaining budget; exhaustion
is incomplete. Record each repetition under its original check ID and require all
mandatory repetitions in both variants for a report pass. Use independent streams
per variant/attempt; formal repeats use new roots/seeds. Host replay can select an
old attempt, but cannot count as a fresh sample.

| Dimension | Frozen variation and retained constraints |
|---|---|
| Port | Fresh unprivileged host-selected port in the isolated namespace, excluding the Spec's conventional port in at least one required repetition. Exact bind port recorded. |
| MQTT client IDs/topics | Multiple legal distinct values/lengths, with required same-ID reuse retained for CleanSession reset; preserve valid UTF-8 and mandatory invalid-encoding cases. Never randomize away role/isolation prerequisites. |
| Legal payloads | Empty, binary, embedded framing bytes and large/boundary payloads retained; add varied lengths/bytes within the declared subset. Always test equality against the actual generated payload. |
| Fragmentation/coalescing | Retain mandatory header/length/body cuts and incomplete-tail assertions; add random ordered cuts and multiple-message grouping without altering expected protocol semantics. |
| Repetitions | Multiple independent IDs/ports and exchange counts; preserve absence-of-delivery, ordering, healthy-client survival and reconnect assertions. |
| HTTP | Vary lawful header case/OWS, Host, echo payload and missing-route values; retain GET/HEAD/echo application rules and all original malformed/length/truncation/Transfer-Encoding subset checks. |

Migration report must map every original check and assertion to its new location and
req_ids. Counts alone cannot prove unchanged strength. Preserve all MQTT 20 and HTTP
12 mandatory checks in both variants, their required flags, error/absence assertions,
sanitizer behavior and explicit gaps. Public development checks may expose simple
documented examples, but their results never substitute for a private check.

## 6. P1 validation and publication

Run real Docker tests as well as host-tool tests. Use known-correct host-maintained
fixtures and copied successful exports as verifier controls; any source mutation is
in a disposable test copy and is never imported into a generation run.

Required controls: correct project passes; wrong protocol response, sleep-only
server, hardcoded single client ID, fixed-port server, premature exit and actual
ASan/UBSan defect fail. Preserve a diagnostic-marker control, but also run an
instrumented C defect to prove sanitizer execution. A generated test server that
tries to open `/oracle`, `/checks`, `/verification`, private absolute paths and
checker `/proc`/file descriptors must fail to read them during verification. A
server that writes received bytes into its logs must not expose raw private vectors
to repair context. These are local correctness checks of NePA's isolation only.

Test list/read/search/write/replace/run_command with absolute paths, parent traversal,
file and directory symlinks, aliases to raw evidence, old action logs, old calls,
checkpoint Git objects, `/proc`, environment, mount metadata and arbitrary command
subprocesses. Assert accessible useful public data as well as denied private reads.
Inspect real Docker mounts/namespaces; mock command-vector assertions alone do not
close ACC-001/002. Independently rebuild and privately verify copied MQTT and HTTP
exports with the full unchanged assertion set and replay one recorded attempt.
G2's automated repair-boundary test can drive the existing session with a deterministic
provider double and a known fault; it proves publication/access/repair execution, not
real model ability. G5 adds the real model repair session before any countable protocol
generation, with both transcript and resulting behavior inspected.

Report5 host output retains full per-check evidence refs, requirement claims and
final-export joins, profile/identity/pricing data and exposure classification:
`legacy_readable`, `public_development`, or `private_isolated`. Distributable report
projection contains aggregate private suite/verifier hashes, check IDs/categories,
variant/status/coverage, sanitized diagnostics, costs and an opaque host evidence ID.
It contains no private filenames or a general path for reading raw evidence.

Both reports keep `scenarios_passed`, `failed`, `incomplete`, `unverified` semantics.
Only required private checks on the current final export establish private scenario
status; older attempts and optional/public checks cannot fill missing results.
Report all original claims and gaps, both release/san outcomes, incomplete controls,
and failed initialized runs. Preserve the current exit-code success contract.

## 7. P2: provider capabilities, identity and CNY settlement

### 7.1 Required fact handoff and profile

The provider-verification owner supplies dated official documentation/account evidence
for each exact ID, endpoint/region and price before a paid probe. Never query paid
inference merely to discover an unpriced model. The main agent's local audit records
a successful Beijing `GET /models` listing both IDs and cites the corresponding official
model/API pages; its account evidence is `runs/qwen-p0-p2-discovery/models.json`.
This planner consumed that audit, not the API. The first metered probe still must
confirm actual inference access, returned identity and the documented contract.

| Role | Exact requested model ID | Planning status |
|---|---|---|
| Difficult/shared/integration/repair | `qwen3.7-plus-2026-05-26` | Listed/documented per main-agent audit; real inference pending |
| Bootstrap/ordinary message/initial ordinary requirements | `qwen3.7-flash-2026-07-15` | Listed/documented per main-agent audit; real inference pending |

Use the audit's Beijing endpoint `https://dashscope.aliyuncs.com/compatible-mode/v1`.
Its recorded CNY prices per million tokens are:

| Model | Actual total input tier | Miss input / hit input / output |
|---|---|---|
| Plus exact snapshot | ≤256K | 2 / 0.4 / 8 |
| Plus exact snapshot | >256K to 1M | 6 / 1.2 / 24 |
| Flash exact snapshot | ≤32K | 0.2 / 0.04 / 0.8 |
| Flash exact snapshot | >32K to 256K | 0.6 / 0.12 / 2.4 |
| Flash exact snapshot | >256K to 1M | 1.2 / 0.24 / 4.8 |

Copy exact numeric tier boundaries from the cited official snapshot into Config3;
do not guess whether a displayed K means a decimal or binary multiplier. Tier selection
uses actual total prompt tokens before subtracting cache hits. There is no DeepSeek
off-peak discount. Preserve source refs and the local audit hash in the rate profile.
The audit records context 1,000,000, maximum input 991,808 (983,616 with thinking),
and maximum output 131,072; keep the existing 180,000-byte/16,000-output decision
limits. Use `max_completion_tokens`, including thinking and the documented possible
10-token overrun in the reservation. Do not silently clamp measured usage to 16,000.

Plan Plus with `enable_thinking=true`, Flash with `enable_thinking=false`, streaming
usage enabled, JSON-object actions, temperature 0 and no stop. Admit these combinations
only after probes. When reasoning history must be retained, configure the documented
`preserve_thinking` behavior explicitly. A native-mode probe uses `tool_choice=auto`
and `parallel_tool_calls=false`; do not force a named tool in thinking mode. Normalize
`prompt_tokens_details.cached_tokens` and optional
`completion_tokens_details.reasoning_tokens`; reasoning is a subset of completion
tokens, not an additional charge. These request/usage choices come from the main-agent
audit and still need actual-wire/response evidence.

Create `configs/qwen-p0-p2.yaml` after the fact handoff can populate validated
Config3 fields; incomplete placeholders are non-runnable. Keep the Qwen model/profile
entries explicit rather than inheriting the default DeepSeek configuration. Store
official evidence refs/hashes, verification date, exact API ID, region/endpoint,
streaming/JSON object/native tool support and compatible combinations, thinking
controls/output accounting, context/output ceilings, stop/temperature restrictions,
returned-model semantics, usage/cache fields, retry/error policy and CNY tariff.

Represent capabilities as per-provider/model configuration data with narrowly typed
known wire options in the existing adapter, not an arbitrary override dictionary.
Unsupported configured combinations fail before I/O. Unknown fields remain unknown;
do not assume all OpenAI-compatible models accept the same thinking, streaming or
output parameters. No implicit action-format, thinking, endpoint or model switch.

### 7.2 Use the actual adapter-prepared request everywhere

Give the existing Provider contract a request-preparation operation; retain its
single-attempt send operation. The selected adapter prepares the actual serializable
wire payload once. Context sizing and reservation use the same payload/token-bound
logic that the send operation consumes. Provider selection, request limits and
errors remain in `LLMClient`, with `CodingContext` using that preparation/measurement
path instead of importing the OpenAI adapter. Avoid estimating one payload while
sending another, or rebuilding it with different defaults after reserving.

Honor both the actual-wire byte cap and the documented context/output token limits,
including tool schemas, reasoning fields, retained history and requested thinking
budget. Use a documented tokenizer if already available or a conservative UTF-8
byte-based bound with explicit overhead; fail locally when the verified limit cannot
be respected. Do not silently evict required observations, lower requested output or
truncate task facts. Profile limitations/overestimation so capacity errors are
distinguishable from provider rejection. Add nonstreaming normalization only if the
verified selected profile requires it, within the same adapter; do not implement
unused provider modes for hypothetical models.

Keep strict AgentAction1 validation, matched native IDs, complete transaction
retention and the existing single executor. JSON parsing remains outer-envelope only.
No XML/DSML extraction or schema weakening. Start Qwen with explicit JSON-object mode
if verified compatible; any choice of native mode must be a deliberate pre-run
configuration with corresponding fixture evidence. Both IDs must pass the selected
mode. Unsupported optional modes are reported unsupported, not pretended tested.

### 7.3 Identity, usage and conservative accounting

Extend normalized response/call evidence with requested model, actually returned
model (nullable), observed flag, identity match status, provider/endpoint/region,
input/output totals, cache hit/miss, reasoning token count or explicit unavailable
status, finish reason, rate snapshot ID, selected rates/tier and cost basis. Preserve
original provider usage with secrets redacted. Do not synthesize observed identity
from the request. Require exact returned ID for countable Qwen proof calls; a provider
that omits it or only returns a family alias cannot establish these mandated IDs.
Do not silently add alias mappings; report identity-unverifiable as a fact gate.

Before action decoding/execution and settlement, validate identity and usage:

- Token values are nonnegative integers, not booleans; cache components agree with
  the documented total and reasoning is included or separate exactly as documented.
  Do not double bill reasoning when it is already part of completion tokens.
- Missing required usage, inconsistent usage, missing/mismatched identity or unknown
  calls retain the full reservation and yield no executable action. Save available
  response/usage evidence first, with an explicit accounting status. A separately
  auditable identity violation must not become a zero-cost response or Qwen success.
- Missing optional cache detail is recorded as unavailable and conservatively all
  misses; missing optional reasoning detail is explicitly unavailable. This does
  not make an otherwise valid total free. If the verified API promises required
  reasoning/cache fields and they are absent, classify a usage-contract failure.
- Retry only the existing bounded transport/429/5xx classes, with a fresh call ID
  and reservation for each attempt. Authentication, unavailable model, invalid
  parameters, payment, unsupported capabilities and identity/usage failures stop
  that probe/run for diagnosis. No provider/model fallback, including to DeepSeek.

Reuse `RunStore.reserve_call`, `settle_call`, `fail_call`, immutable call traces and
`.campaign.lock`. Add the necessary metadata/timing to these paths; never settle a
probe through direct HTTP code or a separate cost spreadsheet. Retain responses even
when a subsequent price/identity validation fails. Recovery must not settle twice,
reuse call IDs, free uncertain reservations or reset creation time/session budgets.

Extend existing `ModelPrice`/telemetry to explicit verified pricing policies. DeepSeek
keeps its documented weekday periods; Qwen uses its own documented constant or
context-tiered/time-dependent policy, with no inherited 0.5 off-peak multiplier.
Implement only tariff dimensions present in the verified IDs: currency CNY, per-million
input/cache/output rates, relevant context tiers and reasoning treatment. Record
source URL/date/region/tax convention when specified; estimates are not invoices.

Reserve at the maximum applicable verified input/output rates, assume input misses,
include billable thinking in the output bound and select worst applicable tiers.
Settle using recorded request/usage and the frozen tariff. Validate tier boundaries,
cache extremes, absent optional detail, reasoning inclusion, malformed counts and
period boundaries. If usage exceeds the reserved bound, retain an explicit accounting
fault, record the actual known liability and stop new calls; do not clip cost to the
cap or silently allow an underestimated budget. Correct the bound before new runs.

The new campaign root is `/home/ljf/NePA/runs/qwen-e2e`, shared by Qwen probes,
fixtures, MQTT and HTTP runs. Reserve/settle under the existing atomic campaign lock.
No nested run-root arrangement that escapes `*/run.json` accounting. Every run is
at most CNY20/four hours; use smaller probe/fixture limits. Set an initial aggregate
CNY5/30-minute capability phase and CNY5/30-minute public-tool phase within CNY300;
enforce phase headroom through the same locked reservation path, derived from tagged
RunStore records, not an unlocked local counter. A phase that cannot complete inside
its preregistered cap remains incomplete; increases require a recorded new experiment
plan within the already authorized total. No request starts without headroom.

### 7.4 Generic routing and public tool fixtures

Keep `CoderConfig.for_task` and extend its return/evidence reason coherently:

| Task property/state | Route | Evidence reason |
|---|---|---|
| Initial ordinary bootstrap/message/requirements session | Flash exact ID | `initial_ordinary_task` plus task kind and session |
| shared-wire, integration, follow-up/high-impact shared interface | Plus exact ID | `shared_interface`, `integration`, or `followup` |
| Exhausted/failed session retry | Plus exact ID | `task_retry` and prior diagnostic ref |
| Build failure requiring difficult repair or private-check failure | Plus exact ID | `build_repair` or `private_acceptance_repair` and diagnostic ref |

Route on plan task kind and observed build/verification failure, never protocol names,
message constants or requirement prefixes. In this serial baseline a build failure
after initial edits conservatively routes subsequent repair decisions to Plus, keeping
the same task/session/decision limits and current observations; record the transition.
No invented difficulty classifier is needed. Apply a future adjustment to the generic
rule and freeze a new candidate rather than overriding MQTT or a named fixture.

Freeze `tests/fixtures/public_tools/` as a genuinely public, protocol-neutral suite:
file discovery/read/write, exact replacement, compile → diagnostic read → repair,
cross-file declaration/caller repair, and finish/host build. Small terminating C
programs have explicit exit/output contracts and short runtime checks in both variants.
Fixtures include build recipes and bad input source, not a golden finished protocol.

Use the same `CodingSession`, `CodingContext`, `WorkspaceTools`, `BuildRunner`, local
action validator and RunStore accounting. Supply an explicit public fixture prompt
and task facts at the test harness boundary, using the same action-schema instructions;
do not label these tasks production bootstrap or tell them to create a listening
server. This is a test-only session setup, not a new Target runtime type or production
fixture branch. Keep production Target1/server lint unchanged. Record this prompt
difference honestly; full protocol prompts are assessed by the real P2 runs.

Force each exact Qwen ID for its own fixture sessions with `fast_model` unset, so
routing to Plus cannot falsely establish Flash repair capability. Run all five
fixtures once per model with at most 12 decisions and the frozen short-session
timeout; every fixture must complete its actual tool/compile/execute/finish contract.
Invalid actions never execute and remain in the rate/cost/error report. Any retry is
a new recorded fixture attempt; a corrected fixture/prompt candidate reruns the suite.
Also test sanitized private diagnostic repair with a small host-owned faulty fixture
and the production feedback projection before protocol feasibility admission. It
counts only as repair-boundary evidence, not a generated protocol success.

## 8. Ordered execution gates

| Gate | Work and exit evidence | Consequence if unmet |
|---|---|---|
| G0 — P0 handoff | Main-agent inventory, original/fresh statuses, scoped hashes, task/call profiling and missing-data statement; freeze public-fixture/private-suite contracts | P0 incomplete; preserve historical successes with correct exposure label |
| G1 — design/contracts | Implementation owner updates System Design11 and versioned schemas/config/examples; reviews input → tool → verifier → repair → report/export chain | No claim that a partial mount change implements P1 |
| G2 — isolation/regression | All ACC controls on real Docker, full MQTT/HTTP copied-export checks, host replay, safe diagnostic repair and public export inspection; Run7 recovery/accounting regressions | P1 incomplete; no formal Qwen protocol evidence |
| G3 — provider facts/offline | Official/account fact handoff for both exact IDs; runnable Config3 with CNY prices; capability/wire/identity/usage/error/accounting tests and phase caps | No paid inference for an unverified/unpriced ID; status `provider_unavailable` or `facts_unverified`, without substitution |
| G4 — metered capabilities | Bounded real probes for each ID, selected action format, observed exact identity, usage, limits/thinking request, no hidden retries/fallback; mock transport tests cover dangerous/error cases without intentionally wasting paid calls | P2 probe incomplete/failed, charged; classify and repair generic adapter/config |
| G5 — public sessions | Both IDs pass every frozen public tool fixture, plus sanitized repair-boundary fixture; retained actual action histories, builds, exit results and costs | Do not infer readiness from JSON-only probes or fixture collection |
| G6 — first MQTT | Empty accepted initial checkpoint, Qwen-only generation, all tasks/claims, clean release/san builds, full randomized private final-export checks, separately copied export rebuild/checks, auditable identities/costs | Retain/classify failure. No stability repetitions after a failed first run |
| G7 — frozen MQTT repeats | After first success, seal candidate; execute two more independent empty-project successes with new seeds and identical MQTT candidate/config/input/image. Default sequential to preserve serial profiling | Any failed repetition or candidate change prevents three-consecutive-success claim; retain attempts, restart cohort as required |
| G8 — HTTP cross-protocol | One fresh empty-project HTTP subset generation under the same final runtime/config/model routes/image and frozen HTTP input/private suite; complete independent export rebuild/checks | Report `MQTT-only; cross-protocol unverified`; P2 is partial, not complete |
| G9 — final audit | Reconcile per-call ledger, raw/safe evidence, hashes, failure classifications, all requirements/variants and matrices; publish claim-bounded results | Missing evidence remains incomplete; passing existing unit tests is insufficient |

For failures distinguish: provider availability/auth/rate/network; capability/wire
adapter; action syntax/schema/claim contract; context capacity/staleness; task design
or routing; generated build/interface/semantic defect; verifier/oracle/infrastructure;
sanitizer/runtime; budget/deadline/interruption. Store the failing input/call/action/
check refs, observed reason and any fix commit. Fix production code/config/input only
through a recorded candidate revision; do not hand-edit generated code, import an old
solution, disable a check or tailor semantics to a requirement ID. Unknown cause is
explicitly unresolved until diagnosis, not assigned to the model by default.

Qwen success requires Qwen for every generation/repair call. If another provider was
used, preserve and label the run mixed-model and exclude it from the Qwen cohort.
No response cache or reuse of historical generated trees. Provider-reported prompt
cache discounts may be accounted if documented; they are distinct from a prohibited
client-side response cache and must not be reported as cached generated answers.

During long real runs assign read-only observation to a low-cost monitoring sub-agent
when available. It reports only meaningful progress, finish/failure, apparent stall
or required action. Main agent retains diagnosis, modifications and acceptance;
monitor must not stop containers or alter runs. No paid work is performed by this
planning task.

## 9. Completion-evidence matrix

All implementation/test/live rows below are planned gates, not claimed completions.
P0 identifiers are assigned here because requirements section 4 has no formal IDs.

| Requirement | Required implementation/evidence | Completion gate |
|---|---|---|
| P0-REQ-001: retain first DeepSeek success | Original input/config/runtime/model/cost/task/build/interaction/export refs and digests, mixed-version history if applicable | G0; immutable originals |
| P0-REQ-002: close MQTT/HTTP statuses | Separate passed/failed/incomplete status backed by final reports and independent export checks; readable-oracle label on `10cb987` samples | G0; consume main inventory |
| P0-REQ-003: comparable serial profile | Per-task/session/call/provider/tool/build/retry wall time, tokens, CNY, failure categories and routes; active time versus interruption gaps | G0/G9; no inferred zero durations |
| P0-REQ-004: freeze public/private checks | Public-tools suite digest and migrated MQTT/HTTP private suite/assertion map, frozen before provider comparison | G0/G2/G5; existing study retained separately |
| P0-DONE-001: locate time/cost | Ranked task/call/retry contributors, reconciliation and missing-data/residual statement | G0; usable attribution, not totals alone |
| P0-DONE-002: candidate hashes | All comparison inputs/images/prompts/assets/config/runtime/harness identified; drift splits candidates | G0/G3/G6–G9 |
| P0-DONE-003: historical integrity | Read-only inventory and before/after original digest equality; new derived reports at new paths | G0/G9; no migration/overwrite |
| ACC-001: physical storage isolation | Separate private snapshot, no private coder mounts, separate server/checker filesystems and narrow mounts | G2; actual Docker inspection + generated-server file-read controls |
| ACC-002: every access path closed | File/search/hash/command/symlink/absolute-path/history/context/follow-up/export tests, no raw evidence root, safe infrastructure errors | G2; canary absent from every model-visible surface and archive |
| ACC-003: structured usable feedback | `repair-diagnostic/1`, logical source diagnostics and paginated safe logs; no scripts/seeds/tracebacks/raw read refs | G2/G5; a repair session fixes the declared fault using only safe feedback |
| ACC-004: dynamic repeatable inputs | Host seed/materialized-vector/raw-interaction records; varied ports/IDs/payloads/cuts/coalescing/counts; replay + distinct-seed tests | G2/G6–G8; public results separate |
| ACC-005: evidence and history | Run7 private digests/verifier/result refs, Report5 exposure/claims/gaps, exact baseline assertion mapping | G0/G2/G9; no reduction or retroactive private claim |
| P1-DONE-001: actions cannot read secrets | Real tool action coverage for all actions, path classes, subprocesses, context refresh and resumed history | G2; command-vector mocks insufficient |
| P1-DONE-002: verifier runs same private suite | Full suite starts server, performs checks, gathers byte/log/exit evidence in isolated topology, release and san | G2; full MQTT20/HTTP12 plus frozen variations |
| P1-DONE-003: controls pass/fail correctly | Correct, wrong, sleep-only, hardcoded-ID, fixed-port, early-exit and actual sanitizer controls with per-case outcomes | G2; cannot replace with minimum smoke fixture |
| P1-DONE-004: repair/report | Transcript proves sanitized-only repair; final report distinguishes private independent and public checks | G2/G5/G9; no generic-failure-only diagnostic |
| P2-TARGET-001: precise model facts | Both mandated IDs, region/endpoint, context/output, thinking/actions, usage/cache/identity and official CNY rates with date/refs | G3/G4; fact handoff plus account probe, no guessed price/alias |
| MODEL-001: unified capabilities | Explicit profiles; actual adapter request used for send/size/reserve; mode/limits/errors tested for selected profiles; neutral planner/tools/checks | G3–G5; unsupported combinations rejected locally |
| MODEL-002: configuration/accounting | Auditable Config3 model/rates; requested/observed identity and all documented usage categories; unknown reserve retention; no fallback | G3/G4/G9; reconcile every paid attempt to RunStore |
| MODEL-003: attribute-based routing | Flash initial ordinary tasks; Plus shared/integration/difficult repair; actual reason/transition recorded | G3 offline route tests + G6–G8 live call evidence |
| MODEL-004: staged experiments | All six stages below complete with immutable evidence and their predecessor gates satisfied | G4–G9; no stage inferred from another |
| MODEL-004-1: capabilities | Small metered probes of exact IDs, action/usage/error behavior | G4; no unit-only capability claim |
| MODEL-004-2: tool sessions | Both IDs complete read/edit/compile-fail/diagnose/fix/finish contracts and actual output checks | G5; fixture/session failures retained |
| MODEL-004-3: fresh MQTT feasibility | One new Qwen-only empty project succeeds through private acceptance/export | G6; no fixture/copied-export substitute |
| MODEL-004-4: diagnosis/optimization | Every failed attempt classified with evidence, neutral fixes, immutable candidate lineage | G4–G9; unresolved causes explicit |
| MODEL-004-5: two frozen repeats | Two additional fresh MQTT successes after the first, all three same candidate; complete real calls, claims/build/check/export evidence | G7; new candidate restarts cohort |
| MODEL-004-6: cross-protocol | Fresh HTTP subset success with same final runtime/config and equally isolated gates | G8; omission means P2 partial |
| P2-BUDGET: adopted limits | Separate Qwen root, CNY300 total, CNY20/four hours/run, small probe/session caps, all failures/retries/reservations charged | G3–G9; atomic reservation/deadline tests + live ledger |
| P2-DONE-001: both model contracts | Both IDs' successful action sessions, exact returned identity and auditable CNY usage | G4/G5/G9 |
| P2-DONE-002: stable MQTT | First plus two new successes under one frozen candidate; no human generated-code changes/cache/imported solution | G6/G7/G9 |
| P2-DONE-003: HTTP scope | At least one HTTP private final-export and independent rebuild success; otherwise explicit MQTT-only limitation | G8/G9; this plan requires HTTP for full P2 completion |
| P2-DONE-004: failures/neutrality | Root-cause report and scoped production diff showing no protocol name/constants/req-ID/fixture/Qwen semantic branches | G9; adapter wire differences allowed, task semantics unchanged |

Shared-constraint closure:

| Requirements §10 | Evidence |
|---|---|
| 1: protocol-neutral core | Review affected production diffs and run existing non-MQTT planning tests; protocol bytes/semantics only in inputs and acceptance assets |
| 2: no weakened/leaked checks, solution import/cache/manual patch | Full assertion map, isolation matrix, empty initial checkpoint, call/cache records, unchanged generated-project lineage and export hashes |
| 3: multidimensional optimization | Each candidate report includes quality, wall time, tokens, CNY and failures, with matching denominator/scope |
| 4: probe → feasibility → frozen repeats | G3–G8 prerequisites recorded, no skipped/incomplete stage converted into success |
| 5: recoverable state and atomic budget | Existing crash-window/checkpoint/lock tests extended to private refs, paired-container cleanup and pricing validation; all costs preserved |
| 6: design authority | Implementation owner publishes Design11 change and rationale using explicit user authorization; this plan leaves Design10 untouched |
| 7: no external publication/history cleanup | Scope-limited local changes and untouched original artifacts; no push/merge/deploy/cleanup |

## 10. Suggested implementation ownership and validation

This crosses more than three files because the leak is end-to-end and wire/pricing
assumptions occur in multiple existing consumers. Split ownership by invariant and
integrate sequentially where files overlap; do not let several agents edit shared
state/session modules concurrently. Names below are roles, not new Codex tasks.

| Owner | Suggested files | Handoff/dependency |
|---|---|---|
| Main/integration | `project_docs/system_design.md`, result documents; P0 inventory; `nepa/orchestrator.py`, `nepa/application.py`, `nepa/cli.py`; final ownership of `nepa/run_store.py` | Publishes design/contracts; integrates private refs, repair/export, campaign/report gates and recovery. Existing user ownership of this plan remains exclusive to the planner. |
| Isolation/verification | `nepa/tools/sandbox.py`, `nepa/tools/verification.py`, `nepa/tools/verification_worker.py`, `nepa/tools/workspace.py`; Docker change only if proved necessary | Agrees result/lifecycle/public-view API first; owns host and actual-container isolation controls. Submits RunStore/session changes to integrator rather than concurrent edits. |
| Acceptance/report | `gold_file/{mqtt,http}/acceptance.json` and acceptance scripts, `nepa/report.py`, acceptance/report schemas and examples, `nepa/speclib/lint.py` | Preserves assertion map; provides randomized transcript/diagnostic contract to isolation owner; host/public report projections. |
| Provider/accounting | `nepa/config.py`, `nepa/llm/client.py`, `nepa/llm/providers/openai_compat.py`, `nepa/llm/telemetry.py`, affected other adapter; config profiles | Consumes main-agent official facts; proposes RunStore settlement metadata to integrator. No parallel ledger or API bypass. |
| Session/context | `nepa/agents/session.py`, `nepa/agents/context.py`, `nepa/agents/prompts/coder.md` | One owner combines sanitized-feedback/public refs, adapter sizing, route transitions; preserve observations and transaction pairing. |
| Validation/experiments | Existing related `tests/test_*.py`, new `tests/fixtures/public_tools/`; extend existing action-study/live harness logic with explicit P2 inputs and sequencing | No new production framework. Preserve original preregistered study evidence; freeze new fixture/harness versions. Full live harness cannot assume the old parallel MQTT/HTTP schedule or old input paths. |

Update Run7, Report5 and Acceptance2 schemas/examples atomically with their consumers.
Keep tests that assert old readability only as historical evidence references; replace
active assertions with private-denial/public-access contracts. Do not delete unrelated
provider/SSE/redaction/lock/schema/checkpoint tests or rerun historical paid studies.

Suggested offline validation commands after implementation (not executed by this plan):

```bash
python -m pytest -m 'not live_e2e'
python -m ruff check nepa tests
python -m mypy nepa
python -m build
```

Build/identify the sandbox image before container tests, using the project's existing
Dockerfile and the frozen profile image. Container-marked tests must actually run;
skips due to unavailable Docker make G2 incomplete. Validate wheel-installed behavior,
CLI lint/run/status/resume/version rejection, archive contents and copied-export
clean builds. Keep paid opt-in disabled during these commands. Then execute the
explicit G4–G8 harness under the adopted budgets and candidate manifests, not a test
collection or the previous parallel two-protocol feasibility harness unchanged.

The planner's deliverable is this reviewed plan and matrix. P0 closure awaits the
main inventory; P1 awaits implementation and real isolation/regression evidence; P2
awaits exact-ID fact validation, metered sessions and the required real runs. If an
ID is unavailable, pricing unverified, budget exhausted or a run fails, preserve the
evidence and report the affected gate incomplete. None of those states can be
renamed “P2 complete” because the current unit suite passes.
