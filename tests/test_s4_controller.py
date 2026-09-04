import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nepa.config import load_config
from nepa.orchestrator import ControlledStageFailure, Orchestrator
from nepa.run_store import RunStore, SpecRunInputs
from nepa.stages.s4_planning import S4Controller


ROOT = Path(__file__).parents[1]
ARCHITECTURE = json.loads((ROOT / "gold_file/architecture-draft.json").read_text(encoding="utf-8"))
NON_MQTT = ROOT / "tests/fixtures/non_mqtt_application"


def _store(tmp_path, *, strategy="layered", architecture_repairs=1):
    return RunStore.initialize_spec_run(
        tmp_path,
        SpecRunInputs(ROOT / "gold_file/specIR.json", ROOT / "gold_file/target.json", ROOT / "gold_file/test_bundle.json"),
        load_config(overrides={"planning": {"strategy": strategy}, "budgets": {"plan_architecture_repairs": architecture_repairs}, "run": {"until": "s4"}}),
    )


def _shard(package):
    return {
        "schema_version": "1.0",
        "work_package_id": package["id"],
        "tasks": [{
            "local_id": "implement",
            "title": package["title"],
            "goal": package["goal"],
            "kind": "integration",
            "instructions": package["goal"],
            "deliverable_files": list(package["allowed_files"]),
            "context_refs": copy.deepcopy(package["context_refs"]),
            "requirement_responsibilities": copy.deepcopy(package["requirement_responsibilities"]),
            "provides_contracts": list(package["provides_contracts"]),
            "consumes_contracts": list(package["consumes_contracts"]),
            "depends_on": [],
            "acceptance": {"build_variant_ids": ["san"], "tests": []},
        }],
    }


def _flat_draft(architecture):
    return {
        "schema_version": "1.0",
        "architecture": copy.deepcopy(architecture),
        "work_packages": copy.deepcopy(architecture["work_packages"]),
        "task_shards": [_shard(package) for package in architecture["work_packages"]],
    }


class ScriptedInvoker:
    def __init__(self, *, architecture=None, repair=None, critic=None, strategy="layered"):
        self.config = load_config(overrides={"planning": {"strategy": strategy}})
        self.architecture = copy.deepcopy(architecture or ARCHITECTURE)
        self.repair = copy.deepcopy(repair)
        self.critic = copy.deepcopy(critic or {"schema_version": "1.0", "verdict": "pass", "issues": []})
        self.calls = []

    def invoke(self, **kwargs):
        role = kwargs["role"]
        self.calls.append(kwargs)
        if role == "architecture_planner":
            parsed = self.architecture if "patch_ops" not in kwargs["output_schema"].get("properties", {}) else self.repair
        elif role == "task_planner":
            parsed = _shard(kwargs["inputs"]["work_package"])
        elif role == "plan_critic":
            parsed = self.critic.pop(0) if isinstance(self.critic, list) else self.critic
        elif role == "flat_plan_baseline":
            parsed = _flat_draft(self.architecture)
        else:
            raise AssertionError(role)
        if parsed is None:
            raise AssertionError(f"missing scripted result for {role}")
        return SimpleNamespace(parsed=copy.deepcopy(parsed))


def test_layered_controller_is_registered_at_the_programmatic_boundary_and_publishes_typed_seal(tmp_path):
    store = _store(tmp_path)
    invoker = ScriptedInvoker()
    orchestrator = Orchestrator()
    orchestrator.register_s4(S4Controller(invoker))

    assert orchestrator.run_spec(store) == 0
    run = store.load_run()
    assert set(run["stages"]["s4"]["output_refs"]) == {
        "plan", "active_plan", "delivery_blueprint_sha256", "config_snapshot_sha256",
    }
    assert [call["role"] for call in invoker.calls] == [
        "architecture_planner", "task_planner", "task_planner", "plan_critic",
    ]
    assert not (ROOT / "plan/active_plan.json").exists()


def test_architecture_semantic_repair_is_bounded_and_precedes_any_shard(tmp_path):
    broken = copy.deepcopy(ARCHITECTURE)
    broken["work_packages"][1]["depends_on"] = ["wp-codec"]
    repaired = {"schema_version": "2.0", "patch_ops": [{
        "op": "replace", "path": "/work_packages/wp-entry/depends_on", "expected_presence": "present", "value": [],
    }]}
    store = _store(tmp_path)
    invoker = ScriptedInvoker(architecture=broken, repair=repaired)
    orchestrator = Orchestrator({"s4": S4Controller(invoker)})

    assert orchestrator.run_spec(store) == 0
    assert [call["role"] for call in invoker.calls].count("architecture_planner") == 2
    assert [call["role"] for call in invoker.calls].count("task_planner") == 2
    assert (store.root / "plan/_s4/checkpoints").is_dir()


def test_architecture_budget_exhaustion_is_controlled_and_does_not_publish_plan(tmp_path):
    broken = copy.deepcopy(ARCHITECTURE)
    broken["work_packages"][1]["depends_on"] = ["wp-codec"]
    store = _store(tmp_path, architecture_repairs=0)
    invoker = ScriptedInvoker(architecture=broken)

    assert Orchestrator({"s4": S4Controller(invoker)}).run_spec(store) == 10
    run = store.load_run()
    assert run["termination_kind"] == "controlled_exit"
    assert run["termination_request"]["reason"]["code"] == "S4_BUDGET_EXHAUSTED"
    assert not (store.root / "plan/versions/plan-1.0.0.json").exists()


def test_explicit_flat_strategy_has_no_layered_fallback(tmp_path):
    store = _store(tmp_path, strategy="flat")
    invoker = ScriptedInvoker(strategy="flat")

    assert Orchestrator({"s4": S4Controller(invoker)}).run_spec(store) == 0
    assert [call["role"] for call in invoker.calls] == ["flat_plan_baseline", "plan_critic"]


def _local_issue():
    return {
        "schema_version": "1.0", "verdict": "revise", "issues": [{
            "id": "LOCAL-001", "severity": "major", "scope": "work_package", "target_id": "wp-entry",
            "code": "LOCAL_PLAN_GAP", "description": "The entry package has a planning gap.",
            "required_change": "Redo the entry package shard.", "context_refs": [],
        }],
    }


def _global_issue():
    return {
        "schema_version": "1.0", "verdict": "revise", "issues": [{
            "id": "GLOBAL-001", "severity": "major", "scope": "global", "target_id": "plan",
            "code": "GLOBAL_PLAN_GAP", "description": "The architecture needs one global revision.",
            "required_change": "Replan the architecture and regenerate shards.", "context_refs": [],
        }],
    }


def test_critic_local_repair_redoes_only_the_named_shard_and_then_reviews_fresh(tmp_path):
    store = _store(tmp_path)
    invoker = ScriptedInvoker(critic=[_local_issue(), {"schema_version": "1.0", "verdict": "pass", "issues": []}])

    assert Orchestrator({"s4": S4Controller(invoker)}).run_spec(store) == 0
    assert sum(call["role"] == "task_planner" for call in invoker.calls) == 3
    assert sum(call["role"] == "plan_critic" for call in invoker.calls) == 2
    assert [call["task_id"] for call in invoker.calls if call["role"] == "task_planner"] == [
        "wp-codec", "wp-entry", "wp-entry",
    ]


def test_critic_repeated_major_signature_stops_without_publishing_a_plan(tmp_path):
    store = _store(tmp_path)
    invoker = ScriptedInvoker(critic=[_local_issue(), _local_issue()])

    assert Orchestrator({"s4": S4Controller(invoker)}).run_spec(store) == 20
    run = store.load_run()
    assert run["termination_request"]["reason"]["code"] == "S4_CRITIC_NON_CONVERGENT"
    assert not (store.root / "plan/versions/plan-1.0.0.json").exists()


def test_global_replan_does_not_consume_architecture_validation_repair_budget(tmp_path):
    repair = {"schema_version": "2.0", "patch_ops": [{
        "op": "replace", "path": "/work_packages/wp-entry/depends_on",
        "expected_presence": "present", "value": [],
    }]}
    store = _store(tmp_path, architecture_repairs=0)
    invoker = ScriptedInvoker(
        repair=repair,
        critic=[_global_issue(), {"schema_version": "1.0", "verdict": "pass", "issues": []}],
    )

    assert Orchestrator({"s4": S4Controller(invoker)}).run_spec(store) == 0
    assert sum(call["role"] == "architecture_planner" for call in invoker.calls) == 2


def test_terminal_resume_revalidates_the_sealed_plan(tmp_path):
    store = _store(tmp_path)
    orchestrator = Orchestrator({"s4": S4Controller(ScriptedInvoker())})
    assert orchestrator.run_spec(store) == 0
    plan_path = store.root / "plan/versions/plan-1.0.0.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["version"] = "1.0.1"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    assert orchestrator.resume(RunStore(store.root)) == 1
    assert store.load_run()["termination_kind"] == "internal_error"


@pytest.mark.parametrize("strategy", ["layered", "flat"])
def test_controller_runs_protocol_neutral_fixture_end_to_end(tmp_path, strategy):
    architecture = json.loads((NON_MQTT / "architecture-draft.json").read_text(encoding="utf-8"))
    store = RunStore.initialize_spec_run(
        tmp_path,
        SpecRunInputs(NON_MQTT / "spec.json", NON_MQTT / "target.json", NON_MQTT / "test_bundle.json"),
        load_config(overrides={"planning": {"strategy": strategy}, "run": {"until": "s4"}}),
    )
    invoker = ScriptedInvoker(architecture=architecture, strategy=strategy)

    assert Orchestrator({"s4": S4Controller(invoker)}).run_spec(store) == 0
    assert (store.root / "plan/versions/plan-1.0.0.json").is_file()
    assert not (store.root / "_s4").exists()
