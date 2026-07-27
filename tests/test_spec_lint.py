"""spec_lint 单元测试（设计文档 5.1.8 五类检查）。

含共享 fixture 构造器 make_mini_spec / make_mini_manifest，
供 test_plan_lint.py 与 test_slice.py 复用。
"""

from __future__ import annotations

from typing import Any

from nepa.speclib.lint import LintReport, spec_lint


def make_mini_spec() -> dict[str, Any]:
    """构造一份通过全部 5.1.8 检查的最小合法 Spec IR（5.1 格式）。"""
    return {
        "schema_version": "2.0",
        "meta": {
            "protocol_name": "mqtt",
            "protocol_version": "3.1.1",
            "source": {"kind": "manual"},
            "created_at": "2026-07-26T12:00:00Z",
        },
        "scope": {
            "roles": ["client", "broker"],
            "features_included": ["connect", "publish_qos0"],
            "features_excluded": [
                {"name": "qos1_qos2", "reason": "M0 功能子集仅覆盖 QoS 0（7.1）"}
            ],
        },
        "transport": {"layer": "tcp", "default_port": 1883, "byte_order": "big_endian"},
        "types": [
            {
                "id": "mqtt_varint",
                "name": "MQTT Remaining Length varint",
                "encoding": {"kind": "varint", "max_bytes": 4},
                "req_ids": ["REQ-FRAME-001"],
            }
        ],
        "messages": [
            {
                "id": "connect",
                "name": "CONNECT",
                "packet_type_code": 1,
                "direction": "client_to_server",
                "wire_layout": ["fixed_header", "variable_header"],
                "fields": [
                    {
                        "name": "remaining_length",
                        "loc": "fixed_header",
                        "type": "mqtt_varint",
                        "derived": {"kind": "length_of", "of": ["variable_header"]},
                        "req_ids": ["REQ-FRAME-001"],
                    },
                    {
                        "name": "protocol_level",
                        "loc": "variable_header",
                        "type": "uint8",
                        "constraint": {"const": 4},
                        "on_violation": {"action": "connack_then_close", "connack_rc": 1},
                        "req_ids": ["REQ-CONNECT-002"],
                    },
                ],
                "req_ids": ["REQ-CONNECT-002"],
            },
            {
                "id": "connack",
                "name": "CONNACK",
                "packet_type_code": 2,
                "direction": "server_to_client",
                "wire_layout": ["fixed_header", "variable_header"],
                "fields": [
                    {
                        "name": "return_code",
                        "loc": "variable_header",
                        "type": "uint8",
                        "req_ids": ["REQ-CONNECT-002"],
                    }
                ],
                "req_ids": ["REQ-CONNECT-002"],
            },
        ],
        "state_machines": [
            {
                "id": "broker_session",
                "role": "broker",
                "states": ["wait_connect", "connected", "closed"],
                "initial": "wait_connect",
                "transitions": [
                    {
                        "from": "wait_connect",
                        "event": "recv:CONNECT",
                        "guard": "connect.protocol_level != 4",
                        "actions": ["send:CONNACK(rc=1)", "close"],
                        "to": "closed",
                        "req_ids": ["REQ-CONNECT-002"],
                    },
                    {
                        "from": "connected",
                        "event": "recv:CONNECT",
                        "actions": ["close"],
                        "to": "closed",
                        "req_ids": ["REQ-STATE-001"],
                    },
                ],
            }
        ],
        "behaviors": [
            {
                "id": "BEH-BROKER-001",
                "role": "broker",
                "trigger": "收到 PUBLISH（QoS 0），存在主题完全匹配的订阅者",
                "requirement": "MUST 将消息转发给每个匹配订阅者各一次",
                "level": "MUST",
                "observable_check": "A 订阅 t/1，B 发布一条 QoS0，A 在 2 秒内收到且仅收到一条",
                "req_ids": ["REQ-PUB-001"],
            }
        ],
        "requirements": [
            {
                "id": "REQ-FRAME-001",
                "text": "剩余长度 MUST 采用 7 bit 分组继续位编码，最多 4 字节",
                "level": "MUST",
                "category": "syntax",
                "source_ref": {"section": "2.2.3", "quote": "variable length encoding"},
                "covered_by": {
                    "elements": ["types/mqtt_varint"],
                    "tests": ["l1_codec/test_varint.py::test_remaining_length_roundtrip"],
                },
            },
            {
                "id": "REQ-CONNECT-002",
                "text": "protocol_level 不为 4 时 MUST 回复 rc=1 的 CONNACK 后断开",
                "level": "MUST",
                "category": "semantics",
                "source_ref": {"section": "3.1.2.2", "quote": "unacceptable protocol level"},
                "covered_by": {
                    "elements": ["messages/connect/fields/protocol_level"],
                    "tests": ["l2_behavior/test_connect.py::test_bad_protocol_level"],
                },
            },
            {
                "id": "REQ-STATE-001",
                "text": "同一连接收到第二个 CONNECT MUST 断开连接",
                "level": "MUST",
                "category": "semantics",
                "source_ref": {"section": "3.1", "quote": "second CONNECT ... protocol violation"},
                "covered_by": {
                    "elements": ["state_machines/broker_session"],
                    "tests": ["l2_behavior/test_connect.py::test_second_connect_closes"],
                },
            },
            {
                "id": "REQ-PUB-001",
                "text": "QoS0 PUBLISH MUST 转发给每个匹配订阅者各一次",
                "level": "MUST",
                "category": "semantics",
                "source_ref": {"section": "3.3", "quote": "deliver the message"},
                "covered_by": {
                    "elements": ["behaviors/BEH-BROKER-001"],
                    "tests": ["l2_behavior/test_publish.py::test_qos0_forward"],
                },
            },
        ],
    }


def make_mini_manifest() -> list[dict[str, Any]]:
    """gold 测试清单条目数组（5.3 tests 字段结构），覆盖 mini-spec 引用的全部测试。"""
    return [
        {
            "nodeid": "l1_codec/test_varint.py::test_remaining_length_roundtrip",
            "layer": "l1",
            "req_ids": ["REQ-FRAME-001"],
            "description": "剩余长度编码往返",
        },
        {
            "nodeid": "l2_behavior/test_connect.py::test_bad_protocol_level",
            "layer": "l2",
            "req_ids": ["REQ-CONNECT-002"],
            "description": "非法 protocol_level 回 rc=1 后断开",
        },
        {
            "nodeid": "l2_behavior/test_connect.py::test_second_connect_closes",
            "layer": "l2",
            "req_ids": ["REQ-STATE-001"],
            "description": "第二个 CONNECT 断开连接",
        },
        {
            "nodeid": "l2_behavior/test_publish.py::test_qos0_forward",
            "layer": "l2",
            "req_ids": ["REQ-PUB-001"],
            "description": "QoS0 转发一次",
        },
    ]


def _error_codes(report: LintReport) -> set[str]:
    return report.error_codes()


# ---------------------------------------------------------------------------
# 合法 fixture 全过
# ---------------------------------------------------------------------------


def test_valid_mini_spec_passes() -> None:
    """合法 mini-spec 在默认模式下 0 error（5.1.8）。"""
    report = spec_lint(make_mini_spec())
    assert report.errors == []
    assert report.ok


def test_valid_mini_spec_passes_gold_mode_with_manifest() -> None:
    """合法 mini-spec 在 gold 模式 + 清单下同样 0 error（检查 3 修订版）。"""
    report = spec_lint(make_mini_spec(), tests_manifest=make_mini_manifest(), gold_mode=True)
    assert report.errors == []


def test_guard_conjunction_is_legal_vocab() -> None:
    """guard 支持"字段 比较符 常量"的与组合（5.1.4）。"""
    spec = make_mini_spec()
    spec["state_machines"][0]["transitions"][0]["guard"] = (
        "connect.protocol_level != 4 && connect.keep_alive >= 0x0"
    )
    report = spec_lint(spec)
    assert "SPEC-VOCAB-GUARD" not in _error_codes(report)


# ---------------------------------------------------------------------------
# 检查 1：结构校验
# ---------------------------------------------------------------------------


def test_schema_violation_reports_spec_schema() -> None:
    """缺必填顶层键 transport（5.1.1）→ SPEC-SCHEMA。"""
    spec = make_mini_spec()
    del spec["transport"]
    report = spec_lint(spec)
    assert "SPEC-SCHEMA" in _error_codes(report)


# ---------------------------------------------------------------------------
# 检查 2：引用完整
# ---------------------------------------------------------------------------


def test_undefined_field_type_reports_ref_type() -> None:
    """字段 type 非原语且未在 types 定义 → SPEC-REF-TYPE。"""
    spec = make_mini_spec()
    spec["messages"][0]["fields"][0]["type"] = "mystery_type"
    report = spec_lint(spec)
    assert "SPEC-REF-TYPE" in _error_codes(report)


def test_field_loc_must_exist_in_message_wire_layout() -> None:
    spec = make_mini_spec()
    spec["messages"][0]["fields"][0]["loc"] = "not_a_segment"
    assert "SPEC-REF-LOC" in _error_codes(spec_lint(spec))


def test_mqtt_message_requires_packet_type_code() -> None:
    spec = make_mini_spec()
    del spec["messages"][0]["packet_type_code"]
    assert "SPEC-REF-PACKET-TYPE" in _error_codes(spec_lint(spec))


def test_mqtt_packet_type_codes_are_unique() -> None:
    spec = make_mini_spec()
    spec["messages"][1]["packet_type_code"] = spec["messages"][0]["packet_type_code"]
    assert "SPEC-DUP-PACKET-TYPE" in _error_codes(spec_lint(spec))


def test_undefined_req_id_reports_ref_req() -> None:
    """req_ids 引用未定义需求 → SPEC-REF-REQ。"""
    spec = make_mini_spec()
    spec["behaviors"][0]["req_ids"] = ["REQ-NOPE-999"]
    report = spec_lint(spec)
    assert "SPEC-REF-REQ" in _error_codes(report)


def test_unknown_state_reports_ref_state() -> None:
    """转移指向未声明状态 → SPEC-REF-STATE。"""
    spec = make_mini_spec()
    spec["state_machines"][0]["transitions"][0]["to"] = "limbo"
    report = spec_lint(spec)
    assert "SPEC-REF-STATE" in _error_codes(report)


def test_event_with_unknown_message_reports_ref_msg() -> None:
    """event 引用未定义报文名 → SPEC-REF-MSG（词表本身合法）。"""
    spec = make_mini_spec()
    spec["state_machines"][0]["transitions"][0]["event"] = "recv:UNKNOWNMSG"
    report = spec_lint(spec)
    codes = _error_codes(report)
    assert "SPEC-REF-MSG" in codes
    assert "SPEC-VOCAB-EVENT" not in codes


def test_role_not_in_scope_reports_ref_role() -> None:
    """state_machine.role 不在 scope.roles → SPEC-REF-ROLE。"""
    spec = make_mini_spec()
    spec["state_machines"][0]["role"] = "server"
    report = spec_lint(spec)
    assert "SPEC-REF-ROLE" in _error_codes(report)


def test_behavior_role_both_is_legal() -> None:
    """behavior.role 取 both 合法（5.1.5 / 5.1.8 检查 2）。"""
    spec = make_mini_spec()
    spec["behaviors"][0]["role"] = "both"
    report = spec_lint(spec)
    assert "SPEC-REF-ROLE" not in _error_codes(report)


def test_duplicate_id_reports_dup_id() -> None:
    """id 全局唯一性（5 章通用约定）→ SPEC-DUP-ID。"""
    spec = make_mini_spec()
    spec["messages"][1]["id"] = "connect"
    report = spec_lint(spec)
    assert "SPEC-DUP-ID" in _error_codes(report)


# ---------------------------------------------------------------------------
# 检查 3：覆盖完整
# ---------------------------------------------------------------------------


def test_must_req_empty_elements_reports_cov_elements() -> None:
    """MUST 需求 covered_by.elements 为空 → SPEC-COV-ELEMENTS。"""
    spec = make_mini_spec()
    spec["requirements"][1]["covered_by"]["elements"] = []
    report = spec_lint(spec)
    assert "SPEC-COV-ELEMENTS" in _error_codes(report)


def test_must_req_empty_tests_only_errors_in_gold_mode() -> None:
    """MUST 需求 covered_by.tests 为空：gold 模式报 SPEC-COV-TESTS，默认模式不报。"""
    spec = make_mini_spec()
    spec["requirements"][1]["covered_by"]["tests"] = []
    assert "SPEC-COV-TESTS" not in _error_codes(spec_lint(spec))
    report = spec_lint(spec, gold_mode=True)
    assert "SPEC-COV-TESTS" in _error_codes(report)


def test_test_ref_missing_from_manifest_reports_cov_test_missing() -> None:
    """提供 tests_manifest 时，covered_by.tests 引用不存在 → SPEC-COV-TEST-MISSING。"""
    spec = make_mini_spec()
    manifest = [e for e in make_mini_manifest() if "test_qos0_forward" not in e["nodeid"]]
    report = spec_lint(spec, tests_manifest=manifest)
    assert "SPEC-COV-TEST-MISSING" in _error_codes(report)


def test_test_ref_prefix_matches_manifest_nodeid() -> None:
    """covered_by.tests 是 nodeid 前缀（5.1.6），前缀匹配即视为存在。"""
    spec = make_mini_spec()
    spec["requirements"][0]["covered_by"]["tests"] = ["l1_codec/test_varint.py"]
    report = spec_lint(spec, tests_manifest=make_mini_manifest())
    assert "SPEC-COV-TEST-MISSING" not in _error_codes(report)


# ---------------------------------------------------------------------------
# 检查 4：词表合规
# ---------------------------------------------------------------------------


def test_illegal_event_reports_vocab_event() -> None:
    """event 不符合受限词表（5.1.4）→ SPEC-VOCAB-EVENT。"""
    spec = make_mini_spec()
    spec["state_machines"][0]["transitions"][0]["event"] = "on connect"
    report = spec_lint(spec)
    assert "SPEC-VOCAB-EVENT" in _error_codes(report)


def test_illegal_action_reports_vocab_action() -> None:
    """action 不符合受限词表（5.1.4）→ SPEC-VOCAB-ACTION。"""
    spec = make_mini_spec()
    spec["state_machines"][0]["transitions"][0]["actions"] = ["explode"]
    report = spec_lint(spec)
    assert "SPEC-VOCAB-ACTION" in _error_codes(report)


def test_illegal_guard_reports_vocab_guard() -> None:
    """guard 不符合"字段 比较符 常量"语法（5.1.4）→ SPEC-VOCAB-GUARD。"""
    spec = make_mini_spec()
    spec["state_machines"][0]["transitions"][0]["guard"] = "protocol_level ~ 4"
    report = spec_lint(spec)
    assert "SPEC-VOCAB-GUARD" in _error_codes(report)


# ---------------------------------------------------------------------------
# 检查 5：无孤儿元素
# ---------------------------------------------------------------------------


def test_transition_without_req_ids_reports_orphan() -> None:
    """transition 无 req_ids → SPEC-ORPHAN（结构上合法，故只报孤儿）。"""
    spec = make_mini_spec()
    del spec["state_machines"][0]["transitions"][1]["req_ids"]
    report = spec_lint(spec)
    codes = _error_codes(report)
    assert "SPEC-ORPHAN" in codes
    assert "SPEC-SCHEMA" not in codes


def test_behavior_without_req_ids_reports_orphan() -> None:
    """behavior 无 req_ids → SPEC-ORPHAN。"""
    spec = make_mini_spec()
    del spec["behaviors"][0]["req_ids"]
    report = spec_lint(spec)
    assert "SPEC-ORPHAN" in _error_codes(report)


def test_issue_carries_code_path_message() -> None:
    """LintIssue 三要素齐备且 path 指向出错位置。"""
    spec = make_mini_spec()
    spec["state_machines"][0]["transitions"][0]["to"] = "limbo"
    report = spec_lint(spec)
    issue = next(i for i in report.errors if i.code == "SPEC-REF-STATE")
    assert issue.path == "state_machines/0/transitions/0/to"
    assert "limbo" in issue.message
