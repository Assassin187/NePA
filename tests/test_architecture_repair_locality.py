import copy
import json
from pathlib import Path

from nepa.calibration.s4_architecture import (
    REPAIR_IMPACT_POLICY,
    architecture_draft_changed_paths,
    assess_repair_locality,
    repair_impact_closure,
)


def _draft():
    return json.loads(Path("nepa/schemas/examples/architecture-draft.example.json").read_text(encoding="utf-8"))


def _validation(**values):
    gates = [{"id": f"arch_{index:02d}", "verdict": values.get(f"arch_{index:02d}", "pass")} for index in range(1, 11)]
    return {"gates": gates}


def test_canonical_diff_uses_stable_ids_and_ignores_set_like_reordering():
    before = _draft()
    after = copy.deepcopy(before)
    after["modules"].reverse()
    assert architecture_draft_changed_paths(before, after) == []
    after["modules"][0]["purpose"] = "Changed purpose"
    assert architecture_draft_changed_paths(before, after) == ["/modules/module-adapter/purpose"]


def test_repair_locality_attributes_issue_scoped_change_and_detects_regression():
    before = _draft()
    after = copy.deepcopy(before)
    after["contracts"][0]["provider"] = "module-adapter"
    issues = [{"gate": "arch_04", "code": "ARCH_CONTRACT_PROVIDER_INVALID", "path": "/contracts/0/provider"}]
    passing = assess_repair_locality(before, after, issues, _validation(arch_04="fail"), _validation())
    assert passing["locality_pass"] is True
    assert passing["improved_gates"] == ["arch_04"]
    regressed = assess_repair_locality(before, after, issues, _validation(arch_04="fail"), _validation(arch_02="fail"))
    assert regressed["locality_pass"] is False
    assert regressed["regressed_gates"] == ["arch_02"]


def test_repair_impact_policy_is_closed_for_all_architecture_gates():
    assert set(REPAIR_IMPACT_POLICY) == {f"arch_{index:02d}" for index in range(1, 11)}
    closure = repair_impact_closure([{"gate": "arch_10", "code": "ARCH_TEST_READINESS_UNCLOSED", "path": "/tests/0"}])
    assert "/work_packages" in closure["arch_10:ARCH_TEST_READINESS_UNCLOSED"]
