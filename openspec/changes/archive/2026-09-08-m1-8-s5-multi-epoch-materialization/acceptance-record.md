# M1-8 post-remediation acceptance record

## Requirement-to-evidence closure

| Requirement | Schema-valid input and controller path | Persisted result and damage proof |
| --- | --- | --- |
| Canonical activation migration | Closed migration-report, revision-ledger and pending-state Schemas; F3 activation is created through `RunStore.activate_revision` | Activation hash chain retains `pending_groups`/`re_adopt`; F2, aliases, ordering, identity and closure violations are rejected |
| Structural E1 ready/retirement | MQTT and non-MQTT frozen F3 packages contain a changed Plan and Blueprint; stage tests activate changed owned/frozen paths | E1 checkpoint, ready receipt, binding, ledger, current copies, quarantine and zero-change replay are checked |
| E2 re-adoption | Accepted E2 activation names canonical quarantine source, target owner and byte hash | Original realized bytes and historical evidence are retained without a new verification claim |
| Strict pending repair | Accepted F3 activation carries frozen group closure; compiler/linker diagnostics are parsed structurally | Registered failures publish sorted group ids and failed build refs without smoke; unregistered, ambiguous, timeout, tool/sandbox, cleanup, template and unparsed failures reject |
| F2 metadata binding | Accepted F2 projection reuses the E0 epoch and workspace | Only version binding/current metadata copies change; epoch receipt, checkpoint, workspace and S5 instance remain byte-stable |
| Recovery and reconciliation | Fault hooks cover every action and publication suffix | Render, replace, new-stub, preserve, retire-slot, quarantine and re-adopt reverse to the predecessor; receipt, binding, version/epoch/current copies, ledger, workspace and build-evidence damage fail closed; a sole missing event is appended once after validation |

## Frozen fixtures and collection

- MQTT and non-MQTT S5 fixtures are generated deterministically through S4 publication and include canonical F2 plus structurally changed F3 activation packages.
- Generator output is compared byte-for-byte with every checked-in S5 fixture file; S6 and S6 lease provenance fixtures were regenerated after the S5 hashes changed.
- `pytest --collect-only -q -m s5_epoch` collected 48 intended tests out of 621 total; it did not pass through an empty selector.
- Stage-level tests use accepted activation, RunStore and the S5 controller for structural ready E1, retirement, registered and unregistered repair outcomes, E2 re-adoption and F2 binding. Pure projection tests remain supplementary.

## Commands and final results

- `uv run pytest -q -m s5_epoch` — 48 passed, 573 deselected.
- `uv run pytest -q` — 621 passed, including MQTT/non-MQTT real sandbox cases.
- S5/S6/S6-lease fixture dependency checks — 21 passed after ordered regeneration.
- Public Schema examples — 19 passed.
- `uv run python -m nepa lint spec gold_file/specIR.json` — valid, zero errors and warnings.
- `uv run ruff check .` — passed.
- `uv run mypy nepa` — passed.
- `openspec validate m1-8-s5-multi-epoch-materialization --strict` — valid.
- `openspec validate --all --strict` — 17 passed, 0 failed.
- `git diff --check` — passed.

## Scope review

Production searches found no migration compatibility aliases or activation-level
`pending_group_ids` fallback. Binding-named nested manifest/map refs are read and
verified during completed replay. `project_docs/` is unchanged. This change does
not implement M1-9 repair execution, M1-10 trigger/operator behavior, M1-11
automatic activation production, a public CLI, or a new owner-signature gate.
The pre-existing `.gitignore` worktree change was preserved rather than attributed
to this remediation.
