import copy
import json
from pathlib import Path

from nepa.speclib.delivery import compile_delivery_constraints
from nepa.speclib.planning import build_test_manifest_metadata, prepare_architecture_inputs
from nepa.speclib.plan import link_plan, plan_lint


ROOT = Path(__file__).parents[1]


def _linked():
    prepared = prepare_architecture_inputs(
        ROOT / "tests/fixtures/non_mqtt_application/spec.json",
        ROOT / "tests/fixtures/non_mqtt_application/target.json",
        ROOT / "tests/fixtures/non_mqtt_application/test_bundle.json",
    )
    constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
    architecture = json.loads((ROOT / "tests/fixtures/non_mqtt_application/architecture-draft.json").read_text(encoding="utf-8"))
    shards = []
    for package in architecture["work_packages"]:
        shards.append({
            "schema_version": "1.0",
            "work_package_id": package["id"],
            "tasks": [{
                "local_id": "implement",
                "title": package["title"],
                "goal": package["goal"],
                "kind": "app",
                "instructions": "Implement the declared behavior.",
                "deliverable_files": package["allowed_files"],
                "context_refs": package["context_refs"],
                "requirement_responsibilities": package["requirement_responsibilities"],
                "provides_contracts": package["provides_contracts"],
                "consumes_contracts": package["consumes_contracts"],
                "depends_on": [],
                "acceptance": {"build_variant_ids": ["san"], "tests": []},
            }],
        })
    result = link_plan(architecture, architecture["work_packages"], shards, constraints, spec=prepared.spec, manifest=manifest)
    return result["plan"], result["blueprint"], prepared.spec, manifest, constraints, prepared.target_profile


def test_basic_and_full_lint_are_distinct_and_deterministic():
    plan, blueprint, spec, manifest, constraints, target = _linked()
    basic = plan_lint(plan, spec, manifest)
    full = plan_lint(plan, spec, manifest, level="full", constraints=constraints, blueprint=blueprint, target_profile=target)
    assert basic["valid"] is True
    assert full["valid"] is True
    assert basic == plan_lint(plan, spec, manifest)

    drifted = copy.deepcopy(plan)
    drifted["coverage"]["tests"][0]["enabled"] = not drifted["coverage"]["tests"][0]["enabled"]
    assert any(error["code"] == "PLAN_COVERAGE_DRIFT" for error in plan_lint(drifted, spec, manifest)["errors"])


def test_full_lint_requires_the_frozen_delivery_inputs():
    plan, _blueprint, spec, manifest, _constraints, _target = _linked()
    report = plan_lint(plan, spec, manifest, level="full")
    assert report["valid"] is False
    assert any(error["code"] == "PLAN_FULL_INPUT_MISSING" for error in report["errors"])
