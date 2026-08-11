import json
from pathlib import Path

import pytest

from nepa.speclib.lint import canonical_json_bytes, lint_spec, lint_test_bundle


def _write(path: Path, value: dict) -> Path:
    path.write_bytes(canonical_json_bytes(value))
    return path


def _write_raw(path: Path, raw: bytes) -> Path:
    path.write_bytes(raw)
    return path


def _spec(*requirements: tuple[str, str]) -> dict:
    return {
        "schema_version": "3.0",
        "protocol": {"name": "MQTT", "version": "3.1.1", "roles": ["server"]},
        "types": [],
        "messages": [],
        "requirements": [
            {
                "id": req_id,
                "text": f"{req_id} is defined.",
                "level": level,
                "source_ref": {"section": "1", "quote": f"{req_id} is defined."},
            }
            for req_id, level in requirements
        ],
    }


def _test(*, nodeid: str = "tests/l1_codec/test_codec.py::test_vector", layer: str = "l1", req_ids: list[str] | None = None, gate: str = "task", variants: list[str] | None = None) -> dict:
    test = {
        "nodeid": nodeid,
        "layer": layer,
        "req_ids": req_ids or ["REQ-TEST-001"],
        "description": "A declarative test case.",
        "gate": gate,
    }
    if variants is not None:
        test["build_variant_ids"] = variants
    return test


def _bundle(tests: list[dict], default_variants: list[str] | None = None) -> dict:
    return {
        "schema_version": "3.0",
        "bundle": {
            "id": "test-bundle",
            "version": "1.0.0",
            "default_build_variant_ids": default_variants or ["san"],
        },
        "tests": tests,
    }


def test_bundle_lint_accepts_valid_metadata_and_spec_refs(tmp_path):
    bundle = _write(tmp_path / "bundle.json", _bundle([_test()]))
    spec = _write(tmp_path / "spec.json", _spec(("REQ-TEST-001", "MUST")))

    report = lint_test_bundle(bundle, spec)

    assert report["valid"]


def test_bundle_lint_rejects_schema_extra_field(tmp_path):
    value = _bundle([_test()])
    value["tests"][0]["unexpected"] = True

    report = lint_test_bundle(_write(tmp_path / "bundle.json", value))

    assert not report["valid"]
    assert any(error["code"] == "SCHEMA_INVALID" for error in report["errors"])


def test_bundle_lint_rejects_duplicate_nodeid(tmp_path):
    value = _bundle([_test(), _test(nodeid="tests/l1_codec/test_other.py::test_other")])
    value["tests"][1]["nodeid"] = value["tests"][0]["nodeid"]

    report = lint_test_bundle(_write(tmp_path / "bundle.json", value))

    assert not report["valid"]
    assert any(error["code"] == "TEST_NODEID_DUPLICATE" for error in report["errors"])


def test_bundle_lint_rejects_nodeid_layer_mismatch(tmp_path):
    value = _bundle([_test(layer="l2")])

    report = lint_test_bundle(_write(tmp_path / "bundle.json", value))

    assert not report["valid"]
    assert any(error["code"] == "TEST_NODEID_LAYER_MISMATCH" for error in report["errors"])


def test_bundle_lint_rejects_unknown_requirement(tmp_path):
    bundle = _write(tmp_path / "bundle.json", _bundle([_test(req_ids=["REQ-MISSING-001"])]))
    spec = _write(tmp_path / "spec.json", _spec(("REQ-TEST-001", "MUST")))

    report = lint_test_bundle(bundle, spec)

    assert not report["valid"]
    assert any(error["code"] == "TEST_REQUIREMENT_UNKNOWN" for error in report["errors"])


def test_bundle_lint_rejects_invalid_gate(tmp_path):
    value = _bundle([_test(gate="not-a-gate")])

    report = lint_test_bundle(_write(tmp_path / "bundle.json", value))

    assert not report["valid"]
    codes = {error["code"] for error in report["errors"]}
    assert {"SCHEMA_INVALID", "TEST_GATE_UNSUPPORTED"} <= codes


@pytest.mark.parametrize("missing_field", ["gate", "req_ids"])
def test_bundle_lint_schema_error_with_spec_skips_coverage_for_missing_field(tmp_path, missing_field):
    value = _bundle([_test()])
    del value["tests"][0][missing_field]
    bundle = _write(tmp_path / "bundle.json", value)
    spec = _write(tmp_path / "spec.json", _spec(("REQ-TEST-001", "MUST")))

    report = lint_test_bundle(bundle, spec)

    assert not report["valid"]
    codes = {error["code"] for error in report["errors"]}
    assert "SCHEMA_INVALID" in codes
    assert "NEPA_INTERNAL_ERROR" not in codes


def test_bundle_lint_schema_error_with_spec_skips_coverage_for_non_object_test(tmp_path):
    bundle = _write(tmp_path / "bundle.json", _bundle(["not-an-object"]))
    spec = _write(tmp_path / "spec.json", _spec(("REQ-TEST-001", "MUST")))

    report = lint_test_bundle(bundle, spec)

    assert not report["valid"]
    codes = {error["code"] for error in report["errors"]}
    assert "SCHEMA_INVALID" in codes
    assert "NEPA_INTERNAL_ERROR" not in codes


def test_bundle_lint_rejects_unavailable_build_variant(tmp_path):
    value = _bundle([_test(variants=["debug"])])

    report = lint_test_bundle(_write(tmp_path / "bundle.json", value))

    assert not report["valid"]
    assert any(error["code"] == "TEST_BUILD_VARIANT_UNSUPPORTED" for error in report["errors"])


def test_bundle_lint_rejects_unavailable_default_build_variant(tmp_path):
    value = _bundle([_test()], default_variants=["debug"])

    report = lint_test_bundle(_write(tmp_path / "bundle.json", value))

    assert not report["valid"]
    assert any(error["code"] == "TEST_BUILD_VARIANT_UNSUPPORTED" for error in report["errors"])


def test_bundle_canonical_json_is_compact_sorted_utf8_without_newline():
    assert canonical_json_bytes({"z": "值", "a": 1}) == '{"a":1,"z":"值"}'.encode("utf-8")


def test_bundle_canonical_accepts_file_bytes(tmp_path):
    value = _bundle([_test()])
    path = _write(tmp_path / "bundle.json", value)

    report = lint_test_bundle(path)

    assert report["valid"]
    assert path.read_bytes() == canonical_json_bytes(value)


def test_bundle_canonical_rejects_semantically_valid_indented_file(tmp_path):
    value = _bundle([_test()])
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")

    report = lint_test_bundle(_write_raw(tmp_path / "bundle.json", raw))

    assert not report["valid"]
    assert report["errors"] == [{
        "code": "TEST_CANONICAL_JSON_NONCANONICAL",
        "path": "/",
        "message": "input bytes must equal canonical JSON bytes",
    }]


def test_bundle_canonical_rejects_semantically_valid_trailing_newline(tmp_path):
    value = _bundle([_test()])
    raw = canonical_json_bytes(value) + b"\n"

    report = lint_test_bundle(_write_raw(tmp_path / "bundle.json", raw))

    assert not report["valid"]
    assert any(error["code"] == "TEST_CANONICAL_JSON_NONCANONICAL" for error in report["errors"])


def test_bundle_canonical_rejects_non_finite_value():
    with pytest.raises(ValueError):
        canonical_json_bytes({"value": float("nan")})


def test_bundle_coverage_gold_uses_task_and_s7_cases():
    spec = _spec(("REQ-TEST-001", "MUST"), ("REQ-TEST-002", "MUST NOT"))
    manifest = _bundle([
        _test(req_ids=["REQ-TEST-001"], gate="s5"),
        _test(nodeid="tests/l1_codec/test_task.py::test_task", req_ids=["REQ-TEST-001"]),
        _test(nodeid="tests/l2_behavior/test_behavior.py::test_behavior", layer="l2", req_ids=["REQ-TEST-002"], gate="s7_only"),
    ])

    report = lint_spec(spec, gold=True, manifest=manifest)

    assert report["valid"]


def test_bundle_coverage_gold_rejects_uncovered_requirement():
    spec = _spec(("REQ-TEST-001", "MUST"))
    manifest = _bundle([_test(req_ids=["REQ-TEST-001"], gate="s5")])

    report = lint_spec(spec, gold=True, manifest=manifest)

    assert not report["valid"]
    assert any(error["code"] == "SPEC_REQUIREMENT_UNCOVERED" for error in report["errors"])


def test_bundle_coverage_spec_accepts_task_and_s7_only_and_ignores_s5():
    spec = _spec(("REQ-TEST-001", "MUST"), ("REQ-TEST-002", "MUST NOT"))
    manifest = _bundle([
        _test(req_ids=["REQ-TEST-001"], gate="s5"),
        _test(nodeid="tests/l1_codec/test_task.py::test_task", req_ids=["REQ-TEST-001"]),
        _test(nodeid="tests/l2_behavior/test_behavior.py::test_behavior", layer="l2", req_ids=["REQ-TEST-002"], gate="s7_only"),
    ])

    report = lint_test_bundle(manifest, spec)

    assert report["valid"]


def test_bundle_coverage_spec_rejects_s5_only_coverage():
    spec = _spec(("REQ-TEST-001", "MUST"))
    manifest = _bundle([_test(req_ids=["REQ-TEST-001"], gate="s5")])

    report = lint_test_bundle(manifest, spec)

    assert not report["valid"]
    assert any(error["code"] == "SPEC_REQUIREMENT_UNCOVERED" for error in report["errors"])


def test_bundle_coverage_gold_without_manifest_reports_skipped_warning():
    report = lint_spec(_spec(("REQ-TEST-001", "MUST")), gold=True)

    assert report["valid"]
    assert any(warning["code"] == "SPEC_COVERAGE_SKIPPED" for warning in report["warnings"])
