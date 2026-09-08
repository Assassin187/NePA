## Why

M1-8 can now accept an E1+ workspace as `ready` or as a registered `pending_repair` checkpoint, but S6 still admits only ready E0 and cannot execute the migration modes or repair groups carried by an accepted F2/F3 revision. M1-9 is the next serial work item and must close that migration-verification boundary before trigger production, automatic activation, or circuit-breaker behavior can be implemented.

## What Changes

- Generalize S6 admission from the initial ready E0 baseline to the current accepted Plan/binding/epoch chain, including an E1+ `pending_repair` epoch whose frozen groups must be processed before ordinary work.
- Correct migration-State projection so activation publishes changed tasks as pending: `REVALIDATE` receives no premature success proof, `AMEND` retains its historical ordinary attempts and receives one independent T1 Fixer allowance even when those attempts are full, and `REGENERATE` starts a new normal generation at zero attempts.
- Execute migration work through the existing S6 path: REVALIDATE performs deterministic build/smoke verification without an LLM, AMEND invokes exactly one T1 Fixer, and REGENERATE uses the ordinary Coder/Fixer schedule subject to its new-generation and run-wide limits.
- Assemble each frozen F3 repair group in stable task order, retain intermediate candidates without publishing success, rerun the group's complete default build/smoke gate after each bounded pass, and publish success only when the whole group passes.
- Extend Task Evidence, Joint Evidence, verification WAL, Plan State/file-ledger publication and `verification_committed` events for migration and `group` results, including a same-tree evidence commit for pure revalidation.
- Reconcile every joint-verification interruption: before a legal commit, restore the group baseline while retaining spent calls, sequences, candidates and failures; after the legal commit, validate and publish every member atomically without replaying an Agent, validation, or commit.
- Reopen only dependencies proven unblocked by an accepted revision, block an exhausted group without any intermediate `done`, and continue only independent branches that can still receive complete validation.
- Add frozen MQTT and non-MQTT M1-9 fixtures and `s6_execution` acceptance cases for incompatible groups, full-history AMEND, zero-attempt REVALIDATE, dependency reopening, failure and every verification publication window.
- **Out of scope:** M1-10 trigger evaluation and patch operators; M1-11 candidate gates or automatic activation; M1-12 circuit breakers; M1-13 through M1-15 production evidence/calibration; M2 test assets/S7/S8; public CLI switches; edits to `project_docs/`, architecture prompts, or calibration lineage.

## Capabilities

### New Capabilities

- `s6-migration-joint-verification`: Migration-mode execution, frozen F3 repair-group assembly, whole-group validation, atomic evidence/commit/State publication, failure handling and recovery.

### Modified Capabilities

- `s6-f0-execution`: Replace the E0-only S6 admission boundary with current accepted Plan/binding/epoch admission while preserving ordinary F0 execution behavior.
- `plan-state-validation`: Project pending migration modes, independent AMEND usage, zero-attempt revalidation, revision-based dependency reopening and all-member group transitions and external validation.
- `plan-revision-infrastructure`: Represent group-kind joint verification, complete member evidence and commit facts, and idempotent verification-WAL/event recovery without treating verification as activation.

## Impact

- **Milestone/work item:** M1-9 only. Governing sections are `project_docs/system_design.md` §5.2.4-§5.2.5, §5.4, §5.6.7, §6.6 and §10.2.1-§10.2.2, plus `project_docs/pipeline_design_s4_s9.md` §5.6-§5.6.1 and §6.4-§6.5. The directly associated DoD is D1.15.
- **Prerequisite status:** `m1-8-s5-multi-epoch-materialization` is archived at `openspec/changes/archive/2026-09-08-m1-8-s5-multi-epoch-materialization/`; implementation commit `3a249de` and archive/finalization commit `292c632` supply the required E1+/F2 artifacts. M1-9 implementation must first verify that exact committed baseline and must not rewrite or silently repair its archived artifacts.
- **Affected paths:** the existing S6 controller and RunStore verification transaction; Plan-State/revision/materialization helpers; Task/Joint Evidence, verification-WAL and revision-ledger Schemas; joint Git commit support; S6 execution lint; frozen S6 fixture generation and tests.
- **Public contracts:** persisted M1 artifact contracts gain current-version/current-epoch migration and group forms. The user-facing CLI surface remains unchanged, F1 lease behavior remains valid, and no new Agent role or runtime dependency is introduced.
- **Frozen behavior:** accepted Plan versions, activation migration rows, epoch/binding receipts, prior task evidence/commits, M1-8 realized bytes, protocol-neutral Agent assets and ordinary F0/F1 semantics remain immutable.
- **Acceptance:** `uv run pytest -q -m s6_execution` must include the M1-9 cases, followed by the affected Schema/State/revision/S5/S6/recovery regressions, full public CI, strict current/all OpenSpec validation and `git diff --check`. M1-9 has no design-mandated milestone signature beyond the repository's explicit pre-archive owner review, and does not claim D1.13, D1.14 or the final M1-15 owner acceptance.
