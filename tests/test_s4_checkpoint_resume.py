import json
from pathlib import Path

import pytest

from nepa.config import load_config
from nepa.orchestrator import CrashInjected, Orchestrator
from nepa.run_store import RunStore, SpecRunInputs
from nepa.speclib.lint import canonical_json_bytes
from nepa.stages.s4_planning import S4ArtifactDamage, _CheckpointBook


ROOT = Path(__file__).parents[1]


def _store(tmp_path):
    return RunStore.initialize_spec_run(
        tmp_path,
        SpecRunInputs(ROOT / "gold_file/specIR.json", ROOT / "gold_file/target.json", ROOT / "gold_file/test_bundle.json"),
        load_config(),
    )


def test_checkpoint_parent_chain_replays_and_recovers_budget_without_reset(tmp_path):
    store = _store(tmp_path)
    book = _CheckpointBook(store)
    payload = store.publish_immutable_json("plan/_s4/checkpoint-payload.json", {"kind": "evidence"}).as_dict()
    root = book.publish(kind="commitment", target_id="commitment", parents=[], payloads=[payload], reports=[])
    book.consume("task_shard_repairs", 1, target_id="wp-app")
    child = book.publish(kind="shard_attempt", target_id="wp-app", parents=[root], payloads=[payload], reports=[])

    resumed = _CheckpointBook(store)
    found = resumed.find(kind="shard_attempt", target_id="wp-app", parents=[root])
    assert found is not None
    assert found[1] == child
    assert resumed.task_repairs_used("wp-app") == 1


def test_task_shard_repair_budgets_are_per_package_and_persist_immediately(tmp_path):
    store = _store(tmp_path)
    book = _CheckpointBook(store)
    book.consume("task_shard_repairs", 1, target_id="wp-a")
    book.consume("task_shard_repairs", 1, target_id="wp-b")

    resumed = _CheckpointBook(store)
    assert resumed.task_repairs_used("wp-a") == 1
    assert resumed.task_repairs_used("wp-b") == 1
    with pytest.raises(Exception, match="exhausted"):
        resumed.consume("task_shard_repairs", 1, target_id="wp-a")


def test_pending_agent_result_is_recovered_from_durable_trace(tmp_path):
    store = _store(tmp_path)
    book = _CheckpointBook(store)
    book.reserve_call("plan_critic", "plan-critic", 1)
    output = store.publish_immutable_json(
        "trace/outputs/s4-critic.json",
        {"parsed": {"schema_version": "1.0", "verdict": "pass", "issues": []}},
    )
    store.append_llm_trace({
        "stage": "S4", "agent_role": "plan_critic", "task_id": "plan-critic",
        "attempt": 1, "validation": "pass", "output_path": output.path,
    })

    recovered = _CheckpointBook(store).recover_pending_result("plan_critic", "plan-critic", 1)
    assert recovered is not None
    assert recovered.parsed["verdict"] == "pass"


def test_checkpoint_payload_hash_conflict_fails_closed(tmp_path):
    store = _store(tmp_path)
    book = _CheckpointBook(store)
    payload = store.publish_immutable_json("plan/_s4/checkpoint-payload.json", {"kind": "evidence"}).as_dict()
    book.publish(kind="commitment", target_id="commitment", parents=[], payloads=[payload], reports=[])
    path = store.root / payload["path"]
    path.write_bytes(canonical_json_bytes({"kind": "changed"}))

    with pytest.raises(S4ArtifactDamage, match="hash"):
        _CheckpointBook(store)


def test_checkpoint_parent_hash_conflict_invalidates_descendant(tmp_path):
    store = _store(tmp_path)
    book = _CheckpointBook(store)
    payload = store.publish_immutable_json("plan/_s4/checkpoint-payload.json", {"kind": "evidence"}).as_dict()
    parent = book.publish(kind="commitment", target_id="commitment", parents=[], payloads=[payload], reports=[])
    child = book.publish(kind="candidate", target_id="round_001", parents=[parent], payloads=[payload], reports=[])
    parent_path = store.root / parent["path"]
    original = json.loads(parent_path.read_text(encoding="utf-8"))
    original["target_id"] = "changed"
    parent_path.write_text(json.dumps(original), encoding="utf-8")

    with pytest.raises(S4ArtifactDamage, match="hash"):
        _CheckpointBook(store)
    assert child["path"].startswith("plan/_s4/checkpoints/")


def test_s4_publication_crash_reuses_the_longest_valid_prefix(tmp_path):
    from nepa.stages.s4_planning import S4Controller

    store = RunStore.initialize_spec_run(
        tmp_path,
        SpecRunInputs(ROOT / "gold_file/specIR.json", ROOT / "gold_file/target.json", ROOT / "gold_file/test_bundle.json"),
        load_config(overrides={"run": {"until": "s4"}}),
    )

    class Scripted:
        def __init__(self):
            self.config = load_config()
            self.calls = []

        def invoke(self, **kwargs):
            self.calls.append(kwargs["role"])
            if kwargs["role"] == "architecture_planner":
                value = json.loads((ROOT / "gold_file/architecture-draft.json").read_text(encoding="utf-8"))
            elif kwargs["role"] == "task_planner":
                package = kwargs["inputs"]["work_package"]
                value = {
                    "schema_version": "1.0", "work_package_id": package["id"], "tasks": [{
                        "local_id": "implement", "title": package["title"], "goal": package["goal"], "kind": "integration",
                        "instructions": package["goal"], "deliverable_files": package["allowed_files"],
                        "context_refs": package["context_refs"], "requirement_responsibilities": package["requirement_responsibilities"],
                        "provides_contracts": package["provides_contracts"], "consumes_contracts": package["consumes_contracts"],
                        "depends_on": [], "acceptance": {"build_variant_ids": ["san"], "tests": []},
                    }],
                }
            else:
                value = {"schema_version": "1.0", "verdict": "pass", "issues": []}
            return type("Result", (), {"parsed": value})()

    first = Scripted()
    def crash(point):
        if point == "plan_published":
            raise CrashInjected()

    with pytest.raises(CrashInjected):
        Orchestrator({"s4": S4Controller(first, fault_hook=crash)}).run_spec(store)
    published_plan = (store.root / "plan/versions/plan-1.0.0.json").read_bytes()
    resumed = Scripted()
    assert Orchestrator({"s4": S4Controller(resumed)}).resume(RunStore(store.root)) == 0
    assert resumed.calls == []
    assert (store.root / "plan/versions/plan-1.0.0.json").read_bytes() == published_plan
