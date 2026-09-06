"""M1-6 ordinary S6 execution controller."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from ..agents.s6 import CODER_INPUTS, FIXER_INPUTS, S6AgentError, candidate_tree_hash, coding_contract, normalize_candidate, project_s6_context
from ..agents.base import AgentInvoker
from ..llm.client import StructuredOutputError
from ..orchestrator import BudgetExhausted, ControlledStageFailure, StageContext, StageResult
from ..run_store import ArtifactRef, RunStore, RunStoreError, sha256_bytes
from ..schemas import load_schema
from ..speclib.lint import canonical_json_bytes
from ..speclib.delivery import compile_delivery_blueprint, compile_delivery_constraints
from ..speclib.materialization import build_artifact_manifest, build_contract_map, derive_rendering_view, parse_c99_declaration, render_e0_files
from ..speclib.plan import blueprint_task_semantic_projection
from ..speclib.plan_state import execution_state_lint, initialize_plan_state, plan_state_snapshot_lint, project_state_transition
from ..speclib.plan_revision import append_verification_committed, validate_file_ledger, validate_revision_ledger
from ..tools.build import _tree_sha256, run_build_variants, run_smoke_checks
from ..tools.git_ops import GitOperationError, prepare_task_commit, publish_task_commit
from ..tools.sandbox import SandboxExecutor


class S6AdmissionError(RuntimeError):
    """The run is not a coherent ready-E0 input."""


class S6ExecutionError(RuntimeError):
    """An ordinary candidate cannot be accepted."""


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _git(workspace: Path, *args: str) -> str:
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(["git", *args], cwd=workspace, capture_output=True, text=True, check=False, env=env)
    if result.returncode != 0:
        raise S6ExecutionError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _clean(workspace: Path) -> bool:
    return _git(workspace, "status", "--porcelain", "--untracked-files=all") == ""


def _status_paths(workspace: Path) -> set[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if result.returncode != 0:
        raise S6ExecutionError(f"git status failed: {result.stderr.strip()}")
    lines = result.stdout.splitlines()
    paths: set[str] = set()
    for line in lines:
        if len(line) < 4:
            raise S6ExecutionError("git status returned an invalid porcelain row")
        value = line[3:]
        if " -> " in value:
            value = value.rsplit(" -> ", 1)[1]
        paths.add(value)
    return paths


def _commit_descends(workspace: Path, commit: str, ancestor: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, commit],
        cwd=workspace,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _load(store: RunStore, path: str, schema: str | None = None) -> dict[str, Any]:
    return store._read_json_artifact(path, schema_name=schema)


def _validate_wal_snapshots(wal: Mapping[str, Any]) -> None:
    for key, schema_name in (
        ("old_state", "plan-state.schema.json"), ("new_state", "plan-state.schema.json"),
        ("old_file_ledger", "file-ledger.schema.json"), ("new_file_ledger", "file-ledger.schema.json"),
        ("old_revision_ledger", "revision-ledger.schema.json"), ("new_revision_ledger", "revision-ledger.schema.json"),
    ):
        errors = sorted(
            Draft202012Validator(load_schema(schema_name)).iter_errors(wal.get(key)),
            key=lambda error: (tuple(error.absolute_path), error.message),
        )
        if errors:
            raise RunStoreError(f"verification WAL {key} is invalid: {errors[0].message}")
    for key in ("old_file_ledger", "new_file_ledger"):
        validate_file_ledger(wal[key])
    for key in ("old_revision_ledger", "new_revision_ledger"):
        validate_revision_ledger(wal[key])


def _task_files(workspace: Path, paths: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in sorted(paths, key=lambda value: value.encode("utf-8")):
        raw_path = workspace / relative
        if raw_path.is_symlink() or any(parent.is_symlink() for parent in raw_path.parents if parent != workspace and workspace in parent.parents):
            raise S6ExecutionError(f"task file is a symlink: {relative}")
        path = raw_path.resolve()
        try:
            path.relative_to(workspace.resolve())
        except ValueError as exc:
            raise S6ExecutionError(f"task path escapes workspace: {relative}") from exc
        if not path.is_file() or path.is_symlink():
            raise S6ExecutionError(f"task file is missing or unsafe: {relative}")
        result[relative] = path.read_text(encoding="utf-8")
    return result


def _spec_slice(
    spec: Mapping[str, Any], task: Mapping[str, Any], work_package: Mapping[str, Any] | None = None,
) -> list[Mapping[str, Any]]:
    refs = {
        item.get("id")
        for source in (task, work_package or {})
        for item in source.get("context_refs", [])
        if isinstance(item, Mapping)
    }
    requirements = spec.get("requirements", [])
    if not refs:
        return []
    return [item for item in requirements if isinstance(item, Mapping) and item.get("id") in refs]


def _work_package(plan: Mapping[str, Any], task: Mapping[str, Any]) -> Mapping[str, Any]:
    package_id = task.get("work_package")
    for package in plan.get("work_packages", []):
        if isinstance(package, Mapping) and package.get("id") == package_id:
            return package
    return {}


def _public_task(task: Mapping[str, Any]) -> dict[str, Any]:
    """Remove controller-only identities and digests from the Agent card."""

    forbidden = {"task_uid", "obligation_digest", "guidance_digest"}
    return {key: copy.deepcopy(value) for key, value in task.items() if key not in forbidden}


def _architecture_projection(
    plan: Mapping[str, Any], task: Mapping[str, Any], work_package: Mapping[str, Any],
) -> dict[str, Any]:
    architecture = plan.get("architecture", {})
    module_id = work_package.get("module")
    contract_ids = set(task.get("consumes_contracts", [])) | set(task.get("provides_contracts", []))
    return {
        "schema_version": architecture.get("schema_version"),
        "modules": [
            copy.deepcopy(item) for item in architecture.get("modules", [])
            if isinstance(item, Mapping) and item.get("id") == module_id
        ],
        "contracts": [
            copy.deepcopy(item) for item in architecture.get("contracts", [])
            if isinstance(item, Mapping) and item.get("id") in contract_ids
        ],
        "decisions": copy.deepcopy(architecture.get("decisions", [])),
        "assumptions": copy.deepcopy(architecture.get("assumptions", [])),
    }


def _contract_projection(contract_map: Mapping[str, Any], task: Mapping[str, Any]) -> dict[str, Any]:
    contract_ids = set(task.get("consumes_contracts", [])) | set(task.get("provides_contracts", []))
    return {
        "schema_version": contract_map.get("schema_version"),
        "contracts": [
            copy.deepcopy(item) for item in contract_map.get("contracts", [])
            if isinstance(item, Mapping) and item.get("contract_id") in contract_ids
        ],
    }


def _interface_files(workspace: Path, plan: Mapping[str, Any], task: Mapping[str, Any]) -> dict[str, str]:
    contract_ids = set(task.get("consumes_contracts", [])) | set(task.get("provides_contracts", []))
    result: dict[str, str] = {}
    for contract in plan.get("architecture", {}).get("contracts", []):
        if not isinstance(contract, Mapping) or contract.get("id") not in contract_ids:
            continue
        for relative in contract.get("interface_files", []):
            if isinstance(relative, str):
                result[relative] = _task_files(workspace, [relative])[relative]
    return result


def _find_previous_failure(store: RunStore, task_uid: str, attempt: int) -> Mapping[str, Any] | None:
    if attempt <= 1:
        return None
    record = _load(store, f"attempts/{task_uid}/attempt_{attempt - 1:03d}.json", "s6-attempt.schema.json")
    ref = record.get("failure_ref")
    if record.get("status") != "failed" or not isinstance(ref, Mapping):
        raise RunStoreError("Fixer requires the immediately preceding terminal failure")
    store.verify_ref(ref)
    value = _load(store, ref["path"])
    if value.get("attempt") != attempt - 1:
        raise RunStoreError("Fixer failure artifact attempt identity drifted")
    return value


def _failure_ref(store: RunStore, task_uid: str, attempt: int, value: Mapping[str, Any]) -> ArtifactRef:
    return store.publish_immutable_json(f"attempts/{task_uid}/attempt_{attempt:03d}/failure.json", dict(value))


def _model_output_ref(store: RunStore, task_uid: str, attempt: int, text: str) -> ArtifactRef:
    return store.publish_immutable_bytes(
        f"attempts/{task_uid}/attempt_{attempt:03d}/model_output.json",
        text.encode("utf-8"),
    )


def _candidate_text(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or not isinstance(value.get("files"), list):
        return {}
    result: dict[str, str] = {}
    for item in value["files"]:
        if isinstance(item, Mapping) and isinstance(item.get("path"), str) and isinstance(item.get("content"), str):
            result[item["path"]] = item["content"]
    return result


def _write_candidate(store: RunStore, task_uid: str, attempt: int, files: Mapping[str, bytes]) -> dict[str, ArtifactRef]:
    refs: dict[str, ArtifactRef] = {}
    for relative, content in sorted(files.items(), key=lambda item: item[0].encode("utf-8")):
        refs[relative] = store.publish_immutable_bytes(f"attempts/{task_uid}/attempt_{attempt:03d}/candidate/{relative}", content)
    return refs


def _archive_candidate_response(store: RunStore, task_uid: str, attempt: int, value: Any) -> ArtifactRef | None:
    """Archive ordered model file bytes without using model paths as host paths."""

    if not isinstance(value, Mapping) or not isinstance(value.get("files"), list):
        return None
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(value["files"]):
        if not isinstance(item, Mapping) or not isinstance(item.get("path"), str) or not isinstance(item.get("content"), str):
            continue
        data = item["content"].encode("utf-8")
        ref = store.publish_immutable_bytes(
            f"attempts/{task_uid}/attempt_{attempt:03d}/candidate_blobs/{index:03d}-{sha256_bytes(data)}.bin",
            data,
        )
        rows.append({"index": index, "path": item["path"], "content_ref": ref.as_dict()})
    return store.publish_immutable_json(
        f"attempts/{task_uid}/attempt_{attempt:03d}/candidate_manifest.json",
        {"files": rows},
    )


def _candidate_workspace(workspace: Path, store: RunStore, task_uid: str, attempt: int, files: Mapping[str, bytes]) -> Path:
    target = store._confined(f"_scratch/s6/{task_uid}/attempt_{attempt:03d}")
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(workspace, target, symlinks=False, ignore=shutil.ignore_patterns(".git"))
    for relative, content in files.items():
        path = (target / relative).resolve()
        try:
            path.relative_to(target.resolve())
        except ValueError as exc:
            raise S6ExecutionError(f"candidate path escapes attempt workspace: {relative}") from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return target


def _validate_contract_map(plan: Mapping[str, Any], contract_map: Mapping[str, Any]) -> None:
    expected: dict[str, Mapping[str, Any]] = {}
    for contract in plan.get("architecture", {}).get("contracts", []):
        if isinstance(contract, Mapping):
            expected[str(contract.get("id"))] = contract
    actual = {str(item.get("contract_id")): item for item in contract_map.get("contracts", []) if isinstance(item, Mapping)}
    if set(actual) != set(expected):
        raise S6ExecutionError("contract map does not bind exactly the sealed contract set")
    for contract_id, source in expected.items():
        bound = actual[contract_id]
        for field in ("owner", "ready_gate", "interface_files"):
            if bound.get(field) != source.get(field):
                raise S6ExecutionError(f"contract map drifted for {contract_id}:{field}")
        if bound.get("provider_task_id") != source.get("provider_task_id"):
            raise S6ExecutionError(f"contract map drifted for {contract_id}:provider_task_id")
        source_exports = {(item.get("symbol"), item.get("signature"), item.get("interface_file")) for item in source.get("exports", []) if isinstance(item, Mapping)}
        bound_exports = {(item.get("symbol"), item.get("signature"), item.get("interface_file")) for item in bound.get("exports", []) if isinstance(item, Mapping)}
        if source_exports != bound_exports:
            raise S6ExecutionError(f"contract map exports drifted for {contract_id}")


def _validate_candidate_bindings(
    files: Mapping[str, bytes],
    task: Mapping[str, Any],
    plan: Mapping[str, Any],
    blueprint: Mapping[str, Any],
    contract_map: Mapping[str, Any],
) -> None:
    rules = {str(item.get("path_pattern")): item for item in blueprint.get("file_rules", []) if isinstance(item, Mapping)}
    allowed = set(task.get("deliverable_files", []))
    for path in files:
        rule = rules.get(path)
        if path not in allowed or not isinstance(rule, Mapping) or rule.get("mutability") != "s6_owned" or rule.get("owner_task_id") != task.get("id"):
            raise S6ExecutionError(f"candidate path is not owned by the current task: {path}")
    _validate_contract_map(plan, contract_map)
    for contract in contract_map.get("contracts", []):
        if not isinstance(contract, Mapping):
            continue
        for export in contract.get("exports", []):
            if not isinstance(export, Mapping) or export.get("owner_task_id") != task.get("id"):
                continue
            implementation = export.get("implementation_file")
            if not isinstance(implementation, str) or implementation not in files:
                raise S6ExecutionError(f"sealed export {export.get('symbol', '')} has no candidate implementation")
            text = files[implementation].decode("utf-8")
            symbol = str(export.get("symbol", ""))
            if not symbol or re.search(rf"\b{re.escape(symbol)}\b", text) is None:
                raise S6ExecutionError(f"candidate does not implement sealed export {symbol}")
            try:
                declaration = parse_c99_declaration(str(export.get("signature", "")))
            except Exception as exc:
                raise S6ExecutionError(f"sealed contract declaration is invalid for {symbol}") from exc
            if declaration.get("kind") == "function":
                definition = re.search(
                    rf"(?m)^\s*{re.escape(str(declaration['return_type']))}\s+{re.escape(symbol)}\s*\((?P<parameters>[^()]*)\)\s*\{{",
                    text,
                )
                if definition is None:
                    raise S6ExecutionError(f"candidate declaration drifts for sealed export {symbol}")
                try:
                    candidate_declaration = parse_c99_declaration(
                        f"{declaration['return_type']} {symbol}({definition.group('parameters')});"
                    )
                except Exception as exc:
                    raise S6ExecutionError(f"candidate declaration is invalid for {symbol}") from exc
                expected_types = [item["c_type"] for item in declaration.get("parameters", [])]
                candidate_types = [item["c_type"] for item in candidate_declaration.get("parameters", [])]
                if candidate_declaration.get("return_type") != declaration.get("return_type") or candidate_types != expected_types:
                    raise S6ExecutionError(f"candidate declaration drifts for sealed export {symbol}")


def _publish_results(store: RunStore, prefix: str, build: list[Mapping[str, Any]], smoke: list[Mapping[str, Any]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    build_refs: list[dict[str, str]] = []
    for index, result in enumerate(build):
        variant = str(result.get("variant", index))
        build_refs.append(store.publish_immutable_json(f"{prefix}/build_{variant}.json", dict(result), schema_name="build-result.schema.json").as_dict())
    smoke_refs: list[dict[str, str]] = []
    for index, result in enumerate(smoke):
        variant = str(result.get("variant", index))
        artifact = str(result.get("artifact", index)).replace("/", "_")
        smoke_refs.append(store.publish_immutable_json(f"{prefix}/smoke_{variant}_{artifact}.json", dict(result), schema_name="smoke-result.schema.json").as_dict())
    return build_refs, smoke_refs


def _file_ledger_after(
    ledger: Mapping[str, Any],
    task: Mapping[str, Any],
    changed: Mapping[str, bytes],
    commit_sha: str,
    build_refs: list[Mapping[str, Any]],
    evidence_ref: Mapping[str, Any],
    plan_version: str,
) -> dict[str, Any]:
    result = copy.deepcopy(dict(ledger))
    by_path = {row["path"]: row for row in result["files"]}
    owner = {"plan_version": plan_version, "task_uid": task["task_uid"], "task_id": task["id"]}
    verified = {"build_variant_ids": [str(ref["path"]).split("build_")[-1].removesuffix(".json") for ref in build_refs], "evidence_ref": dict(evidence_ref)}
    for relative, content in changed.items():
        row = by_path.get(relative)
        if row is None or row.get("class") != "s6_owned":
            raise S6ExecutionError(f"accepted file is not an S6-owned ledger row: {relative}")
        history = row.get("owner_history", [])
        if not history or history[-1] != owner:
            history = [*history, owner]
        by_path[relative] = {
            "path": relative, "class": "s6_owned", "state": "realized", "created_in_epoch": "E0",
            "content_sha256": sha256_bytes(content), "last_commit_sha": commit_sha,
            "verified_by": verified, "owner_history": history,
        }
    result["files"] = [by_path[path] for path in sorted(by_path, key=lambda value: value.encode("utf-8"))]
    validate_file_ledger(result)
    return result


def _state_after_success(
    state: Mapping[str, Any], task_id: str, commit_sha: str,
    evidence_ref: Mapping[str, Any], file_ledger: Mapping[str, Any],
    revision_ledger: Mapping[str, Any], notes: str,
) -> dict[str, Any]:
    event_seq = revision_ledger["entries"][-1]["event_seq"]
    return project_state_transition(
        state,
        {
            "schema_version": "2.0", "event": "attempt_succeeded", "task_id": task_id,
            "commit_sha": commit_sha, "evidence_ref": dict(evidence_ref), "notes": notes,
            "proof": {
                "commit_sha": commit_sha, "evidence_ref": dict(evidence_ref),
                "file_ledger_ref": {"path": "plan/file_ledger.json", "sha256": _hash(file_ledger)},
                "revision_ledger_ref": {"path": "plan/revision_ledger.json", "sha256": _hash(revision_ledger)},
                "event_ref": {"event_seq": event_seq},
            },
        },
    )


class S6ExecutionController:
    """Run ordinary tasks serially with durable candidate and commit boundaries."""

    def __init__(self, agent: AgentInvoker, executor: Any | None = None, *, fault_hook: Any | None = None) -> None:
        self.agent = agent
        self.executor = executor
        self.fault_hook = fault_hook

    def _fault(self, point: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(point)

    @staticmethod
    def _finish_attempt(
        store: RunStore,
        task_uid: str,
        attempt: int,
        *,
        status: str,
        output_ref: Mapping[str, Any] | None = None,
        failure_ref: Mapping[str, Any] | None = None,
    ) -> None:
        path = f"attempts/{task_uid}/attempt_{attempt:03d}.json"
        record = _load(store, path, "s6-attempt.schema.json")
        if record.get("status") in {"failed", "succeeded", "exhausted"}:
            if record.get("status") != status or record.get("output_ref") != output_ref or record.get("failure_ref") != failure_ref:
                raise S6ExecutionError("S6 attempt record has conflicting terminal facts")
            return
        record.update({"status": status, "output_ref": dict(output_ref) if output_ref is not None else None, "failure_ref": dict(failure_ref) if failure_ref is not None else None})
        store.replace_json(path, record, schema_name="s6-attempt.schema.json")

    @staticmethod
    def _record_failure(store: RunStore, task: Mapping[str, Any], attempt: int, error: Mapping[str, Any]) -> ArtifactRef:
        failure_ref = _failure_ref(store, task["task_uid"], attempt, error)
        state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
        if attempt >= 4:
            attempt_record = _load(store, f"attempts/{task['task_uid']}/attempt_{attempt:03d}.json", "s6-attempt.schema.json")
            event = {
                "schema_version": "2.0", "event": "attempts_exhausted", "task_id": task["id"],
                "error": failure_ref.path,
                "proof": {
                    "previous_failure_ref": failure_ref.as_dict(),
                    "evidence_seq": attempt_record["evidence_seq"],
                    "s6_attempts_used": state["s6_attempts_used"],
                },
            }
            plan = _load(store, state["plan_ref"]["path"], "plan.schema.json")
            state = project_state_transition(state, event, plan=plan, config_snapshot=store.load_run()["config_snapshot"])
            store.replace_json("plan/plan_state.json", state, schema_name="plan-state.schema.json")
        attempt_path = store._confined(f"attempts/{task['task_uid']}/attempt_{attempt:03d}.json")
        if attempt_path.exists():
            S6ExecutionController._finish_attempt(
                store,
                task["task_uid"],
                attempt,
                status="exhausted" if attempt >= 4 else "failed",
                failure_ref=failure_ref.as_dict(),
            )
        return failure_ref

    def _admit(self, store: RunStore) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        run = store.load_run()
        if run["stages"]["s5"].get("status") != "done":
            raise S6AdmissionError("S6 requires a completed S5 stage")
        output = run["stages"]["s5"].get("output_refs", {})
        if set(output) != {"epoch_receipt", "binding_receipt"}:
            raise S6AdmissionError("S5 output refs are incomplete")
        epoch_ref = ArtifactRef.from_value(output["epoch_receipt"])
        binding_ref = ArtifactRef.from_value(output["binding_receipt"])
        store.verify_ref(epoch_ref, schema_name="epoch-receipt.schema.json")
        store.verify_ref(binding_ref, schema_name="binding-receipt.schema.json")
        epoch = store._read_json_artifact(epoch_ref.path, schema_name="epoch-receipt.schema.json")
        binding = store._read_json_artifact(binding_ref.path, schema_name="binding-receipt.schema.json")
        if epoch.get("materialization_status") != "ready" or epoch.get("epoch") != "E0":
            raise S6AdmissionError("S6 accepts only ready E0")
        active = _load(store, "plan/active_plan.json", "active-plan.schema.json")
        plan = _load(store, active["path"], "plan.schema.json")
        blueprint = _load(store, "plan/_s4/delivery_blueprint.json", "delivery-blueprint.schema.json")
        constraints = _load(store, "plan/_s4/delivery_constraints.json")
        ledger = _load(store, "plan/file_ledger.json", "file-ledger.schema.json")
        revision = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
        manifest = _load(store, "plan/artifact_manifest.json", "artifact-manifest.schema.json")
        contract_map = _load(store, "plan/contract_map.json", "contract-map.schema.json")
        immutable_manifest = _load(store, "plan/bindings/1.0.0/artifact_manifest.json", "artifact-manifest.schema.json")
        immutable_contract_map = _load(store, "plan/bindings/1.0.0/contract_map.json", "contract-map.schema.json")
        validate_file_ledger(ledger)
        validate_revision_ledger(revision)
        if binding.get("plan_ref") != {"path": active["path"], "sha256": active["sha256"]}:
            raise S6AdmissionError("E0 binding does not reference the active Plan")
        if binding.get("epoch_receipt_ref") != output["epoch_receipt"]:
            raise S6AdmissionError("E0 binding does not reference the accepted epoch receipt")
        if active.get("sha256") != store._json_artifact_hash(active["path"]):
            raise S6AdmissionError("active Plan pointer hash is drifted")
        if active.get("version") != "1.0.0" or active.get("revision_seq") != 0 or active.get("epoch") != "E0":
            raise S6AdmissionError("S6 requires the active 1.0.0/E0/0 pointer")
        if manifest != immutable_manifest or contract_map != immutable_contract_map:
            raise S6AdmissionError("current E0 manifest/map copies drifted from immutable bindings")
        if binding.get("manifest_ref") != {"path": "plan/bindings/1.0.0/artifact_manifest.json", "sha256": _hash(immutable_manifest)} or binding.get("contract_map_ref") != {"path": "plan/bindings/1.0.0/contract_map.json", "sha256": _hash(immutable_contract_map)}:
            raise S6AdmissionError("E0 binding does not reference the immutable manifest/map")
        if epoch.get("materialized_plan_ref") != {"path": active["path"], "sha256": active["sha256"]} or epoch.get("blueprint_sha256") != _hash(blueprint):
            raise S6AdmissionError("E0 receipt Plan or Blueprint binding drifted")
        if _git(store._confined("workspace"), "rev-parse", f"{epoch['checkpoint_commit']}^{{tree}}") != epoch["checkpoint_tree"]:
            raise S6AdmissionError("E0 checkpoint tree is not stable")
        if _hash(blueprint) != run["stages"]["s4"]["output_refs"]["delivery_blueprint_sha256"] or plan.get("delivery_blueprint_sha256") != _hash(blueprint):
            raise S6AdmissionError("sealed Delivery Blueprint binding drifted")
        try:
            spec = _load(store, "spec/spec.json")
            target = _load(store, "inputs/target.json")
            derived_constraints = compile_delivery_constraints(spec, target)
            derived_blueprint = compile_delivery_blueprint(derived_constraints, plan["architecture"], plan["work_packages"], blueprint_task_semantic_projection(plan["tasks"]))
        except Exception as exc:
            raise S6AdmissionError(f"frozen E0 delivery inputs cannot be recomputed: {exc}") from exc
        if derived_constraints != constraints or derived_blueprint != blueprint:
            raise S6AdmissionError("sealed E0 delivery inputs do not recompute")
        rendering_view = derive_rendering_view(plan, spec, target, blueprint, constraints)
        rendered_files = render_e0_files(rendering_view, spec, target, blueprint, constraints)
        plan_anchor = {"path": active["path"], "sha256": active["sha256"]}
        expected_manifest = build_artifact_manifest(plan_anchor, blueprint, {**rendering_view, "rendered_files": rendered_files}, "E0")
        expected_contract_map = build_contract_map(plan_anchor, blueprint, {**rendering_view, "rendered_files": rendered_files}, "E0")
        if immutable_manifest != expected_manifest or immutable_contract_map != expected_contract_map:
            raise S6AdmissionError("immutable E0 manifest/map do not recompute")
        _validate_contract_map(plan, immutable_contract_map)
        for ref in epoch.get("build_result_refs", []) + epoch.get("smoke_result_refs", []):
            schema = "smoke-result.schema.json" if "/smoke/" in str(ref.get("path", "")) else "build-result.schema.json"
            store.verify_ref(ref, schema_name=schema)
            result = _load(store, ref["path"], schema)
            if result.get("status") != "passed":
                raise S6AdmissionError("E0 receipt contains a failed validation result")
        workspace = store._confined("workspace")
        if not workspace.is_dir() or not _clean(workspace):
            raise S6AdmissionError("S6 workspace must be a clean E0 workspace")
        head = _git(workspace, "rev-parse", "HEAD")
        state_path = store._confined("plan/plan_state.json")
        if not state_path.exists() and head != epoch["checkpoint_commit"]:
            raise S6AdmissionError("Plan State is missing after the accepted E0 baseline")
        if state_path.exists() and not _commit_descends(workspace, head, epoch["checkpoint_commit"]):
            raise S6AdmissionError("S6 workspace is not descended from its accepted E0 checkpoint")
        if not state_path.exists() and _git(workspace, "rev-parse", "HEAD^{tree}") != epoch["checkpoint_tree"]:
            raise S6AdmissionError("workspace is not the accepted E0 checkpoint")
        if not state_path.exists():
            attempts_root = store._confined("attempts")
            if attempts_root.is_dir() and any(item.is_file() for item in attempts_root.rglob("*")):
                raise S6AdmissionError("attempt artifacts exist without the corresponding Plan State")
        if state_path.exists():
            state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
            report = plan_state_snapshot_lint(plan, state, s4_seal={"plan": run["stages"]["s4"]["output_refs"]["plan"], "active_plan": active, "config_snapshot_sha256": run["config_snapshot_sha256"]}, config_snapshot=run["config_snapshot"], revision_ledger=revision)
            if not report["valid"]:
                raise S6AdmissionError(report["errors"][0]["message"])
        else:
            state = initialize_plan_state(plan, plan_ref=active)
            store.replace_json("plan/plan_state.json", state, schema_name="plan-state.schema.json")
        return run, plan, active, blueprint, constraints, epoch

    def reconcile_verification_wal(self, store: RunStore) -> None:
        path = store._confined("plan/verification_pending.json")
        if not path.exists():
            return
        wal = _load(store, "plan/verification_pending.json", "verification-pending.schema.json")
        _validate_wal_snapshots(wal)
        workspace = store._confined("workspace")
        head = _git(workspace, "rev-parse", "HEAD")
        if wal["phase"] != "committed":
            if head == wal["expected_parent"]:
                allowed = {item["path"] for item in wal.get("changed_files", [])}
                dirty = _status_paths(workspace)
                if not dirty.issubset(allowed):
                    raise S6ExecutionError("pre-commit verification WAL found unrelated workspace changes")
                if dirty:
                    for relative in dirty:
                        ref = wal.get("candidate_file_refs", {}).get(relative)
                        if not isinstance(ref, Mapping):
                            raise S6ExecutionError("pre-commit WAL is missing a candidate file reference")
                        store.verify_ref(ref)
                        if (workspace / relative).read_bytes() != store._confined(ref["path"]).read_bytes():
                            raise S6ExecutionError("pre-commit workspace bytes disagree with the candidate")
                    selected = sorted(dirty, key=lambda value: value.encode("utf-8"))
                    _git(workspace, "restore", "--source", wal["expected_parent"], "--staged", "--worktree", "--", *selected)
                path.unlink()
                self._fault("s6_wal_removed")
                return
            if not self._legal_task_commit(workspace, wal, head):
                raise S6ExecutionError("pre-commit verification WAL found a conflicting workspace commit")
            wal = self._complete_wal_commit(store, wal, head)
            store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
        commit_sha = wal.get("commit_sha")
        if not isinstance(commit_sha, str) or not self._legal_task_commit(workspace, wal, commit_sha):
            raise S6ExecutionError("committed verification WAL does not match the legal task commit")
        for label, value in (("new_state", wal["new_state"]), ("new_file_ledger", wal["new_file_ledger"]), ("new_revision_ledger", wal["new_revision_ledger"])):
            if not isinstance(value, Mapping):
                raise S6ExecutionError(f"verification WAL {label} is incomplete")
        current_state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
        current_file = _load(store, "plan/file_ledger.json", "file-ledger.schema.json")
        current_revision = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
        if current_state not in (wal["old_state"], wal["new_state"]):
            raise S6ExecutionError("verification WAL State has conflicting facts")
        if current_file not in (wal["old_file_ledger"], wal["new_file_ledger"]):
            raise S6ExecutionError("verification WAL file ledger has conflicting facts")
        if current_revision not in (wal["old_revision_ledger"], wal["new_revision_ledger"]):
            raise S6ExecutionError("verification WAL revision ledger has conflicting facts")
        if current_state != wal["new_state"]:
            store.replace_json("plan/plan_state.json", wal["new_state"], schema_name="plan-state.schema.json")
            self._fault("s6_state_projection_published")
        if current_file != wal["new_file_ledger"]:
            store.replace_json("plan/file_ledger.json", wal["new_file_ledger"], schema_name="file-ledger.schema.json")
            self._fault("s6_file_ledger_published")
        if current_revision != wal["new_revision_ledger"]:
            store.replace_json("plan/revision_ledger.json", wal["new_revision_ledger"], schema_name="revision-ledger.schema.json")
            self._fault("s6_event_published")
        self._finish_attempt(
            store,
            wal["task_uid"],
            int(wal["attempt"]),
            status="succeeded",
            output_ref=wal["evidence_ref"],
        )
        self._fault("s6_attempt_terminal_published")
        path.unlink()
        self._fault("s6_wal_removed")

    @staticmethod
    def _legal_task_commit(workspace: Path, wal: Mapping[str, Any], commit: str) -> bool:
        try:
            if commit != wal.get("expected_commit"):
                return False
            if _git(workspace, "rev-parse", f"{commit}^{{commit}}") != commit:
                return False
            parents = _git(workspace, "rev-list", "--parents", "-n", "1", commit).split()
            if len(parents) != 2 or parents[1] != wal["expected_parent"]:
                return False
            changed = set(_git(workspace, "diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines())
            expected = {item["path"] for item in wal.get("changed_files", [])}
            if changed != expected:
                return False
            if _git(workspace, "rev-parse", f"{commit}^{{tree}}") != wal.get("expected_git_tree"):
                return False
            if _git(workspace, "rev-parse", "HEAD") == commit and _tree_sha256(workspace) != wal["expected_tree"]:
                return False
            for key, value in wal["expected_trailers"].items():
                actual = _git(workspace, "show", "-s", f"--format=%(trailers:key={key},valueonly)", commit).splitlines()
                if actual != [str(value)]:
                    return False
            return True
        except (S6ExecutionError, KeyError, TypeError):
            return False

    def _complete_wal_commit(self, store: RunStore, wal: Mapping[str, Any], commit_sha: str) -> dict[str, Any]:
        store.verify_ref(wal["evidence_ref"], schema_name="task-evidence.schema.json")
        evidence = _load(store, wal["evidence_ref"]["path"], "task-evidence.schema.json")
        for ref in wal.get("candidate_file_refs", {}).values():
            store.verify_ref(ref)
        for ref in wal.get("build_result_refs", []):
            store.verify_ref(ref, schema_name="build-result.schema.json")
            if _load(store, ref["path"], "build-result.schema.json").get("status") != "passed":
                raise RunStoreError("verification WAL references a failed build result")
        for ref in wal.get("smoke_result_refs", []):
            store.verify_ref(ref, schema_name="smoke-result.schema.json")
            if _load(store, ref["path"], "smoke-result.schema.json").get("status") != "passed":
                raise RunStoreError("verification WAL references a failed smoke result")
        old_state = wal["old_state"]
        task = next((row for row in old_state["tasks"] if row["id"] == wal["task_id"]), None)
        if not isinstance(task, Mapping):
            raise S6ExecutionError("verification WAL task is not in old State")
        changed: dict[str, bytes] = {}
        for item in wal.get("changed_files", []):
            relative = item["path"]
            ref = wal.get("candidate_file_refs", {}).get(relative)
            if not isinstance(ref, Mapping):
                raise S6ExecutionError("verification WAL changed file has no candidate reference")
            data = store._confined(ref["path"]).read_bytes()
            if sha256_bytes(data) != item["sha256"]:
                raise S6ExecutionError("verification WAL candidate hash drifted")
            changed[relative] = data
        new_file = _file_ledger_after(wal["old_file_ledger"], task, changed, commit_sha, evidence["build_result_refs"], wal["evidence_ref"], old_state["plan_ref"]["version"])
        new_revision = append_verification_committed(wal["old_revision_ledger"], task_uid=wal["task_uid"], evidence_ref=wal["evidence_ref"], commit_sha=commit_sha, revision_seq=old_state["plan_ref"]["revision_seq"])
        new_state = _state_after_success(old_state, wal["task_id"], commit_sha, wal["evidence_ref"], new_file, new_revision, str(wal.get("notes", "")))
        if new_state != wal.get("new_state") or new_file != wal.get("new_file_ledger") or new_revision != wal.get("new_revision_ledger"):
            raise RunStoreError("verification WAL projected snapshots are incomplete or conflicting")
        result = copy.deepcopy(dict(wal))
        result.update({"commit_sha": commit_sha, "phase": "committed"})
        return result

    def _choose(self, plan: Mapping[str, Any], state: Mapping[str, Any]) -> Mapping[str, Any] | None:
        rows = {row["id"]: row for row in state["tasks"]}
        for task in sorted(plan.get("tasks", []), key=lambda value: value["id"].encode("utf-8")):
            row = rows[task["id"]]
            if row["status"] not in {"pending", "in_progress"}:
                continue
            dependencies = task.get("depends_on", [])
            if all(rows.get(dependency, {}).get("status") == "done" for dependency in dependencies):
                return task
        return None

    def _propagate_dependency_blocks(self, store: RunStore, plan: Mapping[str, Any]) -> None:
        state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
        rows = {row["id"]: row for row in state["tasks"]}
        changed_any = False
        changed = True
        while changed:
            changed = False
            for task in plan.get("tasks", []):
                row = rows[task["id"]]
                if row["status"] != "pending":
                    continue
                blocked = [dependency for dependency in task.get("depends_on", []) if rows.get(dependency, {}).get("status") in {"blocked", "blocked_by_dependency"}]
                if blocked:
                    state = project_state_transition(
                        state,
                        {"schema_version": "2.0", "event": "dependency_blocked", "task_id": task["id"], "error": f"dependency blocked: {blocked[0]}"},
                        plan=plan, config_snapshot=store.load_run()["config_snapshot"],
                    )
                    rows = {item["id"]: item for item in state["tasks"]}
                    changed = True
                    changed_any = True
        if changed_any:
            store.replace_json("plan/plan_state.json", state, schema_name="plan-state.schema.json")

    def _run_task(self, context: StageContext, plan: Mapping[str, Any], blueprint: Mapping[str, Any], constraints: Mapping[str, Any], task: Mapping[str, Any]) -> None:
        store = context.store
        workspace = store._confined("workspace")
        state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
        row = next(item for item in state["tasks"] if item["id"] == task["id"])
        attempt = row["attempts"] + 1
        tier = "T2" if attempt <= 3 else "T1"
        role = "coder" if attempt == 1 else "fixer"
        baseline_commit = _git(workspace, "rev-parse", "HEAD")
        baseline_tree = _tree_sha256(workspace)
        allocation = store.allocate_s6_attempt(task_id=task["id"], task_uid=task["task_uid"], role=role, tier=tier, baseline_commit=baseline_commit, baseline_tree=baseline_tree)
        state = allocation["state"]
        self._fault("s6_attempt_allocated")
        previous = _find_previous_failure(store, task["task_uid"], attempt)
        current_files = _task_files(workspace, list(task.get("deliverable_files", [])))
        failure_candidate = previous.get("candidate", {}) if previous else None
        feedback = previous.get("feedback", {}) if previous else None
        diagnosis = previous.get("diagnosis", {}) if previous else None
        package = _work_package(plan, task)
        contract_map = _load(store, "plan/contract_map.json", "contract-map.schema.json")
        try:
            inputs, _accounting = project_s6_context(
                task=_public_task(task), work_package=package,
                architecture=_architecture_projection(plan, task, package),
                spec_slice=_spec_slice(store._read_json_artifact("spec/spec.json"), task, package),
                contract_map=_contract_projection(contract_map, task),
                interface_files=_interface_files(workspace, plan, task), language_guidance=constraints.get("advisory", {}), current_files=current_files,
                execution_mode="normal", failed_candidate=failure_candidate, validation_feedback=feedback, diagnosis=diagnosis,
                max_tokens=int(context.run["config_snapshot"]["budgets"]["coder_context_max_tokens"]),
            )
        except (S6AgentError, RunStoreError) as exc:
            error = {"attempt": attempt, "code": "CONTEXT_INVALID", "detail": str(exc), "candidate": {}, "feedback": {"error": str(exc)}, "diagnosis": {}}
            self._record_failure(store, task, attempt, error)
            return None
        schema, example = coding_contract()
        try:
            if context.orchestrator is not None:
                context.orchestrator.admit_external_call(store)
            result = self.agent.invoke(
                role=role, inputs={key: inputs[key] for key in (CODER_INPUTS if role == "coder" else FIXER_INPUTS)},
                output_schema=schema, output_example=example, run_id=context.run["run_id"], stage="S6", task_id=task["id"], attempt=attempt,
                use_cache=False, tier_override=tier, allow_structured_repair=False,
            )
            parsed = result.parsed
            model_output_ref = _model_output_ref(store, task["task_uid"], attempt, str(getattr(result.response, "text", "")))
        except BudgetExhausted:
            raise
        except Exception as exc:
            parsed = None
            responses = exc.responses if isinstance(exc, StructuredOutputError) else []
            model_ref = _model_output_ref(store, task["task_uid"], attempt, responses[-1].text) if responses else None
            error = {"attempt": attempt, "code": "AGENT_FAILURE", "detail": str(exc), "candidate": {}, "model_output_ref": model_ref.as_dict() if model_ref else None, "feedback": {"error": str(exc)}, "diagnosis": {}}
            self._record_failure(store, task, attempt, error)
            return
        files: dict[str, bytes] = {}
        candidate_refs: dict[str, ArtifactRef] = {}
        builds: list[Mapping[str, Any]] = []
        smokes: list[Mapping[str, Any]] = []
        candidate_manifest_ref = _archive_candidate_response(store, task["task_uid"], attempt, parsed)
        try:
            files = normalize_candidate(parsed, task, _load(store, "plan/file_ledger.json", "file-ledger.schema.json"))
            candidate_refs = _write_candidate(store, task["task_uid"], attempt, files)
            self._fault("s6_candidate_persisted")
            candidate = _candidate_workspace(workspace, store, task["task_uid"], attempt, files)
            self._fault("s6_candidate_workspace_ready")
            candidate_task_files = _task_files(candidate, list(task.get("deliverable_files", [])))
            _validate_candidate_bindings(
                {path: content.encode("utf-8") for path, content in candidate_task_files.items()},
                task, plan, blueprint, _load(store, "plan/contract_map.json", "contract-map.schema.json"),
            )
            executor = self.executor or SandboxExecutor(context.run["config_snapshot"]["sandbox"]["image"], context.run["config_snapshot"]["sandbox"]["cpu"], context.run["config_snapshot"]["sandbox"]["mem_gb"])
            builds = run_build_variants(executor, candidate, blueprint, constraints, fail_fast=False)
            smokes = run_smoke_checks(executor, candidate, blueprint, builds, context.run["config_snapshot"]["smoke"]["dwell_seconds"], context.run["config_snapshot"]["smoke"]["term_grace_seconds"], fail_fast=False) if all(item["status"] == "passed" for item in builds) else []
            build_refs, smoke_refs = _publish_results(store, f"attempts/{task['task_uid']}/attempt_{attempt:03d}", builds, smokes)
            passed = bool(builds) and all(item["status"] == "passed" for item in builds) and bool(smokes) and all(item["status"] == "passed" for item in smokes)
            if not passed:
                error = {"attempt": attempt, "code": "CANDIDATE_VALIDATION_FAILED", "detail": "build or smoke failed", "candidate": {path: content.decode("utf-8") for path, content in files.items()}, "candidate_manifest_ref": candidate_manifest_ref.as_dict() if candidate_manifest_ref else None, "candidate_refs": {path: ref.as_dict() for path, ref in candidate_refs.items()}, "candidate_tree": _tree_sha256(candidate), "model_output_ref": model_output_ref.as_dict(), "feedback": {"build": builds, "smoke": smokes}, "diagnosis": {}}
                self._record_failure(store, task, attempt, error)
                return
            candidate_tree = _tree_sha256(candidate)
            changed = {path: content for path, content in files.items() if not (workspace / path).is_file() or (workspace / path).read_bytes() != content}
            required_files = set(task.get("deliverable_files", []))
            if not changed:
                raise S6ExecutionError("normal S6 candidate contains no actual changes")
            if set(changed) != required_files:
                missing = sorted(required_files - set(changed), key=lambda value: value.encode("utf-8"))
                raise S6ExecutionError(f"normal S6 candidate does not realize every task deliverable: {missing}")
            evidence = {
                "schema_version": "2.0", "task_uid": task["task_uid"], "task_id": task["id"], "evidence_seq": allocation["attempt"]["evidence_seq"], "execution_kind": "normal", "attempt": attempt, "amendment_used": 0,
                "plan_ref": dict(state["plan_ref"]), "plan_version": state["plan_ref"]["version"], "epoch": state["plan_ref"]["epoch"], "binding_ref": dict(context.run["stages"]["s5"]["output_refs"]["binding_receipt"]), "input_refs": {key: {"path": path, "sha256": context.run["inputs"][key]["sha256"]} for key, path in (("spec", "spec/spec.json"), ("target_profile", "inputs/target.json"), ("test_bundle", "inputs/test_bundle.json"))}, "plan_sha256": _hash(plan), "workspace_tree": candidate_tree, "build_result_refs": build_refs, "smoke_result_refs": smoke_refs, "test_summary_refs": [],
                "changed_files": [{"path": path, "sha256": sha256_bytes(content)} for path, content in sorted(changed.items(), key=lambda item: item[0].encode("utf-8"))], "accepted": True, "migration_ref": None,
            }
            evidence_ref = store.publish_immutable_json(f"test_results/task_evidence/{task['task_uid']}/evidence_{evidence['evidence_seq']:03d}.json", evidence, schema_name="task-evidence.schema.json")
            self._fault("s6_evidence_published")
            prepared = prepare_task_commit(
                workspace, changed, task_id=task["id"], task_uid=task["task_uid"],
                attempt=attempt, evidence_seq=evidence["evidence_seq"], evidence_sha256=evidence_ref.sha256,
                plan_version=state["plan_ref"]["version"], epoch=state["plan_ref"]["epoch"],
            )
            old_file = _load(store, "plan/file_ledger.json", "file-ledger.schema.json")
            old_revision = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
            new_file = _file_ledger_after(old_file, task, changed, prepared["commit_sha"], build_refs, evidence_ref.as_dict(), state["plan_ref"]["version"])
            new_revision = append_verification_committed(old_revision, task_uid=task["task_uid"], evidence_ref=evidence_ref.as_dict(), commit_sha=prepared["commit_sha"], revision_seq=state["plan_ref"]["revision_seq"])
            new_state = _state_after_success(state, task["id"], prepared["commit_sha"], evidence_ref.as_dict(), new_file, new_revision, parsed.get("notes", ""))
            wal = {
                "schema_version": "2.0", "kind": "normal", "task_id": task["id"], "task_uid": task["task_uid"], "attempt": attempt, "evidence_seq": evidence["evidence_seq"],
                "old_state": state, "new_state": new_state, "old_file_ledger": old_file, "new_file_ledger": new_file, "old_revision_ledger": old_revision, "new_revision_ledger": new_revision, "evidence_ref": evidence_ref.as_dict(), "candidate_ref": next(iter(candidate_refs.values())).as_dict(), "candidate_file_refs": {path: ref.as_dict() for path, ref in candidate_refs.items()}, "changed_files": [{"path": path, "sha256": sha256_bytes(content)} for path, content in sorted(changed.items(), key=lambda item: item[0].encode("utf-8"))], "build_result_refs": build_refs, "smoke_result_refs": smoke_refs, "notes": parsed.get("notes", ""), "expected_tree": candidate_tree, "expected_git_tree": prepared["tree_sha"], "expected_commit": prepared["commit_sha"], "commit_message": prepared["message"], "commit_timestamp": prepared["timestamp"], "expected_parent": baseline_commit,
                "expected_trailers": {"NePA-Task": task["id"], "NePA-Task-UID": task["task_uid"], "NePA-Plan": state["plan_ref"]["version"], "NePA-Epoch": state["plan_ref"]["epoch"], "NePA-Attempt": str(attempt), "NePA-Evidence-Seq": str(evidence["evidence_seq"]), "NePA-Evidence-SHA256": evidence_ref.sha256}, "phase": "prepared", "commit_sha": None,
            }
            store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
            self._fault("s6_wal_prepared")
            for path, content in changed.items():
                target = workspace / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            self._fault("s6_candidate_installed")
            wal["phase"] = "candidate_installed"
            store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
            commit = publish_task_commit(workspace, changed.keys(), prepared)
            self._fault("s6_commit_created")
            wal.update({"phase": "committed", "commit_sha": commit["commit_sha"]})
            store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
            self._fault("s6_wal_committed")
            self.reconcile_verification_wal(store)
            self._fault("s6_state_published")
            return
        except (S6AgentError, GitOperationError, RunStoreError, RuntimeError) as exc:
            if _git(workspace, "rev-parse", "HEAD") != baseline_commit:
                raise RunStoreError(f"post-commit S6 publication failed: {exc}") from exc
            candidate_text = {path: content.decode("utf-8") for path, content in files.items()} or _candidate_text(parsed)
            error = {"attempt": attempt, "code": "CANDIDATE_FAILURE", "detail": str(exc), "candidate": candidate_text, "candidate_manifest_ref": candidate_manifest_ref.as_dict() if candidate_manifest_ref else None, "candidate_refs": {path: ref.as_dict() for path, ref in candidate_refs.items()}, "model_output_ref": model_output_ref.as_dict(), "feedback": {"build": builds, "smoke": smokes, "error": str(exc)}, "diagnosis": {}}
            self._record_failure(store, task, attempt, error)
            return

    def _finalize(self, context: StageContext, plan: Mapping[str, Any], active: Mapping[str, Any], blueprint: Mapping[str, Any], constraints: Mapping[str, Any], epoch: Mapping[str, Any]) -> StageResult:
        store = context.store
        workspace = store._confined("workspace")
        state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
        if any(row["status"] != "done" for row in state["tasks"]):
            raise ControlledStageFailure({"code": "EXECUTION_UNRESOLVED", "detail": "static-valid tasks remain unresolved"})
        executor = self.executor or SandboxExecutor(context.run["config_snapshot"]["sandbox"]["image"], context.run["config_snapshot"]["sandbox"]["cpu"], context.run["config_snapshot"]["sandbox"]["mem_gb"])
        builds = run_build_variants(executor, workspace, blueprint, constraints, fail_fast=False)
        if not all(item["status"] == "passed" for item in builds):
            raise ControlledStageFailure({"code": "S6_EXIT_VALIDATION_FAILED", "detail": "final build validation failed"})
        smokes = run_smoke_checks(executor, workspace, blueprint, builds, context.run["config_snapshot"]["smoke"]["dwell_seconds"], context.run["config_snapshot"]["smoke"]["term_grace_seconds"], fail_fast=False)
        if not all(item["status"] == "passed" for item in smokes):
            raise ControlledStageFailure({"code": "S6_EXIT_VALIDATION_FAILED", "detail": "final smoke validation failed"})
        file_ledger = _load(store, "plan/file_ledger.json", "file-ledger.schema.json")
        revision = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
        lint = execution_state_lint(
            plan, state, workspace, store.root,
            {"s5": {"workspace_head": epoch["checkpoint_commit"], "output_refs": context.run["stages"]["s5"]["output_refs"]}},
            config_snapshot=context.run["config_snapshot"], revision_ledger=revision, active_pointer=active,
        )
        if not lint["valid"]:
            raise ControlledStageFailure({"code": "S6_EXIT_VALIDATION_FAILED", "detail": lint["errors"][0]["message"]})
        build_refs, smoke_refs = _publish_results(store, "build/s6/final", builds, smokes)
        state_ref = ArtifactRef("plan/plan_state.json", store._json_artifact_hash("plan/plan_state.json"))
        file_ref = ArtifactRef("plan/file_ledger.json", store._json_artifact_hash("plan/file_ledger.json"))
        revision_ref = store.publish_immutable_json("plan/s6_revision_ledger.json", revision, schema_name="revision-ledger.schema.json")
        receipt = {
            "schema_version": "2.0", "active_plan_ref": context.run["stages"]["s4"]["output_refs"]["active_plan"],
            "binding_ref": context.run["stages"]["s5"]["output_refs"]["binding_receipt"], "plan_state_ref": state_ref.as_dict(), "file_ledger_ref": file_ref.as_dict(), "revision_ledger_ref": revision_ref.as_dict(),
            "workspace_head": _git(workspace, "rev-parse", "HEAD"), "build_result_refs": build_refs, "s6_build_ok": True, "smoke_result_refs": smoke_refs, "status": "complete",
        }
        receipt_ref = store.publish_immutable_json("plan/s6_receipt.json", receipt, schema_name="s6-receipt.schema.json")
        self._fault("s6_receipt_published")
        return StageResult(output_refs={"s6_receipt": receipt_ref})

    def run(self, context: StageContext) -> StageResult:
        store = context.store
        receipt_path = store._confined("plan/s6_receipt.json")
        if receipt_path.exists():
            receipt_ref = ArtifactRef("plan/s6_receipt.json", store._json_artifact_hash("plan/s6_receipt.json"))
            store.verify_ref(receipt_ref, schema_name="s6-receipt.schema.json")
            self.verify_completed(store)
            return StageResult(output_refs={"s6_receipt": receipt_ref})
        self.reconcile_verification_wal(store)
        try:
            run, plan, active, blueprint, constraints, epoch = self._admit(store)
        except (S6AdmissionError, RunStoreError, S6ExecutionError) as exc:
            raise ControlledStageFailure({"code": "S6_ADMISSION_INVALID", "detail": str(exc)}) from exc
        while True:
            self._propagate_dependency_blocks(store, plan)
            state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
            task = self._choose(plan, state)
            if task is None:
                if any(row["status"] == "pending" for row in state["tasks"]):
                    raise ControlledStageFailure({"code": "EXECUTION_UNRESOLVED", "detail": "no executable task remains in the current dependency graph"})
                break
            self._run_task(context, plan, blueprint, constraints, task)
        final = self._finalize(context, plan, active, blueprint, constraints, epoch)
        return StageResult(output_refs=final.output_refs)

    def verify_completed(self, store: RunStore) -> None:
        run = store.load_run()
        receipt = _load(store, "plan/s6_receipt.json", "s6-receipt.schema.json")
        store.verify_ref({"path": "plan/s6_receipt.json", "sha256": store._json_artifact_hash("plan/s6_receipt.json")}, schema_name="s6-receipt.schema.json")
        store.verify_ref(receipt["active_plan_ref"], schema_name="active-plan.schema.json")
        store.verify_ref(receipt["binding_ref"], schema_name="binding-receipt.schema.json")
        store.verify_ref(receipt["plan_state_ref"], schema_name="plan-state.schema.json")
        store.verify_ref(receipt["file_ledger_ref"], schema_name="file-ledger.schema.json")
        store.verify_ref(receipt["revision_ledger_ref"], schema_name="revision-ledger.schema.json")
        if receipt["active_plan_ref"] != run["stages"]["s4"]["output_refs"].get("active_plan"):
            raise RunStoreError("S6 receipt active Plan binding drifted from S4")
        if receipt["binding_ref"] != run["stages"]["s5"]["output_refs"].get("binding_receipt"):
            raise RunStoreError("S6 receipt binding drifted from S5")
        expected_refs = {
            "plan/active_plan.json": receipt["active_plan_ref"],
            "plan/plan_state.json": receipt["plan_state_ref"],
            "plan/file_ledger.json": receipt["file_ledger_ref"],
        }
        for relative, ref in expected_refs.items():
            if ref["path"] != relative or store._json_artifact_hash(relative) != ref["sha256"]:
                raise RunStoreError(f"S6 receipt reference drifted for {relative}")
        current_revision = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
        validate_revision_ledger(current_revision)
        receipt_revision = _load(store, receipt["revision_ledger_ref"]["path"], "revision-ledger.schema.json")
        if receipt_revision != current_revision:
            raise RunStoreError("S6 receipt revision-ledger prefix drifted")
        workspace = store._confined("workspace")
        if store._confined("plan/verification_pending.json").exists():
            raise RunStoreError("completed S6 retains a verification WAL")
        if not _clean(workspace) or _git(workspace, "rev-parse", "HEAD") != receipt["workspace_head"]:
            raise RunStoreError("S6 receipt workspace binding drifted")
        for ref, schema in [(item, "build-result.schema.json") for item in receipt["build_result_refs"]] + [(item, "smoke-result.schema.json") for item in receipt["smoke_result_refs"]]:
            store.verify_ref(ref, schema_name=schema)
            if _load(store, ref["path"], schema).get("status") != "passed":
                raise RunStoreError("S6 receipt references a failed validation result")
        active = _load(store, "plan/active_plan.json", "active-plan.schema.json")
        plan = _load(store, active["path"], "plan.schema.json")
        state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
        epoch_ref = run["stages"]["s5"]["output_refs"]["epoch_receipt"]
        store.verify_ref(epoch_ref, schema_name="epoch-receipt.schema.json")
        epoch = _load(store, epoch_ref["path"], "epoch-receipt.schema.json")
        report = execution_state_lint(
            plan, state, workspace, store.root,
            {"s5": {"workspace_head": epoch["checkpoint_commit"], "output_refs": run["stages"]["s5"]["output_refs"]}},
            config_snapshot=run["config_snapshot"], revision_ledger=current_revision, active_pointer=active,
        )
        if not report["valid"]:
            raise RunStoreError("completed S6 execution facts are invalid: " + report["errors"][0]["message"])

    def reconcile(self, store: RunStore) -> None:
        """Finish a durable WAL first, then account for an interrupted call."""

        self.reconcile_verification_wal(store)
        state_path = store._confined("plan/plan_state.json")
        if not state_path.exists():
            return
        state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
        for row in state["tasks"]:
            if row["status"] != "in_progress" or row["last_error"] is not None:
                continue
            attempt = row["attempts"]
            directory = store._confined(f"attempts/{row['task_uid']}/attempt_{attempt:03d}")
            record = directory / "failure.json"
            if record.exists():
                continue
            candidate: dict[str, str] = {}
            candidate_refs: dict[str, dict[str, str]] = {}
            candidate_root = directory / "candidate"
            if candidate_root.is_dir():
                for candidate_file in sorted((item for item in candidate_root.rglob("*") if item.is_file()), key=lambda item: item.relative_to(candidate_root).as_posix().encode("utf-8")):
                    relative = candidate_file.relative_to(candidate_root).as_posix()
                    data = candidate_file.read_bytes()
                    candidate[relative] = data.decode("utf-8")
                    candidate_refs[relative] = {"path": f"attempts/{row['task_uid']}/attempt_{attempt:03d}/candidate/{relative}", "sha256": sha256_bytes(data)}
            error = {
                "attempt": attempt, "code": "ATTEMPT_INTERRUPTED",
                "detail": "the process stopped after allocating the attempt",
                "candidate": candidate, "candidate_refs": candidate_refs,
                "candidate_tree": candidate_tree_hash({path: value.encode("utf-8") for path, value in candidate.items()}) if candidate else None,
                "feedback": {}, "diagnosis": {},
            }
            self._record_failure(store, row, attempt, error)


__all__ = ["S6AdmissionError", "S6ExecutionController", "S6ExecutionError"]
