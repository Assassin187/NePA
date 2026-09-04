"""Deterministic PlanDraftIR normalization, linking, coverage, and lint."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from ..schemas import load_schema
from .delivery import DeliveryConstraintError, canonical_delivery_blueprint, compile_delivery_blueprint
from .lint import canonical_json_bytes, lint_spec, lint_target, lint_test_bundle


class PlanError(ValueError):
    """A deterministic Plan input or derived relation is invalid."""

    def __init__(self, message: str, *, code: str = "PLAN_INVALID") -> None:
        self.code = code
        super().__init__(message)


def _utf8(value: Any) -> bytes:
    return str(value).encode("utf-8")


def _sorted(values: Any) -> list[Any]:
    return sorted(values, key=_utf8)


def _read(value: Any, label: str) -> Any:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        return json.loads(Path(value).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlanError(f"unable to read {label}: {exc}", code="PLAN_INPUT_INVALID") from exc


def _schema_errors(value: Any, schema_name: str) -> list[str]:
    errors = list(Draft202012Validator(load_schema(schema_name)).iter_errors(value))
    return [error.message for error in sorted(errors, key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message))]


def _ref(kind: str, value: Any) -> dict[str, str]:
    return {"path": f"<memory>/{kind}.json", "sha256": hashlib.sha256(canonical_json_bytes(value)).hexdigest()}


def _responsibility_map(items: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items or []:
        if not isinstance(item, Mapping) or not isinstance(item.get("req_id"), str) or item.get("role") not in {"primary", "supporting"}:
            raise PlanError("requirement responsibilities must contain req_id and role", code="PLAN_RESPONSIBILITY_INVALID")
        req_id = item["req_id"]
        if req_id in result:
            raise PlanError(f"requirement {req_id!r} is declared more than once", code="PLAN_RESPONSIBILITY_INVALID")
        result[req_id] = item["role"]
    return result


def _normalize_shards(task_shards: Any) -> list[dict[str, Any]]:
    if isinstance(task_shards, Mapping):
        if "task_shards" in task_shards:
            task_shards = task_shards["task_shards"]
        elif "work_package_id" in task_shards and "tasks" in task_shards:
            task_shards = [task_shards]
        else:
            task_shards = [dict(value, work_package_id=key) for key, value in task_shards.items()]
    if not isinstance(task_shards, list):
        raise PlanError("task_shards must be an array", code="PLAN_SHARD_INVALID")
    normalized: list[dict[str, Any]] = []
    for index, shard in enumerate(task_shards):
        if not isinstance(shard, Mapping):
            raise PlanError(f"task shard {index} must be an object", code="PLAN_SHARD_INVALID")
        item = copy.deepcopy(dict(shard))
        item.setdefault("schema_version", "1.0")
        errors = _schema_errors(item, "task-shard.schema.json")
        if errors:
            raise PlanError(f"task shard {index} failed Schema validation: {'; '.join(errors)}", code="PLAN_SHARD_SCHEMA_INVALID")
        normalized.append(item)
    return normalized


def normalize_plan_draft(
    architecture: Mapping[str, Any],
    work_packages: list[Mapping[str, Any]] | None = None,
    task_shards: Any = None,
    *,
    constraints: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize exactly one state-free local task shard per work package."""

    architecture = _read(architecture, "ArchitectureDraft")
    if _schema_errors(architecture, "architecture-draft.schema.json"):
        raise PlanError("architecture failed Schema validation", code="PLAN_ARCHITECTURE_INVALID")
    packages = copy.deepcopy(list(work_packages if work_packages is not None else architecture.get("work_packages", [])))
    if not isinstance(packages, list):
        raise PlanError("work_packages must be an array", code="PLAN_WORK_PACKAGE_INVALID")
    modules = {item.get("id"): item for item in architecture.get("modules", []) if isinstance(item, Mapping)}
    architecture_packages = {item.get("id"): item for item in architecture.get("work_packages", []) if isinstance(item, Mapping)}
    package_by_id: dict[str, dict[str, Any]] = {}
    for item in packages:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str) or item["id"] in package_by_id:
            raise PlanError("work package ids must be unique", code="PLAN_WORK_PACKAGE_INVALID")
        if item.get("module") not in modules:
            raise PlanError(f"work package {item.get('id')!r} names an unknown module", code="PLAN_MODULE_UNKNOWN")
        package_by_id[item["id"]] = item
    if architecture_packages and set(architecture_packages) != set(package_by_id):
        raise PlanError("supplied work packages do not equal the accepted architecture work packages", code="PLAN_WORK_PACKAGE_SET_MISMATCH")
    known_contracts = {item.get("id") for item in architecture.get("contracts", []) if isinstance(item, Mapping)}
    for package in package_by_id.values():
        if not set(package.get("provides_contracts", [])).issubset(known_contracts) or not set(package.get("consumes_contracts", [])).issubset(known_contracts):
            raise PlanError(f"work package {package['id']!r} names an unknown contract", code="PLAN_CONTRACT_UNKNOWN")
        if not set(package.get("depends_on", [])).issubset(set(package_by_id)):
            raise PlanError(f"work package {package['id']!r} names an unknown dependency", code="PLAN_WORK_PACKAGE_DEPENDENCY_INVALID")
    module_files: dict[str, set[str]] = defaultdict(set)
    module_provides: dict[str, set[str]] = defaultdict(set)
    module_consumes: dict[str, set[str]] = defaultdict(set)
    package_files_seen: set[str] = set()
    for package in package_by_id.values():
        files = set(package.get("allowed_files", []))
        if package_files_seen.intersection(files) or not files.issubset(set(modules[package["module"]].get("owns_files", []))):
            raise PlanError(f"work package {package['id']!r} does not form a disjoint module file partition", code="PLAN_WORK_PACKAGE_FILE_PARTITION")
        package_files_seen.update(files)
        module_files[package["module"]].update(files)
        module_provides[package["module"]].update(package.get("provides_contracts", []))
        module_consumes[package["module"]].update(package.get("consumes_contracts", []))
    for module_id, module in modules.items():
        if module_files[module_id] != set(module.get("owns_files", [])):
            raise PlanError(f"module {module_id!r} files are not completely assigned to work packages", code="PLAN_WORK_PACKAGE_FILE_PARTITION")
        if module_provides[module_id] != set(module.get("provides_contracts", [])) or module_consumes[module_id] != set(module.get("consumes_contracts", [])):
            raise PlanError(f"module {module_id!r} contract projection is not the work-package union", code="PLAN_CONTRACT_PARTITION_INVALID")
    shards = _normalize_shards(task_shards)
    shard_by_package: dict[str, dict[str, Any]] = {}
    for shard in shards:
        package_id = shard["work_package_id"]
        if package_id in shard_by_package:
            raise PlanError(f"work package {package_id!r} has more than one task shard", code="PLAN_SHARD_DUPLICATE")
        if package_id not in package_by_id:
            raise PlanError(f"task shard names unknown work package {package_id!r}", code="PLAN_WORK_PACKAGE_UNKNOWN")
        shard_by_package[package_id] = shard
    if set(shard_by_package) != set(package_by_id):
        missing = _sorted(set(package_by_id) - set(shard_by_package))
        extra = _sorted(set(shard_by_package) - set(package_by_id))
        raise PlanError(f"task shard set is not exact (missing={missing}, extra={extra})", code="PLAN_SHARD_SET_MISMATCH")

    allowed_variants = set((constraints or {}).get("build_variant_ids", []))
    for package_id in _sorted(package_by_id):
        package = package_by_id[package_id]
        package_responsibilities = _responsibility_map(package.get("requirement_responsibilities"))
        allowed_files = set(package.get("allowed_files", []))
        used_files: set[str] = set()
        provided: set[str] = set()
        consumed: set[str] = set()
        local_ids: set[str] = set()
        for task_index, task in enumerate(shard_by_package[package_id]["tasks"]):
            forbidden = {"id", "task_uid", "obligation_digest", "guidance_digest", "status", "attempts", "notes", "coverage", "hashes"}
            if forbidden.intersection(task):
                field = _sorted(forbidden.intersection(task))[0]
                raise PlanError(f"task shard cannot declare final/runtime field {field!r}", code="PLAN_SHARD_STATEFUL")
            local_id = task["local_id"]
            if local_id in local_ids:
                raise PlanError(f"duplicate local task id {local_id!r}", code="PLAN_LOCAL_TASK_DUPLICATE")
            local_ids.add(local_id)
            files = set(task["deliverable_files"])
            if len(files) > 4 or not files.issubset(allowed_files) or used_files.intersection(files):
                raise PlanError(f"task {local_id!r} does not form a disjoint package file partition", code="PLAN_TASK_FILE_PARTITION")
            used_files.update(files)
            task_responsibilities = _responsibility_map(task.get("requirement_responsibilities"))
            if not set(task_responsibilities).issubset(package_responsibilities) or any(task_responsibilities[key] != package_responsibilities[key] for key in task_responsibilities):
                raise PlanError(f"task {local_id!r} claims a package-external or mismatched responsibility", code="PLAN_TASK_RESPONSIBILITY_INVALID")
            provided.update(task.get("provides_contracts", []))
            consumed.update(task.get("consumes_contracts", []))
            if not set(task.get("provides_contracts", [])).issubset(set(package.get("provides_contracts", []))) or not set(task.get("consumes_contracts", [])).issubset(set(package.get("consumes_contracts", []))):
                raise PlanError(f"task {local_id!r} claims a package-external contract", code="PLAN_TASK_CONTRACT_INVALID")
            if task["acceptance"]["tests"] != []:
                raise PlanError("M2-0 has not authorized task acceptance tests", code="PLAN_TASK_ACCEPTANCE_INVALID")
            if allowed_variants and not set(task["acceptance"]["build_variant_ids"]).issubset(allowed_variants):
                raise PlanError(f"task {local_id!r} uses an unavailable build variant", code="PLAN_BUILD_VARIANT_INVALID")
            if any(dependency not in local_ids and dependency not in {candidate["local_id"] for candidate in shard_by_package[package_id]["tasks"]} for dependency in task.get("depends_on", [])):
                raise PlanError(f"task {local_id!r} has an unknown local dependency", code="PLAN_LOCAL_DEPENDENCY_INVALID")
        if used_files != allowed_files:
            raise PlanError(f"work package {package_id!r} files are not completely task-partitioned", code="PLAN_TASK_FILE_PARTITION")
        if provided != set(package.get("provides_contracts", [])) or consumed != set(package.get("consumes_contracts", [])):
            raise PlanError(f"work package {package_id!r} contract projection is not task-complete", code="PLAN_TASK_CONTRACT_PARTITION")
        for req_id, role in package_responsibilities.items():
            matching = [task for task in shard_by_package[package_id]["tasks"] if any(item.get("req_id") == req_id and item.get("role") == role for item in task.get("requirement_responsibilities", []))]
            if not matching:
                raise PlanError(f"work package {package_id!r} responsibility {req_id!r} is not refined", code="PLAN_RESPONSIBILITY_REFINEMENT")
            if role == "primary" and len(matching) != 1:
                raise PlanError(f"primary responsibility {req_id!r} must have one task", code="PLAN_RESPONSIBILITY_REFINEMENT")

    normalized = {
        "schema_version": "1.0",
        "architecture": architecture,
        "work_packages": [_canonical_package(package_by_id[key]) for key in _sorted(package_by_id)],
        "task_shards": [shard_by_package[key] for key in _sorted(shard_by_package)],
    }
    errors = _schema_errors(normalized, "plan-draft-ir.schema.json")
    if errors:
        raise PlanError("normalized PlanDraftIR failed Schema validation: " + "; ".join(errors), code="PLAN_DRAFT_SCHEMA_INVALID")
    return normalized


def _canonical_package(package: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(dict(package))
    for field in ("context_refs", "requirement_responsibilities", "allowed_files", "provides_contracts", "consumes_contracts", "depends_on"):
        if isinstance(value.get(field), list):
            if field == "requirement_responsibilities":
                value[field] = sorted(value[field], key=lambda item: (_utf8(item.get("req_id")), _utf8(item.get("role"))))
            elif field == "context_refs":
                value[field] = sorted(value[field], key=lambda item: (_utf8(item.get("kind")), _utf8(item.get("id"))))
            else:
                value[field] = _sorted(set(value[field]))
    return value


def _contract_provider_tasks(
    architecture: Mapping[str, Any],
    packages: Mapping[str, Mapping[str, Any]],
    tasks: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, tuple[str, str]]:
    providers: dict[str, list[tuple[str, str]]] = defaultdict(list)
    contracts = {item.get("id"): item for item in architecture.get("contracts", []) if isinstance(item, Mapping)}
    for key, task in tasks.items():
        for contract_id in task.get("provides_contracts", []):
            providers[contract_id].append(key)
    resolved: dict[str, tuple[str, str]] = {}
    for contract_id, contract in contracts.items():
        candidates = providers.get(contract_id, [])
        if contract.get("ready_gate") == "s5":
            if candidates:
                raise PlanError(f"s5-ready contract {contract_id!r} cannot have a task provider", code="PLAN_CONTRACT_PROVIDER_INVALID")
            continue
        if len(candidates) != 1:
            raise PlanError(f"task-ready contract {contract_id!r} needs exactly one provider task", code="PLAN_CONTRACT_PROVIDER_INVALID")
        provider = candidates[0]
        if packages[provider[0]].get("module") != contract.get("owner"):
            raise PlanError(f"provider task for {contract_id!r} is outside its owner module", code="PLAN_CONTRACT_PROVIDER_INVALID")
        resolved[contract_id] = provider
    return resolved


def _stable_topology(
    tasks: Mapping[tuple[str, str], Mapping[str, Any]],
    provider_tasks: Mapping[str, tuple[str, str]],
) -> tuple[list[tuple[str, str]], set[tuple[tuple[str, str], tuple[str, str], str]]]:
    edges: set[tuple[tuple[str, str], tuple[str, str], str]] = set()
    keys = set(tasks)
    for key, task in tasks.items():
        for dependency in task.get("depends_on", []):
            source = (key[0], dependency)
            if source not in keys:
                raise PlanError(f"task {key!r} has an unknown or cross-package dependency", code="PLAN_DEPENDENCY_UNPROVEN")
            edges.add((source, key, "local"))
        for contract_id in task.get("consumes_contracts", []):
            provider = provider_tasks.get(contract_id)
            if provider is None:
                continue
            if provider == key:
                raise PlanError(f"task {key!r} consumes the contract it provides", code="PLAN_CONTRACT_SELF_DEPENDENCY")
            edges.add((provider, key, "contract"))
    indegree = {key: 0 for key in keys}
    outgoing: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for source, target, _reason in edges:
        if target not in outgoing[source]:
            outgoing[source].add(target)
            indegree[target] += 1
    ready = [key for key, degree in indegree.items() if degree == 0]
    ordered: list[tuple[str, str]] = []
    while ready:
        ready.sort(key=lambda key: (_utf8(key[0]), _utf8(key[1])))
        current = ready.pop(0)
        ordered.append(current)
        for child in _sorted(outgoing.get(current, set())):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if len(ordered) != len(keys):
        raise PlanError("task dependency graph contains a cycle", code="PLAN_TASK_DAG_CYCLE")
    return ordered, edges


def build_coverage(
    spec: Mapping[str, Any],
    manifest: Mapping[str, Any],
    packages: Mapping[str, Mapping[str, Any]],
    final_tasks: list[Mapping[str, Any]],
    task_keys: Mapping[str, tuple[str, str]],
    edges: set[tuple[tuple[str, str], tuple[str, str], str]],
    config_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct the complete static REQ/test matrix from authoritative inputs."""

    config_snapshot = config_snapshot or {}
    requirements = {item["id"]: item for item in spec.get("requirements", [])}
    task_by_id = {task["id"]: task for task in final_tasks}
    order = [task["id"] for task in final_tasks]
    position = {task_id: index for index, task_id in enumerate(order)}
    primary_wp: dict[str, str] = {}
    primary_task: dict[str, str] = {}
    supporting_tasks: dict[str, set[str]] = defaultdict(set)
    for package_id, package in packages.items():
        for item in package.get("requirement_responsibilities", []):
            req_id = item["req_id"]
            if req_id not in requirements:
                raise PlanError(f"work package {package_id!r} names unknown requirement {req_id!r}", code="PLAN_REQUIREMENT_UNKNOWN")
            if item["role"] == "primary":
                if req_id in primary_wp:
                    raise PlanError(f"requirement {req_id!r} has multiple primary work packages", code="PLAN_PRIMARY_OWNER_INVALID")
                primary_wp[req_id] = package_id
        for task in final_tasks:
            if task.get("work_package") != package_id:
                continue
            for item in task.get("requirement_responsibilities", []):
                if item["req_id"] not in requirements:
                    raise PlanError(f"task {task.get('id')!r} names unknown requirement {item['req_id']!r}", code="PLAN_REQUIREMENT_UNKNOWN")
                if item["role"] == "primary":
                    if item["req_id"] in primary_task:
                        raise PlanError(f"requirement {item['req_id']!r} has multiple primary tasks", code="PLAN_PRIMARY_OWNER_INVALID")
                    primary_task[item["req_id"]] = task["id"]
                else:
                    supporting_tasks[item["req_id"]].add(task["id"])
    manifest_tests = list(manifest.get("tests", []))
    tests_by_req: dict[str, list[str]] = defaultdict(list)
    for test in manifest_tests:
        for req_id in test.get("req_ids", []):
            if req_id not in requirements:
                raise PlanError(f"test {test.get('nodeid')!r} names unknown requirement {req_id!r}", code="PLAN_REQUIREMENT_UNKNOWN")
            tests_by_req[req_id].append(test["nodeid"])
    for req_id, requirement in requirements.items():
        if requirement.get("level") != "DEFINITION" and req_id not in primary_wp:
            raise PlanError(f"non-DEFINITION requirement {req_id!r} has no primary work package", code="PLAN_PRIMARY_OWNER_INVALID")
        if requirement.get("level") in {"MUST", "MUST NOT"} and not any(test.get("gate") in {"task", "s7_only"} and req_id in test.get("req_ids", []) for test in manifest_tests):
            raise PlanError(f"normative requirement {req_id!r} has no behavior-test coverage", code="PLAN_REQUIREMENT_UNCOVERED")

    incoming: dict[str, set[str]] = defaultdict(set)
    for source, target, _reason in edges:
        incoming[_task_id(task_keys, target)].add(_task_id(task_keys, source))
    ancestors: dict[str, set[str]] = {}
    for task_id in order:
        seen = {task_id}
        stack = list(incoming.get(task_id, set()))
        while stack:
            dependency = stack.pop()
            if dependency not in seen:
                seen.add(dependency)
                stack.extend(incoming.get(dependency, set()))
        ancestors[task_id] = seen

    test_rows: list[dict[str, Any]] = []
    for test in sorted(manifest_tests, key=lambda item: _utf8(item.get("nodeid", ""))):
        gate = test.get("gate")
        task_id = None
        if gate == "task":
            required_tasks: set[str] = set()
            for req_id in test.get("req_ids", []):
                if req_id in primary_task:
                    required_tasks.add(primary_task[req_id])
                required_tasks.update(supporting_tasks.get(req_id, set()))
            for candidate in order:
                if required_tasks.issubset(ancestors[candidate]):
                    task_id = candidate
                    break
            if task_id is None:
                raise PlanError(f"task-gated test {test.get('nodeid')!r} has no readiness convergence point", code="PLAN_TEST_READINESS_UNCLOSED")
        stages = config_snapshot.get("stages", {})
        enabled = stages.get(test.get("layer"), True) if isinstance(stages, Mapping) else True
        test_rows.append({"nodeid": test["nodeid"], "gate": gate, "enabled": bool(enabled), "task_id": task_id})

    requirement_rows = []
    for req_id in _sorted(requirements):
        requirement_rows.append({
            "req_id": req_id,
            "primary_work_package_id": primary_wp.get(req_id),
            "primary_task_id": primary_task.get(req_id),
            "supporting_task_ids": sorted(supporting_tasks.get(req_id, set()), key=lambda value: position.get(value, 10**9)),
            "test_nodeids": sorted(tests_by_req.get(req_id, []), key=_utf8),
        })
    return {"requirements": requirement_rows, "tests": test_rows}


def _task_id(keys: Mapping[str, tuple[str, str]], key: tuple[str, str]) -> str:
    value = next((task_id for task_id, candidate in keys.items() if candidate == key), None)
    if value is None:
        raise PlanError(f"missing final task id for {key!r}", code="PLAN_IDENTIFIER_INVALID")
    return value


def _input_refs(input_refs: Mapping[str, Any] | None, spec: Any, target: Any, manifest: Any) -> dict[str, Any]:
    if input_refs is not None:
        result = copy.deepcopy(dict(input_refs))
        if set(result) != {"spec", "target_profile", "test_bundle"}:
            raise PlanError("input_refs must contain exactly spec, target_profile, and test_bundle", code="PLAN_INPUT_REF_INVALID")
        return result
    return {"spec": _ref("spec", spec), "target_profile": _ref("target_profile", target), "test_bundle": _ref("test_bundle", manifest)}


def link_plan(
    architecture_or_draft: Mapping[str, Any],
    work_packages: list[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    task_shards: Any = None,
    constraints: Mapping[str, Any] | None = None,
    *,
    spec: Mapping[str, Any] | None = None,
    manifest: Mapping[str, Any] | None = None,
    test_manifest: Mapping[str, Any] | None = None,
    config_snapshot: Mapping[str, Any] | None = None,
    input_refs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Link one normalized draft into a candidate Plan and Blueprint."""

    source = _read(architecture_or_draft, "PlanDraftIR")
    if "task_shards" in source and "architecture" in source:
        architecture = source["architecture"]
        packages = source.get("work_packages", work_packages)
        shards = source["task_shards"]
    else:
        architecture = source
        packages = work_packages
        shards = task_shards
    if packages is None:
        packages = architecture.get("work_packages", [])
    if constraints is None:
        constraints = {}
    normalized = normalize_plan_draft(architecture, list(packages), shards, constraints=constraints)
    architecture = normalized["architecture"]
    packages_list = normalized["work_packages"]
    package_map = {item["id"]: item for item in packages_list}
    task_map: dict[tuple[str, str], Mapping[str, Any]] = {}
    for shard in normalized["task_shards"]:
        for task in shard["tasks"]:
            task_map[(shard["work_package_id"], task["local_id"])] = task
    provider_tasks = _contract_provider_tasks(architecture, package_map, task_map)
    ordered, edges = _stable_topology(task_map, provider_tasks)
    final_id_by_key = {key: f"T-{index:03d}" for index, key in enumerate(ordered, start=1)}
    final_key_by_id = {task_id: key for key, task_id in final_id_by_key.items()}
    final_tasks: list[dict[str, Any]] = []
    for key in ordered:
        local = task_map[key]
        context_refs = [dict(ref) for ref in local.get("context_refs", [])]
        existing = {(ref.get("kind"), ref.get("id")) for ref in context_refs}
        for responsibility in local.get("requirement_responsibilities", []):
            marker = ("requirement", responsibility["req_id"])
            if marker not in existing:
                context_refs.append({"kind": marker[0], "id": marker[1]})
        context_refs.sort(key=lambda ref: (_utf8(ref.get("kind")), _utf8(ref.get("id"))))
        final = {
            "id": final_id_by_key[key],
            "work_package": key[0],
            "title": local["title"],
            "goal": local["goal"],
            "kind": local["kind"],
            "instructions": local["instructions"],
            "deliverable_files": _sorted(local["deliverable_files"]),
            "context_refs": context_refs,
            "requirement_responsibilities": sorted(copy.deepcopy(local.get("requirement_responsibilities", [])), key=lambda item: (_utf8(item["req_id"]), _utf8(item["role"]))),
            "provides_contracts": _sorted(local.get("provides_contracts", [])),
            "consumes_contracts": _sorted(local.get("consumes_contracts", [])),
            "depends_on": sorted({final_id_by_key[(key[0], dependency)] for dependency in local.get("depends_on", [])} | {final_id_by_key[source] for source, target, reason in edges if target == key and reason == "contract"}, key=_utf8),
            "acceptance": {"build_variant_ids": _sorted(local["acceptance"]["build_variant_ids"]), "tests": []},
        }
        final_tasks.append(final)
    # No semantic uid or migration digest is computed in this milestone.

    final_architecture = copy.deepcopy(architecture)
    final_architecture.pop("work_packages", None)
    for contract in final_architecture.get("contracts", []):
        contract_id = contract.get("id")
        if contract.get("ready_gate") == "task":
            provider_key = provider_tasks.get(contract_id)
            if provider_key is None:
                raise PlanError(f"contract {contract_id!r} has no provider", code="PLAN_CONTRACT_PROVIDER_INVALID")
            contract["provider_task_id"] = final_id_by_key[provider_key]
        else:
            contract.pop("provider_task_id", None)
    final_packages = []
    for package in packages_list:
        value = copy.deepcopy(package)
        expected_dependencies = {
            package_id
            for contract_id, provider_key in provider_tasks.items()
            for package_id in [next((key[0] for key in task_map if key == provider_key), None)]
            if package_id and package_id != package["id"] and any(contract_id in task.get("consumes_contracts", []) for key, task in task_map.items() if key[0] == package["id"])
        }
        if set(package.get("depends_on", [])) != expected_dependencies:
            raise PlanError(f"work package {package['id']!r} dependencies are not contract-derived", code="PLAN_WORK_PACKAGE_DEPENDENCY_INVALID")
        value["depends_on"] = _sorted(value.get("depends_on", []))
        final_packages.append(value)
    final_packages.sort(key=lambda item: _utf8(item["id"]))

    if spec is None:
        raise PlanError("Spec is required to build coverage", code="PLAN_SPEC_MISSING")
    manifest_value = manifest if manifest is not None else test_manifest
    if manifest_value is None:
        raise PlanError("Test Manifest is required to build coverage", code="PLAN_MANIFEST_MISSING")
    coverage = build_coverage(spec, manifest_value, package_map, final_tasks, final_key_by_id, edges, config_snapshot)
    blueprint = compile_delivery_blueprint(constraints, final_architecture, final_packages, final_tasks)
    blueprint = canonical_delivery_blueprint(blueprint)
    blueprint_sha256 = hashlib.sha256(canonical_json_bytes(blueprint)).hexdigest()
    target = constraints.get("target_profile", {})
    plan = {
        "schema_version": "4.0",
        "input_refs": _input_refs(input_refs, spec, target, manifest_value),
        "delivery_blueprint_sha256": blueprint_sha256,
        "architecture": final_architecture,
        "work_packages": final_packages,
        "tasks": final_tasks,
        "coverage": coverage,
        "review": {"verdict": "pass", "unresolved_minor_issues": []},
    }
    errors = _schema_errors(plan, "plan.schema.json")
    if errors:
        raise PlanError("candidate Plan failed Schema validation: " + "; ".join(errors), code="PLAN_SCHEMA_INVALID")
    link_report = {
        "schema_version": "1.0",
        "task_order": [task["id"] for task in final_tasks],
        "dependency_edges": sorted(
            [{"from": final_id_by_key[source], "to": final_id_by_key[target], "reason": reason} for source, target, reason in edges],
            key=lambda item: (_utf8(item["from"]), _utf8(item["to"]), _utf8(item["reason"])),
        ),
        "coverage_sha256": hashlib.sha256(canonical_json_bytes(coverage)).hexdigest(),
        "delivery_blueprint_sha256": blueprint_sha256,
    }
    errors = _schema_errors(link_report, "link-report.schema.json")
    if errors:
        raise PlanError("link report failed Schema validation: " + "; ".join(errors), code="PLAN_LINK_REPORT_INVALID")
    return {"plan": plan, "blueprint": blueprint, "link_report": link_report, "plan_draft_ir": normalized}


def compile_plan(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return link_plan(*args, **kwargs)


def link_plan_draft(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return link_plan(*args, **kwargs)


normalize_plan_draft_ir = normalize_plan_draft
build_plan_draft_ir = normalize_plan_draft
compile_linked_plan = link_plan


def plan_lint(
    plan: Mapping[str, Any] | str | Path,
    spec: Mapping[str, Any] | str | Path | None = None,
    manifest: Mapping[str, Any] | str | Path | None = None,
    config_snapshot: Mapping[str, Any] | str | Path | None = None,
    *,
    level: str = "basic",
    constraints: Mapping[str, Any] | str | Path | None = None,
    blueprint: Mapping[str, Any] | str | Path | None = None,
    target_profile: Mapping[str, Any] | str | Path | None = None,
    run_dir: str | Path | None = None,
    test_manifest: Mapping[str, Any] | str | Path | None = None,
    delivery_constraints: Mapping[str, Any] | str | Path | None = None,
    delivery_blueprint: Mapping[str, Any] | str | Path | None = None,
    target: Mapping[str, Any] | str | Path | None = None,
) -> dict[str, Any]:
    """Lint a Plan at basic shape level or full S4 readiness level."""

    errors: list[dict[str, str]] = []
    if level not in {"basic", "full"}:
        return _lint_report(level, [{"code": "PLAN_LINT_LEVEL_INVALID", "path": "/level", "message": "level must be basic or full"}])
    plan_value = _read(plan, "Plan")
    schema_errors = _schema_errors(plan_value, "plan.schema.json")
    errors.extend(_lint_issue("S4-G0", "PLAN_SCHEMA_INVALID", "/", message) for message in schema_errors)
    if schema_errors:
        return _lint_report(level, errors)
    spec_value = _read(spec, "Spec IR") if spec is not None else None
    manifest_value = _read(manifest if manifest is not None else test_manifest, "Test Manifest") if (manifest is not None or test_manifest is not None) else None
    config_value = _read(config_snapshot, "config snapshot") if config_snapshot is not None else {}
    if run_dir is not None:
        run_root = Path(run_dir)
        if spec_value is None:
            for candidate in (run_root / "spec" / "spec.json", run_root / "inputs" / "spec.json"):
                if candidate.is_file():
                    spec_value = _read(candidate, "Spec IR")
                    break
        if manifest_value is None:
            for candidate in (run_root / "inputs" / "test_bundle.json", run_root / "test_bundle.json"):
                if candidate.is_file():
                    manifest_value = _read(candidate, "Test Manifest")
                    break
        if not config_value and (run_root / "run.json").is_file():
            run_meta = _read(run_root / "run.json", "run metadata")
            if isinstance(run_meta, Mapping) and isinstance(run_meta.get("config_snapshot"), Mapping):
                config_value = run_meta["config_snapshot"]
    if not isinstance(config_value, Mapping):
        errors.append(_lint_issue("S4-G0", "PLAN_CONFIG_INVALID", "/config_snapshot", "config snapshot must be an object"))
    if spec_value is None or manifest_value is None:
        errors.append(_lint_issue("S4-G0", "PLAN_COMPANION_MISSING", "/", "basic lint requires Spec and Test Manifest"))
    else:
        for ref_name, actual_value in (("spec", spec_value), ("test_bundle", manifest_value)):
            expected_sha = hashlib.sha256(canonical_json_bytes(actual_value)).hexdigest()
            if plan_value["input_refs"][ref_name]["sha256"] != expected_sha:
                errors.append(_lint_issue("S4-G0", "PLAN_INPUT_REF_DRIFT", f"/input_refs/{ref_name}", "Plan input reference does not match the supplied companion content"))
        spec_report = lint_spec(spec_value)
        if not spec_report.get("valid"):
            errors.extend(_lint_issue("S4-G0", item["code"], item["path"], item["message"]) for item in spec_report.get("errors", []))
        if manifest_value.get("schema_version") == "1.0" and isinstance(manifest_value.get("tests"), list):
            manifest_report = {"valid": True, "errors": []}
        else:
            manifest_report = lint_test_bundle(manifest_value, spec_value)
            if not manifest_report.get("valid"):
                errors.extend(_lint_issue("S4-G0", item["code"], item["path"], item["message"]) for item in manifest_report.get("errors", []))
        errors.extend(_lint_basic_relations(plan_value, spec_value, manifest_value, config_value))
        try:
            expected_coverage = _recompute_plan_coverage(plan_value, spec_value, manifest_value, config_value)
            if expected_coverage != plan_value["coverage"]:
                errors.append(_lint_issue("S4-G5", "PLAN_COVERAGE_DRIFT", "/coverage", "coverage does not equal its deterministic recomputation"))
        except (PlanError, KeyError, TypeError, StopIteration) as exc:
            code = getattr(exc, "code", "PLAN_COVERAGE_INVALID")
            errors.append(_lint_issue("S4-G5", code, "/coverage", str(exc)))
    if level == "full":
        full_values = _resolve_full_inputs(run_dir, delivery_constraints if delivery_constraints is not None else constraints, delivery_blueprint if delivery_blueprint is not None else blueprint, target if target is not None else target_profile)
        if any(value is None for value in full_values):
            errors.append(_lint_issue("S4-G1", "PLAN_FULL_INPUT_MISSING", "/", "full lint requires constraints, Blueprint, and Target Profile"))
        elif spec_value is not None and manifest_value is not None:
            errors.extend(_lint_full(plan_value, spec_value, manifest_value, config_value, *full_values))
    return _lint_report(level, errors)


def _lint_report(level: str, errors: list[dict[str, str]]) -> dict[str, Any]:
    errors = sorted(errors, key=lambda item: (item.get("gate", ""), item["code"], item["path"], item["message"]))
    return {"level": level, "valid": not errors, "errors": errors, "warnings": []}


def _lint_issue(gate: str, code: str, path: str, message: str) -> dict[str, str]:
    return {"gate": gate, "code": code, "path": path, "message": message}


def _lint_basic_relations(plan: Mapping[str, Any], spec: Mapping[str, Any], manifest: Mapping[str, Any], config: Mapping[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    package_list = plan.get("work_packages", [])
    task_list = plan.get("tasks", [])
    package_ids = {item.get("id") for item in package_list}
    task_ids = {item.get("id") for item in task_list}
    modules = {item.get("id") for item in plan.get("architecture", {}).get("modules", []) if isinstance(item, Mapping)}
    contracts = [item for item in plan.get("architecture", {}).get("contracts", []) if isinstance(item, Mapping)]
    contract_ids = {item.get("id") for item in contracts}
    if len(package_ids) != len(plan.get("work_packages", [])) or len(task_ids) != len(plan.get("tasks", [])):
        errors.append(_lint_issue("S4-G2", "PLAN_ID_DUPLICATE", "/", "work package and task ids must be unique"))
    package_map = {item.get("id"): item for item in package_list}
    requirement_ids = {item.get("id") for item in spec.get("requirements", []) if isinstance(item, Mapping)}
    manifest_nodeids: set[str] = set()
    for index, test in enumerate(manifest.get("tests", [])):
        base = f"/tests/{index}"
        nodeid = test.get("nodeid") if isinstance(test, Mapping) else None
        if not isinstance(nodeid, str) or not nodeid:
            errors.append(_lint_issue("S4-G0", "PLAN_MANIFEST_ENTRY_INVALID", f"{base}/nodeid", "manifest tests require a non-empty nodeid"))
        elif nodeid in manifest_nodeids:
            errors.append(_lint_issue("S4-G0", "PLAN_MANIFEST_DUPLICATE", f"{base}/nodeid", "manifest nodeids must be unique"))
        else:
            manifest_nodeids.add(nodeid)
        if not isinstance(test, Mapping):
            continue
        if test.get("gate") not in {"s5", "task", "s7_only"}:
            errors.append(_lint_issue("S4-G0", "PLAN_MANIFEST_ENTRY_INVALID", f"{base}/gate", "manifest gate is not supported"))
        for req_index, req_id in enumerate(test.get("req_ids", [])):
            if req_id not in requirement_ids:
                errors.append(_lint_issue("S4-G0", "PLAN_REQUIREMENT_UNKNOWN", f"{base}/req_ids/{req_index}", "manifest references an unknown requirement"))
    for index, package in enumerate(package_list):
        base = f"/work_packages/{index}"
        if package.get("module") not in modules:
            errors.append(_lint_issue("S4-G2", "PLAN_MODULE_UNKNOWN", f"{base}/module", "work package module does not exist"))
        if not set(package.get("depends_on", [])).issubset(package_ids):
            errors.append(_lint_issue("S4-G2", "PLAN_DEPENDENCY_UNKNOWN", f"{base}/depends_on", "work package dependency does not exist"))
        for field in ("provides_contracts", "consumes_contracts"):
            if not set(package.get(field, [])).issubset(contract_ids):
                errors.append(_lint_issue("S4-G2", "PLAN_CONTRACT_UNKNOWN", f"{base}/{field}", "work package contract does not exist"))
    package_incoming = {package.get("id"): set(package.get("depends_on", [])) for package in package_list}
    if _has_cycle(package_ids, package_incoming):
        errors.append(_lint_issue("S4-G2", "PLAN_WORK_PACKAGE_DAG_CYCLE", "/work_packages", "work package dependency graph contains a cycle"))
    module_contracts: dict[str, dict[str, set[str]]] = {module_id: {"provides": set(), "consumes": set()} for module_id in modules}
    for package in package_list:
        bucket = module_contracts.get(package.get("module"))
        if bucket is not None:
            bucket["provides"].update(package.get("provides_contracts", []))
            bucket["consumes"].update(package.get("consumes_contracts", []))
    for index, module in enumerate(plan.get("architecture", {}).get("modules", [])):
        expected = module_contracts.get(module.get("id"), {"provides": set(), "consumes": set()})
        if set(module.get("provides_contracts", [])) != expected["provides"] or set(module.get("consumes_contracts", [])) != expected["consumes"]:
            errors.append(_lint_issue("S4-G2", "PLAN_MODULE_CONTRACT_DRIFT", f"/architecture/modules/{index}", "module contract sets are not the work-package union"))
    for index, contract in enumerate(contracts):
        owner = contract.get("owner")
        if contract.get("ready_gate") == "s5":
            if owner != "s5" or contract.get("provider") != "s5" or contract.get("provider_task_id") is not None:
                errors.append(_lint_issue("S4-G2", "PLAN_CONTRACT_GATE_INVALID", f"/architecture/contracts/{index}", "s5-ready contracts must remain S5-owned and task-free"))
        elif owner not in modules or contract.get("provider") != owner:
            errors.append(_lint_issue("S4-G2", "PLAN_CONTRACT_PROVIDER_INVALID", f"/architecture/contracts/{index}", "task-ready contracts must be owned and provided by one module"))
        consumers = set(contract.get("consumers", []))
        if not consumers.issubset(modules):
            errors.append(_lint_issue("S4-G2", "PLAN_CONSUMER_UNKNOWN", f"/architecture/contracts/{index}/consumers", "contract consumer module does not exist"))
        for module_index, module in enumerate(plan.get("architecture", {}).get("modules", [])):
            module_id = module.get("id")
            if (contract.get("id") in module.get("provides_contracts", [])) != (module_id in {contract.get("provider")}):
                errors.append(_lint_issue("S4-G2", "PLAN_MODULE_CONTRACT_DRIFT", f"/architecture/modules/{module_index}", "module provider projection disagrees with contract declarations"))
            if (contract.get("id") in module.get("consumes_contracts", [])) != (module_id in consumers):
                errors.append(_lint_issue("S4-G2", "PLAN_MODULE_CONTRACT_DRIFT", f"/architecture/modules/{module_index}", "module consumer projection disagrees with contract declarations"))
    task_file_owners: dict[str, str] = {}
    incoming: dict[str, set[str]] = defaultdict(set)
    for index, task in enumerate(task_list):
        base = f"/tasks/{index}"
        if task.get("work_package") not in package_ids:
            errors.append(_lint_issue("S4-G4", "PLAN_WORK_PACKAGE_UNKNOWN", f"{base}/work_package", "task work package does not exist"))
        package = package_map.get(task.get("work_package"), {})
        if not set(task.get("provides_contracts", [])).issubset(set(package.get("provides_contracts", []))) or not set(task.get("consumes_contracts", [])).issubset(set(package.get("consumes_contracts", []))):
            errors.append(_lint_issue("S4-G3", "PLAN_TASK_CONTRACT_INVALID", base, "task contract sets exceed its work package"))
        for responsibility in task.get("requirement_responsibilities", []):
            if ("requirement", responsibility.get("req_id")) not in {(ref.get("kind"), ref.get("id")) for ref in task.get("context_refs", [])}:
                errors.append(_lint_issue("S4-G3", "PLAN_CONTEXT_REQUIREMENT_MISSING", f"{base}/context_refs", "task context must include every responsible requirement"))
        for file_path in task.get("deliverable_files", []):
            if file_path in task_file_owners:
                errors.append(_lint_issue("S4-G4", "PLAN_FILE_OWNER_DUPLICATE", f"{base}/deliverable_files", "an s6_owned file has multiple task owners"))
            task_file_owners[file_path] = task.get("id")
        for dependency in task.get("depends_on", []):
            if dependency not in task_ids:
                errors.append(_lint_issue("S4-G4", "PLAN_DEPENDENCY_UNKNOWN", f"{base}/depends_on", "task dependency does not exist"))
            else:
                incoming[task["id"]].add(dependency)
        if len(task.get("deliverable_files", [])) > 4:
            errors.append(_lint_issue("S4-G3", "PLAN_TASK_FILE_LIMIT", f"{base}/deliverable_files", "a task may own at most four files"))
        if task.get("acceptance", {}).get("tests") != []:
            errors.append(_lint_issue("S4-G5", "PLAN_TASK_ACCEPTANCE_NOT_EMPTY", f"{base}/acceptance/tests", "task test acceptance must remain empty before M2-0"))
    provider_package: dict[str, str] = {}
    provider_tasks: dict[str, list[str]] = defaultdict(list)
    for task in task_list:
        for contract_id in task.get("provides_contracts", []):
            provider_tasks[contract_id].append(task.get("id"))
            if contract_id in provider_package and provider_package[contract_id] != task.get("work_package"):
                errors.append(_lint_issue("S4-G4", "PLAN_PROVIDER_INVALID", f"/tasks/{task.get('id')}", "a task-ready contract has more than one provider package"))
            provider_package[contract_id] = task.get("work_package")
    for index, package in enumerate(package_list):
        package_files = set(package.get("allowed_files", []))
        owned = {path for task in task_list if task.get("work_package") == package.get("id") for path in task.get("deliverable_files", [])}
        if package_files != owned:
            errors.append(_lint_issue("S4-G4", "PLAN_FILE_PARTITION_INVALID", f"/work_packages/{index}", "task files do not equal the package allowed file set"))
        provided = {contract_id for task in task_list if task.get("work_package") == package.get("id") for contract_id in task.get("provides_contracts", [])}
        consumed = {contract_id for task in task_list if task.get("work_package") == package.get("id") for contract_id in task.get("consumes_contracts", [])}
        if provided != set(package.get("provides_contracts", [])) or consumed != set(package.get("consumes_contracts", [])):
            errors.append(_lint_issue("S4-G3", "PLAN_PACKAGE_CONTRACT_PARTITION", f"/work_packages/{index}", "task contract sets do not equal the package contract sets"))
        expected_dependencies = {provider_package[contract_id] for contract_id in package.get("consumes_contracts", []) if contract_id in provider_package and provider_package[contract_id] != package.get("id")}
        if set(package.get("depends_on", [])) != expected_dependencies:
            errors.append(_lint_issue("S4-G4", "PLAN_PACKAGE_DEPENDENCY_INVALID", f"/work_packages/{index}/depends_on", "package dependencies are not the contract-derived provider set"))
        package_responsibilities = _responsibility_map(package.get("requirement_responsibilities"))
        task_responsibilities = {req_id: [] for req_id in package_responsibilities}
        for task in task_list:
            if task.get("work_package") == package.get("id"):
                for responsibility in task.get("requirement_responsibilities", []):
                    task_responsibilities.setdefault(responsibility.get("req_id"), []).append(responsibility.get("role"))
        for req_id, role in package_responsibilities.items():
            roles = task_responsibilities.get(req_id, [])
            if roles.count(role) != 1 or len(roles) != roles.count(role):
                errors.append(_lint_issue("S4-G3", "PLAN_RESPONSIBILITY_REFINEMENT", f"/work_packages/{index}/requirement_responsibilities", "package responsibility is not exactly refined by tasks"))
    if _has_cycle(task_ids, incoming):
        errors.append(_lint_issue("S4-G4", "PLAN_TASK_DAG_CYCLE", "/tasks", "task dependency graph contains a cycle"))
    for index, contract in enumerate(contracts):
        if contract.get("id") not in contract_ids:
            continue
        consumers = set(contract.get("consumers", []))
        if any(consumer not in modules for consumer in consumers):
            errors.append(_lint_issue("S4-G2", "PLAN_CONSUMER_UNKNOWN", f"/architecture/contracts/{index}/consumers", "contract consumer module does not exist"))
        if contract.get("ready_gate") == "task":
            provider = contract.get("provider_task_id")
            provider_task = next((task for task in task_list if task.get("id") == provider), None)
            candidates = provider_tasks.get(contract.get("id"), [])
            if len(candidates) != 1 or provider_task is None or candidates[0] != provider or contract.get("provider") not in modules or contract.get("id") not in provider_task.get("provides_contracts", []) or package_map.get(provider_task.get("work_package"), {}).get("module") != contract.get("owner"):
                errors.append(_lint_issue("S4-G4", f"PLAN_PROVIDER_INVALID", f"/architecture/contracts/{index}", "task-ready contract provider task is not closed"))
    errors.extend(_full_contract_ancestry(plan))
    return errors


def _has_cycle(nodes: set[str], incoming: Mapping[str, set[str]]) -> bool:
    state: dict[str, int] = {}
    def visit(node: str) -> bool:
        if state.get(node) == 1:
            return True
        if state.get(node) == 2:
            return False
        state[node] = 1
        if any(visit(parent) for parent in incoming.get(node, set())):
            return True
        state[node] = 2
        return False
    return any(visit(node) for node in nodes)


def _recompute_plan_coverage(plan: Mapping[str, Any], spec: Mapping[str, Any], manifest: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    package_map = {item["id"]: item for item in plan["work_packages"]}
    task_keys = {task["id"]: (task["work_package"], task["id"]) for task in plan["tasks"]}
    # Plan lint consumes final ids.  Build the equivalent edge set directly.
    edges: set[tuple[tuple[str, str], tuple[str, str], str]] = set()
    for task in plan["tasks"]:
        target = (task["work_package"], task["id"])
        for dependency in task.get("depends_on", []):
            source_task = next(item for item in plan["tasks"] if item["id"] == dependency)
            edges.add(((source_task["work_package"], source_task["id"]), target, "local"))
    return build_coverage(spec, manifest, package_map, plan["tasks"], task_keys, edges, config)


def _resolve_full_inputs(run_dir: str | Path | None, constraints: Any, blueprint: Any, target: Any) -> tuple[Any, Any, Any]:
    values = [_read(item, label) if item is not None else None for item, label in ((constraints, "Delivery Constraints"), (blueprint, "Delivery Blueprint"), (target, "Target Profile"))]
    if run_dir is None:
        return tuple(values)  # type: ignore[return-value]
    root = Path(run_dir)
    candidates = [
        (root / "_s4" / "delivery_constraints.json", "Delivery Constraints"),
        (root / "_s4" / "delivery_blueprint.json", "Delivery Blueprint"),
        (root / "inputs" / "target.json", "Target Profile"),
    ]
    for index, (path, label) in enumerate(candidates):
        if values[index] is None and path.is_file():
            values[index] = _read(path, label)
    return tuple(values)  # type: ignore[return-value]


def _lint_full(plan: Mapping[str, Any], spec: Mapping[str, Any], manifest: Mapping[str, Any], config: Mapping[str, Any], constraints: Mapping[str, Any], blueprint: Mapping[str, Any], target: Mapping[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    target_report = lint_target(target, spec)
    errors.extend(_lint_issue("S4-G1", item["code"], item["path"], item["message"]) for item in target_report.get("errors", []))
    expected_target_sha = hashlib.sha256(canonical_json_bytes(target)).hexdigest()
    if plan.get("input_refs", {}).get("target_profile", {}).get("sha256") != expected_target_sha:
        errors.append(_lint_issue("S4-G1", "PLAN_INPUT_REF_DRIFT", "/input_refs/target_profile", "Plan target reference does not match the supplied Target Profile"))
    if dict(target) != dict(constraints.get("target_profile", target)):
        errors.append(_lint_issue("S4-G1", "PLAN_TARGET_DRIFT", "/target_profile", "Target Profile differs from Delivery Constraints"))
    try:
        expected = compile_delivery_blueprint(constraints, plan["architecture"], plan["work_packages"], plan["tasks"])
        if canonical_delivery_blueprint(expected) != canonical_delivery_blueprint(blueprint):
            errors.append(_lint_issue("S4-G1", "PLAN_BLUEPRINT_DRIFT", "/blueprint", "Blueprint is not the faithful layout projection"))
        expected_hash = hashlib.sha256(canonical_json_bytes(expected)).hexdigest()
        if plan.get("delivery_blueprint_sha256") != expected_hash:
            errors.append(_lint_issue("S4-G1", "PLAN_BLUEPRINT_HASH_INVALID", "/delivery_blueprint_sha256", "Plan Blueprint hash does not match the recomputed Blueprint"))
    except (PlanError, DeliveryConstraintError) as exc:
        errors.append(_lint_issue("S4-G1", getattr(exc, "code", "PLAN_BLUEPRINT_INVALID"), "/architecture/layout", str(exc)))
    rules = blueprint.get("file_rules", []) if isinstance(blueprint, Mapping) else []
    for index, task in enumerate(plan.get("tasks", [])):
        for file_index, path in enumerate(task.get("deliverable_files", [])):
            matches = [rule for rule in rules if _blueprint_rule_matches_path(rule, path)]
            if len(matches) != 1 or matches[0].get("mutability") != "s6_owned":
                errors.append(_lint_issue("S4-G4", "PLAN_S5_FILE_OWNERSHIP_INVALID", f"/tasks/{index}/deliverable_files/{file_index}", "tasks may own only one resolved s6_owned layout file"))
            elif matches[0].get("owner_task_id") != task.get("id"):
                errors.append(_lint_issue("S4-G4", "PLAN_FILE_OWNER_INVALID", f"/tasks/{index}/deliverable_files/{file_index}", "layout file owner does not match the task"))
    for index, rule in enumerate(rules):
        if rule.get("mutability") == "s6_owned" and not rule.get("owner_task_id"):
            errors.append(_lint_issue("S4-G4", "PLAN_FILE_OWNER_INVALID", f"/blueprint/file_rules/{index}", "every s6_owned file must have one task owner"))
    allowed_variants = set(constraints.get("build_variant_ids", []))
    for index, task in enumerate(plan.get("tasks", [])):
        variants = set(task.get("acceptance", {}).get("build_variant_ids", []))
        if not variants or (allowed_variants and not variants.issubset(allowed_variants)):
            errors.append(_lint_issue("S4-G5", "PLAN_BUILD_VARIANT_INVALID", f"/tasks/{index}/acceptance/build_variant_ids", "task has no valid configured build variant"))
        if len(task.get("instructions", "").encode("utf-8")) // 4 > config.get("budgets", {}).get("coder_context_max_tokens", 10**18):
            errors.append(_lint_issue("S4-G6", "PLAN_CONTEXT_TOO_LARGE", f"/tasks/{index}/instructions", "task context exceeds configured output boundary"))
    return errors


def _blueprint_rule_matches_path(rule: Mapping[str, Any], path: Any) -> bool:
    if not isinstance(path, str) or not isinstance(rule.get("path_pattern"), str):
        return False
    pattern = rule["path_pattern"]
    if rule.get("expansion") == "none":
        return path == pattern
    placeholder = "{message_id}" if rule.get("expansion") == "per_message" else "{type_id}"
    if placeholder not in pattern:
        return False
    prefix, suffix = pattern.split(placeholder, 1)
    return path.startswith(prefix) and path.endswith(suffix) and len(path) >= len(prefix) + len(suffix) + 1 and "/" not in path[len(prefix): len(path) - len(suffix) if suffix else None]


def _full_contract_ancestry(plan: Mapping[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    tasks = {task["id"]: task for task in plan.get("tasks", [])}
    ancestors: dict[str, set[str]] = {}
    for task_id in tasks:
        seen = {task_id}
        stack = list(tasks[task_id].get("depends_on", []))
        while stack:
            parent = stack.pop()
            if parent not in seen:
                seen.add(parent)
                stack.extend(tasks.get(parent, {}).get("depends_on", []))
        ancestors[task_id] = seen
    providers = {contract.get("id"): contract.get("provider_task_id") for contract in plan.get("architecture", {}).get("contracts", []) if contract.get("ready_gate") == "task"}
    for task_id, task in tasks.items():
        for contract_id in task.get("consumes_contracts", []):
            provider = providers.get(contract_id)
            if provider and provider not in ancestors[task_id]:
                errors.append(_lint_issue("S4-G4", "PLAN_CONTRACT_ANCESTRY_INVALID", f"/tasks/{task_id}/consumes_contracts", "consumer has no provider ancestor"))
    return errors


__all__ = [
    "PlanError", "build_coverage", "build_plan_draft_ir", "compile_linked_plan", "compile_plan", "link_plan", "link_plan_draft", "normalize_plan_draft", "normalize_plan_draft_ir", "plan_lint",
]
