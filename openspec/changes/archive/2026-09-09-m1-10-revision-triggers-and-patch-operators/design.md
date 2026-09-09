## Context

M1-9 has completed the consumer side of accepted migration work: current-version admission, REVALIDATE/AMEND/REGENERATE, F3 repair groups, joint evidence and recovery. The dormant revision infrastructure already validates typed ledger entries, classifies migration and projects candidate State, while S4 owns the deterministic Linker/full-lint/Blueprint path. M1-10 must connect validated S6 facts to those existing paths without creating a second planner or crossing the M1-11 activation boundary.

The authoritative behavior is `project_docs/system_design.md` §5.2-§5.2.5, §5.6.7 and §10.2.1-§10.2.2, with trigger/operator details in `project_docs/pipeline_design_s4_s9.md` §2-§4 and §6.1-§6.2. The current `trigger_evaluated` ledger form is singular per hit; M1-10 will preserve that shape and atomically append one ordered event per hit at a shared boundary rather than introducing an aggregate alias. The M1-9 archive and its corrected owner approval are prerequisite evidence, not files this change may rewrite.

## Goals / Non-Goals

**Goals:**

- Recompute TR-1 through TR-8 from accepted facts, produce stable signatures, select the lowest applicable F2/F3 route and connect TR-3's local result to the existing F1 authorization consumer.
- Validate a closed semantic patch language and deterministically build a complete, lint-clean, migration-classified candidate with explicit invariant evidence.
- Persist trigger hits and a non-authoritative candidate so interruption/replay converges without touching the formal version chain or workspace.
- Exercise the same implementation with MQTT and non-MQTT frozen fixtures and make `pytest -m revision_mechanism` the independent M1-10 acceptance entry.

**Non-Goals:**

- No PlanReviser or other LLM call. M1-10 acceptance supplies Schema-valid frozen patches through an internal seam; M1-14 may later produce that same contract.
- No RG result, PlanCritic call, budget gate, S5 rehearsal, `candidate_rejected`, formal version allocation, activation, circuit breaker or effectiveness evaluation.
- No public CLI option and no compatibility path for historical major Schema versions.

## Decisions

### 1. Use one deterministic boundary-fact projection

Add a revision-mechanism module alongside the existing Plan revision helpers, and have the S6 controller construct one closed boundary-fact value from the already validated current execution context. The projection binds active Plan/ref, revision/epoch, State-history position, current workspace commit/tree, Blueprint/map, accepted ledger-prefix hash, frozen thresholds and typed evidence refs. Boundary kinds are `task_boundary` and `provider_submission`; the latter is the only place TR-8 may reject export drift. Inputs that do not share those anchors fail before predicate evaluation.

The boundary key follows system design §5.6.7 exactly: `{phase, revision_seq, tasks}`, where `tasks` is the canonical task-uid-ordered execution snapshot and each entry contains `task_uid`, `mode`, `ordinary_attempts_used`, `amendment_used` and `status`. Active Plan/ref, epoch, State-history position, workspace commit/tree, Blueprint/map, accepted ledger-prefix hash, subject/provider identity and frozen thresholds remain closed top-level evaluation anchors; they are not folded into `boundary_key`. Stable problem signatures are a separate hash of `{tr_code, obligation_uids, lineage_uids, normalized_paths, normalized_symbols, normalized_error_class}`. Keeping the boundary identity separate permits audit of repeated observations while ensuring timestamps, log lines, topological `T-###` ids and evidence hashes cannot evade issue deduplication.

Alternative considered: let individual S6 failure branches call TR helpers with their local dictionaries. Rejected because it duplicates trust checks and makes cross-trigger ordering and signature normalization non-replayable.

### 2. Evaluate all hits, then route once

Each TR predicate returns a normalized hit with `code`, route class (`record_only`, `F1`, `F2`, `F3`, `F4` or `submission_reject`), applicability reason, signature anchors and sorted evidence refs. A pure coordinator runs TR-1 through TR-8 in numeric order, validates threshold denominators and deduplicates raw facts before counting. It then consults accepted `candidate_rejected` and `revision_activated` ledger history for the signature, excludes attempted/inapplicable levels, chooses F2 before F3 and then lowest TR number, and marks at most one F2/F3 hit selected. TR-3 F1 remains local and is emitted as the existing closed lease-authorization input when its validator passes; it does not compete as an F2/F3 candidate. TR-5 and F4 observations never consume version allowance. TR-9 has no M1 predicate entry.

TR-8 runs before provider candidate installation and commit. Export drift rejects that submission through the existing failed-attempt path. It may be recorded as an unselected hit, but only an independently satisfied TR-1 or TR-7 can select F3.

Alternative considered: stop after the first matching predicate. Rejected because the design requires every hit and the selected decision to remain auditable at one boundary.

### 3. Append one typed event per hit as a single ledger transaction

Retain the existing singular `trigger_evaluated` payload and close its `boundary_key` structure. Extend ledger validation and append helpers to accept a canonically ordered hit batch under the run lock. Build all entries against one in-memory accepted prefix, verify common boundary identity, unique `(boundary, signature, code)`, at most one selected F2/F3 hit and byte-equivalent idempotent replay, then atomically replace the ledger once. No-hit evaluation writes nothing. The batch advances only `event_seq`; it cannot change the active pointer or `revision_seq`.

This keeps current metric semantics—one trigger code per event—and avoids a breaking aggregate `hits[]` payload. Existing static metric fixtures are updated only if their payload is no longer Schema-valid; the formulas remain unchanged.

### 4. Define three closed internal artifacts

Add closed, versioned Schemas and positive/negative examples for:

- `revision-trigger-evaluation`: boundary anchors, ordered normalized hits, selection/local routing and source-ledger hash.
- `revision-patch`: source/trigger binding, requested F2/F3 level, ordered discriminated `patch_ops`, rationale and expected effect fields accepted for the future producer but not trusted for validation. `add_file_slot` and `re_adopt` explicitly name the affected build-artifact ids (an empty set is required for a non-link-source slot), because candidate completion cannot guess a build-graph companion edit.
- `revision-candidate`: `candidate_id=candidate-<selected_event_seq>`, source refs, patch ref, candidate Plan/ref, derived Blueprint/manifest/map refs, lineage/obligation map, complete migration ref, invariant/full-lint report refs and content hashes.

Each operator uses semantic ids (`task_uid`, work-package id, contract id, slot id, requirement id and path) rather than JSON Pointer. The Schema expresses the required operands and forbids operands belonging to another discriminator. Referential, partition and invariant checks remain deterministic validators rather than being duplicated as large conditional Schema logic.

Alternative considered: reuse the free-form `revision_activated.patch_ops[]`. Rejected because `additionalProperties` cannot enforce the M1-10 closed language; activation will later consume the canonical closed patch by reference/projection.

### 5. Apply the full patch on an isolated semantic copy

Normalize and Schema-validate the complete patch before applying any operation. Clone the source commitment/architecture/work-package/task IR in memory and execute operations in declared order against stable semantic ids. F2 allows only decomposition edits and leaves the commitment and architecture byte-equivalent. F3 permits the structural operators plus F2 edits proven necessary to close that structural delta. No intermediate state is published or independently required to lint; only the complete result is eligible.

After application, feed the result into the existing candidate-completion route: contract closure and ownership checks, stable uid/digest derivation, Linker topological ids, coverage, basic/full lint, Delivery Blueprint compilation, manifest and contract-map derivation. The operator layer never invents missing companion edits. A dangling owner, reference, contract edge, build slot or acceptance mapping rejects the whole patch.

Operator families are implemented as direct transformations in the same module, with shared existing Plan/architecture validators supplying closure. This is one coherent operator engine, not one framework/class per operation.

### 6. Prove invariants before classifying migration

Candidate validation emits an invariant report with exact old/new anchors:

- INV-1 compares the complete canonical commitment projection and hash; any changed normative requirements, classifications, test contract, build variants or frozen budgets fails.
- INV-2 recomputes normative-requirement primary ownership from the candidate and requires the same frozen requirement set with exactly one primary owner each.
- INV-3 consumes explicit operation lineage and obligation mappings, recomputes old/new task obligations and earliest legal acceptance gates, and requires every old obligation/file/test to have a non-weaker successor mapping. It never infers lineage from titles, `T-###` ids or text similarity.

Only after these checks does the existing migration classifier compare the accepted Plan State/file ledger with the completed candidate. Its output must cover every old/new task and old realized file and carry any pending group/retirement/re-adoption facts already consumed by M1-8/M1-9. A concrete counterexample exposing an actual M1-4d classifier defect is handled as the smallest necessary correction under system design §10.2.1; absent such evidence the classifier is reused unchanged.

### 7. Publish candidates with an event-scoped commit marker

Candidate construction does not require a workspace checkout. Under the run lock, write the complete bundle to `plan/_s4r/.candidate_<event_seq>.pending/`, including the selected trigger entry and source-ledger hash, then validate every content hash. Append/replay the ordered trigger-event batch atomically. Finally atomically rename the staged directory to `plan/_s4r/candidate_<event_seq>/`; the closed `candidate.json` is the non-authoritative bundle commit marker. Candidate files do not use `plan/versions/` names and do not reserve the future C.A.P successor.

Recovery runs before another trigger/candidate operation. If no matching accepted selected event exists, a pending bundle is ignored as uncommitted transaction data. If the selected event exists and staged/final bytes match, recovery completes or accepts the single rename. If both forms conflict, the selected event/source anchors drift, or any hash differs, it reports artifact damage. It never appends rejection/activation, edits State/file ledger/workspace, or calls an Agent. A trigger-only evaluation with no supplied patch ends after the ledger append and remains a legal input for later patch production.

Alternative considered: publish the candidate before its trigger event or recompute a missing patch after restart. Rejected because the first exposes an unanchored candidate and the second may lack the original future producer output.

### 8. Integrate narrowly and preserve public behavior

The S6 boundary invokes evaluation only after existing WAL reconciliation and stable-context validation. Provider submission invokes the TR-8 gate before candidate installation. Selected F2/F3 results produce `StagePause(kind="revision_handoff", selected_event_seq, candidate_ref)` through `StageResult.pause`; the orchestrator restores S6 from `running` to `pending`, records the stage event, returns non-terminal zero, and emits no termination request or S9 report. Replay of the same ledger/candidate returns the same pause without another trigger event. Tests invoke the optional internal frozen `revision_patch_provider` seam to close candidate construction; it never calls an Agent. Register `revision_mechanism` in `pyproject.toml`; add deterministic MQTT/non-MQTT fixture generation and focused Schema, predicate, operator, invariant, ledger, publication and recovery tests. No CLI syntax, Run Schema state enum, Agent registry, prompt or runtime dependency changes.

### 9. Persist Plan local identity in Plan Schema 5.0

The current formal Plan 4.0 drops `local_task_id` even though stable `task_uid` is derived from `[work_package, local_task_id]`. Two distinct draft tasks can therefore produce valid formal Plans whose local dependency references cannot be reconstructed: once only topological `T-###` ids and derived uids remain, Plan-to-PlanDraftIR cannot recover the original package-local dependency names without guessing. M1-10 requires exact semantic ids for patch operands and deterministic Plan-to-IR replay, so this is a concrete persistence defect rather than a compatibility preference.

Upgrade only the current formal Plan contract and its fresh-run producers/consumers/fixtures to Schema 5.0. Each task stores required `local_task_id`; Linker continues deriving `task_uid` from `[work_package, local_task_id]`; Blueprint semantic projection excludes `local_task_id`; and `plan_to_draft_ir(plan)` restores package-local ids and dependency references losslessly. Move `CandidateCompletion` and `complete_plan_candidate(...)` into the shared Plan speclib so initial S4 completion and revision completion use one path. The current implementation rejects Plan 4.0; it does not convert it, rewrite archived runs or change Run Schema 4.0 and other unrelated contracts. Historical runs remain readable only by their matching historical implementation.

### 10. Review corrections use authoritative evidence and convergent recovery

The production S6 path SHALL project trigger facts only from the current active Plan, State, file/revision ledgers, Blueprint, contract map and immutable evidence whose attempt, task uid, Plan ref, path and hash all validate. Historical failure files and Agent-authored booleans are not machine facts. A stable attempt boundary is established by the absence of a started attempt, unmatched lease, verification WAL, unresolved repair group or revision transaction; a task State row may remain `in_progress` after its latest attempt has durably failed. F1 and provider-submission observations may be recorded at that boundary, while F2/F3 selection waits for a revision-eligible task boundary.

The frozen optional configuration uses `revision.theta2` and `revision.theta6`, both required together and in `(0,1]` when the block is present. A missing block preserves ordinary production behavior and does not silently substitute trial values. TR-4 preflight uses the authority formula added to system design and pipeline design: `4000 * s6_owned_file_count + ceil(canonical_empty_output_envelope_bytes / 4)`, compared with the smaller effective output limit of the frozen normal Coder and primary Fixer routes.

INV-3 mappings identify both source and target task uids. Every obligation absent from the same retained task requires exactly one explicit mapping to obligations that exist on lineage- or operator-proven successor tasks. The validator recomputes file/build ownership and the test nodeid, enabled state and earliest legal gate; it does not trust `acceptance_not_weaker`. `add_work_package` rejects ownership collisions rather than silently moving existing files or responsibilities.

Before returning an already selected revision handoff, S6 reconciles its event-scoped candidate. An accepted event plus matching pending bytes completes the atomic rename; a matching final candidate is replayed; an accepted event without candidate remains a legal empty handoff; and an unaccepted pending directory remains inert. F3 affected groups are the connected components of the actual changed task/path/contract and Blueprint build graph, not one group containing every build artifact. Unattributable structural seeds reject the candidate.

This correction does not add a Diagnoser producer, a public configuration surface, RG/activation behavior or a later-milestone fallback. The narrow TR-4 formula edit in `project_docs/` is the user-authorized exception to the original documentation non-goal.

## Risks / Trade-offs

- **[Risk] Trigger facts currently originate in several S6 artifacts and may not all carry a common stable anchor.** → Build one boundary projection after current execution validation; if a required fact cannot be derived, fail that predicate rather than trusting an unbound local dictionary.
- **[Risk] Closed operations can leave a structurally plausible but obligation-weakened Plan.** → Treat Linker/full lint and INV-1/2/3 as cumulative mandatory checks; never auto-fill omitted mappings.
- **[Risk] F3 operations overlap M1-8 quarantine and M1-9 group contracts.** → Produce only their existing migration inputs and verify exact Schema compatibility; do not perform materialization or group execution in M1-10.
- **[Risk] Event append can succeed while candidate rename is interrupted.** → Stage all immutable bytes first and use the accepted selected event plus `candidate.json` hashes as the sole reconciliation identity.
- **[Risk] Implementing every operator could encourage a generic patch DSL.** → Keep the enum and operands exactly equal to pipeline §6.2, with no extension registry, JSON Patch fallback or implicit rename/delete behavior.
- **[Risk] M1-10 fixtures could be mistaken for production PlanReviser quality evidence.** → Use frozen deterministic patches, label them mechanism-only, and make M1-14 calibration and production enablement explicit non-goals.

## Migration Plan

1. Verify the archived M1-9 acceptance and current focused baseline; record exact commands/results in the later implementation brief or acceptance record without changing the archive.
2. Upgrade the current formal Plan path and fresh-run fixtures to Plan 5.0, establish lossless Plan-to-PlanDraftIR recovery and share the candidate completion path; reject Plan 4.0 without conversion.
3. Add the closed Schemas/examples and pure trigger/operator/candidate validators, then integrate typed-ledger batch append and event-scoped candidate publication.
4. Connect the existing S6 boundary and provider-submission path behind the internal M1 mechanism path; keep public CLI and production F2/F3 activation disabled.
5. Land fixture-generated MQTT/non-MQTT coverage, focused regressions and public CI. Rollback before archive is removal of this unactivated producer path and its new artifacts; no accepted Plan version or workspace migration is involved.
6. Obtain explicit responsible-owner review of the final diff and evidence before marking that gate complete or archiving the change.
