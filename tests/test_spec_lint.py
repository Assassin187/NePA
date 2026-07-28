"""Spec IR v3.0 的确定性校验测试。

共享 fixture ``make_mini_spec`` / ``make_mini_manifest`` 也供 plan 与切片测试复用。
"""

from __future__ import annotations

from typing import Any

from nepa.speclib.lint import LintReport, spec_lint


def make_mini_spec() -> dict[str, Any]:
    """构造一份只含直接文档事实的最小合法 Spec IR。"""
    return {
        "schema_version": "3.0",
        "protocol": {
            "name": "MQTT",
            "version": "3.1.1",
            "roles": ["client", "broker"],
        },
        "transport": {
            "name": "TCP",
            "default_port": 1883,
            "byte_order": "big_endian",
            "req_ids": ["REQ-TRANSPORT-001"],
        },
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
                "senders": ["client"],
                "receivers": ["broker"],
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
                        "req_ids": ["REQ-CONNECT-002"],
                    },
                ],
                "req_ids": ["REQ-CONNECT-002"],
            },
            {
                "id": "connack",
                "name": "CONNACK",
                "senders": ["broker"],
                "receivers": ["client"],
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
        "requirements": [
            {
                "id": "REQ-TRANSPORT-001",
                "text": "TCP port 1883 is registered for MQTT.",
                "level": "DEFINITION",
                "source_ref": {
                    "section": "4.2",
                    "quote": "TCP/IP port 1883 is registered with IANA for use with MQTT.",
                },
            },
            {
                "id": "REQ-FRAME-001",
                "text": "Remaining Length uses variable-byte encoding.",
                "level": "MUST",
                "source_ref": {"section": "2.2.3", "quote": "variable length encoding"},
            },
            {
                "id": "REQ-CONNECT-002",
                "text": "A bad protocol level receives CONNACK rc=1, then disconnects.",
                "level": "MUST",
                "source_ref": {"section": "3.1.2.2", "quote": "unacceptable protocol level"},
            },
            {
                "id": "REQ-STATE-001",
                "text": "A client sends CONNECT only once on a Network Connection.",
                "level": "MUST",
                "source_ref": {
                    "section": "3.1",
                    "quote": "The Client can only send the CONNECT Packet once",
                },
            },
            {
                "id": "REQ-PUB-001",
                "text": "QoS 0 PUBLISH is forwarded to matching subscribers.",
                "level": "MUST",
                "source_ref": {"section": "3.3", "quote": "deliver the message"},
            },
        ],
    }


def make_mini_manifest() -> list[dict[str, Any]]:
    """Test Bundle manifest 直接维护 REQ→测试覆盖。"""
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
            "description": "QoS0 转发",
        },
    ]


def _error_codes(report: LintReport) -> set[str]:
    return report.error_codes()


def test_valid_mini_spec_passes() -> None:
    report = spec_lint(make_mini_spec())
    assert report.errors == []
    assert report.ok


def test_valid_mini_spec_passes_gold_mode_with_manifest() -> None:
    report = spec_lint(make_mini_spec(), tests_manifest=make_mini_manifest(), gold_mode=True)
    assert report.errors == []


def test_schema_violation_reports_spec_schema() -> None:
    spec = make_mini_spec()
    del spec["protocol"]
    assert "SPEC-SCHEMA" in _error_codes(spec_lint(spec))


def test_removed_inference_views_are_rejected() -> None:
    spec = make_mini_spec()
    spec["state_machines"] = []
    assert "SPEC-SCHEMA" in _error_codes(spec_lint(spec))


def test_requirement_requires_direct_source_ref() -> None:
    spec = make_mini_spec()
    del spec["requirements"][1]["source_ref"]
    assert "SPEC-SCHEMA" in _error_codes(spec_lint(spec))


def test_structural_fact_requires_req_ids() -> None:
    spec = make_mini_spec()
    del spec["messages"][0]["fields"][0]["req_ids"]
    assert "SPEC-SCHEMA" in _error_codes(spec_lint(spec))


def test_undefined_field_type_reports_ref_type() -> None:
    spec = make_mini_spec()
    spec["messages"][0]["fields"][0]["type"] = "mystery_type"
    assert "SPEC-REF-TYPE" in _error_codes(spec_lint(spec))


def test_undefined_repeat_item_type_reports_ref_type() -> None:
    spec = make_mini_spec()
    spec["types"].append(
        {
            "id": "items",
            "name": "items",
            "encoding": {"kind": "repeat", "item_type": "mystery_type", "min_items": 1},
            "req_ids": ["REQ-FRAME-001"],
        }
    )
    assert "SPEC-REF-TYPE" in _error_codes(spec_lint(spec))


def test_repeat_min_items_must_not_exceed_max_items() -> None:
    spec = make_mini_spec()
    spec["types"].append(
        {
            "id": "items",
            "name": "items",
            "encoding": {
                "kind": "repeat",
                "item_type": "uint8",
                "min_items": 3,
                "max_items": 2,
            },
            "req_ids": ["REQ-FRAME-001"],
        }
    )
    report = spec_lint(spec)

    issue = next(issue for issue in report.errors if issue.code == "SPEC-REPEAT-BOUNDS")
    assert issue.path == "types/1/encoding"
    assert "3" in issue.message
    assert "2" in issue.message


def test_repeat_equal_min_and_max_items_is_valid() -> None:
    spec = make_mini_spec()
    spec["types"].append(
        {
            "id": "items",
            "name": "items",
            "encoding": {
                "kind": "repeat",
                "item_type": "uint8",
                "min_items": 2,
                "max_items": 2,
            },
            "req_ids": ["REQ-FRAME-001"],
        }
    )

    assert "SPEC-REPEAT-BOUNDS" not in _error_codes(spec_lint(spec))


def test_field_loc_must_exist_in_message_wire_layout() -> None:
    spec = make_mini_spec()
    spec["messages"][0]["fields"][0]["loc"] = "not_a_segment"
    assert "SPEC-REF-LOC" in _error_codes(spec_lint(spec))


def test_undefined_req_id_reports_ref_req() -> None:
    spec = make_mini_spec()
    spec["messages"][0]["req_ids"] = ["REQ-NOPE-999"]
    assert "SPEC-REF-REQ" in _error_codes(spec_lint(spec))


def test_message_roles_must_be_declared_by_protocol() -> None:
    spec = make_mini_spec()
    spec["messages"][0]["senders"] = ["gateway"]
    assert "SPEC-REF-ROLE" in _error_codes(spec_lint(spec))


def test_duplicate_id_reports_dup_id() -> None:
    spec = make_mini_spec()
    spec["messages"][1]["id"] = "connect"
    assert "SPEC-DUP-ID" in _error_codes(spec_lint(spec))


def test_missing_manifest_coverage_errors_only_in_gold_mode() -> None:
    spec = make_mini_spec()
    manifest = [e for e in make_mini_manifest() if "REQ-PUB-001" not in e["req_ids"]]
    assert "SPEC-COV-TESTS" not in _error_codes(spec_lint(spec, tests_manifest=manifest))
    assert "SPEC-COV-TESTS" in _error_codes(
        spec_lint(spec, tests_manifest=manifest, gold_mode=True)
    )


def test_definition_does_not_require_test_coverage() -> None:
    report = spec_lint(make_mini_spec(), tests_manifest=make_mini_manifest(), gold_mode=True)
    assert not any(issue.path == "requirements/0" for issue in report.errors)


def test_issue_carries_code_path_message() -> None:
    spec = make_mini_spec()
    spec["messages"][0]["fields"][0]["loc"] = "limbo"
    issue = next(i for i in spec_lint(spec).errors if i.code == "SPEC-REF-LOC")
    assert issue.path == "messages/0/fields/0/loc"
    assert "limbo" in issue.message
