import pytest

from nepa.agents.base import (
    AgentAvailabilityError,
    AgentConfigurationError,
    AgentInvoker,
    AgentRoleError,
    resolve_route,
)
from nepa.agents.roles import ROLE_REGISTRY, get_role, registered_roles
from nepa.config import load_config


EXPECTED_INPUTS = {
    "architecture_planner": ("planning_index", "delivery_constraints", "repair_context"),
    "task_planner": ("work_package", "spec_slice", "adjacent_contracts", "test_metadata"),
    "plan_critic": ("candidate_plan_graph", "coverage_matrix", "lint_report"),
    "flat_plan_baseline": ("planning_index", "delivery_constraints", "manifest_metadata"),
    "coder": ("task", "spec_slice", "interface_files"),
    "diagnoser": ("build_errors", "relevant_code"),
    "fixer": ("diagnosis", "target_files"),
}


def test_closed_catalog_has_exact_roles_inputs_and_stage_associations():
    assert set(registered_roles()) == set(EXPECTED_INPUTS)
    assert set(ROLE_REGISTRY) == set(EXPECTED_INPUTS)
    assert ROLE_REGISTRY["architecture_planner"].stages == ("S4",)
    assert ROLE_REGISTRY["task_planner"].stages == ("S4",)
    assert ROLE_REGISTRY["plan_critic"].stages == ("S4",)
    assert ROLE_REGISTRY["flat_plan_baseline"].stages == ("S4",)
    assert ROLE_REGISTRY["coder"].stages == ("S6",)
    assert ROLE_REGISTRY["diagnoser"].stages == ("S6", "S8")
    assert ROLE_REGISTRY["fixer"].stages == ("S6", "S8")
    for role, inputs in EXPECTED_INPUTS.items():
        assert ROLE_REGISTRY[role].required_inputs == inputs
        expected_template = "architecture_planner_initial.md" if role == "architecture_planner" else f"{role}.md"
        assert ROLE_REGISTRY[role].template_path == expected_template


def test_unknown_role_is_rejected():
    with pytest.raises(AgentRoleError):
        get_role("unknown")


def test_route_inherits_tier_defaults_and_keeps_escalation_metadata():
    config = load_config()
    route = resolve_route(config, "diagnoser")
    assert route.tier == "T2"
    assert route.provider == "deepseek"
    assert route.model == "deepseek-v4-flash"
    assert route.temperature == 0.1
    assert route.max_tokens == 16000
    assert route.escalate_to == "T1"
    assert resolve_route(config, "fixer").escalate_to == "T1"


def test_route_merges_partial_role_overrides():
    config = load_config(
        overrides={
            "roles": {"coder": {"tier": "T2", "model": "fixture-model", "temperature": 0.2}},
        }
    )
    route = resolve_route(config, "coder")
    assert route.provider == "deepseek"
    assert route.model == "fixture-model"
    assert route.temperature == 0.2
    assert route.max_tokens == 16000


@pytest.mark.parametrize(
    ("role", "overrides", "message"),
    [
        ("architecture_planner", {"roles": {"architecture_planner": {"tier": "missing"}}}, "tier"),
        ("architecture_planner", {"roles": {"architecture_planner": {"tier": "T1", "provider": "missing"}}}, "provider"),
        ("plan_critic", {"roles": {"plan_critic": {"tier": "T1", "temperature": 0.1}}}, "temperature 0"),
        ("coder", {"roles": {"coder": {"tier": "T2", "temperature": 0.3}}}, "no greater"),
        ("fixer", {"roles": {"fixer": {"tier": "T2", "temperature": 0.3}}}, "no greater"),
    ],
)
def test_invalid_routes_fail_before_invocation_work(role, overrides, message):
    with pytest.raises(AgentConfigurationError, match=message):
        resolve_route(load_config(overrides=overrides), role)


def test_default_reviewer_uses_a_different_model_and_zero_temperature():
    config = load_config()
    producer = resolve_route(config, "architecture_planner")
    reviewer = resolve_route(config, "plan_critic")
    assert reviewer.model != producer.model
    assert reviewer.temperature == 0


class _NeverCalled:
    def complete(self, *args, **kwargs):
        raise AssertionError("unavailable role must not call LLMClient")


def test_flat_baseline_is_available_only_for_explicit_flat_strategy():
    schema = {"type": "object"}
    inputs = {"planning_index": {}, "delivery_constraints": {}, "manifest_metadata": {}}
    layered = AgentInvoker(load_config(), _NeverCalled())
    with pytest.raises(AgentAvailabilityError):
        layered.invoke(
            role="flat_plan_baseline",
            inputs=inputs,
            output_schema=schema,
            output_example={},
            run_id="run-1",
            stage="S4",
        )

    flat = AgentInvoker(load_config(overrides={"planning": {"strategy": "flat"}}), _NeverCalled())
    with pytest.raises(AssertionError, match="must not call"):
        flat.invoke(
            role="flat_plan_baseline",
            inputs=inputs,
            output_schema=schema,
            output_example={},
            run_id="run-1",
            stage="S4",
        )
