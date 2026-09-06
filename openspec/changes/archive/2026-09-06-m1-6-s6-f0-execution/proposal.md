## Why

M1-5 now produces a sealed, buildable and smoke-clean `ready` E0 workspace, but NePA still cannot execute the Plan's ordinary coding tasks or publish task completion evidence. M1-6 is the next serial work item and must close the S6 F0 execution, persistence and recovery loop before F1 leases, multi-epoch migration or revision mechanisms can consume real task results.

## What Changes

- Add S6 admission and reconciliation over the active Plan, ready E0 binding/receipt, exact workspace checkpoint, configuration snapshot and execution ledgers; initialize Plan State exactly once only from an untouched E0 checkpoint.
- Add the production protocol-neutral Coder/Fixer context and closed full-file output contract. The first normal attempt uses Coder, later attempts use Fixer, attempts 1-3 use T2 and attempt 4 uses T1, and every started call consumes the persisted task and run-level allowance before provider I/O.
- Add deterministic task selection, dependency blocking, candidate staging, write-whitelist and declaration checks, default build variants and smoke acceptance. M1 continues to expose no Test Bundle implementation, runner, oracle or task tests.
- Preserve every failed candidate with matching build/smoke diagnostics and optional Diagnoser evidence, restore the accepted execution baseline after failure, and feed the latest matching candidate and diagnostics to the next Fixer attempt.
- Publish immutable per-attempt and successful task evidence, one normal-task git commit with exact trailers, Plan State/file-ledger updates and typed execution events through a crash-recoverable commit-before-State transaction.
- Exhaust ordinary F0 after `3×T2 + 1×T1`, propagate only proven dependency blocks, keep revision and F1 lease routing disabled, and terminate unresolved static-valid execution as controlled `degraded` rather than claiming completion.
- Add the final all-variant build, smoke and execution-state gate, immutable S6 receipt and Run output references. `run --until s6`, `resume`, `status` and Plan/Plan-State lint shall expose the delivered path with the specified exit-code semantics and no new CLI parameters.
- Add frozen-provider and sandbox-backed `pytest.mark.s6_execution` fixtures for success, full-budget failure, failed-candidate reuse, clean dependency propagation, commit-before-State recovery, exit smoke failure and zero-change completed replay.
- **Out of scope:** F1 neighbor leases and metrics (M1-7); E1+ materialization (M1-8); AMEND/REVALIDATE/repair groups (M1-9); trigger, PlanReviser, activation and circuit-breaker behavior (M1-10 onward); M2 test assets or S7; production model/parameter qualification; and edits to `project_docs/` or the selected ArchitecturePlanner lineage.

## Capabilities

### New Capabilities

- `s6-f0-execution`: Ready-E0 admission, ordinary Coder/Fixer task execution, build/smoke acceptance, task evidence and commits, S6 receipt, deterministic recovery, controlled exhaustion and the M1 CLI surface.

### Modified Capabilities

- `agent-invocation-runtime`: Bind Coder and Fixer to their production S6 context package, closed full-file response contract, role/attempt routing and protocol-neutral visibility boundary.
- `plan-state-validation`: Complete the ordinary F0 State/event/evidence semantics, persisted attempt allocation, file-ledger realization and commit-before-State reconciliation consumed by S6.
- `plan-revision-infrastructure`: Add the first S6 producer of the existing typed `verification_committed` ledger event and immutable evidence sequence allocation; revision generation and activation remain disabled.

## Impact

- **Milestone/work item:** M1-6 only. Governing sections are `project_docs/system_design.md` §4.7-§4.8, §5.2.4-§5.2.5, §5.5, §5.6.7, §6.6, §8.6-§8.8, §10.2.1-§10.2.2 and `project_docs/pipeline_design_s4_s9.md` §5.5-§5.6, §6.5 and §7.1.
- **Prerequisite status:** M1-5 is archived as `2026-09-06-m1-5-s5-e0-materialization`; implementation must verify its ready-E0 receipts, checkpoint and focused regressions rather than reopen or reinterpret the completed S5 design.
- **Affected paths:** stage/orchestrator/RunStore and CLI wiring; Plan State, file/revision ledger, attempt/task/S6 evidence and receipt Schemas; Coder/Fixer registration, prompts and context construction; sandbox build/smoke and git boundaries; frozen S6 fixtures and CI tests.
- **Dependencies:** reuse the M1-2 logical-completion runtime, M1-3 Agent framework, M1-4d Plan State/revision infrastructure and M1-5 materialization/sandbox/git path. Add no runtime dependency unless a concrete design-mandated capability is absent.
- **Frozen/public behavior:** sealed Plan/Spec/Target/Test Bundle/Blueprint/E0 bytes remain immutable; Coder/Fixer see only the defined structured context and never test implementation. S4/S5 semantics, protocol-neutral templates and existing ArchitecturePlanner prompt lineage remain unchanged.
- **Acceptance:** `uv run pytest -m s6_execution`, sandbox-backed success/failure/recovery scenarios, full public CI, strict current/all OpenSpec validation and `git diff --check`. M1-6 has no independent owner signature gate and does not claim the later production-parameter or comprehensive M1 acceptance gates.
