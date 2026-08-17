import copy

import pytest

from nepa.speclib.delivery import compile_delivery_constraints
from nepa.speclib.planning import PlanningContextError, PlanningInputError, architecture_planner_context_preflight, build_planning_index, build_test_manifest_metadata, prepare_architecture_inputs


def _gold():
    prepared = prepare_architecture_inputs("gold_file/specIR.json", "gold_file/target.json", "gold_file/test_bundle.json")
    constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
    return prepared, constraints, manifest


def test_planning_index_is_repeatable_and_quote_free():
    prepared, constraints, manifest = _gold()
    first = build_planning_index(prepared, manifest, constraints)
    second = build_planning_index(prepared, manifest, constraints)
    assert first == second
    assert "source_ref" not in repr(first)
    assert len(first["requirements"]) == len(prepared.spec["requirements"])


def test_unknown_structural_requirement_reference_is_rejected():
    prepared, constraints, manifest = _gold()
    altered = copy.deepcopy(prepared.spec)
    altered["types"][0]["req_ids"] = ["REQ-MISSING-999"]
    with pytest.raises(PlanningInputError, match="unknown requirement"):
        altered_prepared = prepare_architecture_inputs(altered, prepared.target_bytes, prepared.test_bundle_bytes)
        build_planning_index(altered_prepared, manifest, constraints)


def test_manifest_keeps_the_complete_language_build_variant_index():
    _prepared, constraints, manifest = _gold()
    assert manifest["build_variant_ids"] == sorted(constraints["build_variant_ids"])


def test_planning_index_ignores_mutated_prepared_projection():
    prepared, constraints, manifest = _gold()
    original = build_planning_index(prepared, manifest, constraints)
    prepared.spec["protocol"]["name"] = "mutated-after-freeze"
    prepared.target_profile["roles"] = ["mutated"]
    assert build_planning_index(prepared, manifest, constraints) == original


def test_context_preflight_includes_system_prompt_and_repair_context():
    prepared, constraints, manifest = _gold()
    index = build_planning_index(prepared, manifest, constraints)
    with pytest.raises(PlanningContextError) as exc:
        architecture_planner_context_preflight(
            index,
            constraints,
            model_limits={"fixture": 1},
            requested_output_tokens=1,
            safety_margin_ratio=0,
            repair_context={"previous_candidate": {"large": "x"}, "validation_issues": [{"message": "y"}]},
        )
    assert exc.value.code == "PLAN_CONTEXT_TOO_LARGE"
