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

Not complete. No post-refactor live success yet. Final conclusion must describe
three minimum-check successes, not all requirements or arbitrary protocols proven.
