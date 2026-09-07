from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nepa.application import build_orchestrator
from nepa.config import load_config
from nepa.metrics import compute_run_metrics
from nepa.orchestrator import CrashInjected, StageContext
from nepa.run_store import RunStore, SpecRunInputs
from nepa.speclib.delivery import compile_delivery_constraints
from nepa.speclib.planning import build_test_manifest_metadata, prepare_architecture_inputs
from nepa.stages.s4_planning import complete_plan_candidate, publish_initial_plan
from nepa.stages.s5_materialization import S5MaterializationController
from nepa.tools.sandbox import SandboxExecutor


ROOT = Path(__file__).parents[1]


def _lease_store(tmp_path: Path, case_id: str, *, executor=None) -> tuple[RunStore, object]:
    source = ROOT / ("gold_file" if case_id == "mqtt" else "tests/fixtures/non_mqtt_application")
    store = RunStore.initialize_spec_run(
        tmp_path,
        SpecRunInputs(source / ("specIR.json" if case_id == "mqtt" else "spec.json"), source / "target.json", source / "test_bundle.json"),
        load_config(),
    )
    prepared = prepare_architecture_inputs(store.root / "spec/spec.json", store.root / "inputs/target.json", store.root / "inputs/test_bundle.json")
    constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
    architecture = json.loads((source / "architecture-draft.json").read_text(encoding="utf-8"))
    codec, entry = architecture["work_packages"]
    contract = architecture["contracts"][0]
    contract.update({"ready_gate": "task", "owner": "codec", "provider": "codec", "consumers": ["entry"]})
    codec["depends_on"] = []
    codec["consumes_contracts"] = []
    codec["provides_contracts"] = ["interface-contract"]
    entry["depends_on"] = ["wp-codec"]
    entry["consumes_contracts"] = ["interface-contract"]
    entry["provides_contracts"] = []
    next(module for module in architecture["modules"] if module["id"] == "codec").update({"consumes_contracts": [], "provides_contracts": ["interface-contract"]})
    next(module for module in architecture["modules"] if module["id"] == "entry").update({"consumes_contracts": ["interface-contract"], "provides_contracts": []})

    def shard(package: dict, local_id: str) -> dict:
        return {
            "schema_version": "1.0", "work_package_id": package["id"], "tasks": [{
                "local_id": local_id, "title": package["title"], "goal": package["goal"], "kind": "integration",
                "instructions": package["goal"], "deliverable_files": package["allowed_files"], "context_refs": package["context_refs"],
                "requirement_responsibilities": package["requirement_responsibilities"], "provides_contracts": package["provides_contracts"],
                "consumes_contracts": package["consumes_contracts"], "depends_on": [], "acceptance": {"build_variant_ids": ["san"], "tests": []},
            }],
        }

    run = store.load_run()
    frozen = {
        "spec_value": prepared.spec, "target_profile_value": prepared.target_profile, "test_bundle_value": prepared.test_bundle,
        "refs": {name: {"path": path, "sha256": run["inputs"][name]["sha256"]} for name, path in {
            "spec": "spec/spec.json", "target_profile": "inputs/target.json", "test_bundle": "inputs/test_bundle.json",
        }.items()},
    }
    draft = {"schema_version": "1.0", "architecture": architecture, "work_packages": architecture["work_packages"], "task_shards": [shard(codec, "codec"), shard(entry, "entry")]}
    completion = complete_plan_candidate(draft, constraints, frozen, manifest, run["config_snapshot"])
    result = publish_initial_plan(store, completion)
    store.publish_immutable_json("plan/_s4/delivery_constraints.json", completion.constraints)
    run = store.load_run()
    run["stages"]["s4"].update({"status": "done", "started_at": "2026-01-01T00:00:00Z", "ended_at": "2026-01-01T00:00:01Z", "output_refs": dict(result.output_refs)})
    store.replace_run(run)

    executor = executor or SandboxExecutor("nepa-sandbox:latest", 2, 4)
    s5 = S5MaterializationController(executor)
    materialized = s5.run(StageContext(store, "s5", store.load_run(), None))
    run = store.load_run()
    run["stages"]["s5"].update({"status": "done", "started_at": "2026-01-01T00:00:00Z", "ended_at": "2026-01-01T00:00:01Z", "output_refs": {key: value.as_dict() for key, value in materialized.output_refs.items()}})
    store.replace_run(run)
    s5.after_commit(store, materialized)
    config = load_config(overrides={"run": {"until": "s6"}})
    run = store.load_run()
    run["config_snapshot"] = config.snapshot
    run["config_snapshot_sha256"] = config.snapshot_sha256
    run["stages"]["s4"]["output_refs"]["config_snapshot_sha256"] = config.snapshot_sha256
    store.replace_run(run)
    return store, config


def _authorization(plan, request):
    state = request["state"]
    lender = next(row for row in state["tasks"] if row["id"] == "T-001")
    current = next(task for task in plan["tasks"] if task["id"] == "T-002")
    return {
        "schema_version": "1.0", "active_plan_ref": dict(state["plan_ref"]), "baseline_commit": request["baseline_commit"], "baseline_tree": request["baseline_tree"],
        "current_task_id": current["id"], "current_task_uid": current["task_uid"], "lenders": [{
            "task_id": lender["id"], "task_uid": lender["task_uid"], "paths": ["src/codec/codec.c"],
            "evidence_refs": [dict(lender["acceptance_evidence"]["task_evidence_ref"])],
        }],
    }


class _LeaseSandboxAgent:
    def __init__(self, lease_subset: str = "mixed") -> None:
        self.calls: list[tuple[str, int, bool]] = []
        self.lease_subset = lease_subset

    def invoke(self, **kwargs):
        task_id = kwargs["task_id"]
        attempt = kwargs["attempt"]
        inputs = kwargs["inputs"]
        leased = "leased_files" in inputs
        self.calls.append((task_id, attempt, leased))
        if task_id == "T-002" and attempt == 1:
            output = {"micro_plan": ["implement"], "files": [{"path": "not-owned", "content": "bad"}], "notes": "lease baseline failure"}
        else:
            files = json.loads(inputs["current_files"])
            if leased:
                files.update(json.loads(inputs["leased_files"]))
                current_paths = set(json.loads(inputs["current_files"]))
                leased_paths = set(json.loads(inputs["leased_files"]))
                if self.lease_subset == "current":
                    files = {path: content for path, content in files.items() if path in current_paths}
                elif self.lease_subset == "leased":
                    files = {path: content for path, content in files.items() if path in leased_paths}
            contracts = json.loads(inputs["contract_map"])
            for contract in contracts.get("contracts", []):
                for export in contract.get("exports", []):
                    implementation = export.get("implementation_file")
                    signature = export.get("signature")
                    if implementation in files and isinstance(signature, str):
                        files[implementation] = signature + "\n" + files[implementation]
            output = {"micro_plan": ["implement"], "files": [{"path": path, "content": content + "\n/* accepted by F1 */\n"} for path, content in files.items()], "notes": "joint lease repair"}
        return SimpleNamespace(parsed=output, response=SimpleNamespace(text=json.dumps(output), tokens_in=1, tokens_out=1, cost_usd=0.0, cached=False))


@pytest.mark.s6_lease
@pytest.mark.parametrize("case_id", ["mqtt", "non_mqtt"])
def test_real_sandbox_f1_joint_commit_for_both_protocol_fixtures(tmp_path: Path, case_id: str):
    store, config = _lease_store(tmp_path / case_id, case_id)
    plan = store._read_json_artifact("plan/versions/plan-1.0.0.json")
    agent = _LeaseSandboxAgent()

    def authorize(request):
        return _authorization(plan, request)

    executor = SandboxExecutor("nepa-sandbox:latest", 2, 4)
    orchestrator = build_orchestrator(config, store, agent=agent, executor=executor, lease_authorization_provider=authorize)
    assert orchestrator.run_spec(store) == 0
    assert agent.calls == [("T-001", 1, False), ("T-002", 1, False), ("T-002", 2, True)]

    state = store._read_json_artifact("plan/plan_state.json")
    assert all(row["status"] == "done" for row in state["tasks"])
    assert {row["id"]: row["attempts"] for row in state["tasks"]} == {"T-001": 1, "T-002": 2}
    assert state["plan_ref"]["revision_seq"] == 0
    evidence = [row["acceptance_evidence"]["task_evidence_ref"] for row in state["tasks"]]
    values = []
    for ref in evidence:
        store.verify_ref(ref, schema_name="task-evidence.schema.json")
        values.append(json.loads(store.read_verified_bytes(ref["path"])))
    assert {value["execution_kind"] for value in values} == {"lease"}
    assert len({tuple(value["lease_member_uids"]) for value in values}) == 1
    joint_ref = next(entry["payload"]["joint_evidence_ref"] for entry in store._read_json_artifact("plan/revision_ledger.json")["entries"] if entry["event_type"] == "lease_finished")
    store.verify_ref(joint_ref, schema_name="joint-evidence.schema.json")
    joint = json.loads(store.read_verified_bytes(joint_ref["path"]))
    assert [member["task_uid"] for member in joint["members"]] == sorted(member["task_uid"] for member in joint["members"])

    revision = store._read_json_artifact("plan/revision_ledger.json")
    event_types = [entry["event_type"] for entry in revision["entries"]]
    assert event_types.count("lease_started") == 1
    assert event_types.count("lease_finished") == 1
    assert event_types.count("verification_committed") == 2
    assert [entry["payload"]["kind"] for entry in revision["entries"] if entry["event_type"] == "verification_committed"][-1] == "lease"
    receipt = store._read_json_artifact("plan/s6_receipt.json")
    assert receipt["status"] == "complete" and receipt["s6_build_ok"] is True
    store.verify_ref(receipt["state_history_ref"], schema_name="state-history.schema.json")
    metrics = compute_run_metrics(store.root)
    assert metrics["task_completion_rate@final"] == {"value": 1.0}
    assert metrics["first_pass_rate"] == {"value": 0.5}
    assert metrics["smoke"]["s6"]["pass"] == {"value": True}
    assert not store._confined("plan/verification_pending.json").exists()
    assert store.load_run()["stages"]["s6"]["status"] == "done"


@pytest.mark.s6_lease
@pytest.mark.parametrize("subset", ["current", "leased"])
def test_f1_accepts_current_only_and_leased_only_changed_subsets(tmp_path: Path, subset: str):
    from test_s5_materialization import FakeExecutor

    executor = FakeExecutor()
    store, config = _lease_store(tmp_path, "non_mqtt", executor=executor)
    plan = store._read_json_artifact("plan/versions/plan-1.0.0.json")
    agent = _LeaseSandboxAgent(subset)
    orchestrator = build_orchestrator(
        config, store, agent=agent, executor=executor,
        lease_authorization_provider=lambda request: _authorization(plan, request),
    )
    assert orchestrator.run_spec(store) == 0
    state = store._read_json_artifact("plan/plan_state.json")
    evidence = [store._read_json_artifact(row["acceptance_evidence"]["task_evidence_ref"]["path"]) for row in state["tasks"]]
    changed_counts = {item["task_id"]: len(item["changed_files"]) for item in evidence}
    assert sorted(changed_counts.values()) == [0, 1]


@pytest.mark.s6_lease
@pytest.mark.parametrize("fault_point", [
    "s6_attempt_allocated", "s6_wal_prepared", "s6_candidate_installed", "s6_commit_created",
    "s6_state_projection_published", "s6_file_ledger_published", "s6_event_published",
    "s6_attempt_terminal_published", "s6_wal_removed",
])
def test_lease_recovery_fault_windows_are_forward_or_rollback_only(tmp_path: Path, fault_point: str):
    from test_s5_materialization import FakeExecutor

    executor = FakeExecutor()
    store, config = _lease_store(tmp_path, "non_mqtt", executor=executor)
    plan = store._read_json_artifact("plan/versions/plan-1.0.0.json")
    first_agent = _LeaseSandboxAgent()

    def authorize(request):
        return _authorization(plan, request)

    def fault(point):
        if point not in {
            "s6_attempt_allocated", "s6_wal_prepared", "s6_candidate_installed", "s6_commit_created",
            "s6_state_projection_published", "s6_file_ledger_published", "s6_event_published",
            "s6_attempt_terminal_published", "s6_wal_removed",
        }:
            return
        state = store._read_json_artifact("plan/plan_state.json")
        started = any(entry["event_type"] == "lease_started" for entry in store._read_json_artifact("plan/revision_ledger.json")["entries"])
        current = next(row for row in state["tasks"] if row["id"] == "T-002")
        if point == fault_point and started and current["attempts"] >= 2:
            raise CrashInjected(point)

    with pytest.raises(CrashInjected):
        build_orchestrator(config, store, agent=first_agent, executor=executor, fault_hook=fault, lease_authorization_provider=authorize).run_spec(store)

    resumed_agent = _LeaseSandboxAgent()
    assert build_orchestrator(config, store, agent=resumed_agent, executor=executor, lease_authorization_provider=authorize).resume(store) == 0
    assert not store._confined("plan/verification_pending.json").exists()
    assert all(row["status"] == "done" for row in store._read_json_artifact("plan/plan_state.json")["tasks"])
    entries = store._read_json_artifact("plan/revision_ledger.json")["entries"]
    assert sum(entry["event_type"] == "lease_started" for entry in entries) == 1
    assert sum(entry["event_type"] == "lease_finished" for entry in entries) == 1
