import hashlib
import json
import shutil
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
CURRENT_TEST_LINEAGE_ID = hashlib.sha256(b"nepa-m1-4d-current-contract-s4-test-lineage-v1").hexdigest()


def _ref(root: Path, relative: str) -> dict[str, str]:
    path = root / relative
    return {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _write_json(root: Path, relative: str, value: dict) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


@pytest.fixture
def current_contract_handoff(tmp_path: Path) -> dict[str, object]:
    """Build an isolated current-contract admission fixture for S4 tests.

    This is test data only. It is never the production handoff and its
    affirmative approval must not be copied into a real calibration lineage.
    """

    root = tmp_path / "current-contract-handoff" / CURRENT_TEST_LINEAGE_ID
    schema_path = root / "schema/architecture-draft.schema.json"
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "nepa/schemas/architecture-draft.schema.json", schema_path)
    for phase in ("initial", "repair"):
        destination = root / f"prompt-development/versions/v0/{phase}.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / f"nepa/agents/prompts/architecture_planner_{phase}.md", destination)
    for relative, value in (
        ("planning_index.json", {}),
        ("delivery_constraints.json", {}),
        ("prompt-development/config.json", {}),
        ("prompt-development/context_limits.json", {}),
        ("prompt-development/protocol.json", {}),
        ("v0/test/calibration_report.json", {}),
    ):
        _write_json(root, relative, value)
    _write_json(root, "lineage.json", {
        "lineage_id": CURRENT_TEST_LINEAGE_ID,
        "artifacts": {"schema": _ref(root, "schema/architecture-draft.schema.json")},
    })
    _write_json(root, "prompt-development/versions/v0/snapshot.json", {
        "schema_version": "4.0",
        "lineage_id": CURRENT_TEST_LINEAGE_ID,
        "version": "v0",
        "initial_ref": _ref(root, "prompt-development/versions/v0/initial.md"),
        "repair_ref": _ref(root, "prompt-development/versions/v0/repair.md"),
        "byte_encoding": "utf-8-raw-template",
    })
    snapshot_ref = _ref(root, "prompt-development/versions/v0/snapshot.json")
    report_ref = _ref(root, "v0/test/calibration_report.json")
    _write_json(root, "prompt-development/versions/v0/assessment-n003.json", {
        "schema_version": "4.0",
        "lineage_id": CURRENT_TEST_LINEAGE_ID,
        "version": "v0",
        "model_slot": "test",
        "trial_count": 3,
        "attempt": 1,
        "status": "complete",
        "screening_pass": True,
        "ambiguity": "none",
        "models": {"test": {
            "report_ref": report_ref,
            "p1": 2 / 3,
            "p2": 2 / 3,
            "p2_passes": 2,
            "unavailable_trials": 0,
            "schema_after_format_repair_rate": 1.0,
            "arch_semantic_first_pass_rate": 1.0,
            "truncations": 0,
            "infrastructure_invalid": False,
            "repeated_gate_failures": [],
            "screening_pass": True,
            "initial_gate_failures": {},
            "identity_stable": True,
            "provider": "fixture",
            "requested_model": "fixture",
            "returned_versions": ["fixture"],
            "parameter_support": {},
            "usage": {},
            "gates": {},
            "repairs": {},
            "tuple": [2 / 3, 2 / 3, 1.0, 1.0, 0.0],
            "trial_ids": ["trial_001", "trial_002", "trial_003"],
        }},
    })
    assessment_ref = _ref(root, "prompt-development/versions/v0/assessment-n003.json")
    _write_json(root, "prompt-development/selection.json", {
        "schema_version": "4.0",
        "lineage_id": CURRENT_TEST_LINEAGE_ID,
        "status": "selected",
        "selected_version": "v0",
        "bundle_ref": snapshot_ref,
        "assessment_ref": assessment_ref,
        "reason": "current-contract test fixture",
        "comparison_tuples": {"v0": [2 / 3, 2 / 3, 1.0, 1.0, 0.0]},
        "assessment_refs": {"v0": assessment_ref},
    })
    _write_json(root, "prompt-development/versions/v0/neutrality.json", {
        "schema_version": "1.0",
        "lineage_id": CURRENT_TEST_LINEAGE_ID,
        "status": "pass",
        "policy": "m1-4a2-prompt-neutrality-v1",
        "protocol_ref": _ref(root, "prompt-development/protocol.json"),
        "prompt_refs": {
            "initial": _ref(root, "prompt-development/versions/v0/initial.md"),
            "repair": _ref(root, "prompt-development/versions/v0/repair.md"),
        },
        "input_refs": {
            "planning_index": _ref(root, "planning_index.json"),
            "delivery_constraints": _ref(root, "delivery_constraints.json"),
            "config": _ref(root, "prompt-development/config.json"),
            "context_limits": _ref(root, "prompt-development/context_limits.json"),
        },
    })
    neutrality_ref = _ref(root, "prompt-development/versions/v0/neutrality.json")
    _write_json(root, "prompt-development/owner-approval.json", {
        "schema_version": "5.0",
        "approved": True,
        "reviewer": "test-fixture-owner",
        "lineage_id": CURRENT_TEST_LINEAGE_ID,
        "selection_ref": _ref(root, "prompt-development/selection.json"),
        "bundle_ref": snapshot_ref,
        "assessment_ref": assessment_ref,
        "protocol_neutrality": {"status": "pass", "evidence_ref": neutrality_ref},
        "baseline_2_of_3": {"trial_count": 3, "p2_passes": 2, "screening_pass": True, "recomputed_from": "assessment_ref"},
    })
    _write_json(root, "prompt-development/handoff.json", {
        "schema_version": "4.0",
        "lineage_id": CURRENT_TEST_LINEAGE_ID,
        "consumer": "m1-4c",
        "selection_ref": _ref(root, "prompt-development/selection.json"),
        "selected_version": "v0",
        "bundle_ref": snapshot_ref,
        "assessment_ref": assessment_ref,
        "owner_approval_ref": _ref(root, "prompt-development/owner-approval.json"),
        "selection_reason": "current-contract test fixture",
        "satisfies": {
            "baseline_2_of_3": True,
            "protocol_neutrality": True,
            "owner_signature": True,
            "production_quality_proven": False,
        },
    })
    return {"root": root, "lineage_id": CURRENT_TEST_LINEAGE_ID}
