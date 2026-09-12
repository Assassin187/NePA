import copy
import json
from pathlib import Path
import pytest
from nepa.speclib.plan import compile_plan, validate_claims
from nepa.speclib.lint import lint_acceptance, lint_spec, lint_target
from nepa.speclib.planning import message_context

ROOT = Path(__file__).parents[1]

def inputs():
    return [json.loads((ROOT / "gold_file" / name).read_bytes()) for name in ("specIR.json", "target.json")]

def test_gold_exact_plan_and_complete_primary_requirements():
    spec, target = inputs()
    plan = compile_plan(spec, target)
    assert len(plan["tasks"]) == 23
    assert len(plan["primary_tasks"]) == 110
    assert list(plan["primary_tasks"]) == [r["id"] for r in spec["requirements"]]
    assert plan == compile_plan(spec, target)
    assert plan["tasks"][-2]["requirement_ids"] == [r["id"] for r in spec["requirements"][-2:]]
    assert plan["tasks"][2]["context"]["directions"] == ["decode"]
    assert plan["tasks"][3]["context"]["directions"] == ["encode"]

def test_all_current_inputs_valid():
    spec, target = inputs()
    assert lint_spec(spec)["valid"]
    assert lint_target(target, spec)["valid"]
    assert lint_acceptance(ROOT / "gold_file/acceptance.json", spec)["valid"]

def test_schema_optional_transport_is_not_an_internal_key_error():
    spec, target = inputs()
    spec.pop("transport")
    assert compile_plan(spec, target)["tasks"][0]["context"]["transport"] is None

def test_wire_context_contains_complete_referenced_requirement_facts():
    spec, target = inputs()
    task = compile_plan(spec, target)["tasks"][1]
    expected = {ref for obj in [spec["transport"], *spec["types"]] for ref in obj.get("req_ids", [])}
    assert task["context"]["requirements"] == [r for r in spec["requirements"] if r["id"] in expected]
    assert task["requirement_ids"] == []  # Supporting facts do not claim primary coverage.

def test_renaming_protocol_and_message_is_structural_not_special():
    spec, target = inputs()
    spec["protocol"]["name"] = "SyntheticName"
    spec["messages"][0]["id"] = "renamed"
    spec["messages"].append(copy.deepcopy(spec["messages"][0]))
    spec["messages"][-1]["id"] = "extra"
    plan = compile_plan(spec, target)
    assert len(plan["tasks"]) == 24
    assert plan["tasks"][2]["id"] == "message:renamed"

def test_bad_references_and_duplicate_ids_fail():
    spec, target = inputs()
    spec["requirements"].append(spec["requirements"][0])
    assert not lint_spec(spec)["valid"]
    with pytest.raises(ValueError):
        compile_plan(spec, target)

def test_full_type_and_constraint_context_preserved():
    spec, _ = inputs()
    message = spec["messages"][2]
    context = message_context(spec, message)
    assert context["message"] == message
    assert {t["id"] for t in context["types"]} >= {"mqtt_utf8_string", "mqtt_subscription_entry", "mqtt_subscription_list"}

def test_claims_cannot_omit_or_defer_requirements(tmp_path):
    task = {"requirement_ids": ["r"]}
    with pytest.raises(ValueError):
        validate_claims(task, [], tmp_path)
    with pytest.raises(ValueError):
        validate_claims(task, [{"id": "r", "status": "deferred", "reason": "no test"}], tmp_path)
    (tmp_path / "a.c").write_text("int x;")
    validate_claims(task, [{"id": "r", "status": "implemented", "reason": "x", "code_refs": ["a.c:1"]}], tmp_path)

def test_target_output_collision_rejected():
    spec, target = inputs()
    target["builds"][1]["artifact"] = target["builds"][0]["artifact"]
    assert not lint_target(target, spec)["valid"]
