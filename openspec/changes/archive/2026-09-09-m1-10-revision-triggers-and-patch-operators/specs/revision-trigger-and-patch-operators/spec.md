## Purpose

Define the deterministic M1 mechanism that turns validated execution facts into auditable revision-trigger decisions and applies only the closed F2/F3 operator language to produce a complete, invariant-preserving migration candidate without activating it.

## ADDED Requirements

### Requirement: Trigger evaluation consumes only validated boundary facts
The trigger evaluator SHALL run only at a stable S6 task boundary with no in-flight attempt, lease, repair group, validation transaction or revision transaction. Its input SHALL bind the current active Plan and revision-ledger prefix, current Plan State, file ledger, Blueprint, contract map, frozen thresholds and referenced immutable failure/diagnosis/build evidence. It SHALL recompute every deterministic predicate from those inputs, reject stale, missing, future or mismatched facts, and SHALL NOT treat Agent notes, one build/smoke/test failure, approaching cost limits or a PlanCritic preference as a trigger by themselves. (Design: `project_docs/pipeline_design_s4_s9.md` §5.6, §6.1-§6.1.1; M1-10.)

#### Scenario: Stable task boundary is evaluated
- **WHEN** all authoritative inputs bind the same active Plan, revision, workspace boundary and accepted evidence prefix with no work in flight
- **THEN** the evaluator returns a canonical evaluation containing every applicable M1 hit and its routing decision

#### Scenario: Evidence belongs to a stale revision
- **WHEN** a supplied fact or evidence reference binds another active Plan or a future/unaccepted ledger event
- **THEN** evaluation fails before appending a trigger event or producing a candidate

#### Scenario: Failed attempt retains an in-progress State row
- **WHEN** the latest bound attempt is durably failed and no call, lease, WAL, repair group or revision transaction remains in flight while its State row is still `in_progress`
- **THEN** the attempt boundary is stable for local trigger recording without making F2/F3 prematurely selectable

#### Scenario: Model-authored trigger flags are supplied
- **WHEN** an historical failure or diagnosis claims graph closure, lease eligibility or architecture fit without current controller-derived evidence
- **THEN** those claims are ignored and cannot make a trigger predicate true

#### Scenario: A non-trigger observation occurs alone
- **WHEN** the only fact is an Agent note, one failure, cost proximity or a request to re-review the Plan
- **THEN** no TR hit or revision candidate is produced

### Requirement: M1 trigger predicates and routes are exact
The evaluator SHALL implement the following M1 rules without adding model conclusions to deterministic facts: TR-1 selects F3 only when an undeclared undefined symbol is required by at least two task build evidences and the consumes closure is complete; a single-task diagnosis is record-only. TR-2 selects F2 only when the unique task-ready provider is blocked and its consumer closure divided by remaining incomplete primary tasks is at least the frozen `theta_2`, with a zero denominator never matching. TR-3 SHALL prefer an eligible F1 lease after at least two same-task-uid/path foreign-owned write rejections; it selects F2 only when F1 is ineligible or exhausted and same-package reassignment is legal, and otherwise does not change ownership. TR-4 selects F2 after at least two truncations for one task or a proven full-lint output-budget overflow. TR-5 records only two independent Diagnoser findings of the same cross-module required-file gap after graph confirmation that it is outside the writable/ready set. TR-6 selects F2 when the deduplicated current blocked plus dependency-blocked task count divided by all tasks is at least frozen `theta_6`. TR-7 selects F3 only for a required input absent from the Blueprint when the existing module/layout rules can legally contain it; otherwise it records an F4 diagnosis and produces no M1 candidate. TR-8 SHALL reject a provider submission whose export declarations disagree with its frozen contract and route it first through available F0/F1; it SHALL NOT select F3 unless an independent TR-1 or TR-7 structural fact also matches. TR-9 SHALL never be evaluated or executed by M1. (Design: pipeline §6.1, §7.2; system design §10.2.2 M1-10.)

#### Scenario: TR-1 has multi-task machine evidence
- **WHEN** two task build evidences require the same undefined symbol, the symbol is absent from declared exports and the consumes closure is complete
- **THEN** TR-1 is an F3-selectable hit with the symbol and obligation anchors in its signature

#### Scenario: TR-1 has only one task diagnosis
- **WHEN** only one task's Diagnoser attributes a failure to an undeclared symbol
- **THEN** the observation is recorded but does not select F3

#### Scenario: TR-2 threshold is met
- **WHEN** the unique task-ready provider is blocked and its consumer closure reaches `theta_2` of the remaining incomplete primary tasks
- **THEN** TR-2 is an F2-selectable hit

#### Scenario: TR-2 denominator is empty
- **WHEN** there are no remaining incomplete primary tasks
- **THEN** TR-2 does not match regardless of the blocked provider

#### Scenario: TR-3 can use a lease
- **WHEN** the same task uid/path has two foreign-owned write rejections and the owner is a done same-package or direct-provider neighbor within the F1 limits
- **THEN** the evaluator authorizes the existing F1 route and does not create an F2 candidate

#### Scenario: TR-3 can legally reassign inside one package
- **WHEN** the rejection threshold is met, F1 is ineligible or exhausted, and the package can retain a complete ownership partition after reassignment
- **THEN** TR-3 is an F2-selectable hit

#### Scenario: TR-4 proves granularity is unsuitable
- **WHEN** one task has two truncations or its complete lint output is projected beyond the frozen output budget
- **THEN** TR-4 is an F2-selectable hit without requiring another failed model call

#### Scenario: TR-2 and TR-4 use production artifacts
- **WHEN** current State/Plan graph facts prove the blocked-provider ratio or current typed provider metadata and the authoritative preflight formula prove output overflow
- **THEN** the production S6 projection emits the same normalized TR-2 or TR-4 hit as deterministic fixture replay

#### Scenario: TR-5 has two independent findings
- **WHEN** two independent Diagnoser results identify the same cross-module required-file gap and graph validation places it outside the writable and ready set
- **THEN** TR-5 is recorded with no selected revision level and consumes no activation allowance

#### Scenario: TR-6 counts unique current failures
- **WHEN** unique currently blocked and dependency-blocked task rows reach `theta_6` of all tasks
- **THEN** TR-6 is an F2-selectable hit and duplicated issue facts do not increase the numerator

#### Scenario: TR-7 fits the existing architecture
- **WHEN** a build requires an input absent from the Blueprint and the current module and layout constraints admit a closed new slot
- **THEN** TR-7 is an F3-selectable hit

#### Scenario: TR-7 would require a forbidden layer
- **WHEN** the missing input cannot fit the frozen module boundary or declared interface rules
- **THEN** the result records F4 and does not create an M1 revision candidate

#### Scenario: TR-8 appears without structural evidence
- **WHEN** a provider candidate changes or omits a declared export but neither TR-1 nor TR-7 matches
- **THEN** submission is rejected for F0/F1 correction and no F3 candidate is selected merely because local attempts are exhausted

#### Scenario: TR-9 facts are supplied
- **WHEN** an S8/test structural diagnosis resembling TR-9 is present in the input
- **THEN** M1 produces no TR-9 event, route or candidate

### Requirement: Trigger signatures, selection and deduplication are canonical
Each hit SHALL have a canonical SHA-256 signature over its TR code, stable obligation/lineage anchors, normalized paths or symbols, and normalized error category. The signature SHALL exclude wall-clock data, log line numbers, topological task ids, and complete evidence-file hashes; evidence references SHALL be retained separately and validated. At one boundary the evaluator SHALL consider every hit, prefer the lowest applicable solution level in F2-before-F3 order, then the lowest TR number, and record every hit with exactly one selected hit when a legal revision route exists. A record-only hit SHALL never be selected. A `(signature, level)` already rejected SHALL not be retried at that level; it MAY remain eligible at one applicable higher level not yet attempted. A signature already activated SHALL not produce another candidate. Identical accepted inputs SHALL produce byte-identical ordered evaluations. (Design: pipeline §6.1-§6.1.1; M1-10.)

#### Scenario: Volatile evidence changes
- **WHEN** two evaluations describe the same normalized issue but differ only in timestamps, log line numbers, task numbering or evidence hashes
- **THEN** they derive the same signature while retaining their distinct validated evidence references

#### Scenario: F2 and F3 hits share a boundary
- **WHEN** at least one applicable F2 hit and one applicable F3 hit are present
- **THEN** the lowest-numbered applicable F2 hit is selected and every other hit is recorded as unselected

#### Scenario: Same level was already rejected
- **WHEN** the accepted ledger contains a rejection for the same signature and level
- **THEN** that level is not proposed again, while a previously unattempted applicable higher level remains eligible

#### Scenario: Signature was already activated
- **WHEN** the accepted ledger proves a revision activation for the same signature
- **THEN** the recurrence is classified as ineffective for later handling and produces no candidate or duplicate activation attempt

### Requirement: F2 patches use only the closed decomposition operators
An F2 patch SHALL contain only ordered `split_task`, `merge_tasks`, `move_responsibility`, `move_file_owner`, `rewrite_instructions`, `insert_task`, or `reorder_dependency` operations. Split/merge/move/insert SHALL remain within one work package, maintain a unique complete responsibility and file partition, use explicit task uid lineage and obligation mappings, keep every task non-empty and at no more than four files, and take inserted files only from the package's allowed files while removing them from the prior owner. Instruction rewrites SHALL change only goal/instructions/context references and SHALL NOT refresh failure or execution budgets. Dependency reordering SHALL only add contract-proven package-local edges and SHALL be followed by deterministic ordering. An F2 patch SHALL NOT alter the commitment or architecture layers. (Design: pipeline §2, §6.2; M1-10.)

#### Scenario: Task is split legally
- **WHEN** one package task is split into non-empty successors with explicit new uids, complete file/responsibility partition and old-to-new obligation mapping
- **THEN** the operation produces a valid F2 candidate whose Linker-derived ids may change without losing lineage

#### Scenario: File gains two owners
- **WHEN** a move or insert operation leaves the same file assigned to both old and new tasks
- **THEN** the entire patch is rejected without publishing a partial candidate

#### Scenario: Instructions are rewritten
- **WHEN** an operation changes only the allowed instruction fields
- **THEN** the task's guidance may change while its ordinary attempts, amendment use and run-wide usage remain unchanged

#### Scenario: Dependency lacks a contract proof
- **WHEN** `reorder_dependency` adds an edge not implied by a declared provider-consumer contract or crosses a package boundary
- **THEN** the patch is rejected

### Requirement: F3 patches use only the closed architecture operators
An F3 patch SHALL contain only `add_contract`, `extend_contract`, `add_file_slot`, `add_work_package`, `move_file_across_wp`, `retire_file_slot`, and `re_adopt`, plus only those F2 operations necessary to close the same structural change. Contract operations SHALL preserve every published declaration and signature and SHALL declare implementation slots and provider/consumer bindings. Slot, package and cross-package operations SHALL preserve the existing module and commitment boundary and maintain complete file/responsibility/provider partitions; `add_file_slot` and `re_adopt` SHALL explicitly bind every new link-source slot to its affected build artifacts and SHALL use an empty binding for non-link-source slots. Retirement SHALL remove every live reference while preserving all obligations and published symbols through an explicit successor; realized content SHALL be quarantined rather than deleted. Re-adoption SHALL name the registered quarantine source, explicit target slot and owner and require later revalidation. Path changes SHALL be expressed as retirement, addition and explicit migration in one patch; implicit rename, deletion, requirement removal, contract removal, acceptance shrinkage, realized-file deletion and whole-Plan replacement SHALL be unavailable. (Design: pipeline §3.5, §6.2; M1-10.)

#### Scenario: Contract is extended monotonically
- **WHEN** an F3 operation adds a declaration while retaining every old export byte-for-byte and supplying its implementation slot and provider/consumer closure
- **THEN** the structural candidate may be produced for complete invariant validation

#### Scenario: Published signature is changed
- **WHEN** an operation deletes or changes an existing export declaration or signature
- **THEN** the complete patch is rejected as an unavailable operation

#### Scenario: Realized slot is retired
- **WHEN** one patch retires a realized slot, assigns every obligation and published symbol to legal successors, removes all live references and supplies the explicit quarantine transition
- **THEN** the candidate records retirement and migration without deleting the content

#### Scenario: Path is renamed implicitly
- **WHEN** a patch names a rename or changes a path without explicit retirement, addition and migration mappings
- **THEN** the patch is rejected rather than repaired by inference

### Requirement: Patch application produces one complete invariant-valid candidate
The system SHALL validate the whole ordered operation list before publication, apply it atomically to a copy of the bound active Plan inputs, recompute task identity and ordering through the existing Linker, rerun full Plan lint and Blueprint/manifest/contract-map derivation, and invoke the existing deterministic migration classifier against the accepted State and file ledger. The resulting unactivated candidate SHALL contain the patched Plan, exact trigger/event binding, canonical patch operations, explicit split/merge/move/path lineage, recomputed derived artifacts, a complete migration report for every old/new task and old realized file, and affected-group inputs required by M1-9. It SHALL prove INV-1 by unchanged commitment content/hash, INV-2 by a unique primary owner for every frozen normative requirement, and INV-3 by an explicit non-weakening mapping for every prior task obligation, acceptance node and moved/split/merged file. Failure of any operation, derivation, invariant or migration check SHALL reject the complete candidate without partial output. (Design: pipeline §2, §3, §6.2; system design §5.2, §10.2.1-§10.2.2; M1-10.)

#### Scenario: F2 candidate renumbers tasks
- **WHEN** a legal split causes deterministic topological task ids to change while stable uid lineage and every old obligation mapping remain complete
- **THEN** the candidate passes INV-1/2/3 and records classifications from the existing migration rules rather than treating renumbering as new work

#### Scenario: F3 candidate extends an interface
- **WHEN** a legal contract extension adds its slots and closed provider/consumer bindings without changing existing declarations or commitments
- **THEN** the candidate includes the recomputed Blueprint, migration report and affected groups needed by later rehearsal and execution

#### Scenario: Requirement loses its primary owner
- **WHEN** ordered operations leave any frozen normative requirement unowned or multiply primary-owned
- **THEN** INV-2 fails and no candidate is published

#### Scenario: Acceptance is weakened during a merge
- **WHEN** merged successors omit or postpone an old acceptance obligation without a legal complete mapping
- **THEN** INV-3 fails even if the candidate otherwise passes Schema and graph validation

#### Scenario: Obligation is mapped to an unrelated task
- **WHEN** a removed obligation names an existing target obligation on a task without explicit lineage or matching move/insert/cross-package operator semantics
- **THEN** INV-3 rejects the mapping even though that obligation exists elsewhere in the candidate Plan

#### Scenario: Acceptance gate is postponed
- **WHEN** a mapped test is removed, disabled or first accepted by a later task gate than before the patch
- **THEN** INV-3 recomputation rejects the candidate regardless of its `acceptance_not_weaker` declaration

#### Scenario: Structural changes form disjoint build components
- **WHEN** an F3 patch affects two task/path/build components with no shared task, path, symbol or build artifact
- **THEN** the migration report contains two exact affected groups and does not add unrelated build artifacts

### Requirement: M1-10 does not activate or mutate execution state
Trigger-event publication SHALL be an atomic, idempotent append to the accepted revision ledger and SHALL advance only `event_seq`. Candidate artifacts MAY be written under the event-scoped `_s4r` candidate area as non-authoritative immutable inputs for M1-11, but candidate construction SHALL NOT write a formal Plan version or binding, move `active_plan.json`, increment `revision_seq`, modify Plan State or the file ledger, touch workspace source, invoke PlanCritic or S5 rehearsal, append `candidate_rejected` or `revision_activated`, or allocate an F2/F3 activation budget. Interrupted trigger append or candidate publication SHALL reconcile by accepted ledger identity and content hashes to either the unique complete output or a clean retry; conflicting bytes SHALL fail as artifact damage rather than be guessed. (Design: pipeline §4.2-§4.4, §6.1-§6.4; M1-10/M1-11 boundary.)

#### Scenario: Candidate is produced successfully
- **WHEN** a selected hit and supplied closed patch produce a complete invariant-valid candidate
- **THEN** the trigger event and event-scoped candidate are available while the active version, revision sequence, State, file ledger and workspace remain unchanged

#### Scenario: Publication is interrupted
- **WHEN** interruption occurs before or during event/candidate publication
- **THEN** recovery validates the accepted event and candidate hashes, completes the same identity at most once or retries from unchanged authoritative inputs without activating it

#### Scenario: Later-stage fields are supplied
- **WHEN** an M1-10 caller attempts to supply RG results, critic output, rehearsal output, activation data or PlanReviser call evidence
- **THEN** the M1-10 contract rejects those fields rather than performing M1-11 or M1-14 work

### Requirement: Selected revisions pause at an internal S6 handoff
After a selected F2/F3 trigger is durably recorded, S6 SHALL optionally consume only a frozen Schema-valid patch from the internal `revision_patch_provider`, construct or replay its non-authoritative candidate, and return `StagePause(kind="revision_handoff", selected_event_seq, candidate_ref)` through the internal stage result. The orchestrator SHALL restore S6 to `pending`, record the pause event and return non-terminal zero without producing a termination request or S9 report. Resuming from the same accepted ledger and candidate SHALL return the identical pause and SHALL NOT append a duplicate event. This handoff SHALL add no public CLI parameter, Run state enum, Agent call, gate, rejection or activation behavior. (Design: system design §5.6.7, §10.2.2; M1-10/M1-11 boundary.)

#### Scenario: Selected trigger has no frozen patch
- **WHEN** an F2/F3 hit is selected and no internal patch provider supplies a patch
- **THEN** S6 returns the revision handoff with no candidate reference and remains resumable

#### Scenario: Selected trigger produces a candidate
- **WHEN** the frozen internal patch completes and publishes a valid event-scoped candidate
- **THEN** S6 returns the handoff with that candidate reference while authoritative execution artifacts remain unchanged

#### Scenario: Revision handoff is resumed
- **WHEN** S6 re-enters the same accepted selected event and candidate boundary
- **THEN** the same pause is returned without another trigger event, termination request or S9 report

#### Scenario: Accepted event precedes candidate rename
- **WHEN** a crash leaves a hash-valid pending candidate after the selected trigger event was accepted but before the final rename
- **THEN** S6 reconciliation completes the unique rename and returns the same candidate reference without another Agent call or ledger event
