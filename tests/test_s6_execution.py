import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nepa.application import build_orchestrator
from nepa.config import load_config
from nepa.orchestrator import CrashInjected, StageContext
from nepa.run_store import RunStoreError
from nepa.stages.s5_materialization import S5MaterializationController

from test_s5_materialization import FakeExecutor, _frozen_fixture_store, _sealed_store


def _ready_store(tmp_path: Path):
    store, _completion = _sealed_store(tmp_path)
    s5 = S5MaterializationController(FakeExecutor())
    result = s5.run(StageContext(store, "s5", store.load_run(), None))
    run = store.load_run()
    run["stages"]["s5"].update({
        "status": "done", "started_at": "2026-01-01T00:00:00Z", "ended_at": "2026-01-01T00:00:01Z",
        "output_refs": {key: value.as_dict() for key, value in result.output_refs.items()},
    })
    store.replace_run(run)
    s5.after_commit(store, result)
    config = load_config(overrides={"run": {"until": "s6"}})
    run = store.load_run()
    run["config_snapshot"] = config.snapshot
    run["config_snapshot_sha256"] = config.snapshot_sha256
    run["stages"]["s4"]["output_refs"]["config_snapshot_sha256"] = config.snapshot_sha256
    store.replace_run(run)
    return store, config


class _CurrentFilesAgent:
    def __init__(self, *, failures: int = 0, mutate: bool = False):
        self.failures = failures
        self.mutate = mutate
        self.calls = []
        self.input_snapshots = []

    def invoke(self, **kwargs):
        self.calls.append((kwargs["task_id"], kwargs["attempt"], kwargs["tier_override"]))
        self.input_snapshots.append(kwargs["inputs"])
        if kwargs["attempt"] <= self.failures:
            output = {"micro_plan": ["implement"], "files": [{"path": "not-owned", "content": "bad"}], "notes": "failed"}
        else:
            current = json.loads(kwargs["inputs"]["current_files"])
            if self.mutate:
                current = {path: content + "\n/* accepted by S6 */\n" for path, content in current.items()}
            output = {"micro_plan": ["implement"], "files": [{"path": path, "content": content} for path, content in current.items()], "notes": "accepted"}
        return SimpleNamespace(
            parsed=output,
            response=SimpleNamespace(text=json.dumps(output), tokens_in=1, tokens_out=1, cost_usd=0.0, cached=False),
        )


class _FrozenProviderAgent:
    def __init__(self, fixture: Path):
        value = json.loads((fixture / "provider-sequence.json").read_text(encoding="utf-8"))
        self.responses = value["responses"]
        self.sequence = list(value["sequences"]["success"])
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append((kwargs["task_id"], kwargs["attempt"], kwargs["tier_override"]))
        expected = self.sequence.pop(0)
        assert (kwargs["task_id"], kwargs["attempt"], kwargs["tier_override"]) == (expected["task_id"], expected["attempt"], expected["tier"])
        output = self.responses[expected["response"]]
        return SimpleNamespace(
            parsed=output,
            response=SimpleNamespace(text=json.dumps(output), tokens_in=1, tokens_out=1, cost_usd=0.0, cached=False),
        )


@pytest.mark.s6_execution
def test_s6_success_receipt_and_completed_replay_are_read_only(tmp_path):
    store, config = _ready_store(tmp_path)
    agent = _CurrentFilesAgent(mutate=True)
    orchestrator = build_orchestrator(config, store, agent=agent, executor=FakeExecutor())

    assert orchestrator.run_spec(store) == 0
    run = store.load_run()
    assert run["termination_kind"] == "planned_stop"
    assert run["stages"]["s6"]["status"] == "done"
    assert run["stages"]["s7"]["status"] == "pending"
    assert run["stages"]["s9"]["status"] == "pending"
    before = {
        path.relative_to(store.root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in store.root.rglob("*") if path.is_file() and path.name != ".controller.lock"
    }
    assert orchestrator.resume(store) == 0
    after = {
        path.relative_to(store.root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in store.root.rglob("*") if path.is_file() and path.name != ".controller.lock"
    }
    assert before == after
    assert len(agent.calls) == 2


@pytest.mark.s6_execution
def test_s6_exhaustion_uses_four_calls_and_degraded_exit(tmp_path):
    store, config = _ready_store(tmp_path)
    agent = _CurrentFilesAgent(failures=4)
    orchestrator = build_orchestrator(config, store, agent=agent, executor=FakeExecutor())

    assert orchestrator.run_spec(store) == 10
    assert agent.calls == [
        ("T-001", 1, "T2"), ("T-001", 2, "T2"), ("T-001", 3, "T2"), ("T-001", 4, "T1"),
        ("T-002", 1, "T2"), ("T-002", 2, "T2"), ("T-002", 3, "T2"), ("T-002", 4, "T1"),
    ]
    state = store._read_json_artifact("plan/plan_state.json")
    assert all(row["status"] == "blocked" and row["attempts"] == 4 for row in state["tasks"])
    assert not store._confined("plan/s6_receipt.json").exists()


def test_s6_normal_noop_never_marks_tasks_done_or_creates_task_commit(tmp_path):
    store, config = _ready_store(tmp_path)
    workspace = store._confined("workspace")
    baseline = __import__("subprocess").run(
        ["git", "rev-parse", "HEAD"], cwd=workspace, capture_output=True, text=True, check=True,
    ).stdout.strip()
    agent = _CurrentFilesAgent(mutate=False)

    assert build_orchestrator(config, store, agent=agent, executor=FakeExecutor()).run_spec(store) == 10
    state = store._read_json_artifact("plan/plan_state.json")
    assert all(row["status"] == "blocked" for row in state["tasks"])
    head = __import__("subprocess").run(
        ["git", "rev-parse", "HEAD"], cwd=workspace, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert head == baseline
    assert all(row["state"] == "slot_only" for row in store._read_json_artifact("plan/file_ledger.json")["files"] if row["class"] == "s6_owned")


def test_postcommit_corrupt_evidence_fails_internal_and_retains_wal(tmp_path):
    store, config = _ready_store(tmp_path)

    def stop_after_commit(point):
        if point == "s6_commit_created":
            raise CrashInjected(point)

    with pytest.raises(CrashInjected):
        build_orchestrator(
            config, store, agent=_CurrentFilesAgent(mutate=True), executor=FakeExecutor(),
            fault_hook=stop_after_commit,
        ).run_spec(store)
    wal = store._read_json_artifact("plan/verification_pending.json")
    evidence_path = store._confined(wal["evidence_ref"]["path"])
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["plan_sha256"] = "0" * 64
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    state_before = store._confined("plan/plan_state.json").read_bytes()

    assert build_orchestrator(config, store, agent=_CurrentFilesAgent(mutate=True), executor=FakeExecutor()).resume(store) == 1
    assert store._confined("plan/verification_pending.json").exists()
    assert store._confined("plan/plan_state.json").read_bytes() == state_before


@pytest.mark.s6_execution
def test_s6_fixer_receives_the_matching_failed_candidate(tmp_path):
    store, config = _ready_store(tmp_path)
    agent = _CurrentFilesAgent(failures=1, mutate=True)
    orchestrator = build_orchestrator(config, store, agent=agent, executor=FakeExecutor())

    assert orchestrator.run_spec(store) == 0
    assert len(agent.calls) == 4
    assert agent.calls[0] == ("T-001", 1, "T2")
    assert agent.calls[1] == ("T-001", 2, "T2")
    assert json.loads(agent.input_snapshots[1]["failed_candidate"]) == {"not-owned": "bad"}
    assert json.loads(agent.input_snapshots[1]["validation_feedback"])["error"]
    assert agent.calls[2] == ("T-002", 1, "T2")


def test_s6_dependency_propagation_does_not_consume_attempts(tmp_path):
    from nepa.speclib.plan_state import initialize_plan_state

    store, _completion = _sealed_store(tmp_path)
    plan = store._read_json_artifact("plan/versions/plan-1.0.0.json")
    state = initialize_plan_state(plan, plan_ref=store._read_json_artifact("plan/active_plan.json"))
    state["tasks"][0].update({"status": "blocked", "attempts": 4, "last_error": "attempts/blocked/failure.json"})
    store.replace_json("plan/plan_state.json", state, schema_name="plan-state.schema.json")
    dependent_plan = copy.deepcopy(plan)
    dependent_plan["tasks"][1]["depends_on"] = [dependent_plan["tasks"][0]["id"]]
    from nepa.stages.s6_execution import S6ExecutionController

    S6ExecutionController(_CurrentFilesAgent(), FakeExecutor())._propagate_dependency_blocks(store, dependent_plan)
    updated = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    assert updated["tasks"][1]["status"] == "blocked_by_dependency"
    assert updated["tasks"][1]["attempts"] == 0
    assert updated["s6_attempts_used"] == 0


@pytest.mark.s6_execution
@pytest.mark.parametrize(
    "fault_point",
    [
        "s6_attempt_allocated", "s6_candidate_persisted", "s6_candidate_workspace_ready",
        "s6_evidence_published", "s6_wal_prepared", "s6_candidate_installed", "s6_commit_created",
        "s6_wal_committed", "s6_state_projection_published", "s6_file_ledger_published",
        "s6_event_published", "s6_attempt_terminal_published", "s6_wal_removed", "s6_state_published",
        "s6_receipt_published",
    ],
)
def test_s6_fault_boundaries_reconcile_without_duplicate_first_task(tmp_path, fault_point):
    store, config = _ready_store(tmp_path)

    def fault_hook(point):
        if point == fault_point:
            raise CrashInjected(point)

    first_agent = _CurrentFilesAgent(mutate=True)
    with pytest.raises(CrashInjected):
        build_orchestrator(config, store, agent=first_agent, executor=FakeExecutor(), fault_hook=fault_hook).run_spec(store)

    resumed_agent = _CurrentFilesAgent(mutate=True)
    assert build_orchestrator(config, store, agent=resumed_agent, executor=FakeExecutor()).resume(store) == 0
    assert store.load_run()["stages"]["s6"]["status"] == "done"
    assert not store._confined("plan/verification_pending.json").exists()
    assert store._confined("plan/s6_receipt.json").is_file()


@pytest.mark.parametrize(
    "bound_field",
    [
        "active_plan_ref", "binding_ref", "plan_state_ref", "file_ledger_ref", "revision_ledger_ref",
        "build_result_refs", "smoke_result_refs", "workspace_head",
    ],
)
def test_s6_completed_receipt_rejects_bound_artifact_corruption(tmp_path, bound_field):
    from nepa.stages.s6_execution import S6ExecutionController

    store, config = _ready_store(tmp_path)
    agent = _CurrentFilesAgent(mutate=True)
    orchestrator = build_orchestrator(config, store, agent=agent, executor=FakeExecutor())
    assert orchestrator.run_spec(store) == 0
    receipt_path = store._confined("plan/s6_receipt.json")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if bound_field == "workspace_head":
        receipt[bound_field] = "0" * 40
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    else:
        ref = receipt[bound_field][0] if bound_field.endswith("_refs") else receipt[bound_field]
        target = store._confined(ref["path"])
        target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(RunStoreError):
        S6ExecutionController(agent, FakeExecutor()).verify_completed(store)


@pytest.mark.s6_execution
@pytest.mark.parametrize("case_id", ["mqtt", "non_mqtt"])
def test_s6_frozen_e0_fixtures_pass_through_real_sandbox(tmp_path, case_id):
    from nepa.tools.sandbox import SandboxExecutor

    store = _frozen_fixture_store(tmp_path / case_id, case_id)
    executor = SandboxExecutor("nepa-sandbox:latest", 2, 4)
    s5 = S5MaterializationController(executor)
    result = s5.run(StageContext(store, "s5", store.load_run(), None))
    run = store.load_run()
    run["stages"]["s5"].update({
        "status": "done", "started_at": "2026-01-01T00:00:00Z", "ended_at": "2026-01-01T00:00:01Z",
        "output_refs": {key: value.as_dict() for key, value in result.output_refs.items()},
    })
    store.replace_run(run)
    s5.after_commit(store, result)
    config = load_config(overrides={"run": {"until": "s6"}})
    run = store.load_run()
    run["config_snapshot"] = config.snapshot
    run["config_snapshot_sha256"] = config.snapshot_sha256
    run["stages"]["s4"]["output_refs"]["config_snapshot_sha256"] = config.snapshot_sha256
    store.replace_run(run)
    agent = _FrozenProviderAgent(Path(__file__).parent / "fixtures" / "s6" / case_id)
    orchestrator = build_orchestrator(config, store, agent=agent, executor=executor)

    assert orchestrator.run_spec(store) == 0
    assert store.load_run()["termination_kind"] == "planned_stop"
    assert len(agent.calls) == 2
