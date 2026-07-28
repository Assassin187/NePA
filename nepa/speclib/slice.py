"""Spec 切片器（设计文档 6.6.2）。

把任务的 ``context_refs`` 解析为 Coder 上下文包第 2 项："Spec 切片——
context_refs 解析出的 JSON 片段（含关联 REQ 全文）"。只处理指向 spec 元素
的引用（message/type/requirement，见 5.2 context_refs 词表）；
``interface_file`` 引用属于上下文包第 3 项（workspace 头文件），本模块跳过。
"""

from __future__ import annotations

import copy
from typing import Any

__all__ = ["element_req_ids", "resolve_refs"]

# 5.2 context_refs.kind -> spec 顶层集合名（interface_file 不指向 spec）
_SPEC_COLLECTIONS: dict[str, str] = {
    "message": "messages",
    "type": "types",
    "requirement": "requirements",
}


def _as_list(value: Any) -> list[Any]:
    """容错取列表：非 list 一律按空列表处理（结构错误由 schema 校验报告）。"""
    return value if isinstance(value, list) else []


def _str_items(value: Any) -> list[str]:
    return [x for x in _as_list(value) if isinstance(x, str)]


def element_req_ids(kind: str, element: dict[str, Any]) -> list[str]:
    """收集一个 spec 元素直接关联的 req_ids（去重、保序）。

    按 5.1.2/5.1.3：message 含报文级与字段级 req_ids，type 取自身
    req_ids。requirement 本身就是证据条目，不再复制为关联需求。
    """
    collected: list[str] = list(_str_items(element.get("req_ids")))
    if kind == "message":
        for field in _as_list(element.get("fields")):
            if isinstance(field, dict):
                collected.extend(_str_items(field.get("req_ids")))
    return list(dict.fromkeys(collected))


def _index_by_id(spec: dict[str, Any], collection: str) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in _as_list(spec.get(collection)):
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            index.setdefault(item["id"], item)
    return index


def resolve_refs(spec: dict[str, Any], context_refs: list[dict[str, Any]]) -> dict[str, Any]:
    """按 6.6.2 解析 context_refs，返回 Spec 切片 JSON 片段。

    返回结构::

        {
          "slices": [{"kind": ..., "id": ..., "element": <元素全文深拷贝>}, ...],
          "requirements": [<关联 REQ 条目全文深拷贝>, ...]  # 按 id 排序、去重
        }

    - ``interface_file`` 引用跳过（6.6.2 上下文包第 3 项，由 workspace 组装）；
    - 未知 kind 或 spec 中不存在的 id 抛 ``ValueError``（plan_lint 应已拦截）；
    - 返回片段均为深拷贝，调用方可安全裁剪而不污染 spec。
    """
    indexes: dict[str, dict[str, dict[str, Any]]] = {
        kind: _index_by_id(spec, collection) for kind, collection in _SPEC_COLLECTIONS.items()
    }
    req_index: dict[str, dict[str, Any]] = _index_by_id(spec, "requirements")

    slices: list[dict[str, Any]] = []
    seen_refs: set[tuple[str, str]] = set()
    linked_req_ids: list[str] = []

    for ref in context_refs:
        if not isinstance(ref, dict):
            raise TypeError(f"context_ref 必须是对象，得到: {ref!r}")
        kind = ref.get("kind")
        ref_id = ref.get("id")
        if kind == "interface_file":
            continue  # 6.6.2：接口头文件走 workspace，不在 spec 切片内
        if not isinstance(kind, str) or kind not in _SPEC_COLLECTIONS:
            raise ValueError(f"未知 context_ref kind: {kind!r}")
        if not isinstance(ref_id, str):
            raise TypeError(f"context_ref id 必须是字符串，得到: {ref_id!r}")
        element = indexes[kind].get(ref_id)
        if element is None:
            raise ValueError(f"spec 中不存在 {kind} 元素: {ref_id!r}")
        if (kind, ref_id) in seen_refs:
            continue
        seen_refs.add((kind, ref_id))
        slices.append({"kind": kind, "id": ref_id, "element": copy.deepcopy(element)})
        linked_req_ids.extend(element_req_ids(kind, element))

    # 关联 REQ 全文（6.6.2 第 2 行"含关联 REQ 全文"）：去重后按 id 排序
    unique_ids = sorted(set(linked_req_ids))
    requirements = [copy.deepcopy(req_index[rid]) for rid in unique_ids if rid in req_index]
    return {"slices": slices, "requirements": requirements}
