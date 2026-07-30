"""规格与计划校验器（M0-5）。

- ``spec_lint``：设计文档 5.1.5 的确定性检查（结构、引用完整、来源关联与
  gold 测试覆盖），非 LLM。
- ``plan_lint``：5.2/6.4 的计划检查（冻结输入引用、DAG 无环、
  deliverable_files 互斥、acceptance 测试存在性、粒度、MUST 需求经
  context_refs 可追溯）。

两者输出统一的结构化报告（错误/警告分级，S3 与 CI 共用，5.1.6）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from nepa.plan_draft import PlanDraftError, build_coverage
from nepa.speclib.slice import element_req_ids

__all__ = [
    "LintIssue",
    "LintReport",
    "plan_full_lint",
    "plan_lint",
    "spec_lint",
]

_SCHEMA_DIR: Path = Path(__file__).resolve().parent.parent / "schemas"

# 5.1.2 内建原语
_PRIMITIVE_TYPES: frozenset[str] = frozenset(
    {"uint8", "uint16_be", "uint32_be", "bytes", "bitfield8"}
)

_MUST_LEVELS: frozenset[str] = frozenset({"MUST", "MUST NOT"})

# 5.2：任务粒度 SHOULD ≤ 4 文件
_MAX_TASK_FILES: int = 4

_PLAN_INPUT_KINDS: tuple[str, ...] = (
    "spec",
    "target_profile",
    "language_profile",
    "test_bundle",
)


@dataclass(frozen=True)
class LintIssue:
    """单条校验问题：错误码、定位路径（斜杠分隔的 JSON 路径）、说明。"""

    code: str
    path: str
    message: str


@dataclass
class LintReport:
    """校验报告，错误/警告分级（5.1.6：结构化报告，S3 与 CI 共用）。"""

    errors: list[LintIssue] = field(default_factory=list)
    warnings: list[LintIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error_codes(self) -> set[str]:
        return {issue.code for issue in self.errors}

    def warning_codes(self) -> set[str]:
        return {issue.code for issue in self.warnings}


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------


def _as_list(value: Any) -> list[Any]:
    """容错取列表：类型错误由结构校验（检查 1）报告，语义检查跳过即可。"""
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


@cache
def _validator(schema_name: str) -> Draft202012Validator:
    with (_SCHEMA_DIR / schema_name).open(encoding="utf-8") as fh:
        return Draft202012Validator(json.load(fh))


def _check_schema(instance: Any, schema_name: str, code: str, errors: list[LintIssue]) -> None:
    """检查 1：JSON Schema draft 2020-12 结构校验（5 章通用约定）。"""
    schema_errors = sorted(
        _validator(schema_name).iter_errors(instance),
        key=lambda e: [str(p) for p in e.absolute_path],
    )
    for err in schema_errors:
        path = "/".join(str(p) for p in err.absolute_path)
        errors.append(LintIssue(code=code, path=path or "<root>", message=err.message))


def _manifest_req_ids(tests_manifest: list[Any] | None) -> set[str] | None:
    """收集 Test Bundle 清单直接声明覆盖的需求 id。"""
    if tests_manifest is None:
        return None
    req_ids: set[str] = set()
    for entry in tests_manifest:
        if isinstance(entry, dict):
            req_ids.update(rid for rid in _as_list(entry.get("req_ids")) if isinstance(rid, str))
    return req_ids


def _manifest_nodeids(tests_manifest: list[Any] | None) -> set[str] | None:
    """收集 plan acceptance 可引用的测试 nodeid。"""
    if tests_manifest is None:
        return None
    nodeids: set[str] = set()
    for entry in tests_manifest:
        if isinstance(entry, dict) and isinstance(entry.get("nodeid"), str):
            nodeids.add(entry["nodeid"])
        elif isinstance(entry, str):
            nodeids.add(entry)
    return nodeids


# ---------------------------------------------------------------------------
# spec_lint（5.1.6）
# ---------------------------------------------------------------------------

# 参与全局 id 唯一性检查的 spec 顶层集合（5 章通用约定）
_SPEC_ID_COLLECTIONS: tuple[str, ...] = ("types", "messages", "requirements")


def spec_lint(
    spec: dict,
    *,
    tests_manifest: list | None = None,
    gold_mode: bool = False,
) -> LintReport:
    """按 5.1.6 校验 Spec IR，返回结构化报告。

    :param spec: Spec IR v3.0（5.1 格式）。
    :param tests_manifest: Test Bundle 清单的条目数组（5.3 ``tests`` 字段）。
    :param gold_mode: gold 规格模式——每条 MUST/MUST NOT 需求必须出现在
        Test Bundle 清单的 ``req_ids`` 中。
    """
    report = LintReport()
    # 检查 1：结构合法
    _check_schema(spec, "specs-requirements.schema.json", "SPEC-SCHEMA", report.errors)
    if not isinstance(spec, dict):
        return report

    _check_spec_duplicate_ids(spec, report.errors)
    _check_spec_references(spec, report.errors)
    _check_spec_test_coverage(spec, tests_manifest, gold_mode, report.errors)
    return report


def _check_spec_duplicate_ids(spec: dict[str, Any], errors: list[LintIssue]) -> None:
    """5 章通用约定：id 类字段全局唯一性由校验器检查。"""
    seen: dict[str, str] = {}
    for collection in _SPEC_ID_COLLECTIONS:
        for i, item in enumerate(_as_list(spec.get(collection))):
            item_id = _as_dict(item).get("id")
            if not isinstance(item_id, str):
                continue
            path = f"{collection}/{i}/id"
            if item_id in seen:
                errors.append(
                    LintIssue(
                        "SPEC-DUP-ID", path, f"id '{item_id}' 重复定义（先见于 {seen[item_id]}）"
                    )
                )
            else:
                seen[item_id] = path


def _defined_req_ids(spec: dict[str, Any]) -> set[str]:
    return {
        r["id"]
        for r in _as_list(spec.get("requirements"))
        if isinstance(_as_dict(r).get("id"), str)
    }


def _iter_req_id_sites(spec: dict[str, Any]) -> list[tuple[str, Any]]:
    """列出全部 req_ids 引用位置：(路径, 值)。"""
    sites: list[tuple[str, Any]] = []

    def _collect(prefix: str, obj: Any) -> None:
        for j, rid in enumerate(_as_list(_as_dict(obj).get("req_ids"))):
            sites.append((f"{prefix}/req_ids/{j}", rid))

    transport = _as_dict(spec.get("transport"))
    if transport:
        _collect("transport", transport)
    for coll in ("types",):
        for i, item in enumerate(_as_list(spec.get(coll))):
            _collect(f"{coll}/{i}", item)
    for i, msg in enumerate(_as_list(spec.get("messages"))):
        _collect(f"messages/{i}", msg)
        for k, fld in enumerate(_as_list(_as_dict(msg).get("fields"))):
            _collect(f"messages/{i}/fields/{k}", fld)
    return sites


def _check_spec_references(spec: dict[str, Any], errors: list[LintIssue]) -> None:
    """检查类型、角色、位置和需求引用完整性。"""
    type_ids = {
        t["id"] for t in _as_list(spec.get("types")) if isinstance(_as_dict(t).get("id"), str)
    }
    req_ids = _defined_req_ids(spec)
    roles = {
        role
        for role in _as_list(_as_dict(spec.get("protocol")).get("roles"))
        if isinstance(role, str)
    }

    def _check_type_ref(value: Any, path: str) -> None:
        if isinstance(value, str) and value not in _PRIMITIVE_TYPES and value not in type_ids:
            errors.append(
                LintIssue(
                    "SPEC-REF-TYPE",
                    path,
                    f"类型 '{value}' 既非内建原语（5.1.2）也未在 types 中定义",
                )
            )

    # 命名类型内部的 sequence/repeat/长度前缀/enum 引用同一类型空间。
    for i, type_raw in enumerate(_as_list(spec.get("types"))):
        encoding = _as_dict(_as_dict(type_raw).get("encoding"))
        kind = encoding.get("kind")
        if kind == "sequence":
            for j, member_raw in enumerate(_as_list(encoding.get("members"))):
                _check_type_ref(
                    _as_dict(member_raw).get("type"),
                    f"types/{i}/encoding/members/{j}/type",
                )
        elif kind == "repeat":
            _check_type_ref(encoding.get("item_type"), f"types/{i}/encoding/item_type")
            min_items = encoding.get("min_items")
            max_items = encoding.get("max_items")
            if (
                isinstance(min_items, int)
                and not isinstance(min_items, bool)
                and isinstance(max_items, int)
                and not isinstance(max_items, bool)
                and min_items > max_items
            ):
                errors.append(
                    LintIssue(
                        "SPEC-REPEAT-BOUNDS",
                        f"types/{i}/encoding",
                        f"repeat 的 min_items ({min_items}) 不得大于 max_items ({max_items})",
                    )
                )
        elif kind in {"length_prefixed_string", "length_prefixed_bytes"}:
            _check_type_ref(encoding.get("length_type"), f"types/{i}/encoding/length_type")
        elif kind == "enum":
            _check_type_ref(encoding.get("base_type"), f"types/{i}/encoding/base_type")

    # 报文角色、字段类型与线段引用。
    for i, msg in enumerate(_as_list(spec.get("messages"))):
        msg_obj = _as_dict(msg)
        for key in ("senders", "receivers"):
            for j, role in enumerate(_as_list(msg_obj.get(key))):
                if isinstance(role, str) and role not in roles:
                    errors.append(
                        LintIssue(
                            "SPEC-REF-ROLE",
                            f"messages/{i}/{key}/{j}",
                            f"角色 '{role}' 未在 protocol.roles 中声明",
                        )
                    )
        wire_layout = {loc for loc in _as_list(msg_obj.get("wire_layout")) if isinstance(loc, str)}
        for k, fld in enumerate(_as_list(msg_obj.get("fields"))):
            field = _as_dict(fld)
            _check_type_ref(field.get("type"), f"messages/{i}/fields/{k}/type")
            loc = field.get("loc")
            if isinstance(loc, str) and loc not in wire_layout:
                errors.append(
                    LintIssue(
                        "SPEC-REF-LOC",
                        f"messages/{i}/fields/{k}/loc",
                        f"字段位置 '{loc}' 未在本报文 wire_layout 中声明",
                    )
                )

    # 所有结构化事实都必须引用已定义的直接证据条目。
    for path, rid in _iter_req_id_sites(spec):
        if isinstance(rid, str) and rid not in req_ids:
            errors.append(
                LintIssue("SPEC-REF-REQ", path, f"req_id '{rid}' 未在 requirements 中定义")
            )

def _check_spec_test_coverage(
    spec: dict[str, Any],
    tests_manifest: list[Any] | None,
    gold_mode: bool,
    errors: list[LintIssue],
) -> None:
    """gold 的测试覆盖只由 Test Bundle manifest 声明，不回写 Spec IR。"""
    covered_req_ids = _manifest_req_ids(tests_manifest)
    if not gold_mode:
        return
    for i, req_raw in enumerate(_as_list(spec.get("requirements"))):
        req = _as_dict(req_raw)
        req_id = req.get("id", f"requirements[{i}]")
        if (
            req.get("level") in _MUST_LEVELS
            and isinstance(req_id, str)
            and (covered_req_ids is None or req_id not in covered_req_ids)
        ):
            errors.append(
                LintIssue(
                    "SPEC-COV-TESTS",
                    f"requirements/{i}",
                    f"{req_id}: gold 模式下必须由 Test Bundle manifest 的 req_ids 覆盖",
                )
            )


# ---------------------------------------------------------------------------
# plan_lint（5.2 / 6.4）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PlanIndex:
    """按 id 索引的 Plan v3 视图；只收录结构合法的条目。"""

    modules: dict[str, dict[str, Any]]
    contracts: dict[str, dict[str, Any]]
    packages: dict[str, dict[str, Any]]
    tasks: dict[str, dict[str, Any]]
    task_order: list[dict[str, Any]]

    @property
    def packages_by_module(self) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in self.modules}
        for package in self.packages.values():
            grouped.setdefault(str(package.get("module")), []).append(package)
        return grouped

    @property
    def tasks_by_package(self) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in self.packages}
        for task in self.task_order:
            grouped.setdefault(str(task.get("work_package")), []).append(task)
        return grouped


def _plan_index(plan: dict[str, Any]) -> _PlanIndex:
    architecture = _as_dict(plan.get("architecture"))

    def _by_id(items: Any) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for item in _as_list(items):
            entry = _as_dict(item)
            if isinstance(entry.get("id"), str):
                result.setdefault(entry["id"], entry)
        return result

    tasks = [task for task in _as_list(plan.get("tasks")) if isinstance(task, dict)]
    return _PlanIndex(
        modules=_by_id(architecture.get("modules")),
        contracts=_by_id(architecture.get("contracts")),
        packages=_by_id(plan.get("work_packages")),
        tasks=_by_id(plan.get("tasks")),
        task_order=tasks,
    )


def plan_lint(
    plan: dict,
    spec: dict,
    tests_manifest: list | None = None,
    expected_input_refs: dict[str, Any] | None = None,
    config_snapshot: dict[str, Any] | None = None,
) -> LintReport:
    """5.2.5 basic lint：只依赖 Plan、Spec、Test Manifest 与 config snapshot。

    检查结构、四项引用、id/引用完整性、工作包/任务 DAG、责任守恒、contract
    在 architecture/module/work package/task 四层的一致性、文件完整分区、
    coverage 可重算性、测试存在性与 acceptance 反向注入等式。缺少清单或
    config snapshot 时，coverage 重算降级为 ``PLAN-COVERAGE-UNCHECKED`` 警告。
    """
    report = LintReport()
    _check_schema(plan, "plan.schema.json", "PLAN-SCHEMA", report.errors)
    if not isinstance(plan, dict):
        return report

    index = _plan_index(plan)
    _check_plan_input_refs(plan, expected_input_refs, report.errors)
    _check_plan_legacy_fields(plan, report.errors)
    _check_plan_duplicate_ids(plan, report.errors)
    _check_plan_references(index, report.errors)
    _check_plan_dag(index, report.errors)
    _check_plan_file_partition(index, report.errors)
    _check_plan_contract_sets(index, report.errors)
    _check_plan_responsibilities(index, spec, report.errors)
    _check_plan_context_refs(index, spec, report.errors)
    _check_plan_coverage(
        plan, index, spec, tests_manifest, config_snapshot, report
    )
    return report


def _check_plan_input_refs(
    plan: dict[str, Any],
    expected_input_refs: dict[str, Any] | None,
    errors: list[LintIssue],
) -> None:
    """5.2：调用方提供冻结输入时，逐项核对 Plan 的路径与内容哈希。"""
    if expected_input_refs is None:
        return
    actual_refs = _as_dict(plan.get("input_refs"))
    for kind in _PLAN_INPUT_KINDS:
        expected = _as_dict(expected_input_refs.get(kind))
        if not expected:
            continue
        actual = _as_dict(actual_refs.get(kind))
        for key in ("path", "sha256"):
            if key not in expected or actual.get(key) == expected[key]:
                continue
            errors.append(
                LintIssue(
                    "PLAN-INPUT-MISMATCH",
                    f"input_refs/{kind}/{key}",
                    f"{kind} 的 {key} 与本次运行冻结输入不一致",
                )
            )


_LEGACY_TASK_FIELDS: tuple[str, ...] = ("status", "attempts", "notes")


def _check_plan_legacy_fields(plan: dict[str, Any], errors: list[LintIssue]) -> None:
    """5.2.5 硬错误：Plan 不得含执行状态字段，也不得存在 scaffold task。"""
    for index, raw in enumerate(_as_list(plan.get("tasks"))):
        task = _as_dict(raw)
        for field_name in _LEGACY_TASK_FIELDS:
            if field_name in task:
                errors.append(
                    LintIssue(
                        "PLAN-EXECUTION-STATE",
                        f"tasks/{index}/{field_name}",
                        f"Plan v3 不得携带执行状态字段 {field_name}（已移入 Plan State）",
                    )
                )
        if task.get("kind") == "scaffold":
            errors.append(
                LintIssue(
                    "PLAN-SCAFFOLD-TASK",
                    f"tasks/{index}/kind",
                    "Plan v3 中不存在 scaffold 任务，脚手架完全属于 S5",
                )
            )
    if "modules" in plan:
        errors.append(
            LintIssue(
                "PLAN-LEGACY-MODULES",
                "modules",
                "Plan v3 的模块位于 architecture.modules，不得保留顶层 modules",
            )
        )


_ID_COLLECTIONS: tuple[tuple[str, str], ...] = (
    ("architecture/modules", "architecture"),
    ("architecture/contracts", "architecture"),
    ("work_packages", "plan"),
    ("tasks", "plan"),
)


def _check_plan_duplicate_ids(plan: dict[str, Any], errors: list[LintIssue]) -> None:
    """5 章通用约定：模块、contract、工作包与任务 id 各自唯一。"""
    architecture = _as_dict(plan.get("architecture"))
    collections = {
        "architecture/modules": architecture.get("modules"),
        "architecture/contracts": architecture.get("contracts"),
        "work_packages": plan.get("work_packages"),
        "tasks": plan.get("tasks"),
    }
    for prefix, items in collections.items():
        seen: dict[str, str] = {}
        for index, raw in enumerate(_as_list(items)):
            item_id = _as_dict(raw).get("id")
            if not isinstance(item_id, str):
                continue
            path = f"{prefix}/{index}/id"
            if item_id in seen:
                errors.append(
                    LintIssue(
                        "PLAN-DUP-ID",
                        path,
                        f"id '{item_id}' 重复（先见于 {seen[item_id]}）",
                    )
                )
            else:
                seen[item_id] = path


def _check_plan_references(index: _PlanIndex, errors: list[LintIssue]) -> None:
    """5.2.1/5.2.2：模块、工作包与任务只能引用已声明的 id。"""
    for package_id, package in sorted(index.packages.items()):
        if package.get("module") not in index.modules:
            errors.append(
                LintIssue(
                    "PLAN-REF-MODULE",
                    f"work_packages/{package_id}/module",
                    f"工作包 '{package_id}' 的模块 '{package.get('module')}' 未声明",
                )
            )
    for task_id, task in sorted(index.tasks.items()):
        if task.get("work_package") not in index.packages:
            errors.append(
                LintIssue(
                    "PLAN-REF-WORK-PACKAGE",
                    f"tasks/{task_id}/work_package",
                    f"任务 '{task_id}' 的工作包 '{task.get('work_package')}' 未声明",
                )
            )
    holders: tuple[tuple[str, dict[str, dict[str, Any]]], ...] = (
        ("architecture/modules", index.modules),
        ("work_packages", index.packages),
        ("tasks", index.tasks),
    )
    for prefix, items in holders:
        for item_id, item in sorted(items.items()):
            for field_name in ("provides_contracts", "consumes_contracts"):
                for contract_id in _as_list(item.get(field_name)):
                    if contract_id not in index.contracts:
                        errors.append(
                            LintIssue(
                                "PLAN-REF-CONTRACT",
                                f"{prefix}/{item_id}/{field_name}",
                                f"引用了未声明的 contract '{contract_id}'",
                            )
                        )
    for contract_id, contract in sorted(index.contracts.items()):
        owner = contract.get("owner")
        if contract.get("ready_gate") == "task" and owner not in index.modules:
            errors.append(
                LintIssue(
                    "PLAN-REF-MODULE",
                    f"architecture/contracts/{contract_id}/owner",
                    f"task-ready contract '{contract_id}' 的 owner 模块未声明",
                )
            )
        provider = contract.get("provider_task_id")
        if provider is not None and provider not in index.tasks:
            errors.append(
                LintIssue(
                    "PLAN-REF-TASK",
                    f"architecture/contracts/{contract_id}/provider_task_id",
                    f"provider_task_id '{provider}' 不存在",
                )
            )


def _acyclic(edges: dict[str, set[str]]) -> list[str]:
    """返回无法拓扑排序（成环）的节点集合，按 id 排序。"""
    remaining = {node: set(deps) for node, deps in edges.items()}
    ready = sorted(node for node, deps in remaining.items() if not deps)
    while ready:
        node = ready.pop()
        remaining.pop(node, None)
        for other, deps in remaining.items():
            if node in deps:
                deps.discard(node)
                if not deps:
                    ready.append(other)
    return sorted(remaining)


def _check_plan_dag(index: _PlanIndex, errors: list[LintIssue]) -> None:
    """5.2.2/6.4.5：工作包与任务 DAG 引用完整且均无环。"""
    levels: tuple[tuple[str, dict[str, dict[str, Any]], str], ...] = (
        ("work_packages", index.packages, "PLAN-REF-WORK-PACKAGE"),
        ("tasks", index.tasks, "PLAN-REF-TASK"),
    )
    for prefix, items, ref_code in levels:
        edges: dict[str, set[str]] = {}
        for item_id, item in sorted(items.items()):
            deps: set[str] = set()
            for dep in _as_list(item.get("depends_on")):
                if not isinstance(dep, str) or dep not in items:
                    errors.append(
                        LintIssue(
                            ref_code,
                            f"{prefix}/{item_id}/depends_on",
                            f"depends_on 引用了不存在的 '{dep}'",
                        )
                    )
                elif dep == item_id:
                    errors.append(
                        LintIssue(
                            "PLAN-CYCLE",
                            f"{prefix}/{item_id}/depends_on",
                            f"'{item_id}' 不得依赖自身",
                        )
                    )
                else:
                    deps.add(dep)
            edges[item_id] = deps
        cyclic = _acyclic(edges)
        if cyclic:
            errors.append(
                LintIssue(
                    "PLAN-CYCLE",
                    prefix,
                    f"{prefix} 依赖图存在环，涉及: {', '.join(cyclic)}",
                )
            )


def _check_plan_file_partition(index: _PlanIndex, errors: list[LintIssue]) -> None:
    """5.2.2 硬错误：task→work package→module 的文件所有权是完整分区。"""
    owners: dict[str, list[str]] = {}
    for task_id, task in sorted(index.tasks.items()):
        files = [value for value in _as_list(task.get("deliverable_files")) if isinstance(value, str)]
        if not files:
            errors.append(
                LintIssue(
                    "PLAN-TASK-NO-FILES",
                    f"tasks/{task_id}/deliverable_files",
                    f"任务 '{task_id}' 必须声明非空 deliverable_files",
                )
            )
        if len(files) > _MAX_TASK_FILES:
            errors.append(
                LintIssue(
                    "PLAN-GRANULARITY",
                    f"tasks/{task_id}/deliverable_files",
                    f"任务 '{task_id}' 交付 {len(files)} 个文件，超过上限 {_MAX_TASK_FILES}",
                )
            )
        for path in files:
            owners.setdefault(path, []).append(task_id)
    for path, task_ids in sorted(owners.items()):
        if len(task_ids) > 1:
            errors.append(
                LintIssue(
                    "PLAN-FILE-CONFLICT",
                    "tasks",
                    f"文件 '{path}' 出现在多个任务的 deliverable_files: {', '.join(sorted(task_ids))}",
                )
            )

    tasks_by_package = index.tasks_by_package
    for package_id, package in sorted(index.packages.items()):
        allowed = {value for value in _as_list(package.get("allowed_files")) if isinstance(value, str)}
        union = {
            path
            for task in tasks_by_package.get(package_id, [])
            for path in _as_list(task.get("deliverable_files"))
            if isinstance(path, str)
        }
        if union != allowed:
            errors.append(
                LintIssue(
                    "PLAN-FILE-PARTITION",
                    f"work_packages/{package_id}/allowed_files",
                    "任务 deliverable_files 并集必须恰等于工作包 allowed_files；"
                    f"缺失 {sorted(allowed - union)}，多余 {sorted(union - allowed)}",
                )
            )

    packages_by_module = index.packages_by_module
    for module_id, module in sorted(index.modules.items()):
        owns = {value for value in _as_list(module.get("owns_files")) if isinstance(value, str)}
        package_files: list[str] = []
        for package in packages_by_module.get(module_id, []):
            package_files.extend(
                value for value in _as_list(package.get("allowed_files")) if isinstance(value, str)
            )
        if len(package_files) != len(set(package_files)):
            errors.append(
                LintIssue(
                    "PLAN-FILE-PARTITION",
                    f"architecture/modules/{module_id}/owns_files",
                    f"模块 '{module_id}' 内工作包的 allowed_files 不互斥",
                )
            )
        if set(package_files) != owns:
            errors.append(
                LintIssue(
                    "PLAN-FILE-PARTITION",
                    f"architecture/modules/{module_id}/owns_files",
                    "工作包 allowed_files 并集必须恰等于模块 owns_files；"
                    f"缺失 {sorted(owns - set(package_files))}，"
                    f"多余 {sorted(set(package_files) - owns)}",
                )
            )


def _check_plan_contract_sets(index: _PlanIndex, errors: list[LintIssue]) -> None:
    """5.2.1/5.2.2：contract 集合三层等式、ready gate 条件与 provider 唯一性。"""
    tasks_by_package = index.tasks_by_package
    packages_by_module = index.packages_by_module
    for field_name in ("provides_contracts", "consumes_contracts"):
        for package_id, package in sorted(index.packages.items()):
            union = {
                value
                for task in tasks_by_package.get(package_id, [])
                for value in _as_list(task.get(field_name))
            }
            if union != set(_as_list(package.get(field_name))):
                errors.append(
                    LintIssue(
                        "PLAN-CONTRACT-SETS",
                        f"work_packages/{package_id}/{field_name}",
                        f"任务 {field_name} 并集必须等于工作包集合",
                    )
                )
        for module_id, module in sorted(index.modules.items()):
            union = {
                value
                for package in packages_by_module.get(module_id, [])
                for value in _as_list(package.get(field_name))
            }
            if union != set(_as_list(module.get(field_name))):
                errors.append(
                    LintIssue(
                        "PLAN-CONTRACT-SETS",
                        f"architecture/modules/{module_id}/{field_name}",
                        f"工作包 {field_name} 并集必须等于模块集合",
                    )
                )

    for contract_id, contract in sorted(index.contracts.items()):
        providers = sorted(
            task_id
            for task_id, task in index.tasks.items()
            if contract_id in _as_list(task.get("provides_contracts"))
        )
        if contract.get("ready_gate") == "s5":
            if providers:
                errors.append(
                    LintIssue(
                        "PLAN-CONTRACT-READY-GATE",
                        f"architecture/contracts/{contract_id}",
                        f"s5-ready contract 不得由任务提供，实际: {', '.join(providers)}",
                    )
                )
            continue
        if len(providers) != 1:
            errors.append(
                LintIssue(
                    "PLAN-CONTRACT-PROVIDER",
                    f"architecture/contracts/{contract_id}",
                    f"task-ready contract 必须恰有一个 provider task，实际 {len(providers)}",
                )
            )
            continue
        provider_task_id = providers[0]
        if contract.get("provider_task_id") != provider_task_id:
            errors.append(
                LintIssue(
                    "PLAN-CONTRACT-PROVIDER",
                    f"architecture/contracts/{contract_id}/provider_task_id",
                    f"provider_task_id 必须等于唯一提供任务 '{provider_task_id}'",
                )
            )
        provider_task = index.tasks[provider_task_id]
        if contract_id in _as_list(provider_task.get("consumes_contracts")):
            errors.append(
                LintIssue(
                    "PLAN-CONTRACT-PROVIDER",
                    f"tasks/{provider_task_id}/consumes_contracts",
                    f"provider task 不得同时消费 contract '{contract_id}'",
                )
            )
        owner_module = contract.get("owner")
        provider_package = index.packages.get(str(provider_task.get("work_package")), {})
        if provider_package.get("module") != owner_module:
            errors.append(
                LintIssue(
                    "PLAN-CONTRACT-OWNER",
                    f"architecture/contracts/{contract_id}/owner",
                    f"provider task '{provider_task_id}' 不在 owner 模块 '{owner_module}' 内",
                )
            )

    ancestors = _task_ancestors(index)
    for task_id, task in sorted(index.tasks.items()):
        for contract_id in _as_list(task.get("consumes_contracts")):
            consumed = index.contracts.get(str(contract_id))
            if consumed is None or consumed.get("ready_gate") != "task":
                continue
            consumed_provider = consumed.get("provider_task_id")
            if not isinstance(consumed_provider, str) or consumed_provider not in ancestors.get(
                task_id, set()
            ):
                errors.append(
                    LintIssue(
                        "PLAN-CONTRACT-CLOSURE",
                        f"tasks/{task_id}/consumes_contracts",
                        f"消费 contract '{contract_id}' 的任务缺少 provider ancestor",
                    )
                )

    for package_id, package in sorted(index.packages.items()):
        expected: set[str] = set()
        for contract_id in _as_list(package.get("consumes_contracts")):
            consumed = index.contracts.get(str(contract_id))
            consumed_provider = consumed.get("provider_task_id") if consumed else None
            if not isinstance(consumed_provider, str) or consumed_provider not in index.tasks:
                continue
            provider_package_id = str(index.tasks[consumed_provider].get("work_package"))
            if provider_package_id != package_id:
                expected.add(provider_package_id)
        if set(_as_list(package.get("depends_on"))) != expected:
            errors.append(
                LintIssue(
                    "PLAN-WORK-PACKAGE-DEPS",
                    f"work_packages/{package_id}/depends_on",
                    f"depends_on 必须恰等于跨包 provider 工作包集合 {sorted(expected)}",
                )
            )


def _task_ancestors(index: _PlanIndex) -> dict[str, set[str]]:
    """按任务 DAG 计算祖先闭包（含自身）；成环时按已解析部分返回。"""
    edges = {
        task_id: {
            dep
            for dep in _as_list(task.get("depends_on"))
            if isinstance(dep, str) and dep in index.tasks and dep != task_id
        }
        for task_id, task in index.tasks.items()
    }
    closure: dict[str, set[str]] = {}
    pending = dict(edges)
    progress = True
    while pending and progress:
        progress = False
        for task_id in sorted(pending):
            deps = pending[task_id]
            if not deps <= closure.keys():
                continue
            ancestors = {task_id}
            for dep in deps:
                ancestors |= closure[dep]
            closure[task_id] = ancestors
            del pending[task_id]
            progress = True
    return closure


def _check_plan_coverage(
    plan: dict[str, Any],
    index: _PlanIndex,
    spec: dict[str, Any],
    tests_manifest: list[Any] | None,
    config_snapshot: dict[str, Any] | None,
    report: LintReport,
) -> None:
    """5.2.3：coverage 必须可由 Linker 重算复现，acceptance 是其反向注入。"""
    coverage = _as_dict(plan.get("coverage"))
    nodeids = _manifest_nodeids(tests_manifest)
    if nodeids is not None:
        gates_by_req: dict[str, set[str]] = {}
        for raw in tests_manifest or []:
            entry = _as_dict(raw)
            gate = entry.get("gate")
            if not isinstance(gate, str):
                continue
            for req_id in _as_list(entry.get("req_ids")):
                if isinstance(req_id, str):
                    gates_by_req.setdefault(req_id, set()).add(gate)
        for raw in _as_list(spec.get("requirements")):
            requirement = _as_dict(raw)
            req_id = requirement.get("id")
            if (
                requirement.get("level") in _MUST_LEVELS
                and isinstance(req_id, str)
                and not (gates_by_req.get(req_id, set()) & {"task", "s7_only"})
            ):
                report.errors.append(
                    LintIssue(
                        "PLAN-NORMATIVE-TEST-GATE",
                        "coverage/requirements",
                        f"{req_id}: MUST/MUST NOT 必须关联 gate=task 或 s7_only 的测试",
                    )
                )
        for position, row_raw in enumerate(_as_list(coverage.get("tests"))):
            nodeid = _as_dict(row_raw).get("nodeid")
            if isinstance(nodeid, str) and nodeid not in nodeids:
                report.errors.append(
                    LintIssue(
                        "PLAN-TEST-MISSING",
                        f"coverage/tests/{position}/nodeid",
                        f"coverage 引用了清单外测试 '{nodeid}'",
                    )
                )
        for task_id, task in sorted(index.tasks.items()):
            acceptance = _as_dict(task.get("acceptance"))
            for position, nodeid in enumerate(_as_list(acceptance.get("tests"))):
                if isinstance(nodeid, str) and nodeid not in nodeids:
                    report.errors.append(
                        LintIssue(
                            "PLAN-TEST-MISSING",
                            f"tasks/{task_id}/acceptance/tests/{position}",
                            f"验收测试 '{nodeid}' 不在 Test Bundle 清单中",
                        )
                    )

    _check_plan_acceptance_injection(index, coverage, report.errors)

    if tests_manifest is None or config_snapshot is None:
        report.warnings.append(
            LintIssue(
                "PLAN-COVERAGE-UNCHECKED",
                "coverage",
                "缺少 Test Manifest 或 config snapshot，跳过 coverage 重算（5.2.5）",
            )
        )
        return
    try:
        expected = build_coverage(
            index.task_order,
            spec=spec,
            manifest={"tests": tests_manifest},
            contracts=list(_as_dict(plan.get("architecture")).get("contracts", [])),
            providers={
                contract_id: str(contract["provider_task_id"])
                for contract_id, contract in index.contracts.items()
                if isinstance(contract.get("provider_task_id"), str)
            },
            work_package_by_task={
                task_id: str(task.get("work_package"))
                for task_id, task in index.tasks.items()
            },
            config_snapshot=config_snapshot,
        )
    except (PlanDraftError, KeyError, TypeError) as exc:
        report.errors.append(
            LintIssue("PLAN-COVERAGE-MISMATCH", "coverage", f"coverage 无法重算: {exc}")
        )
        return
    if coverage != expected:
        report.errors.append(
            LintIssue(
                "PLAN-COVERAGE-MISMATCH",
                "coverage",
                "coverage 与由责任/DAG/Manifest/config snapshot 重算的结果不一致",
            )
        )


def _check_plan_acceptance_injection(
    index: _PlanIndex,
    coverage: dict[str, Any],
    errors: list[LintIssue],
) -> None:
    """5.2.3：每个 enabled 的 gate=task nodeid 恰出现在其绑定任务的 acceptance。"""
    expected: dict[str, set[str]] = {task_id: set() for task_id in index.tasks}
    for position, row_raw in enumerate(_as_list(coverage.get("tests"))):
        row = _as_dict(row_raw)
        nodeid = row.get("nodeid")
        task_id = row.get("task_id")
        if not isinstance(nodeid, str):
            continue
        if row.get("gate") != "task":
            if task_id is not None:
                errors.append(
                    LintIssue(
                        "PLAN-TEST-GATE",
                        f"coverage/tests/{position}/task_id",
                        f"gate={row.get('gate')} 的测试不得绑定任务",
                    )
                )
            continue
        if not isinstance(task_id, str) or task_id not in index.tasks:
            errors.append(
                LintIssue(
                    "PLAN-TEST-GATE",
                    f"coverage/tests/{position}/task_id",
                    f"gate=task 的测试必须绑定已存在的任务，实际 '{task_id}'",
                )
            )
            continue
        if row.get("enabled"):
            expected[task_id].add(nodeid)
    for task_id, task in sorted(index.tasks.items()):
        acceptance = _as_dict(task.get("acceptance"))
        actual = {value for value in _as_list(acceptance.get("tests")) if isinstance(value, str)}
        if actual != expected[task_id]:
            errors.append(
                LintIssue(
                    "PLAN-ACCEPTANCE-TESTS",
                    f"tasks/{task_id}/acceptance/tests",
                    "acceptance.tests 必须恰等于 coverage 中绑定本任务的 enabled 测试；"
                    f"缺失 {sorted(expected[task_id] - actual)}，多余 {sorted(actual - expected[task_id])}",
                )
            )
        if not _as_list(acceptance.get("build_variant_ids")):
            errors.append(
                LintIssue(
                    "PLAN-BUILD-GATE",
                    f"tasks/{task_id}/acceptance/build_variant_ids",
                    f"任务 '{task_id}' 必须至少绑定一个构建变体（5.2.2）",
                )
            )


# context_refs.kind -> spec 顶层集合（与 slice._SPEC_COLLECTIONS 一致）
_CONTEXT_KIND_COLLECTIONS: dict[str, str] = {
    "message": "messages",
    "type": "types",
    "requirement": "requirements",
}


def _responsibility_roles(item: dict[str, Any]) -> dict[str, str]:
    roles: dict[str, str] = {}
    for entry_raw in _as_list(item.get("requirement_responsibilities")):
        entry = _as_dict(entry_raw)
        req_id = entry.get("req_id")
        role = entry.get("role")
        if isinstance(req_id, str) and isinstance(role, str):
            roles[req_id] = role
    return roles


def _check_plan_responsibilities(
    index: _PlanIndex, spec: dict[str, Any], errors: list[LintIssue]
) -> None:
    """5.2.2：责任只存在于工作包/任务两层，且工作包→任务守恒。"""
    spec_reqs = {
        item["id"]: item
        for item in (_as_dict(raw) for raw in _as_list(spec.get("requirements")))
        if isinstance(item.get("id"), str)
    }
    tasks_by_package = index.tasks_by_package
    global_primaries: dict[str, list[str]] = {}
    for package_id, package in sorted(index.packages.items()):
        package_roles = _responsibility_roles(package)
        for declared_req_id in sorted(package_roles):
            if declared_req_id not in spec_reqs:
                errors.append(
                    LintIssue(
                        "PLAN-REQ-REF",
                        f"work_packages/{package_id}/requirement_responsibilities",
                        f"责任引用了 spec 中不存在的需求 '{declared_req_id}'",
                    )
                )
        task_roles: dict[str, list[str]] = {}
        for task in tasks_by_package.get(package_id, []):
            task_id = str(task.get("id"))
            entries = _as_list(task.get("requirement_responsibilities"))
            seen: set[str] = set()
            for entry_raw in entries:
                entry = _as_dict(entry_raw)
                req_id = entry.get("req_id")
                role = entry.get("role")
                if not isinstance(req_id, str) or not isinstance(role, str):
                    continue
                if req_id in seen:
                    errors.append(
                        LintIssue(
                            "PLAN-REQ-DUPLICATE",
                            f"tasks/{task_id}/requirement_responsibilities",
                            f"任务内需求 '{req_id}' 责任重复",
                        )
                    )
                seen.add(req_id)
                if req_id not in package_roles:
                    errors.append(
                        LintIssue(
                            "PLAN-REQ-OUT-OF-SCOPE",
                            f"tasks/{task_id}/requirement_responsibilities",
                            f"任务认领了所属工作包未获分配的需求 '{req_id}'",
                        )
                    )
                elif role == "primary" and package_roles[req_id] != "primary":
                    errors.append(
                        LintIssue(
                            "PLAN-REQ-ROLE",
                            f"tasks/{task_id}/requirement_responsibilities",
                            f"supporting 工作包内不允许 primary 任务责任 '{req_id}'",
                        )
                    )
                task_roles.setdefault(req_id, []).append(role)
                if role == "primary":
                    global_primaries.setdefault(req_id, []).append(task_id)
        for req_id, role in sorted(package_roles.items()):
            claimed = task_roles.get(req_id, [])
            if not claimed:
                errors.append(
                    LintIssue(
                        "PLAN-REQ-UNREFINED",
                        f"work_packages/{package_id}/requirement_responsibilities",
                        f"责任 '{req_id}' 未细化到本包任何任务",
                    )
                )
            elif role == "primary" and claimed.count("primary") != 1:
                errors.append(
                    LintIssue(
                        "PLAN-REQ-ROLE",
                        f"work_packages/{package_id}/requirement_responsibilities",
                        f"primary 责任 '{req_id}' 必须恰有一个 primary 任务",
                    )
                )

    for req_id, task_ids in sorted(global_primaries.items()):
        if len(task_ids) > 1:
            errors.append(
                LintIssue(
                    "PLAN-REQ-ROLE",
                    "tasks",
                    f"需求 '{req_id}' 有多个 primary 任务: {', '.join(sorted(task_ids))}",
                )
            )
    for req_id, requirement in sorted(spec_reqs.items()):
        if requirement.get("level") == "DEFINITION":
            continue
        if len(global_primaries.get(req_id, [])) != 1:
            errors.append(
                LintIssue(
                    "PLAN-REQ-UNCOVERED",
                    "work_packages",
                    f"非 DEFINITION 需求 '{req_id}' 必须恰有一个 primary 任务（5.2.2）",
                )
            )


def _check_plan_context_refs(
    index: _PlanIndex, spec: dict[str, Any], errors: list[LintIssue]
) -> None:
    """5.2.2：context_refs 必须可解析，且覆盖本层每项责任的 req_id。"""
    indexes: dict[str, dict[str, dict[str, Any]]] = {
        kind: {
            item["id"]: item
            for item in (_as_dict(raw) for raw in _as_list(spec.get(collection)))
            if isinstance(item.get("id"), str)
        }
        for kind, collection in _CONTEXT_KIND_COLLECTIONS.items()
    }
    holders: tuple[tuple[str, dict[str, dict[str, Any]]], ...] = (
        ("work_packages", index.packages),
        ("tasks", index.tasks),
    )
    for prefix, items in holders:
        for item_id, item in sorted(items.items()):
            reachable: set[str] = set()
            for position, ref_raw in enumerate(_as_list(item.get("context_refs"))):
                ref = _as_dict(ref_raw)
                ref_kind = ref.get("kind")
                ref_id = ref.get("id")
                if ref_kind == "interface_file" or ref_kind not in _CONTEXT_KIND_COLLECTIONS:
                    continue  # interface_file 指向 workspace；非法 kind 由 schema 报告
                element = indexes[str(ref_kind)].get(ref_id) if isinstance(ref_id, str) else None
                if element is None:
                    errors.append(
                        LintIssue(
                            "PLAN-REF-CONTEXT",
                            f"{prefix}/{item_id}/context_refs/{position}",
                            f"context_ref 引用了 spec 中不存在的 {ref_kind} '{ref_id}'",
                        )
                    )
                elif ref_kind == "requirement" and isinstance(ref_id, str):
                    reachable.add(ref_id)
                else:
                    reachable.update(element_req_ids(str(ref_kind), element))
            missing = sorted(set(_responsibility_roles(item)) - reachable)
            if missing:
                errors.append(
                    LintIssue(
                        "PLAN-REQ-CONTEXT",
                        f"{prefix}/{item_id}/context_refs",
                        f"责任需求 {missing} 不在 context slice closure 中（5.2.2）",
                    )
                )


# ---------------------------------------------------------------------------
# stage full plan_lint（5.2.5、6.4.5）
# ---------------------------------------------------------------------------


def plan_full_lint(
    plan: dict[str, Any],
    spec: dict[str, Any],
    *,
    constraints: dict[str, Any],
    blueprint: dict[str, Any],
    tests_manifest: list[Any],
    config_snapshot: dict[str, Any],
    expected_input_refs: dict[str, Any] | None = None,
    planning_index: dict[str, Any] | None = None,
) -> LintReport:
    """5.2.5 stage full lint：basic lint 叠加本次 run 的交付资产核对。

    额外检查文件类别与完整分区（对 Delivery Blueprint）、顶层 blueprint seal、
    build variant 合法性、测试 gate readiness 与规划预算 preflight。只有本函数
    可作为 S4 的发布门；CLI 必须重建四项冻结输入与 blueprint 才能调用。
    """
    report = plan_lint(
        plan,
        spec,
        tests_manifest=tests_manifest,
        expected_input_refs=expected_input_refs,
        config_snapshot=config_snapshot,
    )
    index = _plan_index(plan)
    _check_full_blueprint(plan, index, blueprint, report.errors)
    _check_full_delivery_graph(constraints, blueprint, report.errors)
    _check_full_build_variants(index, constraints, report.errors)
    _check_full_test_gates(plan, index, constraints, tests_manifest, report.errors)
    _check_full_budget(planning_index, report.errors)
    return report


def _check_full_delivery_graph(
    constraints: dict[str, Any],
    blueprint: dict[str, Any],
    errors: list[LintIssue],
) -> None:
    """6.4.1：复核已编译三段构建图与机械契约没有被替换或截断。"""
    artifacts = [
        item for item in _as_list(constraints.get("build_artifacts"))
        if isinstance(item, dict)
    ]
    if not artifacts:
        errors.append(
            LintIssue(
                "PLAN-DELIVERY-BUILD-GRAPH",
                "delivery_constraints/build_artifacts",
                "Delivery Constraints 必须包含已闭合的 build artifacts",
            )
        )
    artifact_ids = [item.get("id") for item in artifacts]
    artifact_paths = [item.get("path") for item in artifacts]
    if len(artifact_ids) != len(set(artifact_ids)) or len(artifact_paths) != len(
        set(artifact_paths)
    ):
        errors.append(
            LintIssue(
                "PLAN-DELIVERY-BUILD-GRAPH",
                "delivery_constraints/build_artifacts",
                "build artifact id 与输出路径必须分别唯一",
            )
        )
    slots = {
        item.get("path"): item
        for item in _as_list(constraints.get("file_slots"))
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    used_apps: list[str] = []
    for artifact in artifacts:
        source_paths = artifact.get("source_paths")
        if not isinstance(source_paths, list) or not source_paths:
            errors.append(
                LintIssue(
                    "PLAN-DELIVERY-BUILD-GRAPH",
                    f"delivery_constraints/build_artifacts/{artifact.get('id')}",
                    "build artifact 必须有非空已展开 source_paths",
                )
            )
            continue
        source_slots = [slots.get(path) for path in source_paths]
        if any(item is None or item.get("kind") not in {"app", "source"} for item in source_slots):
            errors.append(
                LintIssue(
                    "PLAN-DELIVERY-BUILD-GRAPH",
                    f"delivery_constraints/build_artifacts/{artifact.get('id')}/source_paths",
                    "链接源必须全部解析为 app/source file slot",
                )
            )
            continue
        apps = [
            str(item["path"])
            for item in source_slots
            if item is not None and item.get("kind") == "app"
        ]
        if len(apps) != 1:
            errors.append(
                LintIssue(
                    "PLAN-DELIVERY-BUILD-GRAPH",
                    f"delivery_constraints/build_artifacts/{artifact.get('id')}/source_paths",
                    "每个 build artifact 必须恰有一个 app 入口源",
                )
            )
        used_apps.extend(apps)
    all_apps = sorted(
        str(path) for path, item in slots.items() if item.get("kind") == "app"
    )
    if sorted(used_apps) != all_apps:
        errors.append(
            LintIssue(
                "PLAN-DELIVERY-BUILD-GRAPH",
                "delivery_constraints/build_artifacts",
                "全部 app file slot 必须各归属恰一个 build artifact",
            )
        )

    mechanical_rules = {
        str(item["rule_id"])
        for item in slots.values()
        if item.get("producer") == "mechanical_spec"
    }
    mechanical_users: list[str] = []
    for contract in _as_list(constraints.get("mechanical_generation_contracts")):
        if not isinstance(contract, dict):
            continue
        mechanical_users.extend(
            str(value) for value in _as_list(contract.get("output_rule_ids"))
        )
    if sorted(mechanical_users) != sorted(mechanical_rules):
        errors.append(
            LintIssue(
                "PLAN-DELIVERY-MECHANICAL",
                "delivery_constraints/mechanical_generation_contracts",
                "mechanical_spec rule 必须与机械生成契约一一闭合",
            )
        )
    if blueprint.get("build_artifacts") != artifacts:
        errors.append(
            LintIssue(
                "PLAN-DELIVERY-BUILD-GRAPH",
                "delivery_blueprint/build_artifacts",
                "Blueprint build artifacts 必须逐字段等于 Delivery Constraints",
            )
        )
    if blueprint.get("mechanical_generation_contracts") != constraints.get(
        "mechanical_generation_contracts"
    ):
        errors.append(
            LintIssue(
                "PLAN-DELIVERY-MECHANICAL",
                "delivery_blueprint/mechanical_generation_contracts",
                "Blueprint mechanical contracts 必须逐字段等于 Delivery Constraints",
            )
        )


def _check_full_blueprint(
    plan: dict[str, Any],
    index: _PlanIndex,
    blueprint: dict[str, Any],
    errors: list[LintIssue],
) -> None:
    """5.2.1/6.4.5：顶层 seal 绑定本次 blueprint，且文件类别与 owner 一致。"""
    expected_sha256 = blueprint.get("content_sha256")
    if plan.get("delivery_blueprint_sha256") != expected_sha256:
        errors.append(
            LintIssue(
                "PLAN-BLUEPRINT-HASH",
                "delivery_blueprint_sha256",
                "顶层 blueprint seal 与本次 Delivery Blueprint 的 content_sha256 不一致",
            )
        )
    owners: dict[str, str] = {}
    frozen: set[str] = set()
    for entry_raw in _as_list(blueprint.get("files")):
        entry = _as_dict(entry_raw)
        path = entry.get("path")
        if not isinstance(path, str):
            continue
        if entry.get("mutability") == "s5_frozen":
            frozen.add(path)
        owner = entry.get("owner_task_id")
        if isinstance(owner, str):
            owners[path] = owner
    for task_id, task in sorted(index.tasks.items()):
        for path in _as_list(task.get("deliverable_files")):
            if not isinstance(path, str):
                continue
            if path in frozen:
                errors.append(
                    LintIssue(
                        "PLAN-FILE-FROZEN",
                        f"tasks/{task_id}/deliverable_files",
                        f"任务不得写入 s5_frozen 文件 '{path}'",
                    )
                )
            elif path not in owners:
                errors.append(
                    LintIssue(
                        "PLAN-FILE-UNKNOWN",
                        f"tasks/{task_id}/deliverable_files",
                        f"文件 '{path}' 不在 Delivery Blueprint 的 s6_owned 槽位中",
                    )
                )
            elif owners[path] != task_id:
                errors.append(
                    LintIssue(
                        "PLAN-FILE-OWNER",
                        f"tasks/{task_id}/deliverable_files",
                        f"文件 '{path}' 的 blueprint owner 是 '{owners[path]}'",
                    )
                )
    missing = sorted(
        path
        for path, owner in owners.items()
        if owner not in index.tasks
        or path not in set(_as_list(index.tasks[owner].get("deliverable_files")))
    )
    if missing:
        errors.append(
            LintIssue(
                "PLAN-FILE-PARTITION",
                "tasks",
                f"Delivery Blueprint 的 s6_owned 文件缺少匹配任务: {missing}",
            )
        )


def _check_full_build_variants(
    index: _PlanIndex,
    constraints: dict[str, Any],
    errors: list[LintIssue],
) -> None:
    """5.2.2：任务构建变体必须存在，且覆盖 Language Profile 的 required 变体。"""
    variants = {
        entry["id"]: entry
        for entry in (_as_dict(raw) for raw in _as_list(constraints.get("build_variants")))
        if isinstance(entry.get("id"), str)
    }
    required = {key for key, entry in variants.items() if entry.get("required")}
    for task_id, task in sorted(index.tasks.items()):
        declared = {
            value
            for value in _as_list(_as_dict(task.get("acceptance")).get("build_variant_ids"))
            if isinstance(value, str)
        }
        unknown = sorted(declared - variants.keys())
        if unknown:
            errors.append(
                LintIssue(
                    "PLAN-REF-BUILD-VARIANT",
                    f"tasks/{task_id}/acceptance/build_variant_ids",
                    f"引用了未声明的构建变体 {unknown}",
                )
            )
        if not required <= declared:
            errors.append(
                LintIssue(
                    "PLAN-BUILD-GATE",
                    f"tasks/{task_id}/acceptance/build_variant_ids",
                    f"必须包含 required 构建变体 {sorted(required - declared)}",
                )
            )


def _check_full_test_gates(
    plan: dict[str, Any],
    index: _PlanIndex,
    constraints: dict[str, Any],
    tests_manifest: list[Any],
    errors: list[LintIssue],
) -> None:
    """5.2.3：每个 gate=task 测试的 contract/REQ 实现闭包在绑定任务处已就绪。"""
    coverage = _as_dict(plan.get("coverage"))
    binding = {
        str(_as_dict(row).get("nodeid")): _as_dict(row)
        for row in _as_list(coverage.get("tests"))
    }
    ancestors = _task_ancestors(index)
    s5_contracts = {
        contract_id
        for contract_id, contract in index.contracts.items()
        if contract.get("ready_gate") == "s5"
    }
    external_ids = {
        entry["id"]
        for entry in (_as_dict(raw) for raw in _as_list(constraints.get("external_contracts")))
        if isinstance(entry.get("id"), str)
    }
    implementers: dict[str, set[str]] = {}
    for task_id, task in index.tasks.items():
        for entry_raw in _as_list(task.get("requirement_responsibilities")):
            req_id = _as_dict(entry_raw).get("req_id")
            if isinstance(req_id, str):
                implementers.setdefault(req_id, set()).add(task_id)

    manifest_nodeids = {
        str(_as_dict(entry).get("nodeid")) for entry in tests_manifest if isinstance(entry, dict)
    }
    for nodeid in sorted(manifest_nodeids - binding.keys()):
        errors.append(
            LintIssue(
                "PLAN-COVERAGE-MISMATCH",
                "coverage/tests",
                f"coverage 必须包含 Manifest 全集，缺少 '{nodeid}'",
            )
        )
    for entry_raw in tests_manifest:
        entry = _as_dict(entry_raw)
        raw_nodeid = entry.get("nodeid")
        if not isinstance(raw_nodeid, str):
            continue
        nodeid = raw_nodeid
        row = binding.get(nodeid, {})
        unknown_contracts = sorted(set(_as_list(entry.get("required_contracts"))) - external_ids)
        if unknown_contracts:
            errors.append(
                LintIssue(
                    "PLAN-REF-CONTRACT",
                    f"tests/{nodeid}/required_contracts",
                    f"测试引用了 Target Profile 外的 contract {unknown_contracts}",
                )
            )
        if row.get("gate") != entry.get("gate"):
            errors.append(
                LintIssue(
                    "PLAN-TEST-GATE",
                    f"coverage/tests/{nodeid}/gate",
                    f"coverage gate 必须等于 Manifest 声明的 '{entry.get('gate')}'",
                )
            )
        if entry.get("gate") == "s5":
            invalid_s5_contracts = sorted(
                set(_as_list(entry.get("required_contracts"))) - s5_contracts
            )
            if invalid_s5_contracts:
                errors.append(
                    LintIssue(
                        "PLAN-TEST-GATE",
                        f"tests/{nodeid}/required_contracts",
                        f"gate=s5 只能依赖 s5-ready contracts: {invalid_s5_contracts}",
                    )
                )
        if entry.get("gate") != "task":
            continue
        bound_task_id = row.get("task_id")
        if not isinstance(bound_task_id, str) or bound_task_id not in ancestors:
            continue
        closure = ancestors[bound_task_id]
        for contract_id in sorted(set(_as_list(entry.get("required_contracts")))):
            if contract_id in s5_contracts:
                continue
            contract = index.contracts.get(str(contract_id), {})
            provider = contract.get("provider_task_id")
            if not isinstance(provider, str) or provider not in closure:
                errors.append(
                    LintIssue(
                        "PLAN-TEST-READINESS",
                        f"coverage/tests/{nodeid}/task_id",
                        f"测试绑定任务 '{bound_task_id}' 的闭包缺少 contract '{contract_id}' 的 provider",
                    )
                )
        for req_id in sorted(set(_as_list(entry.get("req_ids")))):
            missing = sorted(implementers.get(str(req_id), set()) - closure)
            if not implementers.get(str(req_id)):
                errors.append(
                    LintIssue(
                        "PLAN-TEST-READINESS",
                        f"coverage/tests/{nodeid}/task_id",
                        f"测试需求 '{req_id}' 没有任何实现任务",
                    )
                )
            elif missing:
                errors.append(
                    LintIssue(
                        "PLAN-TEST-READINESS",
                        f"coverage/tests/{nodeid}/task_id",
                        f"测试绑定任务 '{bound_task_id}' 的闭包缺少 '{req_id}' 的实现任务 {missing}",
                    )
                )


def _check_full_budget(
    planning_index: dict[str, Any] | None,
    errors: list[LintIssue],
) -> None:
    """6.4.3：规划上下文与预估输出必须落在配置上限内，无截断风险。"""
    if planning_index is None:
        return
    preflight = _as_dict(planning_index.get("preflight"))
    if not preflight:
        errors.append(
            LintIssue(
                "PLAN-BUDGET",
                "planning_index/preflight",
                "planning index 缺少 preflight 预算记录",
            )
        )
        return
    if preflight.get("fits") is not True:
        errors.append(
            LintIssue(
                "PLAN-BUDGET",
                "planning_index/preflight",
                f"规划上下文超出上限: 需要 {preflight.get('required_tokens')} "
                f"> {preflight.get('context_limit')}",
            )
        )
