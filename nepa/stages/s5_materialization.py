"""S5 epoch materialization controller and its admission boundary."""

from __future__ import annotations

import copy
import hashlib
import os
import subprocess
from typing import Any
from collections.abc import Mapping

from ..orchestrator import ControlledStageFailure, StageContext, StageResult
from ..run_store import ArtifactRef, RunStore, RunStoreError, sha256_bytes
from ..speclib.delivery import DeliveryConstraintError, compile_delivery_blueprint, compile_delivery_constraints
from ..speclib.lint import canonical_json_bytes
from ..speclib.materialization import (
    MaterializationError, attribute_pending_repair, build_artifact_manifest, build_contract_map,
    build_epoch_context, derive_rendering_view, plan_epoch_materialization, render_e0_files,
    validate_completed_epoch, validate_completed_e0,
)
from ..speclib.plan import blueprint_task_semantic_projection, plan_lint
from ..speclib.plan_revision import append_epoch_materialized, validate_file_ledger, validate_revision_ledger
from ..speclib.plan_state import plan_state_snapshot_lint
from ..tools.build import _tree_sha256, run_build_variants, run_smoke_checks
from ..tools.sandbox import SandboxExecutor
from ..tools.git_ops import verify_checkpoint


class S5AdmissionError(MaterializationError):
    """A sealed input is not admissible for S5 materialization."""


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

    @staticmethod
    def _workspace_files(store: RunStore) -> dict[str, bytes]:
        workspace = store._confined("workspace")
        if not workspace.is_dir():
            return {}
        return {
            item.relative_to(workspace).as_posix(): item.read_bytes()
            for item in workspace.rglob("*") if item.is_file() and ".git" not in item.parts
        }

    def _admit_epoch(self, store: RunStore, epoch: str) -> dict[str, Any]:
        run = store.load_run()
        s4 = run["stages"]["s4"]
        expected_s4_refs = {"plan", "active_plan", "delivery_blueprint_sha256", "config_snapshot_sha256"}
        if s4.get("status") != "done" or set(s4.get("output_refs", {})) != expected_s4_refs:
            raise S5AdmissionError("E1+ S5 requires a completed S4 activation seal", code="S5_ADMISSION_INVALID")
        try:
            store.verify_stage_refs(s4, "s4")
            initial_ref = ArtifactRef.from_value(s4["output_refs"]["plan"]).as_dict()
            if initial_ref["path"] != "plan/versions/plan-1.0.0.json":
                raise S5AdmissionError("S4 immutable Plan anchor is not 1.0.0", code="S5_SEAL_INVALID")
            store._read_json_artifact(initial_ref["path"], schema_name="plan.schema.json")
            spec, target, bundle = self._read_inputs(store, run)
            active = store._read_json_artifact("plan/active_plan.json", schema_name="active-plan.schema.json")
            plan_ref = {"path": active["path"], "sha256": active["sha256"]}
            plan = store._read_json_artifact(active["path"], schema_name="plan.schema.json")
            revision = store._read_json_artifact("plan/revision_ledger.json", schema_name="revision-ledger.schema.json")
            ledger = store._read_json_artifact("plan/file_ledger.json", schema_name="file-ledger.schema.json")
            state = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
        except RunStoreError as exc:
            raise S5AdmissionError(str(exc), code="S5_SEAL_INVALID") from exc
        if s4["output_refs"]["config_snapshot_sha256"] != run["config_snapshot_sha256"] or _sha_json(run["config_snapshot"]) != run["config_snapshot_sha256"]:
            raise S5AdmissionError("S4 configuration snapshot anchor drifted", code="S5_CONFIG_DRIFT")
        if active.get("epoch") != epoch or active.get("revision_seq", 0) < 1:
            raise S5AdmissionError("S5 current instance does not match the active E1+ pointer", code="S5_ADMISSION_INVALID")
        epoch_root = store._confined("plan/epochs")
        if epoch_root.is_dir():
            for pending_path in sorted(epoch_root.glob("E*/pending.json"), key=lambda item: item.as_posix().encode("utf-8")):
                if pending_path.parent.name == epoch:
                    continue
                raise S5AdmissionError("another epoch has an unreconciled pending record", code="S5_PENDING_INVALID")
        from ..speclib.plan_revision import latest_activation
        activation = latest_activation(revision)
        if not isinstance(activation, Mapping) or activation.get("level") != "F3" or activation.get("epoch_after") != epoch:
            raise S5AdmissionError("E1+ S5 requires an accepted F3 activation", code="S5_ACTIVATION_INVALID")
        old_ref = dict(activation["from_plan_ref"])
        old_plan = store._read_json_artifact(old_ref["path"], schema_name="plan.schema.json")
        predecessor_epoch = f"E{int(epoch[1:]) - 1}"
        predecessor_receipt = store._read_json_artifact(f"plan/epochs/{predecessor_epoch}/receipt.json", schema_name="epoch-receipt.schema.json")
        old_version_match = __import__("re").search(r"plan-(1\.[0-9]+\.[0-9]+)\.json$", old_ref["path"])
        if old_version_match is None:
            raise S5AdmissionError("activation predecessor Plan ref is not versioned", code="S5_PREDECESSOR_INVALID")
        predecessor_binding = store._read_json_artifact(f"plan/bindings/{old_version_match.group(1)}/receipt.json", schema_name="binding-receipt.schema.json")
        constraints = compile_delivery_constraints(spec, target)
        blueprint = compile_delivery_blueprint(constraints, plan["architecture"], plan["work_packages"], blueprint_task_semantic_projection(plan["tasks"]))
        old_blueprint_path = store._confined(f"plan/epochs/{predecessor_epoch}/blueprint.json")
        if old_blueprint_path.is_file():
            old_blueprint = store._read_json_artifact(f"plan/epochs/{predecessor_epoch}/blueprint.json", schema_name="delivery-blueprint.schema.json")
        else:
            old_blueprint = compile_delivery_blueprint(constraints, old_plan["architecture"], old_plan["work_packages"], blueprint_task_semantic_projection(old_plan["tasks"]))
        lint_plan = copy.deepcopy(plan)
        lint_plan["input_refs"] = {
            "spec": {"path": plan["input_refs"]["spec"]["path"], "sha256": _sha_json(spec)},
            "target_profile": {"path": plan["input_refs"]["target_profile"]["path"], "sha256": _sha_json(constraints["target_profile"])},
            "test_bundle": {"path": plan["input_refs"]["test_bundle"]["path"], "sha256": _sha_json(bundle)},
        }
        full = plan_lint(lint_plan, spec, bundle, level="full", constraints=constraints, blueprint=blueprint, target_profile=target)
        if not full.get("valid"):
            raise S5AdmissionError("active E1+ Plan failed full lint", code="S5_LINT_INVALID")
        state_report = plan_state_snapshot_lint(
            plan,
            state,
            s4_seal={
                "plan": run["stages"]["s4"].get("output_refs", {}).get("plan"),
                "active_plan": active,
                "config_snapshot_sha256": s4["output_refs"]["config_snapshot_sha256"],
            },
            config_snapshot=run["config_snapshot"],
            revision_ledger=revision,
        )
        if not state_report.get("valid"):
            raise S5AdmissionError("active E1+ Plan State failed snapshot lint", code="S5_STATE_INVALID")
        context = build_epoch_context(
            run, plan, active, plan_ref, blueprint, constraints, spec=spec, target=target,
            state=state, file_ledger=ledger, revision_ledger=revision, old_plan=old_plan,
            old_plan_ref=old_ref, old_blueprint=old_blueprint, predecessor_receipt=predecessor_receipt,
            predecessor_binding=predecessor_binding, activation=activation,
        )
        self._validate_epoch_migration(context)
        context["bundle"] = bundle
        return context

    @staticmethod
    def _validate_epoch_migration(context: Mapping[str, Any]) -> None:
        migration = context["migration"]
        old_paths = set(context["old_inventory"])
        new_paths = set(context["new_inventory"])
        known_paths = old_paths | new_paths
        known_artifacts = {row["id"] for row in context["blueprint"].get("build_artifacts", [])}
        known_symbols = {
            export["symbol"]
            for contract in context["plan"].get("architecture", {}).get("contracts", [])
            for export in contract.get("exports", [])
            if isinstance(export, Mapping) and isinstance(export.get("symbol"), str)
        }
        for group in migration.get("pending_groups", []):
            paths = set(group["affected_paths"])
            symbols = set(group["affected_symbols"])
            artifacts = set(group["build_artifact_ids"])
            if not paths and not symbols:
                raise S5AdmissionError("pending migration group has no affected path or symbol", code="S5_MIGRATION_INVALID")
            if not paths <= known_paths or not symbols <= known_symbols or not artifacts <= known_artifacts:
                raise S5AdmissionError("pending migration group references unknown Blueprint facts", code="S5_MIGRATION_INVALID")
        tasks = {task["task_uid"]: task for task in context["plan"].get("tasks", [])}
        quarantined = {
            row.get("quarantine_path"): row
            for row in context["file_ledger"].get("files", [])
            if row.get("state") == "quarantined"
        }
        for row in migration.get("re_adopt", []):
            source = quarantined.get(row["quarantine_path"])
            owner = row["owner"]
            task = tasks.get(owner["task_uid"])
            rule = context["new_inventory"].get(row["target_path"])
            if source is None or source.get("content_sha256") != row["content_sha256"]:
                raise S5AdmissionError("re-adoption does not bind registered quarantine bytes", code="S5_MIGRATION_INVALID")
            if task is None or task.get("id") != owner["task_id"] or rule is None or rule.get("owner_task_id") != owner["task_id"]:
                raise S5AdmissionError("re-adoption target owner disagrees with the active Blueprint", code="S5_MIGRATION_INVALID")

    @staticmethod
    def _apply_epoch_actions(store: RunStore, actions: list[Mapping[str, Any]], facts: Mapping[str, Mapping[str, Any]], fault_hook: Any | None = None) -> None:
        workspace = store._confined("workspace")
        workspace.mkdir(parents=True, exist_ok=True)

        def confined(relative: str):
            target = (workspace / relative).resolve()
            try:
                target.relative_to(workspace.resolve())
            except ValueError as exc:
                raise S5AdmissionError(f"epoch action escapes workspace: {relative}", code="S5_PATH_INVALID") from exc
            return target

        for action in actions:
            kind = action.get("kind")
            if kind == "preserve":
                name = action.get("path")
                if not isinstance(name, str):
                    raise S5AdmissionError("preserve action lacks a path", code="S5_PLAN_INVALID")
                target = confined(name)
                expected = action.get("sha256")
                if not target.is_file() or not isinstance(expected, str) or sha256_bytes(target.read_bytes()) != expected:
                    raise S5AdmissionError(f"preserve preimage disagrees at {name}", code="S5_PREIMAGE_INVALID")
                if fault_hook is not None:
                    fault_hook(f"epoch_action:{kind}:{name}")
                continue
            if kind in {"quarantine", "re_adopt"}:
                source_name = action.get("source_path")
                target_name = action.get("target_path")
                if not isinstance(source_name, str) or not isinstance(target_name, str):
                    raise S5AdmissionError("move action lacks source or target", code="S5_PLAN_INVALID")
                source = confined(source_name); target = confined(target_name)
                if not source.is_file() or target.exists() or sha256_bytes(source.read_bytes()) != action.get("sha256"):
                    raise S5AdmissionError(f"move preimage disagrees at {source_name}", code="S5_PREIMAGE_INVALID")
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
                if fault_hook is not None:
                    fault_hook(f"epoch_action:{kind}:{source_name}")
                continue
            name = action.get("path")
            if not isinstance(name, str):
                raise S5AdmissionError("file action lacks a path", code="S5_PLAN_INVALID")
            target = confined(name)
            fact = facts.get(name, {})
            before = fact.get("before_sha256")
            actual = sha256_bytes(target.read_bytes()) if target.is_file() else None
            if actual != before:
                raise S5AdmissionError(f"action preimage disagrees at {name}", code="S5_PREIMAGE_INVALID")
            if kind == "retire_slot":
                if target.exists():
                    target.unlink()
            elif kind in {"render", "replace", "new_stub"}:
                content = action.get("content")
                if not isinstance(content, str) or sha256_bytes(content.encode("utf-8")) != action.get("sha256"):
                    raise S5AdmissionError(f"action content hash is invalid at {name}", code="S5_PLAN_INVALID")
                store._write_atomic_at(target, content.encode("utf-8"))
            else:
                raise S5AdmissionError(f"unsupported epoch action {kind!r}", code="S5_PLAN_INVALID")
            if fault_hook is not None:
                fault_hook(f"epoch_action:{kind}:{name}")

    def _run_epoch(self, store: RunStore, epoch: str) -> StageResult:
        recovered = store.recover_s5_epoch(epoch, self.fault_hook)
        if recovered is not None:
            return StageResult(output_refs=recovered)
        context = self._admit_epoch(store, epoch)
        current_files = self._workspace_files(store)
        view = derive_rendering_view(context["plan"], context["spec"], context["target"], context["blueprint"], context["constraints"])
        candidates = render_e0_files(view, context["spec"], context["target"], context["blueprint"], context["constraints"])
        migration = context["migration"]
        planned = plan_epoch_materialization(context["old_blueprint"], context["blueprint"], candidates, context["file_ledger"], migration, constraints=context["constraints"], workspace_files=current_files, epoch=epoch, plan_version=context["plan_version"])
        effective = dict(current_files)
        for action in planned["actions"]:
            kind = action.get("kind")
            if kind in {"render", "replace", "new_stub"} and isinstance(action.get("path"), str):
                effective[action["path"]] = action["content"].encode("utf-8")
            elif kind == "retire_slot" and isinstance(action.get("path"), str):
                effective.pop(action["path"], None)
            elif kind == "quarantine":
                effective[action["target_path"]] = effective.pop(action["source_path"])
            elif kind == "re_adopt":
                effective[action["target_path"]] = effective.pop(action["source_path"])
        active_effective = {path: effective[path] for path in planned["active_paths"] if path in effective}
        view_with_files = {**view, "rendered_files": active_effective}
        pending = {
            "schema_version": "1.0", "epoch": epoch, "phase": "pending", "plan_ref": context["plan_ref"],
            "input_refs": {key: {"path": path, "sha256": store.load_run()["inputs"][key]["sha256"]} for key, path in (("spec", "spec/spec.json"), ("target_profile", "inputs/target.json"), ("test_bundle", "inputs/test_bundle.json"))},
            "blueprint": context["blueprint"], "constraints": context["constraints"], "blueprint_sha256": _sha_json(context["blueprint"]),
            "rendering_view": view, "rendering_view_sha256": _sha_json(view),
            "expected_files": [{"path": path, "sha256": sha256_bytes(data), "content": data.decode("utf-8")} for path, data in sorted(effective.items(), key=lambda item: item[0].encode("utf-8"))],
            "before_files": [{"path": path, "sha256": sha256_bytes(data), "content": data.decode("utf-8")} for path, data in sorted(current_files.items(), key=lambda item: item[0].encode("utf-8"))],
            "before_file_ledger": context["file_ledger"],
            "before_manifest": store._read_json_artifact("plan/artifact_manifest.json", schema_name="artifact-manifest.schema.json"),
            "before_contract_map": store._read_json_artifact("plan/contract_map.json", schema_name="contract-map.schema.json"),
            "build_results": [], "smoke_results": [], "checkpoint_commit": None, "checkpoint_tree": None, "output_refs": {},
            "predecessor_epoch": context["predecessor_receipt"]["epoch"], "predecessor_checkpoint": {"commit_sha": context["predecessor_receipt"]["checkpoint_commit"], "tree_sha": context["predecessor_receipt"]["checkpoint_tree"]},
            "predecessor_receipt_ref": {"path": f"plan/epochs/{context['predecessor_receipt']['epoch']}/receipt.json", "sha256": _sha_json(context["predecessor_receipt"])},
            "predecessor_binding_ref": {"path": f"plan/bindings/{context['old_plan_ref']['path'].split('plan-')[-1][:-5]}/receipt.json", "sha256": _sha_json(context["predecessor_binding"])},
            "activation_ref": {"path": "plan/revision_ledger.json", "sha256": sha256_bytes(store._confined("plan/revision_ledger.json").read_bytes())},
            "migration": migration, "action_plan": planned["actions"], "expected_path_facts": planned["expected_path_facts"], "new_inventory": planned["new_inventory"],
            "projected_active_paths": planned["active_paths"], "projected_quarantine_paths": planned["quarantine_paths"],
        }
        pending_path = store._confined(f"plan/epochs/{epoch}/pending.json")
        if pending_path.exists():
            existing = store._read_json_artifact(f"plan/epochs/{epoch}/pending.json", schema_name="s5-pending-state.schema.json")
            if existing.get("checkpoint_commit") is None:
                store.replace_json(f"plan/epochs/{epoch}/pending.json", pending, schema_name="s5-pending-state.schema.json")
            elif existing.get("plan_ref") != pending["plan_ref"] or existing.get("action_plan") != pending["action_plan"]:
                raise RunStoreError("checkpointed epoch pending state conflicts with the current action plan")
        else:
            store.replace_json(f"plan/epochs/{epoch}/pending.json", pending, schema_name="s5-pending-state.schema.json")
        self._apply_epoch_actions(store, planned["actions"], {item["path"]: item for item in planned["expected_path_facts"]}, self.fault_hook)
        pending["phase"] = "rendered"
        store.replace_json(f"plan/epochs/{epoch}/pending.json", pending, schema_name="s5-pending-state.schema.json")
        executor = self._executor_for(context["run"])
        build_results = run_build_variants(executor, store._confined("workspace"), context["blueprint"], context["constraints"], fail_fast=False)
        pending.update({"phase": "built", "build_results": build_results})
        store.replace_json(f"plan/epochs/{epoch}/pending.json", pending, schema_name="s5-pending-state.schema.json")
        if any(item.get("status") != "passed" for item in build_results):
            attribution = attribute_pending_repair(build_results, migration, changed_paths={path for action in planned["actions"] for path in (action.get("path"), action.get("source_path"), action.get("target_path")) if isinstance(path, str)})
            if not attribution["publishable"]:
                raise S5AdmissionError(f"E1+ build failure is not publishable for repair: {attribution['reason']}", code="S5_BUILD_UNREGISTERED")
            groups = attribution["group_ids"]
            pending.update({"materialization_status": "pending_repair", "pending_group_ids": groups, "phase": "built"})
            store.replace_json(f"plan/epochs/{epoch}/pending.json", pending, schema_name="s5-pending-state.schema.json")
            return StageResult(output_refs=store.publish_s5_epoch({"epoch": epoch, "workspace": "workspace", "plan_ref": context["plan_ref"], "blueprint": context["blueprint"], "constraints": context["constraints"], "rendering_view": view_with_files, "action_plan": planned, "rendered_files": active_effective, "workspace_files": effective, "build_results": build_results, "smoke_results": [], "materialization_status": "pending_repair", "pending_group_ids": groups}, fault_hook=self.fault_hook))
        if self.fault_hook is not None:
            self.fault_hook("epoch_build_results_recorded")
        smoke = run_smoke_checks(executor, store._confined("workspace"), context["blueprint"], build_results, context["run"]["config_snapshot"]["smoke"]["dwell_seconds"], context["run"]["config_snapshot"]["smoke"]["term_grace_seconds"])
        pending.update({"phase": "smoked", "smoke_results": smoke, "materialization_status": "ready", "pending_group_ids": []})
        store.replace_json(f"plan/epochs/{epoch}/pending.json", pending, schema_name="s5-pending-state.schema.json")
        if self.fault_hook is not None:
            self.fault_hook("epoch_smoke_results_recorded")
        return StageResult(output_refs=store.publish_s5_epoch({"epoch": epoch, "workspace": "workspace", "plan_ref": context["plan_ref"], "blueprint": context["blueprint"], "constraints": context["constraints"], "rendering_view": view_with_files, "action_plan": planned, "rendered_files": active_effective, "workspace_files": effective, "build_results": build_results, "smoke_results": smoke, "materialization_status": "ready", "pending_group_ids": []}, fault_hook=self.fault_hook))

    def _run(self, context: StageContext) -> StageResult:
        store = context.store
        existing_run = store.load_run()
        current_epoch = existing_run["stages"]["s5"].get("instance_id", "E0")
        if existing_run["stages"]["s5"].get("status") == "done":
            self.reconcile(store)
            return StageResult(output_refs={key: ArtifactRef.from_value(value) for key, value in existing_run["stages"]["s5"]["output_refs"].items()})
        if isinstance(current_epoch, str) and current_epoch != "E0":
            return self._run_epoch(store, current_epoch)
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
        epoch = store._read_json_artifact(refs["epoch_receipt"]["path"], schema_name="epoch-receipt.schema.json")["epoch"]
        revision_seq = 0 if epoch == "E0" else store._read_json_artifact("plan/active_plan.json", schema_name="active-plan.schema.json")["revision_seq"]
        ledger = store._read_json_artifact("plan/revision_ledger.json", schema_name="revision-ledger.schema.json")
        updated = append_epoch_materialized(ledger, epoch_receipt_ref=refs["epoch_receipt"], binding_ref=refs["binding_receipt"], revision_seq=revision_seq)
        store.replace_json("plan/revision_ledger.json", updated, schema_name="revision-ledger.schema.json")
        if self.fault_hook is not None:
            self.fault_hook("epoch_event_appended")
        self.verify_completed(store, allow_pending=True)
        pending = store._confined(f"plan/epochs/{epoch}/pending.json")
        if pending.exists():
            pending.unlink()

    def reconcile(self, store: RunStore) -> None:
        run = store.load_run()
        stage = run["stages"]["s5"]
        if stage.get("status") == "done":
            current_epoch = stage.get("instance_id", "E0")
            if current_epoch != "E0":
                refs = stage.get("output_refs", {})
                if not isinstance(refs, Mapping) or set(refs) != {"epoch_receipt", "binding_receipt"}:
                    raise RunStoreError("accepted epoch S5 output refs are incomplete")
                revision = store._read_json_artifact("plan/revision_ledger.json", schema_name="revision-ledger.schema.json")
                active = store._read_json_artifact("plan/active_plan.json", schema_name="active-plan.schema.json")
                events = [entry for entry in revision.get("entries", []) if entry.get("event_type") == "epoch_materialized" and entry.get("payload", {}).get("revision_seq") == active.get("revision_seq")]
                if not events:
                    self.verify_completed(store, allow_pending=True, require_event=False)
                    updated = append_epoch_materialized(revision, epoch_receipt_ref=refs["epoch_receipt"], binding_ref=refs["binding_receipt"], revision_seq=active["revision_seq"])
                    store.replace_json("plan/revision_ledger.json", updated, schema_name="revision-ledger.schema.json")
                elif len(events) != 1 or events[0].get("payload", {}).get("epoch_receipt_ref") != refs.get("epoch_receipt") or events[0].get("payload", {}).get("binding_ref") != refs.get("binding_receipt"):
                    raise RunStoreError("accepted epoch output conflicts with its epoch event")
                self.verify_completed(store, allow_pending=True, require_event=True)
                pending = store._confined(f"plan/epochs/{current_epoch}/pending.json")
                if pending.exists():
                    pending.unlink()
                return
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
            workspace = store._confined("workspace")
            epoch = store._read_json_artifact("plan/epochs/E0/receipt.json", schema_name="epoch-receipt.schema.json")
            verification_wal = store._confined("plan/verification_pending.json")
            head = subprocess.run(["git", "-C", str(workspace), "rev-parse", "HEAD"], capture_output=True, text=True, check=False).stdout.strip()
            checkpoint = epoch["checkpoint_commit"]
            if not verification_wal.exists() and head == checkpoint:
                self.verify_completed(store, allow_pending=True)
            elif verification_wal.exists():
                # S6 owns the live candidate/WAL boundary; let its reconciliation
                # restore or forward-complete before rechecking the E0 facts.
                pass
            elif store._confined("plan/plan_state.json").exists() and subprocess.run(
                ["git", "-C", str(workspace), "merge-base", "--is-ancestor", checkpoint, head],
                capture_output=True,
                check=False,
            ).returncode == 0:
                pass
            else:
                self.verify_completed(store, allow_pending=True)
            pending = store._confined("plan/epochs/E0/pending.json")
            if pending.exists():
                pending.unlink()

    def verify_completed(self, store: RunStore, *, allow_pending: bool = False, require_event: bool = True) -> None:
        run = store.load_run()
        stage = run["stages"]["s5"]
        if stage.get("status") != "done":
            raise RunStoreError("cannot verify an S5 stage that is not done")
        current_epoch = stage.get("instance_id", "E0")
        if current_epoch != "E0":
            if not allow_pending and store._confined(f"plan/epochs/{current_epoch}/pending.json").exists():
                raise RunStoreError("completed S5 still has an unreconciled pending record")
            store.verify_stage_refs(stage, "s5")
            refs = stage.get("output_refs", {})
            epoch = store._read_json_artifact(f"plan/epochs/{current_epoch}/receipt.json", schema_name="epoch-receipt.schema.json")
            active = store._read_json_artifact("plan/active_plan.json", schema_name="active-plan.schema.json")
            binding = store._read_json_artifact(f"plan/bindings/{active['version']}/receipt.json", schema_name="binding-receipt.schema.json")
            store.verify_ref(binding["manifest_ref"], schema_name="artifact-manifest.schema.json")
            store.verify_ref(binding["contract_map_ref"], schema_name="contract-map.schema.json")
            version_manifest = store._read_json_artifact(binding["manifest_ref"]["path"], schema_name="artifact-manifest.schema.json")
            version_map = store._read_json_artifact(binding["contract_map_ref"]["path"], schema_name="contract-map.schema.json")
            manifest = store._read_json_artifact("plan/artifact_manifest.json", schema_name="artifact-manifest.schema.json")
            contract_map = store._read_json_artifact("plan/contract_map.json", schema_name="contract-map.schema.json")
            immutable_manifest = store._read_json_artifact(f"plan/epochs/{current_epoch}/artifact_manifest.json", schema_name="artifact-manifest.schema.json")
            immutable_map = store._read_json_artifact(f"plan/epochs/{current_epoch}/contract_map.json", schema_name="contract-map.schema.json")
            if manifest != immutable_manifest or manifest != version_manifest or contract_map != immutable_map or contract_map != version_map:
                raise RunStoreError("current, epoch and version manifest/map copies drifted")
            plan = store._read_json_artifact(active["path"], schema_name="plan.schema.json")
            spec, target, _bundle = self._read_inputs(store, run)
            constraints = compile_delivery_constraints(spec, target)
            blueprint = compile_delivery_blueprint(constraints, plan["architecture"], plan["work_packages"], blueprint_task_semantic_projection(plan["tasks"]))
            for ref in epoch.get("build_result_refs", []) + epoch.get("smoke_result_refs", []):
                store.verify_ref(ref, schema_name="build-result.schema.json" if "/build/" in ref["path"] else "smoke-result.schema.json")
            workspace = store._confined("workspace")
            verify_checkpoint(workspace, {"commit_sha": epoch["checkpoint_commit"], "tree_sha": epoch["checkpoint_tree"], "plan_version": active["version"], "epoch": current_epoch})
            source_files = [item for item in workspace.rglob("*") if item.is_file() and ".git" not in item.parts] if workspace.is_dir() else []
            paths = [item.relative_to(workspace).as_posix() for item in source_files]
            hashes = {item.relative_to(workspace).as_posix(): sha256_bytes(item.read_bytes()) for item in source_files}
            for ref in epoch.get("build_result_refs", []) + epoch.get("smoke_result_refs", []):
                evidence = store._read_json_artifact(ref["path"])
                if evidence.get("tree_sha256") != _tree_sha256(workspace):
                    raise RunStoreError("accepted epoch evidence tree drifted")
                if "/build/" in ref["path"] and epoch.get("materialization_status") == "ready" and evidence.get("status") != "passed":
                    raise RunStoreError("ready epoch has failed build evidence")
            validate_completed_epoch(run, plan, blueprint, store._read_json_artifact("plan/file_ledger.json", schema_name="file-ledger.schema.json"), store._read_json_artifact("plan/revision_ledger.json", schema_name="revision-ledger.schema.json"), epoch, binding, manifest, contract_map, {"paths": paths, "file_hashes": hashes, "constraints": constraints}, active_plan=active, require_event=require_event)
            return
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
