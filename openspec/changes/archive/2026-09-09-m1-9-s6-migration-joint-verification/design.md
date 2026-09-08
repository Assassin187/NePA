## Context

See `proposal.md` for motivation and scope. The current repository has one S6 controller for ordinary F0 work and F1 leases, one `plan/verification_pending.json` transaction, Task/Joint Evidence v2, prepared ordinary/joint Git commits, Plan-State transition/execution lint and typed verification events. Those paths are currently specialized around ready 1.0.0/E0, normal attempts and lease-kind joint results. The committed and archived M1-8 baseline adds current-version bindings, E1+ receipts, canonical pending groups and `pending_repair`, but deliberately stops before S6 migration execution.

M1-9 consumes an already accepted artificial F2/F3 activation and its frozen migration rows. It does not make activation reachable. The authoritative behavior is fixed by `project_docs/system_design.md` §5.2.4, §5.4, §5.6.7, §6.6 and §10.2.2 plus `project_docs/pipeline_design_s4_s9.md` §5.6.1 and §6.4-§6.5. Existing code that requires successful proof while projecting REVALIDATE, rejects AMEND at the ordinary-attempt limit, fixes Task Evidence to 1.0.0/E0, or limits joint transactions to lease is an implementation gap, not an alternate design.

## Goals / Non-Goals

**Goals:**

- Extend the existing S6 path end to end from current binding admission through migrated State, mode-specific execution, evidence/commit publication, external lint and resume.
- Make one frozen group the transaction and validation boundary while retaining per-task budgets, files, evidence sequences and migration lineage.
- Preserve ordinary F0/F1 behavior and every accepted prior epoch/version/evidence byte.
- Prove D1.15 with checked-in MQTT and non-MQTT fixtures and faults at every verification boundary.

**Non-Goals:**

- Do not produce triggers, revision patches, RG gates, activation, evaluation, circuit breakers or production tuning.
- Do not add an Agent role, group-wide free-form prompt, public CLI option, M2 test implementation or compatibility migration for old runs.
- Do not modify S5 materialization rules, accepted Plan/activation/epoch artifacts, `project_docs/`, architecture prompts or calibration assets.

## Decisions

### 1. Generalize S6 admission around one current execution context

After the global reconciliation order (activation, materialization, verification), assemble one immutable current execution context from active pointer, latest accepted activation if present, current Plan, binding/immutable manifest/map, epoch receipt, State, file/revision ledgers, workspace checkpoint/HEAD, frozen inputs and config. Recompute the current Blueprint from the active Plan and frozen inputs and validate every ref before any allocation.

Fresh State remains legal only at a ready E0 checkpoint. Existing State is mandatory for every revised or later-epoch run. A ready receipt permits migration work and then ordinary work; `pending_repair` permits only the exact group ids present in both the F3 activation and epoch receipt until all are resolved. F2 reuses its epoch receipt but uses its current metadata binding: the current Blueprint is validated against the active Plan and current immutable manifest/map, while the reused receipt Blueprint remains bound to `materialized_plan_ref`; equality is required only when that ref is the active Plan. The latest activation must be F2 in the same epoch, the current binding must point to the reused receipt, and its checkpoint must remain an ancestor. Every resolved group's verification commit must be in current HEAD ancestry. M1-9 does not rewrite the immutable epoch receipt from `pending_repair` to `ready`.

Alternative considered: add a separate migration stage or E1 S6 controller. Rejected because S6 owns all task execution, evidence and exit validation, and a second controller would duplicate admission and transaction semantics.

### 2. Project activation State before execution, never future success

Correct the complete migration projector to derive only the activation-time snapshot. INHERIT remains done with old proof and new positional identity. REVALIDATE copies historical attempt usage but clears current commit/evidence and becomes pending/revalidate. AMEND copies historical ordinary attempts even at the configured limit, clears current success and becomes pending/amend with `amendment_used=0`. REGENERATE becomes pending/normal with attempts zero while the activation row retains generation history. Every non-INHERIT row receives the exact `{revision_seq,event_seq}` of the accepted activation; F3 group members also receive their frozen group id.

Derive dependency reopening in the same complete projection: a prior `blocked_by_dependency` row becomes pending only when the accepted current graph and migrated ancestor rows prove the block no longer exists. Do not expose a general caller-controlled reopen operation.

Alternative considered: let S6 repair malformed activation State at admission. Rejected because activation is the publication owner and admission cannot guess or mutate an accepted revision snapshot.

### 3. Reuse the ordinary task pipeline with closed mode branches

Keep one context builder, candidate normalizer, declaration/whitelist validator, build/smoke runner and failure store. Select behavior from the persisted State mode:

- `normal`: unchanged Coder/Fixer schedule; a REGENERATE row enters this branch at attempt zero.
- `amend`: allocate one evidence sequence and one global started call, set `amendment_used=1`, invoke Fixer/T1 once, and never increment ordinary attempts. The Fixer receives `execution_mode=amend`, current task files and current migration-bound failure/obligation context through the existing closed contract.
- `revalidate`: allocate an evidence sequence with `validation_started`, invoke no Agent, and validate the current candidate tree. It changes no coding counters.

Migration refs are mandatory in evidence for all three migrated forms. Build and smoke remain the M1 execution truth; `test_summary_refs` remains empty. A mode failure records the matching candidate/validation evidence and takes only its defined remaining allowance.

Alternative considered: convert AMEND into normal attempt five or REVALIDATE into a no-op INHERIT. Rejected because both erase the independent budget and current-version proof required by the State contract.

### 4. Use the frozen F3 group as a bounded cumulative work unit

Construct the group descriptor solely from `activation.migration.pending_groups`, the active Plan/State and current Blueprint/contract map. Validate exact non-empty membership, paths, symbols, build artifacts, group id and external dependency readiness before work. A group may contain one task; F1 leases remain at least two. The epoch checkpoint commit/tree is the immutable ancestry anchor. The clean accepted HEAD/tree at group start is the transaction baseline and commit parent, so later groups retain prior verified commits.

Within a group, traverse members in stable Plan topological order. Revalidate directly; allocate AMEND/REGENERATE calls only when their mode permits and `task.deliverable_files ∩ group.affected_paths` is non-empty. That intersection is the complete writable set: the Agent receives only those current files, while cumulative candidates and interfaces remain read-only context. A normal/AMEND/REGENERATE response may contain any non-empty legal subset and must make at least one real byte change. Its candidate manifest marks a completely persisted response for recovery. Revalidation has an empty writable set and no model call; no temporary readiness enters formal State.

After each complete traversal, run every default build variant and smoke check on the accumulated tree. Attribute structured compiler/linker failures through the frozen affected paths/symbols/build artifacts. Retry only implicated members with remaining allowance; if attribution is not strict, treat all group members as implicated. Stop on first full pass, or when no implicated member can legally run. Individual mode limits plus the run-wide call/cost/time caps are the only loop bounds; no new group retry budget is introduced.

Alternative considered: commit each member as soon as its local candidate builds. Rejected because interface-incompatible members can only be judged on their shared tree and intermediate readiness is explicitly non-authoritative.

### 5. Extend evidence contracts without changing F1 meaning

Make Task Evidence's Plan version and epoch match its `plan_ref` rather than constants. Permit `attempt=0` only for REVALIDATE; require `amendment_used=1` for AMEND; require migration ref for all migrated execution kinds. `changed_files` may be empty only for accepted revalidation. Preserve the current lease fields and prohibitions unchanged.

Generalize Joint Evidence to discriminated `lease` and `group` forms. Group evidence includes the frozen group id and exact sorted members; unlike lease it has no two-external-file/member-count limit or lease refs. Extend `verification_committed.kind` and the verification WAL with the same `group` discriminator and group-specific complete fields. Use the existing joint commit trailers for both forms, with the Joint Evidence bytes carrying the discriminator and group identity.

For a successful singleton migration, use the ordinary evidence transaction with an execution-kind-aware task commit. For a group, use one joint commit. The Git preparation helper accepts an empty changed set only for a migration verification whose evidence and expected tree equal the current baseline; it still creates an explicit evidence commit with the same tree. Ordinary F0/F1 still require their existing non-empty changed-set rules.

Alternative considered: amend old Task Evidence or treat the epoch checkpoint as revalidation proof. Rejected because evidence is immutable and the checkpoint predates the current migration obligations.

### 6. Keep the legal commit as the sole verification commit point

Extend the existing verification WAL rather than add a second transaction file. Before publishing success, it contains the baseline, current activation/group, exact member modes and allocated sequences, persisted candidate/failure refs, accepted build/smoke refs, expected evidence bytes, prepared commit/tree/trailers and complete old/allocated/new State, file-ledger and revision-ledger snapshots.

Publication order is: reserve all evidence sequences/call usages; persist the WAL and candidates; publish immutable member evidence and Joint Evidence; install the exact candidate tree; publish the prepared legal commit; then forward-publish the complete State, file ledger and one `verification_committed` event; finally terminalize attempt/validation records and remove the WAL. No member State is written done before the commit.

The group WAL records both the epoch checkpoint ancestry anchor and the per-group transaction baseline. Pre-commit reconciliation verifies that relationship, restores only WAL-listed live paths to the transaction baseline, preserves allocated counters and immutable attempt/candidate/failure artifacts, and reassembles manifest-listed candidate subsets without another Agent call. Evidence written before a missing commit is not accepted. Post-commit reconciliation verifies the transaction parent/tree/trailers and every evidence/result ref, then only forward-publishes all missing projections. A strict subset or conflicting bytes fail closed.

Alternative considered: reuse the lease WAL by populating fake lease fields. Rejected because lease authorization and finish events would become false revision history.

### 7. Derive group failure and downstream progress mechanically

When no implicated member has allowance and the group still fails, restore only that group's transaction baseline, derive all unresolved group members to blocked with `GROUP_VALIDATION_EXHAUSTED`, and propagate `blocked_by_dependency` through the current Plan. Earlier verified group commits remain accepted. Preserve all consumed ordinary/amend/global counts and evidence-sequence gaps. No failed-group member retains a candidate commit or done proof.

Continue another branch only if its task dependency closure excludes blocked members and its required build artifacts/runtime closure can be validated independently. If the default whole-workspace build is necessarily broken by the failed group, stop and use the existing `EXECUTION_UNRESOLVED` degraded route. Do not invent an M1-12 circuit-breaker event.

Alternative considered: continue every graph-independent task despite a globally broken build. Rejected because S6 cannot claim complete task acceptance when its required build/smoke gate is unavailable.

### 8. Freeze complete dual-protocol M1-9 fixtures

Extend the existing developer fixture tooling to derive M1-9 packages from checked-in M1-8 MQTT/non-MQTT E0→F2/F3 artifacts and real M1-6/M1-7-shaped execution history. Fixtures contain accepted activation/migration/group inputs plus deterministic frozen Agent responses; CI consumes them but never regenerates them in place.

Required end-to-end cases are: a two-member incompatible F3 group needing sequential repairs; same-tree zero-attempt REVALIDATE; AMEND after four preserved ordinary attempts; REGENERATE at zero; revision-based dependency reopening; group exhaustion; independent branch handling; every pre/post-commit publication fault; and partial/corrupt member negatives. Both protocols use the same production controller and templates, with a byte-stable generator check and protocol-neutrality scan.

Alternative considered: unit-test only pure State/event helpers. Rejected because D1.15 requires WAL/commit/State recovery windows and the task card requires whole S6 execution evidence.

## Risks / Trade-offs

- **[M1-8 is the immutable committed predecessor]** → Treat implementation commit `3a249de`, finalization commit `292c632` and its archived completed artifacts as the baseline, run its focused gates first, and do not rewrite it during M1-9.
- **[Shared lease/group code could blur invariants]** → Share only generic evidence/Git/publication mechanics; keep explicit discriminated validation for lease and group semantics.
- **[Same-tree commits may be mistaken for no validation]** → Permit them only with current migration evidence, successful result refs and migration-specific trailers; execution lint verifies all bindings.
- **[Cumulative candidate restoration can lose prior content]** → Persist the exact baseline plus every candidate ref before installation and fault-test every path and publication boundary.
- **[A failed group can make unrelated progress unverifiable]** → Require both graph independence and a complete runnable acceptance closure; otherwise take the existing degraded exit.
- **[Schema widening could weaken E0/F1 checks]** → Use conditional forms and retain all old negative fixtures and full-suite validation.

## Migration Plan

1. Verify the exact archived M1-8 completion record, commits and focused S5/S6/F1 baseline without modifying predecessor artifacts.
2. Update all persisted contracts and their producers/consumers together; no partially widened Schema is a valid checkpoint.
3. Correct migration-State projection and pure transition/lint behavior before making revised S6 admission reachable.
4. Add singleton modes, then group assembly/validation, then atomic publication/recovery, running focused tests after each boundary.
5. Generate fixtures only into temporary directories, compare them to checked-in MQTT/non-MQTT assets, and update checked-in assets deliberately through the reviewed generator.
6. Run D1.15-focused fault tests, `pytest -m s6_execution`, predecessor regressions, full CI and strict OpenSpec validation. Rollback is the complete M1-9 diff; accepted historical run artifacts are never rewritten.
