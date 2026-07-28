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

from nepa.speclib.slice import element_req_ids

__all__ = ["LintIssue", "LintReport", "plan_lint", "spec_lint"]

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


def plan_lint(
    plan: dict,
    spec: dict,
    tests_manifest: list | None = None,
    expected_input_refs: dict[str, Any] | None = None,
) -> LintReport:
    """按 5.2/6.4 校验 plan.json。

    检查：结构；input_refs 与调用方提供的冻结输入一致（提供时）；task id
    唯一；depends_on 引用存在且 DAG 无环；
    deliverable_files 互斥（脚手架任务持有的接口头文件 ``*.h`` 豁免）；
    acceptance 测试在 Test Bundle 清单中存在（未提供清单时跳过）；粒度 ≤ 4 文件
    （SHOULD 级 → warning）；每条 MUST/MUST NOT 需求经 context_refs 可追溯
    到至少一个任务。
    """
    report = LintReport()
    _check_schema(plan, "plan.schema.json", "PLAN-SCHEMA", report.errors)
    if not isinstance(plan, dict):
        return report

    tasks = [t for t in _as_list(plan.get("tasks")) if isinstance(t, dict)]
    _check_plan_input_refs(plan, expected_input_refs, report.errors)
    _check_plan_duplicate_ids(tasks, report.errors)
    _check_plan_dag(tasks, report.errors)
    _check_plan_file_exclusivity(tasks, report.errors)
    _check_plan_acceptance(tasks, tests_manifest, report.errors)
    _check_plan_granularity(tasks, report.warnings)
    _check_plan_modules(plan, tasks, report.warnings)
    _check_plan_req_coverage(tasks, spec, report.errors)
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


def _check_plan_duplicate_ids(tasks: list[dict[str, Any]], errors: list[LintIssue]) -> None:
    seen: dict[str, str] = {}
    for i, task in enumerate(tasks):
        tid = task.get("id")
        if not isinstance(tid, str):
            continue
        path = f"tasks/{i}/id"
        if tid in seen:
            errors.append(
                LintIssue("PLAN-DUP-ID", path, f"任务 id '{tid}' 重复（先见于 {seen[tid]}）")
            )
        else:
            seen[tid] = path


def _check_plan_dag(tasks: list[dict[str, Any]], errors: list[LintIssue]) -> None:
    """6.4：depends_on 引用存在且构成 DAG（无环，Kahn 算法）。"""
    known = {t["id"] for t in tasks if isinstance(t.get("id"), str)}
    deps: dict[str, set[str]] = {}
    for i, task in enumerate(tasks):
        tid = task.get("id")
        if not isinstance(tid, str):
            continue
        dep_set: set[str] = set()
        for j, dep in enumerate(_as_list(task.get("depends_on"))):
            if not isinstance(dep, str) or dep not in known:
                errors.append(
                    LintIssue(
                        "PLAN-REF-TASK",
                        f"tasks/{i}/depends_on/{j}",
                        f"depends_on 引用了不存在的任务 '{dep}'",
                    )
                )
            else:
                dep_set.add(dep)
        deps[tid] = dep_set

    remaining = {tid: set(dep_set) for tid, dep_set in deps.items()}
    ready = [tid for tid, dep_set in remaining.items() if not dep_set]
    while ready:
        done = ready.pop()
        remaining.pop(done, None)
        for tid, dep_set in remaining.items():
            if done in dep_set:
                dep_set.discard(done)
                if not dep_set:
                    ready.append(tid)
    if remaining:
        cycle_ids = ", ".join(sorted(remaining))
        errors.append(LintIssue("PLAN-CYCLE", "tasks", f"任务依赖图存在环，涉及任务: {cycle_ids}"))


def _check_plan_file_exclusivity(tasks: list[dict[str, Any]], errors: list[LintIssue]) -> None:
    """5.2/6.4：同一文件只归一个任务；接口头文件（*.h）且有脚手架任务持有时豁免。"""
    holders: dict[str, list[tuple[str, str]]] = {}
    for task in tasks:
        tid = str(task.get("id"))
        kind = str(task.get("kind"))
        for f in _as_list(task.get("deliverable_files")):
            if isinstance(f, str):
                entry = (tid, kind)
                bucket = holders.setdefault(f, [])
                if entry not in bucket:
                    bucket.append(entry)
    for f, bucket in holders.items():
        owner_ids = sorted({tid for tid, _ in bucket})
        if len(owner_ids) <= 1:
            continue
        if f.endswith(".h") and any(kind == "scaffold" for _, kind in bucket):
            continue  # 6.4：接口头文件归属脚手架，豁免互斥
        errors.append(
            LintIssue(
                "PLAN-FILE-CONFLICT",
                "tasks",
                f"文件 '{f}' 出现在多个任务的 deliverable_files: {', '.join(owner_ids)}",
            )
        )


def _check_plan_acceptance(
    tasks: list[dict[str, Any]],
    tests_manifest: list[Any] | None,
    errors: list[LintIssue],
) -> None:
    """6.4：acceptance 引用的测试在 gold 清单中存在（精确 nodeid，5.2）。"""
    nodeids = _manifest_nodeids(tests_manifest)
    if nodeids is None:
        return
    for i, task in enumerate(tasks):
        acceptance = _as_dict(task.get("acceptance"))
        for j, test in enumerate(_as_list(acceptance.get("tests"))):
            if isinstance(test, str) and test not in nodeids:
                errors.append(
                    LintIssue(
                        "PLAN-TEST-MISSING",
                        f"tasks/{i}/acceptance/tests/{j}",
                        f"验收测试 '{test}' 不在 gold 测试清单中",
                    )
                )


def _check_plan_granularity(tasks: list[dict[str, Any]], warnings: list[LintIssue]) -> None:
    """5.2：单任务 deliverable_files 应当 ≤ 4（SHOULD 级 → warning）。"""
    for i, task in enumerate(tasks):
        files = _as_list(task.get("deliverable_files"))
        if len(files) > _MAX_TASK_FILES:
            warnings.append(
                LintIssue(
                    "PLAN-GRANULARITY",
                    f"tasks/{i}/deliverable_files",
                    f"任务 '{task.get('id')}' 交付 {len(files)} 个文件，"
                    f"超过粒度约束 {_MAX_TASK_FILES}（5.2 SHOULD）",
                )
            )


def _check_plan_modules(
    plan: dict[str, Any], tasks: list[dict[str, Any]], warnings: list[LintIssue]
) -> None:
    """task.module 应指向已声明模块（5.2；未在 6.4 检查清单内，降为 warning）。"""
    module_ids = {
        m["id"] for m in _as_list(plan.get("modules")) if isinstance(_as_dict(m).get("id"), str)
    }
    for i, task in enumerate(tasks):
        module = task.get("module")
        if isinstance(module, str) and module not in module_ids:
            warnings.append(
                LintIssue(
                    "PLAN-REF-MODULE",
                    f"tasks/{i}/module",
                    f"任务 '{task.get('id')}' 的模块 '{module}' 未在 modules 中声明",
                )
            )


# context_refs.kind -> spec 顶层集合（与 slice._SPEC_COLLECTIONS 一致）
_CONTEXT_KIND_COLLECTIONS: dict[str, str] = {
    "message": "messages",
    "type": "types",
    "requirement": "requirements",
}


def _check_plan_req_coverage(
    tasks: list[dict[str, Any]], spec: dict[str, Any], errors: list[LintIssue]
) -> None:
    """6.4：每条 MUST 级需求必须能经某任务的 context_refs 追溯到。"""
    indexes: dict[str, dict[str, dict[str, Any]]] = {}
    for kind, collection in _CONTEXT_KIND_COLLECTIONS.items():
        indexes[kind] = {
            item["id"]: item
            for item in _as_list(spec.get(collection))
            if isinstance(_as_dict(item).get("id"), str)
        }

    covered: set[str] = set()
    for i, task in enumerate(tasks):
        for j, ref_raw in enumerate(_as_list(task.get("context_refs"))):
            ref = _as_dict(ref_raw)
            ref_kind = ref.get("kind")
            ref_id = ref.get("id")
            if ref_kind == "interface_file" or ref_kind not in _CONTEXT_KIND_COLLECTIONS:
                continue  # interface_file 指向 workspace；非法 kind 由 schema 报告
            element = indexes[ref_kind].get(ref_id) if isinstance(ref_id, str) else None
            if element is None:
                errors.append(
                    LintIssue(
                        "PLAN-REF-CONTEXT",
                        f"tasks/{i}/context_refs/{j}",
                        f"context_ref 引用了 spec 中不存在的 {ref_kind} '{ref_id}'",
                    )
                )
            else:
                if ref_kind == "requirement" and isinstance(ref_id, str):
                    covered.add(ref_id)
                else:
                    covered.update(element_req_ids(str(ref_kind), element))

    for i, req_raw in enumerate(_as_list(spec.get("requirements"))):
        req = _as_dict(req_raw)
        req_id = req.get("id")
        if req.get("level") in _MUST_LEVELS and isinstance(req_id, str) and req_id not in covered:
            errors.append(
                LintIssue(
                    "PLAN-REQ-UNCOVERED",
                    f"requirements/{i}",
                    f"MUST 级需求 '{req_id}' 无法经任何任务的 context_refs 追溯（6.4）",
                )
            )
