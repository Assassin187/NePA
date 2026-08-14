## Context

See `proposal.md` for motivation and `specs/run-lifecycle/spec.md` for the behavior contract. The repository currently has M0 Schemas, canonical JSON support, deterministic input lints, a Typer lint-only CLI, and confirmed gold/freeze records; it does not yet have configuration models, a run store, an orchestrator, stage controllers, or runtime tests.

M1-1 is deterministic infrastructure at L1/L2/L4. It must prepare the state and side-effect boundary consumed by later S4-S6 work without implementing any LLM provider, agent, Plan compiler, scaffold, or coder. The authoritative constraints are §4.2, §4.4, §4.7, §4.8, §5 general conventions, §5.4, §5.6.2, §5.6.5.5, §6.9, §8.1-§8.3, §8.7, §9.1.2, and §10.2 M1-1/D1.4/D1.9/D1.10.

## Goals / Non-Goals

**Goals:**

- Establish one typed, deterministic runtime path from resolved configuration and validated M0 inputs to a durable non-terminal Run v3.
- Make every run-state commit atomic, canonical, hash-verifiable, and recoverable after process death.
- Define the stage-controller boundary that later M1 changes plug into while keeping S4-S6 execution serial and controller-owned.
- Persist budget and termination decisions before the controller performs the next side effect.
- Produce the smallest Report v2/report receipt path needed to complete a controlled S4-S6 exit.
- Keep time, process-liveness, and stage implementations injectable so crash and boundary behavior is deterministically testable.

**Non-Goals:**

- No provider requests, response cache, LLM telemetry, agent roles, prompts, Plan/Plan State, Blueprint, workspace/git, build, test-round, or repair behavior.
- No public `run`, `resume`, or `status` CLI commands; M1-7 will adapt the runtime API to Typer.
- No doc-run execution and no real S4-S6 stage implementation. M1-1 supports the Run v3 shapes and orchestration boundary needed by spec-run; tests use deterministic fake controllers.
- No full S9 evidence aggregation or Reporter LLM. The partial-report producer only covers controlled exits with artifacts available at the M1-1 boundary.
- No universal stage-receipt schema invented ahead of S4-S6. Each later stage owns its documented receipt shape; M1-1 validates generic `{path, sha256}` anchors and the Report v2 commit it directly produces.

## Decisions

### 1. Add a closed typed configuration model and persist its canonical public snapshot

`nepa/config.py` will define the §8.3 configuration hierarchy with Pydantic v2, reject undeclared configuration fields, and merge defaults, YAML, and explicit overrides before a run is created. Credential configuration stores environment-variable names, never values; provider adapters introduced by M1-2 will resolve values only at call time. The snapshot is `model_dump(mode="json")` encoded through the existing project canonical JSON function and hashed before Run v3 publication.

This uses the design-mandated Pydantic dependency and prevents later providers from defining a second configuration path. A loose dictionary loader was rejected because it cannot enforce the documented closed shape or give stable snapshots. M1-1 will model later-section configuration fields needed for snapshot compatibility but will not interpret provider, role, or planning behavior. S4 repair defaults remain configurable placeholders until M1-4a3 freezes their production values; M1-1 tests persistence, not qualification.

### 2. Reuse M0 validation and canonicalization for an explicit spec-run initializer

Initialization will validate Spec IR, Target Profile, and Test Bundle through the existing `nepa.speclib` path before creating a committed run. It will copy Spec IR bytes to `spec/spec.json`, canonicalize the validated Target Profile into the run-local closed two-field object, and preserve the already canonical Test Bundle bytes in `inputs/test_bundle.json`. Run v3 will record the caller Spec raw-byte reference, the run-local Target canonical reference, and the Test Bundle id/version plus canonical run-local reference exactly as §5.6.2 and §5.6.5.5 distinguish them.

Run creation uses a sibling staging directory and publishes the run directory by atomic rename only after all frozen inputs and the initial Run v3 validate. This avoids a visible half-initialized run. Existing source files and freeze records are read-only. Copying first and validating later was rejected because a crash could leave a directory that looks resumable but has no valid initial commit.

### 3. Make `RunStore` the sole publisher of durable run facts

`nepa/run_store.py` will own canonical JSON read/write, raw-byte hashing, same-filesystem temporary paths, `fsync`, atomic replacement, stage-event append, immutable publication, Run v3 Schema validation, and reference verification. It will not contain orchestration policy. All paths supplied by later stages are resolved relative to one validated run root; callers cannot publish through a second direct file-writing path.

Mutable artifacts such as `run.json` use validated atomic replacement. Immutable artifacts and receipts accept replay only when canonical bytes are identical. `output_refs` are verified against actual bytes and applicable Schema/receipt checks before a `done` stage is consumed. A generic object repository or database was rejected because filesystem artifacts are the system's specified audit and resume boundary.

### 4. Serialize controllers with an advisory run lock and a small stage protocol

The orchestrator acquires a non-blocking process-held advisory lock under the run directory for the whole controller session. On Linux/WSL2 this proves that another cooperating controller is not active; failure to acquire rejects concurrent run/resume without mutating Run v3. PID files alone were rejected because stale PIDs and PID reuse do not prove ownership.

Later stages register deterministic `StageController` implementations. The initial orchestrator accepts only the S4, S5, S6, and minimal S9 slots, invokes S4-S6 serially, and treats their returned commit evidence or typed failure as data. Fake controllers exercise this interface in M1-1 tests. No LLM can call a state-transition method directly.

### 5. Separate stage work from its Run v3 commit point

Before stage work, the orchestrator atomically records the admitted stage as `running` after checking upstream completion and budget. A successful controller result is validated with its output refs, then one atomic Run v3 replacement records `done`, end time, and the refs; this is the logical stage commit point. A controlled stage failure atomically records `failed` and the single termination request. An unexpected exception or invariant failure follows the internal-error path and never becomes a process outcome.

The run store rejects unsupported transitions and a caller cannot mark `done` without validated evidence. Later S4-S6 changes may add stage-specific reconciliation before their commit, but they must use this same final Run v3 path.

### 6. Account budget at explicit controller boundaries with injectable clocks

`BudgetTracker` uses a monotonic clock supplied by the controller session. It synchronizes elapsed active time at stage boundaries, immediately before external-call admission, immediately after the returned usage is known, and during normal finalization. The persisted total is the previous Run v3 value plus current-session deltas; wall time between sessions is never inferred from UTC timestamps.

Provider usage is accepted through a typed `UsageDelta` supplied by M1-2. Cache replay sets provider increments to zero but still synchronizes active time. The pre-call check and post-call charge are distinct: a permitted call may exhaust the remaining budget, in which case its actual usage is persisted and the next action is blocked. Automatically increasing a limit or ignoring an overrun was rejected because §4.7 requires an honest controlled exit.

### 7. Persist termination intent before routing and keep the three terminal branches disjoint

Expected stage/input/LLM/budget failures use a typed `ControlledExit` with a stable reason code and target stage. The orchestrator first persists the affected stage state plus the one `termination_request`; after that commit, only S9 is eligible. S9 budget checks use `enforce=False`. Planned stop is derived from sealed `config_snapshot.run.until`, never from a resume-time override, and finalizes directly after the target stage without a report. Internal exceptions finalize as `internal_error`, exit 1, with no outcome.

If a second controlled-exit request is proposed, identical data is an idempotent replay and conflicting data is an internal artifact conflict. This preserves one authoritative decision for report reproduction.

### 8. Implement a deterministic partial Report v2 producer for controlled exits

`nepa/stages/s9_report.py` will inspect only validated artifacts and independent output refs, build the existing Report v2 availability envelopes, copy the termination reason exactly, classify the controlled run according to §9.1.2, validate the object, and atomically publish `report/report.json`. It will also write a deterministic minimal `report/report.md` containing the same available facts; no Reporter judgment or new process decision is introduced. One S9 receipt binds both files, and `stages.s9=done` plus its refs is the commit point before controlled-exit finalization.

The minimal producer reports unavailable/not-run data honestly and cannot manufacture Plan, coverage, code, test, model, or repair facts. Full aggregation remains a later S9 change. A report generator that infers a fresh reason from the filesystem was rejected because `termination_request.reason` is the only decision source.

### 9. Reconcile persisted facts before any resumed work

After acquiring the run lock, resume validates Run v3 and its configuration hash, then converts each orphaned `running` stage to `failed` with the exact crash message without creating a termination request. With no request, the first incomplete ordinary stage can retry via `failed -> running`. With an existing request and no terminal kind, all non-S9 work is bypassed; a valid committed S9 can finalize directly, while an incomplete/orphaned S9 retries. A `done` S9 whose report, receipt, hash, Schema, or request binding fails is fail-stop and finalizes `internal_error` without reopening `done`.

M1-1 does not implement S4/S5/S6 internal checkpoint reconciliation. Their later changes perform stage-specific reconciliation before returning to this shared transition path.

### 10. Preserve the existing CLI and M0 contracts

The current lint commands remain registered exactly as they are. Runtime entry functions are Python APIs until M1-7 adds Typer commands. The existing Run and Report Schemas are extended only if a direct design contract is currently absent; every Schema change gets a minimal valid example and evidence-backed negative tests. Gold files and confirmed freeze records are hash-checked in change-level validation and never rewritten.

## Interfaces and Ownership

The signatures below are the implementation boundary for this change; names may be mechanically adjusted only if the resulting ownership and behavior remain identical.

```python
# nepa/config.py
def load_config(path: Path | None, overrides: Mapping[str, object] | None = None) -> ResolvedConfig: ...
def public_config_snapshot(config: ResolvedConfig) -> dict[str, object]: ...

# nepa/run_store.py
class RunStore:
    @classmethod
    def initialize_spec_run(cls, runs_root: Path, inputs: SpecRunInputs, config: ResolvedConfig) -> "RunStore": ...
    def load_run(self) -> dict[str, object]: ...
    def replace_run(self, run: Mapping[str, object]) -> None: ...
    def publish_immutable_json(self, relative_path: str, value: object, *, schema_name: str | None = None) -> ArtifactRef: ...
    def publish_immutable_bytes(self, relative_path: str, data: bytes) -> ArtifactRef: ...
    def verify_ref(self, ref: ArtifactRef, *, schema_name: str | None = None) -> None: ...
    def append_stage_event(self, event: Mapping[str, object]) -> None: ...
    def controller_lock(self) -> ContextManager[None]: ...

# nepa/orchestrator.py
class StageController(Protocol):
    def run(self, context: StageContext) -> StageResult: ...

class Orchestrator:
    def run_spec(self, store: RunStore) -> int: ...
    def resume(self, store: RunStore) -> int: ...
    def admit_external_call(self, store: RunStore) -> None: ...
    def record_external_usage(self, store: RunStore, usage: UsageDelta) -> None: ...

# nepa/stages/s9_report.py
def publish_controlled_exit_report(store: RunStore) -> ArtifactRef: ...
```

`RunStore` exclusively owns filesystem mutation; `Orchestrator` exclusively owns lifecycle and budget policy; stage controllers own their in-stage deterministic/LLM workflows in later changes; the partial report producer owns only controlled-exit report construction. This preserves the L1/L2/L4 boundary and leaves L3 absent from M1-1.

## Persistent Commit and Recovery Sequence

```text
initialize:
  validate source inputs
  -> build complete run in sibling staging directory
  -> fsync files/directories
  -> atomic rename to final run_id

ordinary stage:
  pre-budget check
  -> run.json: pending/failed -> running
  -> controller work
  -> validate artifacts/output refs
  -> run.json: running -> done + output_refs

controlled exit:
  run.json: affected stage failed/pending + termination_request
  -> S9 running
  -> publish and validate report files + receipt
  -> run.json: S9 done + output_refs
  -> run.json: controlled_exit + outcome + exit_code

resume:
  acquire controller lock
  -> validate run/config hash
  -> running -> failed crash reconciliation
  -> reconcile existing request/S9 facts
  -> retry first eligible stage or finalize
```

## Validation Strategy

- Unit tests use temporary run roots, deterministic UTC/monotonic clocks, fake stage controllers, and explicit crash hooks around each publication boundary.
- Schema tests cover initial/non-terminal Run v3, each terminal branch, request-stage status binding, Report v2 availability envelopes, and the M1 S9 receipt if a new Schema is required.
- Persistence tests interrupt before and after file replacement and stage commit points, then create a fresh store/orchestrator instance to prove recovery from disk rather than memory.
- Change-level validation runs the new runtime tests, the full existing M0 suite, all M0 lint commands, Schema/example validation, and a hash comparison against the signed freeze record.
- D1.4 and D1.10 remain milestone-level acceptance: later S4-S6 changes must reuse these injected boundaries with their real artifacts. M1-1 proves only the shared runtime behavior with deterministic fixtures.

## Risks / Trade-offs

- [Run/Report Schemas from M0 may expose a direct contradiction with the detailed runtime rules during implementation] → Stop before changing `project_docs/system_design.md`; report the exact conflict and obtain explicit authorization if the authoritative design itself must change.
- [Advisory locking only coordinates NePA processes that use the shared store] → Keep all run/resume entry paths inside `RunStore.controller_lock`; do not claim protection from unrelated manual filesystem edits, which are detected by hash/Schema verification.
- [A generic output-ref checker cannot know later stage-specific receipt semantics] → Verify generic path/hash integrity now and require each later stage to add its documented receipt validator before its controller is accepted.
- [The minimal partial reporter could grow into an accidental parallel full S9] → Limit inputs and fields to the existing Report v2 controlled-exit branches and defer Reporter prose, terminal-test aggregation, repair convergence, and full coverage joining.
- [Adding a full §8.3 configuration shape before providers exist can create unused behavior] → Persist and validate the documented shape, but do not add provider calls, inferred defaults, compatibility shims, or model-specific runtime decisions in M1-1.

## Migration Plan

1. Add the typed configuration and runtime dependency while retaining all current M0 CLI behavior.
2. Add and validate the shared store and initial spec-run fixture path without exposing new CLI commands.
3. Add orchestrator, budget, termination, partial-report, and resume behavior behind Python APIs.
4. Run the complete M0 and M1-1 validation set and confirm signed input hashes are unchanged.

Rollback consists of reverting only the M1-1 implementation and artifacts; no existing M0 data migration or gold-file rewrite is required. Run directories produced during development are gitignored experimental outputs and are not authoritative project inputs.
