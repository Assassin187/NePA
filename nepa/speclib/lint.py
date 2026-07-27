"""规格与计划校验器（M0-5）。

- ``spec_lint``：设计文档 5.1.8 的五类确定性检查（结构、引用完整、覆盖
  完整、词表合规、无孤儿元素），非 LLM。
- ``plan_lint``：5.2/6.4 的计划检查（DAG 无环、deliverable_files 互斥、
  acceptance 测试存在性、粒度、MUST 需求经 context_refs 可追溯）。

两者输出统一的结构化报告（错误/警告分级，S3 与 CI 共用，5.1.8）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from nepa.speclib.slice import element_req_ids

__all__ = ["LintIssue", "LintReport", "spec_lint", "plan_lint"]

_SCHEMA_DIR: Path = Path(__file__).resolve().parent.parent / "schemas"

# 5.1.2 内建原语
_PRIMITIVE_TYPES: frozenset[str] = frozenset(
    {"uint8", "uint16_be", "uint32_be", "bytes", "bitfield8"}
)

# 5.1.4 受限词表（与 specs-requirements.schema.json 的 pattern 保持一致）
_EVENT_RE: re.Pattern[str] = re.compile(
    r"^(recv:(?P<recv>[A-Za-z0-9_]+)|send:(?P<send>[A-Za-z0-9_]+)"
    r"|timer:[a-z0-9_-]+|api:[A-Za-z0-9_]+|transport:(connected|closed))$"
)
_ACTION_RE: re.Pattern[str] = re.compile(
    r"^(send:(?P<send>[A-Za-z0-9_]+)(\(.*\))?|close|start_timer:[a-z0-9_-]+"
    r"|stop_timer:[a-z0-9_-]+|deliver:[A-Za-z0-9_:-]+)$"
)
# 5.1.4 guard 语法："字段 比较符 常量" 的与组合；连接词取 && / and（保守选型）
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_FIELD_PATH = rf"{_IDENT}(?:\.{_IDENT})*"
_CONST = r"(?:0[xX][0-9A-Fa-f]+|-?[0-9]+(?:\.[0-9]+)?|\"[^\"]*\"|'[^']*'|true|false)"
_COMPARISON = rf"{_FIELD_PATH}\s*(?:==|!=|<=|>=|<|>)\s*{_CONST}"
_GUARD_RE: re.Pattern[str] = re.compile(
    rf"^\s*{_COMPARISON}(?:\s*(?:&&|and)\s+{_COMPARISON})*\s*$"
)

_MUST_LEVELS: frozenset[str] = frozenset({"MUST", "MUST NOT"})

# 5.2：任务粒度 SHOULD ≤ 4 文件
_MAX_TASK_FILES: int = 4


@dataclass(frozen=True)
class LintIssue:
    """单条校验问题：错误码、定位路径（斜杠分隔的 JSON 路径）、说明。"""

    code: str
    path: str
    message: str


@dataclass
class LintReport:
    """校验报告，错误/警告分级（5.1.8：结构化报告，S3 与 CI 共用）。"""

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


def _check_schema(
    instance: Any, schema_name: str, code: str, errors: list[LintIssue]
) -> None:
    """检查 1：JSON Schema draft 2020-12 结构校验（5 章通用约定）。"""
    schema_errors = sorted(
        _validator(schema_name).iter_errors(instance),
        key=lambda e: [str(p) for p in e.absolute_path],
    )
    for err in schema_errors:
        path = "/".join(str(p) for p in err.absolute_path)
        errors.append(LintIssue(code=code, path=path or "<root>", message=err.message))


def _manifest_nodeids(tests_manifest: list[Any] | None) -> set[str] | None:
    """归一化测试清单为 nodeid 集合。

    接受 5.3 tests_manifest.json 的 ``tests`` 条目数组（dict 含 nodeid），
    也容忍纯 nodeid 字符串列表。
    """
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
# spec_lint（5.1.8）
# ---------------------------------------------------------------------------

# 参与全局 id 唯一性检查的 spec 顶层集合（5 章通用约定）
_SPEC_ID_COLLECTIONS: tuple[str, ...] = (
    "types",
    "messages",
    "state_machines",
    "behaviors",
    "timers",
    "errors",
    "requirements",
)


def spec_lint(
    spec: dict,
    *,
    tests_manifest: list | None = None,
    gold_mode: bool = False,
) -> LintReport:
    """按 5.1.8 校验 Spec IR，返回结构化报告。

    :param spec: Spec IR（5.1 格式）。
    :param tests_manifest: gold 测试清单的条目数组（5.3 ``tests`` 字段）；
        提供时校验所有 ``covered_by.tests`` 引用（nodeid 前缀，5.1.6）存在。
    :param gold_mode: spec-run/gold 规格模式——每条 MUST/MUST NOT 需求的
        ``covered_by.tests`` 必须非空（5.1.8 检查 3）。
    """
    report = LintReport()
    # 检查 1：结构合法
    _check_schema(spec, "specs-requirements.schema.json", "SPEC-SCHEMA", report.errors)
    if not isinstance(spec, dict):
        return report

    _check_spec_duplicate_ids(spec, report.errors)
    _check_spec_references(spec, report.errors)
    _check_spec_coverage(spec, tests_manifest, gold_mode, report.errors)
    _check_spec_orphans(spec, report.errors)
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


def _message_tokens(spec: dict[str, Any]) -> set[str]:
    """event/actions 中报文名的合法取值：message 的 id 或 name（5.1.7 示例用 name）。"""
    tokens: set[str] = set()
    for msg in _as_list(spec.get("messages")):
        for key in ("id", "name"):
            value = _as_dict(msg).get(key)
            if isinstance(value, str):
                tokens.add(value)
    return tokens


def _iter_req_id_sites(spec: dict[str, Any]) -> list[tuple[str, Any]]:
    """列出全部 req_ids 引用位置：(路径, 值)。"""
    sites: list[tuple[str, Any]] = []

    def _collect(prefix: str, obj: Any) -> None:
        for j, rid in enumerate(_as_list(_as_dict(obj).get("req_ids"))):
            sites.append((f"{prefix}/req_ids/{j}", rid))

    for coll in ("types", "behaviors", "timers", "errors", "constants"):
        for i, item in enumerate(_as_list(spec.get(coll))):
            _collect(f"{coll}/{i}", item)
    for i, msg in enumerate(_as_list(spec.get("messages"))):
        _collect(f"messages/{i}", msg)
        for k, fld in enumerate(_as_list(_as_dict(msg).get("fields"))):
            _collect(f"messages/{i}/fields/{k}", fld)
    for i, sm in enumerate(_as_list(spec.get("state_machines"))):
        for k, tr in enumerate(_as_list(_as_dict(sm).get("transitions"))):
            _collect(f"state_machines/{i}/transitions/{k}", tr)
    return sites


def _check_spec_references(spec: dict[str, Any], errors: list[LintIssue]) -> None:
    """检查 2（引用完整）与检查 4（词表合规），单遍完成。"""
    type_ids = {
        t["id"]
        for t in _as_list(spec.get("types"))
        if isinstance(_as_dict(t).get("id"), str)
    }
    req_ids = _defined_req_ids(spec)
    msg_tokens = _message_tokens(spec)
    roles = {r for r in _as_list(_as_dict(spec.get("scope")).get("roles")) if isinstance(r, str)}

    # 2a：字段 type 是原语或 types 中的命名类型（5.1.3）
    for i, msg in enumerate(_as_list(spec.get("messages"))):
        for k, fld in enumerate(_as_list(_as_dict(msg).get("fields"))):
            ftype = _as_dict(fld).get("type")
            if isinstance(ftype, str) and ftype not in _PRIMITIVE_TYPES and ftype not in type_ids:
                errors.append(
                    LintIssue(
                        "SPEC-REF-TYPE",
                        f"messages/{i}/fields/{k}/type",
                        f"类型 '{ftype}' 既非内建原语（5.1.2）也未在 types 中定义",
                    )
                )

    # 2b：所有 req_ids 已定义
    for path, rid in _iter_req_id_sites(spec):
        if isinstance(rid, str) and rid not in req_ids:
            errors.append(
                LintIssue("SPEC-REF-REQ", path, f"req_id '{rid}' 未在 requirements 中定义")
            )

    # 2c/2d/4：状态机——角色、状态名、event/actions 报文名与词表、guard 语法
    for i, sm_raw in enumerate(_as_list(spec.get("state_machines"))):
        sm = _as_dict(sm_raw)
        role = sm.get("role")
        # 5.1.8 检查 2：role ∈ scope.roles（both 除外）
        if isinstance(role, str) and role != "both" and role not in roles:
            errors.append(
                LintIssue(
                    "SPEC-REF-ROLE",
                    f"state_machines/{i}/role",
                    f"角色 '{role}' 未在 scope.roles 中声明",
                )
            )
        states = {s for s in _as_list(sm.get("states")) if isinstance(s, str)}
        initial = sm.get("initial")
        if isinstance(initial, str) and initial not in states:
            errors.append(
                LintIssue(
                    "SPEC-REF-STATE",
                    f"state_machines/{i}/initial",
                    f"初始状态 '{initial}' 不在 states 中",
                )
            )
        for k, tr_raw in enumerate(_as_list(sm.get("transitions"))):
            tr = _as_dict(tr_raw)
            tr_path = f"state_machines/{i}/transitions/{k}"
            for end in ("from", "to"):
                state = tr.get(end)
                if isinstance(state, str) and state not in states:
                    errors.append(
                        LintIssue(
                            "SPEC-REF-STATE", f"{tr_path}/{end}", f"状态 '{state}' 不在 states 中"
                        )
                    )
            _check_transition_vocab(tr, tr_path, msg_tokens, errors)

    # 2e：behaviors 的 role（5.1.5 允许 both）
    for i, beh_raw in enumerate(_as_list(spec.get("behaviors"))):
        role = _as_dict(beh_raw).get("role")
        if isinstance(role, str) and role != "both" and role not in roles:
            errors.append(
                LintIssue(
                    "SPEC-REF-ROLE",
                    f"behaviors/{i}/role",
                    f"角色 '{role}' 未在 scope.roles 中声明",
                )
            )


def _check_transition_vocab(
    tr: dict[str, Any],
    tr_path: str,
    msg_tokens: set[str],
    errors: list[LintIssue],
) -> None:
    """检查 4：event/actions/guard 受限词表（5.1.4）；顺带做报文名引用检查（检查 2）。"""
    event = tr.get("event")
    if isinstance(event, str):
        match = _EVENT_RE.match(event)
        if match is None:
            errors.append(
                LintIssue(
                    "SPEC-VOCAB-EVENT",
                    f"{tr_path}/event",
                    f"event '{event}' 不符合 5.1.4 受限词表",
                )
            )
        else:
            msg = match.group("recv") or match.group("send")
            if msg is not None and msg not in msg_tokens:
                errors.append(
                    LintIssue(
                        "SPEC-REF-MSG",
                        f"{tr_path}/event",
                        f"event 引用了未定义的报文 '{msg}'",
                    )
                )
    for j, action in enumerate(_as_list(tr.get("actions"))):
        if not isinstance(action, str):
            continue
        match = _ACTION_RE.match(action)
        if match is None:
            errors.append(
                LintIssue(
                    "SPEC-VOCAB-ACTION",
                    f"{tr_path}/actions/{j}",
                    f"action '{action}' 不符合 5.1.4 受限词表",
                )
            )
        else:
            msg = match.group("send")
            if msg is not None and msg not in msg_tokens:
                errors.append(
                    LintIssue(
                        "SPEC-REF-MSG",
                        f"{tr_path}/actions/{j}",
                        f"action 引用了未定义的报文 '{msg}'",
                    )
                )
    guard = tr.get("guard")
    if isinstance(guard, str) and _GUARD_RE.match(guard) is None:
        errors.append(
            LintIssue(
                "SPEC-VOCAB-GUARD",
                f"{tr_path}/guard",
                f"guard '{guard}' 不符合 5.1.4 语法（字段 比较符 常量 的与组合）",
            )
        )


def _check_spec_coverage(
    spec: dict[str, Any],
    tests_manifest: list[Any] | None,
    gold_mode: bool,
    errors: list[LintIssue],
) -> None:
    """检查 3（覆盖完整，按 5.1.8 修订版）。"""
    nodeids = _manifest_nodeids(tests_manifest)
    for i, req_raw in enumerate(_as_list(spec.get("requirements"))):
        req = _as_dict(req_raw)
        req_id = req.get("id", f"requirements[{i}]")
        covered_by = _as_dict(req.get("covered_by"))
        elements = _as_list(covered_by.get("elements"))
        tests = _as_list(covered_by.get("tests"))
        if req.get("level") in _MUST_LEVELS:
            if not elements:
                errors.append(
                    LintIssue(
                        "SPEC-COV-ELEMENTS",
                        f"requirements/{i}/covered_by/elements",
                        f"{req_id}: MUST/MUST NOT 需求的 covered_by.elements 不得为空",
                    )
                )
            if gold_mode and not tests:
                errors.append(
                    LintIssue(
                        "SPEC-COV-TESTS",
                        f"requirements/{i}/covered_by/tests",
                        f"{req_id}: gold 模式下 MUST/MUST NOT 需求的 covered_by.tests 不得为空",
                    )
                )
        if nodeids is not None:
            # 5.1.6：covered_by.tests 为 pytest nodeid 前缀，故做前缀匹配
            for j, ref in enumerate(tests):
                if not isinstance(ref, str):
                    continue
                if not any(nodeid == ref or nodeid.startswith(ref) for nodeid in nodeids):
                    errors.append(
                        LintIssue(
                            "SPEC-COV-TEST-MISSING",
                            f"requirements/{i}/covered_by/tests/{j}",
                            f"{req_id}: 测试引用 '{ref}' 不在 gold 测试清单中",
                        )
                    )


def _check_spec_orphans(spec: dict[str, Any], errors: list[LintIssue]) -> None:
    """检查 5：每个 message/behavior/transition 至少关联一条需求。"""

    def _orphan(path: str, kind: str, obj: Any) -> None:
        if not _as_list(_as_dict(obj).get("req_ids")):
            errors.append(LintIssue("SPEC-ORPHAN", path, f"{kind}未关联任何需求（req_ids 为空）"))

    for i, msg in enumerate(_as_list(spec.get("messages"))):
        _orphan(f"messages/{i}", "message ", msg)
    for i, beh in enumerate(_as_list(spec.get("behaviors"))):
        _orphan(f"behaviors/{i}", "behavior ", beh)
    for i, sm in enumerate(_as_list(spec.get("state_machines"))):
        for k, tr in enumerate(_as_list(_as_dict(sm).get("transitions"))):
            _orphan(f"state_machines/{i}/transitions/{k}", "transition ", tr)


# ---------------------------------------------------------------------------
# plan_lint（5.2 / 6.4）
# ---------------------------------------------------------------------------


def plan_lint(
    plan: dict,
    spec: dict,
    tests_manifest: list | None = None,
) -> LintReport:
    """按 5.2/6.4 校验 plan.json。

    检查：结构；task id 唯一；depends_on 引用存在且 DAG 无环；
    deliverable_files 互斥（脚手架任务持有的接口头文件 ``*.h`` 豁免）；
    acceptance 测试在 gold 清单中存在（未提供清单时跳过）；粒度 ≤ 4 文件
    （SHOULD 级 → warning）；每条 MUST/MUST NOT 需求经 context_refs 可追溯
    到至少一个任务。
    """
    report = LintReport()
    _check_schema(plan, "plan.schema.json", "PLAN-SCHEMA", report.errors)
    if not isinstance(plan, dict):
        return report

    tasks = [t for t in _as_list(plan.get("tasks")) if isinstance(t, dict)]
    _check_plan_duplicate_ids(tasks, report.errors)
    _check_plan_dag(tasks, report.errors)
    _check_plan_file_exclusivity(tasks, report.errors)
    _check_plan_acceptance(tasks, tests_manifest, report.errors)
    _check_plan_granularity(tasks, report.warnings)
    _check_plan_modules(plan, tasks, report.warnings)
    _check_plan_req_coverage(tasks, spec, report.errors)
    return report


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
        errors.append(
            LintIssue("PLAN-CYCLE", "tasks", f"任务依赖图存在环，涉及任务: {cycle_ids}")
        )


def _check_plan_file_exclusivity(
    tasks: list[dict[str, Any]], errors: list[LintIssue]
) -> None:
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


def _check_plan_granularity(
    tasks: list[dict[str, Any]], warnings: list[LintIssue]
) -> None:
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
        m["id"]
        for m in _as_list(plan.get("modules"))
        if isinstance(_as_dict(m).get("id"), str)
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
    "state_machine": "state_machines",
    "behavior": "behaviors",
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
            kind = ref.get("kind")
            ref_id = ref.get("id")
            if kind == "interface_file" or kind not in _CONTEXT_KIND_COLLECTIONS:
                continue  # interface_file 指向 workspace；非法 kind 由 schema 报告
            element = indexes[kind].get(ref_id) if isinstance(ref_id, str) else None
            if element is None:
                errors.append(
                    LintIssue(
                        "PLAN-REF-CONTEXT",
                        f"tasks/{i}/context_refs/{j}",
                        f"context_ref 引用了 spec 中不存在的 {kind} '{ref_id}'",
                    )
                )
            else:
                covered.update(element_req_ids(str(kind), element))

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
