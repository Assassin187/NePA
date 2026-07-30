"""TaskPlanner 输入构造与 shard 级硬门（设计 6.4.4、S4-G3）。

本模块只做确定性工作：为一个工作包裁剪 TaskPlanner 可见输入，并在链接前
独立校验该 shard 的结构、文件范围、责任细化与局部依赖。任何测试实现、
runner、oracle 与适配器内容都不进入 payload（P1 防作弊边界）。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import cache
from typing import Any

from jsonschema import Draft202012Validator

from nepa.agents.contracts import task_shard_schema
from nepa.speclib.slice import resolve_refs

__all__ = [
    "ShardIssue",
    "build_task_planner_payload",
    "frozen_file_paths",
    "planning_test_metadata",
    "validate_task_shard",
]

# 6.4.7/P1：规划角色只能看到 Test Manifest v2 的这几项元数据。
_VISIBLE_TEST_FIELDS: tuple[str, ...] = (
    "nodeid",
    "description",
    "req_ids",
    "gate",
    "required_contracts",
    "build_variant_ids",
)


@dataclass(frozen=True, slots=True)
class ShardIssue:
    """一条可路由的 shard 校验问题；code 与 6.4.6 修复负载共用。"""

    code: str
    path: str
    message: str


@cache
def _validator() -> Draft202012Validator:
    return Draft202012Validator(task_shard_schema())


def frozen_file_paths(constraints: dict[str, Any]) -> set[str]:
    """Delivery Constraints 中 S5 冻结、任务禁止写入的文件（6.4.1）。"""
    return {
        str(slot["path"])
        for slot in constraints.get("file_slots", [])
        if isinstance(slot, dict) and slot.get("mutability") == "s5_frozen"
    }


def planning_test_metadata(
    manifest: dict[str, Any],
    *,
    contract_ids: set[str],
    req_ids: set[str],
) -> list[dict[str, Any]]:
    """按 contract/REQ 相关性裁剪测试元数据，只保留白名单字段。"""
    selected: list[dict[str, Any]] = []
    for item in manifest.get("tests", []):
        if not isinstance(item, dict):
            continue
        required = {str(value) for value in item.get("required_contracts", [])}
        requirements = {str(value) for value in item.get("req_ids", [])}
        if not (required & contract_ids) and not (requirements & req_ids):
            continue
        selected.append(
            {key: deepcopy(item[key]) for key in _VISIBLE_TEST_FIELDS if key in item}
        )
    return sorted(selected, key=lambda item: str(item["nodeid"]))


def _related_decisions(
    architecture: dict[str, Any],
    *,
    related_ids: set[str],
) -> list[dict[str, Any]]:
    """与本包相关的架构决定：无 context_refs 视为全局，其余按引用 id 命中。

    这条相关性规则是确定性的，不依赖模型判断，也不给出包外实现细节。
    """
    selected: list[dict[str, Any]] = []
    for decision in architecture.get("decisions", []):
        if not isinstance(decision, dict):
            continue
        refs = {
            str(ref["id"])
            for ref in decision.get("context_refs", [])
            if isinstance(ref, dict) and isinstance(ref.get("id"), str)
        }
        if refs and not (refs & related_ids):
            continue
        selected.append(deepcopy(decision))
    return selected


def _contract_summary(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(contract[key])
        for key in ("id", "kind", "purpose", "owner", "ready_gate", "interface_files")
        if key in contract
    }


def build_task_planner_payload(
    package: dict[str, Any],
    *,
    architecture: dict[str, Any],
    spec: dict[str, Any],
    manifest: dict[str, Any],
    constraints: dict[str, Any],
    max_task_files: int,
) -> dict[str, Any]:
    """6.4.4：为一个工作包组装全新、无历史的 TaskPlanner 输入。"""
    module_by_id = {
        str(item["id"]): item
        for item in architecture.get("modules", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    contract_by_id = {
        str(item["id"]): item
        for item in architecture.get("contracts", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    package_contracts = [
        str(value)
        for field in ("provides_contracts", "consumes_contracts")
        for value in package.get(field, [])
    ]
    responsibilities = [
        deepcopy(item) for item in package.get("requirement_responsibilities", [])
    ]
    req_ids = {str(item["req_id"]) for item in responsibilities}
    context_refs = [
        deepcopy(ref) for ref in package.get("context_refs", []) if isinstance(ref, dict)
    ]
    slice_refs = list(context_refs)
    slice_refs.extend({"kind": "requirement", "id": value} for value in sorted(req_ids))
    module = module_by_id.get(str(package.get("module")), {})
    related_ids = (
        set(req_ids)
        | {str(ref["id"]) for ref in context_refs if isinstance(ref.get("id"), str)}
        | set(package.get("allowed_files", []))
        | set(package_contracts)
    )
    return {
        "work_package": deepcopy(package),
        "module": {
            key: deepcopy(module[key])
            for key in ("id", "name", "purpose", "responsibilities", "non_goals")
            if key in module
        },
        "architecture_decisions": _related_decisions(architecture, related_ids=related_ids),
        "spec_slice": resolve_refs(spec, slice_refs),
        "adjacent_contracts": [
            _contract_summary(contract_by_id[value])
            for value in sorted(set(package_contracts))
            if value in contract_by_id
        ],
        "allowed_files": list(package.get("allowed_files", [])),
        "s5_frozen_files": sorted(frozen_file_paths(constraints)),
        "test_metadata": planning_test_metadata(
            manifest,
            contract_ids=set(package_contracts),
            req_ids=req_ids,
        ),
        "budget": {"max_files_per_task": max_task_files},
    }


def _acyclic(edges: dict[str, set[str]]) -> bool:
    remaining = {node: set(values) for node, values in edges.items()}
    ready = sorted(node for node, values in remaining.items() if not values)
    visited = 0
    while ready:
        node = ready.pop(0)
        visited += 1
        for other, values in remaining.items():
            if node in values:
                values.discard(node)
                if not values:
                    ready.append(other)
                    ready.sort()
    return visited == len(remaining)


def validate_task_shard(
    shard: Any,
    *,
    package: dict[str, Any],
    constraints: dict[str, Any],
    max_task_files: int,
) -> list[ShardIssue]:
    """S4-G3：单个 shard 的 Schema、范围、责任与局部依赖硬门。

    返回稳定机器码列表，供 6.4.6 的定点重做负载与 `_s4/reviews` 审计复用。
    """
    issues: list[ShardIssue] = []
    schema_errors = sorted(
        _validator().iter_errors(shard),
        key=lambda item: [str(part) for part in item.absolute_path],
    )
    for error in schema_errors:
        issues.append(
            ShardIssue(
                "SHARD_SCHEMA",
                "/".join(str(part) for part in error.absolute_path) or "<root>",
                error.message,
            )
        )
    if schema_errors or not isinstance(shard, dict):
        return issues

    work_package_id = str(package["id"])
    if shard["work_package_id"] != work_package_id:
        issues.append(
            ShardIssue(
                "SHARD_WORK_PACKAGE_ID",
                "work_package_id",
                f"shard 必须展开工作包 {work_package_id}",
            )
        )
        return issues

    tasks: list[dict[str, Any]] = list(shard["tasks"])
    _check_local_ids(tasks, issues)
    _check_files(
        tasks,
        package=package,
        frozen=frozen_file_paths(constraints),
        max_task_files=max_task_files,
        issues=issues,
    )
    _check_contract_sets(tasks, package=package, issues=issues)
    _check_responsibilities(tasks, package=package, issues=issues)
    _check_dependencies(tasks, issues=issues)
    return issues


def _check_local_ids(tasks: list[dict[str, Any]], issues: list[ShardIssue]) -> None:
    seen: set[str] = set()
    for index, task in enumerate(tasks):
        local_id = str(task["local_id"])
        if local_id in seen:
            issues.append(
                ShardIssue("SHARD_DUPLICATE_LOCAL_ID", f"tasks/{index}/local_id", local_id)
            )
        seen.add(local_id)


def _check_files(
    tasks: list[dict[str, Any]],
    *,
    package: dict[str, Any],
    frozen: set[str],
    max_task_files: int,
    issues: list[ShardIssue],
) -> None:
    allowed = {str(value) for value in package["allowed_files"]}
    claimed: dict[str, str] = {}
    for index, task in enumerate(tasks):
        files = [str(value) for value in task["deliverable_files"]]
        if len(files) > max_task_files:
            issues.append(
                ShardIssue(
                    "SHARD_TASK_FILE_LIMIT",
                    f"tasks/{index}/deliverable_files",
                    f"任务文件数 {len(files)} 超过上限 {max_task_files}",
                )
            )
        for path in files:
            if path not in allowed:
                issues.append(
                    ShardIssue(
                        "SHARD_FILE_UNKNOWN",
                        f"tasks/{index}/deliverable_files",
                        f"{path} 不在工作包 allowed_files 内",
                    )
                )
            if path in frozen:
                issues.append(
                    ShardIssue(
                        "SHARD_FILE_FROZEN",
                        f"tasks/{index}/deliverable_files",
                        f"{path} 属于 s5_frozen，任务不得写入",
                    )
                )
            if path in claimed:
                issues.append(
                    ShardIssue(
                        "SHARD_FILE_DUPLICATE",
                        f"tasks/{index}/deliverable_files",
                        f"{path} 已由任务 {claimed[path]} 认领",
                    )
                )
            else:
                claimed[path] = str(task["local_id"])
    missing = sorted(allowed - set(claimed))
    if missing:
        issues.append(
            ShardIssue(
                "SHARD_FILE_PARTITION",
                "tasks",
                f"allowed_files 未被任务完整覆盖: {missing}",
            )
        )


def _check_contract_sets(
    tasks: list[dict[str, Any]],
    *,
    package: dict[str, Any],
    issues: list[ShardIssue],
) -> None:
    for field in ("provides_contracts", "consumes_contracts"):
        union = {str(value) for task in tasks for value in task[field]}
        expected = {str(value) for value in package[field]}
        if union != expected:
            issues.append(
                ShardIssue(
                    "SHARD_CONTRACT_SETS",
                    f"tasks/*/{field}",
                    f"任务并集 {sorted(union)} 必须等于工作包集合 {sorted(expected)}",
                )
            )


def _check_responsibilities(
    tasks: list[dict[str, Any]],
    *,
    package: dict[str, Any],
    issues: list[ShardIssue],
) -> None:
    package_roles = {
        str(item["req_id"]): str(item["role"])
        for item in package["requirement_responsibilities"]
    }
    for index, task in enumerate(tasks):
        seen: set[str] = set()
        for item in task["requirement_responsibilities"]:
            req_id = str(item["req_id"])
            path = f"tasks/{index}/requirement_responsibilities"
            if req_id in seen:
                issues.append(
                    ShardIssue("SHARD_RESPONSIBILITY_DUPLICATE", path, f"{req_id} 重复声明")
                )
            seen.add(req_id)
            if req_id not in package_roles:
                issues.append(
                    ShardIssue(
                        "SHARD_RESPONSIBILITY_OUT_OF_SCOPE",
                        path,
                        f"{req_id} 不属于本工作包",
                    )
                )
            elif item["role"] == "primary" and package_roles[req_id] != "primary":
                issues.append(
                    ShardIssue(
                        "SHARD_RESPONSIBILITY_PRIMARY",
                        path,
                        f"supporting 工作包不得声明 {req_id} 的 primary 任务",
                    )
                )
    for req_id, role in sorted(package_roles.items()):
        matches = [
            item
            for task in tasks
            for item in task["requirement_responsibilities"]
            if str(item["req_id"]) == req_id
        ]
        if not matches:
            issues.append(
                ShardIssue(
                    "SHARD_RESPONSIBILITY_UNREFINED",
                    "tasks",
                    f"工作包责任 {req_id} 未细化到任何任务",
                )
            )
            continue
        primaries = sum(item["role"] == "primary" for item in matches)
        if role == "primary" and primaries != 1:
            issues.append(
                ShardIssue(
                    "SHARD_RESPONSIBILITY_PRIMARY",
                    "tasks",
                    f"primary 责任 {req_id} 必须恰有一个 primary 任务，实际 {primaries}",
                )
            )


def _check_dependencies(tasks: list[dict[str, Any]], *, issues: list[ShardIssue]) -> None:
    local_ids = {str(task["local_id"]) for task in tasks}
    edges: dict[str, set[str]] = {}
    for index, task in enumerate(tasks):
        local_id = str(task["local_id"])
        dependencies = {str(value) for value in task["depends_on"]}
        unknown = sorted(dependencies - local_ids)
        if unknown:
            issues.append(
                ShardIssue(
                    "SHARD_DEPENDENCY_UNKNOWN",
                    f"tasks/{index}/depends_on",
                    f"局部依赖只能引用同包任务: {unknown}",
                )
            )
        if local_id in dependencies:
            issues.append(
                ShardIssue(
                    "SHARD_DEPENDENCY_SELF",
                    f"tasks/{index}/depends_on",
                    f"{local_id} 不能依赖自身",
                )
            )
        edges[local_id] = dependencies & local_ids
    if edges and not _acyclic(edges):
        issues.append(ShardIssue("SHARD_DEPENDENCY_CYCLE", "tasks", "shard 内局部依赖成环"))


def shard_issue_dicts(issues: list[ShardIssue]) -> list[dict[str, str]]:
    """把 shard 问题转为可写入 `_s4` 与修复 payload 的稳定 JSON 形态。"""
    return [
        {"code": issue.code, "path": issue.path, "message": issue.message} for issue in issues
    ]
