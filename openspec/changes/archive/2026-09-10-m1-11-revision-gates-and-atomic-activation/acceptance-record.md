# M1-11 acceptance record

> Supersession notice (2026-09-10): the review-remediation evidence at the end of this record supersedes the earlier statements that all tasks were complete and that the prior owner approval covered the final implementation. The earlier results and decision remain below only as historical evidence.

## Scope and prerequisite record

- Verification date: 2026-09-10 (Asia/Shanghai).
- Implementation baseline: `796f835`. Before implementation the only working-tree item was the untracked M1-11 change directory.
- The archived M1-10 record contains the responsible owner's dated approval of its corrected implementation and acceptance evidence. No other active change overlapped the revision/S5/S6/RunStore path.
- Pre-implementation evidence: `uv run pytest -q -m revision_mechanism` reported 43 passed; the combined M1-8/M1-9/M1-10, S5/S6, RunStore and orchestrator predecessor selection reported 260 passed; strict current/all OpenSpec validation passed with 17/17 items.
- This record covers M1-11 only: the RG-1 through RG-5, rejection, atomic activation and routing mechanism portion of D1.13. Production limits remain F2/F3 = 0/0.

## Requirement-to-evidence closure

| Requirement boundary | Schema/fixture evidence | Controller/publication evidence | Test evidence |
| --- | --- | --- | --- |
| Frozen handoff and ordered RG-1…RG-5 | closed `revision-gate-result` Schema/example and MQTT/non-MQTT gate cases | S6 consumes the existing `revision_handoff`, reconciles activation → materialization → verification, revalidates the authoritative boundary and persists ordered prefixes | gate/critic/rehearsal/rejected selector: 13 passed |
| RG-2 and RG-3 deterministic validation | candidate, migration and frozen configuration contracts | candidate completion/INV/link/full-lint replay; preservation, cost, level-count and call-cap checks; explicit build rate and frozen pricing | config/Schema/State/RunStore focused set and complete marker |
| RG-4 PlanCritic | unchanged closed PlanCritic output Schema; delta-closure projection tests | same role, route, temperature-zero rule and three inputs at S6; actual usage retained; artifact conflicts propagate as damage | Agent/S4 regression plus critic selector |
| RG-5 F3 rehearsal and F2 N/A | closed F3 rehearsal Schema/example and two-run fixture rows | two isolated S5 runs use the existing materialization algorithm, compare canonical results/trees and group attribution; F2 performs no rehearsal | isolated S5 rehearsal and activation selectors |
| First-failure rejection | tightened `candidate_rejected` ledger payload | one idempotent append; conflicting replay fails; pointer/State/file ledger/Run/current/workspace side-effect audit | five first-failure state-machine rows, end-to-end RG-3 rejection, replay/conflict tests |
| F2/F3 successor and activation | fresh-run activation WAL v2 plus tightened `revision_activated` F2/F3 discrimination | one frozen successor projection; WAL → Plan/binding → State → file ledger → revision ledger → pointer → Run/current → reconciliation; pointer is the sole commit | F2 same-epoch binding and F3 pending-materialization success tests |
| Crash recovery and admission order | WAL old/new bytes, refs, hashes and phases | pointer-old rollback/isolation; pointer-new forward completion; third pointer/hash/byte conflicts fail closed; reconciliation before every S4/S5/S6 admission | activation recovery selector: 20 passed, including every before/after boundary and the new-ledger/old-pointer window |
| Downstream routing and preservation | migrated State/file ledger and existing epoch/binding receipts | F2 resumes the M1-9 modes in the same epoch; F3 makes the next S5 epoch pending; rejection retains the original execution view | complete marker, predecessor S4/S5/S6/lease/migration suites and full pytest |
| Protocol neutrality and deterministic fixtures | MQTT and non-MQTT generated gate/rehearsal/activation/recovery rows bound to source hashes | one core path with no protocol-name branch | two temporary revision-fixture generations were byte-identical and matched checked-in output; dependent S5/S6/migration/lease provenance was regenerated in order |

## Final machine acceptance

- `uv run pytest -q -m revision_mechanism -k 'gate or critic or rehearsal or rejected'` — 13 passed, 762 deselected.
- `uv run pytest -q -m revision_mechanism -k 'activation and not recovery'` — 4 passed, 772 deselected.
- `uv run pytest -q -m revision_mechanism -k 'activation and recovery'` — 20 passed, 755 deselected.
- `uv run pytest -q -m revision_mechanism` — 79 passed, 697 deselected in 49.95s.
- Focused config, Schema, Agent/S4, revision/State and RunStore collection — 68 passed in 2.43s.
- Final `uv run pytest -q` — 776 passed in 705.14s.
- `uv run pytest -q tests/test_schema_examples.py` — 20 passed in 1.47s.
- `uv run ruff check .` — passed.
- `uv run mypy nepa` — passed with no issues in 4 source files.
- Public Spec, gold-coverage Spec, Target and Test Bundle lints — valid with zero errors and warnings.
- A temporary exact `_linked()` Plan/Spec/Manifest triplet passed public basic Plan lint with zero errors and warnings. Directly combining the checked-in non-MQTT Plan with its canonical companion retains the documented predecessor `PLAN_INPUT_REF_DRIFT` fixture-harness distinction and was not altered to hide it.
- `openspec validate m1-11-revision-gates-and-atomic-activation --strict` — valid.
- `openspec validate --all --strict` — 17 passed, 0 failed.
- `git diff --check` — passed.

The first full run exposed the expected frozen-config hash cascade after adding the default-zero limits. Existing generators were run in dependency order for S5, S6, S6 migration and S6 lease; only their provenance hashes changed. Fixture replay then passed, and the final full run above is the authoritative result.

## Final scope audit

- No file under `project_docs/`, any archived change, public CLI, prompt or calibration asset is changed.
- Added production behavior is confined to frozen revision configuration, closed evidence Schemas, revision calculations/ledger helpers, the existing S5/S6/orchestrator path and RunStore atomic publication/recovery.
- Added-source scans found no protocol-specific core branch. The only added MQTT text is the protocol-neutral fixture generator's description of its two fixture families.
- No `revision_evaluated`, effectiveness/circuit-breaker/degradation policy, PlanReviser producer/call, M1-13 parameter selection, M1-15 production enablement, S7/S8 or M2 behavior was added.
- No public Stage or CLI option was added. Initial S4 PlanCritic inputs remain unchanged; RG-4 only admits the delta closure through the same role contract.
- Activation WAL v1 fails the public v2 Schema. No historical-run converter or in-place migration was added; the pre-existing internal activation helper remains only as predecessor-test scaffolding.

## Responsible-owner gate

- Decision date: 2026-09-10 (Asia/Shanghai).
- Decision: approved. The responsible owner explicitly replied `批准` after receiving the final M1-11 implementation plan and acceptance package.
- Reviewed implementation: the final M1-11 diff from baseline `796f835`, covering RG-1 through RG-5, deterministic rejection, F2/F3 successor projection, pointer-committed atomic activation, recovery and downstream routing.
- Reviewed evidence: the requirement-to-evidence closure matrix, focused gate/activation/recovery results, complete `revision_mechanism` collection, full pytest, Schema/config/type/style/public-lint checks, strict OpenSpec validation, deterministic-fixture replay and final scope audit recorded above.
- Requested corrections: none.
- Approval scope: only the M1-11 mechanism portion of D1.13. This decision does not approve D1.14/M1-13 parameter selection, M1-15 production enablement, M1-12 evaluation/circuit-breaking policy, M1-14 PlanReviser, or any public CLI/S7/S8/M2 expansion. Production limits remain F2/F3 = 0/0.
- Post-decision condition: satisfied by the final inspection and rerun below.

## Post-owner final verification

- No corrections were requested. The complete final diff was inspected against baseline `796f835`; no unrelated refactoring or configurability was found to remove.
- `uv run pytest -q -m revision_mechanism -k 'gate or critic or rehearsal or rejected'` — 13 passed, 763 deselected in 6.67s.
- `uv run pytest -q -m revision_mechanism -k 'activation and not recovery'` — 4 passed, 772 deselected in 6.39s.
- `uv run pytest -q -m revision_mechanism -k 'activation and recovery'` — 20 passed, 756 deselected in 25.72s.
- `uv run pytest -q -m revision_mechanism` — 79 passed, 697 deselected in 49.53s.
- Final focused config, Schema, Agent/S4, revision State, activation and RunStore collection — 79 passed in 27.53s.
- Final `uv run pytest -q` — 776 passed in 695.40s.
- `uv run pytest -q tests/test_schema_examples.py` — 20 passed in 1.48s.
- `uv run ruff check .` — passed.
- `uv run mypy nepa` — passed with no issues in 4 source files.
- Public Spec, gold-coverage Spec, Target, Test Bundle and temporary exact `_linked()` Plan lints — valid with zero errors and warnings.
- Two fresh revision-fixture generations were byte-identical to each other and to the checked-in MQTT/non-MQTT outputs.
- `openspec validate m1-11-revision-gates-and-atomic-activation --strict` — valid; `openspec validate --all --strict` — 17 passed, 0 failed.
- `git diff --check 796f835` — passed. Diff/path audits confirm M1-10 and all archived changes are unchanged and `project_docs/`, public CLI and prompts are untouched.
- Added-scope scans confirm no protocol-specific core branch, `revision_evaluated`, new effectiveness/circuit-breaking behavior, M1-13 parameter selection, PlanReviser call, production enablement, or M2/S7/S8 capability was introduced. Pre-existing metrics/Plan/role references outside the M1-11 diff remain unchanged.

All M1-11 requirements and tasks are now satisfied. Remaining limitations are intentional scope boundaries: activation WAL v1 and historical-run migration are unsupported, production F2/F3 limits remain 0/0, and later revision evaluation, parameter selection, PlanReviser and production-run work remain separate milestones.

## Review remediation (2026-09-10, superseding)

The follow-up implementation review found that the earlier acceptance package did not cover consecutive activations, final post-Critic budget recomputation, strict scalar types, lock ownership, or the full v2 recovery closure. The old “all tasks satisfied” statement and owner approval above are therefore superseded for the corrected diff.

### Corrections applied

- Recovery now validates every WAL's internal version/hash/reference closure, reconciles only the unique unfinished v2 WAL, fully verifies a pointer-new view before finalization, and ignores reconciled history when later activations or normal S5/S6 updates advance mutable state.
- The v1 activation writer, v1 recovery fallback and remaining production `rev_*/activation.json` admission check were removed. `activate_revision_v2()` now requires the same `RunStore` controller lock before any write and revalidates the expected view between persistent writes.
- F2 binding manifest/map/receipt hashes, receipt back-references, epoch receipt, Plan ref, Run output ref, current copies and activation-ledger payload are cross-checked. Pointer-old and pointer-new recovery fail closed on drift.
- RG-3 is recomputed from synchronized wall-clock and actual model cost after RG-4/RG-5 and immediately before activation. A late failure rewrites the final gate evidence to RG-3 failure, retains consumed Critic/rehearsal evidence and cost, appends one rejection, and publishes no formal version or WAL.
- Revision limits are strict integers and rho/build rates are strict numeric fields; bool/string and floating activation limits are rejected while 0/0 defaults and 0.0/1.0 numeric boundaries remain valid.
- F3's projected ledger can represent pending quarantine/new-slot/retired-slot work until the new S5 epoch performs and receipts those physical changes, avoiding a false workspace-drift block while retaining rejection of unrelated paths.
- Predecessor tests now construct complete v2 WAL fixtures; no test calls the removed v1 entrypoint.

### Superseding machine acceptance

- Focused configuration, activation, recovery and gate set — 81 passed, 13 deselected.
- Complete S5/S6 files — 147 passed in 337.87s.
- `uv run pytest -q -m revision_mechanism -k 'gate or critic or rehearsal or rejected'` — 14 passed, 768 deselected.
- `uv run pytest -q -m revision_mechanism -k 'activation and not recovery'` — 12 passed, 770 deselected.
- `uv run pytest -q -m revision_mechanism -k 'activation and recovery'` — 20 passed, 762 deselected.
- Final `uv run pytest -q -m revision_mechanism` — 89 passed, 694 deselected in 65.61s.
- Final `uv run pytest -q` — 783 passed in 708.39s.
- `uv run pytest -q tests/test_schema_examples.py` — 20 passed in 1.48s.
- `uv run ruff check .` — passed.
- `uv run mypy nepa` — passed with no issues in 4 source files.
- Public Spec, gold-coverage Spec, Target and Test Bundle lints — valid with zero errors and warnings.
- `openspec validate m1-11-revision-gates-and-atomic-activation --strict` — valid.
- `openspec validate --all --strict` — 17 passed, 0 failed.
- `git diff --check 796f835` — passed.

### Superseding scope and owner status

- `project_docs/`, archived changes, public CLI and production defaults remain unchanged; F2/F3 defaults are still 0/0.
- No M1-12 evaluation/circuit-breaker, M1-13 parameter selection, M1-14 PlanReviser call, M1-15 production enablement, M2 or S7/S8 behavior was added.
- MQTT and non-MQTT fixtures pass the same core implementation; no protocol-specific production branch was introduced.
- Responsible-owner decision: approved on 2026-09-10 (Asia/Shanghai). After receiving the remediation summary and final-diff machine evidence, the responsible owner explicitly replied `批准，然后归档这个change，然后提交相关的代码`.
- Approval scope: the corrected M1-11 final diff and its superseding acceptance package only; the unchanged M1-12/M1-13/M1-14/M1-15, public CLI, M2 and S7/S8 exclusions remain outside approval.
- Post-owner verification is required before archive and is recorded below.

### Post-owner remediation verification

- Per the responsible owner's follow-up direction, post-approval verification was limited to the corrected paths rather than repeating the full suite.
- Configuration, gate, activation and recovery selection — 82 passed, 13 deselected in 56.51s.
- Consecutive activation, F3 materialization, quarantine/re-adoption and related S5 selection — 14 passed, 35 deselected in 18.93s.
- F2 migration-mode S6 selection — 23 passed, 75 deselected in 53.76s.
- Schema examples — 20 passed in 1.49s; Ruff, mypy and `git diff --check 796f835` passed.
- Current change strict validation passed; all-repository OpenSpec strict validation passed 17/17.
- The approval gate and requested post-approval focused verification are satisfied. The change is ready for spec synchronization and archive.

### Specification synchronization and archive readiness

- The four accepted delta specs were synchronized into the main `agent-invocation-runtime`, `plan-revision-infrastructure`, `revision-gates-and-activation`, and `s6-f0-execution` specifications without changing the approved behavior.
- After synchronization, strict validation passed for the current change and for all repository OpenSpec items (18/18).
- All 53 implementation tasks are complete, the archive target is unused, and the approved change is ready to move into the dated archive.
