# M1-4d final acceptance

Completed on 2026-09-05 for `m1-4d-plan-version-and-migration-infrastructure`.

## Approval and production handoff

The project owner explicitly authorized signature and handoff in the conversation: “现在完成该change的后续任务，授权签字交接”. The assistant recorded this authorization without inventing a separate human identity.

- Selected lineage: `4420296f4dead63a3119c49106e234147f4a69a1f4f4aecda8fa562af7944ab7`, V0, N=3, p2 passes=3.
- [Owner approval](owner-approval.json), SHA-256 `95d48c647a154879b30429e4b142dd3fa98c466d2a4a42a72cc291d211178f64`.
- [Handoff](handoff.json), SHA-256 `a6822ba4e46c1db7eb9e347d1e830dfb8d740d6c2c07e426276ab0aed81f6ee5`.
- Canonical records live under `runs/_calibration/s4-architecture/<lineage>/prompt-development/`; the two JSON files here are byte-identical review copies.
- S4 `LINEAGE_ID` and `DEFAULT_HANDOFF_ROOT` now bind this approved lineage. The existing production verifier admitted it with the exact packaged initial/repair bytes.
- Independent `recompute('v0', 3, require_complete=True, require_source_match=True)` passed after final implementation cleanup. No new Provider calls were made during final acceptance.

## Validation

- `uv run pytest -q`: **465 passed**, 170.04 seconds.
- Focused revision/activation/S4 contract regression: **33 passed**.
- Protocol neutrality, recovery artifacts and schema examples: **23 passed**.
- `uv run python -m nepa lint spec gold_file/specIR.json`: valid, no errors or warnings.
- Basic Plan lint with a linked non-MQTT Plan and its exact Spec/Manifest companions: valid, no errors or warnings.
- `openspec validate m1-4d-plan-version-and-migration-infrastructure --strict`: passed.
- `openspec validate --all --strict`: **12 passed, 0 failed**.
- `git diff --check`: passed.

The original task command passed only the schema example to Plan lint and failed with `PLAN_COMPANION_MISSING`. That example contains placeholder input hashes; it is validated as a schema example, not misrepresented as a linked semantic artifact. The task now requires a linked Plan with matching companions. The actual basic-lint fixture was prepared using:

```sh
PYTHONPATH=tests uv run python - <<'PYCODE'
from pathlib import Path
from test_plan_lint import _linked
from nepa.speclib.lint import canonical_json_bytes
plan, blueprint, spec, manifest, constraints, target = _linked()
root = Path('/tmp/nepa-m1-4d-acceptance')
root.mkdir(exist_ok=True)
for name, value in [('plan', plan), ('spec', spec), ('manifest', manifest)]:
    (root / f'{name}.json').write_bytes(canonical_json_bytes(value))
PYCODE
uv run python -m nepa lint plan /tmp/nepa-m1-4d-acceptance/plan.json --spec /tmp/nepa-m1-4d-acceptance/spec.json --manifest /tmp/nepa-m1-4d-acceptance/manifest.json
```

## Scope review and closure

The review covered tracked implementation diffs and the new revision module, schemas and tests. Final cleanup removed F1 construction/admission from the revision capability, removed filesystem artifact loading and unused API aliases from the pure revision module, and restricted explicit lineage to the current task_mappings contract. Conflicting source forms and merge/split source reuse are rejected with regression coverage. The S4 substituted-prompt test now uses an isolated current-contract fixture rather than a historical production lineage.

F1 trigger/lease logic, F2/F3 candidate generation and patch/RG orchestration, physical S5 quarantine operations, the S6 execution loop, S9 metrics and additional business CLI remain outside this change. F2/F3 successor validation and supplied gate-evidence checks are infrastructure, not those deferred controllers. File-ledger quarantine projection does not move or delete files.

`project_docs/system_design.md` and `project_docs/pipeline_design_s4_s9.md` were already modified design inputs before this change, as recorded by its proposal; they were not changed or reverted during completion and are excluded from the implementation scope.

The change tasks are complete. The selected calibration establishes minimum usability only, not production quality. Runtime evidence remains in the gitignored runs directory under the existing workflow. The OpenSpec change is archived at `openspec/changes/archive/2026-09-05-m1-4d-plan-version-and-migration-infrastructure/`; the enclosing Git commit records the implementation and archive.
