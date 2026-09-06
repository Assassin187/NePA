## Context

See `proposal.md` for motivation. The archived M1-5 change leaves a `ready` E0 whose workspace HEAD, immutable epoch/binding receipts, manifest/map and S5-frozen file evidence are mutually bound; every task-owned file remains `slot_only`. Existing code already provides the Orchestrator lock/stage lifecycle, RunStore confinement/atomic publication, Agent invocation boundary, initial Plan-State helpers, typed revision-ledger primitives, sandbox build/smoke execution and git verification. M1-6 must connect those paths without creating parallel persistence or execution frameworks.

The authoritative target contracts move fresh runs to Plan State 2.0 and Task Evidence 2.0 when their first producer/consumer is delivered. Old runs are read-only; there is no in-place compatibility conversion. M1 still has no generated Test Bundle assets, so an S6 task gate consists only of deterministic source checks, declared build variants and smoke.

## Goals / Non-Goals

**Goals:**

- Close fresh ready-E0 admission through ordinary F0 task commits, Plan State/file-ledger realization and a final S6 receipt.
- Make every provider, workspace, evidence, Git and mutable-State boundary replayable from persisted facts.
- Preserve exact failed candidates and matching feedback for later Fixer attempts.
- Deliver the existing M1 CLI surface and sandbox-backed `s6_execution` acceptance path.

**Non-Goals:**

- No F1 leases, AMEND/REVALIDATE/group execution, E1+, trigger evaluation, revision candidate or activation behavior.
- No Test Bundle implementation, task test execution, S7/S8 behavior or protocol-specific generation logic.
- No production model qualification, new fallback route, host execution or automatic migration of older runs.

## Decisions

### 1. Extend the existing stage transaction model with one S6 controller

Register an S6 controller on the same Orchestrator path as S4/S5. Its entry order is: reconcile activation (existing no-op for M1-6), reconcile S5, reconcile verification WAL, verify ready-E0/current execution facts, initialize State if uniquely legal, execute work, and seal the final receipt. Admission recomputes or verifies authoritative inputs exactly as S5 does and never trusts mutable convenience copies without the binding receipt.

Alternative considered: infer State by scanning task commits. Rejected because missing or partial evidence cannot be distinguished from an accepted task transaction, and §6.6 explicitly forbids guessing State after task execution starts.

### 2. Upgrade the complete fresh-run State chain to v2 at its first runtime consumer

Update the closed State Schema, examples, projector, snapshot/transition/execution lint, RunStore readers and CLI lint together. State owns `execution_mode`, `amendment_used`, `migration_ref`, `group_id`, historical `evidence_counters` and global `s6_attempts_used`, even though fresh M1-6 uses only `normal` and null migration/group values. This is the minimum coherent contract required by the authoritative state machine and future consumers; no dormant producer for later modes is enabled.

Alternative considered: keep v1 and add counters in an S6-private file. Rejected because it would split the authoritative state and make attempt/evidence allocation impossible to validate atomically.

### 3. Persist attempt allocation before calling an Agent

For each stable-topological ready task, create an attempt directory keyed by task uid and monotonically allocated evidence sequence. Under the run lock, atomically apply `attempt_started`, increment global usage, record role/tier/attempt, baseline commit/tree and expected writable paths, then release the lock for Agent I/O. Attempt 1 binds Coder/T2; attempts 2-3 bind Fixer/T2; attempt 4 binds Fixer/T1. Interrupted allocations remain spent.

The run-level cap and existing global time/cost budget are checked before allocation and again at external boundaries through the existing budget owner. Configured Agent `escalate_to` metadata is not followed: tier escalation is a controller decision derived from the attempt number, resolving the old M1-3 ambiguity without inventing a second role transition.

Alternative considered: increment counters after a response. Rejected because a crash during provider I/O would refund a real call and violate hard-budget reproducibility.

### 4. Assemble one canonical, protocol-neutral context package

Add a pure context projector that resolves the task/work package, ordered architecture/contract summaries, exact `context_refs` Spec slices, relevant contract-map rows and interface bytes, Delivery Constraints language guidance, and all task-owned current files. Fixer replaces current-file preference with the latest failed candidate and appends its matching normalized validation evidence and diagnosis. The projector returns a canonical object and token accounting before rendering; only advisory language guidance may be dropped, and required overflow is a controlled pre-call failure.

Coder/Fixer share one closed draft-2020-12 response Schema and full-file serializer. Existing generic role registration/invocation is reused; only the production input names/template content and caller-supplied route override for the fourth-attempt T1 tier are added. Template and projector sources pass the existing protocol/provider/model-neutral scan.

Alternative considered: expose the whole Plan, Spec or workspace. Rejected because it violates the fixed visibility boundary, increases prompt size and leaks unrelated/test facts.

### 5. Keep candidate bytes outside the accepted workspace until preflight closes

Persist validated model output and normalized full files beneath the attempt record, resolving paths through RunStore confinement. Check exact task ownership, duplicate/path closure, changed-file subset and contract-export equivalence before assembling a candidate worktree from the persisted baseline. No response can edit `s5_frozen`, another owner's file, Plan or evidence directories. Build and smoke execute only in the network-disabled sandbox over that candidate tree; M1 passes an empty applicable-test set by construction.

On failure, persist build/smoke results and a normalized error tied to the candidate tree, then restore or retain the live workspace at the accepted baseline. A deterministic diagnosis policy may invoke Diagnoser only after a concrete failed validation and persists its output beside that same attempt; it is not a coding attempt and cannot modify files. The next Fixer receives those immutable facts.

Alternative considered: apply output directly and use `git checkout` after failure. Rejected because crashes would leave an ambiguous live tree and could detach the feedback from the actual candidate.

### 6. Use the legal task commit as the verification transaction point

After candidate build/smoke passes, allocate immutable Task Evidence 2.0 containing all input refs, attempt identity, exact candidate tree and changed-file hashes, result refs and acceptance summary. Persist `plan/verification_pending.json` with old/new State, old/new file ledger, old/new revision ledger, evidence ref, expected commit/tree/trailers and transaction phase. Move the accepted task files into the live workspace, verify the exact staged set, and create one commit with task uid/id, Plan/version/epoch, attempt/evidence sequence and evidence trailers.

The commit is the logical point of no return. Before it, recovery restores the WAL baseline and retains attempt artifacts. After it, recovery verifies the tree/trailers/evidence and only moves forward: atomically replace State and file ledger, append the idempotent `verification_committed` event, then delete the WAL. File-ledger realization updates only changed task-owned rows and preserves owner history and prior S5 facts.

Alternative considered: mark State done before commit. Rejected because State could advertise success without a durable source tree.

### 7. Derive blocking and final S6 acceptance from durable facts

After an attempt failure, transition to `in_progress` while local budget remains; after the fourth failure, apply `attempts_exhausted` with the final attempt reference. Recompute `blocked_by_dependency` only from the current Plan DAG and State, and continue independent ready branches. No F1 or revision event is evaluated in this change. If any static-valid task remains blocked, request `EXECUTION_UNRESOLVED` degraded termination; a proven invalid static contract uses the failed route.

When every task is done, rerun all Blueprint build variants and executable smoke checks from final clean HEAD, run snapshot plus execution lint, copy the accepted revision-ledger prefix immutably and publish an S6 receipt. Run S6 output refs are the logical receipt acceptance point; post-commit reconciliation finishes any missing observation without rebuilding. A completed replay performs verification only and changes no file or timestamp.

Alternative considered: reuse the last task's build as final evidence. Rejected because tasks can target subsets and final acceptance must bind the complete final tree.

### 8. Wire only the already-designed M1 CLI behavior

Extend existing commands rather than add S6-specific flags: `run --until s6` reaches S6 and ends as `planned_stop` only after receipt acceptance; `resume` invokes reconciliation first; `status` projects task counts/current task/attempt/global usage from durable artifacts; Plan and Plan-State lint expose basic versus run-directory execution checks. Preserve exit codes 0/10/20/1 and do not invoke S7/S9 on a successful `--until s6` planned stop.

Alternative considered: use test-only stage entry as in M1-5. Rejected because §10.2.2 explicitly assigns the public S6 CLI/resume/status/lint boundary to M1-6.

## Risks / Trade-offs

- [State v2 touches dormant migration fields] → Upgrade Schema, pure validators, persistence and examples as one chain, but expose no later event producer; run all M1-4d/S4/S5 regressions.
- [Candidate isolation and commit recovery can diverge] → Bind every path/tree/hash and expected trailer in one verification WAL and fault-inject every publication boundary.
- [Build/smoke errors can exceed context limits] → Persist complete evidence but inject bounded normalized diagnostics; never omit required candidate files or the matching failure summary.
- [A model can alter exported ABI while compiling locally] → Compare declarations and implementation bindings against the sealed contract map before sandbox execution and again in task evidence validation.
- [Diagnoser adds cost without an attempt count] → Invoke only on a concrete failed candidate under global time/cost accounting; it cannot grant attempts or modify State.
- [Full-project smoke after each task may be expensive] → Retain it because the design makes smoke a hard per-unit gate; record actual durations/cost rather than weakening acceptance.

## Migration Plan

1. Verify the archived M1-5 ready-E0 fixtures and current S4/S5 recovery baseline.
2. Introduce closed State/attempt/evidence/S6 receipt contracts and pure transition/execution validators before enabling S6.
3. Bind production Coder/Fixer contexts and responses using frozen provider fixtures.
4. Add candidate validation, build/smoke and verification-WAL publication/recovery paths behind the S6 controller.
5. Wire CLI/status/lint and frozen end-to-end fixtures, then enable the `s6_execution` CI marker.
6. Preserve old run directories unchanged; rollback consists of disabling the S6 controller for new runs, not rewriting accepted v2 artifacts.
