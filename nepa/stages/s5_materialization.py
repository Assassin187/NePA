"""S5 E0 materialization controller and its admission boundary."""

from __future__ import annotations

import copy
import hashlib
from typing import Any
from collections.abc import Mapping

from ..orchestrator import ControlledStageFailure, StageContext, StageResult
from ..run_store import ArtifactRef, RunStore, RunStoreError, sha256_bytes
from ..speclib.delivery import DeliveryConstraintError, compile_delivery_blueprint, compile_delivery_constraints
from ..speclib.lint import canonical_json_bytes
from ..speclib.materialization import (
    MaterializationError, build_artifact_manifest, build_contract_map, derive_rendering_view,
    render_e0_files, validate_completed_e0,
)
from ..speclib.plan import blueprint_task_semantic_projection, plan_lint
from ..speclib.plan_revision import append_epoch_materialized, validate_file_ledger, validate_revision_ledger
from ..tools.build import _tree_sha256, run_build_variants, run_smoke_checks
from ..tools.sandbox import SandboxExecutor
from ..tools.git_ops import verify_checkpoint


class S5AdmissionError(MaterializationError):
    """A sealed input is not admissible for E0 materialization."""


def _sha_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class S5MaterializationController:
    def __init__(self, executor: Any | None = None, *, fault_hook: Any | None = None) -> None:
        self.executor = executor
        self.fault_hook = fault_hook

    @staticmethod
    def _read_inputs(store: RunStore, run: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        try:
            spec = store._read_json_artifact("spec/spec.json")
            target = store._read_json_artifact("inputs/target.json")
            bundle = store._read_json_artifact("inputs/test_bundle.json")
        except RunStoreError as exc:
            raise S5AdmissionError(str(exc), code="S5_INPUT_INVALID") from exc
        for label, relative, ref in (("spec", "spec/spec.json", run["inputs"]["spec"]), ("target", "inputs/target.json", run["inputs"]["target_profile"]), ("test bundle", "inputs/test_bundle.json", run["inputs"]["test_bundle"])):
            actual = sha256_bytes(store._confined(relative).read_bytes())
            if actual != ref["sha256"]:
                raise S5AdmissionError(f"frozen {label} ref drifted", code="S5_INPUT_DRIFT")
        return spec, target, bundle

    def _admit(self, store: RunStore) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        run = store.load_run()
        s4 = run["stages"]["s4"]
        if s4.get("status") != "done" or set(s4.get("output_refs", {})) != {"plan", "active_plan", "delivery_blueprint_sha256", "config_snapshot_sha256"}:
            raise S5AdmissionError("S5 requires a completed fresh S4 seal", code="S5_ADMISSION_INVALID")
        try:
            store.verify_stage_refs(s4, "s4")
            plan_ref = ArtifactRef.from_value(s4["output_refs"]["plan"]).as_dict()
            active_ref = ArtifactRef.from_value(s4["output_refs"]["active_plan"]).as_dict()
            plan = store._read_json_artifact(plan_ref["path"], schema_name="plan.schema.json")
            active = store._read_json_artifact(active_ref["path"], schema_name="active-plan.schema.json")
            file_ledger = store._read_json_artifact("plan/file_ledger.json", schema_name="file-ledger.schema.json")
            revision_ledger = store._read_json_artifact("plan/revision_ledger.json", schema_name="revision-ledger.schema.json")
        except RunStoreError as exc:
            raise S5AdmissionError(str(exc), code="S5_SEAL_INVALID") from exc
        if plan_ref["path"] != "plan/versions/plan-1.0.0.json" or active.get("revision_seq") != 0 or active.get("epoch") != "E0" or active.get("sha256") != plan_ref["sha256"]:
            raise S5AdmissionError("S5 E0 admission requires the immutable 1.0.0/E0/0 pointer", code="S5_ADMISSION_INVALID")
        spec, target, bundle = self._read_inputs(store, run)
        try:
            constraints = compile_delivery_constraints(spec, target)
            blueprint = compile_delivery_blueprint(constraints, plan["architecture"], plan["work_packages"], blueprint_task_semantic_projection(plan["tasks"]))
            validate_file_ledger(file_ledger)
            validate_revision_ledger(revision_ledger)
        except (DeliveryConstraintError, ValueError) as exc:
            raise S5AdmissionError(str(exc), code="S5_BLUEPRINT_INVALID") from exc
        if s4["output_refs"]["delivery_blueprint_sha256"] != _sha_json(blueprint) or plan.get("delivery_blueprint_sha256") != _sha_json(blueprint):
            raise S5AdmissionError("sealed Delivery Blueprint does not recompute from frozen inputs", code="S5_BLUEPRINT_DRIFT")
        if s4["output_refs"]["config_snapshot_sha256"] != run["config_snapshot_sha256"] or _sha_json(run["config_snapshot"]) != run["config_snapshot_sha256"]:
            raise S5AdmissionError("sealed configuration snapshot anchor drifted", code="S5_CONFIG_DRIFT")
        if revision_ledger.get("entries"):
            materialized = [entry for entry in revision_ledger["entries"] if entry.get("event_type") == "epoch_materialized" and entry.get("payload", {}).get("revision_seq") == 0]
            if materialized:
                raise S5AdmissionError("E0 is already recorded; use completed reconciliation", code="S5_ALREADY_ACCEPTED")
        lint_plan = copy.deepcopy(plan)
        lint_plan["input_refs"] = {
            "spec": {"path": plan["input_refs"]["spec"]["path"], "sha256": _sha_json(spec)},
            "target_profile": {"path": plan["input_refs"]["target_profile"]["path"], "sha256": _sha_json(constraints["target_profile"])},
            "test_bundle": {"path": plan["input_refs"]["test_bundle"]["path"], "sha256": _sha_json(bundle)},
        }
        basic = plan_lint(lint_plan, spec, bundle)
        full = plan_lint(lint_plan, spec, bundle, level="full", constraints=constraints, blueprint=blueprint, target_profile=target)
        if not basic.get("valid") or not full.get("valid"):
            raise S5AdmissionError("sealed Plan failed S5 basic/full lint", code="S5_LINT_INVALID")
        return run, plan, plan_ref, blueprint, constraints, {"spec": spec, "target": target, "bundle": bundle, "file_ledger": file_ledger, "revision_ledger": revision_ledger}

    def _executor_for(self, run: Mapping[str, Any]) -> Any:
        if self.executor is not None:
            return self.executor
        sandbox = run["config_snapshot"]["sandbox"]
        return SandboxExecutor(sandbox["image"], sandbox["cpu"], sandbox["mem_gb"])

    def _run(self, context: StageContext) -> StageResult:
        store = context.store
        existing_run = store.load_run()
        if existing_run["stages"]["s5"].get("status") == "done":
            self.reconcile(store)
            return StageResult(output_refs={key: ArtifactRef.from_value(value) for key, value in existing_run["stages"]["s5"]["output_refs"].items()})
        recovered = store.recover_s5_e0(self.fault_hook)
        if recovered is not None:
            return StageResult(output_refs=recovered)
        run, plan, plan_ref, blueprint, constraints, inputs = self._admit(store)
        view = derive_rendering_view(plan, inputs["spec"], inputs["target"], blueprint, constraints)
        rendered = render_e0_files(view, inputs["spec"], inputs["target"], blueprint, constraints)
        pending = {
            "schema_version": "1.0", "epoch": "E0", "phase": "pending", "plan_ref": plan_ref,
            "input_refs": {
                key: {"path": path, "sha256": run["inputs"][key]["sha256"]}
                for key, path in (("spec", "spec/spec.json"), ("target_profile", "inputs/target.json"), ("test_bundle", "inputs/test_bundle.json"))
            },
            "blueprint": blueprint, "constraints": constraints,
            "blueprint_sha256": _sha_json(blueprint), "rendering_view": view, "rendering_view_sha256": _sha_json(view),
            "expected_files": [{"path": path, "sha256": sha256_bytes(data), "content": data.decode("utf-8")} for path, data in rendered.items()],
            "build_results": [], "smoke_results": [],
            "checkpoint_commit": None, "checkpoint_tree": None, "output_refs": {},
        }
        pending_path = store._confined("plan/epochs/E0/pending.json")
        if pending_path.exists():
            current_pending = store._read_json_artifact("plan/epochs/E0/pending.json", schema_name="s5-pending-state.schema.json")
            if current_pending.get("checkpoint_commit") is None:
                store.replace_json("plan/epochs/E0/pending.json", pending, schema_name="s5-pending-state.schema.json")
            elif current_pending.get("plan_ref") != pending["plan_ref"] or current_pending.get("blueprint_sha256") != pending["blueprint_sha256"] or current_pending.get("rendering_view_sha256") != pending["rendering_view_sha256"] or current_pending.get("expected_files") != pending["expected_files"]:
                raise RunStoreError("checkpointed E0 pending state conflicts with the current rendering")
        else:
            store.replace_json("plan/epochs/E0/pending.json", pending, schema_name="s5-pending-state.schema.json")
        workspace = store._confined("workspace")
        workspace.mkdir(parents=True, exist_ok=True)
        for path, data in rendered.items():
            target = (workspace / path).resolve()
            try:
                target.relative_to(workspace)
            except ValueError as exc:
                raise S5AdmissionError(f"rendered path escapes workspace: {path}", code="S5_RENDER_PATH_CLOSURE") from exc
            store._write_atomic_at(target, data)
            if self.fault_hook is not None:
                self.fault_hook(f"workspace_written:{path}")
        pending["phase"] = "rendered"
        store.replace_json("plan/epochs/E0/pending.json", pending, schema_name="s5-pending-state.schema.json")
        executor = self._executor_for(run)
        build_results = run_build_variants(executor, workspace, blueprint, constraints)
        pending.update({"phase": "built", "build_results": build_results})
        store.replace_json("plan/epochs/E0/pending.json", pending, schema_name="s5-pending-state.schema.json")
        if self.fault_hook is not None:
            self.fault_hook("build_results_recorded")
        smoke = run_smoke_checks(executor, workspace, blueprint, build_results, run["config_snapshot"]["smoke"]["dwell_seconds"], run["config_snapshot"]["smoke"]["term_grace_seconds"])
        pending.update({"phase": "smoked", "smoke_results": smoke})
        store.replace_json("plan/epochs/E0/pending.json", pending, schema_name="s5-pending-state.schema.json")
        if self.fault_hook is not None:
            self.fault_hook("smoke_results_recorded")
        refs = store.publish_s5_e0({
            "workspace": "workspace", "plan_ref": plan_ref, "blueprint": blueprint, "constraints": constraints,
            "rendering_view": view, "rendered_files": rendered, "build_results": build_results, "smoke_results": smoke,
        }, fault_hook=self.fault_hook)
        return StageResult(output_refs=refs)

    def run(self, context: StageContext) -> StageResult:
        try:
            return self._run(context)
        except MaterializationError as exc:
            if exc.code.startswith("S5_TEMPLATE_"):
                raise
            raise ControlledStageFailure({"code": exc.code, "detail": str(exc)}) from exc

    def after_commit(self, store: RunStore, result: StageResult) -> None:
        refs = {key: ArtifactRef.from_value(value).as_dict() for key, value in result.output_refs.items()}
        ledger = store._read_json_artifact("plan/revision_ledger.json", schema_name="revision-ledger.schema.json")
        updated = append_epoch_materialized(ledger, epoch_receipt_ref=refs["epoch_receipt"], binding_ref=refs["binding_receipt"], revision_seq=0)
        store.replace_json("plan/revision_ledger.json", updated, schema_name="revision-ledger.schema.json")
        if self.fault_hook is not None:
            self.fault_hook("epoch_event_appended")
        self.verify_completed(store, allow_pending=True)
        pending = store._confined("plan/epochs/E0/pending.json")
        if pending.exists():
            pending.unlink()

    def reconcile(self, store: RunStore) -> None:
        run = store.load_run()
        stage = run["stages"]["s5"]
        if stage.get("status") == "done":
            revision = store._read_json_artifact("plan/revision_ledger.json", schema_name="revision-ledger.schema.json")
            events = [
                entry
                for entry in revision.get("entries", [])
                if entry.get("event_type") == "epoch_materialized"
                and entry.get("payload", {}).get("revision_seq") == 0
            ]
            refs = stage.get("output_refs", {})
            if len(events) == 0:
                updated = append_epoch_materialized(
                    revision,
                    epoch_receipt_ref=refs["epoch_receipt"],
                    binding_ref=refs["binding_receipt"],
                    revision_seq=0,
                )
                store.replace_json("plan/revision_ledger.json", updated, schema_name="revision-ledger.schema.json")
            elif len(events) != 1 or events[0].get("payload", {}).get("epoch_receipt_ref") != refs.get("epoch_receipt") or events[0].get("payload", {}).get("binding_ref") != refs.get("binding_receipt"):
                raise RunStoreError("accepted S5 output conflicts with its epoch event")
            self.verify_completed(store, allow_pending=True)
            pending = store._confined("plan/epochs/E0/pending.json")
            if pending.exists():
                pending.unlink()

    def verify_completed(self, store: RunStore, *, allow_pending: bool = False) -> None:
        run = store.load_run()
        stage = run["stages"]["s5"]
        if stage.get("status") != "done":
            raise RunStoreError("cannot verify an S5 stage that is not done")
        if not allow_pending and store._confined("plan/epochs/E0/pending.json").exists():
            raise RunStoreError("completed S5 still has an unreconciled pending record")
        store.verify_stage_refs(stage, "s5")
        plan = store._read_json_artifact("plan/versions/plan-1.0.0.json", schema_name="plan.schema.json")
        ledger = store._read_json_artifact("plan/file_ledger.json", schema_name="file-ledger.schema.json")
        revision = store._read_json_artifact("plan/revision_ledger.json", schema_name="revision-ledger.schema.json")
        epoch = store._read_json_artifact("plan/epochs/E0/receipt.json", schema_name="epoch-receipt.schema.json")
        binding = store._read_json_artifact("plan/bindings/1.0.0/receipt.json", schema_name="binding-receipt.schema.json")
        manifest = store._read_json_artifact("plan/artifact_manifest.json", schema_name="artifact-manifest.schema.json")
        contract_map = store._read_json_artifact("plan/contract_map.json", schema_name="contract-map.schema.json")
        immutable_manifest = store._read_json_artifact("plan/bindings/1.0.0/artifact_manifest.json", schema_name="artifact-manifest.schema.json")
        immutable_map = store._read_json_artifact("plan/bindings/1.0.0/contract_map.json", schema_name="contract-map.schema.json")
        if manifest != immutable_manifest or contract_map != immutable_map:
            raise RunStoreError("current E0 manifest/map copies drifted from immutable bindings")
        spec, target, _bundle = self._read_inputs(store, run)
        constraints = compile_delivery_constraints(spec, target)
        blueprint = compile_delivery_blueprint(constraints, plan["architecture"], plan["work_packages"], blueprint_task_semantic_projection(plan["tasks"]))
        for ref in epoch.get("build_result_refs", []):
            store.verify_ref(ref, schema_name="build-result.schema.json")
            evidence = store._read_json_artifact(ref["path"], schema_name="build-result.schema.json")
            if evidence.get("status") != "passed":
                raise RunStoreError("accepted E0 build evidence is not bound to the checkpoint")
        for ref in epoch.get("smoke_result_refs", []):
            store.verify_ref(ref, schema_name="smoke-result.schema.json")
            evidence = store._read_json_artifact(ref["path"], schema_name="smoke-result.schema.json")
            if evidence.get("status") != "passed":
                raise RunStoreError("accepted E0 smoke evidence is not bound to the checkpoint")
        view = derive_rendering_view(plan, spec, target, blueprint, constraints)
        rendered = render_e0_files(view, spec, target, blueprint, constraints)
        projected = {**view, "rendered_files": rendered}
        plan_ref = {"path": "plan/versions/plan-1.0.0.json", "sha256": _sha_json(plan)}
        if manifest != build_artifact_manifest(plan_ref, blueprint, projected, "E0") or contract_map != build_contract_map(plan_ref, blueprint, projected, "E0"):
            raise RunStoreError("accepted E0 manifest/map do not recompute from the sealed Plan and Blueprint")
        workspace = store._confined("workspace")
        verify_checkpoint(workspace, {"commit_sha": epoch["checkpoint_commit"], "tree_sha": epoch["checkpoint_tree"]})
        source_tree_sha = _tree_sha256(workspace)
        for ref in epoch.get("build_result_refs", []) + epoch.get("smoke_result_refs", []):
            evidence = store._read_json_artifact(ref["path"])
            if evidence.get("tree_sha256") != source_tree_sha:
                raise RunStoreError("accepted E0 evidence tree drifted")
        source_files = [item for item in workspace.rglob("*") if item.is_file() and ".git" not in item.parts] if workspace.is_dir() else []
        paths = [item.relative_to(workspace).as_posix() for item in source_files]
        hashes = {item.relative_to(workspace).as_posix(): sha256_bytes(item.read_bytes()) for item in source_files}
        validate_completed_e0(run, plan, blueprint, ledger, revision, epoch, binding, manifest, contract_map, {"paths": paths, "file_hashes": hashes, "constraints": constraints})
        event = [item for item in revision.get("entries", []) if item.get("event_type") == "epoch_materialized" and item.get("payload", {}).get("revision_seq") == 0]
        if len(event) != 1 or event[0]["payload"].get("epoch_receipt_ref") != stage["output_refs"]["epoch_receipt"] or event[0]["payload"].get("binding_ref") != stage["output_refs"]["binding_receipt"]:
            raise RunStoreError("accepted S5 output is missing or conflicts with its epoch event")


__all__ = ["S5AdmissionError", "S5MaterializationController"]
