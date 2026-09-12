# NePA System Design 9.0

Status: approved architecture. Actual implementation/acceptance progress is in
`refactor_plan.md`. Replaces 8.0.3 and the separate S4–S9 pipeline design.

## 1. Success contract

Inputs are manually curated Spec 3.0 (including all original requirements), Target
1.0 and independent Acceptance 1.0 assets. Initial implementation scope: Linux
x86_64, C99, server. MQTT is an acceptance input, never a production special case.

Success requires all tasks completed, clean release and ASan/UBSan builds, mandatory
protocol interactions passed, and independently buildable sources, binaries and
Report 3.0 published. JSON validity, stub compilation, process survival or an agent's
finish declaration are not success. Optional observations remain non-gating and
never establish full protocol conformance.

Formal acceptance: three consecutive independent empty-project runs with identical
NePA commit, prompts, input/configuration and image, real API calls, no response
cache, imported solution or manual generated-code edits. Changing these inputs
restarts the batch. Retain debug/failed runs. Approved campaign budget: USD100
including debug and failures; each run USD20 and four hours from creation, never
reset on resume. Unknown usage keeps its pre-call reservation.

## 2. Deterministic planning

Freeze original input bytes. Validate schemas, unique IDs and structural references,
not natural-language semantics. Requirements have one original source: Spec
requirements[]. Keep input array order. Generate a serial Plan 6.0:

1. bootstrap: real project, build/start entrypoints, initial interfaces and README.
2. shared-wire: transport and builtin/custom wire types.
3. One message:<id> task per message. Decode for receiver target roles, encode for
   sender roles, both if applicable. Explicitly review scope for irrelevant messages.
4. requirements:<n> batches of at most 12 original requirements, including DEFINITION.
5. final-integration: integrate and run clean builds and mandatory interactions.

Current gold: 8 types, 10 messages, 110 requirements, 23 tasks. Every requirement has
exactly one primary batch. Codec/type req references provide context, not completion.
Resolve type closure through fields, encoding members/item_type/length_type/base_type.
Keep constraints, bits, presence, derived values and source references intact.
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

Reuse current provider adapters in a JSON action loop:
list_files/read_file/search/write_file/replace_text/run_command/finish/request_followup.
Native provider function calling is not required. Serialize the action schema once,
budget actual wire requests including corrections, and honor explicit coder config.

Default: configured deepseek/deepseek-v4-pro, temperature 0, max output 16000. This is
a starting configuration, not a proven model ranking. Maximum 40 decisions per
session, three sessions per task; retries carry real prior diagnostics and consume
the same run budgets. Full evidence is durable; file/log tools paginate outputs.
Retain task/target/index and recent transcript in context; older evidence remains
readable by reference. Read current code rather than trust stale summaries.

Only generated project files are writable. Input, oracle, runtime state and Git
metadata are outside that mount. Reject traversal and symbolic-link escapes.
Commands run in a network-disabled, resource-limited container without host secrets,
NePA source, old answer fixtures, cached answers or installed protocol servers.
Host tools never execute generated commands outside that sandbox.
finish requests host checks; it cannot mark success itself.

## 4. Independent build and interaction checks

Target fixes argv build/run commands and output paths. The initial C99 target uses
make release/make san and separate build/release/protocol-server and
build/san/protocol-server outputs. Both require -std=c99 -Wall -Wextra -Werror;
san additionally uses ASan/UBSan. Agent-editable build files must honor this contract.

Each task passes actual builds and output checks before acceptance. Final checks
clean-build the exported project. A generic supervisor starts its binary and the
trusted client in one network-disabled container on loopback with a dynamic port.
Oracle code is read-only, performs readiness/interaction assertions and returns real
results. Capture actual server/client exit codes, timeouts and logs; stop the server
and reject sanitizer failures or unexpected termination. Never normalize failure.

MQTT-specific checks exist only in sample acceptance assets: valid CONNECT/CONNACK,
PINGREQ/PINGRESP, unsupported-level CONNACK 0x01 followed by EOF, then a subsequent
valid connection. Vary client IDs; run both variants. Oracle unit-test doubles do
not count as real generation. Remaining protocol behavior is explicitly unverified.

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
Contracts: Spec3.0, Target1.0, Acceptance1.0, AgentAction1.0, Plan6.0, Run5.0, Report3.0.

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
