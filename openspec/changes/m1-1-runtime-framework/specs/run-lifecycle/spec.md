## Purpose

Define the deterministic and auditable lifecycle contract that lets M1 spec-runs freeze their inputs, persist Run v3 state, enforce bounded execution, terminate honestly, and resume safely after interruption.

## ADDED Requirements

### Requirement: Configuration is resolved once and sealed without secrets

The system SHALL resolve runtime configuration in the order default values, configuration file, then explicit invocation overrides, and SHALL persist the resolved configuration in `run.json.config_snapshot` with environment-variable names in place of API-key values. It SHALL compute `config_snapshot_sha256` from the canonical in-memory snapshot and SHALL reject load or resume when the persisted snapshot no longer matches that hash. This requirement implements §8.3 and §5.6.2.

#### Scenario: Configuration precedence is deterministic

- **WHEN** the same setting is present in defaults, a configuration file, and an explicit invocation override
- **THEN** the persisted snapshot contains the invocation value and repeated resolution of the same inputs produces the same canonical snapshot hash

#### Scenario: API key values are not persisted

- **WHEN** provider credentials are available through their configured environment variables during run initialization
- **THEN** neither `run.json` nor the resolved configuration snapshot contains the credential values and the snapshot retains only the configured environment-variable names

#### Scenario: Snapshot drift blocks resume

- **WHEN** a persisted `config_snapshot` is modified without a corresponding valid canonical hash
- **THEN** the run is rejected before any stage is resumed

### Requirement: Spec-run initialization freezes the authoritative inputs

The system SHALL create an isolated run directory for a valid spec-run, preserve the caller Spec IR as `spec/spec.json`, freeze the validated Target Profile as `inputs/target.json`, freeze the canonical Test Bundle description as `inputs/test_bundle.json`, and record the input references and required raw or canonical SHA-256 values in Run v3. The Target Profile and Test Bundle source assets SHALL remain unmodified. This requirement implements §4.2, §4.4, §5.6.2, and §5.6.5.5.

#### Scenario: Valid M0 inputs initialize a spec-run

- **WHEN** initialization receives the validated frozen gold Spec IR, Target Profile, Test Bundle, and a valid resolved configuration
- **THEN** it creates the documented run layout, writes the three run-local frozen inputs, and records input references whose hashes match the required source or canonical bytes

#### Scenario: Source input is not rewritten

- **WHEN** spec-run initialization succeeds
- **THEN** the bytes of the caller-provided Spec IR, Target Profile, and Test Bundle source files remain unchanged

#### Scenario: Invalid source input does not publish a run

- **WHEN** a required source input is missing or fails its applicable M0 validation during initialization
- **THEN** initialization fails without publishing a resumable run directory or any downstream stage output

#### Scenario: Frozen input drift is a controlled failure before S4

- **WHEN** a committed run-local input is missing or no longer matches its recorded hash when the controller prepares to admit S4
- **THEN** the system does not begin S4 and records the applicable controlled process failure without publishing downstream stage outputs

### Requirement: Run state and output anchors are atomically durable

The system SHALL use one durable run-store path for canonical JSON publication, atomic replacement, and SHA-256 references. A stage SHALL be considered complete only when its required output artifacts exist, validate against their contracts, and match the independent `output_refs` recorded at the stage commit point. This requirement implements §4.8, §5 general conventions, and §5.6.2.

#### Scenario: Atomic publication exposes only complete JSON

- **WHEN** a run artifact is published successfully
- **THEN** readers observe either the prior complete version or the new complete canonical version and never a partially written JSON document

#### Scenario: State-only completion is rejected

- **WHEN** a stage is marked `done` but a required output is absent, invalid, or does not match its recorded SHA-256 reference
- **THEN** the stage is not accepted as complete and downstream execution does not consume that output

#### Scenario: Idempotent publication requires identical bytes

- **WHEN** an immutable receipt or referenced artifact path is published again
- **THEN** identical canonical bytes are accepted as an idempotent replay and different bytes are rejected as an artifact conflict

### Requirement: The deterministic orchestrator owns stage progression

The system SHALL let deterministic controller code, rather than an LLM or persisted status alone, decide S4-S6 stage entry, state transitions, commit points, and termination routing. Re-executing a verified completed stage SHALL be a no-op, while an incomplete or failed ordinary stage MAY re-enter only through its documented recovery transition. This requirement implements §4.2, §4.8, and §10.2 M1-1.

#### Scenario: Verified completed stage is idempotent

- **WHEN** orchestration reaches a stage already marked `done` whose required artifacts and output references all verify
- **THEN** the controller performs no stage work and advances without changing the committed outputs

#### Scenario: Invalid transition is rejected

- **WHEN** a caller requests a stage-state transition that is not allowed by the documented lifecycle or omits required commit-point evidence
- **THEN** the transition is rejected and the prior durable state remains authoritative

#### Scenario: S4-S6 controllers are invoked in order

- **WHEN** a non-terminal spec-run has no controlled-exit request and each preceding stage completes successfully
- **THEN** the controller admits S4, S5, and S6 in order and does not admit a downstream stage before its upstream commit point verifies

### Requirement: Global budgets persist across resume and stop work at boundaries

The system SHALL account global wall-clock time as accumulated active controller time across sessions and SHALL persist actual provider cost and token usage without charging cached replays. It SHALL check existing budget before an external call and SHALL atomically record the call's new active time and actual usage immediately after the call. Exhaustion of any global budget SHALL prevent further ordinary stage work and request a controlled exit. This requirement implements §4.7 and contributes to D1.10.

#### Scenario: Offline time is excluded from the wall-clock budget

- **WHEN** a run is resumed after the controller has not been executing
- **THEN** the persisted wall-clock usage continues from prior active time without adding the offline interval

#### Scenario: Cached replay has zero provider usage increment

- **WHEN** a later component reports a response as a cache replay
- **THEN** cost and token budget increments are zero while local active controller time is still accumulated

#### Scenario: Exhausted budget before a stage prevents admission

- **WHEN** a global budget is already exhausted before S4, S5, or S6 begins
- **THEN** that stage remains `pending`, a controlled-exit request names that stage and the budget reason, and no stage side effect is started

#### Scenario: External-call usage is charged before further work

- **WHEN** an external call returns usage that exhausts a global budget
- **THEN** the returned usage and active time are durably recorded and no subsequent ordinary stage action is admitted

### Requirement: Termination branches have distinct persisted semantics

The system SHALL distinguish planned stops, controlled process exits, and NePA internal errors using the Run v3 terminal contracts. A planned stop SHALL have exit code 0 and no outcome, report, or `termination_request`. A controlled process exit SHALL first persist one `termination_request`, route through deterministic S9 partial reporting, and finalize only as degraded or failed with exit code 10 or 20. An internal error SHALL use exit code 1 and SHALL NOT be represented as a three-value process outcome. This requirement implements §4.7, §5.6.2, §8.7, and §9.1.2.

#### Scenario: M1 planned stop does not run S9

- **WHEN** S6 is committed and the sealed configuration requests the declared M1 stop at S6
- **THEN** the run finalizes with `termination_kind=planned_stop` and `exit_code=0`, does not create a process outcome or report, and does not enter S7, S8, or S9

#### Scenario: Controlled failure persists its decision before S9

- **WHEN** an expected S4-S6 process failure occurs
- **THEN** the affected stage is `failed` or remains `pending` as applicable, one controlled-exit request with a stable reason is atomically persisted, and only then is S9 admitted

#### Scenario: Internal invariant failure is not disguised

- **WHEN** a deterministic template/tool failure, violated NePA invariant, or unhandled implementation defect terminates the controller
- **THEN** the run finalizes as `internal_error` with exit code 1, produces at most a best-effort diagnostic package, and has no degraded/failed/success process outcome

### Requirement: Controlled exits produce a conditionally valid partial report

The M1 S9 core SHALL deterministically produce a Report v2 for controlled exits using only available, validated artifacts. It SHALL mark absent or invalid downstream artifacts with the documented availability envelopes, SHALL use `null` plus a machine-readable reason instead of fabricated zero values, and SHALL copy `termination_request.reason` exactly into `termination_reason`. This requirement implements §5.4, §6.9, and contributes to D1.10.

#### Scenario: Failure before Plan seal reports unavailable downstream facts

- **WHEN** a controlled exit reaches S9 before a valid Plan seal exists
- **THEN** the partial report is Schema-valid and marks coverage, generated-code, and test-dependent values as `unavailable` or `not_run` with machine-readable reasons

#### Scenario: Report reason is not reinterpreted

- **WHEN** S9 produces a partial report for a persisted controlled-exit request
- **THEN** `report.termination_reason` is field-for-field equal to `run.termination_request.reason`

#### Scenario: S9 ignores exhausted global budgets

- **WHEN** S9 is entered because a global budget is exhausted
- **THEN** its entry and final budget synchronization do not enforce the exhausted budget again and the partial report can be committed and finalized

### Requirement: Resume reconciles durable facts before continuing

The system SHALL resume only after confirming that the prior controller is no longer active. It SHALL reconcile Run v3, stage receipts, artifact hashes, and any persisted termination request before admitting work; it SHALL convert orphaned `running` stages to `failed` with the documented crash error without creating a new controlled-exit request. This requirement implements §4.8 and contributes to D1.4.

#### Scenario: Orphaned ordinary stage is retried

- **WHEN** resume finds an S4-S6 stage left `running` by a dead controller and no controlled-exit request exists
- **THEN** it atomically marks the stage `failed` with `process crashed mid-stage` and may retry it through the ordinary failed-to-running path

#### Scenario: Existing controlled-exit request bypasses ordinary stages

- **WHEN** resume finds a persisted controlled-exit request but no terminal kind
- **THEN** it skips every non-S9 stage, accepts an already valid S9 receipt or reruns an orphaned/incomplete S9, and finalizes from the original request

#### Scenario: Corrupt completed S9 is fail-stop

- **WHEN** resume finds S9 marked `done` but its receipt, Report Schema, report hash, or request binding is invalid
- **THEN** the system preserves the completed-stage history, does not reopen S9, and finalizes the run as `internal_error`

#### Scenario: Live controller prevents concurrent resume

- **WHEN** resume can confirm that the run's original controller is still active
- **THEN** it rejects the resume attempt without changing run state

### Requirement: M0 public behavior and frozen inputs remain unchanged

The runtime foundation SHALL preserve the existing `nepa lint spec`, `nepa lint target`, and `nepa lint test-bundle` behavior and SHALL NOT rewrite the signed M0 gold inputs or freeze records. This requirement implements §10.8 and preserves the M1 entry condition.

#### Scenario: M0 regression remains green

- **WHEN** the M1-1 change-level validation is run
- **THEN** all existing M0 tests and lint commands still pass and the three gold input hashes equal `configs/m0-default-inputs.freeze.yaml`
