## Context

See `proposal.md` for motivation. The archived M1-6 path already owns ordinary attempt allocation, protocol-neutral Coder/Fixer context construction, candidate isolation, build/smoke validation, Task Evidence v2, task commits, a normal-only verification WAL, Plan State/file-ledger publication, typed `verification_committed` events and S6 receipt recovery. The present revision-ledger Schema already names `lease_started` and `lease_finished`, and Task Evidence already admits `execution_kind=lease`, but the ledger validator accepts only single-member normal verification, the WAL is normal-only, there is no Joint Evidence Schema, and S6 has no lease producer or metric implementation.

The repair adds `plan/state_history.json` as a simple sequence of accepted transitions and complete State snapshots. It deliberately does not add another hash chain: ordering, transition semantics and the repository's existing ArtifactRef validation are sufficient. Attempt allocation persists attempt intent before the history/ledger/current-State projections so recovery can finish a partially published allocation without refunding or repeating it. Metric call associations use the telemetry row's stable `output_path`, not a synthetic call id or a new digest.

M1-7 must extend those exact paths. The authoritative design assigns automatic TR predicate evaluation and revision operators to M1-10, E1+ to M1-8, and AMEND/REVALIDATE/group execution to M1-9. Therefore this mechanism accepts an explicit controller-owned lease authorization at an S6 task boundary for frozen acceptance fixtures and the future trigger producer; it adds no public CLI parameter and does not infer a lease from model prose. The archived M1-6 completion record is verified; its current regression and real-sandbox health are not yet re-run and remain the first implementation gate.

## Goals / Non-Goals

**Goals:**

- Extend one existing normal Fixer attempt with a validated, exact F1 scope while preserving all F0 counters and route choices.
- Make a two-sided success one verifiable Git/evidence/State/file-ledger/ledger transaction, with the legal joint commit as the point of no return.
- Keep pre-commit failure local and auditable, and make every post-commit interruption converge forward for all members.
- Provide one pure formula source for all §9.1.4 M1-computable and future-event metric fields, including explicit unavailable/empty-domain results.

**Non-Goals:**

- Do not implement automatic TR-3 detection, any other trigger, candidate Plan generation, revision activation, new epochs or repair groups.
- Do not change Plan task ownership, ordinary attempt limits, Plan versioning, the selected architecture prompt bundle or frozen inputs.
- Do not introduce a Test Bundle runner, accepted terminal-round discovery, S7/S8 repair logic, batch aggregation or the `eval runs` command.
- Do not add an older-run compatibility converter or a second S6 persistence/controller path.

## Decisions

### 1. Represent lease intent as an explicit closed authorization consumed by S6

Add a closed lease-authorization artifact/projector containing the current Plan ref, baseline commit/tree, current task uid, sorted lending uid/path rows and evidence refs. A pure validator resolves every path through the active Plan, State, file ledger, work-package membership and direct-provider edges; it also recomputes remaining κ from accepted `lease_started` events. The S6 controller accepts this authorization only at the boundary immediately before an otherwise legal Fixer attempt. M1-7 tests inject frozen authorizations through an internal controller seam; the normal CLI path has no new option, and M1-10 can later become the producer without changing the F1 consumer.

This separates mechanism acceptance from the not-yet-delivered trigger policy while still exercising the production controller, budget and recovery code. Deriving a lease from a model's out-of-whitelist response was rejected because model prose cannot grant authority and doing so would prematurely implement TR-3.

### 2. Reuse ordinary attempt allocation, then durably start the lease before provider I/O

Extend the existing S6 attempt record with an optional closed lease binding. The current task first consumes the same next normal Fixer attempt, tier and `s6_attempts_used` slot as F0. Under the controller lock, S6 then appends the canonical `lease_started` entry and records the resulting `lease_id=lease-<event_seq>` in the attempt before invoking the Agent. Provider I/O is forbidden until the State/attempt and start event revalidate. A crash in this pre-call sequence does not refund an allocated attempt; once the start event is accepted, it also does not refund κ. Resume either continues from the exact accepted start or closes an invocation that cannot be resumed as a failed lease, without issuing a duplicate call.

The lending task receives no attempt or mode transition. Treating F1 as a fifth call or as an AMEND invocation was rejected because §6.5 defines it only as an enlarged whitelist for the current Fixer.

### 3. Extend the existing context and candidate projectors with one exact lease scope

Keep the shared coding response Schema unchanged. Extend the Fixer input contract with canonical lease authorization and sorted leased-file bytes, present only in lease mode. Required token accounting includes those bytes and fails before provider I/O if they do not fit; it never drops required current or lending files. Candidate normalization receives an effective whitelist equal to current-task deliverables union exact leased paths, but still rejects every frozen, unsafe, duplicate or undeclared path.

Run the existing export/implementation-binding validator across every changed file before sandbox execution. Then run the same complete default build variants and executable smoke set once on the assembled joint candidate tree; the empty M1 test set remains explicit. Building separate member trees was rejected because the accepted proof and commit must bind one jointly valid state.

### 4. Allocate new evidence sequences for every member after joint validation

After build/smoke success and before writing evidence, allocate a monotonically increasing evidence sequence for the current uid and every lending uid through the existing State/evidence-counter owner. Gaps after interruption remain legal. Project one Task Evidence v2 object per member with `execution_kind=lease`, the same Plan/tree/result refs and only that member's changed-file rows, then project a new closed Joint Evidence v2 object at `test_results/task_evidence/joint/<verification_id>.json`. Sort members by uid and derive `verification_id` from the minimum uid and its newly allocated sequence exactly as §5.4 requires.

Reusing the lender's old evidence was rejected because it verifies the pre-lease tree, and overwriting it would destroy immutable history.

### 5. Generalize the verification WAL and Git publisher rather than add a lease transaction path

Change `verification-pending.schema.json` to a closed `normal`/`lease` union. The lease form carries the authorization/start-event identity, sorted members and evidence refs, Joint Evidence ref, exact changed-file ownership, common baseline/candidate tree, expected joint trailers, and complete old/new State, file and revision ledgers. Generalize the current verification reconciler and Git preparation helper to select either the existing single-task trailers or exactly `NePA-Verification-ID` plus `NePA-Joint-Evidence-SHA256`; no task trailer appears on a joint commit.

The joint commit remains the point of no return. Before it, recovery restores only WAL-listed paths to the common parent and preserves attempts, immutable diagnostics and sequence gaps. After it, recovery verifies tree, parent, trailers and all evidence and only then atomically replaces complete State/file-ledger/ledger projections and removes the WAL. A lease-specific commit/recovery implementation was rejected because it would duplicate the safety-critical M1-6 path.

### 6. Project all-member State, file and event results from one proof

Add `amended_under_lease` to the existing transition projector as one complete task-set operation. The current row becomes done with its current attempt; every lender remains done with unchanged attempts, execution mode and owner history, but receives the new evidence/commit binding. File-ledger projection changes only accepted current/leased realized rows and sets their validation to the joint tree without changing owner history. Ledger projection appends one lease-kind `verification_committed` and one matching successful `lease_finished`; a failed pre-commit unit appends only the failed finish.

Extend revision-ledger validation to pair unique lease ids, allow sorted multi-member lease verification, enforce forward references and keep `revision_seq` equal to the latest activation (zero for fresh M1-7 fixtures). Idempotence compares complete event payloads; an identity collision with different bytes is damage. Publishing member rows independently was rejected because it can advertise a half-accepted tree.

### 7. Close every failure path against the shared baseline

Schema/context/path/declaration/build/smoke failure records the exact candidate and associated call/result refs, restores the common pre-lease workspace, verifies the lender's old done proof still holds, and appends a failed finish once. The current task remains `in_progress` while another ordinary attempt exists or follows the established exhaustion transition after attempt four. Failures do not change owner, lender attempts or revision state. A pre-commit crash with orphan evidence follows the existing WAL rules; a post-commit conflict is artifact damage, never rollback.

This retains M1-6's controlled `EXECUTION_UNRESOLVED` semantics and prevents an F1 failure from becoming evidence of a Plan defect. Automatic escalation after failure was rejected because later work items own it.

### 8. Add a standalone pure metrics module with validated input adapters

Introduce one small metrics module, separate from CLI/report rendering, with pure projections for task/lineage rates, first-pass history, S5/S6 build-smoke values, revision statistics and lease statistics, plus a composition function returning the public §9.1.4 keys. Callers supply already loaded artifacts; a run-directory adapter may read and validate refs but does not write. Every nullable value uses one `{value: null, reason: {code, detail?}}` envelope, while available values use the corresponding deterministic value shape.

Lineage calculations consume only activation migration rows and immutable verification proofs. Percentiles sort integer external-file counts and use the repository's one frozen deterministic nearest-rank rule documented in the implementation brief/tests; they never depend on a statistics library. Cost association deduplicates immutable call refs before summing telemetry. Future F2/F3/S7/S8 inputs are covered by static fixtures, not produced by this change. Implementing metrics inside `s9_report.py` or the future eval CLI was rejected because M1 planned stops have no report and M2-6 must reuse the same formulas.

### 9. Extend only existing observability and acceptance surfaces

Status adds accepted lease counts and an active lease identity derived from State/attempt/ledger facts. Full Plan-State lint invokes the generalized joint evidence checks; snapshot-only lint remains filesystem-neutral. No new CLI flag or command is added. Register `s6_lease` and `metric_contract` markers, use frozen provider/authorization/ledger fixtures, and retain real sandbox coverage for joint builds and smoke.

The required implementation brief from §10.8 is the first task and freezes exact input/output Schemas, function/class signatures, acceptance commands and all cited sections before code edits. It is a derived artifact only and cannot add design choices.

## Risks / Trade-offs

- [An accepted lease start can outlive a provider interruption] → Keep both ordinary attempt and κ spent, bind the attempt to the start event, and close it once with a failed finish rather than guessing whether provider work occurred.
- [A lender's prior done proof and new joint proof can be mixed] → Allocate fresh per-member sequences and require State, Joint Evidence, commit trailers, file ledger and both typed events to agree before execution lint passes.
- [Generalizing the WAL can regress ordinary F0 recovery] → Preserve the normal branch byte-for-byte at the contract level and rerun every M1-6 fault window in addition to new lease windows.
- [Lineage fixtures describe producers not delivered until later work items] → Keep metrics read-only and validate only the authoritative event contracts; do not make those fixture events reachable from S6.
- [Percentile and null representations can drift across later consumers] → Freeze them once in `metric_contract` fixtures and require M2-6 to import the same pure functions.
- [Two external files can make Fixer context overflow] → Treat leased bytes as required and fail before provider I/O; never trim current/leased files or widen the budget locally.

## Migration Plan

1. Re-run the archived M1-6 State/Schema/S6 markers and real-sandbox baseline, then write the §10.8 M1-7 implementation brief from the current authoritative sections.
2. Add closed lease authorization, Joint Evidence and normal/lease WAL contracts plus ledger/State projections and validators before enabling the lease controller seam.
3. Extend context, candidate, Git publication and reconciliation through the existing S6 path; fault-inject every pre/post-commit boundary and retain all F0 regressions.
4. Add the pure metrics module and all §9.1.4 fixed, empty-ledger, unavailable and future-event fixtures without adding the M2 CLI.
5. Wire status/lint and CI markers, run the complete scoped and public validation suite, and inspect the diff for later-work-item behavior.
6. Old runs remain read-only. Rollback disables the new internal lease input for fresh execution and metric consumers; it never rewrites accepted joint commits, evidence or ledgers.
