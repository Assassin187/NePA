## Why

M1-11 can atomically activate an F2/F3 candidate and route its migrated execution view, but NePA still has no production owner for post-activation evaluation, level-local circuit breaking, derived `revision_locked`, or the controlled degraded exit after revision paths can no longer make progress. M1-12 is the next serial work item and must close that bounded-execution boundary before M1-13 can study natural failures or any later work can enable production revision budgets.

## What Changes

- Add one deterministic post-activation evaluator that appends exactly one terminal `revision_evaluated` fact for an activated revision only after its affected obligation set has reached terminal evidence or no budget remains to continue.
- Judge resolution against the activation's original trigger signature and obligation/lineage anchors: successful acceptance of the same obligations and disappearance of the original predicate is resolved; the same predicate after one bounded affected-set traversal is ineffective; missing budget or validation evidence remains unresolved. Task-count changes, node deletion, denominator changes, or a lower blocked count alone never prove improvement.
- Derive attempted signature/level pairs, consecutive same-level gate rejections, independent F2/F3 exhaustion, level closure, and `revision_locked` entirely from the accepted revision ledger and frozen configuration. Do not add writable lock flags to Plan, State, or Run.
- Close only the exhausted/repeatedly rejected level: F2 exhaustion must not consume or close an otherwise applicable F3 route, and F3 exhaustion must not prevent an unrelated legal F2 route. A successful activation resets only its level's consecutive-rejection counter.
- Lock later revision activity after an ineffective activation or an F4/F5 diagnosis, and when every applicable revision route is closed; retain accepted code, evidence, attempts, and failure history without reopening F0.
- Continue any dependency/build-independent branch that can still pass its complete acceptance gate. When no such work remains, route static-valid unresolved execution, circuit breaking, or an unbuildable exhausted repair group through the existing `EXECUTION_UNRESOLVED` controlled exit and S9 degraded result.
- Enforce global time/cost and S6 total-call exhaustion before any further Agent/Coder/Fixer call, persist the existing termination request, and finish through the existing S9 path without adding a new stage, CLI switch, configuration key, retry, or fallback.
- Extend the existing protocol-neutral revision fixtures and `revision_mechanism` acceptance matrix with evaluation, circuit-breaker, global-budget, group-termination, resume/idempotence, and independent-branch cases.
- **Out of scope:** M1-13 root-cause sampling or parameter selection; M1-14 PlanReviser prompt/call/calibration; M1-15 production enablement or full runs; TR-9 execution; S7/S8/M2 behavior; public CLI changes; new configuration; historical-run conversion; edits to `project_docs/`, prompts, calibration lineage, or archived changes.

## Capabilities

### New Capabilities

- `revision-circuit-breakers-and-controlled-degradation`: Terminal revision evaluation, ledger-derived level/global closure, bounded escalation, independent-branch continuation, and the controlled degraded exit when revision-backed execution cannot progress.

### Modified Capabilities

- `plan-revision-infrastructure`: Turn the existing `revision_evaluated` ledger shape into a unique, idempotent, activation-bound runtime fact and validate its ordering, anchors, evidence, and terminal semantics.
- `revision-gates-and-activation`: Make RG-1 admission and RG-3 level allowance consume ledger-derived attempted/closed/locked state while preserving independent F2/F3 budgets and reset rules.
- `s6-f0-execution`: Evaluate activated revisions at the authoritative S6 boundary, preserve independent work, stop all calls at hard global limits, and use the existing controlled degraded exit when no legal progress remains.

## Impact

- **Milestone/work item:** M1-12 only. The prerequisite is the archived `m1-11-revision-gates-and-atomic-activation` change; its superseding acceptance record contains the responsible owner's dated approval and reports 783 full tests plus strict OpenSpec acceptance. No active change currently overlaps this work.
- **Design authority:** `project_docs/system_design.md` §4.7, §4.8, §5.2.4, §5.6.7, §6.6, §9.1.2, §9.1.4, §10.2.1-§10.2.2, §10.8 and D1.10/D1.13/D1.16; `project_docs/pipeline_design_s4_s9.md` §4.3-§4.4, §5.6, §6.1.1, §6.4-§6.5, §7.1, §7.3-§7.4 and §9.
- **Affected paths:** revision event/state projections in `nepa/speclib/plan_revision.py` and `nepa/speclib/revision_mechanism.py`; the existing S6 handoff, group and task-boundary loop in `nepa/stages/s6_execution.py`; existing global-budget/termination routing in `nepa/orchestrator.py`; closed ledger examples, protocol-neutral fixtures, and focused tests. The cross-cutting paths form one end-to-end control loop and cannot be safely split without leaving evaluation facts disconnected from call admission or controlled exit.
- **Interfaces/artifacts:** no public API or CLI change and no expected revision-ledger version bump. The existing v2 `revision_evaluated` payload gains its production append/validation semantics; level closure and `revision_locked` remain internal pure projections, not persisted mutable fields. Existing `code-generation-metrics` requirements and formulas remain unchanged and are regression-tested against the new producer.
- **Upstream/downstream:** M1-11 activation and M1-8/M1-9 materialization/migration execution remain the only producers of the execution evidence being evaluated. Production F2/F3 defaults remain 0/0; M1-13, M1-14 and M1-15 remain separate work items.
- **Acceptance:** `uv run pytest -q -m revision_mechanism` must cover the M1-12 row in §10.2.2, including F2-full/F3-legal, ineffective revision, unbuildable-group termination and an independent branch. Run the affected Schema, S5/S6, RunStore, budget, termination, resume, metric and public-contract modules, then run the complete §10.8 public CI including unselected repository-wide `uv run pytest -q`. Retain ruff, mypy, public lint, strict current/all OpenSpec validation and diff hygiene. A responsible owner must review the final implementation and acceptance package; machine checks cannot substitute for that decision.
