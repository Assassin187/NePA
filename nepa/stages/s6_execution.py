"""M1-6 ordinary S6 execution controller."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator

from ..agents.s6 import CODER_INPUTS, FIXER_INPUTS, LEASE_FIXER_INPUTS, S6AgentError, candidate_tree_hash, coding_contract, normalize_candidate, project_s6_context
from ..agents.base import AgentInvoker
from ..llm.client import StructuredOutputError
from ..orchestrator import BudgetExhausted, ControlledStageFailure, StageContext, StageResult
from ..run_store import ArtifactRef, RunStore, RunStoreError, sha256_bytes
from ..schemas import load_schema
from ..speclib.lint import canonical_json_bytes
from ..speclib.delivery import compile_delivery_blueprint, compile_delivery_constraints
from ..speclib.materialization import MaterializationError, attribute_pending_repair, build_artifact_manifest, build_contract_map, derive_rendering_view, parse_c99_declaration, render_e0_files, validate_completed_epoch
from ..speclib.plan import blueprint_task_semantic_projection
from ..speclib.plan_state import PlanStateError, execution_state_lint, initialize_plan_state, plan_state_snapshot_lint, project_state_transition, validate_lease_authorization
from ..speclib.plan_revision import append_lease_finished, append_verification_committed, latest_activation, validate_file_ledger, validate_revision_ledger
from ..tools.build import _tree_sha256, run_build_variants, run_smoke_checks
from ..tools.git_ops import GitOperationError, prepare_joint_commit, prepare_task_commit, publish_joint_commit, publish_task_commit
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

    def __init__(self, agent: AgentInvoker, executor: Any | None = None, *, fault_hook: Any | None = None, lease_authorization_provider: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None) -> None:
        self.agent = agent
        self.executor = executor
        self.fault_hook = fault_hook
        self.lease_authorization_provider = lease_authorization_provider

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

    def _choose(self, plan: Mapping[str, Any], state: Mapping[str, Any], *, pending_group_ids: list[str] | None = None) -> Mapping[str, Any] | None:
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
            if row["status"] not in {"pending", "in_progress"}:
                continue
            if required_groups and row.get("group_id") not in required_groups:
                continue
            dependencies = task.get("depends_on", [])
            if all(rows.get(dependency, {}).get("status") == "done" for dependency in dependencies):
                return task
        return None

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
                self._exhaust_group(store, plan, descriptor, allocations, build_refs, smoke_refs)
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
    ) -> None:
        state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
        failure_ref = store.publish_immutable_json(
            f"groups/{descriptor['group_id']}/failure.json",
            {"code": "GROUP_VALIDATION_EXHAUSTED", "build_result_refs": build_refs, "smoke_result_refs": smoke_refs},
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
            request = {
                "plan": copy.deepcopy(dict(plan)),
                "state": copy.deepcopy(state),
                "file_ledger": _load(store, "plan/file_ledger.json", "file-ledger.schema.json"),
                "revision_ledger": _load(store, "plan/revision_ledger.json", "revision-ledger.schema.json"),
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
            error = {"attempt": attempt, "code": "AGENT_FAILURE", "detail": str(exc), "candidate": {}, "model_output_ref": model_ref.as_dict() if model_ref else None, "feedback": {"error": str(exc)}, "diagnosis": {}}
            self._record_failure(store, task, attempt, error)
            call_refs = _call_refs_for_attempt(store, task["id"], attempt)
            self._finish_failed_lease(store, lease_id, str(exc), call_refs or ([{"path": model_ref.path}] if model_ref else []))
            return
        files: dict[str, bytes] = {}
        candidate_refs: dict[str, ArtifactRef] = {}
        builds: list[Mapping[str, Any]] = []
        smokes: list[Mapping[str, Any]] = []
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
                error = {"attempt": attempt, "code": "CANDIDATE_VALIDATION_FAILED", "detail": "build or smoke failed", "candidate": {path: content.decode("utf-8") for path, content in files.items()}, "candidate_manifest_ref": candidate_manifest_ref.as_dict() if candidate_manifest_ref else None, "candidate_refs": {path: ref.as_dict() for path, ref in candidate_refs.items()}, "candidate_tree": _tree_sha256(candidate), "model_output_ref": model_output_ref.as_dict(), "feedback": {"build": builds, "smoke": smokes}, "diagnosis": {}}
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
            error = {"attempt": attempt, "code": "CANDIDATE_FAILURE", "detail": str(exc), "candidate": candidate_text, "candidate_manifest_ref": candidate_manifest_ref.as_dict() if candidate_manifest_ref else None, "candidate_refs": {path: ref.as_dict() for path, ref in candidate_refs.items()}, "model_output_ref": model_output_ref.as_dict(), "feedback": {"build": builds, "smoke": smokes, "error": str(exc)}, "diagnosis": {}}
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
        while True:
            self._propagate_dependency_blocks(store, plan)
            state = _load(store, "plan/plan_state.json", "plan-state.schema.json")
            unresolved_groups = sorted(
                {str(row["group_id"]) for row in state["tasks"] if row.get("group_id") is not None and row["status"] in {"pending", "in_progress"}},
                key=lambda value: value.encode("utf-8"),
            )
            pending_groups = unresolved_groups if unresolved_groups else None
            task = self._choose(plan, state, pending_group_ids=pending_groups)
            if task is None:
                if any(row["status"] == "pending" for row in state["tasks"]):
                    raise ControlledStageFailure({"code": "EXECUTION_UNRESOLVED", "detail": "no executable task remains in the current dependency graph"})
                break
            row = next(item for item in state["tasks"] if item["id"] == task["id"])
            if row.get("group_id") is not None:
                self._run_group(context, plan, blueprint, constraints, epoch, str(row["group_id"]))
            else:
                self._run_task(context, plan, blueprint, constraints, task)
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
