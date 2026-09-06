## Context

See `proposal.md` for motivation and milestone scope, and the three delta specs for normative behavior. The current repository already has one deterministic Delivery Compiler, full Plan lint, S4 publication/verification, canonical JSON helpers, atomic RunStore writes, a controller lock, revision activation/recovery primitives, and an Orchestrator that admits registered S4-S6 controllers. It currently publishes Run v3 plus file/revision ledger v1, has no production S5 controller, no packaged scaffold templates at the Blueprint-declared paths, no sandbox execution wrapper, and no manifest/map or epoch/binding receipt Schemas.

The archived M1-4d change records its implementation and acceptance as complete, but this planning turn has not rerun that code baseline. The apply phase must begin with focused M1-4d regressions and treat any failure as evidence to investigate, not as permission to redesign earlier work. The working tree also contains user changes to both design documents; they are authoritative inputs to this change and must not be edited or absorbed into the implementation diff.

M1-5 crosses the stage, persistence, rendering, build, and recovery sections, so the required §10.8 four-part derived brief is stored beside this design as `implementation-brief.md`. It adds no design decision and yields to `project_docs/system_design.md` on any conflict.

## Goals / Non-Goals

**Goals:**

- Add one production S5 controller on the existing orchestration path and close E0 from S4 admission through accepted typed receipts and ledger event.
- Keep renderability, path expansion, manifest/map projection, and output bytes deterministic and independently testable before side effects.
- Execute generated code only in the configured network-disabled sandbox and preserve build/smoke evidence needed to audit the accepted checkpoint.
- Make each crash window converge either to no accepted E0 or one exact accepted E0, with pointer/receipt/event semantics matching the current design.
- Cut fresh runs over coherently to Run v4 and v2 ledgers without changing the already selected architecture bundle or sealed Plan shape.

**Non-Goals:**

- No E1+ diff algorithm, quarantine/re-adoption, pending-repair group, F2 rebinding, or F3 activation controller is added here.
- No Coder/Fixer/PlanReviser invocation, Plan State initialization, Test Bundle test execution, new command-line option, or production parameter calibration is added here.
- The C declaration parser is not a general C frontend; it accepts exactly the finite declarations authorized by §5.2.1.
- No old run is converted in place and no dual-version compatibility reader is introduced.

## Decisions

### 1. Make a single fresh-run contract cutover while preserving the sealed Plan shape

`RunStore.initialize_spec_run` and S4 initial publication will emit Run v4 and file/revision ledger v2 for new runs. S4 will still publish the existing delivered Plan shape at 1.0.0/E0/revision-sequence 0 and an empty event ledger; it will not produce a rendering view, epoch receipt, or materialization event. The S4 verifier and existing revision helpers will be translated to the typed event envelope and will locate the most recent `revision_activated` event instead of assuming that event count equals revision sequence or that the ledger tail is an activation.

This translation is limited to keeping the already delivered dormant M1-4d chain coherent with the new persistence contract. It does not expose a trigger, candidate generator, or revision controller. Historical v1/v3 artifacts remain byte-preserved and are readable only by the matching historical code version, as required by §5.6.7.

Alternative considered: let S5 accept v1 ledgers and upgrade them in place. Rejected because S4 and S5 would then disagree on the fresh-run contract, crash recovery could observe a mixed version, and the authoritative design explicitly forbids automatic old-run migration.

### 2. Add one pure materialization compiler and share concrete file expansion

`nepa/speclib/materialization.py` will own the filesystem-free operations: derive the finite C99 rendering view, project immutable manifest/map values, render the complete path-to-bytes map, project the E0 file ledger, and validate a completed E0 binding. `nepa/speclib/delivery.py` will expose its concrete file-rule expansion as one canonical helper, replacing S4's private duplicate and supplying S5. This preserves a single source for message/type expansion, path identity, rule id, owner, contract, and build role.

The declaration parser will tokenize identifiers, punctuation, integer constants, qualifiers, pointers, fixed arrays, struct fields, enum members, typedefs, and function declarations. It will reject preprocessing directives, bodies, extern object definitions, variable-length arrays, function pointers, variadics, and declarations that do not consume all tokens. It will first register every declared export symbol, then resolve dependencies and includes, then topologically order declarations with UTF-8 byte ordering as the tie-break. Function normalization will produce the exact return type and named parameters needed for declarations and stubs. Implementation-source resolution will use only explicit data or the unique provider-task-deliverable ∩ Blueprint link-source intersection.

The rendering view is an internal deterministic value recorded in the S5 pending state; it is not added to the sealed Plan. Its public projections are the contract map and artifact manifest, both of which bind the Plan, Blueprint, version, and epoch.

Alternative considered: parse signatures with independent regular expressions at each template. Rejected because nested type declarations, dependency closure, and full-input rejection would diverge across headers and source stubs. A third-party C parser was also rejected because the authorized grammar is finite and no runtime dependency is needed.

### 3. Render exact Blueprint rules through packaged, strict templates

Templates will live at the existing compiler-declared `nepa/templates/...` paths and be included as package data. The renderer will resolve a template only from the allowlisted `template_path` already present in the Blueprint or from the explicit `s6_task` rule kind/build role; it will never select by suffix, directory, purpose text, or protocol. Jinja uses `StrictUndefined`, sorted inputs, fixed line endings, and a closed context.

The layout-header template emits ordered declarations and mechanically resolved includes. Mechanical type/codec templates consume only their declared `input_kinds`. The build template derives source sets, entry slots, outputs, release flags, sanitizer flags, `all`, and `clean` from the Blueprint and C99 rule. Task-owned sources receive only the functions assigned by the rendering view. The explicit entry-point rule receives the fixed POSIX wait/SIGTERM body and no protocol behavior. Documentation is mechanical and contains only declared project metadata.

`render_e0_files` returns the complete expected byte map before writing. The stage compares that map bidirectionally with expanded active Blueprint paths; build outputs are cleaned before checkpointing and are never silently admitted as source files.

Alternative considered: generate files directly inside the stage with `if path.endswith(...)` branches. Rejected because it would create an undeclared parallel layout policy and make protocol-neutrality and replay harder to prove.

### 4. Keep generated execution inside one narrow sandbox/build boundary

Add `nepa/tools/sandbox.py` with the design-prescribed list-argument execution contract and `nepa/tools/build.py` for variant and smoke orchestration. The sandbox wrapper will mount only the workspace, apply configured image/CPU/memory limits, disable networking, enforce timeouts, capture complete bounded process results, and append stage observations. Commands and Blueprint output paths are passed as argument vectors; no input is interpolated into a shell command.

Each variant starts from `make clean`, builds using the exact release or SAN command, and then runs smoke for every declared executable before the final clean. A fixed supervisor inside the container launches the artifact, observes early exit, sends SIGTERM after dwell, waits for grace, uses SIGKILL only to clean up a failure, normalizes SIGTERM to 143, and reports sanitizer text. Evidence is written immediately after each completed external action so crash recovery can reuse it.

`SmokeConfig` adds only positive `dwell_seconds` and `term_grace_seconds` to the resolved configuration and public snapshot. Ruff and mypy are development-only locked dependencies for the first CI workflow; no runtime dependency is added.

Alternative considered: compile or smoke directly on the host in tests and use Docker only in production. Rejected because generated code is untrusted and §8.5 prohibits host execution; tests instead inject a deterministic fake executor except for the marked sandbox integration case.

### 5. Publish E0 as a forward-only staged transaction with two explicit commit points

`S5MaterializationController.run` executes under the Orchestrator's existing run lock and never reacquires it. Before touching `workspace/`, it writes an atomically replaced, schema-validated pending record under the E0 epoch area containing the accepted input refs, rendering-view hash/value, expected file hashes, evidence refs, phase, and expected public values. E0 writes only paths listed in that record. Recovery may remove an uncommitted generated path only when its bytes still match the recorded attempted bytes; unrelated or conflicting content is artifact damage.

The ordered path is:

1. recover any pending E0 suffix, validate S4 admission, derive the view and byte map, and persist pending state;
2. atomically write the expected workspace files, run build/smoke, clean build outputs, and verify the active source tree exactly;
3. initialize git only if absent and create one checkpoint with fixed NePA identity/message and Plan/Epoch trailers; this valid checkpoint is the workspace commit point;
4. publish immutable build/smoke evidence, manifest/map version copies, epoch receipt, and then binding receipt; atomically replace the file ledger and current manifest/map copies;
5. return typed epoch/binding refs to the Orchestrator, whose existing atomic stage update is the S5 acceptance point;
6. invoke an optional controller post-commit hook to append the idempotent `epoch_materialized` event, then remove the pending record.

The Orchestrator will call the S5 reconciliation hook on resume before terminal/done short-circuiting, which closes the only window in which the Run accepts S5 but the event is absent. A crash before the checkpoint replays only recorded E0 outputs. A crash after the checkpoint never creates another commit; it verifies the commit tree/trailers and reconstructs only the missing canonical suffix. `verify_completed` is read-only once pending reconciliation is complete and rejects any accepted-byte conflict.

Alternative considered: append `epoch_materialized` before the Run stage commit. Rejected because the event would claim an epoch not yet accepted by the authoritative S5 Run update. Letting the S5 controller update Run itself was also rejected because stage lifecycle ownership belongs to the Orchestrator.

### 6. Use closed public evidence contracts and avoid receipt hash cycles

Add closed Schemas/examples for artifact manifest v2, contract map v2, epoch receipt 1.0, binding receipt 1.0, build result, smoke result, and S5 pending state; update Run, file ledger, revision ledger, S4 examples, and directly affected activation fixtures to their current fresh-run forms. Run v4 gives S5 a typed output object requiring `epoch_receipt` and `binding_receipt` refs when done.

The immutable epoch receipt binds Plan/Blueprint, checkpoint commit/tree, ready status, and build/smoke refs, but not a future binding or a file ledger that points back to it. The binding receipt is created afterward and binds the Plan, epoch receipt, and immutable manifest/map refs. The file ledger can then reference epoch proof for S5-frozen realized rows. Root manifest/map files are atomic current copies and are never accepted without the immutable binding receipt. S6-owned stubs stay slot-only even though bytes exist in the checkpoint.

Revision-ledger validation will use a generic canonical event-envelope chain plus closed payload validators. M1-5 enables only the `epoch_materialized` runtime producer; later event producers remain unreachable. The append operation deduplicates the exact E0 identity and rejects a conflicting second materialization fact.

Alternative considered: place manifest/map hashes directly in the epoch receipt and also place the epoch-receipt hash in those projections. Rejected because it creates a hash cycle and contradicts §5.6.7's epoch-then-binding order.

### 7. Freeze two real S4 outputs and make CI exercise the complete M1-5 boundary

The MQTT and existing non-MQTT inputs will each be passed through the delivered S4 completion/publication path with deterministic stub Agent outputs; the resulting Plan, Spec, Target, Test Bundle, Blueprint, and relevant S4 refs will be stored as version-controlled S5 fixtures. Hand-authored fixed slot tables are not acceptable. Fixture generation is a developer command, while CI consumes the frozen values and fails on unexplained drift.

Focused tests will separately cover pure renderability, output bytes, manifest/map closure, sandbox result handling, ledger/event validation, stage admission, all documented fault hooks, and zero-change replay. The `s5_epoch` marker groups the end-to-end fixture cases. The first CI workflow will install the locked project/dev environment, run schema examples, ruff, mypy, full pytest, gold lint, strict OpenSpec validation, and the Docker-backed S5 integration where the runner supports Docker.

Alternative considered: synthesize just enough Plan JSON inside each test. Rejected because it would bypass the exact S4 producer contract that M1-5 is required to consume and could conceal a principle-level baseline gap.

## Risks / Trade-offs

- [The finite C99 parser may reject a valid but unauthorized C declaration] → Keep the accepted grammar exactly aligned with §5.2.1, emit the contract/export and unconsumed token range, and treat any required expansion as a reported design blocker rather than a permissive fallback.
- [The existing frozen S4 outputs may expose a declaration or implementation binding that cannot be derived uniquely] → Preserve the fixtures and report the concrete counterexample under §10.2.1; do not edit the architecture prompt or Plan in this change without user authorization.
- [Changing Run and ledger versions touches established M1-4d paths] → Make one fresh-run cutover, update all direct readers/writers/fixtures together, and run the complete S4/revision/RunStore regression set before S5 work and again at final acceptance.
- [Docker availability can make local acceptance environment-dependent] → Keep pure/unit cases executor-injected, retain one explicitly marked real-sandbox integration, and require the final M1-5 evidence to include the actual configured sandbox run rather than treating fakes as build truth.
- [Crash recovery could delete user or predecessor content] → E0 operates only on a fresh absent workspace, records exact attempted path hashes before writes, and removes only matching uncommitted outputs; any unrecorded or conflicting path is fail-stop.
- [Build/smoke evidence includes nondeterministic durations] → Determinism is required for rendered source/tree and canonical projection from a recorded result, not for elapsed time across independent executions; once written, evidence is immutable and recovery reuses it.
- [The change necessarily spans more than three files] → The breadth follows the end-to-end M1-5 contract: stage controller, pure compiler/templates, sandbox/build boundary, public Schemas, and tests are all required to avoid a partial S5 that cannot be admitted, verified, or resumed.

## Migration Plan

1. Run and record focused M1-4d/S4/RunStore regressions, then add the locked development tools and CI skeleton without changing runtime behavior.
2. Land closed fresh-run Run/file/revision-ledger Schemas and examples, translate S4 publication/verifiers and revision helpers to typed events, and prove empty-ledger plus latest-activation behavior before adding S5.
3. Add shared concrete Blueprint expansion, the pure C99 rendering-view/compiler path, packaged templates, manifest/map projections, and two frozen S4-produced fixtures; keep side effects disabled while pure closure tests pass.
4. Add configuration, sandbox/build/smoke execution and evidence contracts, then validate the rendered fixtures in the actual configured sandbox.
5. Add E0 pending state, git checkpoint, receipt/binding/file-ledger/current-copy publication, Orchestrator post-commit/reconciliation hooks, and fault injection at every ordered boundary.
6. Run `s5_epoch`, all focused regressions, the full public CI suite, strict current/all OpenSpec validation, protocol-neutrality scans, and diff inspection. Confirm no S6/E1+/revision-controller/test-asset or `project_docs/` implementation work entered the change.

There is no in-place data rollback. Before any new v4/v2 run is created, the code deployment may be reverted normally. After such a run exists, its artifacts stay immutable and must be read with the matching implementation version; reverting code does not rewrite it into v3/v1. A failed E0 has no accepted binding and recovery follows the pending/checkpoint rules above; an accepted E0 is not rolled back in place.
