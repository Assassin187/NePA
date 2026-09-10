## Context

See `proposal.md` for motivation. M1-10 ends with an accepted `trigger_evaluated` selection, an immutable event-scoped candidate under `plan/_s4r/candidate_<event_seq>/`, and `StagePause(kind="revision_handoff", selected_event_seq, candidate_ref)`. The candidate already contains the completed Plan, Blueprint/manifest/map projections, invariant/lint evidence, migration report and exact source hashes, but it is deliberately non-authoritative. M1-8 already consumes an accepted F3 activation to materialize E1+, and M1-9 already consumes an accepted migration to perform REVALIDATE, AMEND, REGENERATE and affected-group verification.

The dormant M1-4d activation helpers and `plan-activation.schema.json` do not yet implement the authoritative M1-11 gate-to-activation transaction. In particular, the current main OpenSpec recovery prose still contains a ledger-before-pointer forward-recovery branch; `project_docs/system_design.md` §5.6.7 and the pipeline design §6.4 instead make `active_plan.json` the sole commit authority and require rollback whenever it remains old. This change corrects the delta spec and implementation to the authoritative rule without editing either design document.

M1-11 spans the revision mechanism, Agent boundary, S5/S6 routing, Run projection and several persistent artifacts, so a design artifact is required. Production revision limits remain 0/0 until later calibration; M1-11 proves the mechanism with explicit fixture configuration and stub/frozen PlanCritic output.

## Goals / Non-Goals

**Goals:**

- Close the one existing M1-10 handoff with a single ordered RG-1 through RG-5 controller and a single rejection-or-activation result.
- Reuse all delivered Plan completion, migration, S5 multi-epoch and S6 verification paths rather than create alternate planners, materializers or executors.
- Make candidate rejection and successful activation idempotent, hash-bound and crash-recoverable under the existing run lock.
- Keep every accepted semantic fact in formal Plan/version/binding/State/ledger/Run artifacts; keep `_s4r` as process evidence only.
- Demonstrate D1.13's remaining gate/activation mechanism with protocol-neutral fixtures and all §5.6.7 activation windows.

**Non-Goals:**

- No automatic PlanReviser producer, prompt, correction loop or calibration; the input candidate remains the frozen M1-10 output.
- No `revision_evaluated`, effectiveness, circuit breaker, level-closing, degradation policy or independent-branch policy from M1-12.
- No new public CLI parameter, S7/S8 behavior, generated test asset, architecture-prompt change, historical-run converter or design-document edit.
- No retry after RG rejection at the same `(signature, level)` and no alternate critic/planner call after RG-4 failure.

## Decisions

### 1. Compose one revision controller into the existing S6 handoff

Add one internal revision gate/activation component, invoked by `S6ExecutionController` when the accepted M1-10 handoff is replayed. It receives `StageContext`, the selected event sequence and candidate ref; it returns one of `rejected`, `activated_f2`, or `activated_f3` with the accepted refs needed by the caller. It does not become a new top-level Run stage or public controller registration.

The controller first calls the existing candidate reconciliation/readers, then verifies selected-event ancestry and all current Plan/State/ledger/workspace/binding hashes. Before gate admission it invokes the existing global transaction reconciliation order: activation, current epoch materialization, verification. S6 remains `pending` throughout the handoff; it is never sealed before the new view finishes.

Alternative rejected: adding an `s4r` Run stage. Run v4 has only the documented S1-S9 lifecycle, while S4R is an internal revision path; a new stage would change public Run semantics without design authority.

### 2. Persist one closed gate result and one optional rehearsal record

Introduce `plan/_s4r/candidate_<event_seq>/gates.json` with a new closed Schema. It binds candidate/trigger/source refs, level, evaluation-boundary hashes, ordered gate rows, `first_failed_gate`, critic evidence/usage refs, rehearsal ref and final disposition. Gate status is `pass`, `fail`, `not_applicable` or `not_evaluated`; only RG-5/F2 may be `not_applicable`, and every gate after the first failure must be `not_evaluated`. The file is immutable once a rejection or activation WAL is prepared; byte-equivalent replay reuses it and conflicting bytes are artifact damage.

Introduce `plan/_s4r/candidate_<event_seq>/rehearsal.json` for F3 only. It binds the accepted baseline commit/tree and candidate Plan/Blueprint/migration, both isolated rehearsal tree results, build/smoke refs, file-difference and quarantine/re-adoption rows, registered group attribution and the final RG-5 verdict. F2 has no rehearsal file.

Upgrade the activation WAL contract to `schema_version="2.0"` because its required shape changes incompatibly. It contains the complete old/new values and refs for pointer, State, file ledger, revision ledger, Run active reference and current copies, candidate Plan, F2 binding or F3 pending-materialization intent, immutable gate/rehearsal refs, content hashes and a transaction phase. M1-11 supports fresh-run v2 activation WALs only and adds no v1 conversion.

### 3. Evaluate gates once, in strict order, from recomputed facts

RG-1 re-runs the existing M1-10 predicate/selection logic at the same boundary and validates that the accepted ledger still permits this `(signature, level)`. RG-2 reuses candidate validation, INV-1/2/3, Plan/Linker/full lint, Blueprint and migration validators; F3 additionally reruns only the candidate-declared affected architecture closure. Passing stored M1-10 reports are evidence inputs, not substitutes for revalidation.

RG-3 extends configuration with the authoritative keys:

- `budgets.revision_f2_limit` and `budgets.revision_f3_limit`, non-negative, default 0 and capped by validation at 3/1;
- `revision.rho_min_f2` and `revision.rho_min_f3`, each in `[0,1]`;
- `revision.cost_rates.build_usd`, non-negative;
- existing `revision.theta2/theta6` remain required with the new fields whenever either revision limit is nonzero.

An absent `revision` object is legal only when both limits are zero. Checked-in mechanism fixtures explicitly use trial values, including a build rate of 0 when build resources are not charged; no suggested value is silently supplied. Rework cost is recomputed from migration execution units as defined by pipeline §3.4 using frozen role pricing/token bounds plus the explicit per-build rate. The gate independently checks preservation, `estimate <= remaining_global_cost * 0.5`, the applicable successful-activation count, and remaining `s6_total_attempts_cap`; no rejected activation count is consumed and no historical usage is reset.

RG-4 reuses `PlanCriticContractBinding`, its existing output Schema, route and temperature rule. The `candidate_plan_graph` input becomes the candidate delta plus complete affected contract/task/test closure; coverage and lint stay the other two declared inputs, so the Agent runtime shape does not fork. Initial S4 continues passing the complete compact graph. Tests inject the existing stub Agent/frozen output seam; production code still goes through `AgentInvoker` and records actual usage.

Alternative rejected: trusting M1-10 reports or Agent assertions. Gate inputs can become stale, and PlanCritic is only the RG-4 semantic reviewer; deterministic gates and budgets remain controller-owned.

### 4. Rehearse F3 through the existing S5 algorithm in isolated workspaces

RG-5 creates two fresh temporary workspaces from the same accepted HEAD and runs the existing multi-epoch planning/rendering/difference/build/smoke primitives against the candidate without publishing a git commit, receipt, ledger or Run change. The controller compares the two canonical result projections and git trees. It accepts `pending_repair` only when every failing build artifact/path/symbol is completely covered by a migration `pending_group`; an unregistered, partial or smoke failure rejects the candidate. Existing realized S6-owned files must retain bytes unless the migration explicitly retires/re-adopts them.

The temporary roots are discarded after their result refs are durably copied into the candidate area. The live workspace and its HEAD/status are checked before and after rehearsal. F2 records RG-5 as `not_applicable` without calling S5.

Alternative rejected: a second dry-run renderer. It would duplicate the exact logic D1.12 is intended to protect and could pass a candidate that the live S5 path rejects.

### 5. Reject at the first failed gate with one ordinary ledger append

The controller builds the closed `candidate_rejected` payload from the gate result and appends it atomically under the run lock using the existing typed-ledger append path. Its deterministic identity is the candidate id/trigger event/level already constrained by the ledger. Byte-equivalent replay returns the accepted event; any different failed gate, reason or refs for the same candidate fails closed.

Before and after the append, the controller asserts that active pointer, formal version paths, State, file ledger, Run active ref, binding/current copies and workspace HEAD/tree are unchanged. Rejection evidence remains in `_s4r`; consumed critic/build usage remains in Run/trace. S6 is left pending on the original view and a repeated same-level candidate is prevented by M1-10 deduplication.

### 6. Prepare one complete F2/F3 successor transaction

After all applicable gates pass, reuse `successor_pointer`, `project_plan_state`, `project_file_ledger`, revision-ledger builders and existing binding projections to compute every byte before publication.

- F2 increments P and `revision_seq`, preserves epoch, creates an immutable binding under `plan/bindings/<version>/receipt.json` referencing the existing epoch receipt plus candidate-specific immutable manifest/map, and projects migration modes/groups without changing workspace bytes or creating an epoch commit.
- F3 increments A/epoch, resets P, increments `revision_seq`, sets activation `binding_ref=null` and `pending_materialization=true`, projects the new State/file ledger for the new Plan, and prepares Run so the new S5 instance is pending while historical epoch/binding receipts remain immutable.

The `revision_activated` payload is built from the same values and records all gate outcomes, patch operations, migration rows, preservation/rework estimate, current HEAD as `activated_at_commit`, and the level-specific binding state. No post-activation verification or effectiveness fact is inserted.

### 7. Make pointer replacement the only commit point

Under the run lock, publication order is:

1. write and fsync the complete v2 activation WAL;
2. publish the immutable successor Plan and, for F2, immutable binding/manifest/map;
3. atomically replace migrated State, file ledger and the pre-commit revision ledger containing `revision_activated`;
4. atomically replace `plan/active_plan.json` — the sole logical commit;
5. update only `run.stages.s4.output_refs.active_plan`, publish the correct current manifest/map copies, set the F3 S5 instance projection pending when applicable, and leave S6 pending;
6. mark the WAL reconciled without rewriting any accepted semantic event.

Every fault boundary receives a stable injection point. No downstream reader may trust prewritten new bytes until activation reconciliation sees the new pointer.

Alternative rejected: treating revision-ledger append as commit. The authoritative design explicitly requires pointer-old rollback even if the new ledger was already written.

### 8. Reconcile by pointer state before every stage admission

The RunStore-level reconciliation entry examines each unreconciled activation WAL under the same lock:

- Pointer equals old: verify the WAL and old refs, restore the exact old State/file ledger/revision ledger/Run active ref/current copies, and move any unactivated formal Plan/F2 binding into an immutable isolation subtree within the candidate directory. This rule applies before or after the pre-commit ledger replacement.
- Pointer equals new: verify all new bytes and refs, never roll back, and forward-complete only missing Run/current-copy/F3-pending projection work.
- Pointer equals neither, WAL is absent, or bytes/hashes/chains/version/epoch disagree: raise artifact damage and leave accepted state untouched.

Recovery then routes F2 to S6 migration execution or F3 to S5. The existing materialization and verification reconcilers run only after activation is settled, preserving the §5.6.7 transaction order.

### 9. Preserve protocol neutrality and acceptance boundaries

Use the M1-10 MQTT and non-MQTT candidates and their existing generators as the base. Add deterministic gate/rehearsal/activation fixture layers through the same core code path; do not branch on protocol, path vocabulary, model or provider. `pytest -m revision_mechanism` must contain nonzero cases for every RG failure, F2/F3 success, exact replay/conflict behavior, and fault injection before/after WAL, version/binding, State, file-ledger, revision-ledger, pointer and Run/current-copy publication.

The implementation acceptance record must map each requirement to fixture, artifact and test evidence, run all §10.8 checks, and state that M1-11 completes only the mechanism portion of D1.13. Responsible-owner review is an explicit final task and remains unchecked until an external dated decision names the final diff and evidence.

## Risks / Trade-offs

- **[Cross-file rollback accidentally overwrites accepted history]** → WAL carries complete old bytes/hashes, rollback is allowed only while pointer equals old, and conflicting bytes fail instead of being guessed.
- **[F3 rehearsal diverges from live S5]** → call the same deterministic materialization primitives twice from the same baseline and compare canonical output/tree projections; no parallel renderer is introduced.
- **[Critic or build cost is undercounted]** → charge actual Agent usage immediately through the existing orchestrator boundary and recompute RG-3 from frozen pricing/token/build inputs before the call and again before activation publication.
- **[Run stage rewind loses prior epoch evidence]** → preserve immutable epoch/binding receipts and change only the current S5 projection for F3; F2 never rewinds S5.
- **[Main spec recovery text masks the real commit point]** → the delta fully replaces both activation/recovery requirements and tests the pointer-old-with-new-ledger window explicitly.
- **[Scope drifts into M1-12 or M1-14]** → source/fixture audits reject new `revision_evaluated`, effectiveness/circuit-breaker, PlanReviser registration/prompt/call, public CLI or production-enabled default behavior.

## Migration Plan

1. Verify the archived M1-10 acceptance and current baseline; create the §10.8 implementation brief before code changes.
2. Add closed Schema/examples and disabled-by-default configuration fields, then validate all existing fresh-run fixtures unchanged.
3. Add pure gate/rework calculations and PlanCritic delta projection, followed by isolated F3 rehearsal and rejection append.
4. Add v2 activation preparation/publication and pointer-driven recovery, then connect the existing S6 pause to F2/F3 downstream routes.
5. Regenerate only affected checked-in fixtures in dependency order, run focused/predecessor/full CI, write the acceptance record, obtain owner review, and rerun final acceptance after any correction.

Rollback during development is ordinary source rollback before any production enablement; runtime recovery follows Decision 8. Historical accepted runs are not migrated, and default F2/F3 limits remain zero.
