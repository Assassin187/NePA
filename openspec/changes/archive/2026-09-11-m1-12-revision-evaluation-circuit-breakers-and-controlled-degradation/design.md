## Context

See `proposal.md` for motivation. M1-11 ends with an accepted `revision_activated`, an active successor pointer and an S5/S6 execution view produced by the existing M1-8/M1-9 paths. The v2 ledger Schema already reserves `revision_evaluated`, and the metric calculator already consumes such events, but no runtime path appends or semantically validates them. Current trigger admission derives simple rejected/activated signature sets but does not derive two-rejection level closure, independent F2/F3 exhaustion or a durable-on-resume global lock.

The existing S6 controller already owns task/group selection, migration execution and `EXECUTION_UNRESOLVED`; the orchestrator already owns global wall-clock/cost synchronization, `termination_request`, S9 entry and outcome/exit-code finalization. M1-12 must complete those existing paths rather than create another stage, ledger, controller or reporting system. The design authority and exact boundaries are the sections listed in `proposal.md`; production revision limits remain 0/0.

This work spans ledger semantics, revision admission, S6 execution and controlled termination, so a design artifact is required. Implementation must begin with the §10.8 implementation brief and must use protocol-neutral frozen/stub fixtures rather than real models or M1-13 data.

## Goals / Non-Goals

**Goals:**

- Produce one auditable terminal result for each accepted activation before another revision can be admitted.
- Make attempted pairs, per-level rejection/activation closure and the run-wide revision lock deterministic functions of accepted history.
- Preserve independent F2/F3 allowances, existing execution evidence and any independently verifiable branch.
- Stop model activity at every hard budget and finish static-valid unresolved execution through the existing S9/degraded contract.
- Extend the current M1-10/M1-11 fixture and test path without protocol-specific production branches.

**Non-Goals:**

- No PlanReviser invocation, prompt development, natural-failure study, threshold/limit selection or production enablement.
- No new Run stage, public command/option, configuration field, mutable lock flag, alternate ledger, retry budget or historical format converter.
- No S7/S8/TR-9/M2 implementation, architecture-prompt change, design-document edit or modification of an archived change.

## Decisions

### 1. Use one pure revision-availability projection

Add one pure projection over a validated v2 revision ledger plus the frozen `budgets.revision_f2_limit` and `budgets.revision_f3_limit`. Its result contains:

- attempted `(trigger_signature, level)` pairs resolved through each rejection's referenced trigger event and each activation's bound signature;
- successful activation counts for F2 and F3;
- one rejection streak per level, obtained by scanning that level's rejection/activation events while ignoring the other level, resetting only on a same-level activation, and closing after two different-signature rejections;
- `level_closed[F2/F3]`, true when that level reaches its activation limit or rejection breaker;
- `revision_locked`, true after any accepted ineffective evaluation, any accepted F4/F5 diagnosis, or closure of both supported levels;
- the accepted activation, if any, that still awaits terminal evaluation.

The projection does not write Plan, State, Run or a sidecar file. All call sites use the same result for trigger selection, RG-1, RG-3 and S6 termination.

Alternative rejected: persist `revision_locked` and rejection counters in Run or State. They would duplicate append-only evidence, create new crash windows and permit resume drift.

### 2. Freeze an activation's affected obligations from accepted immutable inputs

For an unevaluated activation, construct the affected set from the immutable from/to Plans, the activation's patch operations and migration rows:

- include each task directly targeted by a patch operation;
- include every migration row not classified `INHERIT` and every registered pending-group member;
- follow explicit split/merge mapping and the current Plan dependency/contract closure needed to validate those members;
- represent each anchor canonically as the original obligation digest plus its explicit old/new uid lineage, sorted by canonical bytes.

The selected trigger event and activation signature remain the problem identity. A task id/title, current task count, evidence timestamp or path name is never an anchor. Missing or ambiguous immutable Plan/lineage evidence is artifact damage rather than an empty affected set.

Alternative rejected: use the current blocked task list or every task in the trigger boundary. The former changes merely by migration status; the latter would make unrelated work delay or influence the evaluation.

### 3. Evaluate only at a stable transaction-free boundary

Before ordinary trigger selection at each S6 boundary, check for one unevaluated activation. Evaluation is ready only when no activation/materialization/verification/attempt/lease/group transaction is in flight and either:

- every affected successor obligation has accepted terminal State plus the evidence required by its execution mode; or
- synchronized global time/cost or the S6 total-call cap proves that remaining affected validation cannot start.

Recompute the original trigger predicate using the existing M1-10 fact projector over the same stable signature/anchor domain. The result table is fixed:

- predicate absent and every anchor has accepted success proof: `resolved=true, ineffective=false`;
- one bounded affected-set traversal is terminal and the same signature persists: `resolved=false, ineffective=true`;
- a hard budget ends the traversal without sufficient terminal evidence: `resolved=false, ineffective=false`.

`evaluated_at` is a deterministic evaluation-boundary identity derived from revision sequence, accepted State-history snapshot, relevant ledger prefix and terminal reason, not wall-clock time. Once evaluation is ready, S6 publishes that exact history value to `plan/revision_evaluations/revision_<revision_seq>/state_history.json` and uses the resulting immutable ref; publishing happens before the ordinary ledger append, is byte-idempotent on resume, and conflicting bytes are artifact damage. Evidence refs bind the selected trigger, activation and terminal task/group/State facts. Call refs are deduplicated stable trace `output_path` references associated through candidate, critic, activation, group and verification identities; `cost_usd` is their exact sum. Zero actual cost is valid and remains a metric-side `ZERO_COST_DENOMINATOR`, not an evaluation failure.

The affected successor uid set and its anchors are produced by one canonical obligation-scope projection and passed unchanged into readiness, terminal evaluation and evidence collection. S6 does not reconstruct a smaller set from migration rows. After dependency blocking is propagated, S6 processes pending evaluation again before no-task exit or finalization. The boundary identity includes the validated ledger-prefix hash immediately preceding evaluation.

Alternative rejected: evaluate immediately after activation or after the first affected member. Pending migration status and partial group work are not effectiveness evidence.

### 4. Append one ordinary, idempotent `revision_evaluated` event

Extend the existing v2 ledger validator and append helpers without changing the ledger version. Validation requires a prior activation with the same positive `revision_seq`, one evaluation maximum per revision, a canonical non-empty anchor set and legal truth combinations. An evaluation advances only `event_seq`.

The append runs under the existing controller lock as an ordinary observation event. A byte-equivalent replay returns the current ledger; a different boundary, anchors, truth value, evidence, calls or cost for the same revision fails as artifact damage. Because all payload inputs are already accepted immutable refs or canonical state-history facts, resume after a pre-append crash recomputes the same bytes. No separate evaluation WAL or mutable sidecar is introduced.

The v2 semantic validator rejects a new activation while the prior activation lacks its unique terminal evaluation. The availability projection fails closed if malformed history nevertheless exposes more than one pending activation.

Alternative rejected: add a new WAL. The append has no external side effect or multi-file semantic commit, so the existing atomic ledger replacement is the documented transaction boundary.

### 5. Gate selection and activation budgets consume the same projection

Run the projection before selecting a trigger, staging a candidate or entering RG-1/RG-3. A rejected pair cannot repeat; if the same signature has an applicable untried higher level, it may be selected. A signature with an activation awaiting evaluation cannot start another cycle. Once an evaluation is ineffective or a recorded F4/F5 diagnosis is accepted, no later F2/F3 candidate is admitted.

RG-3 counts only successful activations at its own level. F2 closure never subtracts from F3 and vice versa. Re-read synchronized global usage and S6 call capacity at the existing pre-call and pre-activation boundaries; a hard global exit supersedes candidate continuation without refunding evidence or usage already accepted.

Alternative rejected: close all revision activity when either configured level reaches zero/exhaustion. That contradicts the M1-12 requirement that F2-full/F3-legal remains executable.

### 6. Keep branch continuation inside the existing S6 scheduler

After task or group exhaustion, preserve the accepted baseline, terminal State and failure evidence, then propagate dependency blocking with the existing Plan graph. Before selecting a nominally independent task, additionally prove that every default build artifact and runtime smoke prerequisite needed for that task remains runnable without the failed group. Selection still uses the existing stable order and the task's unchanged budgets.

If the whole default build is broken or no fully verifiable branch remains, do not invoke another Agent merely because a task-DAG edge is absent. Complete any ready terminal revision evaluation, then raise the existing controlled S6 failure with `EXECUTION_UNRESOLVED`.

Alternative rejected: create a separate partial-build execution mode. The design requires each continued branch to receive its complete existing acceptance, not a weaker new gate.

### 7. Reuse the current controlled-exit and S9 transaction

Global wall-clock/cost remains enforced by the orchestrator before external calls and synchronized after calls. S6 total coding/fixing calls remain atomically consumed by the current allocation path, but S6 checks remaining capacity before selecting a call and translates exhaustion into the controlled path instead of an internal validation error.

For a global budget, persist the existing budget reason; for static-valid locked/unbuildable/no-progress execution, persist `EXECUTION_UNRESOLVED`. The orchestrator retains the existing order: termination request and failed/pending stage projection, S9 with `enforce=false`, Schema-valid partial report, then degraded/10. `--until s6` cannot override a request, and resume completes the request before any stage admission. Static-contract or artifact-chain damage remains failed/20; implementation/invariant bugs remain internal_error/1.

`LLMClient` remains the single production owner of post-response usage registration for Agent calls. S6 performs pre-call admission but never charges the returned response again. At the evaluation boundary, only validated artifact/ref conflicts are translated to the existing `S6_ADMISSION_INVALID` failed/20 route; unexpected exceptions continue to surface as internal errors.

Alternative rejected: finalize degraded inside S6. That would bypass the single S9 report producer and duplicate outcome logic.

### 8. Keep metrics as an existing downstream consumer

Do not add a `code-generation-metrics` delta requirement. Its main spec already defines effectiveness, ineffective count, cost association and unavailable/zero-cost behavior. Update implementation only where needed to consume the Schema-valid production event identity (`revision_seq`) and add regression cases proving duplicate/conflicting terminal evaluations cannot distort results.

MQTT and non-MQTT M1-10/M1-11 fixtures gain the same evaluation/closure/termination scenario descriptors and source hashes through the existing generator. Core code must not inspect protocol names or fixture paths.

Evaluation evidence and cost are associated through accepted candidate/critic, activation, migration attempt, group and verification identities. For an activation-bound attempt, the accepted call is the last canonical S6 trace row for its `(task_id, attempt)` at that stable boundary; this matches the existing `_call_refs_for_attempt` producer rule and excludes an earlier pre-activation row that reused the reset attempt number. When call telemetry is supplied, its unique output-path costs must agree with the ledger payload even when the payload is zero; when telemetry is absent, `revision_evaluated.cost_usd` remains authoritative rather than becoming a false zero.

## Risks / Trade-offs

- **[Affected-set construction omits a changed obligation]** → Derive it from patch targets, all non-INHERIT migration rows, pending groups and explicit lineage/dependency closure; reject missing immutable mappings.
- **[Resume produces a second or different evaluation]** → Build the boundary identity and payload only from accepted refs/history, enforce one event per revision sequence and conflict-close replays.
- **[A closed F2 accidentally suppresses F3]** → Maintain separate counters/streaks and cover both cross-level directions with fixtures.
- **[A nominally independent task cannot pass the shared build]** → Require complete build/runtime independence before invocation; otherwise take the controlled exit.
- **[Budget exhaustion is surfaced as internal_error]** → Add explicit pre-selection/pre-allocation checks and use the existing orchestrator termination contract.
- **[M1-12 drifts into calibration or production enablement]** → Keep defaults 0/0, use frozen/stub inputs, scan the final diff for PlanReviser, prompt, M1-13/14/15 and public CLI changes.

## Migration Plan

1. Add and validate the pure projection and strict evaluation-ledger semantics against synthetic v2 histories.
2. Integrate terminal evaluation before trigger selection, then make trigger/gate admission consume the shared projection.
3. Close S6 independent-branch, group-exhaustion and hard-budget routing through the existing controlled-exit path.
4. Extend protocol-neutral fixtures and focused tests; run the complete `revision_mechanism` marker plus the affected Schema, S5/S6, RunStore, gate, activation/recovery, budget/termination/resume, metric and public-contract modules.
5. Run the complete §10.8 public CI including repository-wide pytest, ruff, mypy, public lints, strict OpenSpec validation and diff hygiene; publish the exact M1-12 acceptance record and obtain responsible-owner approval before archive.

There is no data migration or rollout toggle. M1-12 supports fresh-run v2 artifacts only, preserves production revision defaults at 0/0 and adds no conversion for historical runs.
