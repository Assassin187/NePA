# Approved refactor execution record

## Baseline and authorization

User authorized implementation with “PLEASE IMPLEMENT THIS PLAN” on 2026-09-12.
Active design: System Design 9.0. No OpenSpec skills/workflow.
Source baseline: bba527d5f520f2de63a4fc11f74f57b9ba0ba890, master, remote matched.
Worktree: /home/ljf/NePA/runs/_refactor/worktree; branch codex/e2e-refactor.
Original dirty state: deleted project_docs/_lessons-top-agent-workflow.md and
untracked project_docs/research/. Original directory remains untouched.
Exact user research snapshot is separate commit db8741d.
Report SHA256: 401f640efd6b8a00c6acd9fc14188f5165abb65b0573238b5e175fc2e513d17b.
Lessons SHA256: 991f7096c32758b070a8340f1f1ee5da2af97f26c71af7445de81e6e45b4f61e.
Original user patch: /home/ljf/NePA/runs/_refactor/baseline/user.patch.

Baseline full pytest: 816 passed, 6 failed, 712.09s. Command:
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider.
Six failures are stale S5 config and downstream S6/lease provenance; assertions
were not relaxed. Ruff passed; mypy covered only four files; gold input lints passed.
M1-13: 25/30 checked; 7.2/7.3/7.4/8.3/8.4 incomplete. Nine natural runs stopped at
S4; 65K group has 2/5 and missing final study artifacts. Latest --check fails.
No real generated-protocol success at baseline. Old study is superseded, not completed.

## Decision

Preserve provider/SSE, budget, logging, sandbox and storage foundations; replace
planning/execution/verification. Incremental frozen-architecture patches retain
blockers; rewriting tested HTTP/sandbox foundations adds no endpoint value.
Research v2 remains unchanged. Adopt factual refs, deterministic small tasks and
feedback repair; defer OPIR/DSL/solvers/extraction/test generation/language expansion.
Do not retain CAP/epochs/F1–F3/calibration as production prerequisites.

## Execution checklist and verification

- [x] R0 isolated branch, original baseline and exact user snapshot.
- [x] R1 Design9.0, this record and README before runtime changes.
- [x] R2 Target/Acceptance contracts and independent verification supervisor.
- [x] R3 deterministic 23-task compiler with 110 primary requirement bindings.
- [x] R4 API tool loop and actual compiler-diagnostic repair.
- [x] R5 CLI-to-export pipeline, checkpoint recovery, truthful final reports.
- [x] R6 wheel, full mypy, CI and obsolete-path retirement.
- [ ] R7 three consecutive frozen-version real API successes.

R1: git diff --check; inspect active document references.
R2/R3: input/schema/oracle/plan/reference tests.
R4: provider and budget regressions, actual compilation-failure→repair.
R5: actual CLI/orchestrator wiring, interruptions, budgets, report consistency.
R6: uv run ruff check nepa tests; uv run mypy nepa;
docker build -t nepa-sandbox:refactor -f docker/sandbox.Dockerfile .;
uv run pytest -q -m 'not live_e2e'; wheel install outside source checkout.
R7: NEPA_LIVE_E2E=1 uv run pytest -q -m live_e2e tests/test_live_e2e.py.

## Acceptance, budget and rollback

All 110 requirements enter 23 tasks. Agent claims are distinct from verification.
Both release and san exported clean builds must pass CONNECT, PING, unsupported-level
reply/close and a subsequent valid connection. Original inputs, random client IDs,
dynamic ports, real API, zero cache, no imported/hand-fixed target.
Three consecutive runs share commit/prompts/config/inputs/image. Changes restart
the batch; debug/failed calls remain recorded. USD100 campaign; USD20/four hours per
run. Unknown calls keep reserved cost. Stop at budget, do not silently increase.

Each phase uses an isolated commit. Old run versions are not migrated. Preserve
failed attempts and manual edits before restoring accepted code in a new copy.
Original raw runs/research are not cleaned. Old tracked content is recoverable from
baseline. Before deletion confirm consumers removed and replacement tests exist.

## Deletion ledger

| Item | Reason, recovery, replacement |
| --- | --- |
| pipeline_design_s4_s9.md | Superseded by Design9.0; recover at baseline; one active pipeline contract |
| 365 obsolete source/schema/test/fixture paths | Exact paths and replacement coverage in refactor_deletions.json; no remaining production imports; baseline recovery |

## Deviations / execution evidence

- Actual Dockerfile is docker/sandbox.Dockerfile, not docker/Dockerfile in the plan;
  retain the existing path. This is a path correction, not an architecture change.
- R0: original HEAD/status unchanged, research hashes identical.
- R1: user explicitly authorized replacing old design; historical research retained.
- R2/R3: 10 input/plan/oracle unit checks passed; 26 original provider/SSE tests
  passed unchanged. Current gold deterministically yields 23 tasks / 110 bindings.
- R4/storage: 23 new tests passed, including actual Docker compilation failure,
  diagnostic-driven API-action repair, budget reservation, snapshot recovery and
  preservation of unexpected manual edits. Oracle positive/wrong-response/sanitizer/
  missing-binary/idle cases: 4 tests passed in 7.58s, using test doubles only.
- Build-only container successfully built without mosquitto or protocol libraries.
- Storage reservation/checkpoint primitives were implemented alongside R4 because
  tool sessions require durable evidence and pre-I/O budgets; R5 supplies their
  production orchestration. This changes implementation grouping, not architecture.
- A standalone stdlib verification_worker.py is packaged for the container boundary;
  the host verification module cannot be executed there with package-relative imports.

## Completion

Live debug 20260912T053200Z-c7cc1591 was intentionally interrupted after 18 real
calls and USD0.39849744 accounted cost: the single-user history representation led
to repeated list_files without implementation progress. Preserve its input, calls,
Makefile and report; do not reuse its generated code. Fix: actual assistant/action
and user/tool-result chat messages through the same provider API, no native tools.

Independent sanitizer probe in runs/_refactor/asan-probe: an empty C program had
5/20 PIE startup failures, 0/20 with -fno-pie -no-pie. Old templates used non-PIE too.
The san Target now requires these flags; ASan/UBSan and interaction gates remain.
This is consistent with the extra repair observed in the test-only CLI wiring run.

Second live debug 20260912T053748Z-a3f90aac was interrupted at 26 calls and
USD0.98489160 accounted cost. It over-read the full Spec in bootstrap and attempted
unavailable acceptance paths. Added the complete task overview, explicit bootstrap
scope, character-pagination guidance, and read-only input/check mounts. The oracle
remains immutable and host judged. Combined debug accounting: USD1.38338904.
No generated source was copied into NePA or subsequent runs.

Fault injection proved a final repair could fail but export still return success.
The terminal loop now requires every task passed before checking export. Its
regression test failed against the old loop and passed after correction.
Optional transport was incorrectly indexed as mandatory by the new Spec linter;
retained original Spec tests caught it, now handled according to Spec3 schema.

R6 initial full non-live suite: 94 passed, one paid test deselected, 23.03 seconds;
actual Docker tests ran, not skipped. Full mypy: all 29 retained Python modules
passed, without the previous four-file exclusion or skipped imports. Original
provider/SSE tests remain; obsolete stage API tests have named replacement coverage.
Runtime configuration no longer requires Jinja2 or pytest; pytest is a dev extra.
Wheel 0.1.0 built and installed into an independent virtual environment; CLI help
and Spec/Target/Acceptance lints passed from a non-source directory. Packaged coder,
seven schemas/examples and verification worker are included. Historical configs
m1-* and experiments remain as baseline-only records, not loaded by production.
CI builds the sandbox before tests and does not install or invoke OpenSpec.
Additional crash-window tests cover response-before-settlement and Git-checkpoint-
before-state-publication, preserving unknown costs and incomplete trees. A Linux
deadline alarm now interrupts in-flight operations at the approved four-hour limit,
not only at the next model decision; sandbox cleanup also handles that interruption.

Pre-live frozen candidate: full non-live regression 100 passed / one paid test
deselected in 23.49s; Ruff passed and mypy passed all 29 production modules.
Wheel resource imports and lints passed outside the source checkout.

Candidate 2241bbd / batch 69de2630c7494280b25345caef93c5c8 failed by controlled
interruption (CLI 130), not task-budget exhaustion: run 20260912T055806Z-895133e7
accepted bootstrap, then spent all 40 decisions of shared-wire's first session
without modifying source. Repeated individual requirement reads and invalid XML
pseudo-tool outputs were preserved. One read-only response consumed 12040 output
tokens. Stopped after 56 calls / USD1.40643492, with 1/23 tasks accepted.
Combined live accounting is USD2.78982396; no completed generation.

Evidence-driven correction: include all structurally referenced requirement texts
in bootstrap/shared-wire context (no semantic guessing or protocol branch), show
remaining decisions, supply JSON action examples and explicit XML correction.
Search now supports regex because actual agent actions used regex alternation and
the literal implementation silently returned no matches. Oracle, task ownership,
model, budgets and final acceptance remain unchanged. Next batch starts at zero.

Candidate 48c09e7 / batch e2df059be0a249f482a13987a5df128e failed naturally at the
per-run reservation budget (CLI 3), without manual interruption: run
20260912T062127Z-d81914f3, 539 calls, USD19.77608556, 7661.68 seconds, 16/23
tasks accepted and 48/110 claims. No delivery; runs two and three did not start.
Campaign accounting including previous debug runs: USD22.56590952. Retain everything.
The generated project had successful agent-invoked smoke checks, but these are not
the final independent export gate and do not establish complete acceptance.

Cost diagnosis: 13,720,245 input and 420,546 output tokens; about 91.6% of cost is
input. There were 45 non-action responses. Several full finish responses omitted
the final outer closing brace; generic JSON extraction then returned their inner
arguments object, obscuring the syntax error behind a large generic schema error.
The final task repeatedly submitted partial claims instead of repairing its report.
Evidence also shows source reads using 80/90-character windows as if they were lines,
and an evidence-root listing that incorrectly searched the project directory.

Corrections within the existing design: reduce configurable actual-wire context
limit from 180000 to 60000 bytes (all task facts retained, older transcript trimmed);
require complete JSON actions in sessions with precise syntax location; validate
the selected action schema branch for useful missing-argument diagnostics; enumerate
missing/extra/duplicate claim IDs; show a complete finish envelope and explicit
character/line reading guidance. Fix read-only evidence-root resolution. No model,
task count, claim requirements, cost ceilings or oracle changed. Regression tests
cover malformed outer JSON, schema diagnostics, claim diagnostics and evidence root.
No edits to the authoritative design are required for these implementation/config
corrections. The next frozen batch starts from three entirely new projects.

Subsequent explicit user authorization on 2026-09-12 raises the per-run cost ceiling
from USD20 to USD100. Update the authoritative budget clause and configuration to
match this instruction. Campaign ceiling remains USD100, prior USD22.56590952 stays
counted, and the four-hour deadline remains. Available campaign funds are therefore
USD77.43409048, not a new USD100 allocation. Old snapshots and failed runs remain
unchanged. The prompt/context improvements above continue alongside this change.

The next user instruction explicitly raises the cumulative experiment ceiling to
USD300 as well. This supersedes the preceding USD100 campaign limit: current limits
are USD100/run, USD300/campaign, four hours/run. Historical USD22.56590952 remains
accounted, leaving USD277.43409048. Configuration validation and the authoritative
design now reflect both authorized increases; no historical record is rewritten.

Validation after the final budget changes: full non-live suite 110 passed / one
paid test deselected in 34.07 seconds; Ruff and mypy (all 29 production modules)
passed; wheel/sdist rebuilt. Added long-history tests exercise actual 60000-byte
request trimming while retaining complete current task facts. A test process that
had imported the earlier USD100 campaign validator was restarted after the live
user-authorized config update; only the fresh full run above is the final result.

## Context-mechanism root-cause correction

The user authorized all evidence-driven design changes during this refactor without
further per-change approval. Remote writes, existing user changes, cost ceilings and
the actual end-to-end acceptance boundary remain protected. Latest scheduling:
prove one complete real run first, then execute two independent stability repeats
on the identical frozen candidate; these two can run concurrently.

The fc170b1 parallel batch failed: two task-session exhaustions and one subsequent
user-authorized interruption, cumulative campaign USD49.02257712. See
session_context_failure_analysis.md for the exact runs and transcript audit.
Its one-time adoption scheduler is preserved byte-for-byte in that batch's
scheduler.py (SHA256 matches batch.harness_sha256); its completed adoption CLI has
been removed from the active test harness. The active harness gates repetitions
on the first run's full independent export verification, not only its CLI result.

Root fix implemented in agents/context.py and the existing session/workspace path:
versioned exact read observations are deduplicated and checked against actual file
hashes before requests; unchanged source survives command/build actions, changed
source is invalidated. Requests use complete action/result transactions and retain
the latest observed failure/check diagnostic. Older transactions may be evicted;
current facts/observations/latest feedback cannot silently disappear. Over-capacity
working sets cause an explicit error before further paid API calls. Repeated
sessions share valid observations without inserting unpaired user messages. This
is ephemeral model context, not another persisted project state or answer cache.

Read-only replay runs/_refactor/replay_failed_context.py executes the exact 120-step
failed SUBSCRIBE read sequence without API calls or code changes. At 60000 bytes,
the eighth step requires 62714 bytes and explicitly fails; at 180000, all 120 steps
fit, retaining eight observations with a 130855-byte peak request. This is context
retention evidence only, not a claim of autonomous generation success.

Regression coverage includes working-set retention, source hash invalidation,
deletion/symlink changes, JSON-pointer reads, latest compiler diagnostic retention,
capacity exhaustion, real compiler repair spanning three sessions, concurrent
campaign reservations, and first-success-before-repetitions scheduling. Full suite:
120 passed / one paid test deselected in 34.88s; Ruff and mypy passed all 30 production
modules. Model, complete input, serial task plan, budgets and oracle are unchanged.
Final pre-live rerun: 120 passed / one deselected in 33.97s. Wheel reinstalled in the
isolated package-test environment; new context module imports from site-packages,
and Spec/Target/Acceptance lint all pass from outside the source directory.
Original workspace status and research SHA256 were rechecked unchanged.

Candidate ebd3341 / batch e5e9fb02bb134fba86707b8372df9f4c / run
20260912T112242Z-56f67d18 stopped after 3566.52s with CLI 2 because the configured
DeepSeek endpoint returned HTTP402 on call278 (error response after 0.86s, not a
network hang or NePA budget limit). All 12 foundation/message tasks passed within
one session each; CONNECT used22 decisions and SUBSCRIBE23, compared with the
previous read-loop failures at120. This is progress evidence, not full generation
or a controlled model-success-rate comparison. requirements:001 was in progress;
no delivery and no independent final acceptance. Repetition runs were not launched.

This run accounts USD8.12165244, including an unresolved USD0.29382936 reservation
for the402 call. Historical cumulative accounting: USD57.14422956 out of USD300.
Do not clear reservations or reset prior costs. The provider adapter correctly
does not retry402. Its response body was not retained, so the exact account billing
condition is not proven solely by the recorded status. Restoring API availability
requires an external account action or a verified alternative provider config.
Alternative configured credentials are present, but their model prices are absent;
do not initiate unpriced paid generation or claim those accounts are usable.
After adding a402 regression (one attempt, reservation retained), full non-live
suite:121 passed / one paid test deselected in34.30s; Ruff and all30-module mypy
passed. No production code or prompt changed after candidate ebd3341.

Not complete. No post-refactor live success yet. Final conclusion must describe
three minimum-check successes, not all requirements or arbitrary protocols proven.

### Recharge continuation and V4.1 Flash routing (2026-09-12)

The user confirmed recharge and explicitly requested resuming the interrupted run,
using V4.1 Flash for faster tasks and V4 Pro for difficult work; first experimental
continuation may mix configurations. Official pricing was directly checked at
https://api-docs.deepseek.com/quick_start/pricing/ on 2026-09-12. The API name is
`deepseek-flash`, serving DeepSeek-V4.1-Flash, not `deepseek-v4.1-flash`.
USD per million tokens, Flash peak: cache-hit input0.006, cache-miss input0.30,
output1.20; off-peak0.003/0.15/0.60. Pro peak0.044/1.32/3.96,
off-peak0.022/0.66/1.98. Peak hours Monday-Friday01:00-04:00 and06:00-10:00 UTC.
The current official pricing page supersedes the September10 announcement's
planned Pro retirement: it now explicitly says Pro service and pricing continue.

Accounting retains conservative peak cache-miss rates for reservations and usage
estimates. Cache-hit and off-peak discounts are documented, not claimed as realized
savings or a provider invoice. Historical settled costs and unresolved reservations
remain unchanged. Default YAML selects Flash for initial bootstrap/message/requirement
sessions, Pro for shared-wire/integration/follow-up and retry/repair sessions.
Actual request model drives wire payload, pricing and context; tests cover all routes
and real compiler-repair escalation. No protocol names participate in selection.

The minimal affected path includes config, context/session, client, resume/store,
report and schema because model selection must propagate to actual billing and
explicit continuation must preserve evidence end-to-end. Resume configuration
changes are explicit and reasoned, preserve old state/report in immutable evidence,
and do not reset task attempts, creation time, call IDs, costs or checkpoints.
Run20260912T112242Z-56f67d18 remains the continuation target. Its first requirement
session was interrupted by402; its next session uses Pro as an existing retry,
then new ordinary task sessions use Flash. No generated source is manually edited.
Read-only authenticated GET /models returned HTTP200 and exactly deepseek-flash /
deepseek-v4-pro; no generation charge was initiated by this check. Regression suite:
130 passed, one paid test deselected,34.45s; Ruff and mypy all30 modules passed.
Resume launched from387955c, retaining the original run identity and time limit.
The opt-in harness now accepts NEPA_LIVE_FIRST_RUN for the explicitly requested
development continuation: it must already be successful and pass the same current
input/config/runtime/image, empty-root, call, export, clean-build and oracle checks
before launching two fresh projects. Reports distinguish that mixed-version first
run from the two stability samples. A failed first run never triggers repetitions.
