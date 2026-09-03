import copy
import json
from pathlib import Path

from nepa.calibration.s4_architecture import (
    assess_patch_locality,
    map_architecture_failures_to_paths,
)


def _candidate():
    return json.loads(Path("nepa/schemas/examples/architecture-draft.example.json").read_text(encoding="utf-8"))


def test_issue_paths_are_canonicalized_to_stable_array_selectors():
    candidate = _candidate()
    mapped = map_architecture_failures_to_paths(candidate, [{"gate": "arch_04", "code": "X", "path": "/contracts/0/provider"}])
    assert mapped["allowed_paths"] == ["/contracts/contract-interface/provider"]
    assert mapped["mappings"][0]["mode"] == "exact"


def test_missing_issue_path_expands_only_declared_gate_prefixes():
    candidate = _candidate()
    mapped = map_architecture_failures_to_paths(candidate, [{"gate": "arch_10", "code": "X", "path": None}])
    assert mapped["allowed_paths"] == ["/work_packages"]


def test_locality_records_changed_leaf_and_regression():
    before = _candidate()
    after = copy.deepcopy(before)
    after["contracts"][0]["provider"] = "module-adapter"
    before_validation = {"gates": [{"id": f"arch_{i:02d}", "verdict": "fail" if i == 4 else "pass"} for i in range(1, 16)]}
    after_validation = {"gates": [{"id": f"arch_{i:02d}", "verdict": "pass" if i in {2, 4} else "fail" if i == 9 else "pass"} for i in range(1, 16)]}
    result = assess_patch_locality(before, after, ["/contracts/contract-interface/provider"], before_validation, after_validation)
    assert result["locality_pass"] is False
    assert result["changed_paths"] == ["/contracts/contract-interface/provider"]
    assert result["regressed_gates"] == ["arch_09"]
