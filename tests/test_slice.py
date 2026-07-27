"""resolve_refs 单元测试（设计文档 6.6.2 上下文包第 2 项：Spec 切片）。"""

from __future__ import annotations

from typing import Any

import pytest

from nepa.speclib.slice import element_req_ids, resolve_refs
from tests.test_spec_lint import make_mini_spec


def _req_ids(result: dict[str, Any]) -> set[str]:
    return {r["id"] for r in result["requirements"]}


def test_resolve_message_and_type_returns_fragments_with_reqs() -> None:
    """message + type 引用返回元素全文与关联 REQ 全文（6.6.2）。"""
    spec = make_mini_spec()
    result = resolve_refs(
        spec,
        [{"kind": "message", "id": "connect"}, {"kind": "type", "id": "mqtt_varint"}],
    )
    assert [s["id"] for s in result["slices"]] == ["connect", "mqtt_varint"]
    connect = result["slices"][0]["element"]
    assert connect["name"] == "CONNECT"
    assert connect["fields"][0]["name"] == "remaining_length"
    # message 级 + 字段级 req_ids 均纳入（5.1.3）
    assert _req_ids(result) == {"REQ-FRAME-001", "REQ-CONNECT-002"}
    # REQ 全文：条目含 text/level/source_ref
    req = result["requirements"][0]
    assert req["text"]
    assert req["level"] == "MUST"
    assert req["source_ref"]["section"]


def test_resolve_state_machine_collects_transition_reqs() -> None:
    """state_machine 切片汇集各 transition 的 req_ids（5.1.4）。"""
    result = resolve_refs(make_mini_spec(), [{"kind": "state_machine", "id": "broker_session"}])
    assert _req_ids(result) == {"REQ-CONNECT-002", "REQ-STATE-001"}


def test_resolve_behavior() -> None:
    """behavior 切片返回条款全文与其 REQ。"""
    result = resolve_refs(make_mini_spec(), [{"kind": "behavior", "id": "BEH-BROKER-001"}])
    assert result["slices"][0]["element"]["observable_check"]
    assert _req_ids(result) == {"REQ-PUB-001"}


def test_interface_file_refs_are_skipped() -> None:
    """interface_file 属上下文包第 3 项（workspace 头文件），切片器跳过（6.6.2）。"""
    result = resolve_refs(
        make_mini_spec(), [{"kind": "interface_file", "id": "include/mqtt/mqtt_codec.h"}]
    )
    assert result["slices"] == []
    assert result["requirements"] == []


def test_requirements_deduped_and_sorted() -> None:
    """重复引用去重；requirements 按 id 排序输出。"""
    spec = make_mini_spec()
    result = resolve_refs(
        spec,
        [
            {"kind": "message", "id": "connect"},
            {"kind": "message", "id": "connect"},
            {"kind": "state_machine", "id": "broker_session"},
        ],
    )
    assert [s["id"] for s in result["slices"]] == ["connect", "broker_session"]
    ids = [r["id"] for r in result["requirements"]]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))


def test_unknown_id_raises_value_error() -> None:
    """spec 中不存在的元素 id → ValueError（plan_lint 应已拦截）。"""
    with pytest.raises(ValueError, match="nonexistent"):
        resolve_refs(make_mini_spec(), [{"kind": "message", "id": "nonexistent"}])


def test_unknown_kind_raises_value_error() -> None:
    """未知 kind → ValueError。"""
    with pytest.raises(ValueError, match="kind"):
        resolve_refs(make_mini_spec(), [{"kind": "banana", "id": "connect"}])


def test_fragments_are_deep_copies() -> None:
    """返回片段是深拷贝，调用方裁剪不污染 spec（6.6.2 上下文组装可安全复用）。"""
    spec = make_mini_spec()
    result = resolve_refs(spec, [{"kind": "message", "id": "connect"}])
    result["slices"][0]["element"]["name"] = "MUTATED"
    result["requirements"][0]["text"] = "MUTATED"
    assert spec["messages"][0]["name"] == "CONNECT"
    assert all(r["text"] != "MUTATED" for r in spec["requirements"])


def test_element_req_ids_message_includes_field_level() -> None:
    """element_req_ids：message 汇集报文级与字段级 req_ids，去重保序。"""
    spec = make_mini_spec()
    assert element_req_ids("message", spec["messages"][0]) == [
        "REQ-CONNECT-002",
        "REQ-FRAME-001",
    ]
