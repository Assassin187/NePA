import json
from pathlib import Path

from nepa.speclib.lint import lint_spec


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_spec_lint_accepts_minimal_example(tmp_path):
    example = json.loads((Path(__file__).parents[1] / "nepa/schemas/examples/specs-requirements.example.json").read_text())
    assert lint_spec(_write(tmp_path / "spec.json", example))["valid"]


def test_spec_lint_rejects_unknown_type_reference(tmp_path):
    spec = {
        "schema_version": "3.0",
        "protocol": {"name": "MQTT", "version": "3.1.1", "roles": ["server"]},
        "types": [],
        "messages": [{
            "id": "ping",
            "name": "PING",
            "senders": ["server"],
            "receivers": ["server"],
            "wire_layout": ["fixed_header"],
            "fields": [{"name": "value", "loc": "fixed_header", "type": "missing", "req_ids": ["REQ-TEST-001"]}],
            "req_ids": ["REQ-TEST-001"]
        }],
        "requirements": [{
            "id": "REQ-TEST-001",
            "text": "The value is defined.",
            "level": "DEFINITION",
            "source_ref": {"section": "1", "quote": "The value is defined."}
        }]
    }
    report = lint_spec(_write(tmp_path / "spec.json", spec))
    assert not report["valid"]
    assert any(error["code"] == "SPEC_TYPE_UNKNOWN" for error in report["errors"])


def test_spec_lint_rejects_unknown_role_and_field_location(tmp_path):
    spec = {
        "schema_version": "3.0",
        "protocol": {"name": "MQTT", "version": "3.1.1", "roles": ["server"]},
        "types": [],
        "messages": [{
            "id": "ping",
            "name": "PING",
            "senders": ["client"],
            "receivers": ["server"],
            "wire_layout": ["fixed_header"],
            "fields": [{"name": "value", "loc": "payload", "type": "uint8", "req_ids": ["REQ-TEST-001"]}],
            "req_ids": ["REQ-TEST-001"]
        }],
        "requirements": [{
            "id": "REQ-TEST-001",
            "text": "The value is defined.",
            "level": "DEFINITION",
            "source_ref": {"section": "1", "quote": "The value is defined."}
        }]
    }
    report = lint_spec(_write(tmp_path / "spec.json", spec))
    codes = {error["code"] for error in report["errors"]}
    assert {"SPEC_ROLE_UNKNOWN", "SPEC_FIELD_LOCATION_UNKNOWN"} <= codes
