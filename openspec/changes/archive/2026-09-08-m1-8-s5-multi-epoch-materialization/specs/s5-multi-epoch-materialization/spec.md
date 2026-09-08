## Purpose

Define deterministic, protocol-neutral S5 materialization for E1 and later epochs while preserving accepted implementation, isolating retired content, and publishing auditable ready or registered pending-repair checkpoints.

## ADDED Requirements

### Requirement: E1+ admission consumes an already-activated structural version
S5 SHALL admit an E1+ instance only when the active pointer identifies an already-accepted F3 Plan version, its revision sequence and epoch agree with the latest accepted activation, the frozen Spec/Target/Test Bundle and configuration anchors validate, the prior epoch receipt/binding/checkpoint and current Plan State/file ledger validate, no earlier transaction remains unreconciled, and the recomputed Delivery Blueprint and stage-full Plan lint pass. The migration input SHALL be the frozen migration rows and pending-group declarations bound to that activation; S5 SHALL NOT synthesize an activation, infer migration from workspace names, or accept a caller-supplied unbound Blueprint. (Design 8.0.2: §5.6.7, §6.5, §10.2.2; pipeline design 2.0.2 §4.1-§4.4, §5.4; M1-8.)

#### Scenario: Accepted F3 input is admitted
- **WHEN** the active F3 Plan, activation, frozen migration input, preceding epoch, State, ledgers, recomputed Blueprint and full lint all agree after reconciliation
- **THEN** S5 may derive the E1+ difference without invoking an LLM or changing the active Plan

#### Scenario: An artificial Blueprint is not bound to an activation
- **WHEN** a test or caller supplies a structurally valid Blueprint difference without the complete frozen accepted Plan/migration/epoch bindings
- **THEN** S5 rejects admission before modifying the workspace

#### Scenario: Structural migration declarations use the canonical activation payload
- **WHEN** an F3 activation declares repair groups or re-adoption operations
- **THEN** those declarations are closed canonical rows inside `activation.migration`, bind the activation hash chain, and are rejected when missing, aliased, unordered, duplicated, or inconsistent with the active Plan, Blueprint, revision, tasks, files or quarantine ledger

### Requirement: Multi-epoch materialization changes only the declared difference
For each admitted E1+ Blueprint difference, S5 SHALL deterministically render every new or changed `s5_frozen` file, create a deterministic stub for every new `s6_owned` slot, preserve the exact bytes of every retained realized `s6_owned` file, and leave an owner-only F2 change out of source materialization. It SHALL derive all actions from the old/new expanded Blueprints, current file ledger and explicit migration rows, and SHALL require the resulting active source path set to equal the new Blueprint path set after excluding declared build outputs and registered quarantine paths. It SHALL NOT overwrite retained realized task-owned content, infer an action from a suffix or protocol name, or run an LLM. (Design 8.0.2: §5.6.5.4, §6.5; pipeline design 2.0.2 §3.3, §5.4; D1.7/D1.11/D1.12; M1-8.)

#### Scenario: A structural epoch adds frozen and task-owned slots
- **WHEN** an accepted E1 Blueprint adds one mechanically generated frozen path and one task-owned path
- **THEN** S5 renders the frozen file and a task stub while preserving all retained realized task-owned bytes

#### Scenario: A realized task-owned path survives the new Blueprint
- **WHEN** an existing realized `s6_owned` path remains active even though its owner or surrounding metadata changes
- **THEN** its bytes are unchanged by S5 and its prior verification is not promoted into proof for changed obligations

### Requirement: Retirement and re-adoption preserve historical content
When an explicit F3 migration retires a realized path, S5 SHALL move its bytes to the unique safe `_orphan/<epoch>/<original-path>` location, retain its historical realized evidence in a `quarantined` ledger row, and exclude that path from the active build graph. A slot-only retired path MAY be removed without fabricated evidence. A `re_adopt` migration SHALL identify the exact quarantine path, target slot and owner, move the preserved bytes into that declared active target, and leave the target without a new successful verification claim until M1-9 revalidation or repair completes. S5 SHALL NOT delete realized content, infer an implicit rename, or treat quarantine as task completion. (Design 8.0.2: §6.5; pipeline design 2.0.2 §3.3, §3.5, §5.4; D1.12; M1-8.)

#### Scenario: A realized slot is retired
- **WHEN** the frozen F3 migration removes an active realized path and names its retirement
- **THEN** the bytes remain under the epoch quarantine path and the ledger records the inactive quarantined history instead of deleting it

#### Scenario: Quarantined content is explicitly re-adopted
- **WHEN** a later frozen F3 migration names an existing quarantine path, new target slot and owner
- **THEN** S5 restores those bytes to the target and records pending validation without claiming a new done proof

### Requirement: E1+ readiness distinguishes registered incompatibility from failure
S5 SHALL execute every default build variant against the complete E1+ candidate tree after structural, declaration, build-graph, manifest, contract-map and ledger closure have passed. It SHALL publish `materialization_status=ready` only when every required build succeeds and all required smoke checks succeed. It SHALL publish `materialization_status=pending_repair` only when every build or link failure can be mechanically attributed to the exact affected closure of one or more migration groups already frozen in the activation, and SHALL retain the failed build refs and sorted pending group ids without claiming build or smoke success. An error outside those registered closures, an ambiguous attribution, or a deterministic template/tool failure SHALL prevent epoch acceptance under the applicable controlled-failure or internal-error semantics. S5 SHALL NOT call Fixer or execute repair groups. (Design 8.0.2: §6.5, §10.2.2; pipeline design 2.0.2 §5.4; D1.10/D1.12; M1-8.)

#### Scenario: E1 is immediately ready
- **WHEN** all default builds and executable smoke checks pass on the closed E1 candidate tree
- **THEN** the accepted epoch receipt reports `ready`, contains no pending group id and binds all successful result refs

#### Scenario: A registered interface migration breaks an old implementation
- **WHEN** every observed compile or link failure maps exactly to a frozen pending repair group
- **THEN** S5 may checkpoint the tree as `pending_repair` with the failed build refs and exact group ids but no successful smoke claim

#### Scenario: A failed build contains ineligible or partially mapped diagnostics
- **WHEN** a failed build timed out, reports a sandbox/template/tool failure, contains an unparsed error, or contains any compiler/linker diagnostic that maps to zero or multiple frozen groups
- **THEN** S5 rejects the epoch and does not publish `pending_repair`

#### Scenario: A failure is not registered
- **WHEN** any build failure cannot be located uniquely within the frozen migration-group closures
- **THEN** S5 rejects the epoch and does not label the failure as pending repair

### Requirement: Each accepted epoch has one immutable publication chain
After an E1+ candidate is accepted as `ready` or `pending_repair`, S5 SHALL create exactly one ordinary checkpoint commit on the preceding legal workspace history without reinitializing Git. The checkpoint SHALL bind the active Plan and epoch, after which S5 SHALL publish immutable build/smoke evidence, epoch-scoped manifest and contract-map copies, an epoch receipt, the current-version binding receipt, the projected file ledger and current manifest/map copies, and atomically mark the current Run S5 instance done with the epoch/binding refs. The accepted epoch receipt SHALL bind its Plan, Blueprint, checkpoint tree/commit, materialization status, result refs and pending group ids without forming a hash cycle. Only after Run acceptance SHALL the ledger append one matching `epoch_materialized` event. Prior epoch and version artifacts SHALL remain immutable. (Design 8.0.2: §5.6.5.4, §5.6.7, §6.5; pipeline design 2.0.2 §4.2-§4.4, §5.4; D1.12/D1.15; M1-8.)

#### Scenario: Ready E1 is accepted
- **WHEN** E1 passes its gates and its checkpoint exists
- **THEN** one E1 receipt, version binding, Run S5 instance and materialization event form a recomputable chain while E0 remains unchanged

#### Scenario: Pending-repair E1 is accepted
- **WHEN** E1 has only registered migration incompatibilities
- **THEN** the same publication chain is accepted with `pending_repair`, exact pending groups and no assertion that the workspace is executable

### Requirement: Multi-epoch recovery and replay are forward-only and idempotent
S5 SHALL reconcile activation state before materialization and SHALL persist an epoch-keyed staging record before changing workspace content. Before a valid epoch checkpoint exists, recovery SHALL restore only the current attempt's recorded paths to the preceding legal checkpoint and preserve all predecessor realized/quarantined content and accepted call evidence. After a valid checkpoint exists, recovery SHALL verify and reuse that exact commit and complete only the missing canonical receipt, binding, ledger, Run or event suffix without another materialization, build, smoke or commit. If the current epoch is already done and all bindings validate, replay SHALL return the accepted refs with zero changes to files, Git refs, artifact bytes, event count or timestamps. Conflicting accepted bytes or an unregistered workspace path SHALL fail as artifact damage. (Design 8.0.2: §4.8, §5.6.7, §6.5; pipeline design 2.0.2 §4.4, §5.4; D1.12/D1.15; M1-8.)

#### Scenario: S5 crashes before the E1 checkpoint
- **WHEN** interruption occurs after staging or applying part of the declared difference but before a legal checkpoint
- **THEN** resume restores only the recorded E1 attempt, preserves E0 and prior realized content, and may deterministically retry E1

#### Scenario: S5 crashes after the E1 checkpoint
- **WHEN** the checkpoint exists but a receipt, binding, mutable projection, Run update or materialization event is missing
- **THEN** resume completes the missing suffix from that checkpoint without a second commit or build execution

#### Scenario: Completed E1 is invoked again
- **WHEN** the same accepted E1 instance is replayed with all artifacts valid
- **THEN** S5 returns its existing refs and changes no persisted byte, timestamp or Git reference

#### Scenario: Completed E1 is missing only its event suffix
- **WHEN** Run already accepts E1 and the materialization event is absent
- **THEN** S5 validates the complete checkpoint, receipt, binding, immutable and current copies, ledger, workspace and evidence chain before appending exactly one event

#### Scenario: Completed E1 contains a damaged nested binding artifact
- **WHEN** a manifest or contract-map file referenced by the accepted binding is missing or differs from its reference
- **THEN** replay fails as artifact damage without appending an event, deleting pending state or returning accepted refs
