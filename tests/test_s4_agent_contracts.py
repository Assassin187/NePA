import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from nepa.agents.base import AgentAvailabilityError, AgentInvoker
from nepa.config import load_config
from nepa.schemas import flat_plan_draft_contract, plan_critic_contract, task_shard_contract
from nepa.stages.s4_planning import (
    FlatPlanBaselineContractBinding,
    PlanCriticContractBinding,
    TaskPlannerContractBinding,
    validate_plan_critic_result,
    verify_m1_4a2_handoff,
)


ROOT = Path(__file__).parents[1]
LINEAGE = ROOT / "runs/_calibration/s4-architecture/ee5a23a8fcbaa5dc273f36c0365707fac5a9684f050463fc32ec7fd6bc3b67a5"


class _NeverCalled:
    def complete(self, *args, **kwargs):
        raise AssertionError("unavailable role must not call LLMClient")


class _RecordingInvoker:
    def __init__(self, config):
        self.config = config
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(parsed=kwargs["output_example"])


def test_task_planner_binding_has_exact_five_inputs_and_closed_state_free_output():
    schema, example = task_shard_contract()
    invoker = _RecordingInvoker(load_config())
    binding = TaskPlannerContractBinding(invoker)
    inputs = {name: {} for name in ("work_package", "spec_slice", "adjacent_contracts", "test_metadata", "planning_budget")}
    binding.invoke(inputs=inputs, run_id="run", task_id="wp-app")
    assert invoker.calls[0]["inputs"] == inputs
    assert invoker.calls[0]["output_schema"] == schema
    assert Draft202012Validator(schema).is_valid(example)
    invalid = copy.deepcopy(example)
    invalid["tasks"][0]["task_uid"] = "T-001"
    assert not Draft202012Validator(schema).is_valid(invalid)


def test_critic_result_rejects_inconsistent_verdict_and_replacement_plan_fields():
    schema, example = plan_critic_contract()
    invalid = copy.deepcopy(example)
    invalid["verdict"] = "revise"
    with pytest.raises(Exception, match="inconsistent"):
        validate_plan_critic_result(invalid)
    invalid = copy.deepcopy(example)
    invalid["plan"] = {}
    assert not Draft202012Validator(schema).is_valid(invalid)
    assert PlanCriticContractBinding(_RecordingInvoker(load_config())).schema == schema


def test_critic_pass_may_retain_minor_issues_but_revise_must_be_blocking():
    _, example = plan_critic_contract()
    minor = copy.deepcopy(example)
    minor["verdict"] = "pass"
    minor["issues"] = [{
        "id": "MINOR-001", "severity": "minor", "scope": "global", "target_id": "plan",
        "code": "STYLE_NOTE", "description": "non-blocking wording",
        "required_change": "Polish the wording later.", "context_refs": [],
    }]
    assert validate_plan_critic_result(minor)["verdict"] == "pass"
    minor["verdict"] = "revise"
    with pytest.raises(Exception, match="inconsistent"):
        validate_plan_critic_result(minor)


def test_critic_and_flat_bindings_forward_only_their_closed_contracts():
    invoker = _RecordingInvoker(load_config(overrides={"planning": {"strategy": "flat"}}))
    critic = PlanCriticContractBinding(invoker)
    critic_inputs = {"candidate_plan_graph": {}, "coverage_matrix": {}, "lint_report": {}}
    critic.invoke(inputs=critic_inputs, run_id="run", task_id="critic")
    flat = FlatPlanBaselineContractBinding(invoker)
    flat_inputs = {"planning_index": {}, "delivery_constraints": {}, "manifest_metadata": {}}
    flat.invoke(inputs=flat_inputs, run_id="run")
    assert [call["role"] for call in invoker.calls] == ["plan_critic", "flat_plan_baseline"]
    assert invoker.calls[0]["inputs"] == critic_inputs
    assert invoker.calls[1]["inputs"] == flat_inputs


def test_flat_contract_is_closed_and_flat_role_is_strategy_gated():
    schema, example = flat_plan_draft_contract()
    assert Draft202012Validator(schema).is_valid(example)
    invalid = copy.deepcopy(example)
    invalid["plan"] = {}
    assert not Draft202012Validator(schema).is_valid(invalid)
    invoker = AgentInvoker(load_config(), _NeverCalled())
    with pytest.raises(AgentAvailabilityError):
        invoker.invoke(
            role="flat_plan_baseline",
            inputs={"planning_index": {}, "delivery_constraints": {}, "manifest_metadata": {}},
            output_schema=schema,
            output_example=example,
            run_id="run",
            stage="S4",
        )


def test_approved_handoff_resolves_confined_prompt_bytes_before_use():
    bundle = verify_m1_4a2_handoff(LINEAGE)
    assert bundle.initial_bytes == (ROOT / "nepa/agents/prompts/architecture_planner_initial.md").read_bytes()
    assert bundle.repair_bytes == (ROOT / "nepa/agents/prompts/architecture_planner_repair.md").read_bytes()


def test_handoff_rejects_substituted_packaged_prompt_bytes_before_invocation():
    with pytest.raises(Exception, match="prompt bytes"):
        verify_m1_4a2_handoff(LINEAGE, {"initial": b"substituted", "repair": b"substituted"})
