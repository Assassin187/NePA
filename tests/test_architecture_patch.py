import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from nepa.calibration.s4_architecture import (
    CalibrationEvidenceError,
    apply_architecture_patch,
    apply_architecture_patch_with_projection,
    validate_architecture_patch,
)


def _candidate():
    return json.loads(Path("nepa/schemas/examples/architecture-draft.example.json").read_text(encoding="utf-8"))


def _replace(path, new):
    return {
        "schema_version": "2.0",
        "patch_ops": [{
            "op": "replace", "path": path, "expected_presence": "present",
            "value": new,
        }],
    }


def test_patch_schema_accepts_closed_replace_and_rejects_draft_payload():
    schema = json.loads(Path("nepa/schemas/architecture-patch.schema.json").read_text(encoding="utf-8"))
    candidate = _candidate()
    patch = _replace("/contracts/contract-interface/provider", "module-adapter")
    Draft202012Validator(schema).validate(patch)
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(candidate)


def test_patch_application_is_atomic_and_uses_stable_array_identity():
    candidate = _candidate()
    original = copy.deepcopy(candidate)
    patch = _replace("/contracts/contract-interface/provider", "module-adapter")
    changed = apply_architecture_patch(candidate, patch, ["/contracts/contract-interface/provider"])
    assert candidate == original
    assert changed["contracts"][0]["provider"] == "module-adapter"


def test_patch_uses_presence_only_for_add_replace_and_remove():
    candidate = {"container": {"existing": "old"}}
    patch = {
        "schema_version": "2.0",
        "patch_ops": [
            {"op": "add", "path": "/container/new", "expected_presence": "absent", "value": "added"},
            {"op": "replace", "path": "/container/existing", "expected_presence": "present", "value": "new"},
        ],
    }
    changed = apply_architecture_patch(candidate, patch, ["/container"])
    assert candidate == {"container": {"existing": "old"}}
    assert changed == {"container": {"existing": "new", "new": "added"}}
    removed = {
        "schema_version": "2.0",
        "patch_ops": [{"op": "remove", "path": "/container/existing", "expected_presence": "present"}],
    }
    assert apply_architecture_patch(changed, removed, ["/container"]) == {"container": {"new": "added"}}


@pytest.mark.parametrize("patch", [
    {"schema_version": "2.0", "patch_ops": [{"op": "replace", "path": "/", "expected_presence": "present", "value": {}}]},
    {"schema_version": "2.0", "patch_ops": [{"op": "replace", "path": "/contracts/0/provider", "expected_presence": "present", "value": "x"}]},
    {"schema_version": "2.0", "patch_ops": [{"op": "replace", "path": "/contracts/contract-interface/provider", "expected_presence": "present", "value": "x", "extra": True}]},
    {"schema_version": "2.0", "patch_ops": [{"op": "replace", "path": "/contracts/contract-interface/provider", "expected_presence": "present", "expected_value_sha256": "a" * 64, "value": "x"}]},
    {"schema_version": "2.0", "patch_ops": [{"op": "remove", "path": "/contracts/contract-interface/provider", "expected_presence": "absent"}]},
])
def test_patch_rejects_root_numeric_array_extra_hash_and_presence_mismatch(patch):
    with pytest.raises(CalibrationEvidenceError):
        validate_architecture_patch(patch, _candidate(), ["/contracts"])


def test_missing_presence_targets_and_overlapping_operations_reject_without_mutation():
    candidate = _candidate()
    missing = {
        "schema_version": "2.0",
        "patch_ops": [{"op": "replace", "path": "/contracts/contract-interface/missing", "expected_presence": "present", "value": "x"}],
    }
    with pytest.raises(CalibrationEvidenceError, match="not present"):
        apply_architecture_patch(candidate, missing, ["/contracts"])
    overlapping = {
        "schema_version": "2.0",
        "patch_ops": [
            {"op": "replace", "path": "/contracts/contract-interface/provider", "expected_presence": "present", "value": "x"},
            {"op": "replace", "path": "/contracts/contract-interface", "expected_presence": "present", "value": candidate["contracts"][0]},
        ],
    }
    with pytest.raises(CalibrationEvidenceError, match="overlap"):
        apply_architecture_patch(candidate, overlapping, ["/contracts"])
    assert candidate == _candidate()


def test_static_layout_projection_updates_exact_module_and_work_package_closure():
    candidate = _candidate()
    original = copy.deepcopy(candidate)
    patch = _replace("/layout/files/core-source/path", "src/core/core_impl.c")
    changed, evidence = apply_architecture_patch_with_projection(
        candidate, patch, ["/layout/files/core-source/path"], {},
    )
    assert candidate == original
    assert changed["modules"][0]["owns_files"] == ["include/core/interface.h", "src/core/core_impl.c"]
    assert changed["work_packages"][0]["allowed_files"] == ["include/core/interface.h", "src/core/core_impl.c"]
    assert changed["modules"][1]["owns_files"] == ["src/adapter/adapter.c"]
    assert len(evidence["derived_operations"]) == 2
    assert evidence["projection"]["derived_paths"] == [
        "/modules/module-core/owns_files", "/work_packages/wp-core/allowed_files"
    ]


def test_expanded_layout_projection_preserves_unrelated_list_entries():
    from tests.test_architecture_validation import _valid_draft

    candidate, _planning, _manifest, constraints = _valid_draft()
    candidate["modules"][0]["owns_files"].append("src/codec/unrelated.c")
    candidate["work_packages"][0]["allowed_files"].append("src/codec/unrelated.c")
    old_pattern = candidate["layout"]["files"][3]["path_pattern"]
    patch = _replace("/layout/files/message-codecs/path_pattern", "src/codec/src_{message_id}.c")
    changed, evidence = apply_architecture_patch_with_projection(
        candidate, patch, ["/layout/files/message-codecs/path_pattern"], constraints,
    )
    assert old_pattern != changed["layout"]["files"][3]["path_pattern"]
    assert "src/codec/unrelated.c" in changed["modules"][0]["owns_files"]
    assert "src/codec/unrelated.c" in changed["work_packages"][0]["allowed_files"]
    assert len(evidence["projection"]["mappings"][0]["pairs"]) == len(constraints["naming"]["message_ids"])


def test_expanded_projection_revalidates_as_one_atomic_all_gate_candidate():
    from nepa.speclib.architecture import validate_architecture
    from tests.test_architecture_validation import _valid_draft

    candidate, planning, manifest, constraints = _valid_draft()
    before = validate_architecture(candidate, planning, manifest, constraints)
    patch = _replace("/layout/files/message-codecs/path_pattern", "src/codec/src_{message_id}.c")
    changed, _evidence = apply_architecture_patch_with_projection(
        candidate, patch, ["/layout/files/message-codecs/path_pattern"], constraints,
    )
    after = validate_architecture(changed, planning, manifest, constraints)
    assert before["verdict"] == "pass"
    assert after["verdict"] == "pass"
    assert len(after["gates"]) == 15


def test_layout_projection_rejects_domain_change_missing_closure_and_broader_edit():
    from tests.test_architecture_validation import _valid_draft

    candidate, _planning, _manifest, constraints = _valid_draft()
    domain_patch = _replace("/layout/files/message-codecs/path_pattern", "src/codec/{type_id}.c")
    with pytest.raises(CalibrationEvidenceError, match="placeholder"):
        apply_architecture_patch_with_projection(candidate, domain_patch, ["/layout/files/message-codecs/path_pattern"], constraints)

    missing = copy.deepcopy(candidate)
    missing["modules"][0]["owns_files"].pop()
    with pytest.raises(CalibrationEvidenceError, match="missing closure"):
        apply_architecture_patch_with_projection(
            missing, _replace("/layout/files/message-codecs/path_pattern", "src/codec/src_{message_id}.c"),
            ["/layout/files/message-codecs/path_pattern"], constraints,
        )

    broader = {
        "schema_version": "2.0",
        "patch_ops": [
            {"op": "replace", "path": "/layout/files/message-codecs/path_pattern", "expected_presence": "present", "value": "src/codec/src_{message_id}.c"},
            {"op": "replace", "path": "/modules/codec/name", "expected_presence": "present", "value": "Changed"},
        ],
    }
    with pytest.raises(CalibrationEvidenceError, match="broader"):
        apply_architecture_patch_with_projection(candidate, broader, ["/layout", "/modules"], constraints)
