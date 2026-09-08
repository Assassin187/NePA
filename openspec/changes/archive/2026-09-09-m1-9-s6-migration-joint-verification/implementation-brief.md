# M1-9 implementation brief

## Inputs and authority

- The authoritative behavior is `project_docs/system_design.md` at the sections named by the proposal; this change does not edit it.
- The immutable predecessor is M1-8 implementation commit `3a249de`, finalization commit `292c632`, and `openspec/changes/archive/2026-09-08-m1-8-s5-multi-epoch-materialization/`.
- Accepted execution identity is derived from active pointer, latest accepted activation, current Plan ref, version binding, epoch receipt, immutable/current manifest and contract map, Plan State, file/revision ledgers, workspace checkpoint/HEAD, frozen inputs, and configuration.

## Contract closure map

| Fact | Producer | Persisted contract | Consumer / validation | Confirmed pre-M1-9 mismatch |
| --- | --- | --- | --- | --- |
| Current Plan/version/epoch | S4/S4R activation and S5 binding/materialization | active pointer, revision ledger, binding and epoch receipts | S6 admission, State snapshot lint, execution lint | S6 admission and some ledger checks assume 1.0.0/E0 |
| Migration classification and group | accepted activation | revision ledger migration rows and pending groups | State projection, S6 mode/group selection, execution lint | projection requires future REVALIDATE proof and rejects full-history AMEND |
| Task execution allocation | S6/RunStore | Plan State plus attempt or validation record | resume, budget checks, execution lint | only normal attempt allocation is complete |
| Per-task verification | S6 | Task Evidence v2 | State/file-ledger projection, commit validation, execution lint | Schema fixes plan_version/epoch and attempt minimum to E0/one |
| Joint verification | S6 | Joint Evidence v2 | joint commit, revision event, execution lint | contract is lease-only |
| Verification transaction | S6/RunStore | `plan/verification_pending.json` | pre/post-commit reconciliation | WAL is normal/lease-only and lacks group candidates/failures/activation baseline |
| Accepted verification history | S6 | `verification_committed` revision event | admission, recovery, execution lint | event supports normal/lease only |
| Realized ownership | S5/S6 | file ledger | whitelist, State/execution lint | publication helpers assume ordinary/lease result shapes |
| Stage completion | S6/orchestrator | Run S6 receipt | completed replay and downstream stage gate | receipt validation is anchored to E0-era context |

## Intended implementation path

Extend the existing Schema, Plan-State/revision, S6 controller, RunStore and Git transaction paths. Normal and F1 lease forms remain closed regressions. Migration singletons use the ordinary transaction with mode-aware allocation/evidence; F3 groups use the existing joint transaction mechanics with a separate `group` discriminator, never synthetic lease facts.

The legal commit remains the sole verification commit point. Before it, persisted candidates and evidence are recovery inputs only; after it, reconciliation may only publish the exact complete State/file-ledger/revision-ledger suffix. No compatibility upgrader, public CLI, new Agent role, trigger/activation producer, circuit breaker or M2 test path is introduced.

## Acceptance

Each task runs its focused verification before its checkbox is marked. Final acceptance comprises non-empty S6/S5 marker collections, the complete pytest suite, Ruff, mypy, public Spec lint, strict current/all OpenSpec validation and `git diff --check`. The D1.15 acceptance record must name every injected pre/post-commit boundary. Owner review remains an external unchecked gate until explicitly supplied.

## Review correction closure

The corrected path distinguishes the active F2 Blueprint/current binding from the reused receipt's materialized-Plan Blueprint; admits non-empty groups including M1-8 singleton groups while retaining lease cardinality; and separates the epoch ancestry anchor from each group's clean accepted transaction baseline. Group execution now freezes per-member writes to `deliverable_files ∩ affected_paths`, accepts non-empty legal response subsets, treats empty non-REVALIDATE output as a real failed attempt, resumes from the persisted candidate manifest, and attributes path/symbol/build-artifact diagnostics through frozen ownership before whole-group fallback. The replacement acceptance record supersedes, but does not delete, the original results.
