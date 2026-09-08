# M1-9 D1.15 acceptance record

> **Review correction status (2026-09-09):** The original acceptance result below is retained as historical evidence and is superseded by the replacement technical acceptance appended at the end of this record. Review-correction tasks 9.1-9.7 are complete, including explicit owner review of the corrected final diff.

## Requirement-to-contract closure

| Requirement | Schema-valid fixture and controller path | Persisted result and corruption proof |
| --- | --- | --- |
| Current revision admission and ordering | MQTT/non-MQTT F2/F3 packages bind the active Plan, latest activation, current version binding, epoch receipt, manifest/map, State and ledgers; `S6ExecutionController` reconciles before admission and selects migration/group work first | Stale Plan, binding, epoch, activation, group and missing-State variants fail before allocation; fresh ready E0 and ordinary/F1 histories remain valid |
| REVALIDATE | Closed Task Evidence and validation-record forms carry current Plan/version/epoch and exact migration ref; the singleton and group controller paths run build/smoke without Agent allocation | Attempt-zero evidence, same-tree legal commit, State/file/revision projections and zero LLM/global-call assertions; cross-kind and result corruption fail closed |
| AMEND | Closed attempt/evidence forms carry `execution_mode=amend`, one independent amendment use and preserved ordinary attempts; allocation precedes the sole Fixer/T1 call | Full-four-attempt fixtures publish one current proof without attempt five; replay, second-use, counter, role/tier and evidence corruption are rejected |
| REGENERATE | Accepted migration projects a normal zero-attempt row with migration lineage and uses the existing Coder/Fixer schedule | First call is Coder/T2, normal bounded retries and current evidence/event/file ownership are checked while historical lineage remains immutable |
| Frozen repair group assembly | Activation `pending_groups`, current Plan/State, Blueprint and contract map form the only descriptor; dual-protocol fixtures persist topologically accumulated member candidates | Unknown/stale/duplicate members, paths, symbols, artifacts and dependencies fail before allocation; no candidate boundary publishes done, ledger, event or commit |
| Whole-group verification and exhaustion | The production build/smoke path validates the cumulative tree and maps structured diagnostics through the frozen closure | Strict attribution retries only implicated members; unlocatable failures cover the group; success publishes one joint result, while exhaustion restores the epoch baseline and blocks all unresolved members plus proven dependents |
| Atomic evidence/commit/State publication | Discriminated Task Evidence, Joint Evidence, group WAL and `verification_committed` contracts bind sorted exact membership, sequences, migration refs, result refs, tree and commit | One evidence per member, one group Joint Evidence and one joint commit precede complete State/file/revision projection; omitted members, wrong group/activation/mode, stale candidate/tree, invalid trailers and partial projections fail closed |
| Recovery and completed replay | One normal/lease/group verification WAL owns reconstruction and forward publication; admission, resume and completed-stage checks invoke reconciliation first | Faults at allocation/candidate/result plus WAL prepared, joint evidence, candidate install, commit, State, file ledger, event, terminals and WAL removal converge without duplicate Agent/build/smoke/commit work |
| Protocol neutrality and preservation | MQTT and non-MQTT migration fixtures are derived by one byte-stable developer generator and executed by the same controller/templates | Generated temporary roots match checked-in assets; ordinary S6, F1 lease and S5 multi-epoch regressions retain their semantics |

## Focused acceptance

- Combined Schema, revision/State, RunStore/orchestrator, S5, ordinary S6, F1 lease, migration/group and D1.15 fault collection: `216 passed`.
- M1-9 group success/recovery/corruption smoke after final relational WAL check: `18 passed, 72 deselected`.
- `project_docs/`, prompts and calibration assets are unchanged.
- Source audit found no S6 admission dependency on `1.0.0/E0`; remaining E0 literals belong to the unique initial materialization/checkpoint path, examples, or explicit fresh-E0 validation. No fake lease representation, second execution framework, compatibility upgrader, M1-10 trigger producer, M1-11 automatic activation producer or M1-12 circuit-breaker event was added.

## D1.15 fault windows

Pre-transaction interruption coverage includes each member allocation, each persisted candidate and complete group results. Transaction coverage injects faults after WAL preparation, member/joint evidence publication, candidate installation, legal joint commit, complete State publication, file-ledger publication, revision-event publication, terminal records and WAL removal. Corruption cases cover group id, activation ref, member mode/migration, member omission/order, candidate bytes/tree, build/smoke result refs, commit parent/tree/trailers, evidence bytes and partial mutable projections.

## Full automated acceptance

- `uv run pytest -q -m s6_execution` — `76 passed, 609 deselected`.
- `uv run pytest -q -m s5_epoch` — `48 passed, 637 deselected`.
- `uv run pytest -q` — `685 passed`.
- `uv run pytest -q tests/test_schema_examples.py` — `19 passed`.
- `uv run ruff check .` — passed.
- `uv run mypy nepa` — passed with no issues in 4 source files.
- `uv run python -m nepa lint spec gold_file/specIR.json` — valid, zero errors and warnings.
- Gold coverage Spec lint, Target lint and Test Bundle lint — all valid, zero errors and warnings.
- Public basic Plan lint using the matched non-MQTT `_linked()` Plan/Spec/Test Manifest fixture — valid, zero errors and warnings.
- `openspec validate m1-9-s6-migration-joint-verification --strict` — valid.
- `openspec validate --all --strict` — 17 passed, 0 failed.
- `git diff --check` — passed.

## Scope and later-milestone boundary

M1-8 commits `3a249de` and `292c632` and its archived artifacts remain unchanged. This acceptance does not implement or approve M1-10 trigger/operator behavior, M1-11 automatic activation, M1-12 circuit breakers, M1-13 through M1-15 calibration/production acceptance, M2/S7/S8, public CLI changes, architecture prompts or calibration lineage. It is D1.15 evidence only and is neither D1.14 parameter approval nor final M1-15 owner acceptance.

## Owner review

- **Decision date:** 2026-09-08 (Asia/Shanghai).
- **Decision:** approved by the responsible owner through the direct response `批准` after receiving the M1-9 review request.
- **Reviewed package:** this D1.15 acceptance record, its requirement-to-contract closure and fault-window matrix, the complete M1-9 implementation diff, the 216-test focused result, the 76-test `s6_execution` result, the 48-test `s5_epoch` result, the 685-test full-suite result, public lint results and strict OpenSpec validation.
- **Scope:** approval is limited to M1-9 migration/joint-verification implementation and D1.15 evidence. It is not D1.14 parameter approval and not final M1-15 acceptance.

## Post-review final verification

No review correction was requested. After recording the approval, the complete acceptance was rerun against the final implementation and acceptance package:

- `uv run pytest -q -m s6_execution` — `76 passed, 609 deselected`.
- `uv run pytest -q -m s5_epoch` — `48 passed, 637 deselected`.
- `uv run pytest -q` — `685 passed`.
- Schema examples — `19 passed`.
- Ruff and mypy — passed.
- Four public gold lint commands and the matched non-MQTT public Plan lint — valid with zero errors and warnings.
- Current-change and all-change strict OpenSpec validation — current valid; 17 passed, 0 failed.
- `git diff --check` — passed.
- Final scope inspection found no modified `project_docs/`, M1-8 archive, prompt/calibration asset, public CLI or M2/S7/S8 asset. The only added trigger-related reads compute the accepted M1-8 activation event sequence; this change adds no trigger/operator, automatic-activation or circuit-breaker producer.

## Review-correction replacement acceptance (2026-09-09)

This result supersedes the original technical acceptance above. The prior owner decision is retained as history but does not constitute review of the corrected diff.

### Corrected closure

- Current F2 admission now binds the active Plan to its recomputed current Blueprint, manifest, map and metadata while requiring Blueprint equality with the epoch receipt only when that receipt materialized the active Plan. Reused receipts require the latest same-epoch F2 activation, old-receipt binding and checkpoint ancestry; real task renumbering and owner/provider rebinding are covered.
- Frozen repair groups permit one or more members while lease contracts continue to require two or more members across Task/Joint Evidence, verification WAL, revision events and pure validators.
- The epoch checkpoint commit/tree is an immutable ancestry anchor. Each group records the clean accepted current HEAD/tree as its transaction baseline and commit parent, preserves prior accepted group commits, restores only its own baseline before commit, and verifies resolved group commits on the current HEAD ancestry chain.
- Per-member writes are frozen to `deliverable_files ∩ affected_paths`. Normal, AMEND and REGENERATE accept a non-empty legal subset but reject byte-empty results; REVALIDATE remains zero-call and empty-change capable. Candidate manifests persist the actual subset and recovery reloads it without repeating Agent I/O.
- Retry attribution uses frozen path ownership, contract symbol/provider ownership and Blueprint build-artifact closure. Smoke, timeout and diagnostics without a strict subset fall back to the whole group without relaxing mode or run-wide budgets.

### Replacement verification

- Combined corrected Schema, Plan State/revision, F2 admission, group, candidate, attribution and recovery collection — `139 passed`.
- Final post-Schema focused regression selector — `21 passed, 102 deselected`.
- `uv run pytest -q -m s6_execution` — `83 passed, 610 deselected`.
- `uv run pytest -q -m s5_epoch` — `48 passed, 645 deselected`.
- Final `uv run pytest -q` after all implementation and Schema edits — `693 passed`.
- `uv run ruff check .` — passed.
- `uv run mypy nepa` — passed with no issues in 4 source files.
- Public Spec, gold-coverage Spec, Target, Test Bundle and matched non-MQTT public basic Plan lint — valid with zero errors and warnings.
- `openspec validate m1-9-s6-migration-joint-verification --strict` — valid.
- `openspec validate --all --strict` — `17 passed, 0 failed`.
- `git diff --check` — passed.

### Scope and owner gate

Final inspection found no diff under `project_docs/`, the M1-8 archive, architecture prompts or calibration assets. No public CLI, M1-10 trigger/operator, M1-11 automatic activation, M1-12 circuit breaker, or M2/S7/S8 behavior was added. F0/F1 and the accepted M1-8 inputs remain on their existing paths and pass the full regression suite.

The corrected implementation is technically accepted without a newly discovered blocker.

### Corrected-diff owner review

- **Decision date:** 2026-09-09 (Asia/Shanghai).
- **Decision:** approved by the responsible owner through the direct response `批准` after receiving the corrected implementation scope, final verification results and explicit explanation of the approval boundary.
- **Reviewed package:** the review-correction replacement acceptance above, the corrected final diff, `693 passed` full-suite result, focused S5/S6 results, public lint results and strict OpenSpec validation.
- **Scope:** approval is limited to the corrected M1-9 implementation and D1.15 evidence. It does not approve later milestones and does not by itself authorize behavior outside M1-9.
