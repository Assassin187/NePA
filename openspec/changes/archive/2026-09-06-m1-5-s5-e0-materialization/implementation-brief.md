## Input artifacts and Schema references

- Fresh `run.json` using Run v4; `run.stages.s4=done` with immutable Plan 1.0.0 ref, active-pointer ref, Delivery Blueprint hash, configuration-snapshot hash, and frozen `spec/spec.json`, `inputs/target.json`, `inputs/test_bundle.json` refs. Schemas: `run.schema.json`, `plan.schema.json`, `active-plan.schema.json`, Spec/Target/Test Bundle Schemas, S4 commitment/checkpoint Schemas.
- `plan/file_ledger.json` and `plan/revision_ledger.json` in their empty fresh-run v2 forms. Schemas: `file-ledger.schema.json`, `revision-ledger.schema.json`.
- Recomputed `DeliveryConstraints` and `DeliveryBlueprint` from the existing `compile_delivery_constraints` / `compile_delivery_blueprint` path plus basic/full Plan lint. Schema: `delivery-blueprint.schema.json`; constraints remain the existing deterministic S4 projection.
- Version-controlled MQTT and non-MQTT frozen fixtures produced through the existing S4 completion/publication path, including each Plan, Spec, Target, Test Bundle, Blueprint, and S4 anchors.

## Output artifacts and acceptance commands

- `workspace/` containing exactly the expanded active Blueprint source paths, one git checkpoint with Plan/Epoch trailers, no committed build outputs, warning-free release/SAN builds, and passing per-variant executable smoke evidence.
- `plan/bindings/1.0.0/{artifact_manifest.json,contract_map.json,receipt.json}`, current `plan/{artifact_manifest.json,contract_map.json}`, `plan/epochs/E0/receipt.json`, v2 `plan/file_ledger.json`, and one v2 `epoch_materialized` event; Schemas: new manifest/map/epoch/binding/build/smoke contracts plus updated Run/file/revision ledgers.
- `run.stages.s5=done` with typed `epoch_receipt` and `binding_receipt` refs; stable completion has no S5 pending record. Failures retain structured stage/report evidence but no ready receipt.
- Acceptance: `uv run pytest -m s5_epoch`; focused schema/S4/revision/RunStore/config/protocol-neutrality tests; actual sandbox integration; `uv run ruff check .`; `uv run mypy nepa`; `uv run pytest -q`; gold lint; `openspec validate m1-5-s5-e0-materialization --strict`; `openspec validate --all --strict`; `git diff --check`.

## Required function and class signatures

- `nepa.speclib.delivery.expand_file_rules(blueprint, constraints) -> list[dict[str, Any]]`
- `nepa.speclib.materialization.derive_rendering_view(plan, spec, target, blueprint, constraints) -> dict[str, Any]`
- `nepa.speclib.materialization.render_e0_files(rendering_view, spec, target, blueprint, constraints) -> dict[str, bytes]`
- `nepa.speclib.materialization.build_artifact_manifest(plan_ref, blueprint, rendering_view, epoch) -> dict[str, Any]`
- `nepa.speclib.materialization.build_contract_map(plan_ref, blueprint, rendering_view, epoch) -> dict[str, Any]`
- `nepa.speclib.materialization.project_e0_file_ledger(initial_ledger, rendered_files, checkpoint, build_evidence, epoch_receipt_ref) -> dict[str, Any]`
- `nepa.speclib.materialization.validate_completed_e0(run, plan, blueprint, file_ledger, revision_ledger, epoch_receipt, binding_receipt, manifest, contract_map, workspace_facts) -> None`
- `nepa.tools.sandbox.ExecResult` and `nepa.tools.sandbox.SandboxExecutor.exec(cmd: list[str], cwd: str, timeout_s: int, net: Literal["none", "loopback", "internal"] = "none") -> ExecResult`
- `nepa.tools.build.run_build_variants(executor, workspace, blueprint, constraints) -> list[dict[str, Any]]`
- `nepa.tools.build.run_smoke_checks(executor, workspace, blueprint, variants, dwell_seconds, term_grace_seconds) -> list[dict[str, Any]]`
- `nepa.stages.s5_materialization.S5MaterializationController.run(context: StageContext) -> StageResult`, `.reconcile(store: RunStore) -> None`, `.after_commit(store: RunStore, result: StageResult) -> None`, and `.verify_completed(store: RunStore) -> None`
- `nepa.run_store.RunStore.publish_s5_e0(bundle, fault_hook=None) -> dict[str, ArtifactRef]` and `nepa.run_store.RunStore.recover_s5_e0(fault_hook=None) -> dict[str, ArtifactRef] | None`

## Referenced design sections

- `project_docs/system_design.md` 8.0.2: §4.7-§4.8, §5.2.1, §5.6.5.1-§5.6.7, §6.4.7, §6.5, §7.4, §8.3, §8.5, §10.2.1-§10.2.2, §10.8, D1.1/D1.4/D1.7/D1.10-D1.12/D1.15.
- `project_docs/pipeline_design_s4_s9.md` 2.0.2: §3.3, §4.2-§4.4, §5.2.1-§5.2.5, §5.4-§5.5.
