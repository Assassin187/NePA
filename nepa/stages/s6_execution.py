"""M1-6 ordinary S6 execution controller."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator

from ..agents.s6 import CODER_INPUTS, FIXER_INPUTS, LEASE_FIXER_INPUTS, S6AgentError, candidate_tree_hash, coding_contract, normalize_candidate, project_s6_context
from ..agents.base import AgentInvoker
from ..llm.client import StructuredOutputError
from ..orchestrator import BudgetExhausted, ControlledStageFailure, StageContext, StagePause, StageResult
from ..run_store import ArtifactConflict, ArtifactRef, RunStore, RunStoreError, RunValidationError, sha256_bytes
from ..schemas import load_schema
from ..speclib.lint import canonical_json_bytes
from ..speclib.delivery import compile_delivery_blueprint, compile_delivery_constraints, expand_file_rules
from ..speclib.materialization import MaterializationError, attribute_pending_repair, build_artifact_manifest, build_contract_map, derive_rendering_view, parse_c99_declaration, project_version_binding, render_e0_files, validate_completed_epoch
from ..speclib.plan import blueprint_task_semantic_projection, plan_to_draft_ir
from ..speclib.planning import build_test_manifest_metadata
from ..speclib.revision_mechanism import (
    RevisionMechanismError, append_trigger_batch, build_gate_result, build_revision_rehearsal,
    complete_revision_candidate, derive_revision_obligation_scope, estimate_revision_rework,
    evaluate_revision_budget, evaluate_revision_triggers, prepare_activation_wal,
    project_plan_critic_delta, project_revision_availability, project_revision_boundary,
    project_revision_evaluation,
)
from ..speclib.plan_state import PlanStateError, execution_state_lint, initialize_plan_state, lease_lender_directly_related, plan_state_snapshot_lint, project_state_transition, validate_lease_authorization
from ..speclib.plan_revision import (
    PlanRevisionError, append_candidate_rejected, append_lease_finished, append_revision_evaluated,
    append_verification_committed, build_event_entry, latest_activation, project_file_ledger,
    project_plan_state, successor_pointer, validate_file_ledger, validate_revision_ledger,
)
from .s4_planning import bind_plan_critic_contract, validate_plan_critic_result
from .s5_materialization import S5MaterializationController
from ..tools.build import _tree_sha256, run_build_variants, run_smoke_checks
from ..tools.git_ops import GitOperationError, prepare_joint_commit, prepare_task_commit, publish_joint_commit, publish_task_commit
from ..tools.sandbox import SandboxExecutor


class S6AdmissionError(RuntimeError):
    """The run is not a coherent ready-E0 input."""


class S6ExecutionError(RuntimeError):
    """An ordinary candidate cannot be accepted."""


class ContractExportDrift(S6ExecutionError):
    """Typed controller rejection for provider-submission contract drift."""


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


def _call_refs_for_attempt(store: RunStore, task_id: str, attempt: int) -> list[dict[str, str]]:
    trace_path = store._confined("trace/llm_calls.ndjson")
    if not trace_path.is_file():
        return []
    refs: list[dict[str, str]] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, Mapping) and row.get("task_id") == task_id and row.get("attempt") == attempt and isinstance(row.get("output_path"), str):
            refs.append({"path": row["output_path"]})
    return refs[-1:]


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


def _candidate_from_manifest(
    store: RunStore,
    task_uid: str,
    attempt: int,
    task: Mapping[str, Any],
    allowed_paths: set[str],
) -> dict[str, bytes] | None:
    manifest_path = f"attempts/{task_uid}/attempt_{attempt:03d}/candidate_manifest.json"
    if not store._confined(manifest_path).is_file():
        return None
    manifest = _load(store, manifest_path)
    rows = manifest.get("files") if isinstance(manifest, Mapping) else None
    if not isinstance(rows, list):
        raise S6ExecutionError("persisted candidate manifest is malformed")
    files: dict[str, bytes] = {}
    frozen = {
        row.get("path")
        for row in _load(store, "plan/file_ledger.json", "file-ledger.schema.json").get("files", [])
        if row.get("class") == "s5_frozen"
    }
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str) or not isinstance(row.get("content_ref"), Mapping):
            raise S6ExecutionError("persisted candidate manifest row is malformed")
        store.verify_ref(row["content_ref"])
        path = str(row["path"])
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts or "\\" in path or not path.strip() or path in frozen or path not in allowed_paths or path in files:
            raise S6ExecutionError(f"persisted candidate manifest contains an illegal path: {path}")
        data = store._confined(row["content_ref"]["path"]).read_bytes()
        data.decode("utf-8")
        files[path] = data
    if not files:
        raise S6ExecutionError("persisted candidate manifest is empty")
    return files


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
    lease_tasks: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    written_paths: set[str] | None = None,
    allowed_paths: set[str] | None = None,
) -> None:
    rules = {str(item.get("path_pattern")): item for item in blueprint.get("file_rules", []) if isinstance(item, Mapping)}
    owner_by_path = {path: task.get("id") for path in task.get("deliverable_files", [])}
    for lender in (lease_tasks or {}).values():
        for path in lender.get("deliverable_files", []):
            owner_by_path[path] = lender.get("id")
    allowed = set(owner_by_path) if allowed_paths is None else set(allowed_paths)
    writes = set(files) if written_paths is None else set(written_paths)
    for path in writes:
        rule = rules.get(path)
        if path not in allowed or not isinstance(rule, Mapping) or rule.get("mutability") != "s6_owned" or rule.get("owner_task_id") != owner_by_path.get(path):
            raise S6ExecutionError(f"candidate path is not owned by the current task: {path}")
    _validate_contract_map(plan, contract_map)
    for contract in contract_map.get("contracts", []):
        if not isinstance(contract, Mapping):
            continue
        for export in contract.get("exports", []):
            if not isinstance(export, Mapping) or export.get("owner_task_id") not in ({task.get("id")} | {item.get("id") for item in (lease_tasks or {}).values()}):
                continue
            implementation = export.get("implementation_file")
            if not isinstance(implementation, str) or implementation not in files:
                raise ContractExportDrift(f"sealed export {export.get('symbol', '')} has no candidate implementation")
            text = files[implementation].decode("utf-8")
            symbol = str(export.get("symbol", ""))
            if not symbol or re.search(rf"\b{re.escape(symbol)}\b", text) is None:
                raise ContractExportDrift(f"candidate does not implement sealed export {symbol}")
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
                    raise ContractExportDrift(f"candidate declaration drifts for sealed export {symbol}")
                try:
                    candidate_declaration = parse_c99_declaration(
                        f"{declaration['return_type']} {symbol}({definition.group('parameters')});"
                    )
                except Exception as exc:
                    raise S6ExecutionError(f"candidate declaration is invalid for {symbol}") from exc
                expected_types = [item["c_type"] for item in declaration.get("parameters", [])]
                candidate_types = [item["c_type"] for item in candidate_declaration.get("parameters", [])]
                if candidate_declaration.get("return_type") != declaration.get("return_type") or candidate_types != expected_types:
                    raise ContractExportDrift(f"candidate declaration drifts for sealed export {symbol}")


def _attribute_group_members(
    builds: list[Mapping[str, Any]],
    descriptor: Mapping[str, Any],
    blueprint: Mapping[str, Any],
    contract_map: Mapping[str, Any],
) -> set[str]:
    """Map strict frozen path/symbol/artifact diagnostics to group members."""

    members = list(descriptor["members"])
    uid_by_id = {str(task["id"]): str(task["task_uid"]) for task in members}
    affected_paths = set(descriptor["affected_paths"])
    affected_symbols = set(descriptor["affected_symbols"])
    symbols_by_uid: dict[str, set[str]] = {uid: set() for uid in uid_by_id.values()}
    for contract in contract_map.get("contracts", []):
        if not isinstance(contract, Mapping):
            continue
        provider = uid_by_id.get(str(contract.get("provider_task_id")))
        for export in contract.get("exports", []):
            if not isinstance(export, Mapping) or str(export.get("symbol")) not in affected_symbols:
                continue
            owner = uid_by_id.get(str(export.get("owner_task_id"))) or provider
            if owner is not None:
                symbols_by_uid[owner].add(str(export["symbol"]))
    diagnostic_groups = [
        {
            "group_id": str(task["task_uid"]),
            "affected_paths": sorted(set(task.get("deliverable_files", [])) & affected_paths),
            "affected_symbols": sorted(symbols_by_uid[str(task["task_uid"])]),
        }
        for task in members
    ]
    parsed = attribute_pending_repair(builds, {"pending_groups": diagnostic_groups})
    if parsed.get("publishable"):
        return set(parsed["group_ids"])

    diagnostics = "\n".join(
        f"{item.get('stdout', '')}\n{item.get('stderr', '')}"
        for item in builds
        if item.get("status") != "passed"
    )
    frozen_artifacts = set(descriptor["build_artifact_ids"])
    source_sets = {str(item.get("id")): set(item.get("file_rule_ids", [])) for item in blueprint.get("link_source_sets", []) if isinstance(item, Mapping)}
    rules = {str(item.get("id")): item for item in blueprint.get("file_rules", []) if isinstance(item, Mapping)}
    artifact_owners: list[set[str]] = []
    for artifact in blueprint.get("build_artifacts", []):
        if not isinstance(artifact, Mapping) or str(artifact.get("id")) not in frozen_artifacts:
            continue
        owners = {
            uid_by_id[str(rules[rule_id]["owner_task_id"])]
            for rule_id in source_sets.get(str(artifact.get("link_source_set_id")), set())
            if rule_id in rules and str(rules[rule_id].get("owner_task_id")) in uid_by_id
        }
        tokens = (str(artifact.get("id")), str(artifact.get("path")))
        if any(token and token in diagnostics for token in tokens):
            artifact_owners.append(owners)
    if len(artifact_owners) == 1:
        return artifact_owners[0]
    return set()


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
    build_refs: Sequence[Mapping[str, Any]],
    evidence_ref: Mapping[str, Any],
    plan_version: str,
    *,
    epoch: str,
    verified_paths: tuple[str, ...] = (),
) -> dict[str, Any]:
    result = copy.deepcopy(dict(ledger))
    by_path = {row["path"]: row for row in result["files"]}
    owner = {"plan_version": plan_version, "task_uid": task["task_uid"], "task_id": task["id"]}
    verified = {"build_variant_ids": [str(ref["path"]).split("build_")[-1].removesuffix(".json") for ref in build_refs], "evidence_ref": dict(evidence_ref)}
    paths = set(changed) | set(verified_paths)
    for relative in sorted(paths, key=lambda value: value.encode("utf-8")):
        content = changed.get(relative)
        row = by_path.get(relative)
        if row is None or row.get("class") != "s6_owned" or (content is None and row.get("state") != "realized"):
            raise S6ExecutionError(f"accepted file is not an S6-owned ledger row: {relative}")
        history = row.get("owner_history", [])
        if not history or history[-1] != owner:
            history = [*history, owner]
        by_path[relative] = {
            "path": relative, "class": "s6_owned", "state": "realized", "created_in_epoch": row.get("created_in_epoch", epoch),
            "content_sha256": sha256_bytes(content) if content is not None else row["content_sha256"], "last_commit_sha": commit_sha,
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


def _state_after_migration_success(
    state: Mapping[str, Any], task_id: str, commit_sha: str,
    evidence_ref: Mapping[str, Any], mode: str, notes: str,
) -> dict[str, Any]:
    event_name = "revalidation_passed" if mode == "revalidate" else "amendment_succeeded"
    return project_state_transition(
        state,
        {
            "schema_version": "2.0", "event": event_name, "task_id": task_id,
            "commit_sha": commit_sha, "evidence_ref": dict(evidence_ref), "notes": notes,
        },
    )


def _joint_file_ledger_after(
    ledger: Mapping[str, Any],
    tasks_by_path: Mapping[str, Mapping[str, Any]],
    changed: Mapping[str, bytes],
    commit_sha: str,
    build_refs: Sequence[Mapping[str, Any]],
    evidence_by_uid: Mapping[str, Mapping[str, Any]],
    plan_version: str,
    epoch: str,
) -> dict[str, Any]:
    result = copy.deepcopy(dict(ledger))
    by_path = {row["path"]: row for row in result["files"]}
    variants = [str(ref["path"]).split("build_")[-1].removesuffix(".json") for ref in build_refs]
    for relative, content in changed.items():
        row = by_path.get(relative)
        owner_task = tasks_by_path.get(relative)
        if not isinstance(row, Mapping) or row.get("class") != "s6_owned" or not isinstance(owner_task, Mapping):
            raise S6ExecutionError(f"accepted joint file is not an owned S6 row: {relative}")
        owner_uid = str(owner_task["task_uid"])
        evidence_ref = evidence_by_uid.get(owner_uid)
        if evidence_ref is None:
            raise S6ExecutionError(f"joint file has no owner evidence: {relative}")
        verified = {"build_variant_ids": variants, "evidence_ref": dict(evidence_ref)}
        updated = copy.deepcopy(dict(row))
        owner = {"plan_version": plan_version, "task_uid": owner_uid, "task_id": str(owner_task["id"])}
        history = updated.get("owner_history", [])
        if not isinstance(history, list) or not history:
            history = [owner]
        elif history[-1] != owner:
            history = [*history, owner]
        updated = {
            "path": relative, "class": "s6_owned", "state": "realized", "created_in_epoch": str(row.get("created_in_epoch", epoch)),
            "content_sha256": sha256_bytes(content), "last_commit_sha": commit_sha, "verified_by": verified, "owner_history": history,
        }
        by_path[relative] = updated
    result["files"] = [by_path[path] for path in sorted(by_path, key=lambda value: value.encode("utf-8"))]
    validate_file_ledger(result)
    return result


def _state_after_lease(
    state: Mapping[str, Any],
    *,
    current_task_id: str,
    member_task_ids: list[str],
    commit_sha: str,
    evidence_refs: list[Mapping[str, Any]],
    verification_id: str,
    workspace_tree: str,
    parent_sha: str,
) -> dict[str, Any]:
    current_index = member_task_ids.index(current_task_id)
    event = {
        "schema_version": "2.0", "event": "amended_under_lease", "task_id": current_task_id,
        "member_task_ids": list(member_task_ids), "member_evidence_refs": [dict(ref) for ref in evidence_refs],
        "commit_sha": commit_sha, "verification_id": verification_id,
        "proof": {"kind": "lease", "commit_sha": commit_sha, "verification_id": verification_id, "workspace_tree": workspace_tree, "parent_sha": parent_sha, "member_uids": [str(next(row["task_uid"] for row in state.get("tasks", []) if row.get("id") == member_id)) for member_id in member_task_ids], "member_evidence_refs": [dict(ref) for ref in evidence_refs]},
        "evidence_ref": dict(evidence_refs[current_index]),
    }
    return project_state_transition(state, event)


class S6ExecutionController:
    """Run ordinary tasks serially with durable candidate and commit boundaries."""

    def __init__(self, agent: AgentInvoker, executor: Any | None = None, *, fault_hook: Any | None = None, lease_authorization_provider: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None, revision_patch_provider: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None) -> None:
        self.agent = agent
        self.executor = executor
        self.fault_hook = fault_hook
        self.lease_authorization_provider = lease_authorization_provider
        self.revision_patch_provider = revision_patch_provider

    def _fault(self, point: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(point)

    @staticmethod
    def _require_s6_call_capacity(store: RunStore, task_id: str) -> None:
        """Fail controllably before allocating any new Agent call."""

        state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
        row = next((item for item in state["tasks"] if item.get("id") == task_id), None)
        if isinstance(row, Mapping) and row.get("status") == "in_progress":
            return
        run = store.load_run()
        cap = run.get("config_snapshot", {}).get("budgets", {}).get("s6_total_attempts_cap")
        if not isinstance(cap, int) or isinstance(cap, bool) or int(state.get("s6_attempts_used", 0)) >= cap:
            raise ControlledStageFailure({
                "code": "EXECUTION_UNRESOLVED",
                "detail": "S6 total attempt cap is exhausted before task allocation",
            })

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
    def _finish_revalidation(store: RunStore, task_uid: str, evidence_seq: int, evidence_ref: Mapping[str, Any]) -> None:
        path = f"validations/{task_uid}/validation_{evidence_seq:03d}.json"
        record = _load(store, path, "s6-validation.schema.json")
        if record.get("status") == "succeeded":
            if record.get("evidence_ref") != evidence_ref:
                raise S6ExecutionError("revalidation record has conflicting terminal facts")
            return
        evidence = _load(store, str(evidence_ref["path"]), "task-evidence.schema.json")
        record.update({
            "status": "succeeded", "evidence_ref": dict(evidence_ref), "failure_ref": None,
            "build_result_refs": list(evidence["build_result_refs"]),
            "smoke_result_refs": list(evidence["smoke_result_refs"]),
        })
        store.replace_json(path, record, schema_name="s6-validation.schema.json")

    @staticmethod
    def _finish_amendment(
        store: RunStore,
        task_uid: str,
        *,
        status: str,
        output_ref: Mapping[str, Any] | None = None,
        failure_ref: Mapping[str, Any] | None = None,
    ) -> None:
        path = f"attempts/{task_uid}/amendment.json"
        record = _load(store, path, "s6-attempt.schema.json")
        if record.get("status") in {"failed", "succeeded"}:
            if record.get("status") != status or record.get("output_ref") != output_ref or record.get("failure_ref") != failure_ref:
                raise S6ExecutionError("S6 amendment record has conflicting terminal facts")
            return
        record.update({
            "status": status,
            "output_ref": dict(output_ref) if output_ref is not None else None,
            "failure_ref": dict(failure_ref) if failure_ref is not None else None,
        })
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
            store.replace_plan_state(state, event_type="attempts_exhausted", event=event)
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

    @staticmethod
    def _finish_failed_lease(store: RunStore, lease_id: str | None, reason: str, call_refs: Sequence[Mapping[str, Any]] | None = None) -> None:
        if lease_id is None:
            return
        revision = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
        updated = append_lease_finished(
            revision,
            lease_id=lease_id,
            success=False,
            reason=reason,
            joint_evidence_ref=None,
            commit_sha=None,
            call_refs=call_refs or [],
        )
        if updated != revision:
            store.replace_json("plan/revision_ledger.json", updated, schema_name="revision-ledger.schema.json")

    def _publish_lease_success(
        self,
        context: StageContext,
        plan: Mapping[str, Any],
        task: Mapping[str, Any],
        state: Mapping[str, Any],
        allocation: Mapping[str, Any],
        authorization: Mapping[str, Any],
        lease_tasks: Mapping[str, Mapping[str, Any]],
        files: Mapping[str, bytes],
        changed: Mapping[str, bytes],
        candidate_tree: str,
        candidate_refs: Mapping[str, ArtifactRef],
        build_refs: Sequence[Mapping[str, Any]],
        smoke_refs: Sequence[Mapping[str, Any]],
        parsed: Mapping[str, Any],
        baseline_commit: str,
    ) -> None:
        store = context.store
        workspace = store._confined("workspace")
        attempt = int(allocation["attempt"]["attempt"])
        lease_record = allocation["attempt"].get("lease")
        if not isinstance(lease_record, Mapping):
            raise S6ExecutionError("lease success is missing its persisted lease binding")
        lease_id = str(lease_record["lease_id"])
        members = [task, *lease_tasks.values()]
        members = sorted(members, key=lambda item: str(item["task_uid"]).encode("utf-8"))
        member_uids = [str(item["task_uid"]) for item in members]
        member_ids = [str(item["id"]) for item in members]
        if len(member_uids) != len(set(member_uids)) or task["task_uid"] not in member_uids:
            raise S6ExecutionError("lease member set is not unique")

        # Allocate every member's evidence sequence before publishing any evidence.
        evidence_state = copy.deepcopy(dict(state))
        sequences: dict[str, int] = {}
        for member in members:
            uid = str(member["task_uid"])
            if uid == str(task["task_uid"]):
                # The current member's sequence was consumed by the ordinary
                # Fixer allocation; only lending members receive a new
                # evidence sequence at the joint-validation boundary.
                sequence = int(allocation["attempt"]["evidence_seq"])
            else:
                sequence = int(evidence_state.get("evidence_counters", {}).get(uid, 0)) + 1
                evidence_state["evidence_counters"][uid] = sequence
            sequences[uid] = sequence
        owner_by_path = {path: str(task["task_uid"]) for path in task.get("deliverable_files", [])}
        for lender in lease_tasks.values():
            for path in lender.get("deliverable_files", []):
                owner_by_path[path] = str(lender["task_uid"])
        changed_by_uid: dict[str, list[dict[str, str]]] = {uid: [] for uid in member_uids}
        for path, content in sorted(changed.items(), key=lambda item: item[0].encode("utf-8")):
            owner_uid = owner_by_path.get(path)
            if owner_uid not in changed_by_uid:
                raise S6ExecutionError(f"lease changed path has no participating owner: {path}")
            changed_by_uid[owner_uid].append({"path": path, "sha256": sha256_bytes(content)})
        common = {
            "schema_version": "2.0", "plan_ref": dict(evidence_state["plan_ref"]), "plan_version": evidence_state["plan_ref"]["version"], "epoch": evidence_state["plan_ref"]["epoch"],
            "binding_ref": dict(context.run["stages"]["s5"]["output_refs"]["binding_receipt"]),
            "input_refs": {key: {"path": path, "sha256": context.run["inputs"][key]["sha256"]} for key, path in (("spec", "spec/spec.json"), ("target_profile", "inputs/target.json"), ("test_bundle", "inputs/test_bundle.json"))},
            "plan_sha256": _hash(plan), "workspace_tree": candidate_tree, "build_result_refs": build_refs, "smoke_result_refs": smoke_refs, "test_summary_refs": [], "accepted": True, "migration_ref": None,
            "execution_kind": "lease", "amendment_used": 0, "lease_id": lease_id, "lease_authorization_ref": dict(lease_record["authorization_ref"]), "lease_member_uids": member_uids,
        }
        evidence_refs: dict[str, dict[str, str]] = {}
        evidence_values: dict[str, dict[str, Any]] = {}
        for member in members:
            uid = str(member["task_uid"])
            evidence = {**common, "task_uid": uid, "task_id": member["id"], "evidence_seq": sequences[uid], "attempt": int(next(row["attempts"] for row in evidence_state["tasks"] if row["id"] == member["id"]) ), "changed_files": changed_by_uid[uid]}
            evidence_values[uid] = evidence
            evidence_path = f"test_results/task_evidence/{uid}/evidence_{sequences[uid]:03d}.json"
            evidence_refs[uid] = ArtifactRef(evidence_path, sha256_bytes(canonical_json_bytes(evidence))).as_dict()
        verification_id = f"v-{member_uids[0]}-{sequences[member_uids[0]]}"
        joint = {
            "schema_version": "2.0", "verification_id": verification_id, "kind": "lease", "plan_ref": dict(evidence_state["plan_ref"]), "plan_sha256": _hash(plan), "workspace_tree": candidate_tree,
            "parent_sha": baseline_commit,
            "members": [{"task_uid": str(member["task_uid"]), "task_id": str(member["id"]), "evidence_seq": sequences[str(member["task_uid"])], "task_evidence_ref": evidence_refs[str(member["task_uid"])], "changed_files": [{**item, "owner_uid": str(member["task_uid"])} for item in changed_by_uid[str(member["task_uid"])] ]} for member in members],
        }
        joint_path = f"test_results/task_evidence/joint/{verification_id}.json"
        joint_ref = ArtifactRef(joint_path, sha256_bytes(canonical_json_bytes(joint)))
        prepared = prepare_joint_commit(workspace, changed, verification_id=verification_id, joint_evidence_sha256=joint_ref.sha256)
        old_file = _load(store, "plan/file_ledger.json", "file-ledger.schema.json")
        old_revision = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
        new_revision = append_verification_committed(old_revision, commit_sha=prepared["commit_sha"], revision_seq=evidence_state["plan_ref"]["revision_seq"], kind="lease", members=[{"task_uid": uid, "evidence_ref": ref} for uid, ref in sorted(evidence_refs.items(), key=lambda item: item[0].encode("utf-8"))], lease_id=lease_id, joint_evidence_ref=joint_ref.as_dict())
        new_revision = append_lease_finished(new_revision, lease_id=lease_id, success=True, reason=None, joint_evidence_ref=joint_ref.as_dict(), commit_sha=prepared["commit_sha"], call_refs=_call_refs_for_attempt(store, task["id"], attempt))
        evidence_by_uid = {uid: ref for uid, ref in evidence_refs.items()}
        task_by_path = {path: member for member in members for path in member.get("deliverable_files", [])}
        new_file = _joint_file_ledger_after(
            old_file,
            task_by_path,
            changed,
            prepared["commit_sha"],
            build_refs,
            evidence_by_uid,
            evidence_state["plan_ref"]["version"],
            evidence_state["plan_ref"]["epoch"],
        )
        new_state = _state_after_lease(evidence_state, current_task_id=task["id"], member_task_ids=member_ids, commit_sha=prepared["commit_sha"], evidence_refs=[evidence_refs[uid] for uid in member_uids], verification_id=verification_id, workspace_tree=candidate_tree, parent_sha=baseline_commit)
        wal = {
            "schema_version": "2.0", "kind": "lease", "task_id": task["id"], "task_uid": task["task_uid"], "attempt": attempt, "evidence_seq": sequences[str(task["task_uid"])],
            "old_state": state, "allocated_state": evidence_state, "new_state": new_state, "old_file_ledger": old_file, "new_file_ledger": new_file, "old_revision_ledger": old_revision, "new_revision_ledger": new_revision,
            "evidence_ref": evidence_refs[str(task["task_uid"])], "candidate_ref": next(iter(candidate_refs.values())).as_dict(), "candidate_file_refs": {path: ref.as_dict() for path, ref in candidate_refs.items()}, "changed_files": [{"path": path, "sha256": sha256_bytes(content)} for path, content in sorted(changed.items(), key=lambda item: item[0].encode("utf-8"))], "build_result_refs": build_refs, "smoke_result_refs": smoke_refs, "notes": parsed.get("notes", ""),
            "expected_tree": candidate_tree, "expected_git_tree": prepared["tree_sha"], "expected_commit": prepared["commit_sha"], "commit_message": prepared["message"], "commit_timestamp": prepared["timestamp"], "expected_parent": baseline_commit, "expected_trailers": {"NePA-Verification-ID": verification_id, "NePA-Joint-Evidence-SHA256": joint_ref.sha256}, "phase": "prepared", "commit_sha": None,
            "lease_id": lease_id, "lease_authorization_ref": dict(lease_record["authorization_ref"]), "lease_start_event_ref": {"event_seq": next(entry["event_seq"] for entry in old_revision["entries"] if entry.get("event_type") == "lease_started" and entry.get("payload", {}).get("lease_id") == lease_id)}, "joint_evidence_ref": joint_ref.as_dict(), "member_uids": member_uids, "member_evidence": [{"task_uid": uid, "task_id": next(member["id"] for member in members if member["task_uid"] == uid), "evidence_seq": sequences[uid], "evidence_ref": evidence_refs[uid], "changed_files": changed_by_uid[uid]} for uid in member_uids],
        }
        store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
        self._fault("s6_wal_prepared")
        store.replace_plan_state(evidence_state, event_type="evidence_sequences_allocated", event={"lease_id": lease_id, "member_sequences": sequences})
        self._fault("s6_evidence_sequences_allocated")
        for uid in member_uids:
            published = store.publish_immutable_json(evidence_refs[uid]["path"], evidence_values[uid], schema_name="task-evidence.schema.json")
            if published.as_dict() != evidence_refs[uid]:
                raise S6ExecutionError("published member evidence disagrees with its WAL reference")
        published_joint = store.publish_immutable_json(joint_ref.path, joint, schema_name="joint-evidence.schema.json")
        if published_joint != joint_ref:
            raise S6ExecutionError("published Joint Evidence disagrees with its WAL reference")
        self._fault("s6_joint_evidence_published")
        for path, content in changed.items():
            target = workspace / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        self._fault("s6_candidate_installed")
        wal["phase"] = "candidate_installed"
        store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
        commit = publish_joint_commit(workspace, changed.keys(), prepared)
        wal.update({"phase": "committed", "commit_sha": commit["commit_sha"]})
        store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
        self._fault("s6_commit_created")
        self.reconcile_verification_wal(store)

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
        if epoch.get("materialization_status") not in {"ready", "pending_repair"}:
            raise S6AdmissionError("S6 requires a ready or registered pending-repair epoch")
        active = _load(store, "plan/active_plan.json", "active-plan.schema.json")
        plan = _load(store, active["path"], "plan.schema.json")
        constraints = _load(store, "plan/_s4/delivery_constraints.json")
        ledger = _load(store, "plan/file_ledger.json", "file-ledger.schema.json")
        revision = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
        manifest = _load(store, "plan/artifact_manifest.json", "artifact-manifest.schema.json")
        contract_map = _load(store, "plan/contract_map.json", "contract-map.schema.json")
        immutable_manifest_path = f"plan/bindings/{active['version']}/artifact_manifest.json"
        immutable_contract_map_path = f"plan/bindings/{active['version']}/contract_map.json"
        immutable_manifest = _load(store, immutable_manifest_path, "artifact-manifest.schema.json")
        immutable_contract_map = _load(store, immutable_contract_map_path, "contract-map.schema.json")
        validate_file_ledger(ledger)
        validate_revision_ledger(revision)
        activation = latest_activation(revision)
        if active.get("revision_seq", 0) == 0:
            if activation is not None:
                raise S6AdmissionError("initial Plan cannot have a revision activation")
        elif (
            not isinstance(activation, Mapping)
            or activation.get("revision_seq") != active.get("revision_seq")
            or activation.get("to_plan_ref") != {"path": active["path"], "sha256": active["sha256"]}
            or activation.get("to_version") != active.get("version")
            or activation.get("epoch_after") != active.get("epoch")
        ):
            raise S6AdmissionError("latest activation does not bind the active Plan")
        if binding.get("plan_ref") != {"path": active["path"], "sha256": active["sha256"]}:
            raise S6AdmissionError("current binding does not reference the active Plan")
        if binding.get("epoch_receipt_ref") != output["epoch_receipt"]:
            raise S6AdmissionError("current binding does not reference the accepted epoch receipt")
        if active.get("sha256") != store._json_artifact_hash(active["path"]):
            raise S6AdmissionError("active Plan pointer hash is drifted")
        if manifest != immutable_manifest or contract_map != immutable_contract_map:
            raise S6AdmissionError("current manifest/map copies drifted from immutable bindings")
        if binding.get("manifest_ref") != {"path": immutable_manifest_path, "sha256": _hash(immutable_manifest)} or binding.get("contract_map_ref") != {"path": immutable_contract_map_path, "sha256": _hash(immutable_contract_map)}:
            raise S6AdmissionError("current binding does not reference the immutable manifest/map")
        if _git(store._confined("workspace"), "rev-parse", f"{epoch['checkpoint_commit']}^{{tree}}") != epoch["checkpoint_tree"]:
            raise S6AdmissionError("epoch checkpoint tree is not stable")
        try:
            spec = _load(store, "spec/spec.json")
            target = _load(store, "inputs/target.json")
            derived_constraints = compile_delivery_constraints(spec, target)
            blueprint = compile_delivery_blueprint(derived_constraints, plan["architecture"], plan["work_packages"], blueprint_task_semantic_projection(plan["tasks"]))
        except Exception as exc:
            raise S6AdmissionError(f"frozen delivery inputs cannot be recomputed: {exc}") from exc
        if derived_constraints != constraints or plan.get("delivery_blueprint_sha256") != _hash(blueprint):
            raise S6AdmissionError("current delivery inputs do not recompute")
        if active.get("revision_seq") == 0 and _hash(blueprint) != run["stages"]["s4"]["output_refs"]["delivery_blueprint_sha256"]:
            raise S6AdmissionError("initial sealed Delivery Blueprint binding drifted")
        active_plan_ref = {"path": active["path"], "sha256": active["sha256"]}
        if epoch.get("materialized_plan_ref") == active_plan_ref and epoch.get("blueprint_sha256") != _hash(blueprint):
            raise S6AdmissionError("epoch receipt Blueprint binding drifted")
        if epoch.get("materialized_plan_ref") != active_plan_ref and (
            not isinstance(activation, Mapping)
            or activation.get("level") != "F2"
            or activation.get("epoch_after") != epoch.get("epoch")
            or binding.get("epoch_receipt_ref") != output["epoch_receipt"]
        ):
            raise S6AdmissionError("only the latest same-epoch F2 activation may reuse an older epoch receipt")
        _validate_contract_map(plan, immutable_contract_map)
        for ref in epoch.get("build_result_refs", []) + epoch.get("smoke_result_refs", []):
            schema = "smoke-result.schema.json" if "/smoke/" in str(ref.get("path", "")) else "build-result.schema.json"
            store.verify_ref(ref, schema_name=schema)
            result = _load(store, ref["path"], schema)
            if epoch.get("materialization_status") == "ready" and result.get("status") != "passed":
                raise S6AdmissionError("ready epoch receipt contains a failed validation result")
        workspace = store._confined("workspace")
        if not workspace.is_dir() or not _clean(workspace):
            raise S6AdmissionError("S6 workspace must be clean at admission")
        head = _git(workspace, "rev-parse", "HEAD")
        state_path = store._confined("plan/plan_state.json")
        if not state_path.exists() and (active.get("revision_seq") != 0 or epoch.get("epoch") != "E0" or epoch.get("materialization_status") != "ready" or head != epoch["checkpoint_commit"]):
            raise S6AdmissionError("Plan State is missing outside the unique fresh ready-E0 baseline")
        if state_path.exists() and not _commit_descends(workspace, head, epoch["checkpoint_commit"]):
            raise S6AdmissionError("S6 workspace is not descended from its accepted epoch checkpoint")
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
            if not store._confined("plan/state_history.json").exists():
                store.append_state_history(state, event_type="state_initialized")
        else:
            state = initialize_plan_state(plan, plan_ref=active)
            store.replace_plan_state(state, event_type="state_initialized")
        if epoch.get("materialization_status") == "pending_repair":
            activation_groups = (
                activation.get("migration", {}).get("pending_groups", [])
                if isinstance(activation, Mapping)
                else []
            )
            accepted_group_ids = sorted(
                (str(group["group_id"]) for group in activation_groups),
                key=lambda value: value.encode("utf-8"),
            )
            receipt_group_ids = sorted(
                (str(group_id) for group_id in epoch.get("pending_group_ids", [])),
                key=lambda value: value.encode("utf-8"),
            )
            state_group_ids = sorted(
                {
                    str(row["group_id"])
                    for row in state["tasks"]
                    if row.get("status") in {"pending", "in_progress"} and row.get("group_id") is not None
                },
                key=lambda value: value.encode("utf-8"),
            )
            verified_groups = {
                str(entry.get("payload", {}).get("group_id")): str(entry.get("payload", {}).get("commit_sha"))
                for entry in revision.get("entries", [])
                if isinstance(entry, Mapping)
                and entry.get("event_type") == "verification_committed"
                and isinstance(entry.get("payload"), Mapping)
                and entry["payload"].get("kind") == "group"
            }
            resolved_group_ids = set(receipt_group_ids) - set(state_group_ids)
            if (
                not receipt_group_ids
                or receipt_group_ids != accepted_group_ids
                or not set(state_group_ids) <= set(receipt_group_ids)
                or not resolved_group_ids <= set(verified_groups)
                or any(not _commit_descends(workspace, head, verified_groups[group_id]) for group_id in resolved_group_ids)
            ):
                raise S6AdmissionError("pending-repair groups disagree across activation, epoch receipt and State")
        return run, plan, active, blueprint, constraints, epoch

    def reconcile_verification_wal(self, store: RunStore) -> None:
        path = store._confined("plan/verification_pending.json")
        if not path.exists():
            return
        wal = _load(store, "plan/verification_pending.json", "verification-pending.schema.json")
        _validate_wal_snapshots(wal)
        if wal.get("kind") == "lease":
            self._reconcile_lease_wal(store, wal)
            return
        if wal.get("kind") == "group":
            self._reconcile_group_wal(store, wal)
            return
        workspace = store._confined("workspace")
        head = _git(workspace, "rev-parse", "HEAD")
        if wal["phase"] != "committed":
            if head == wal["expected_parent"]:
                allowed = {item["path"] for item in wal.get("changed_files", [])}
                dirty = _status_paths(workspace)
                if not dirty.issubset(allowed):
                    raise S6ExecutionError("pre-commit verification WAL found unrelated workspace changes")
                candidate_files: dict[str, bytes] = {}
                for relative in sorted(allowed, key=lambda value: value.encode("utf-8")):
                    ref = wal.get("candidate_file_refs", {}).get(relative)
                    if not isinstance(ref, Mapping):
                        raise S6ExecutionError("pre-commit WAL is missing a candidate file reference")
                    store.verify_ref(ref)
                    candidate_files[relative] = store._confined(ref["path"]).read_bytes()
                for relative in dirty:
                    if (workspace / relative).read_bytes() != candidate_files[relative]:
                        raise S6ExecutionError("pre-commit workspace bytes disagree with the candidate")
                self._publish_wal_task_evidence(store, wal)
                for relative, data in candidate_files.items():
                    target = workspace / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                prepared = {
                    "commit_sha": wal["expected_commit"], "tree_sha": wal["expected_git_tree"],
                    "parent_sha": wal["expected_parent"], "message": wal["commit_message"],
                    "timestamp": wal["commit_timestamp"],
                }
                commit = publish_task_commit(workspace, candidate_files, prepared, allow_empty=not candidate_files)
                wal = {**dict(wal), "phase": "committed", "commit_sha": commit["commit_sha"]}
                store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
            if not self._legal_task_commit(workspace, wal, head):
                head = _git(workspace, "rev-parse", "HEAD")
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
            store.replace_plan_state(wal["new_state"], event_type="verification_committed", event={"kind": wal["kind"], "commit_sha": wal.get("commit_sha")})
            self._fault("s6_state_projection_published")
        if current_file != wal["new_file_ledger"]:
            store.replace_json("plan/file_ledger.json", wal["new_file_ledger"], schema_name="file-ledger.schema.json")
            self._fault("s6_file_ledger_published")
        if current_revision != wal["new_revision_ledger"]:
            store.replace_json("plan/revision_ledger.json", wal["new_revision_ledger"], schema_name="revision-ledger.schema.json")
            self._fault("s6_event_published")
        evidence = _load(store, wal["evidence_ref"]["path"], "task-evidence.schema.json")
        if evidence["execution_kind"] == "revalidate":
            self._finish_revalidation(store, wal["task_uid"], int(wal["evidence_seq"]), wal["evidence_ref"])
        elif evidence["execution_kind"] == "amend":
            self._finish_amendment(store, wal["task_uid"], status="succeeded", output_ref=wal["evidence_ref"])
        else:
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
    def _publish_wal_task_evidence(store: RunStore, wal: Mapping[str, Any]) -> None:
        ref = wal.get("evidence_ref")
        if not isinstance(ref, Mapping):
            raise S6ExecutionError("verification WAL is missing its Task Evidence reference")
        target = store._confined(str(ref["path"]))
        if target.is_file():
            store.verify_ref(ref, schema_name="task-evidence.schema.json")
            return
        state = wal["old_state"]
        row = next((item for item in state["tasks"] if item["id"] == wal["task_id"]), None)
        if not isinstance(row, Mapping):
            raise S6ExecutionError("verification WAL task is absent from its allocated State")
        run = store.load_run()
        plan = store._read_json_artifact(state["plan_ref"]["path"], schema_name="plan.schema.json")
        execution_kind = "revalidate" if int(wal["attempt"]) == 0 else ("amend" if row.get("execution_mode") == "amend" else "normal")
        evidence = {
            "schema_version": "2.0", "task_uid": wal["task_uid"], "task_id": wal["task_id"],
            "evidence_seq": wal["evidence_seq"], "execution_kind": execution_kind,
            "attempt": wal["attempt"], "amendment_used": 1 if execution_kind == "amend" else 0,
            "plan_ref": copy.deepcopy(state["plan_ref"]), "plan_version": state["plan_ref"]["version"],
            "epoch": state["plan_ref"]["epoch"],
            "binding_ref": copy.deepcopy(run["stages"]["s5"]["output_refs"]["binding_receipt"]),
            "input_refs": {key: {"path": path, "sha256": run["inputs"][key]["sha256"]} for key, path in (("spec", "spec/spec.json"), ("target_profile", "inputs/target.json"), ("test_bundle", "inputs/test_bundle.json"))},
            "plan_sha256": _hash(plan), "workspace_tree": wal["expected_tree"],
            "build_result_refs": copy.deepcopy(wal["build_result_refs"]),
            "smoke_result_refs": copy.deepcopy(wal["smoke_result_refs"]), "test_summary_refs": [],
            "changed_files": copy.deepcopy(wal["changed_files"]), "accepted": True,
            "migration_ref": copy.deepcopy(row.get("migration_ref")),
        }
        if _hash(evidence) != ref.get("sha256"):
            raise S6ExecutionError("verification WAL cannot reconstruct its exact Task Evidence")
        published = store.publish_immutable_json(str(ref["path"]), evidence, schema_name="task-evidence.schema.json")
        if published.as_dict() != dict(ref):
            raise S6ExecutionError("reconstructed Task Evidence disagrees with the WAL")

    def _reconcile_lease_wal(self, store: RunStore, wal: Mapping[str, Any]) -> None:
        """Reconcile a lease WAL without replaying provider or validation work."""
        workspace = store._confined("workspace")
        head = _git(workspace, "rev-parse", "HEAD")
        path = store._confined("plan/verification_pending.json")
        if wal["phase"] != "committed" and head == wal["expected_parent"]:
            allowed = {item["path"] for item in wal.get("changed_files", [])}
            dirty = _status_paths(workspace)
            if not dirty.issubset(allowed):
                raise S6ExecutionError("pre-commit lease WAL found unrelated workspace changes")
            if dirty:
                selected = sorted(dirty, key=lambda value: value.encode("utf-8"))
                _git(workspace, "restore", "--source", wal["expected_parent"], "--staged", "--worktree", "--", *selected)
            current_state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
            if current_state == wal["old_state"]:
                store.replace_plan_state(wal["allocated_state"], event_type="evidence_sequences_allocated", event={"lease_id": wal["lease_id"]})
            elif current_state != wal["allocated_state"]:
                raise S6ExecutionError("pre-commit lease WAL State has conflicting facts")
            orphan_root = store._confined(f"attempts/{wal['task_uid']}/attempt_{int(wal['attempt']):03d}/orphan_evidence")
            orphan_root.mkdir(parents=True, exist_ok=True)
            refs = [member.get("evidence_ref") for member in wal.get("member_evidence", [])]
            refs.append(wal.get("joint_evidence_ref"))
            for index, ref in enumerate(refs):
                if not isinstance(ref, Mapping) or not isinstance(ref.get("path"), str):
                    continue
                source = store._confined(ref["path"])
                if source.is_file():
                    source.replace(orphan_root / f"{index:03d}-{source.name}")
            revision = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
            failed = append_lease_finished(revision, lease_id=str(wal["lease_id"]), success=False, reason="PRE_COMMIT_INTERRUPTED", joint_evidence_ref=None, commit_sha=None, call_refs=[])
            if failed != revision:
                store.replace_json("plan/revision_ledger.json", failed, schema_name="revision-ledger.schema.json")
            path.unlink()
            self._fault("s6_wal_removed")
            return
        commit_sha = wal.get("commit_sha")
        if not isinstance(commit_sha, str) or not self._legal_task_commit(workspace, wal, commit_sha):
            raise S6ExecutionError("committed lease WAL does not match the legal joint commit")
        joint_ref = wal.get("joint_evidence_ref")
        if not isinstance(joint_ref, Mapping):
            raise S6ExecutionError("lease WAL is missing Joint Evidence")
        store.verify_ref(joint_ref, schema_name="joint-evidence.schema.json")
        for member in wal.get("member_evidence", []):
            store.verify_ref(member["evidence_ref"], schema_name="task-evidence.schema.json")
        for ref in wal.get("build_result_refs", []):
            store.verify_ref(ref, schema_name="build-result.schema.json")
        for ref in wal.get("smoke_result_refs", []):
            store.verify_ref(ref, schema_name="smoke-result.schema.json")
        member_rows = wal.get("member_evidence", [])
        member_uids = [str(item["task_uid"]) for item in member_rows]
        verification = [entry.get("payload", {}) for entry in wal["new_revision_ledger"].get("entries", []) if entry.get("event_type") == "verification_committed" and entry.get("payload", {}).get("verification_id") == joint_ref.get("path", "").rsplit("/", 1)[-1].removesuffix(".json")]
        finishes = [entry.get("payload", {}) for entry in wal["new_revision_ledger"].get("entries", []) if entry.get("event_type") == "lease_finished" and entry.get("payload", {}).get("lease_id") == wal["lease_id"]]
        if len(verification) != 1 or verification[0].get("lease_id") != wal["lease_id"] or verification[0].get("joint_evidence_ref") != joint_ref or verification[0].get("member_uids") != member_uids or verification[0].get("evidence_refs") != [item["evidence_ref"] for item in member_rows]:
            raise S6ExecutionError("lease WAL verification projection is not derived from its member evidence")
        if len(finishes) != 1 or not finishes[0].get("success") or finishes[0].get("joint_evidence_ref") != joint_ref or finishes[0].get("commit_sha") != commit_sha:
            raise S6ExecutionError("lease WAL finish projection is not derived from the legal joint commit")
        allocated_rows = {row["task_uid"]: row for row in wal["allocated_state"].get("tasks", [])}
        projected_rows = {row["task_uid"]: row for row in wal["new_state"].get("tasks", [])}
        evidence_by_uid = {str(item["task_uid"]): item["evidence_ref"] for item in member_rows}
        for uid in member_uids:
            old_row = allocated_rows.get(uid, {})
            new_row = projected_rows.get(uid, {})
            if new_row.get("status") != "done" or new_row.get("commit_sha") != commit_sha or new_row.get("acceptance_evidence", {}).get("task_evidence_ref") != evidence_by_uid[uid] or new_row.get("attempts") != old_row.get("attempts"):
                raise S6ExecutionError("lease WAL State projection disagrees with its member proof")
        projected_files = {row.get("path"): row for row in wal["new_file_ledger"].get("files", [])}
        for changed in wal.get("changed_files", []):
            row = projected_files.get(changed["path"], {})
            if row.get("last_commit_sha") != commit_sha or row.get("content_sha256") != changed["sha256"]:
                raise S6ExecutionError("lease WAL file projection disagrees with the legal joint commit")
        current_state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
        current_file = _load(store, "plan/file_ledger.json", "file-ledger.schema.json")
        current_revision = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
        if current_state not in (wal["old_state"], wal["allocated_state"], wal["new_state"]) or current_file not in (wal["old_file_ledger"], wal["new_file_ledger"]) or current_revision not in (wal["old_revision_ledger"], wal["new_revision_ledger"]):
            raise S6ExecutionError("lease WAL contains conflicting accepted facts")
        if current_state != wal["new_state"]:
            store.replace_plan_state(wal["new_state"], event_type="verification_committed", event={"kind": "lease", "lease_id": wal.get("lease_id"), "commit_sha": wal.get("commit_sha")})
            self._fault("s6_state_projection_published")
        if current_file != wal["new_file_ledger"]:
            store.replace_json("plan/file_ledger.json", wal["new_file_ledger"], schema_name="file-ledger.schema.json")
            self._fault("s6_file_ledger_published")
        if current_revision != wal["new_revision_ledger"]:
            store.replace_json("plan/revision_ledger.json", wal["new_revision_ledger"], schema_name="revision-ledger.schema.json")
            self._fault("s6_event_published")
        self._finish_attempt(store, str(wal["task_uid"]), int(wal["attempt"]), status="succeeded", output_ref=wal["evidence_ref"])
        self._fault("s6_attempt_terminal_published")
        path.unlink()
        self._fault("s6_wal_removed")

    def _reconcile_group_wal(self, store: RunStore, wal: Mapping[str, Any]) -> None:
        workspace = store._confined("workspace")
        path = store._confined("plan/verification_pending.json")
        active = _load(store, "plan/active_plan.json", "active-plan.schema.json")
        state = wal["allocated_state"]
        if state.get("plan_ref") != active:
            raise S6ExecutionError("group WAL is not bound to the current active Plan")
        activation_entries = [
            entry
            for entry in wal["old_revision_ledger"].get("entries", [])
            if entry.get("event_type") == "revision_activated"
        ]
        if not activation_entries:
            raise S6ExecutionError("group WAL has no accepted activation")
        activation_entry = activation_entries[-1]
        activation = activation_entry.get("payload", {})
        if (
            activation.get("revision_seq") != active["revision_seq"]
            or wal.get("activation_ref") != {"event_seq": activation_entry.get("event_seq")}
        ):
            raise S6ExecutionError("group WAL activation reference is not current")
        groups = [
            item
            for item in activation.get("migration", {}).get("pending_groups", [])
            if isinstance(item, Mapping) and item.get("group_id") == wal.get("group_id")
        ]
        if len(groups) != 1 or groups[0].get("member_task_uids") != wal.get("member_uids"):
            raise S6ExecutionError("group WAL membership is not frozen by the current activation")
        rows = {str(row["task_uid"]): row for row in state.get("tasks", [])}
        modes = wal.get("member_modes", [])
        if [item.get("task_uid") for item in modes] != wal.get("member_uids"):
            raise S6ExecutionError("group WAL member modes do not cover the exact group")
        for item in modes:
            row = rows.get(str(item["task_uid"]), {})
            if (
                row.get("group_id") != wal.get("group_id")
                or row.get("execution_mode") != item.get("execution_mode")
                or row.get("migration_ref") != item.get("migration_ref")
                or state.get("evidence_counters", {}).get(item["task_uid"]) != item.get("evidence_seq")
                or item.get("migration_ref", {}).get("event_seq") != activation_entry.get("event_seq")
                or item.get("migration_ref", {}).get("revision_seq") != active["revision_seq"]
            ):
                raise S6ExecutionError("group WAL member allocation disagrees with activation State")
        epoch = _load(store, f"plan/epochs/{active['epoch']}/receipt.json", "epoch-receipt.schema.json")
        if (
            wal.get("epoch_checkpoint_commit") != epoch.get("checkpoint_commit")
            or wal.get("epoch_checkpoint_tree") != epoch.get("checkpoint_tree")
            or _git(workspace, "rev-parse", f"{wal['epoch_checkpoint_commit']}^{{tree}}") != wal.get("epoch_checkpoint_tree")
            or wal.get("expected_parent") != wal.get("baseline_commit")
            or not _commit_descends(workspace, str(wal.get("baseline_commit")), str(wal.get("epoch_checkpoint_commit")))
        ):
            raise S6ExecutionError("group WAL epoch anchor or transaction baseline is invalid")
        for ref in wal.get("build_result_refs", []):
            store.verify_ref(ref, schema_name="build-result.schema.json")
            if _load(store, ref["path"], "build-result.schema.json").get("status") != "passed":
                raise S6ExecutionError("group WAL references a failed build result")
        for ref in wal.get("smoke_result_refs", []):
            store.verify_ref(ref, schema_name="smoke-result.schema.json")
            if _load(store, ref["path"], "smoke-result.schema.json").get("status") != "passed":
                raise S6ExecutionError("group WAL references a failed smoke result")
        head = _git(workspace, "rev-parse", "HEAD")
        changed_paths = {item["path"] for item in wal.get("changed_files", [])}
        candidate_files: dict[str, bytes] = {}
        for relative in sorted(changed_paths, key=lambda value: value.encode("utf-8")):
            ref = wal.get("candidate_file_refs", {}).get(relative)
            if not isinstance(ref, Mapping):
                raise S6ExecutionError("group WAL changed file has no persisted candidate")
            store.verify_ref(ref)
            data = store._confined(ref["path"]).read_bytes()
            expected = next(item["sha256"] for item in wal["changed_files"] if item["path"] == relative)
            if sha256_bytes(data) != expected:
                raise S6ExecutionError("group WAL candidate bytes drifted")
            candidate_files[relative] = data
        if wal["phase"] != "committed" and head == wal["expected_parent"]:
            dirty = _status_paths(workspace)
            if not dirty.issubset(changed_paths):
                raise S6ExecutionError("pre-commit group WAL found unrelated workspace changes")
            for relative in dirty:
                if (workspace / relative).read_bytes() != candidate_files[relative]:
                    raise S6ExecutionError("pre-commit group workspace bytes disagree with persisted candidates")
            self._publish_group_wal_evidence(store, wal)
            for relative, data in candidate_files.items():
                target = workspace / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            prepared = {
                "commit_sha": wal["expected_commit"], "tree_sha": wal["expected_git_tree"],
                "parent_sha": wal["expected_parent"], "message": wal["commit_message"], "timestamp": wal["commit_timestamp"],
            }
            commit = publish_joint_commit(workspace, candidate_files, prepared, allow_empty=not candidate_files)
            wal = {**dict(wal), "phase": "committed", "commit_sha": commit["commit_sha"]}
            store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
            head = commit["commit_sha"]
        if not self._legal_task_commit(workspace, wal, head):
            raise S6ExecutionError("group WAL does not match its legal joint commit")
        self._publish_group_wal_evidence(store, wal)
        current_state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
        current_file = _load(store, "plan/file_ledger.json", "file-ledger.schema.json")
        current_revision = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
        if current_state not in (wal["allocated_state"], wal["new_state"]) or current_file not in (wal["old_file_ledger"], wal["new_file_ledger"]) or current_revision not in (wal["old_revision_ledger"], wal["new_revision_ledger"]):
            raise S6ExecutionError("group WAL found partial or conflicting accepted projections")
        if current_state != wal["new_state"]:
            store.replace_plan_state(wal["new_state"], event_type="verification_committed", event={"kind": "group", "group_id": wal["group_id"], "commit_sha": head})
            self._fault("s6_group_state_published")
        if current_file != wal["new_file_ledger"]:
            store.replace_json("plan/file_ledger.json", wal["new_file_ledger"], schema_name="file-ledger.schema.json")
            self._fault("s6_group_file_ledger_published")
        if current_revision != wal["new_revision_ledger"]:
            store.replace_json("plan/revision_ledger.json", wal["new_revision_ledger"], schema_name="revision-ledger.schema.json")
            self._fault("s6_group_event_published")
        modes = {item["task_uid"]: item for item in wal["member_modes"]}
        for member in wal["member_evidence"]:
            mode = modes[member["task_uid"]]["execution_mode"]
            if mode == "revalidate":
                self._finish_revalidation(store, member["task_uid"], int(member["evidence_seq"]), member["evidence_ref"])
            elif mode == "amend":
                self._finish_amendment(store, member["task_uid"], status="succeeded", output_ref=member["evidence_ref"])
            else:
                row = next(item for item in wal["new_state"]["tasks"] if item["task_uid"] == member["task_uid"])
                self._finish_attempt(store, member["task_uid"], int(row["attempts"]), status="succeeded", output_ref=member["evidence_ref"])
        self._fault("s6_group_terminals_published")
        path.unlink()
        self._fault("s6_wal_removed")

    @staticmethod
    def _publish_group_wal_evidence(store: RunStore, wal: Mapping[str, Any]) -> None:
        run = store.load_run()
        state = wal["allocated_state"]
        plan = store._read_json_artifact(state["plan_ref"]["path"], schema_name="plan.schema.json")
        modes = {item["task_uid"]: item["execution_mode"] for item in wal["member_modes"]}
        joint_members: list[dict[str, Any]] = []
        for member in wal["member_evidence"]:
            ref = member["evidence_ref"]
            row = next(item for item in state["tasks"] if item["task_uid"] == member["task_uid"])
            evidence = {
                "schema_version": "2.0", "task_uid": member["task_uid"], "task_id": member["task_id"],
                "evidence_seq": member["evidence_seq"], "execution_kind": "group",
                "attempt": 0 if modes[member["task_uid"]] == "revalidate" else row["attempts"],
                "amendment_used": row["amendment_used"], "plan_ref": copy.deepcopy(state["plan_ref"]),
                "plan_version": state["plan_ref"]["version"], "epoch": state["plan_ref"]["epoch"],
                "binding_ref": copy.deepcopy(run["stages"]["s5"]["output_refs"]["binding_receipt"]),
                "input_refs": {key: {"path": path, "sha256": run["inputs"][key]["sha256"]} for key, path in (("spec", "spec/spec.json"), ("target_profile", "inputs/target.json"), ("test_bundle", "inputs/test_bundle.json"))},
                "plan_sha256": _hash(plan), "workspace_tree": wal["expected_tree"],
                "build_result_refs": copy.deepcopy(wal["build_result_refs"]), "smoke_result_refs": copy.deepcopy(wal["smoke_result_refs"]),
                "test_summary_refs": [], "group_id": wal["group_id"], "group_member_uids": list(wal["member_uids"]),
                "changed_files": copy.deepcopy(member["changed_files"]), "accepted": True,
                "migration_ref": copy.deepcopy(row["migration_ref"]),
            }
            if _hash(evidence) != ref["sha256"]:
                raise S6ExecutionError("group WAL cannot reconstruct exact member evidence")
            store.publish_immutable_json(ref["path"], evidence, schema_name="task-evidence.schema.json")
            joint_members.append({
                "task_uid": member["task_uid"], "task_id": member["task_id"], "evidence_seq": member["evidence_seq"],
                "task_evidence_ref": ref,
                "changed_files": [{**item, "owner_uid": member["task_uid"]} for item in member["changed_files"]],
                "execution_kind": modes[member["task_uid"]], "migration_ref": copy.deepcopy(row["migration_ref"]),
            })
        joint_ref = wal["joint_evidence_ref"]
        joint = {
            "schema_version": "2.0", "verification_id": wal["expected_trailers"]["NePA-Verification-ID"],
            "kind": "group", "plan_ref": copy.deepcopy(state["plan_ref"]), "plan_sha256": _hash(plan),
            "workspace_tree": wal["expected_tree"], "parent_sha": wal["expected_parent"],
            "group_id": wal["group_id"], "members": joint_members,
        }
        if _hash(joint) != joint_ref["sha256"]:
            raise S6ExecutionError("group WAL cannot reconstruct exact Joint Evidence")
        store.publish_immutable_json(joint_ref["path"], joint, schema_name="joint-evidence.schema.json")

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
        state_task = next((row for row in old_state["tasks"] if row["id"] == wal["task_id"]), None)
        plan = store._read_json_artifact(old_state["plan_ref"]["path"], schema_name="plan.schema.json")
        task = next((row for row in plan["tasks"] if row["id"] == wal["task_id"]), None)
        if not isinstance(state_task, Mapping) or not isinstance(task, Mapping):
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
        new_file = _file_ledger_after(wal["old_file_ledger"], task, changed, commit_sha, evidence["build_result_refs"], wal["evidence_ref"], old_state["plan_ref"]["version"], epoch=old_state["plan_ref"]["epoch"], verified_paths=tuple(task.get("deliverable_files", [])) if evidence.get("execution_kind") == "revalidate" else ())
        new_revision = append_verification_committed(wal["old_revision_ledger"], task_uid=wal["task_uid"], evidence_ref=wal["evidence_ref"], commit_sha=commit_sha, revision_seq=old_state["plan_ref"]["revision_seq"])
        if evidence.get("execution_kind") in {"revalidate", "amend"}:
            new_state = _state_after_migration_success(old_state, wal["task_id"], commit_sha, wal["evidence_ref"], str(evidence["execution_kind"]), str(wal.get("notes", "")))
        else:
            new_state = _state_after_success(old_state, wal["task_id"], commit_sha, wal["evidence_ref"], new_file, new_revision, str(wal.get("notes", "")))
        if new_state != wal.get("new_state") or new_file != wal.get("new_file_ledger") or new_revision != wal.get("new_revision_ledger"):
            raise RunStoreError("verification WAL projected snapshots are incomplete or conflicting")
        result = copy.deepcopy(dict(wal))
        result.update({"commit_sha": commit_sha, "phase": "committed"})
        return result

    def _choose(
        self,
        plan: Mapping[str, Any],
        state: Mapping[str, Any],
        *,
        pending_group_ids: list[str] | None = None,
        allowed_task_ids: set[str] | None = None,
    ) -> Mapping[str, Any] | None:
        rows = {row["id"]: row for row in state["tasks"]}
        tasks = sorted(
            plan.get("tasks", []),
            key=lambda value: (
                0 if rows[value["id"]].get("group_id") is not None else 1,
                0 if rows[value["id"]].get("migration_ref") is not None else 1,
                value["id"].encode("utf-8"),
            ),
        )
        required_groups = set(pending_group_ids or [])
        for task in tasks:
            row = rows[task["id"]]
            if allowed_task_ids is not None and task["id"] not in allowed_task_ids:
                continue
            if row["status"] not in {"pending", "in_progress"}:
                continue
            if required_groups and row.get("group_id") not in required_groups:
                continue
            dependencies = task.get("depends_on", [])
            if all(rows.get(dependency, {}).get("status") == "done" for dependency in dependencies):
                return task
        return None

    @staticmethod
    def _independent_after_group_failure(
        task: Mapping[str, Any],
        plan: Mapping[str, Any],
        state: Mapping[str, Any],
        blueprint: Mapping[str, Any],
        activation: Mapping[str, Any] | None,
    ) -> bool:
        """Conservatively prove DAG, contract, build, and smoke independence."""

        failed_rows = [
            row for row in state.get("tasks", [])
            if row.get("group_id") is not None and row.get("status") in {"blocked", "blocked_by_dependency"}
        ]
        if not failed_rows:
            return True
        failed_ids = {str(row["id"]) for row in failed_rows}
        tasks = {str(row["id"]): row for row in plan.get("tasks", [])}
        dependencies = list(task.get("depends_on", []))
        seen: set[str] = set()
        while dependencies:
            dependency = str(dependencies.pop())
            if dependency in seen:
                continue
            seen.add(dependency)
            if dependency in failed_ids:
                return False
            dependencies.extend(tasks.get(dependency, {}).get("depends_on", []))
        failed_contracts = {
            str(value)
            for task_id in failed_ids for value in [
                *tasks.get(task_id, {}).get("provides_contracts", []),
                *tasks.get(task_id, {}).get("consumes_contracts", []),
            ]
        }
        task_contracts = {str(value) for value in [*task.get("provides_contracts", []), *task.get("consumes_contracts", [])]}
        if failed_contracts & task_contracts:
            return False
        failed_groups = {str(row["group_id"]) for row in failed_rows}
        groups = activation.get("migration", {}).get("pending_groups", []) if isinstance(activation, Mapping) else []
        failed_artifacts = {
            str(artifact_id)
            for group in groups if isinstance(group, Mapping) and group.get("group_id") in failed_groups
            for artifact_id in group.get("build_artifact_ids", [])
        }
        variants = set(task.get("acceptance", {}).get("build_variant_ids", []))
        task_artifacts = {
            str(artifact.get("id"))
            for artifact in blueprint.get("build_artifacts", []) if isinstance(artifact, Mapping)
            and variants & set(artifact.get("build_variant_ids", []))
        }
        return bool(task_artifacts) and not bool(task_artifacts & failed_artifacts)

    def _group_descriptor(
        self,
        store: RunStore,
        plan: Mapping[str, Any],
        blueprint: Mapping[str, Any],
        state: Mapping[str, Any],
        epoch: Mapping[str, Any],
        group_id: str,
    ) -> dict[str, Any]:
        revision = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
        activation = latest_activation(revision)
        groups = activation.get("migration", {}).get("pending_groups", []) if isinstance(activation, Mapping) else []
        matches = [item for item in groups if isinstance(item, Mapping) and item.get("group_id") == group_id]
        if len(matches) != 1:
            raise S6AdmissionError("repair group is not uniquely frozen by the accepted activation")
        frozen = copy.deepcopy(dict(matches[0]))
        member_uids = list(frozen.get("member_task_uids", []))
        if not member_uids or member_uids != sorted(set(member_uids), key=lambda value: str(value).encode("utf-8")):
            raise S6AdmissionError("repair group must contain a canonical non-empty member set")
        tasks_by_uid = {str(item["task_uid"]): item for item in plan.get("tasks", []) if isinstance(item, Mapping)}
        rows_by_uid = {str(item["task_uid"]): item for item in state.get("tasks", []) if isinstance(item, Mapping)}
        if any(uid not in tasks_by_uid or uid not in rows_by_uid for uid in member_uids):
            raise S6AdmissionError("repair group references a task absent from current Plan State")
        if any(rows_by_uid[uid].get("group_id") != group_id or rows_by_uid[uid].get("migration_ref") is None or rows_by_uid[uid].get("status") not in {"pending", "in_progress"} for uid in member_uids):
            raise S6AdmissionError("repair group members are not exact unresolved migration rows")
        member_ids = {str(tasks_by_uid[uid]["id"]) for uid in member_uids}
        ordered: list[Mapping[str, Any]] = []
        remaining = set(member_ids)
        while remaining:
            ready = sorted(
                (task for task in tasks_by_uid.values() if task["id"] in remaining and not (set(task.get("depends_on", [])) & remaining)),
                key=lambda item: str(item["id"]).encode("utf-8"),
            )
            if not ready:
                raise S6AdmissionError("repair group Plan subgraph is cyclic")
            for task in ready:
                remaining.remove(str(task["id"]))
                ordered.append(task)
        external_dependencies = sorted(
            {dependency for task in ordered for dependency in task.get("depends_on", []) if dependency not in member_ids},
            key=lambda value: str(value).encode("utf-8"),
        )
        rows_by_id = {str(item["id"]): item for item in state["tasks"]}
        if any(rows_by_id.get(dependency, {}).get("status") != "done" for dependency in external_dependencies):
            raise S6AdmissionError("repair group has an unready external dependency")
        owned_paths = {path for task in ordered for path in task.get("deliverable_files", [])}
        if not set(frozen.get("affected_paths", [])) <= owned_paths:
            raise S6AdmissionError("repair group affected paths are not owned by its members")
        build_ids = {str(item.get("id")) for item in blueprint.get("build_artifacts", []) if isinstance(item, Mapping)}
        if not set(frozen.get("build_artifact_ids", [])) <= build_ids:
            raise S6AdmissionError("repair group references an unknown build artifact")
        contract_map = _load(store, "plan/contract_map.json", "contract-map.schema.json")
        known_symbols = {str(export.get("symbol")) for contract in contract_map.get("contracts", []) if isinstance(contract, Mapping) for export in contract.get("exports", []) if isinstance(export, Mapping)}
        if not set(frozen.get("affected_symbols", [])) <= known_symbols:
            raise S6AdmissionError("repair group references an unknown contract symbol")
        workspace = store._confined("workspace")
        epoch_checkpoint_commit = str(epoch["checkpoint_commit"])
        baseline_commit = _git(workspace, "rev-parse", "HEAD")
        if not _commit_descends(workspace, baseline_commit, epoch_checkpoint_commit):
            raise S6AdmissionError("repair group workspace is not descended from its epoch baseline")
        activation_ref = copy.deepcopy(rows_by_uid[member_uids[0]]["migration_ref"])
        if any(rows_by_uid[uid].get("migration_ref") != activation_ref for uid in member_uids):
            raise S6AdmissionError("repair group members disagree on activation lineage")
        return {
            "group_id": group_id, "activation_ref": activation_ref,
            "member_uids": member_uids, "members": [copy.deepcopy(dict(task)) for task in ordered],
            "affected_paths": list(frozen.get("affected_paths", [])),
            "affected_symbols": list(frozen.get("affected_symbols", [])),
            "build_artifact_ids": list(frozen.get("build_artifact_ids", [])),
            "external_dependencies": external_dependencies,
            "epoch_checkpoint_commit": epoch_checkpoint_commit,
            "epoch_checkpoint_tree": str(epoch["checkpoint_tree"]),
            "baseline_commit": baseline_commit,
            "baseline_tree": _tree_sha256(workspace),
        }

    def _run_group(
        self,
        context: StageContext,
        plan: Mapping[str, Any],
        blueprint: Mapping[str, Any],
        constraints: Mapping[str, Any],
        epoch: Mapping[str, Any],
        group_id: str,
    ) -> None:
        store = context.store
        workspace = store._confined("workspace")
        old_state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
        descriptor = self._group_descriptor(store, plan, blueprint, old_state, epoch, group_id)
        call_members = [task for task in descriptor["members"] if next(row for row in old_state["tasks"] if row["id"] == task["id"])["execution_mode"] != "revalidate"]
        run = store.load_run()
        cap = run["config_snapshot"]["budgets"]["s6_total_attempts_cap"]
        remaining = int(cap) - int(old_state.get("s6_attempts_used", 0))
        required = sum(next(row for row in old_state["tasks"] if row["id"] == task["id"])["status"] != "in_progress" for task in call_members)
        if remaining < required:
            raise ControlledStageFailure({
                "code": "EXECUTION_UNRESOLVED",
                "detail": "S6 total attempt cap cannot allocate the pending repair group",
            })
        if _git(workspace, "rev-parse", "HEAD") != descriptor["baseline_commit"]:
            raise S6ExecutionError("repair group must start from its accepted epoch checkpoint")
        cumulative: dict[str, bytes] = {}
        candidate_refs: list[dict[str, str]] = []
        failure_refs: list[dict[str, str]] = []
        empty_members: set[str] = set()
        allocations: dict[str, Mapping[str, Any]] = {}
        modes: dict[str, str] = {}
        contract_map = _load(store, "plan/contract_map.json", "contract-map.schema.json")
        affected_paths = set(descriptor["affected_paths"])
        for task in descriptor["members"]:
            state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
            row = next(item for item in state["tasks"] if item["id"] == task["id"])
            mode = str(row["execution_mode"])
            modes[str(task["task_uid"])] = mode
            writable_paths = set(task.get("deliverable_files", [])) & affected_paths
            if mode == "revalidate":
                allocation = store.allocate_s6_migration(
                    task_id=task["id"], task_uid=task["task_uid"], mode="revalidate",
                    baseline_commit=descriptor["baseline_commit"], baseline_tree=descriptor["baseline_tree"],
                )
                allocations[str(task["task_uid"])] = allocation
                continue
            if not writable_paths:
                raise S6AdmissionError("non-revalidation group member has no frozen writable path")
            if mode == "amend":
                self._require_s6_call_capacity(store, str(task["id"]))
                allocation = store.allocate_s6_migration(
                    task_id=task["id"], task_uid=task["task_uid"], mode="amend",
                    baseline_commit=descriptor["baseline_commit"], baseline_tree=descriptor["baseline_tree"],
                )
                role, tier = "fixer", "T1"
                call_attempt = max(1, int(row["attempts"]))
            else:
                replaying = False
                if row.get("status") == "in_progress" and int(row["attempts"]) > 0:
                    record_path = f"attempts/{task['task_uid']}/attempt_{int(row['attempts']):03d}.json"
                    record = _load(store, record_path, "s6-attempt.schema.json")
                    replaying = record.get("status") == "started"
                call_attempt = int(row["attempts"]) if replaying else int(row["attempts"]) + 1
                role = "coder" if call_attempt == 1 else "fixer"
                tier = "T2" if call_attempt <= 3 else "T1"
                self._require_s6_call_capacity(store, str(task["id"]))
                allocation = store.allocate_s6_attempt(
                    task_id=task["id"], task_uid=task["task_uid"], role=role, tier=tier,
                    baseline_commit=descriptor["baseline_commit"], baseline_tree=descriptor["baseline_tree"],
                )
            allocations[str(task["task_uid"])] = allocation
            self._fault(f"s6_group_member_allocated:{task['task_uid']}")
            storage_uid = f"group/{group_id}/{task['task_uid']}"
            accumulated_workspace = _candidate_workspace(workspace, store, f"group/{group_id}/accumulated", 1, cumulative)
            files = _candidate_from_manifest(store, storage_uid, call_attempt, task, writable_paths)
            if files is not None:
                files = {path: data for path, data in files.items() if not (accumulated_workspace / path).is_file() or (accumulated_workspace / path).read_bytes() != data}
                if not files:
                    empty_members.add(str(task["task_uid"]))
                    failure = {
                        "attempt": call_attempt, "code": "GROUP_EMPTY_CANDIDATE",
                        "detail": "group candidate contains no actual byte changes", "candidate": {},
                        "feedback": {}, "diagnosis": {"group_id": group_id},
                    }
                    failure_ref = _failure_ref(store, str(task["task_uid"]), call_attempt, failure)
                    failure_refs.append(failure_ref.as_dict())
                    if mode == "normal":
                        self._finish_attempt(store, str(task["task_uid"]), call_attempt, status="failed", failure_ref=failure_ref.as_dict())
                    continue
                refs = _write_candidate(store, storage_uid, call_attempt, files)
                combined = {**cumulative, **files}
                candidate = _candidate_workspace(workspace, store, f"group/{group_id}/combined", 1, combined)
                task_files = _task_files(candidate, list(task.get("deliverable_files", [])))
                _validate_candidate_bindings(
                    {path: content.encode("utf-8") for path, content in task_files.items()},
                    task, plan, blueprint, contract_map,
                    written_paths=set(files), allowed_paths=writable_paths,
                )
                cumulative = combined
                candidate_refs.extend(ref.as_dict() for ref in refs.values())
                self._fault(f"s6_group_candidate_persisted:{task['task_uid']}")
                continue
            package = _work_package(plan, task)
            execution_mode = "amend" if mode == "amend" else "normal"
            inputs, _accounting = project_s6_context(
                task=_public_task(task), work_package=package,
                architecture=_architecture_projection(plan, task, package),
                spec_slice=_spec_slice(store._read_json_artifact("spec/spec.json"), task, package),
                contract_map=_contract_projection(contract_map, task),
                interface_files=_interface_files(accumulated_workspace, plan, task),
                language_guidance=constraints.get("advisory", {}),
                current_files=_task_files(accumulated_workspace, sorted(writable_paths, key=lambda value: value.encode("utf-8"))),
                execution_mode=execution_mode,
                failed_candidate={} if role == "fixer" else None,
                validation_feedback={"group_id": group_id, "migration_ref": row["migration_ref"]} if role == "fixer" else None,
                diagnosis={"reason": "frozen repair group"} if role == "fixer" else None,
                max_tokens=int(context.run["config_snapshot"]["budgets"]["coder_context_max_tokens"]),
            )
            if context.orchestrator is not None:
                context.orchestrator.admit_external_call(store)
            schema, example = coding_contract()
            input_names = CODER_INPUTS if role == "coder" else FIXER_INPUTS
            result = self.agent.invoke(
                role=role, inputs={key: inputs[key] for key in input_names}, output_schema=schema,
                output_example=example, run_id=context.run["run_id"], stage="S6", task_id=task["id"],
                attempt=call_attempt, use_cache=False, tier_override=tier, allow_structured_repair=False,
            )
            parsed = result.parsed
            _model_output_ref(store, storage_uid, call_attempt, str(getattr(result.response, "text", "")))
            _archive_candidate_response(store, storage_uid, call_attempt, parsed)
            files = normalize_candidate(parsed, task, _load(store, "plan/file_ledger.json", "file-ledger.schema.json"), allowed_paths=writable_paths)
            files = {path: data for path, data in files.items() if not (accumulated_workspace / path).is_file() or (accumulated_workspace / path).read_bytes() != data}
            if not files:
                empty_members.add(str(task["task_uid"]))
                failure = {
                    "attempt": call_attempt, "code": "GROUP_EMPTY_CANDIDATE",
                    "detail": "group candidate contains no actual byte changes", "candidate": {},
                    "feedback": {}, "diagnosis": {"group_id": group_id},
                }
                failure_ref = _failure_ref(store, str(task["task_uid"]), call_attempt, failure)
                failure_refs.append(failure_ref.as_dict())
                if mode == "normal":
                    self._finish_attempt(store, str(task["task_uid"]), call_attempt, status="failed", failure_ref=failure_ref.as_dict())
                continue
            refs = _write_candidate(store, storage_uid, call_attempt, files)
            candidate_refs.extend(ref.as_dict() for ref in refs.values())
            combined = {**cumulative, **files}
            candidate = _candidate_workspace(workspace, store, f"group/{group_id}/combined", 1, combined)
            task_files = _task_files(candidate, list(task.get("deliverable_files", [])))
            _validate_candidate_bindings(
                {path: content.encode("utf-8") for path, content in task_files.items()},
                task, plan, blueprint, contract_map,
                written_paths=set(files), allowed_paths=writable_paths,
            )
            cumulative = combined
            self._fault(f"s6_group_candidate_persisted:{task['task_uid']}")
        candidate = _candidate_workspace(workspace, store, f"group/{group_id}/validated", 1, cumulative)
        executor = self.executor or SandboxExecutor(
            context.run["config_snapshot"]["sandbox"]["image"],
            context.run["config_snapshot"]["sandbox"]["cpu"],
            context.run["config_snapshot"]["sandbox"]["mem_gb"],
        )
        round_number = 1
        while True:
            builds = run_build_variants(executor, candidate, blueprint, constraints, fail_fast=False)
            smokes = run_smoke_checks(
                executor, candidate, blueprint, builds,
                context.run["config_snapshot"]["smoke"]["dwell_seconds"],
                context.run["config_snapshot"]["smoke"]["term_grace_seconds"], fail_fast=False,
            ) if builds and all(item["status"] == "passed" for item in builds) else []
            build_refs, smoke_refs = _publish_results(store, f"groups/{group_id}/round_{round_number:03d}", builds, smokes)
            self._fault(f"s6_group_results_published:{round_number}")
            passed = not empty_members and bool(builds) and bool(smokes) and all(item["status"] == "passed" for item in [*builds, *smokes])
            if passed:
                break
            implicated = set(empty_members) or _attribute_group_members(builds, descriptor, blueprint, contract_map)
            if not implicated or not builds or any(item.get("timed_out") for item in builds) or (smokes and any(item.get("status") != "passed" for item in smokes)):
                implicated = {str(task["task_uid"]) for task in descriptor["members"]}
            state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
            limit = int(context.run["config_snapshot"]["budgets"]["task_fix_attempts"]) + 1
            retry_tasks = [
                task for task in descriptor["members"]
                if str(task["task_uid"]) in implicated
                and modes[str(task["task_uid"])] == "normal"
                and next(row["attempts"] for row in state["tasks"] if row["id"] == task["id"]) < limit
            ]
            if not retry_tasks:
                self._exhaust_group(
                    store, plan, descriptor, allocations, build_refs, smoke_refs,
                    candidate_refs=candidate_refs, failure_refs=failure_refs,
                )
                return
            for task in retry_tasks:
                uid = str(task["task_uid"])
                current = allocations[uid]["attempt"]
                if uid not in empty_members:
                    failure = {
                        "attempt": current["attempt"], "code": "GROUP_CANDIDATE_VALIDATION_FAILED",
                        "detail": "group build or smoke failed", "candidate": {},
                        "feedback": {"build": builds, "smoke": smokes}, "diagnosis": {"group_id": group_id},
                    }
                    failure_ref = _failure_ref(store, uid, int(current["attempt"]), failure)
                    failure_refs.append(failure_ref.as_dict())
                    self._finish_attempt(store, uid, int(current["attempt"]), status="failed", failure_ref=failure_ref.as_dict())
                allocation, files, refs = self._retry_group_member(
                    context, plan, blueprint, constraints, descriptor, task, cumulative, contract_map,
                )
                allocations[uid] = allocation
                if files:
                    cumulative.update(files)
                    candidate_refs.extend(ref.as_dict() for ref in refs.values())
                    empty_members.discard(uid)
                else:
                    next_attempt = int(allocation["attempt"]["attempt"])
                    failure = {
                        "attempt": next_attempt, "code": "GROUP_EMPTY_CANDIDATE",
                        "detail": "group retry contains no actual byte changes", "candidate": {},
                        "feedback": {}, "diagnosis": {"group_id": group_id},
                    }
                    failure_ref = _failure_ref(store, uid, next_attempt, failure)
                    failure_refs.append(failure_ref.as_dict())
                    self._finish_attempt(store, uid, next_attempt, status="failed", failure_ref=failure_ref.as_dict())
                    empty_members.add(uid)
            round_number += 1
            candidate = _candidate_workspace(workspace, store, f"group/{group_id}/validated", round_number, cumulative)
        self._publish_group_success(
            context, plan, descriptor, old_state, allocations, modes, cumulative,
            candidate_refs, failure_refs, _tree_sha256(candidate), build_refs, smoke_refs,
        )

    def _retry_group_member(
        self,
        context: StageContext,
        plan: Mapping[str, Any],
        blueprint: Mapping[str, Any],
        constraints: Mapping[str, Any],
        descriptor: Mapping[str, Any],
        task: Mapping[str, Any],
        cumulative: Mapping[str, bytes],
        contract_map: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], dict[str, bytes], dict[str, ArtifactRef]]:
        store = context.store
        workspace = store._confined("workspace")
        state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
        row = next(item for item in state["tasks"] if item["id"] == task["id"])
        writable_paths = set(task.get("deliverable_files", [])) & set(descriptor["affected_paths"])
        if not writable_paths:
            raise S6AdmissionError("group retry has no frozen writable path")
        attempt = int(row["attempts"]) + 1
        tier = "T2" if attempt <= 3 else "T1"
        self._require_s6_call_capacity(store, str(task["id"]))
        allocation = store.allocate_s6_attempt(
            task_id=task["id"], task_uid=task["task_uid"], role="fixer", tier=tier,
            baseline_commit=descriptor["baseline_commit"], baseline_tree=descriptor["baseline_tree"],
        )
        candidate_workspace = _candidate_workspace(workspace, store, f"group/{descriptor['group_id']}/retry_context", attempt, cumulative)
        previous = _find_previous_failure(store, task["task_uid"], attempt)
        if not isinstance(previous, Mapping):
            raise S6ExecutionError("group Fixer retry has no persisted preceding failure")
        package = _work_package(plan, task)
        inputs, _accounting = project_s6_context(
            task=_public_task(task), work_package=package,
            architecture=_architecture_projection(plan, task, package),
            spec_slice=_spec_slice(store._read_json_artifact("spec/spec.json"), task, package),
            contract_map=_contract_projection(contract_map, task),
            interface_files=_interface_files(candidate_workspace, plan, task),
            language_guidance=constraints.get("advisory", {}),
            current_files=_task_files(candidate_workspace, sorted(writable_paths, key=lambda value: value.encode("utf-8"))),
            execution_mode="normal", failed_candidate=previous.get("candidate", {}),
            validation_feedback=previous.get("feedback", {}), diagnosis=previous.get("diagnosis", {}),
            max_tokens=int(context.run["config_snapshot"]["budgets"]["coder_context_max_tokens"]),
        )
        if context.orchestrator is not None:
            context.orchestrator.admit_external_call(store)
        schema, example = coding_contract()
        result = self.agent.invoke(
            role="fixer", inputs={key: inputs[key] for key in FIXER_INPUTS}, output_schema=schema,
            output_example=example, run_id=context.run["run_id"], stage="S6", task_id=task["id"],
            attempt=attempt, use_cache=False, tier_override=tier, allow_structured_repair=False,
        )
        parsed = result.parsed
        storage_uid = f"group/{descriptor['group_id']}/{task['task_uid']}"
        _model_output_ref(store, storage_uid, attempt, str(getattr(result.response, "text", "")))
        _archive_candidate_response(store, storage_uid, attempt, parsed)
        files = normalize_candidate(parsed, task, _load(store, "plan/file_ledger.json", "file-ledger.schema.json"), allowed_paths=writable_paths)
        files = {path: data for path, data in files.items() if not (candidate_workspace / path).is_file() or (candidate_workspace / path).read_bytes() != data}
        refs = _write_candidate(store, storage_uid, attempt, files)
        combined = {**cumulative, **files}
        candidate = _candidate_workspace(workspace, store, f"group/{descriptor['group_id']}/retry_candidate", attempt, combined)
        task_files = _task_files(candidate, list(task.get("deliverable_files", [])))
        _validate_candidate_bindings(
            {path: content.encode("utf-8") for path, content in task_files.items()},
            task, plan, blueprint, contract_map,
            written_paths=set(files), allowed_paths=writable_paths,
        )
        return allocation, files, refs

    def _exhaust_group(
        self,
        store: RunStore,
        plan: Mapping[str, Any],
        descriptor: Mapping[str, Any],
        allocations: Mapping[str, Mapping[str, Any]],
        build_refs: list[dict[str, str]],
        smoke_refs: list[dict[str, str]],
        *,
        candidate_refs: Sequence[Mapping[str, Any]],
        failure_refs: Sequence[Mapping[str, Any]],
    ) -> None:
        state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
        attempts = {
            str(task["task_uid"]): int(next(row for row in state["tasks"] if row["id"] == task["id"])["attempts"])
            for task in descriptor["members"]
        }
        failure_ref = store.publish_immutable_json(
            f"groups/{descriptor['group_id']}/failure.json",
            {
                "code": "GROUP_VALIDATION_EXHAUSTED",
                "baseline_commit": descriptor["baseline_commit"], "baseline_tree": descriptor["baseline_tree"],
                "member_attempts": attempts,
                "candidate_refs": [dict(ref) for ref in candidate_refs],
                "member_failure_refs": [dict(ref) for ref in failure_refs],
                "build_result_refs": build_refs, "smoke_result_refs": smoke_refs,
            },
        )
        members = sorted(descriptor["members"], key=lambda item: str(item["task_uid"]).encode("utf-8"))
        event = {
            "schema_version": "2.0", "event": "group_exhausted", "task_id": members[0]["id"],
            "group_id": descriptor["group_id"], "member_task_ids": [item["id"] for item in members],
            "error": failure_ref.path,
            "proof": {"group_id": descriptor["group_id"], "activation_ref": descriptor["activation_ref"], "member_uids": [item["task_uid"] for item in members]},
        }
        failed = project_state_transition(state, event, plan=plan, config_snapshot=store.load_run()["config_snapshot"])
        store.replace_plan_state(failed, event_type="group_exhausted", event=event)
        for task in members:
            allocation = allocations[str(task["task_uid"])]
            row = next(item for item in state["tasks"] if item["id"] == task["id"])
            if row["execution_mode"] == "revalidate":
                record = copy.deepcopy(dict(allocation["record"]))
                record.update({"status": "failed", "failure_ref": failure_ref.as_dict(), "build_result_refs": build_refs, "smoke_result_refs": smoke_refs})
                store.replace_json(allocation["record_ref"].path, record, schema_name="s6-validation.schema.json")
            elif row["execution_mode"] == "amend":
                self._finish_amendment(store, task["task_uid"], status="failed", failure_ref=failure_ref.as_dict())
            else:
                allocation = allocations[str(task["task_uid"])]
                attempt = int(allocation["attempt"]["attempt"])
                record = _load(store, f"attempts/{task['task_uid']}/attempt_{attempt:03d}.json", "s6-attempt.schema.json")
                if record.get("status") == "started":
                    self._finish_attempt(store, task["task_uid"], attempt, status="exhausted", failure_ref=failure_ref.as_dict())

    def _publish_group_success(
        self,
        context: StageContext,
        plan: Mapping[str, Any],
        descriptor: Mapping[str, Any],
        old_state: Mapping[str, Any],
        allocations: Mapping[str, Mapping[str, Any]],
        modes: Mapping[str, str],
        cumulative: Mapping[str, bytes],
        candidate_refs: list[dict[str, str]],
        failure_refs: list[dict[str, str]],
        candidate_tree: str,
        build_refs: list[dict[str, str]],
        smoke_refs: list[dict[str, str]],
    ) -> None:
        store = context.store
        workspace = store._confined("workspace")
        allocated_state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
        members = sorted(descriptor["members"], key=lambda item: str(item["task_uid"]).encode("utf-8"))
        member_uids = [str(task["task_uid"]) for task in members]
        changed = {path: data for path, data in cumulative.items() if not (workspace / path).is_file() or (workspace / path).read_bytes() != data}
        changed_by_uid: dict[str, list[dict[str, str]]] = {}
        member_evidence_values: list[dict[str, Any]] = []
        member_evidence: list[dict[str, Any]] = []
        for task in members:
            uid = str(task["task_uid"])
            allocation = allocations[uid]
            record = allocation.get("record") if isinstance(allocation.get("record"), Mapping) else allocation.get("attempt")
            if not isinstance(record, Mapping):
                raise S6ExecutionError("group allocation has no persisted member record")
            sequence = int(record["evidence_seq"])
            row = next(item for item in allocated_state["tasks"] if item["id"] == task["id"])
            member_changed = [
                {"path": path, "sha256": sha256_bytes(changed[path])}
                for path in sorted(set(task.get("deliverable_files", [])) & set(changed), key=lambda value: value.encode("utf-8"))
            ]
            if modes[uid] == "revalidate" and member_changed:
                raise S6ExecutionError("REVALIDATE group member cannot publish changed files")
            if modes[uid] != "revalidate" and not member_changed:
                raise S6ExecutionError("non-REVALIDATE group member has no actual byte change")
            changed_by_uid[uid] = member_changed
            evidence = {
                "schema_version": "2.0", "task_uid": uid, "task_id": task["id"], "evidence_seq": sequence,
                "execution_kind": "group", "attempt": 0 if modes[uid] == "revalidate" else int(row["attempts"]),
                "amendment_used": int(row["amendment_used"]), "plan_ref": copy.deepcopy(allocated_state["plan_ref"]),
                "plan_version": allocated_state["plan_ref"]["version"], "epoch": allocated_state["plan_ref"]["epoch"],
                "binding_ref": copy.deepcopy(context.run["stages"]["s5"]["output_refs"]["binding_receipt"]),
                "input_refs": {key: {"path": path, "sha256": context.run["inputs"][key]["sha256"]} for key, path in (("spec", "spec/spec.json"), ("target_profile", "inputs/target.json"), ("test_bundle", "inputs/test_bundle.json"))},
                "plan_sha256": _hash(plan), "workspace_tree": candidate_tree,
                "build_result_refs": build_refs, "smoke_result_refs": smoke_refs, "test_summary_refs": [],
                "group_id": descriptor["group_id"], "group_member_uids": member_uids,
                "changed_files": member_changed, "accepted": True,
                "migration_ref": copy.deepcopy(row["migration_ref"]),
            }
            evidence_path = f"test_results/task_evidence/{uid}/evidence_{sequence:03d}.json"
            evidence_ref = ArtifactRef(evidence_path, _hash(evidence))
            member_evidence_values.append(evidence)
            member_evidence.append({
                "task_uid": uid, "task_id": task["id"], "evidence_seq": sequence,
                "evidence_ref": evidence_ref.as_dict(), "changed_files": member_changed,
            })
        verification_id = f"v-{member_uids[0]}-{member_evidence[0]['evidence_seq']}"
        joint = {
            "schema_version": "2.0", "verification_id": verification_id, "kind": "group",
            "plan_ref": copy.deepcopy(allocated_state["plan_ref"]), "plan_sha256": _hash(plan),
            "workspace_tree": candidate_tree, "parent_sha": descriptor["baseline_commit"],
            "group_id": descriptor["group_id"],
            "members": [
                {
                    "task_uid": item["task_uid"], "task_id": item["task_id"], "evidence_seq": item["evidence_seq"],
                    "task_evidence_ref": item["evidence_ref"],
                    "changed_files": [{**changed_file, "owner_uid": item["task_uid"]} for changed_file in item["changed_files"]],
                    "execution_kind": modes[item["task_uid"]],
                    "migration_ref": copy.deepcopy(next(row["migration_ref"] for row in allocated_state["tasks"] if row["task_uid"] == item["task_uid"])),
                }
                for item in member_evidence
            ],
        }
        joint_path = f"test_results/joint_evidence/{verification_id}.json"
        joint_ref = ArtifactRef(joint_path, _hash(joint))
        prepared = prepare_joint_commit(
            workspace, changed, verification_id=verification_id,
            joint_evidence_sha256=joint_ref.sha256, allow_empty=not changed,
        )
        old_file = _load(store, "plan/file_ledger.json", "file-ledger.schema.json")
        old_revision = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
        new_file = copy.deepcopy(old_file)
        for task, item in zip(members, member_evidence, strict=True):
            task_changed = {row["path"]: changed[row["path"]] for row in item["changed_files"]}
            verified_paths = tuple(task.get("deliverable_files", [])) if modes[str(task["task_uid"])] == "revalidate" else ()
            new_file = _file_ledger_after(
                new_file, task, task_changed, prepared["commit_sha"], build_refs, item["evidence_ref"],
                allocated_state["plan_ref"]["version"], epoch=allocated_state["plan_ref"]["epoch"],
                verified_paths=verified_paths,
            )
        new_revision = append_verification_committed(
            old_revision, commit_sha=prepared["commit_sha"], revision_seq=allocated_state["plan_ref"]["revision_seq"],
            kind="group", members=[{"task_uid": item["task_uid"], "evidence_ref": item["evidence_ref"]} for item in member_evidence],
            group_id=str(descriptor["group_id"]), joint_evidence_ref=joint_ref.as_dict(),
        )
        event = {
            "schema_version": "2.0", "event": "group_verified", "task_id": members[0]["id"],
            "group_id": descriptor["group_id"], "member_task_ids": [item["id"] for item in members],
            "member_evidence_refs": [item["evidence_ref"] for item in member_evidence],
            "commit_sha": prepared["commit_sha"], "verification_id": verification_id,
            "evidence_ref": member_evidence[0]["evidence_ref"],
            "proof": {
                "kind": "group", "group_id": descriptor["group_id"], "activation_ref": descriptor["activation_ref"],
                "commit_sha": prepared["commit_sha"], "verification_id": verification_id,
                "workspace_tree": candidate_tree, "parent_sha": descriptor["baseline_commit"],
                "member_uids": member_uids, "member_evidence_refs": [item["evidence_ref"] for item in member_evidence],
            },
        }
        new_state = project_state_transition(allocated_state, event, plan=plan, config_snapshot=context.run["config_snapshot"])
        wal = {
            "schema_version": "2.0", "kind": "group", "old_state": old_state, "allocated_state": allocated_state,
            "new_state": new_state, "old_file_ledger": old_file, "new_file_ledger": new_file,
            "old_revision_ledger": old_revision, "new_revision_ledger": new_revision,
            "activation_ref": {"event_seq": descriptor["activation_ref"]["event_seq"]},
            "group_id": descriptor["group_id"],
            "epoch_checkpoint_commit": descriptor["epoch_checkpoint_commit"],
            "epoch_checkpoint_tree": descriptor["epoch_checkpoint_tree"],
            "baseline_commit": descriptor["baseline_commit"],
            "baseline_tree": descriptor["baseline_tree"], "joint_evidence_ref": joint_ref.as_dict(),
            "member_uids": member_uids, "member_evidence": member_evidence,
            "member_modes": [{"task_uid": uid, "execution_mode": modes[uid], "evidence_seq": next(item["evidence_seq"] for item in member_evidence if item["task_uid"] == uid), "migration_ref": copy.deepcopy(next(row["migration_ref"] for row in allocated_state["tasks"] if row["task_uid"] == uid))} for uid in member_uids],
            "candidate_refs": candidate_refs, "candidate_file_refs": {path: next(ref for ref in reversed(candidate_refs) if ref["path"].endswith(f"/candidate/{path}")) for path in changed},
            "failure_refs": failure_refs, "changed_files": [{"path": path, "sha256": sha256_bytes(data)} for path, data in sorted(changed.items(), key=lambda item: item[0].encode("utf-8"))],
            "build_result_refs": build_refs, "smoke_result_refs": smoke_refs, "notes": "frozen repair group verified",
            "expected_tree": candidate_tree, "expected_git_tree": prepared["tree_sha"], "expected_commit": prepared["commit_sha"],
            "commit_message": prepared["message"], "commit_timestamp": prepared["timestamp"], "expected_parent": descriptor["baseline_commit"],
            "expected_trailers": {"NePA-Verification-ID": verification_id, "NePA-Joint-Evidence-SHA256": joint_ref.sha256},
            "phase": "prepared", "commit_sha": None,
        }
        store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
        self._fault("s6_group_wal_prepared")
        for evidence, item in zip(member_evidence_values, member_evidence, strict=True):
            store.publish_immutable_json(item["evidence_ref"]["path"], evidence, schema_name="task-evidence.schema.json")
            self._fault(f"s6_group_member_evidence_published:{item['task_uid']}")
        store.publish_immutable_json(joint_path, joint, schema_name="joint-evidence.schema.json")
        self._fault("s6_group_joint_evidence_published")
        for path, data in changed.items():
            target = workspace / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        wal["phase"] = "candidate_installed"
        store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
        self._fault("s6_group_candidate_installed")
        commit = publish_joint_commit(workspace, changed, prepared, allow_empty=not changed)
        wal.update({"phase": "committed", "commit_sha": commit["commit_sha"]})
        store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
        self._fault("s6_group_commit_created")
        self.reconcile_verification_wal(store)

    def _propagate_dependency_blocks(self, store: RunStore, plan: Mapping[str, Any]) -> None:
        state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
        rows = {row["id"]: row for row in state["tasks"]}
        changed_any = False
        last_block: tuple[str, str] | None = None
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
                    last_block = (str(task["id"]), str(blocked[0]))
        if changed_any:
            if last_block is None:
                raise S6ExecutionError("dependency propagation lost its derived block")
            store.replace_plan_state(state, event_type="dependency_blocked", event={"task_id": last_block[0], "blocked_by": last_block[1]})

    def _run_revalidation(
        self,
        context: StageContext,
        plan: Mapping[str, Any],
        blueprint: Mapping[str, Any],
        constraints: Mapping[str, Any],
        task: Mapping[str, Any],
    ) -> None:
        store = context.store
        workspace = store._confined("workspace")
        baseline_commit = _git(workspace, "rev-parse", "HEAD")
        baseline_tree = _tree_sha256(workspace)
        allocation = store.allocate_s6_migration(
            task_id=str(task["id"]),
            task_uid=str(task["task_uid"]),
            mode="revalidate",
            baseline_commit=baseline_commit,
            baseline_tree=baseline_tree,
        )
        self._fault("s6_validation_allocated")
        state = allocation["state"]
        record = allocation["record"]
        sequence = int(record["evidence_seq"])
        executor = self.executor or SandboxExecutor(
            context.run["config_snapshot"]["sandbox"]["image"],
            context.run["config_snapshot"]["sandbox"]["cpu"],
            context.run["config_snapshot"]["sandbox"]["mem_gb"],
        )
        builds = run_build_variants(executor, workspace, blueprint, constraints, fail_fast=False)
        smokes = (
            run_smoke_checks(
                executor,
                workspace,
                blueprint,
                builds,
                context.run["config_snapshot"]["smoke"]["dwell_seconds"],
                context.run["config_snapshot"]["smoke"]["term_grace_seconds"],
                fail_fast=False,
            )
            if builds and all(item["status"] == "passed" for item in builds)
            else []
        )
        result_prefix = f"validations/{task['task_uid']}/validation_{sequence:03d}"
        build_refs, smoke_refs = _publish_results(store, result_prefix, builds, smokes)
        if not builds or not smokes or any(item["status"] != "passed" for item in [*builds, *smokes]):
            failure_ref = store.publish_immutable_json(
                f"{result_prefix}/failure.json",
                {"code": "REVALIDATION_FAILED", "build_result_refs": build_refs, "smoke_result_refs": smoke_refs},
            )
            failed = project_state_transition(
                state,
                {
                    "schema_version": "2.0",
                    "event": "revalidation_failed",
                    "task_id": task["id"],
                    "error": failure_ref.path,
                    "proof": {"previous_failure_ref": failure_ref.as_dict(), "evidence_seq": sequence},
                },
            )
            store.replace_plan_state(failed, event_type="revalidation_failed", event={"task_id": task["id"], "failure_ref": failure_ref.as_dict()})
            terminal = copy.deepcopy(record)
            terminal.update({"status": "failed", "build_result_refs": build_refs, "smoke_result_refs": smoke_refs, "failure_ref": failure_ref.as_dict()})
            store.replace_json(allocation["record_ref"].path, terminal, schema_name="s6-validation.schema.json")
            return
        self._publish_revalidation_success(
            context,
            plan,
            task,
            state,
            allocation,
            baseline_commit,
            baseline_tree,
            build_refs,
            smoke_refs,
        )

    def _run_amendment(
        self,
        context: StageContext,
        plan: Mapping[str, Any],
        blueprint: Mapping[str, Any],
        constraints: Mapping[str, Any],
        task: Mapping[str, Any],
    ) -> None:
        store = context.store
        workspace = store._confined("workspace")
        baseline_commit = _git(workspace, "rev-parse", "HEAD")
        baseline_tree = _tree_sha256(workspace)
        self._require_s6_call_capacity(store, str(task["id"]))
        allocation = store.allocate_s6_migration(
            task_id=str(task["id"]), task_uid=str(task["task_uid"]), mode="amend",
            baseline_commit=baseline_commit, baseline_tree=baseline_tree,
        )
        state = allocation["state"]
        row = next(item for item in state["tasks"] if item["id"] == task["id"])
        attempt = int(row["attempts"])
        sequence = int(allocation["record"]["evidence_seq"])
        self._fault("s6_amendment_allocated")
        package = _work_package(plan, task)
        contract_map = _load(store, "plan/contract_map.json", "contract-map.schema.json")
        inputs, _accounting = project_s6_context(
            task=_public_task(task), work_package=package,
            architecture=_architecture_projection(plan, task, package),
            spec_slice=_spec_slice(store._read_json_artifact("spec/spec.json"), task, package),
            contract_map=_contract_projection(contract_map, task),
            interface_files=_interface_files(workspace, plan, task),
            language_guidance=constraints.get("advisory", {}),
            current_files=_task_files(workspace, list(task.get("deliverable_files", []))),
            execution_mode="amend", failed_candidate={},
            validation_feedback={"migration_ref": row["migration_ref"]},
            diagnosis={"reason": "accepted migration amendment"},
            max_tokens=int(context.run["config_snapshot"]["budgets"]["coder_context_max_tokens"]),
        )
        schema, example = coding_contract()
        storage_uid = f"{task['task_uid']}/amendment"
        try:
            if context.orchestrator is not None:
                context.orchestrator.admit_external_call(store)
            result = self.agent.invoke(
                role="fixer", inputs={key: inputs[key] for key in FIXER_INPUTS},
                output_schema=schema, output_example=example, run_id=context.run["run_id"],
                stage="S6", task_id=task["id"], attempt=max(1, attempt), use_cache=False,
                tier_override="T1", allow_structured_repair=False,
            )
            parsed = result.parsed
            model_output_ref = _model_output_ref(store, storage_uid, 1, str(getattr(result.response, "text", "")))
            candidate_manifest_ref = _archive_candidate_response(store, storage_uid, 1, parsed)
            files = normalize_candidate(parsed, task, _load(store, "plan/file_ledger.json", "file-ledger.schema.json"))
            candidate_refs = _write_candidate(store, storage_uid, 1, files)
            self._fault("s6_amendment_candidate_persisted")
            candidate = _candidate_workspace(workspace, store, storage_uid, 1, files)
            candidate_task_files = _task_files(candidate, list(task.get("deliverable_files", [])))
            _validate_candidate_bindings(
                {path: content.encode("utf-8") for path, content in candidate_task_files.items()},
                task, plan, blueprint, contract_map,
            )
            executor = self.executor or SandboxExecutor(
                context.run["config_snapshot"]["sandbox"]["image"],
                context.run["config_snapshot"]["sandbox"]["cpu"],
                context.run["config_snapshot"]["sandbox"]["mem_gb"],
            )
            builds = run_build_variants(executor, candidate, blueprint, constraints, fail_fast=False)
            smokes = run_smoke_checks(
                executor, candidate, blueprint, builds,
                context.run["config_snapshot"]["smoke"]["dwell_seconds"],
                context.run["config_snapshot"]["smoke"]["term_grace_seconds"], fail_fast=False,
            ) if builds and all(item["status"] == "passed" for item in builds) else []
            result_prefix = f"attempts/{task['task_uid']}/amendment"
            build_refs, smoke_refs = _publish_results(store, result_prefix, builds, smokes)
            if not builds or not smokes or any(item["status"] != "passed" for item in [*builds, *smokes]):
                raise S6ExecutionError("amendment build or smoke failed")
            changed = {path: content for path, content in files.items() if not (workspace / path).is_file() or (workspace / path).read_bytes() != content}
            if not changed:
                raise S6ExecutionError("AMEND candidate contains no actual byte changes")
            candidate_tree = _tree_sha256(candidate)
            evidence = {
                "schema_version": "2.0", "task_uid": task["task_uid"], "task_id": task["id"],
                "evidence_seq": sequence, "execution_kind": "amend", "attempt": attempt,
                "amendment_used": 1, "plan_ref": dict(state["plan_ref"]),
                "plan_version": state["plan_ref"]["version"], "epoch": state["plan_ref"]["epoch"],
                "binding_ref": dict(context.run["stages"]["s5"]["output_refs"]["binding_receipt"]),
                "input_refs": {key: {"path": path, "sha256": context.run["inputs"][key]["sha256"]} for key, path in (("spec", "spec/spec.json"), ("target_profile", "inputs/target.json"), ("test_bundle", "inputs/test_bundle.json"))},
                "plan_sha256": _hash(plan), "workspace_tree": candidate_tree,
                "build_result_refs": build_refs, "smoke_result_refs": smoke_refs, "test_summary_refs": [],
                "changed_files": [{"path": path, "sha256": sha256_bytes(content)} for path, content in sorted(changed.items(), key=lambda item: item[0].encode("utf-8"))],
                "accepted": True, "migration_ref": copy.deepcopy(row["migration_ref"]),
            }
            evidence_path = f"test_results/task_evidence/{task['task_uid']}/evidence_{sequence:03d}.json"
            evidence_ref = ArtifactRef(evidence_path, _hash(evidence))
            prepared = prepare_task_commit(
                workspace, changed, task_id=task["id"], task_uid=task["task_uid"], attempt=attempt,
                evidence_seq=sequence, evidence_sha256=evidence_ref.sha256,
                plan_version=state["plan_ref"]["version"], epoch=state["plan_ref"]["epoch"],
            )
            old_file = _load(store, "plan/file_ledger.json", "file-ledger.schema.json")
            old_revision = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
            new_file = _file_ledger_after(old_file, task, changed, prepared["commit_sha"], build_refs, evidence_ref.as_dict(), state["plan_ref"]["version"], epoch=state["plan_ref"]["epoch"])
            new_revision = append_verification_committed(old_revision, task_uid=task["task_uid"], evidence_ref=evidence_ref.as_dict(), commit_sha=prepared["commit_sha"], revision_seq=state["plan_ref"]["revision_seq"])
            new_state = _state_after_migration_success(state, task["id"], prepared["commit_sha"], evidence_ref.as_dict(), "amend", str(parsed.get("notes", "")))
            wal = {
                "schema_version": "2.0", "kind": "normal", "task_id": task["id"], "task_uid": task["task_uid"],
                "attempt": attempt, "evidence_seq": sequence, "old_state": state, "new_state": new_state,
                "old_file_ledger": old_file, "new_file_ledger": new_file,
                "old_revision_ledger": old_revision, "new_revision_ledger": new_revision,
                "evidence_ref": evidence_ref.as_dict(), "candidate_ref": next(iter(candidate_refs.values())).as_dict(),
                "candidate_file_refs": {path: ref.as_dict() for path, ref in candidate_refs.items()},
                "changed_files": evidence["changed_files"], "build_result_refs": build_refs, "smoke_result_refs": smoke_refs,
                "notes": str(parsed.get("notes", "")), "expected_tree": candidate_tree,
                "expected_git_tree": prepared["tree_sha"], "expected_commit": prepared["commit_sha"],
                "commit_message": prepared["message"], "commit_timestamp": prepared["timestamp"],
                "expected_parent": baseline_commit,
                "expected_trailers": {"NePA-Task": task["id"], "NePA-Task-UID": task["task_uid"], "NePA-Plan": state["plan_ref"]["version"], "NePA-Epoch": state["plan_ref"]["epoch"], "NePA-Attempt": str(attempt), "NePA-Evidence-Seq": str(sequence), "NePA-Evidence-SHA256": evidence_ref.sha256},
                "phase": "prepared", "commit_sha": None,
            }
            store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
            self._fault("s6_wal_prepared")
            published = store.publish_immutable_json(evidence_path, evidence, schema_name="task-evidence.schema.json")
            if published != evidence_ref:
                raise S6ExecutionError("published AMEND evidence disagrees with its WAL reference")
            self._fault("s6_evidence_published")
            for path, content in changed.items():
                target = workspace / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            self._fault("s6_candidate_installed")
            wal["phase"] = "candidate_installed"
            store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
            commit = publish_task_commit(workspace, changed.keys(), prepared)
            wal.update({"phase": "committed", "commit_sha": commit["commit_sha"]})
            store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
            self._fault("s6_commit_created")
            self.reconcile_verification_wal(store)
        except BudgetExhausted:
            raise
        except Exception as exc:
            if _git(workspace, "rev-parse", "HEAD") != baseline_commit:
                raise RunStoreError(f"post-commit AMEND publication failed: {exc}") from exc
            failure_ref = store.publish_immutable_json(
                f"attempts/{task['task_uid']}/amendment/failure.json",
                {"code": "AMENDMENT_FAILED", "detail": str(exc)},
            )
            failed = project_state_transition(
                state,
                {"schema_version": "2.0", "event": "amendment_failed", "task_id": task["id"], "error": failure_ref.path, "proof": {"previous_failure_ref": failure_ref.as_dict(), "evidence_seq": sequence}},
            )
            store.replace_plan_state(failed, event_type="amendment_failed", event={"task_id": task["id"], "failure_ref": failure_ref.as_dict()})
            self._finish_amendment(store, task["task_uid"], status="failed", failure_ref=failure_ref.as_dict())

    def _publish_revalidation_success(
        self,
        context: StageContext,
        plan: Mapping[str, Any],
        task: Mapping[str, Any],
        state: Mapping[str, Any],
        allocation: Mapping[str, Any],
        baseline_commit: str,
        baseline_tree: str,
        build_refs: list[dict[str, str]],
        smoke_refs: list[dict[str, str]],
    ) -> None:
        store = context.store
        workspace = store._confined("workspace")
        sequence = int(allocation["record"]["evidence_seq"])
        row = next(item for item in state["tasks"] if item["id"] == task["id"])
        evidence = {
            "schema_version": "2.0",
            "task_uid": task["task_uid"],
            "task_id": task["id"],
            "evidence_seq": sequence,
            "execution_kind": "revalidate",
            "attempt": 0,
            "amendment_used": 0,
            "plan_ref": dict(state["plan_ref"]),
            "plan_version": state["plan_ref"]["version"],
            "epoch": state["plan_ref"]["epoch"],
            "binding_ref": dict(context.run["stages"]["s5"]["output_refs"]["binding_receipt"]),
            "input_refs": {
                key: {"path": path, "sha256": context.run["inputs"][key]["sha256"]}
                for key, path in (
                    ("spec", "spec/spec.json"),
                    ("target_profile", "inputs/target.json"),
                    ("test_bundle", "inputs/test_bundle.json"),
                )
            },
            "plan_sha256": _hash(plan),
            "workspace_tree": baseline_tree,
            "build_result_refs": build_refs,
            "smoke_result_refs": smoke_refs,
            "test_summary_refs": [],
            "changed_files": [],
            "accepted": True,
            "migration_ref": copy.deepcopy(row["migration_ref"]),
        }
        evidence_path = f"test_results/task_evidence/{task['task_uid']}/evidence_{sequence:03d}.json"
        evidence_ref = ArtifactRef(evidence_path, _hash(evidence))
        prepared = prepare_task_commit(
            workspace,
            {},
            task_id=task["id"],
            task_uid=task["task_uid"],
            attempt=0,
            evidence_seq=sequence,
            evidence_sha256=evidence_ref.sha256,
            plan_version=state["plan_ref"]["version"],
            epoch=state["plan_ref"]["epoch"],
            allow_empty=True,
        )
        old_file = _load(store, "plan/file_ledger.json", "file-ledger.schema.json")
        old_revision = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
        new_revision = append_verification_committed(
            old_revision,
            task_uid=task["task_uid"],
            evidence_ref=evidence_ref.as_dict(),
            commit_sha=prepared["commit_sha"],
            revision_seq=state["plan_ref"]["revision_seq"],
        )
        new_file = _file_ledger_after(
            old_file,
            task,
            {},
            prepared["commit_sha"],
            build_refs,
            evidence_ref.as_dict(),
            state["plan_ref"]["version"],
            epoch=state["plan_ref"]["epoch"],
            verified_paths=tuple(task.get("deliverable_files", [])),
        )
        new_state = _state_after_migration_success(
            state,
            task["id"],
            prepared["commit_sha"],
            evidence_ref.as_dict(),
            "revalidate",
            "revalidated current tree",
        )
        wal = {
            "schema_version": "2.0",
            "kind": "normal",
            "task_id": task["id"],
            "task_uid": task["task_uid"],
            "attempt": 0,
            "evidence_seq": sequence,
            "old_state": copy.deepcopy(dict(state)),
            "new_state": new_state,
            "old_file_ledger": old_file,
            "new_file_ledger": new_file,
            "old_revision_ledger": old_revision,
            "new_revision_ledger": new_revision,
            "evidence_ref": evidence_ref.as_dict(),
            "candidate_ref": allocation["record_ref"].as_dict(),
            "candidate_file_refs": {},
            "changed_files": [],
            "build_result_refs": build_refs,
            "smoke_result_refs": smoke_refs,
            "notes": "revalidated current tree",
            "expected_tree": baseline_tree,
            "expected_git_tree": prepared["tree_sha"],
            "expected_commit": prepared["commit_sha"],
            "commit_message": prepared["message"],
            "commit_timestamp": prepared["timestamp"],
            "expected_parent": baseline_commit,
            "expected_trailers": {
                "NePA-Task": task["id"],
                "NePA-Task-UID": task["task_uid"],
                "NePA-Plan": state["plan_ref"]["version"],
                "NePA-Epoch": state["plan_ref"]["epoch"],
                "NePA-Attempt": "0",
                "NePA-Evidence-Seq": str(sequence),
                "NePA-Evidence-SHA256": evidence_ref.sha256,
            },
            "phase": "prepared",
            "commit_sha": None,
        }
        store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
        self._fault("s6_wal_prepared")
        published = store.publish_immutable_json(evidence_path, evidence, schema_name="task-evidence.schema.json")
        if published != evidence_ref:
            raise S6ExecutionError("published revalidation evidence disagrees with its WAL reference")
        self._fault("s6_evidence_published")
        commit = publish_task_commit(workspace, [], prepared, allow_empty=True)
        wal.update({"phase": "committed", "commit_sha": commit["commit_sha"]})
        store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
        self._fault("s6_commit_created")
        self.reconcile_verification_wal(store)

    def _run_task(self, context: StageContext, plan: Mapping[str, Any], blueprint: Mapping[str, Any], constraints: Mapping[str, Any], task: Mapping[str, Any]) -> None:
        store = context.store
        workspace = store._confined("workspace")
        state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
        row = next(item for item in state["tasks"] if item["id"] == task["id"])
        if row["execution_mode"] == "revalidate":
            self._run_revalidation(context, plan, blueprint, constraints, task)
            return
        if row["execution_mode"] == "amend":
            self._run_amendment(context, plan, blueprint, constraints, task)
            return
        attempt = row["attempts"] + 1
        tier = "T2" if attempt <= 3 else "T1"
        role = "coder" if attempt == 1 else "fixer"
        baseline_commit = _git(workspace, "rev-parse", "HEAD")
        baseline_tree = _tree_sha256(workspace)
        lease_authorization: Mapping[str, Any] | None = None
        lease_authorization_ref: Mapping[str, Any] | None = None
        if role == "fixer" and self.lease_authorization_provider is not None:
            revision_ledger = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
            revision_trigger = next(
                (
                    copy.deepcopy(entry["payload"])
                    for entry in reversed(revision_ledger["entries"])
                    if entry.get("event_type") == "trigger_evaluated"
                    and entry.get("payload", {}).get("hit_code") == "TR-3"
                    and entry.get("payload", {}).get("route") == "F1"
                    and any(
                        f"/{task['task_uid']}/" in f"/{ref.get('path', '')}"
                        for ref in entry.get("payload", {}).get("evidence_refs", [])
                        if isinstance(ref, Mapping)
                    )
                ),
                None,
            )
            request = {
                "plan": copy.deepcopy(dict(plan)),
                "state": copy.deepcopy(state),
                "file_ledger": _load(store, "plan/file_ledger.json", "file-ledger.schema.json"),
                "revision_ledger": revision_ledger,
                "revision_trigger": revision_trigger,
                "config_snapshot": copy.deepcopy(context.run["config_snapshot"]),
                "task": copy.deepcopy(dict(task)),
                "baseline_commit": baseline_commit,
                "baseline_tree": baseline_tree,
            }
            candidate_authorization = self.lease_authorization_provider(request)
            if isinstance(candidate_authorization, Mapping):
                try:
                    lease_authorization = validate_lease_authorization(
                        candidate_authorization,
                        plan=plan,
                        state=state,
                        file_ledger=request["file_ledger"],
                        revision_ledger=request["revision_ledger"],
                        config_snapshot=context.run["config_snapshot"],
                        baseline_commit=baseline_commit,
                        baseline_tree=baseline_tree,
                    )
                    authorization_artifact = store.publish_immutable_json(
                        f"attempts/{task['task_uid']}/attempt_{attempt:03d}/lease_authorization.json",
                        lease_authorization,
                        schema_name="lease-authorization.schema.json",
                    )
                    lease_authorization_ref = authorization_artifact.as_dict()
                except (PlanStateError, RunStoreError, KeyError, TypeError):
                    lease_authorization = None
                    lease_authorization_ref = None
        self._require_s6_call_capacity(store, str(task["id"]))
        allocation = store.allocate_s6_attempt(task_id=task["id"], task_uid=task["task_uid"], role=role, tier=tier, baseline_commit=baseline_commit, baseline_tree=baseline_tree, lease_authorization=lease_authorization, lease_authorization_ref=lease_authorization_ref)
        state = allocation["state"]
        lease_id = allocation["attempt"].get("lease", {}).get("lease_id") if isinstance(allocation["attempt"].get("lease"), Mapping) else None
        self._fault("s6_attempt_allocated")
        previous = _find_previous_failure(store, task["task_uid"], attempt)
        lease_files: dict[str, str] = {}
        lease_tasks: dict[str, Mapping[str, Any]] = {}
        leased_paths: tuple[str, ...] = ()
        if lease_authorization is not None:
            task_by_uid = {item.get("task_uid"): item for item in plan.get("tasks", []) if isinstance(item, Mapping)}
            for lender in lease_authorization.get("lenders", []):
                lender_task = task_by_uid.get(lender.get("task_uid"))
                if isinstance(lender_task, Mapping):
                    lease_tasks[str(lender_task["id"])] = lender_task
                    for path in lender.get("paths", []):
                        lease_files[path] = (workspace / path).read_text(encoding="utf-8")
            leased_paths = tuple(sorted(lease_files, key=lambda item: item.encode("utf-8")))
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
                lease_authorization=lease_authorization, leased_files=lease_files,
                max_tokens=int(context.run["config_snapshot"]["budgets"]["coder_context_max_tokens"]),
            )
        except (S6AgentError, RunStoreError) as exc:
            error = {"attempt": attempt, "code": "CONTEXT_INVALID", "detail": str(exc), "candidate": {}, "feedback": {"error": str(exc)}, "diagnosis": {}}
            self._record_failure(store, task, attempt, error)
            self._finish_failed_lease(store, lease_id, str(exc))
            return None
        schema, example = coding_contract()
        try:
            if context.orchestrator is not None:
                context.orchestrator.admit_external_call(store)
            input_names = CODER_INPUTS if role == "coder" else (LEASE_FIXER_INPUTS if lease_authorization is not None else FIXER_INPUTS)
            result = self.agent.invoke(
                role=role, inputs={key: inputs[key] for key in input_names},
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
            controller_facts: dict[str, Any] = {}
            if responses and str(responses[-1].provider_metadata.get("finish_reason", "")).lower() in {"length", "max_tokens"}:
                controller_facts["finish_reason"] = str(responses[-1].provider_metadata["finish_reason"]).lower()
            error = {"attempt": attempt, "code": "AGENT_FAILURE", "detail": str(exc), "candidate": {}, "model_output_ref": model_ref.as_dict() if model_ref else None, "feedback": {"error": str(exc)}, "diagnosis": {}, "controller_facts": controller_facts}
            self._record_failure(store, task, attempt, error)
            call_refs = _call_refs_for_attempt(store, task["id"], attempt)
            self._finish_failed_lease(store, lease_id, str(exc), call_refs or ([{"path": model_ref.path}] if model_ref else []))
            return
        files: dict[str, bytes] = {}
        candidate_refs: dict[str, ArtifactRef] = {}
        builds: list[Mapping[str, Any]] = []
        smokes: list[Mapping[str, Any]] = []
        build_refs: list[dict[str, str]] = []
        smoke_refs: list[dict[str, str]] = []
        candidate_manifest_ref = _archive_candidate_response(store, task["task_uid"], attempt, parsed)
        try:
            files = normalize_candidate(parsed, task, _load(store, "plan/file_ledger.json", "file-ledger.schema.json"), leased_paths=leased_paths)
            candidate_refs = _write_candidate(store, task["task_uid"], attempt, files)
            self._fault("s6_candidate_persisted")
            candidate = _candidate_workspace(workspace, store, task["task_uid"], attempt, files)
            self._fault("s6_candidate_workspace_ready")
            candidate_task_files = _task_files(candidate, [*task.get("deliverable_files", []), *leased_paths])
            _validate_candidate_bindings(
                {path: content.encode("utf-8") for path, content in candidate_task_files.items()},
                task, plan, blueprint, _load(store, "plan/contract_map.json", "contract-map.schema.json"), lease_tasks,
            )
            executor = self.executor or SandboxExecutor(context.run["config_snapshot"]["sandbox"]["image"], context.run["config_snapshot"]["sandbox"]["cpu"], context.run["config_snapshot"]["sandbox"]["mem_gb"])
            builds = run_build_variants(executor, candidate, blueprint, constraints, fail_fast=False)
            smokes = run_smoke_checks(executor, candidate, blueprint, builds, context.run["config_snapshot"]["smoke"]["dwell_seconds"], context.run["config_snapshot"]["smoke"]["term_grace_seconds"], fail_fast=False) if all(item["status"] == "passed" for item in builds) else []
            build_refs, smoke_refs = _publish_results(store, f"attempts/{task['task_uid']}/attempt_{attempt:03d}", builds, smokes)
            passed = bool(builds) and all(item["status"] == "passed" for item in builds) and bool(smokes) and all(item["status"] == "passed" for item in smokes)
            if not passed:
                error = {"attempt": attempt, "code": "CANDIDATE_VALIDATION_FAILED", "detail": "build or smoke failed", "candidate": {path: content.decode("utf-8") for path, content in files.items()}, "candidate_manifest_ref": candidate_manifest_ref.as_dict() if candidate_manifest_ref else None, "candidate_refs": {path: ref.as_dict() for path, ref in candidate_refs.items()}, "candidate_tree": _tree_sha256(candidate), "model_output_ref": model_output_ref.as_dict(), "feedback": {"build": builds, "smoke": smokes}, "diagnosis": {}, "controller_facts": {"build_result_refs": build_refs}}
                self._record_failure(store, task, attempt, error)
                self._finish_failed_lease(store, lease_id, "build or smoke failed", _call_refs_for_attempt(store, task["id"], attempt))
                return
            candidate_tree = _tree_sha256(candidate)
            changed = {path: content for path, content in files.items() if not (workspace / path).is_file() or (workspace / path).read_bytes() != content}
            if not changed:
                raise S6ExecutionError("normal S6 candidate contains no actual changes")
            if lease_authorization is not None:
                self._publish_lease_success(
                    context, plan, task, state, allocation, lease_authorization, lease_tasks, files, changed,
                    candidate_tree, candidate_refs, build_refs, smoke_refs, parsed, baseline_commit,
                )
                return
            evidence = {
                "schema_version": "2.0", "task_uid": task["task_uid"], "task_id": task["id"], "evidence_seq": allocation["attempt"]["evidence_seq"], "execution_kind": "normal", "attempt": attempt, "amendment_used": 0,
                "plan_ref": dict(state["plan_ref"]), "plan_version": state["plan_ref"]["version"], "epoch": state["plan_ref"]["epoch"], "binding_ref": dict(context.run["stages"]["s5"]["output_refs"]["binding_receipt"]), "input_refs": {key: {"path": path, "sha256": context.run["inputs"][key]["sha256"]} for key, path in (("spec", "spec/spec.json"), ("target_profile", "inputs/target.json"), ("test_bundle", "inputs/test_bundle.json"))}, "plan_sha256": _hash(plan), "workspace_tree": candidate_tree, "build_result_refs": build_refs, "smoke_result_refs": smoke_refs, "test_summary_refs": [],
                "changed_files": [{"path": path, "sha256": sha256_bytes(content)} for path, content in sorted(changed.items(), key=lambda item: item[0].encode("utf-8"))], "accepted": True, "migration_ref": copy.deepcopy(row.get("migration_ref")),
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
            new_file = _file_ledger_after(old_file, task, changed, prepared["commit_sha"], build_refs, evidence_ref.as_dict(), state["plan_ref"]["version"], epoch=state["plan_ref"]["epoch"])
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
            candidate_controller_facts: dict[str, Any] = {}
            returned_paths = {
                str(item.get("path"))
                for item in parsed.get("files", [])
                if isinstance(parsed, Mapping) and isinstance(item, Mapping) and isinstance(item.get("path"), str)
            } if isinstance(parsed, Mapping) else set()
            owners = {
                str(path): {"task_uid": str(owner["task_uid"]), "task_id": str(owner["id"]), "work_package": str(owner["work_package"])}
                for owner in plan.get("tasks", []) if isinstance(owner, Mapping)
                for path in owner.get("deliverable_files", [])
            }
            foreign = [
                {"path": path, "owner": owners[path]}
                for path in sorted(returned_paths - set(task.get("deliverable_files", [])), key=lambda value: value.encode("utf-8"))
                if path in owners
            ]
            if foreign:
                candidate_controller_facts["foreign_owned_write_rejections"] = foreign
            if build_refs:
                candidate_controller_facts["build_result_refs"] = build_refs
            detail = str(exc)
            if isinstance(exc, ContractExportDrift):
                candidate_controller_facts["export_drift"] = True
            error = {"attempt": attempt, "code": "CANDIDATE_FAILURE", "detail": detail, "candidate": candidate_text, "candidate_manifest_ref": candidate_manifest_ref.as_dict() if candidate_manifest_ref else None, "candidate_refs": {path: ref.as_dict() for path, ref in candidate_refs.items()}, "model_output_ref": model_output_ref.as_dict(), "feedback": {"build": builds, "smoke": smokes, "error": detail}, "diagnosis": {}, "controller_facts": candidate_controller_facts}
            self._record_failure(store, task, attempt, error)
            if lease_id and not store._confined("plan/verification_pending.json").exists():
                call_refs = _call_refs_for_attempt(store, task["id"], attempt)
                self._finish_failed_lease(store, lease_id, str(exc), call_refs or ([{"path": model_output_ref.path}] if model_output_ref else []))
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
        version = str(active["version"])
        build_refs, smoke_refs = _publish_results(store, f"build/s6/{version}/final", builds, smokes)
        state_ref = ArtifactRef("plan/plan_state.json", store._json_artifact_hash("plan/plan_state.json"))
        state_history = _load(store, "plan/state_history.json", "state-history.schema.json")
        state_history_ref = store.publish_immutable_json(f"plan/s6_snapshots/{version}/state_history.json", state_history, schema_name="state-history.schema.json")
        file_ref = ArtifactRef("plan/file_ledger.json", store._json_artifact_hash("plan/file_ledger.json"))
        revision_ref = store.publish_immutable_json(f"plan/s6_snapshots/{version}/revision_ledger.json", revision, schema_name="revision-ledger.schema.json")
        receipt = {
            "schema_version": "2.0", "active_plan_ref": context.run["stages"]["s4"]["output_refs"]["active_plan"],
            "binding_ref": context.run["stages"]["s5"]["output_refs"]["binding_receipt"], "plan_state_ref": state_ref.as_dict(), "state_history_ref": state_history_ref.as_dict(), "file_ledger_ref": file_ref.as_dict(), "revision_ledger_ref": revision_ref.as_dict(),
            "workspace_head": _git(workspace, "rev-parse", "HEAD"), "build_result_refs": build_refs, "s6_build_ok": True, "smoke_result_refs": smoke_refs, "status": "complete",
        }
        receipt_ref = store.publish_immutable_json("plan/s6_receipt.json", receipt, schema_name="s6-receipt.schema.json")
        self._fault("s6_receipt_published")
        return StageResult(output_refs={"s6_receipt": receipt_ref})

    @staticmethod
    def _revision_facts(
        store: RunStore,
        state: Mapping[str, Any],
        plan: Mapping[str, Any],
        blueprint: Mapping[str, Any],
        contract_map: Mapping[str, Any],
        config_snapshot: Mapping[str, Any],
        constraints: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Project only current, controller-verifiable S6 facts."""

        tasks = {str(task["task_uid"]): task for task in plan.get("tasks", []) if isinstance(task, Mapping)}
        rows = {str(row["task_uid"]): row for row in state.get("tasks", []) if isinstance(row, Mapping)}
        all_uids = sorted(tasks, key=lambda value: value.encode("utf-8"))
        blocked_uids = sorted((uid for uid, row in rows.items() if row.get("status") == "blocked"), key=lambda value: value.encode("utf-8"))
        dependency_blocked_uids = sorted((uid for uid, row in rows.items() if row.get("status") == "blocked_by_dependency"), key=lambda value: value.encode("utf-8"))
        facts: dict[str, Any] = {
            "all_task_uids": all_uids,
            "blocked_task_uids": blocked_uids,
            "dependency_blocked_task_uids": dependency_blocked_uids,
            "revision_ready": not any(row.get("status") == "in_progress" for row in rows.values()),
        }
        declared_symbols = {
            str(export["symbol"])
            for contract in plan.get("architecture", {}).get("contracts", [])
            if isinstance(contract, Mapping)
            for export in contract.get("exports", [])
            if isinstance(export, Mapping) and isinstance(export.get("symbol"), str)
        }
        blueprint_paths = {
            str(rule["path"])
            for rule in expand_file_rules(blueprint, constraints)
            if isinstance(rule, Mapping) and isinstance(rule.get("path"), str)
        } if constraints is not None else {
            str(rule["path_pattern"])
            for rule in blueprint.get("file_rules", [])
            if isinstance(rule, Mapping) and rule.get("expansion") == "none" and isinstance(rule.get("path_pattern"), str)
        }
        roots = {
            str(value).rstrip("/")
            for value in plan.get("architecture", {}).get("layout", {}).get("roots", {}).values()
            if isinstance(value, str) and value
        }
        uid_by_id = {str(task["id"]): uid for uid, task in tasks.items()}
        dependents: dict[str, set[str]] = {uid: set() for uid in tasks}
        for uid, task in tasks.items():
            for dependency_id in task.get("depends_on", []):
                dependency_uid = uid_by_id.get(str(dependency_id))
                if dependency_uid is not None:
                    dependents[dependency_uid].add(uid)

        def downstream(seed: set[str]) -> set[str]:
            result = set(seed)
            stack = list(seed)
            while stack:
                current = stack.pop()
                discovered = dependents.get(current, set()) - result
                result.update(discovered)
                stack.extend(discovered)
            return result

        verified_failures: dict[str, list[tuple[dict[str, Any], dict[str, str], list[tuple[dict[str, Any], dict[str, str]]]]]] = defaultdict(list)
        undefined_pattern = re.compile(r"(?:undefined reference to|undefined symbol:?)\s*[`'\"]?([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
        missing_patterns = (
            re.compile(r"fatal error:\s*([^:\n]+):\s*No such file", re.IGNORECASE),
            re.compile(r"No rule to make target\s+[`'\"]([^`'\"]+)[`'\"]", re.IGNORECASE),
        )
        for uid, task in tasks.items():
            row = rows.get(uid)
            if row is None or not isinstance(row.get("attempts"), int):
                continue
            for attempt in range(1, int(row["attempts"]) + 1):
                attempt_path = f"attempts/{uid}/attempt_{attempt:03d}.json"
                if not store._confined(attempt_path).is_file():
                    continue
                record = _load(store, attempt_path, "s6-attempt.schema.json")
                if record.get("status") not in {"failed", "exhausted"} or record.get("task_uid") != uid or record.get("task_id") != task.get("id") or record.get("plan_ref") != state.get("plan_ref"):
                    continue
                failure_ref = record.get("failure_ref")
                if not isinstance(failure_ref, Mapping) or failure_ref.get("path") != f"attempts/{uid}/attempt_{attempt:03d}/failure.json":
                    continue
                try:
                    store.verify_ref(failure_ref)
                    failure = _load(store, str(failure_ref["path"]))
                except RunStoreError:
                    continue
                if failure.get("attempt") != attempt:
                    continue
                evidence_ref = {"path": str(failure_ref["path"]), "sha256": str(failure_ref["sha256"])}
                builds: list[tuple[Mapping[str, Any], dict[str, str]]] = []
                raw_controller_facts = failure.get("controller_facts")
                controller_facts = dict(raw_controller_facts) if isinstance(raw_controller_facts, Mapping) else {}
                for build_ref in controller_facts.get("build_result_refs", []):
                    if not isinstance(build_ref, Mapping):
                        continue
                    relative = str(build_ref.get("path", ""))
                    expected_prefix = f"attempts/{uid}/attempt_{attempt:03d}/build_"
                    if not relative.startswith(expected_prefix) or not relative.endswith(".json"):
                        continue
                    try:
                        store.verify_ref(build_ref, schema_name="build-result.schema.json")
                        build = _load(store, relative, "build-result.schema.json")
                    except RunStoreError:
                        continue
                    builds.append((build, {"path": relative, "sha256": str(build_ref["sha256"])}))
                verified_failures[uid].append((failure, evidence_ref, builds))

        for uid, failures in verified_failures.items():
            task = tasks[uid]
            for failure_value, evidence_ref, build_results in failures:
                raw_controller_facts = failure_value.get("controller_facts")
                controller_facts = dict(raw_controller_facts) if isinstance(raw_controller_facts, Mapping) else {}
                finish_reason = str(controller_facts.get("finish_reason", "")).lower()
                if finish_reason in {"length", "max_tokens"}:
                    facts.setdefault("truncations", []).append({"task_uid": uid, "task_uids": [uid], "evidence_refs": [evidence_ref]})
                if controller_facts.get("export_drift") is True:
                    facts["boundary_phase"] = "provider_submission"
                    facts.setdefault("export_drifts", []).append({"task_uid": uid, "task_uids": [uid], "matches_frozen_contract": False, "evidence_refs": [evidence_ref]})
                for rejection in controller_facts.get("foreign_owned_write_rejections", []):
                    if not isinstance(rejection, Mapping) or not isinstance(rejection.get("path"), str) or not isinstance(rejection.get("owner"), Mapping):
                        continue
                    owner_uid = str(rejection["owner"].get("task_uid"))
                    owner = tasks.get(owner_uid)
                    owner_row = rows.get(owner_uid)
                    if owner is None or owner_row is None or rejection["path"] not in owner.get("deliverable_files", []):
                        continue
                    file_ledger = _load(store, "plan/file_ledger.json", "file-ledger.schema.json")
                    revision_ledger = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
                    workspace = store._confined("workspace")
                    baseline_commit = _git(workspace, "rev-parse", "HEAD")
                    baseline_tree = _git(workspace, "rev-parse", "HEAD^{tree}")
                    directly_related = lease_lender_directly_related(task, owner, plan)
                    same_package = owner.get("work_package") == task.get("work_package")
                    lease_limit = int(config_snapshot.get("budgets", {}).get("s6_lease_limit", 0))
                    lease_starts = sum(entry.get("event_type") == "lease_started" for entry in revision_ledger.get("entries", []))
                    accepted_ref = owner_row.get("acceptance_evidence", {}).get("task_evidence_ref") if isinstance(owner_row.get("acceptance_evidence"), Mapping) else None
                    authorization = {
                        "schema_version": "1.0", "active_plan_ref": state.get("plan_ref"),
                        "baseline_commit": baseline_commit, "baseline_tree": baseline_tree,
                        "current_task_id": task.get("id"), "current_task_uid": uid,
                        "lenders": [{
                            "task_id": owner.get("id"), "task_uid": owner_uid,
                            "paths": [rejection["path"]],
                            "evidence_refs": [accepted_ref] if isinstance(accepted_ref, Mapping) else [],
                        }],
                        "authorization_evidence_refs": [evidence_ref],
                    }
                    f1_eligible = False
                    if directly_related:
                        try:
                            validate_lease_authorization(
                                authorization, plan=plan, state=state, file_ledger=file_ledger,
                                revision_ledger=revision_ledger, config_snapshot=config_snapshot,
                                baseline_commit=baseline_commit, baseline_tree=baseline_tree,
                            )
                            f1_eligible = True
                        except PlanStateError:
                            pass
                    facts.setdefault("write_rejections", []).append({
                        "task_uid": uid, "task_uids": [uid], "path": rejection["path"], "paths": [rejection["path"]],
                        "f1_eligible": f1_eligible,
                        "f1_exhausted": lease_limit <= 0 or lease_starts >= lease_limit,
                        "same_package_reassignment_legal": same_package,
                        "evidence_refs": [evidence_ref],
                    })
                for build_value, build_ref in build_results:
                    if build_value.get("status") != "failed":
                        continue
                    diagnostics = f"{build_value.get('stdout', '')}\n{build_value.get('stderr', '')}"
                    refs = [evidence_ref, build_ref]
                    for symbol in undefined_pattern.findall(diagnostics):
                        facts.setdefault("undefined_symbols", []).append({
                            "task_uid": uid, "task_uids": [uid], "symbol": symbol,
                            "declared": symbol in declared_symbols, "consumes_closure_complete": True,
                            "evidence_refs": refs,
                        })
                    for pattern in missing_patterns:
                        for missing in pattern.findall(diagnostics):
                            path = posixpath.normpath(str(missing).strip())
                            safe = not path.startswith("/") and ".." not in PurePosixPath(path).parts and "\\" not in path
                            work_package = next(
                                (row for row in plan.get("work_packages", []) if isinstance(row, Mapping) and row.get("id") == task.get("work_package")),
                                None,
                            )
                            module_id = work_package.get("module") if isinstance(work_package, Mapping) else None
                            module = next(
                                (row for row in plan.get("architecture", {}).get("modules", []) if isinstance(row, Mapping) and row.get("id") == module_id),
                                None,
                            )
                            owned = {
                                str(value)
                                for value in [
                                    *(work_package.get("allowed_files", []) if isinstance(work_package, Mapping) else []),
                                    *(module.get("owns_files", []) if isinstance(module, Mapping) else []),
                                ]
                            }
                            owned_directories = {PurePosixPath(value).parent.as_posix() for value in owned}
                            fits = (
                                safe
                                and module is not None
                                and PurePosixPath(path).parent.as_posix() in owned_directories
                                and any(path == root or path.startswith(root + "/") for root in roots)
                            )
                            facts.setdefault("missing_inputs", []).append({
                                "task_uid": uid, "task_uids": [uid], "paths": [path],
                                "absent_from_blueprint": path not in blueprint_paths,
                                "fits_existing_architecture": fits,
                                "evidence_refs": refs,
                            })

        remaining_primary = {
            uid for uid, task in tasks.items()
            if rows.get(uid, {}).get("status") != "done"
            and any(item.get("role") == "primary" for item in task.get("requirement_responsibilities", []))
        }
        for contract in contract_map.get("contracts", []):
            if not isinstance(contract, Mapping) or contract.get("ready_gate") != "task":
                continue
            provider_uid = uid_by_id.get(str(contract.get("provider_task_id")))
            if provider_uid is None or rows.get(provider_uid, {}).get("status") != "blocked":
                continue
            consumers = {uid for uid, task in tasks.items() if contract.get("contract_id") in task.get("consumes_contracts", [])}
            closure = downstream(consumers)
            evidence_refs = [item[1] for item in verified_failures.get(provider_uid, [])[-1:]]
            facts.setdefault("blocked_providers", []).append({
                "task_uids": [provider_uid], "unique_provider": True, "task_ready": True,
                "consumer_task_uids": sorted(closure, key=lambda value: value.encode("utf-8")),
                "remaining_incomplete_primary_task_uids": sorted(remaining_primary, key=lambda value: value.encode("utf-8")),
                "evidence_refs": evidence_refs,
            })

        role_limits: list[int] = []
        for role_name in ("coder", "fixer"):
            role = config_snapshot.get("roles", {}).get(role_name, {})
            tier = config_snapshot.get("tiers", {}).get(role.get("tier"), {}) if isinstance(role, Mapping) else {}
            value = role.get("max_tokens") if isinstance(role, Mapping) and role.get("max_tokens") is not None else tier.get("max_tokens")
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                role_limits.append(value)
        if len(role_limits) == 2:
            frozen_tokens = min(role_limits)
            for uid, task in tasks.items():
                paths = sorted({str(path) for path in task.get("deliverable_files", [])}, key=lambda value: value.encode("utf-8"))
                owned_paths = {
                    str(rule["path_pattern"])
                    for rule in blueprint.get("file_rules", [])
                    if isinstance(rule, Mapping) and rule.get("mutability") == "s6_owned" and rule.get("owner_task_id") == task.get("id") and isinstance(rule.get("path_pattern"), str)
                }
                deliverable_paths = [path for path in paths if path in owned_paths]
                if not deliverable_paths:
                    continue
                envelope = {"micro_plan": [], "files": [{"path": path, "content": ""} for path in deliverable_paths], "notes": ""}
                projected_tokens = 4000 * len(deliverable_paths) + (len(canonical_json_bytes(envelope)) + 3) // 4
                if projected_tokens > frozen_tokens:
                    facts.setdefault("lint_output_overflows", []).append({
                        "task_uids": [uid], "paths": deliverable_paths,
                        "projected_output_tokens": projected_tokens, "frozen_output_tokens": frozen_tokens,
                        "evidence_refs": [],
                    })
        blocked_refs = []
        for uid in blocked_uids + dependency_blocked_uids:
            blocked_refs.extend(item[1] for item in verified_failures.get(uid, [])[-1:])
        facts["blocked_evidence_refs"] = blocked_refs
        return facts

    def _activate_revision_handoff(
        self,
        context: StageContext,
        *,
        candidate: Mapping[str, Any],
        bundle: Mapping[str, Any],
        selected: Mapping[str, Any],
        active: Mapping[str, Any],
        state: Mapping[str, Any],
        file_ledger: Mapping[str, Any],
        gates_ref: Mapping[str, Any],
        rehearsal_ref: Mapping[str, Any] | None,
        rework: Mapping[str, Any],
        constraints: Mapping[str, Any],
    ) -> StageResult:
        store = context.store
        wal_path = f"plan/_s4r/candidate_{selected['event_seq']}/activation.json"
        if store._confined(wal_path).is_file():
            accepted_wal = _load(store, wal_path, "plan-activation.schema.json")
            if (
                accepted_wal.get("phase") == "reconciled"
                and accepted_wal.get("candidate_id") == candidate.get("candidate_id")
                and accepted_wal.get("selected_event_seq") == selected.get("event_seq")
                and accepted_wal.get("level") == candidate.get("level")
                and accepted_wal.get("old", {}).get("pointer") == active
                and accepted_wal.get("gates_ref") == gates_ref
                and accepted_wal.get("rehearsal_ref") == rehearsal_ref
            ):
                replay_wal = copy.deepcopy(accepted_wal)
                replay_wal["phase"] = "prepared"
                current_run = store.load_run()
                expected_run = accepted_wal["old"]["run"]
                comparable_run = copy.deepcopy(current_run)
                comparable_run["stages"]["s6"] = copy.deepcopy(expected_run["stages"]["s6"])
                comparable_run["budget_used"]["wall_clock_s"] = expected_run["budget_used"]["wall_clock_s"]
                if comparable_run != expected_run:
                    raise ArtifactConflict("activation replay Run state differs beyond resume bookkeeping")
                store.replace_run(expected_run)
                store.activate_revision_v2(replay_wal, fault_hook=self.fault_hook)
                return StageResult(pause=StagePause(
                    "revision_handoff", int(selected["event_seq"]),
                    f"plan/_s4r/candidate_{selected['event_seq']}/candidate.json",
                ))
        plan = bundle["plan.json"]
        plan_hash = _hash(plan)
        new_pointer = successor_pointer(active, {"sha256": plan_hash}, candidate["level"])
        plan_ref = {"path": new_pointer["path"], "sha256": plan_hash}
        migration = copy.deepcopy(bundle["migration.json"])
        migration["from_version"] = active["version"]
        migration["to_version"] = new_pointer["version"]
        new_state = project_plan_state(
            state, plan, migration, new_pointer,
            activation_event_seq=len(_load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")["entries"]) + 1,
            config_snapshot=store.load_run()["config_snapshot"],
        )
        new_paths = {row["path"] for row in expand_file_rules(bundle["blueprint.json"], constraints)}
        new_file_ledger = project_file_ledger(
            file_ledger, plan, migration, epoch=new_pointer["epoch"], new_paths=new_paths,
        )
        old_run, new_run = store._run_with_active_pointer(new_pointer)
        new_run["stages"]["s6"].update({
            "status": "pending", "started_at": None, "ended_at": None, "error": None,
        })
        new_run["stages"]["s6"].pop("output_refs", None)
        old_manifest = _load(store, "plan/artifact_manifest.json", "artifact-manifest.schema.json")
        old_map = _load(store, "plan/contract_map.json", "contract-map.schema.json")
        spec = _load(store, "spec/spec.json")
        target = _load(store, "inputs/target.json")
        view = derive_rendering_view(plan, spec, target, bundle["blueprint.json"], constraints)
        binding: dict[str, Any] | None = None
        if candidate["level"] == "F2":
            epoch_receipt_path = f"plan/epochs/{active['epoch']}/receipt.json"
            epoch_receipt = _load(store, epoch_receipt_path, "epoch-receipt.schema.json")
            workspace = store._confined("workspace")
            hashes = {
                item.relative_to(workspace).as_posix(): sha256_bytes(item.read_bytes())
                for item in workspace.rglob("*") if item.is_file() and ".git" not in item.parts
            }
            quarantined = sorted(path for path in hashes if path.startswith("_orphan/"))
            projected = project_version_binding(
                plan_ref, bundle["blueprint.json"], view, epoch_receipt,
                {"file_hashes": hashes, "ignored_paths": quarantined, "constraints": constraints},
                existing_manifest=old_manifest, existing_contract_map=old_map,
            )
            manifest, contract_map = projected["manifest"], projected["contract_map"]
            base = f"plan/bindings/{new_pointer['version']}"
            manifest_ref = {"path": f"{base}/artifact_manifest.json", "sha256": _hash(manifest)}
            map_ref = {"path": f"{base}/contract_map.json", "sha256": _hash(contract_map)}
            receipt = projected["binding_receipt"]
            receipt.update({"manifest_ref": manifest_ref, "contract_map_ref": map_ref})
            receipt_ref = {"path": f"{base}/receipt.json", "sha256": _hash(receipt)}
            binding = {
                "manifest": manifest, "contract_map": contract_map, "receipt": receipt,
                "manifest_ref": manifest_ref, "contract_map_ref": map_ref, "receipt_ref": receipt_ref,
            }
            new_run["stages"]["s5"]["output_refs"]["binding_receipt"] = receipt_ref
            binding_ref: dict[str, Any] | None = receipt_ref
        else:
            rendered = render_e0_files(view, spec, target, bundle["blueprint.json"], constraints)
            rendered_view = {**view, "rendered_files": rendered}
            manifest = build_artifact_manifest(plan_ref, bundle["blueprint.json"], rendered_view, new_pointer["epoch"])
            contract_map = build_contract_map(plan_ref, bundle["blueprint.json"], rendered_view, new_pointer["epoch"])
            binding_ref = None
        ledger = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
        payload = {
            "revision_seq": new_pointer["revision_seq"], "from_version": active["version"],
            "to_version": new_pointer["version"],
            "from_plan_ref": {"path": active["path"], "sha256": active["sha256"]}, "to_plan_ref": plan_ref,
            "level": candidate["level"], "trigger_event_seq": selected["event_seq"],
            "trigger_signature": candidate["selected_trigger"]["signature"],
            "patch_ops": copy.deepcopy(bundle["patch.json"]["patch_ops"]),
            "migration": {key: copy.deepcopy(migration[key]) for key in ("counts", "tasks", "files", "pending_groups", "re_adopt") if key in migration},
            "preservation_rate": migration["preservation_rate"],
            "rework_cost_estimate_usd": rework["estimate"]["cost_usd"],
            "gates": {f"RG-{index}": ("not_applicable" if index == 5 and candidate["level"] == "F2" else "pass") for index in range(1, 6)},
            "epoch_after": new_pointer["epoch"], "activated_at_commit": _git(store._confined("workspace"), "rev-parse", "HEAD"),
            "binding_ref": binding_ref, "pending_materialization": candidate["level"] == "F3",
        }
        new_ledger = copy.deepcopy(ledger)
        new_ledger["entries"].append(build_event_entry(new_ledger, "revision_activated", payload))
        validate_revision_ledger(new_ledger)
        old = {
            "pointer": copy.deepcopy(dict(active)), "state": copy.deepcopy(dict(state)),
            "file_ledger": copy.deepcopy(dict(file_ledger)), "revision_ledger": ledger, "run": old_run,
            "manifest": old_manifest, "contract_map": old_map,
        }
        new = {
            "pointer": new_pointer, "state": new_state, "file_ledger": new_file_ledger,
            "revision_ledger": new_ledger, "run": new_run, "manifest": manifest, "contract_map": contract_map,
        }
        wal = prepare_activation_wal(
            candidate=candidate, candidate_plan=plan, candidate_plan_ref=plan_ref, old=old, new=new,
            gates_ref=gates_ref, binding=binding, rehearsal_ref=rehearsal_ref,
        )
        store.activate_revision_v2(wal, fault_hook=self.fault_hook)
        return StageResult(pause=StagePause(
            "revision_handoff", int(selected["event_seq"]), f"plan/_s4r/candidate_{selected['event_seq']}/candidate.json"
        ))

    def _consume_revision_handoff(
        self,
        context: StageContext,
        *,
        selected: Mapping[str, Any],
        candidate_ref: ArtifactRef,
        plan: Mapping[str, Any],
        active: Mapping[str, Any],
        blueprint: Mapping[str, Any],
        constraints: Mapping[str, Any],
    ) -> StageResult | None:
        """Evaluate RG-1..RG-5 once, then reject or atomically activate."""

        store = context.store
        event_seq = int(selected["event_seq"])
        candidate, bundle, _ = store.read_revision_candidate(event_seq)
        state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
        file_ledger = _load(store, "plan/file_ledger.json", "file-ledger.schema.json")
        ledger = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
        contract_map = _load(store, "plan/contract_map.json", "contract-map.schema.json")
        workspace = store._confined("workspace")
        boundary_hashes = {
            "active_pointer": store._json_artifact_hash("plan/active_plan.json"),
            "plan_state": store._json_artifact_hash("plan/plan_state.json"),
            "file_ledger": store._json_artifact_hash("plan/file_ledger.json"),
            "revision_ledger": store._json_artifact_hash("plan/revision_ledger.json"),
            "workspace_commit": _git(workspace, "rev-parse", "HEAD"),
            "workspace_tree": _git(workspace, "rev-parse", "HEAD^{tree}"),
            "blueprint": _hash(blueprint), "contract_map": _hash(contract_map),
        }

        def assert_frozen_boundary() -> None:
            current = {
                "active_pointer": store._json_artifact_hash("plan/active_plan.json"),
                "plan_state": store._json_artifact_hash("plan/plan_state.json"),
                "file_ledger": store._json_artifact_hash("plan/file_ledger.json"),
                "revision_ledger": store._json_artifact_hash("plan/revision_ledger.json"),
                "workspace_commit": _git(workspace, "rev-parse", "HEAD"),
                "workspace_tree": _git(workspace, "rev-parse", "HEAD^{tree}"),
                "blueprint": _hash(blueprint),
                "contract_map": store._json_artifact_hash("plan/contract_map.json"),
            }
            if current != boundary_hashes:
                raise ArtifactConflict("revision gate authoritative boundary drifted")
            store.verify_ref(candidate_ref, schema_name="revision-candidate.schema.json")
        run_before_gates = store.load_run()
        rejection_snapshot = {
            "pointer": boundary_hashes["active_pointer"], "state": boundary_hashes["plan_state"],
            "file_ledger": boundary_hashes["file_ledger"], "manifest": store._json_artifact_hash("plan/artifact_manifest.json"),
            "contract_map": boundary_hashes["contract_map"],
            "versions": {
                path.name: sha256_bytes(path.read_bytes())
                for path in store._confined("plan/versions").glob("plan-*.json")
            },
            "run_s4_active": copy.deepcopy(run_before_gates["stages"]["s4"]["output_refs"]["active_plan"]),
            "run_s5_outputs": copy.deepcopy(run_before_gates["stages"]["s5"].get("output_refs")),
            "workspace_commit": boundary_hashes["workspace_commit"], "workspace_tree": boundary_hashes["workspace_tree"],
        }
        statuses: dict[str, str] = {}
        reasons: dict[str, str] = {}
        critic_ref: dict[str, str] | None = None
        critic_calls: list[dict[str, str]] = []
        rehearsal_ref: dict[str, str] | None = None
        rework: dict[str, Any] | None = None
        gates_path = f"plan/_s4r/candidate_{event_seq}/gates.json"
        if store._confined(gates_path).is_file():
            accepted_gates = _load(store, gates_path, "revision-gate-result.schema.json")
            if (
                accepted_gates["candidate_id"] != candidate["candidate_id"]
                or accepted_gates["candidate_ref"] != candidate_ref.as_dict()
                or accepted_gates["source_plan_ref"] != candidate["source"]["plan_ref"]
                or accepted_gates["boundary_hashes"] != boundary_hashes
            ):
                raise S6ExecutionError("accepted revision gate evidence conflicts with the current boundary")
            statuses.update({row["gate"]: row["status"] for row in accepted_gates["gates"] if row["status"] != "not_evaluated"})
            reasons.update({row["gate"]: row["reason"] for row in accepted_gates["gates"] if row["reason"] is not None})
            critic_ref = accepted_gates["critic_response_ref"]
            critic_calls = list(accepted_gates["critic_call_refs"])
            rehearsal_ref = accepted_gates["rehearsal_ref"]
            if critic_ref is not None:
                store.verify_ref(critic_ref, schema_name="plan-critic-result.schema.json")
            for ref in critic_calls:
                store.verify_ref(ref)
            if rehearsal_ref is not None:
                store.verify_ref(rehearsal_ref, schema_name="revision-rehearsal.schema.json")

        def fail(gate: str, reason: str) -> None:
            statuses[gate] = "fail"; reasons[gate] = reason

        def checkpoint() -> None:
            assert_frozen_boundary()
            partial = build_gate_result(
                candidate=candidate, candidate_ref=candidate_ref.as_dict(), boundary_hashes=boundary_hashes,
                statuses=statuses, reasons=reasons, critic_response_ref=critic_ref,
                critic_call_refs=critic_calls, rehearsal_ref=rehearsal_ref,
            )
            store.replace_json(gates_path, partial, schema_name="revision-gate-result.schema.json")

        revision_config = context.run["config_snapshot"].get("revision")
        try:
            if "RG-1" in statuses:
                raise StopIteration
            if not isinstance(revision_config, Mapping):
                raise RevisionMechanismError("revision configuration is absent")
            started_attempt = any(
                row.get("status") == "in_progress"
                and isinstance(row.get("attempts"), int)
                and row["attempts"] > 0
                and (
                    not store._confined(f"attempts/{row['task_uid']}/attempt_{row['attempts']:03d}.json").is_file()
                    or _load(store, f"attempts/{row['task_uid']}/attempt_{row['attempts']:03d}.json", "s6-attempt.schema.json").get("status") == "started"
                )
                for row in state["tasks"]
            )
            lease_starts = {entry["payload"]["lease_id"] for entry in ledger["entries"] if entry["event_type"] == "lease_started"}
            lease_finishes = {entry["payload"]["lease_id"] for entry in ledger["entries"] if entry["event_type"] == "lease_finished"}
            pending_group = any(row.get("group_id") and row.get("status") in {"pending", "in_progress"} for row in state["tasks"])
            pending_epoch = any(
                _load(store, path.relative_to(store.root).as_posix(), "s5-pending-state.schema.json").get("phase") != "accepted"
                for path in store._confined("plan/epochs").glob("E*/pending.json")
            ) if store._confined("plan/epochs").is_dir() else False
            if started_attempt or lease_starts - lease_finishes or pending_group or pending_epoch or store._confined("plan/verification_pending.json").exists() or not _clean(workspace):
                raise RevisionMechanismError("revision handoff boundary has an in-flight transaction")
            if candidate["source"]["plan_ref"] != {"path": active["path"], "sha256": active["sha256"]} or candidate["source"]["revision_seq"] != active["revision_seq"]:
                raise RevisionMechanismError("candidate source is no longer active")
            facts = self._revision_facts(store, state, plan, blueprint, contract_map, context.run["config_snapshot"], constraints)
            boundary = project_revision_boundary(
                phase=str(facts.get("boundary_phase", "task_boundary")), revision_seq=int(active["revision_seq"]),
                tasks=state["tasks"], plan_ref={"path": active["path"], "sha256": active["sha256"]}, epoch=str(active["epoch"]),
                state_history_ref={"path": "plan/state_history.json", "sha256": store._json_artifact_hash("plan/state_history.json")},
                workspace_commit=boundary_hashes["workspace_commit"], workspace_tree=boundary_hashes["workspace_tree"],
                blueprint_ref={"path": "plan/_s4/delivery_blueprint.json", "sha256": boundary_hashes["blueprint"]},
                contract_map_ref={"path": "plan/contract_map.json", "sha256": boundary_hashes["contract_map"]},
                file_ledger_ref={"path": "plan/file_ledger.json", "sha256": boundary_hashes["file_ledger"]},
                revision_ledger=ledger, thresholds={"theta_2": revision_config["theta2"], "theta_6": revision_config["theta6"]}, facts=facts,
            )
            evaluation = evaluate_revision_triggers(boundary, ledger, context.run["config_snapshot"])
            selection = evaluation.get("selection")
            expected = selected["payload"]
            if not isinstance(selection, Mapping) or selection.get("level") != candidate["level"] or selection.get("code") != expected.get("hit_code") or selection.get("signature") != expected.get("hit_signature"):
                raise RevisionMechanismError("selected trigger is no longer the same eligible revision")
            statuses["RG-1"] = "pass"
            checkpoint()
        except StopIteration:
            pass
        except (KeyError, RevisionMechanismError) as exc:
            fail("RG-1", str(exc))

        if statuses.get("RG-1") == "pass" and "RG-2" not in statuses:
            try:
                frozen = {
                    "spec_value": _load(store, "spec/spec.json"),
                    "target_profile_value": _load(store, "inputs/target.json"),
                    "test_bundle_value": _load(store, "inputs/test_bundle.json"),
                    "refs": {
                        key: {"path": path, "sha256": context.run["inputs"][key]["sha256"]}
                        for key, path in (("spec", "spec/spec.json"), ("target_profile", "inputs/target.json"), ("test_bundle", "inputs/test_bundle.json"))
                    },
                }
                manifest = build_test_manifest_metadata(frozen["test_bundle_value"], constraints)
                replay = complete_revision_candidate(
                    plan, active, bundle["patch.json"], constraints, frozen, manifest,
                    context.run["config_snapshot"], state, file_ledger,
                    ledger_prefix_sha256=candidate["source"]["ledger_prefix_sha256"],
                )
                if canonical_json_bytes(replay) != canonical_json_bytes(bundle):
                    raise RevisionMechanismError("candidate validation replay differs from committed bytes")
                statuses["RG-2"] = "pass"
                checkpoint()
            except (KeyError, RevisionMechanismError) as exc:
                fail("RG-2", str(exc))

        if statuses.get("RG-2") == "pass" and "RG-3" not in statuses:
            try:
                rework = evaluate_revision_budget(
                    level=candidate["level"], migration=bundle["migration.json"], candidate_plan=bundle["plan.json"],
                    config=context.run["config_snapshot"], run=store.load_run(), revision_ledger=ledger, state=state,
                )
                if not rework["pass"]:
                    failed = ", ".join(key for key, value in rework["checks"].items() if not value)
                    raise RevisionMechanismError(f"revision budget checks failed: {failed}")
                statuses["RG-3"] = "pass"
                checkpoint()
            except (KeyError, RevisionMechanismError, TypeError, ValueError) as exc:
                fail("RG-3", str(exc))
        elif statuses.get("RG-3") == "pass":
            rework = {"estimate": estimate_revision_rework(
                bundle["migration.json"], bundle["plan.json"], context.run["config_snapshot"]
            )}

        if statuses.get("RG-3") == "pass" and "RG-4" not in statuses:
            try:
                critic_path = store._confined(f"plan/_s4r/candidate_{event_seq}/critic.json")
                if critic_path.is_file():
                    review = _load(store, f"plan/_s4r/candidate_{event_seq}/critic.json", "plan-critic-result.schema.json")
                    critic_ref = {"path": f"plan/_s4r/candidate_{event_seq}/critic.json", "sha256": sha256_bytes(critic_path.read_bytes())}
                else:
                    assert_frozen_boundary()
                    context.orchestrator.admit_external_call(store)
                    critic_delta = project_plan_critic_delta(plan, bundle["plan.json"], bundle["patch.json"])
                    result = bind_plan_critic_contract(self.agent).invoke(
                        inputs={
                            "candidate_plan_graph": critic_delta,
                            "coverage_matrix": {"requirements": critic_delta["coverage"], "tests": [
                                row for row in bundle["plan.json"]["coverage"]["tests"]
                                if row.get("task_id") in {task["id"] for task in critic_delta["tasks"]}
                            ]},
                            "lint_report": bundle["lint.json"],
                        },
                        run_id=context.run["run_id"], task_id=f"revision-critic-{event_seq}", stage="S6",
                    )
                    review = result.parsed
                    critic_ref = store.publish_revision_candidate_evidence(
                        event_seq, "critic.json", review, schema_name="plan-critic-result.schema.json"
                    ).as_dict()
                    for ref in _call_refs_for_attempt(store, f"revision-critic-{event_seq}", 1):
                        path = ref["path"]
                        if store._confined(path).is_file():
                            critic_calls.append({"path": path, "sha256": sha256_bytes(store._confined(path).read_bytes())})
                validated = validate_plan_critic_result(review)
                if validated["verdict"] != "pass" or any(item["severity"] in {"blocker", "major"} for item in validated["issues"]):
                    raise RevisionMechanismError("PlanCritic reported blocker or major issues")
                statuses["RG-4"] = "pass"
                checkpoint()
            except (ArtifactConflict, RunStoreError):
                raise
            except Exception as exc:
                fail("RG-4", str(exc))

        if statuses.get("RG-4") == "pass" and "RG-5" not in statuses:
            if candidate["level"] == "F2":
                statuses["RG-5"] = "not_applicable"
                checkpoint()
            else:
                try:
                    assert_frozen_boundary()
                    successor = successor_pointer(active, {"path": "unused", "sha256": _hash(bundle["plan.json"])}, "F3")
                    rehearsal_context = {
                        "run": store.load_run(), "plan": bundle["plan.json"], "spec": _load(store, "spec/spec.json"),
                        "target": _load(store, "inputs/target.json"), "blueprint": bundle["blueprint.json"],
                        "old_blueprint": blueprint, "constraints": constraints, "file_ledger": file_ledger,
                        "migration": bundle["migration.json"], "epoch": successor["epoch"], "plan_version": successor["version"],
                    }
                    materializer = S5MaterializationController(self.executor)
                    runs = []
                    for _ in range(2):
                        assert_frozen_boundary()
                        runs.append(materializer.rehearse_epoch(rehearsal_context, workspace))
                    build_refs: list[list[dict[str, str]]] = []
                    smoke_refs: list[list[dict[str, str]]] = []
                    for index, run in enumerate(runs, start=1):
                        build_refs.append([
                            store.publish_immutable_json(
                                f"plan/_s4r/candidate_{event_seq}/rehearsal-{index}-build-{number}.json", item,
                                schema_name="build-result.schema.json",
                            ).as_dict()
                            for number, item in enumerate(run["build_results"], start=1)
                        ])
                        smoke_refs.append([
                            store.publish_immutable_json(
                                f"plan/_s4r/candidate_{event_seq}/rehearsal-{index}-smoke-{number}.json", item,
                                schema_name="smoke-result.schema.json",
                            ).as_dict()
                            for number, item in enumerate(run["smoke_results"], start=1)
                        ])
                    rehearsal = build_revision_rehearsal(
                        candidate=candidate, baseline_commit=boundary_hashes["workspace_commit"],
                        baseline_tree=boundary_hashes["workspace_tree"], runs=runs,
                        build_result_refs=build_refs, smoke_result_refs=smoke_refs,
                    )
                    rehearsal_ref = store.publish_revision_candidate_evidence(
                        event_seq, "rehearsal.json", rehearsal, schema_name="revision-rehearsal.schema.json"
                    ).as_dict()
                    if _git(workspace, "rev-parse", "HEAD") != boundary_hashes["workspace_commit"] or _git(workspace, "rev-parse", "HEAD^{tree}") != boundary_hashes["workspace_tree"] or not _clean(workspace):
                        raise RevisionMechanismError("live workspace changed during rehearsal")
                    statuses["RG-5"] = "pass"
                    checkpoint()
                except (ArtifactConflict, RunStoreError):
                    raise
                except Exception as exc:
                    fail("RG-5", str(exc))

        if all(statuses.get(f"RG-{index}") in {"pass", "not_applicable"} for index in range(1, 6)):
            assert_frozen_boundary()
            current_run = context.orchestrator.synchronize_budget(store)
            final_rework = evaluate_revision_budget(
                level=candidate["level"], migration=bundle["migration.json"], candidate_plan=bundle["plan.json"],
                config=current_run["config_snapshot"], run=current_run,
                revision_ledger=_load(store, "plan/revision_ledger.json", "revision-ledger.schema.json"),
                state=_load(store, "plan/plan_state.json", "plan-state.schema.json"),
            )
            rework = final_rework
            if not final_rework["pass"]:
                failed = ", ".join(key for key, value in final_rework["checks"].items() if not value)
                statuses = {"RG-1": "pass", "RG-2": "pass", "RG-3": "fail"}
                reasons["RG-3"] = f"revision budget checks changed before activation: {failed}"

        gate_result = build_gate_result(
            candidate=candidate, candidate_ref=candidate_ref.as_dict(), boundary_hashes=boundary_hashes,
            statuses=statuses, reasons=reasons, critic_response_ref=critic_ref,
            critic_call_refs=critic_calls, rehearsal_ref=rehearsal_ref,
        )
        gates_ref = store.replace_json(gates_path, gate_result, schema_name="revision-gate-result.schema.json")
        if gate_result["disposition"] == "rejected":
            assert_frozen_boundary()
            updated = append_candidate_rejected(
                _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json"),
                candidate_id=candidate["candidate_id"], trigger_event_seq=event_seq, level=candidate["level"],
                failed_gate=gate_result["first_failed_gate"],
                reason=reasons[gate_result["first_failed_gate"]], evidence_refs=[gates_ref.as_dict()],
            )
            store.replace_json("plan/revision_ledger.json", updated, schema_name="revision-ledger.schema.json")
            run_after_rejection = store.load_run()
            rejection_after = {
                "pointer": store._json_artifact_hash("plan/active_plan.json"),
                "state": store._json_artifact_hash("plan/plan_state.json"),
                "file_ledger": store._json_artifact_hash("plan/file_ledger.json"),
                "manifest": store._json_artifact_hash("plan/artifact_manifest.json"),
                "contract_map": store._json_artifact_hash("plan/contract_map.json"),
                "versions": {
                    path.name: sha256_bytes(path.read_bytes())
                    for path in store._confined("plan/versions").glob("plan-*.json")
                },
                "run_s4_active": run_after_rejection["stages"]["s4"]["output_refs"]["active_plan"],
                "run_s5_outputs": run_after_rejection["stages"]["s5"].get("output_refs"),
                "workspace_commit": _git(workspace, "rev-parse", "HEAD"),
                "workspace_tree": _git(workspace, "rev-parse", "HEAD^{tree}"),
            }
            if rejection_after != rejection_snapshot:
                raise S6ExecutionError("revision rejection changed authoritative execution state")
            return None
        assert_frozen_boundary()
        return self._activate_revision_handoff(
            context, candidate=candidate, bundle=bundle, selected=selected, active=active,
            state=state, file_ledger=file_ledger, gates_ref=gates_ref.as_dict(),
            rehearsal_ref=rehearsal_ref, rework=rework or {}, constraints=constraints,
        )

    @staticmethod
    def _collect_revision_evidence(
        store: RunStore,
        ledger: Mapping[str, Any],
        activation: Mapping[str, Any],
        affected_uids: set[str],
    ) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
        """Collect only evidence and calls bound to one accepted activation."""

        revision_seq = int(activation["revision_seq"])
        trigger_event_seq = int(activation["trigger_event_seq"])
        evidence: dict[tuple[str, str], dict[str, str]] = {}
        call_keys: set[tuple[str, int]] = {(f"revision-critic-{trigger_event_seq}", 1)}

        def add_ref(value: Mapping[str, Any] | None) -> None:
            if not isinstance(value, Mapping):
                return
            path, digest = value.get("path"), value.get("sha256")
            if not isinstance(path, str) or not isinstance(digest, str):
                raise RevisionMechanismError(
                    "revision evidence reference is incomplete", code="REVISION_ARTIFACT_DAMAGED"
                )
            store.verify_ref({"path": path, "sha256": digest})
            evidence[(path, digest)] = {"path": path, "sha256": digest}

        candidate_root = f"plan/_s4r/candidate_{trigger_event_seq}"
        for name in ("candidate.json", "gates.json", "critic.json"):
            path = f"{candidate_root}/{name}"
            target = store._confined(path)
            if target.is_file():
                add_ref({"path": path, "sha256": sha256_bytes(target.read_bytes())})

        for entry in ledger.get("entries", []):
            payload = entry.get("payload", {})
            if (
                entry.get("event_type") == "verification_committed"
                and payload.get("revision_seq") == revision_seq
                and affected_uids.intersection(str(uid) for uid in payload.get("member_uids", []))
            ):
                for ref in payload.get("evidence_refs", []):
                    add_ref(ref)
                add_ref(payload.get("joint_evidence_ref"))

        attempt_paths = [
            *store._confined("attempts").glob("*/attempt_*.json"),
            *store._confined("attempts").glob("*/amendment.json"),
        ] if store._confined("attempts").is_dir() else []
        for target in sorted(attempt_paths, key=lambda item: item.as_posix().encode("utf-8")):
            path = target.relative_to(store.root).as_posix()
            record = _load(store, path, "s6-attempt.schema.json")
            migration_ref = record.get("migration_ref")
            if (
                record.get("task_uid") not in affected_uids
                or not isinstance(migration_ref, Mapping)
                or migration_ref.get("revision_seq") != revision_seq
            ):
                continue
            add_ref(record.get("output_ref"))
            add_ref(record.get("failure_ref"))
            attempt = int(record.get("attempt", 0))
            if record.get("execution_mode") == "amend":
                attempt = max(1, attempt)
            if attempt > 0:
                call_keys.add((str(record["task_id"]), attempt))

        validation_paths = list(store._confined("validations").glob("*/validation_*.json")) \
            if store._confined("validations").is_dir() else []
        for target in sorted(validation_paths, key=lambda item: item.as_posix().encode("utf-8")):
            path = target.relative_to(store.root).as_posix()
            record = _load(store, path, "s6-validation.schema.json")
            migration_ref = record.get("migration_ref")
            if (
                record.get("task_uid") not in affected_uids
                or not isinstance(migration_ref, Mapping)
                or migration_ref.get("revision_seq") != revision_seq
            ):
                continue
            add_ref(record.get("evidence_ref"))
            add_ref(record.get("failure_ref"))
            for key in ("build_result_refs", "smoke_result_refs"):
                for ref in record.get(key, []):
                    add_ref(ref)

        trace_path = store._confined("trace/llm_calls.ndjson")
        trace_by_key: dict[tuple[str, int], dict[str, Any]] = {}
        if trace_path.is_file():
            for line in trace_path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RevisionMechanismError(
                        "revision call telemetry is invalid", code="REVISION_ARTIFACT_DAMAGED"
                    ) from exc
                if not isinstance(row, Mapping):
                    raise RevisionMechanismError(
                        "revision call telemetry row is invalid", code="REVISION_ARTIFACT_DAMAGED"
                    )
                task_id, attempt = row.get("task_id"), row.get("attempt")
                if (
                    row.get("stage") == "S6"
                    and isinstance(task_id, str)
                    and isinstance(attempt, int)
                ):
                    trace_by_key[(task_id, attempt)] = dict(row)

        selected: dict[str, dict[str, Any]] = {}
        for key in sorted(call_keys, key=lambda item: (item[0].encode("utf-8"), item[1])):
            row = trace_by_key.get(key)
            if row is None:
                continue
            path = row.get("output_path")
            if not isinstance(path, str) or not store._confined(path).is_file():
                raise RevisionMechanismError(
                    "revision call output is unavailable", code="REVISION_ARTIFACT_DAMAGED"
                )
            bound = {**row, "sha256": sha256_bytes(store._confined(path).read_bytes())}
            prior = selected.get(path)
            if prior is not None and prior != bound:
                raise RevisionMechanismError(
                    "revision call output identity conflicts", code="REVISION_ARTIFACT_DAMAGED"
                )
            selected[path] = bound
        return (
            [evidence[key] for key in sorted(evidence, key=lambda item: (item[0].encode("utf-8"), item[1]))],
            [selected[path] for path in sorted(selected, key=lambda value: value.encode("utf-8"))],
        )

    def _revision_handoff(
        self,
        context: StageContext,
        plan: Mapping[str, Any],
        active: Mapping[str, Any],
        blueprint: Mapping[str, Any],
        constraints: Mapping[str, Any],
    ) -> StageResult | None:
        store = context.store
        ledger = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
        consumed_trigger_events = {
            int(entry["payload"]["trigger_event_seq"])
            for entry in ledger["entries"]
            if entry.get("event_type") in {"candidate_rejected", "revision_activated"}
            and isinstance(entry.get("payload", {}).get("trigger_event_seq"), int)
        }
        selected_entries = [
            entry for entry in ledger["entries"]
            if entry.get("event_type") == "trigger_evaluated"
            and entry.get("payload", {}).get("selected") is True
            and int(entry["event_seq"]) not in consumed_trigger_events
        ]
        if selected_entries:
            selected = selected_entries[-1]
            event_seq = int(selected["event_seq"])
            reconciled = store.reconcile_revision_candidate(event_seq)
            if reconciled is None:
                return StageResult(pause=StagePause("revision_handoff", event_seq, None))
            budgets = context.run["config_snapshot"].get("budgets", {})
            if not budgets.get("revision_f2_limit", 0) and not budgets.get("revision_f3_limit", 0):
                return StageResult(pause=StagePause("revision_handoff", event_seq, reconciled.path))
            return self._consume_revision_handoff(
                context, selected=selected, candidate_ref=reconciled, plan=plan,
                active=active, blueprint=blueprint, constraints=constraints,
            )
        revision_config = context.run["config_snapshot"].get("revision")
        if not isinstance(revision_config, Mapping):
            return None
        state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
        started_attempt = False
        for row in state["tasks"]:
            if row.get("status") != "in_progress" or not isinstance(row.get("attempts"), int) or row["attempts"] < 1:
                continue
            attempt_path = f"attempts/{row['task_uid']}/attempt_{row['attempts']:03d}.json"
            if not store._confined(attempt_path).is_file() or _load(store, attempt_path, "s6-attempt.schema.json").get("status") == "started":
                started_attempt = True
                break
        lease_starts = {
            str(entry.get("payload", {}).get("lease_id"))
            for entry in ledger.get("entries", []) if entry.get("event_type") == "lease_started"
        }
        lease_finishes = {
            str(entry.get("payload", {}).get("lease_id"))
            for entry in ledger.get("entries", []) if entry.get("event_type") == "lease_finished"
        }
        unresolved_group = any(row.get("group_id") is not None and row.get("status") in {"pending", "in_progress"} for row in state["tasks"])
        revision_wal = any(
            _load(store, path.relative_to(store.root).as_posix(), "plan-activation.schema.json").get("phase") != "reconciled"
            for path in store._confined("plan/_s4r").glob("candidate_*/activation.json")
        ) if store._confined("plan/_s4r").is_dir() else False
        if started_attempt or lease_starts - lease_finishes or unresolved_group or revision_wal or store._confined("plan/verification_pending.json").exists():
            return None
        state_history_path = "plan/state_history.json"
        if not store._confined(state_history_path).is_file():
            return None
        availability = project_revision_availability(ledger, context.run["config_snapshot"])
        pending_revision = availability["pending_evaluation"]
        if pending_revision is not None:
            activation_entry = next(
                (
                    entry for entry in ledger["entries"]
                    if entry.get("event_type") == "revision_activated"
                    and entry.get("payload", {}).get("revision_seq") == pending_revision
                ),
                None,
            )
            if activation_entry is None:
                raise S6ExecutionError("pending revision evaluation lost its activation")
            activation = activation_entry["payload"]
            trigger_entry = next(
                (
                    entry for entry in ledger["entries"]
                    if entry.get("event_seq") == activation.get("trigger_event_seq")
                    and entry.get("event_type") == "trigger_evaluated"
                ),
                None,
            )
            if trigger_entry is None:
                raise S6ExecutionError("pending revision evaluation lost its trigger")
            from_ref = activation["from_plan_ref"]
            to_ref = activation["to_plan_ref"]
            store.verify_ref(from_ref, schema_name="plan.schema.json")
            store.verify_ref(to_ref, schema_name="plan.schema.json")
            from_plan = _load(store, from_ref["path"], "plan.schema.json")
            to_plan = _load(store, to_ref["path"], "plan.schema.json")
            scope = derive_revision_obligation_scope(from_plan, to_plan, activation)

            history = _load(store, state_history_path, "state-history.schema.json")
            evaluation_history_path = f"plan/revision_evaluations/revision_{pending_revision}/state_history.json"
            evaluation_history_ref = {"path": evaluation_history_path, "sha256": _hash(history)}
            evidence_refs = [evaluation_history_ref]
            affected_uids = set(scope["affected_task_uids"])
            for row in state["tasks"]:
                ref = row.get("acceptance_evidence", {}).get("task_evidence_ref")
                if row.get("task_uid") in affected_uids and isinstance(ref, Mapping):
                    store.verify_ref(ref)
                    evidence_refs.append(dict(ref))
            related_evidence, trace_rows = self._collect_revision_evidence(
                store, ledger, activation, affected_uids,
            )
            evidence_refs.extend(related_evidence)
            hard_budget = int(state.get("s6_attempts_used", 0)) >= int(
                context.run["config_snapshot"]["budgets"]["s6_total_attempts_cap"]
            )
            try:
                context.orchestrator.synchronize_budget(store)
            except BudgetExhausted:
                hard_budget = True

            workspace = store._confined("workspace")
            contract_map = _load(store, "plan/contract_map.json", "contract-map.schema.json")
            facts = self._revision_facts(store, state, plan, blueprint, contract_map, context.run["config_snapshot"], constraints)
            boundary = project_revision_boundary(
                phase=str(facts.get("boundary_phase", "task_boundary")), revision_seq=int(active["revision_seq"]),
                tasks=state["tasks"], plan_ref={"path": active["path"], "sha256": active["sha256"]}, epoch=str(active["epoch"]),
                state_history_ref=evidence_refs[0], workspace_commit=_git(workspace, "rev-parse", "HEAD"),
                workspace_tree=_git(workspace, "rev-parse", "HEAD^{tree}"),
                blueprint_ref={"path": "plan/_s4/delivery_blueprint.json", "sha256": _hash(blueprint)},
                contract_map_ref={"path": "plan/contract_map.json", "sha256": store._json_artifact_hash("plan/contract_map.json")},
                file_ledger_ref={"path": "plan/file_ledger.json", "sha256": store._json_artifact_hash("plan/file_ledger.json")},
                revision_ledger=ledger, thresholds={"theta_2": revision_config["theta2"], "theta_6": revision_config["theta6"]}, facts=facts,
            )
            current = evaluate_revision_triggers(boundary, ledger, context.run["config_snapshot"])
            projected = project_revision_evaluation(
                activation=activation, trigger=trigger_entry["payload"],
                obligation_anchors=scope["obligation_anchors"],
                affected_task_uids=scope["affected_task_uids"],
                state=state, state_history=history["entries"],
                current_signatures=[hit["signature"] for hit in current["hits"]],
                hard_budget_exhausted=hard_budget, evidence_refs=evidence_refs, call_rows=trace_rows,
                ledger_prefix_sha256=_hash(ledger),
            )
            if projected is None:
                return None
            published_history = store.publish_immutable_json(
                evaluation_history_path,
                history,
                schema_name="state-history.schema.json",
            )
            if published_history.as_dict() != evaluation_history_ref:
                raise ArtifactConflict("revision evaluation State-history snapshot changed before append")
            ledger = append_revision_evaluated(ledger, **projected)
            store.replace_json("plan/revision_ledger.json", ledger, schema_name="revision-ledger.schema.json")
        workspace = store._confined("workspace")
        contract_map = _load(store, "plan/contract_map.json", "contract-map.schema.json")
        facts = self._revision_facts(store, state, plan, blueprint, contract_map, context.run["config_snapshot"], constraints)
        boundary = project_revision_boundary(
            phase=str(facts.get("boundary_phase", "task_boundary")),
            revision_seq=int(active["revision_seq"]),
            tasks=state["tasks"],
            plan_ref={"path": active["path"], "sha256": active["sha256"]},
            epoch=str(active["epoch"]),
            state_history_ref={"path": state_history_path, "sha256": store._json_artifact_hash(state_history_path)},
            workspace_commit=_git(workspace, "rev-parse", "HEAD"),
            workspace_tree=_git(workspace, "rev-parse", "HEAD^{tree}"),
            blueprint_ref={"path": "plan/_s4/delivery_blueprint.json", "sha256": _hash(blueprint)},
            contract_map_ref={"path": "plan/contract_map.json", "sha256": store._json_artifact_hash("plan/contract_map.json")},
            file_ledger_ref={"path": "plan/file_ledger.json", "sha256": store._json_artifact_hash("plan/file_ledger.json")},
            revision_ledger=ledger,
            thresholds={
                "theta_2": revision_config["theta2"],
                "theta_6": revision_config["theta6"],
            },
            facts=facts,
        )
        evaluation = evaluate_revision_triggers(boundary, ledger, context.run["config_snapshot"])
        if not evaluation["hits"]:
            return None
        new_ledger = append_trigger_batch(ledger, evaluation)
        selection = evaluation["selection"]
        candidate_ref: str | None = None
        if selection is not None:
            selected_index = next(index for index, hit in enumerate(evaluation["hits"]) if hit["selected"])
            event_seq = len(ledger["entries"]) + selected_index + 1
            context.orchestrator.synchronize_budget(store)
            if self.revision_patch_provider is not None:
                source_ir = plan_to_draft_ir(plan)
                provider_input = {
                    "source_plan": copy.deepcopy(dict(plan)),
                    "source_plan_ref": copy.deepcopy(dict(active)),
                    "source_plan_draft_ir": source_ir,
                    "selected_event_seq": event_seq,
                    "selection": copy.deepcopy(selection),
                    "evaluation": copy.deepcopy(evaluation),
                }
                patch = self.revision_patch_provider(provider_input)
                if patch is not None:
                    spec = _load(store, "spec/spec.json")
                    target = _load(store, "inputs/target.json")
                    test_bundle = _load(store, "inputs/test_bundle.json")
                    test_manifest = build_test_manifest_metadata(test_bundle, constraints)
                    frozen = {
                        "spec_value": spec, "target_profile_value": target, "test_bundle_value": test_bundle,
                        "refs": {
                            key: {"path": path, "sha256": context.run["inputs"][key]["sha256"]}
                            for key, path in (("spec", "spec/spec.json"), ("target_profile", "inputs/target.json"), ("test_bundle", "inputs/test_bundle.json"))
                        },
                    }
                    bundle = complete_revision_candidate(
                        plan, active, patch, constraints, frozen, test_manifest, context.run["config_snapshot"], state,
                        _load(store, "plan/file_ledger.json", "file-ledger.schema.json"), ledger_prefix_sha256=evaluation["ledger_prefix_sha256"],
                    )
                    store.stage_revision_candidate(event_seq, bundle)
                    self._fault("revision_candidate_staged")
            current_ledger = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
            if _hash(current_ledger) != evaluation["ledger_prefix_sha256"]:
                raise ArtifactConflict("revision availability changed before candidate publication")
            current_availability = project_revision_availability(current_ledger, context.run["config_snapshot"])
            if (
                current_availability["revision_locked"]
                or current_availability["pending_evaluation"] is not None
                or current_availability["level_closed"][selection["level"]]
                or (selection["signature"], selection["level"]) in {
                    tuple(pair) for pair in current_availability["attempted_pairs"]
                }
            ):
                raise RevisionMechanismError("revision selection closed before candidate publication")
            context.orchestrator.synchronize_budget(store)
            store.replace_json("plan/revision_ledger.json", new_ledger, schema_name="revision-ledger.schema.json")
            self._fault("revision_trigger_ledger_replaced")
            if self.revision_patch_provider is not None and store._confined(f"plan/_s4r/.candidate_{event_seq}.pending").is_dir():
                context.orchestrator.synchronize_budget(store)
                candidate_ref = store.commit_revision_candidate(event_seq).path
                self._fault("revision_candidate_committed")
            return StageResult(pause=StagePause("revision_handoff", event_seq, candidate_ref))
        store.replace_json("plan/revision_ledger.json", new_ledger, schema_name="revision-ledger.schema.json")
        self._fault("revision_trigger_ledger_replaced")
        return None

    def _checked_revision_handoff(
        self,
        context: StageContext,
        plan: Mapping[str, Any],
        active: Mapping[str, Any],
        blueprint: Mapping[str, Any],
        constraints: Mapping[str, Any],
    ) -> StageResult | None:
        """Map deterministic revision-artifact damage to the existing failed route."""

        try:
            return self._revision_handoff(context, plan, active, blueprint, constraints)
        except (ArtifactConflict, RunValidationError, PlanRevisionError) as exc:
            raise ControlledStageFailure({
                "code": "S6_ADMISSION_INVALID",
                "detail": f"revision evaluation artifact is invalid: {exc}",
            }) from exc
        except RevisionMechanismError as exc:
            if exc.code not in {"REVISION_ARTIFACT_DAMAGED", "REVISION_LEDGER_CONFLICT"}:
                raise
            raise ControlledStageFailure({
                "code": "S6_ADMISSION_INVALID",
                "detail": f"revision evaluation artifact is invalid: {exc}",
            }) from exc

    def run(self, context: StageContext) -> StageResult:
        store = context.store
        self.reconcile_verification_wal(store)
        receipt_path = store._confined("plan/s6_receipt.json")
        if receipt_path.exists():
            receipt = _load(store, "plan/s6_receipt.json", "s6-receipt.schema.json")
            run = store.load_run()
            if (
                receipt.get("active_plan_ref") == run["stages"]["s4"]["output_refs"].get("active_plan")
                and receipt.get("binding_ref") == run["stages"]["s5"]["output_refs"].get("binding_receipt")
            ):
                receipt_ref = ArtifactRef("plan/s6_receipt.json", store._json_artifact_hash("plan/s6_receipt.json"))
                self.verify_completed(store)
                return StageResult(output_refs={"s6_receipt": receipt_ref})
            old_hash = receipt.get("active_plan_ref", {}).get("sha256")
            if not isinstance(old_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", old_hash):
                raise S6ExecutionError("stale S6 receipt has no canonical active-Plan hash")
            store.publish_immutable_json(
                f"plan/s6_receipts/{old_hash}.json",
                receipt,
                schema_name="s6-receipt.schema.json",
            )
            receipt_path.unlink()
        try:
            run, plan, active, blueprint, constraints, epoch = self._admit(store)
        except (S6AdmissionError, RunStoreError, S6ExecutionError) as exc:
            raise ControlledStageFailure({"code": "S6_ADMISSION_INVALID", "detail": str(exc)}) from exc
        handoff = self._checked_revision_handoff(context, plan, active, blueprint, constraints)
        if handoff is not None:
            return handoff
        while True:
            self._propagate_dependency_blocks(store, plan)
            handoff = self._checked_revision_handoff(context, plan, active, blueprint, constraints)
            if handoff is not None:
                return handoff
            state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
            unresolved_groups = sorted(
                {str(row["group_id"]) for row in state["tasks"] if row.get("group_id") is not None and row["status"] in {"pending", "in_progress"}},
                key=lambda value: value.encode("utf-8"),
            )
            pending_groups = unresolved_groups if unresolved_groups else None
            failed_group = any(
                row.get("group_id") is not None and row.get("status") in {"blocked", "blocked_by_dependency"}
                for row in state["tasks"]
            )
            allowed_task_ids = None
            if failed_group and not pending_groups:
                activation = latest_activation(_load(store, "plan/revision_ledger.json", "revision-ledger.schema.json"))
                allowed_task_ids = {
                    str(candidate["id"])
                    for candidate in plan.get("tasks", [])
                    if self._independent_after_group_failure(candidate, plan, state, blueprint, activation)
                }
            task = self._choose(
                plan, state, pending_group_ids=pending_groups, allowed_task_ids=allowed_task_ids,
            )
            if task is None:
                handoff = self._checked_revision_handoff(context, plan, active, blueprint, constraints)
                if handoff is not None:
                    return handoff
                if any(row["status"] == "pending" for row in state["tasks"]):
                    raise ControlledStageFailure({"code": "EXECUTION_UNRESOLVED", "detail": "no executable task remains in the current dependency graph"})
                break
            row = next(item for item in state["tasks"] if item["id"] == task["id"])
            if row.get("group_id") is not None:
                self._run_group(context, plan, blueprint, constraints, epoch, str(row["group_id"]))
            else:
                self._run_task(context, plan, blueprint, constraints, task)
            handoff = self._checked_revision_handoff(context, plan, active, blueprint, constraints)
            if handoff is not None:
                return handoff
        final = self._finalize(context, plan, active, blueprint, constraints, epoch)
        return StageResult(output_refs=final.output_refs)

    def verify_completed(self, store: RunStore) -> None:
        self.reconcile_verification_wal(store)
        run = store.load_run()
        receipt = _load(store, "plan/s6_receipt.json", "s6-receipt.schema.json")
        store.verify_ref({"path": "plan/s6_receipt.json", "sha256": store._json_artifact_hash("plan/s6_receipt.json")}, schema_name="s6-receipt.schema.json")
        store.verify_ref(receipt["active_plan_ref"], schema_name="active-plan.schema.json")
        store.verify_ref(receipt["binding_ref"], schema_name="binding-receipt.schema.json")
        store.verify_ref(receipt["plan_state_ref"], schema_name="plan-state.schema.json")
        store.verify_ref(receipt["state_history_ref"], schema_name="state-history.schema.json")
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
        revision = _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json")
        finished_leases = {
            str(entry.get("payload", {}).get("lease_id"))
            for entry in revision.get("entries", [])
            if entry.get("event_type") == "lease_finished"
        }
        for row in state["tasks"]:
            if row["status"] != "in_progress" or row["last_error"] is not None:
                continue
            if row.get("group_id") is not None or row.get("execution_mode") in {"amend", "revalidate"}:
                continue
            attempt = row["attempts"]
            attempt_path = store._confined(f"attempts/{row['task_uid']}/attempt_{attempt:03d}.json")
            attempt_record = _load(store, f"attempts/{row['task_uid']}/attempt_{attempt:03d}.json", "s6-attempt.schema.json") if attempt_path.exists() else {}
            lease = attempt_record.get("lease") if isinstance(attempt_record, Mapping) else None
            lease_id = str(lease.get("lease_id")) if isinstance(lease, Mapping) and lease.get("lease_id") else None
            directory = store._confined(f"attempts/{row['task_uid']}/attempt_{attempt:03d}")
            record = directory / "failure.json"
            if record.exists():
                if lease_id is not None and lease_id not in finished_leases:
                    self._finish_failed_lease(store, lease_id, "ATTEMPT_INTERRUPTED")
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
            if lease_id is not None and lease_id not in finished_leases:
                self._finish_failed_lease(store, lease_id, "ATTEMPT_INTERRUPTED")


__all__ = ["S6AdmissionError", "S6ExecutionController", "S6ExecutionError"]
