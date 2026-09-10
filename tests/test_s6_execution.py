import copy
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from nepa.application import build_orchestrator
from nepa.config import load_config
from nepa.orchestrator import CrashInjected, ControlledStageFailure, StageContext
from nepa.run_store import RunStoreError
from nepa.stages.s5_materialization import S5MaterializationController

from test_s5_materialization import FakeExecutor, _frozen_fixture_store, _sealed_store


def _ready_store(tmp_path: Path, case_id=None):
    if case_id is None:
        store, _completion = _sealed_store(tmp_path)
    else:
        store = _frozen_fixture_store(tmp_path, case_id)
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
        self.roles = []
        self.input_snapshots = []

    def invoke(self, **kwargs):
        self.calls.append((kwargs["task_id"], kwargs["attempt"], kwargs["tier_override"]))
        self.roles.append(kwargs["role"])
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


def test_candidate_normalizer_accepts_nonempty_subset_and_enforces_frozen_group_intersection():
    from nepa.agents.s6 import S6AgentError, normalize_candidate

    task = {"deliverable_files": ["src/one.c", "src/two.c"]}
    response = {
        "micro_plan": ["repair one affected file"],
        "files": [{"path": "src/one.c", "content": "changed\n"}],
        "notes": "subset",
    }
    assert normalize_candidate(response, task, allowed_paths={"src/one.c"}) == {"src/one.c": b"changed\n"}
    outside = copy.deepcopy(response)
    outside["files"][0]["path"] = "src/two.c"
    with pytest.raises(S6AgentError, match="outside the task whitelist"):
        normalize_candidate(outside, task, allowed_paths={"src/one.c"})


def test_s6_migration_fixture_generator_is_byte_stable_and_source_bound(tmp_path):
    output = tmp_path / "generated"
    subprocess.run(
        ["uv", "run", "python", "tests/tools/generate_s6_migration_fixtures.py", "--output", str(output)],
        cwd=Path(__file__).parents[1], check=True,
    )
    for case_id in ("mqtt", "non_mqtt"):
        generated = output / case_id / "migration-fixtures.json"
        committed = Path(__file__).parent / "fixtures" / "s6" / "migration" / case_id / "migration-fixtures.json"
        assert generated.read_bytes() == committed.read_bytes()
        value = json.loads(committed.read_text(encoding="utf-8"))
        for ref in value["sources"].values():
            source = Path(__file__).parents[1] / ref["path"]
            assert hashlib.sha256(source.read_bytes()).hexdigest() == ref["sha256"]
        assert value["f2"]["scenarios"]["revalidate"]["attempt"] == 0
        assert value["f2"]["scenarios"]["amend_after_four"]["ordinary_attempts"] == 4
        assert len(value["f3"]["group"]["member_task_uids"]) == 2


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


@pytest.mark.s6_execution
@pytest.mark.parametrize("fault_point", ["attempt_persisted", "state_history_appended"])
def test_attempt_allocation_replays_partial_persistence_without_refund(tmp_path, fault_point):
    from nepa.stages.s6_execution import S6ExecutionController, _git
    from nepa.tools.build import _tree_sha256

    store, _config = _ready_store(tmp_path)
    controller = S6ExecutionController(_CurrentFilesAgent(), FakeExecutor())
    _run, plan, _active, _blueprint, _constraints, _epoch = controller._admit(store)
    task = plan["tasks"][0]
    baseline_commit = _git(store._confined("workspace"), "rev-parse", "HEAD")
    baseline_tree = _tree_sha256(store._confined("workspace"))

    def crash(point):
        if point == fault_point:
            raise CrashInjected(point)

    with pytest.raises(CrashInjected):
        store.allocate_s6_attempt(
            task_id=task["id"], task_uid=task["task_uid"], role="coder", tier="T2",
            baseline_commit=baseline_commit, baseline_tree=baseline_tree, fault_hook=crash,
        )
    replay = store.allocate_s6_attempt(
        task_id=task["id"], task_uid=task["task_uid"], role="coder", tier="T2",
        baseline_commit=baseline_commit, baseline_tree=baseline_tree,
    )
    assert replay["attempt"]["attempt"] == 1
    assert replay["state"]["s6_attempts_used"] == 1
    history = store._read_json_artifact("plan/state_history.json", schema_name="state-history.schema.json")
    assert [entry["event_type"] for entry in history["entries"]].count("attempt_started") == 1


@pytest.mark.s6_execution
@pytest.mark.parametrize("mode", ["revalidate", "amend"])
def test_migration_allocation_persists_current_lineage_and_replays_without_refund(tmp_path, mode):
    from nepa.stages.s6_execution import S6ExecutionController, _git
    from nepa.tools.build import _tree_sha256

    store, _config = _ready_store(tmp_path)
    controller = S6ExecutionController(_CurrentFilesAgent(), FakeExecutor())
    _run, plan, _active, _blueprint, _constraints, _epoch = controller._admit(store)
    task = plan["tasks"][0]
    state = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    row = next(item for item in state["tasks"] if item["id"] == task["id"])
    row.update({"execution_mode": mode, "migration_ref": {"revision_seq": 1, "event_seq": 2}})
    if mode == "amend":
        row["attempts"] = 4
        state["s6_attempts_used"] = 4
    store.replace_json("plan/plan_state.json", state, schema_name="plan-state.schema.json")
    workspace = store._confined("workspace")
    baseline_commit = _git(workspace, "rev-parse", "HEAD")
    baseline_tree = _tree_sha256(workspace)

    def crash(point):
        if point == "migration_record_persisted":
            raise CrashInjected(point)

    with pytest.raises(CrashInjected):
        store.allocate_s6_migration(
            task_id=task["id"], task_uid=task["task_uid"], mode=mode,
            baseline_commit=baseline_commit, baseline_tree=baseline_tree, fault_hook=crash,
        )
    replay = store.allocate_s6_migration(
        task_id=task["id"], task_uid=task["task_uid"], mode=mode,
        baseline_commit=baseline_commit, baseline_tree=baseline_tree,
    )
    assert replay["record"]["migration_ref"] == {"revision_seq": 1, "event_seq": 2}
    assert replay["state"]["evidence_counters"][task["task_uid"]] == 1
    assert replay["state"]["s6_attempts_used"] == (5 if mode == "amend" else 0)
    assert replay["state"]["tasks"][0]["attempts"] == (4 if mode == "amend" else 0)


@pytest.mark.s6_execution
def test_s6_admits_current_ready_e1_context(tmp_path):
    from nepa.stages.s6_execution import S6ExecutionController
    from test_s5_multi_epoch import _accepted_structural_e1

    store, _s5 = _accepted_structural_e1(tmp_path)
    run, plan, active, _blueprint, _constraints, epoch = S6ExecutionController(_CurrentFilesAgent(), FakeExecutor())._admit(store)

    assert active["version"] == "1.1.0"
    assert active["epoch"] == "E1"
    assert active["sha256"] == store._json_artifact_hash(active["path"])
    assert epoch["materialization_status"] == "ready"
    assert run["stages"]["s5"]["output_refs"]["epoch_receipt"]["path"] == "plan/epochs/E1/receipt.json"


@pytest.mark.s6_execution
def test_s6_admits_current_f2_binding_on_existing_epoch(tmp_path):
    from nepa.speclib.delivery import compile_delivery_constraints
    from nepa.speclib.materialization import derive_rendering_view
    from nepa.stages.s6_execution import S6ExecutionController
    from test_s5_multi_epoch import _accept_e0_and_activate

    store, completion, plan, pointer, _s5 = _accept_e0_and_activate(tmp_path, "F2")
    spec = store._read_json_artifact("spec/spec.json")
    target = store._read_json_artifact("inputs/target.json")
    constraints = compile_delivery_constraints(spec, target)
    epoch = store._read_json_artifact("plan/epochs/E0/receipt.json", schema_name="epoch-receipt.schema.json")
    binding_ref = store.publish_version_binding({
        "plan_ref": {"path": pointer["path"], "sha256": pointer["sha256"]},
        "blueprint": completion.blueprint,
        "rendering_view": derive_rendering_view(plan, spec, target, completion.blueprint, constraints),
        "epoch_receipt": epoch,
        "constraints": constraints,
    })
    run = store.load_run()
    run["stages"]["s5"]["output_refs"]["binding_receipt"] = binding_ref.as_dict()
    store.replace_run(run)

    _run, admitted_plan, active, _blueprint, _constraints, admitted_epoch = S6ExecutionController(_CurrentFilesAgent(), FakeExecutor())._admit(store)
    assert active["version"] == "1.0.1"
    assert active["epoch"] == "E0"
    assert admitted_plan == plan
    assert admitted_epoch == epoch


@pytest.mark.s6_execution
def test_s6_admits_only_exact_registered_pending_repair_groups(tmp_path):
    from nepa.stages.s6_execution import S6AdmissionError, S6ExecutionController
    from nepa.stages.s5_materialization import S5MaterializationController
    from test_s5_multi_epoch import _CompilerFailureExecutor, _finish_s5, _structural_e1_store

    store, _plan, _blueprint, _pointer, _report, _old_path, new_path, _old_bytes = _structural_e1_store(tmp_path, pending_group=True)
    s5 = S5MaterializationController(_CompilerFailureExecutor(new_path))
    result = s5.run(StageContext(store, "s5", store.load_run(), None))
    _finish_s5(store, s5, result, started="2026-01-01T00:00:04Z", ended="2026-01-01T00:00:05Z")
    controller = S6ExecutionController(_CurrentFilesAgent(), FakeExecutor())

    _run, current_plan, _active, _blueprint, _constraints, current_epoch = controller._admit(store)
    assert current_epoch["pending_group_ids"] == ["g-1-1"]
    current_state = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    selected = controller._choose(current_plan, current_state, pending_group_ids=current_epoch["pending_group_ids"])
    assert selected is not None
    assert next(row for row in current_state["tasks"] if row["id"] == selected["id"])["group_id"] == "g-1-1"
    state = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    next(row for row in state["tasks"] if row.get("group_id"))["group_id"] = "g-1-2"
    store.replace_json("plan/plan_state.json", state, schema_name="plan-state.schema.json")
    with pytest.raises(S6AdmissionError, match="group id disagrees|groups disagree"):
        controller._admit(store)


@pytest.mark.s6_execution
def test_s6_consumes_m1_8_single_member_pending_repair_as_one_group_transaction(tmp_path):
    from nepa.stages.s6_execution import S6ExecutionController
    from test_s5_multi_epoch import _CompilerFailureExecutor, _finish_s5, _structural_e1_store

    store, _plan, _blueprint, _pointer, _report, _old_path, new_path, _old_bytes = _structural_e1_store(
        tmp_path, pending_group=True
    )
    s5 = S5MaterializationController(_CompilerFailureExecutor(new_path))
    result = s5.run(StageContext(store, "s5", store.load_run(), None))
    _finish_s5(store, s5, result, started="2026-01-01T00:00:04Z", ended="2026-01-01T00:00:05Z")
    agent = _CurrentFilesAgent(mutate=True)

    S6ExecutionController(agent, FakeExecutor()).run(StageContext(store, "s6", store.load_run(), None))

    events = [
        entry["payload"]
        for entry in store._read_json_artifact("plan/revision_ledger.json", schema_name="revision-ledger.schema.json")["entries"]
        if entry.get("event_type") == "verification_committed" and entry.get("payload", {}).get("kind") == "group"
    ]
    assert len(events) == 1
    assert len(events[0]["member_uids"]) == 1
    joint = store._read_json_artifact(events[0]["joint_evidence_ref"]["path"], schema_name="joint-evidence.schema.json")
    assert len(joint["members"]) == 1


@pytest.mark.s6_execution
def test_group_descriptor_is_exact_and_topologically_stable(tmp_path):
    from nepa.stages.s6_execution import S6AdmissionError, S6ExecutionController

    store, _candidate, _source_blueprint, _pointer, _report, group = _two_member_pending_group_store(tmp_path)
    controller = S6ExecutionController(_CurrentFilesAgent(), FakeExecutor())
    _run, plan, _active, blueprint, _constraints, epoch = controller._admit(store)
    state = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    descriptor = controller._group_descriptor(store, plan, blueprint, state, epoch, group["group_id"])
    replay = controller._group_descriptor(store, plan, blueprint, state, epoch, group["group_id"])
    assert descriptor == replay
    assert descriptor["member_uids"] == group["member_task_uids"]
    assert {task["task_uid"] for task in descriptor["members"]} == set(group["member_task_uids"])
    assert descriptor["affected_paths"] == group["affected_paths"]
    assert descriptor["build_artifact_ids"] == group["build_artifact_ids"]

    damaged = copy.deepcopy(state)
    next(row for row in damaged["tasks"] if row["task_uid"] == group["member_task_uids"][0])["group_id"] = None
    with pytest.raises(S6AdmissionError, match="exact unresolved"):
        controller._group_descriptor(store, plan, blueprint, damaged, epoch, group["group_id"])


@pytest.mark.s6_execution
@pytest.mark.parametrize("case_id", [None, "mqtt", "non_mqtt"])
def test_migration_group_success_publishes_one_atomic_joint_result(tmp_path, case_id):
    from nepa.stages.s6_execution import S6ExecutionController, _git

    store, _candidate, _source_blueprint, _pointer, _report, group = _two_member_pending_group_store(tmp_path, case_id)
    agent = _CurrentFilesAgent(mutate=True)
    controller = S6ExecutionController(agent, FakeExecutor())
    parent = _git(store._confined("workspace"), "rev-parse", "HEAD")

    result = controller.run(StageContext(store, "s6", store.load_run(), None))

    assert result.output_refs["s6_receipt"].path == "plan/s6_receipt.json"
    assert len(agent.calls) == 2
    assert all(role == "coder" for role in agent.roles)
    state = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    member_rows = sorted((row for row in state["tasks"] if row["group_id"] == group["group_id"]), key=lambda row: row["task_uid"])
    assert len(member_rows) == 2 and all(row["status"] == "done" for row in member_rows)
    commits = {row["commit_sha"] for row in member_rows}
    assert len(commits) == 1 and parent not in commits
    commit = commits.pop()
    evidence_refs = [row["acceptance_evidence"]["task_evidence_ref"] for row in member_rows]
    evidence = [store._read_json_artifact(ref["path"], schema_name="task-evidence.schema.json") for ref in evidence_refs]
    assert all(item["execution_kind"] == "group" and item["group_member_uids"] == group["member_task_uids"] for item in evidence)
    events = [entry["payload"] for entry in store._read_json_artifact("plan/revision_ledger.json")["entries"] if entry.get("event_type") == "verification_committed" and entry.get("payload", {}).get("kind") == "group"]
    assert len(events) == 1 and events[0]["commit_sha"] == commit
    joint = store._read_json_artifact(events[0]["joint_evidence_ref"]["path"], schema_name="joint-evidence.schema.json")
    assert joint["group_id"] == group["group_id"]
    assert [item["task_uid"] for item in joint["members"]] == group["member_task_uids"]
    assert not store._confined("plan/verification_pending.json").exists()


@pytest.mark.s6_execution
@pytest.mark.parametrize("member_index", [0, 1])
def test_migration_group_candidates_never_publish_intermediate_success(tmp_path, member_index):
    from nepa.stages.s6_execution import S6ExecutionController, _git

    store, _candidate, _source_blueprint, _pointer, _report, group = _two_member_pending_group_store(tmp_path)
    agent = _CurrentFilesAgent(mutate=True)
    target_uid = group["member_task_uids"][member_index]

    def fault_hook(point):
        if point == f"s6_group_candidate_persisted:{target_uid}":
            raise CrashInjected(point)

    head = _git(store._confined("workspace"), "rev-parse", "HEAD")
    with pytest.raises(CrashInjected):
        S6ExecutionController(agent, FakeExecutor(), fault_hook=fault_hook).run(
            StageContext(store, "s6", store.load_run(), None)
        )
    state = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    assert all(row["status"] != "done" for row in state["tasks"])
    assert _git(store._confined("workspace"), "rev-parse", "HEAD") == head
    assert not store._confined("plan/verification_pending.json").exists()
    revision = store._read_json_artifact("plan/revision_ledger.json", schema_name="revision-ledger.schema.json")
    assert not any(entry.get("event_type") == "verification_committed" for entry in revision["entries"])
    S6ExecutionController(agent, FakeExecutor()).run(StageContext(store, "s6", store.load_run(), None))
    assert len(agent.calls) == 2


@pytest.mark.s6_execution
def test_empty_group_candidates_cannot_be_accepted_with_other_member_progress(tmp_path):
    from nepa.stages.s6_execution import S6ExecutionController

    store, _candidate, _source_blueprint, _pointer, _report, group = _two_member_pending_group_store(tmp_path)
    agent = _CurrentFilesAgent(mutate=False)

    with pytest.raises(ControlledStageFailure, match="static-valid tasks remain unresolved"):
        S6ExecutionController(agent, FakeExecutor()).run(StageContext(store, "s6", store.load_run(), None))

    state = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    members = [row for row in state["tasks"] if row.get("group_id") == group["group_id"]]
    assert members and all(row["status"] == "blocked" for row in members)
    revision = store._read_json_artifact("plan/revision_ledger.json", schema_name="revision-ledger.schema.json")
    assert not any(
        entry.get("event_type") == "verification_committed" and entry.get("payload", {}).get("group_id") == group["group_id"]
        for entry in revision["entries"]
    )


@pytest.mark.s6_execution
@pytest.mark.parametrize("boundary", ["allocation-first", "allocation-second", "results"])
def test_migration_group_prepared_work_resumes_without_duplicate_agent_calls(tmp_path, boundary):
    from nepa.stages.s6_execution import S6ExecutionController

    store, candidate, _source_blueprint, _pointer, _report, _group = _two_member_pending_group_store(tmp_path)
    traversal_uids = [task["task_uid"] for task in candidate["tasks"]]
    point = {
        "allocation-first": f"s6_group_member_allocated:{traversal_uids[0]}",
        "allocation-second": f"s6_group_member_allocated:{traversal_uids[1]}",
        "results": "s6_group_results_published:1",
    }[boundary]
    agent = _CurrentFilesAgent(mutate=True)

    def fault_hook(actual):
        if actual == point:
            raise CrashInjected(actual)

    with pytest.raises(CrashInjected):
        S6ExecutionController(agent, FakeExecutor(), fault_hook=fault_hook).run(
            StageContext(store, "s6", store.load_run(), None)
        )
    calls_before = list(agent.calls)
    S6ExecutionController(agent, FakeExecutor()).run(StageContext(store, "s6", store.load_run(), None))
    assert len(agent.calls) == 2
    assert agent.calls[:len(calls_before)] == calls_before


@pytest.mark.s6_execution
def test_migration_group_retries_only_strictly_attributed_member(tmp_path):
    from nepa.stages.s6_execution import S6ExecutionController
    from nepa.tools.sandbox import ExecResult

    store, candidate, _source_blueprint, _pointer, _report, _group = _two_member_pending_group_store(tmp_path)
    target = candidate["tasks"][0]

    class FailTargetOnce(FakeExecutor):
        def __init__(self):
            self.failed = False

        def exec(self, command, cwd, timeout_s, net="none"):
            if command[:1] == ["make"] and command[1:] != ["clean"] and not self.failed:
                self.failed = True
                relative = target["deliverable_files"][0]
                return ExecResult(list(command), 1, "", f"{relative}:1: error: incompatible declaration\n", 1, False, "completed")
            return super().exec(command, cwd, timeout_s, net)

    agent = _CurrentFilesAgent(mutate=True)
    S6ExecutionController(agent, FailTargetOnce()).run(StageContext(store, "s6", store.load_run(), None))

    calls_by_task = [call[:2] for call in agent.calls]
    assert calls_by_task.count((target["id"], 1)) == 1
    assert calls_by_task.count((target["id"], 2)) == 1
    other = next(task for task in candidate["tasks"] if task["id"] != target["id"])
    assert calls_by_task.count((other["id"], 1)) == 1
    assert not any(task_id == other["id"] and attempt > 1 for task_id, attempt in calls_by_task)


@pytest.mark.s6_execution
def test_group_diagnostics_attribute_frozen_symbols_and_build_artifacts(tmp_path):
    from nepa.stages.s6_execution import S6ExecutionController, _attribute_group_members

    store, _candidate, _source_blueprint, _pointer, _report, group = _two_member_pending_group_store(tmp_path)
    controller = S6ExecutionController(_CurrentFilesAgent(), FakeExecutor())
    _run, plan, _active, blueprint, _constraints, epoch = controller._admit(store)
    state = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    descriptor = controller._group_descriptor(store, plan, blueprint, state, epoch, group["group_id"])
    contract_map = store._read_json_artifact("plan/contract_map.json", schema_name="contract-map.schema.json")
    export = contract_map["contracts"][0]["exports"][0]
    export["owner_task_id"] = descriptor["members"][0]["id"]
    descriptor["affected_symbols"] = [export["symbol"]]
    symbol_result = [{
        "status": "failed", "timed_out": False, "exit_code": 1,
        "stdout": "", "stderr": f"undefined reference to `{export['symbol']}'\n",
    }]
    assert _attribute_group_members(symbol_result, descriptor, blueprint, contract_map) == {
        next(task["task_uid"] for task in descriptor["members"] if task["id"] == export["owner_task_id"])
    }

    artifact = next(item for item in blueprint["build_artifacts"] if item["id"] in descriptor["build_artifact_ids"])
    artifact_result = [{
        "status": "failed", "timed_out": False, "exit_code": 1,
        "stdout": "", "stderr": f"link failed while producing {artifact['path']}\n",
    }]
    attributed = _attribute_group_members(artifact_result, descriptor, blueprint, contract_map)
    assert attributed
    assert attributed <= set(descriptor["member_uids"])


@pytest.mark.s6_execution
def test_migration_group_unlocatable_failure_retries_all_members(tmp_path):
    from nepa.stages.s6_execution import S6ExecutionController
    from nepa.tools.sandbox import ExecResult

    store, candidate, _source_blueprint, _pointer, _report, _group = _two_member_pending_group_store(tmp_path)

    class FailOnceWithoutLocation(FakeExecutor):
        def __init__(self):
            self.failed = False

        def exec(self, command, cwd, timeout_s, net="none"):
            if command[:1] == ["make"] and command[1:] != ["clean"] and not self.failed:
                self.failed = True
                return ExecResult(list(command), 1, "", "collect2: error: link failed\n", 1, False, "completed")
            return super().exec(command, cwd, timeout_s, net)

    agent = _CurrentFilesAgent(mutate=True)
    S6ExecutionController(agent, FailOnceWithoutLocation()).run(StageContext(store, "s6", store.load_run(), None))
    for task in candidate["tasks"]:
        assert (task["id"], 1) in [call[:2] for call in agent.calls]
        assert (task["id"], 2) in [call[:2] for call in agent.calls]


@pytest.mark.s6_execution
def test_migration_group_exhaustion_blocks_every_member_without_commit(tmp_path):
    from nepa.stages.s6_execution import S6ExecutionController, _git
    from nepa.tools.sandbox import ExecResult

    store, candidate, _source_blueprint, _pointer, _report, _group = _two_member_pending_group_store(tmp_path)
    target = candidate["tasks"][0]

    class AlwaysFailTarget(FakeExecutor):
        def exec(self, command, cwd, timeout_s, net="none"):
            if command[:1] == ["make"] and command[1:] != ["clean"]:
                relative = target["deliverable_files"][0]
                return ExecResult(list(command), 1, "", f"{relative}:1: error: incompatible declaration\n", 1, False, "completed")
            return super().exec(command, cwd, timeout_s, net)

    baseline = _git(store._confined("workspace"), "rev-parse", "HEAD")
    agent = _CurrentFilesAgent(mutate=True)
    with pytest.raises(ControlledStageFailure, match="static-valid tasks remain unresolved"):
        S6ExecutionController(agent, AlwaysFailTarget()).run(StageContext(store, "s6", store.load_run(), None))

    state = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    assert all(row["status"] == "blocked" and row["last_error"].endswith("/failure.json") for row in state["tasks"])
    assert _git(store._confined("workspace"), "rev-parse", "HEAD") == baseline
    assert not store._confined("plan/verification_pending.json").exists()
    assert not any(entry.get("event_type") == "verification_committed" for entry in store._read_json_artifact("plan/revision_ledger.json")["entries"])
    target_calls = [attempt for task_id, attempt, _tier in agent.calls if task_id == target["id"]]
    assert target_calls == [1, 2, 3, 4]


def test_group_exhaustion_propagates_dependencies_but_leaves_independent_branch_selectable(tmp_path):
    from nepa.speclib.lint import canonical_json_bytes
    from nepa.speclib.plan_state import initialize_plan_state, project_state_transition
    from nepa.stages.s6_execution import S6ExecutionController

    store, _config = _ready_store(tmp_path)
    pointer = store._read_json_artifact("plan/active_plan.json")
    plan = store._read_json_artifact(pointer["path"])
    dependent = copy.deepcopy(plan["tasks"][1])
    dependent.update({"id": "T-002", "depends_on": ["T-001"]})
    group_peer = copy.deepcopy(plan["tasks"][0])
    group_peer.update({"id": "T-003", "task_uid": "3" * 16, "depends_on": []})
    independent = copy.deepcopy(plan["tasks"][1])
    independent.update({"id": "T-004", "task_uid": "4" * 16, "depends_on": []})
    plan["tasks"] = [plan["tasks"][0], dependent, group_peer, independent]
    pointer["sha256"] = hashlib.sha256(canonical_json_bytes(plan)).hexdigest()
    state = initialize_plan_state(plan, plan_ref=pointer)
    migration_ref = {"revision_seq": 1, "event_seq": 2}
    for row in (state["tasks"][0], state["tasks"][2]):
        row.update({"group_id": "g-1-1", "migration_ref": migration_ref})
    members = sorted((state["tasks"][0], state["tasks"][2]), key=lambda row: row["task_uid"])
    event = {
        "schema_version": "2.0", "event": "group_exhausted", "task_id": members[0]["id"],
        "group_id": "g-1-1", "member_task_ids": [row["id"] for row in members],
        "error": "groups/g-1-1/failure.json",
        "proof": {"group_id": "g-1-1", "activation_ref": migration_ref, "member_uids": [row["task_uid"] for row in members]},
    }
    exhausted = project_state_transition(state, event, plan=plan, config_snapshot=store.load_run()["config_snapshot"])
    store.replace_json("plan/plan_state.json", exhausted, schema_name="plan-state.schema.json")
    controller = S6ExecutionController(_CurrentFilesAgent(), FakeExecutor())
    controller._propagate_dependency_blocks(store, plan)
    projected = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    assert next(row for row in projected["tasks"] if row["id"] == "T-002")["status"] == "blocked_by_dependency"
    assert next(row for row in projected["tasks"] if row["id"] == "T-004")["status"] == "pending"
    selected = controller._choose(plan, projected)
    assert selected is not None and selected["id"] == "T-004"


@pytest.mark.s6_execution
@pytest.mark.parametrize(
    "fault_point",
    [
        "s6_group_wal_prepared",
        "s6_group_joint_evidence_published",
        "s6_group_candidate_installed",
        "s6_group_commit_created",
        "s6_group_state_published",
        "s6_group_file_ledger_published",
        "s6_group_event_published",
        "s6_group_terminals_published",
        "s6_wal_removed",
    ],
)
def test_migration_group_recovery_after_prepared_transaction_never_replays_external_work(tmp_path, fault_point):
    from nepa.stages.s6_execution import S6ExecutionController

    store, _candidate, _source_blueprint, _pointer, _report, _group = _two_member_pending_group_store(tmp_path)
    agent = _CurrentFilesAgent(mutate=True)

    def fault_hook(point):
        if point == fault_point:
            raise CrashInjected(point)

    with pytest.raises(CrashInjected):
        S6ExecutionController(agent, FakeExecutor(), fault_hook=fault_hook).run(
            StageContext(store, "s6", store.load_run(), None)
        )
    assert len(agent.calls) == 2

    result = S6ExecutionController(agent, FakeExecutor()).run(StageContext(store, "s6", store.load_run(), None))
    assert result.output_refs["s6_receipt"].path == "plan/s6_receipt.json"
    assert len(agent.calls) == 2
    assert not store._confined("plan/verification_pending.json").exists()
    state = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    assert all(row["status"] == "done" for row in state["tasks"])


@pytest.mark.s6_execution
@pytest.mark.parametrize(
    "damage",
    ["group_id", "activation_ref", "epoch_anchor", "transaction_baseline", "member_mode", "candidate", "build_result", "expected_trailer"],
)
def test_migration_group_precommit_corruption_fail_stops(tmp_path, damage):
    from nepa.stages.s6_execution import S6ExecutionController, S6ExecutionError

    store, _candidate, _source_blueprint, _pointer, _report, _group = _two_member_pending_group_store(tmp_path)

    def fault_hook(point):
        if point == "s6_group_wal_prepared":
            raise CrashInjected(point)

    with pytest.raises(CrashInjected):
        S6ExecutionController(_CurrentFilesAgent(mutate=True), FakeExecutor(), fault_hook=fault_hook).run(
            StageContext(store, "s6", store.load_run(), None)
        )
    wal = store._read_json_artifact("plan/verification_pending.json", schema_name="verification-pending.schema.json")
    if damage == "group_id":
        wal["group_id"] = "g-1-2"
        store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
    elif damage == "activation_ref":
        wal["activation_ref"]["event_seq"] += 1
        store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
    elif damage == "member_mode":
        wal["member_modes"][0]["execution_mode"] = "amend"
        store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
    elif damage == "epoch_anchor":
        wal["epoch_checkpoint_commit"] = "0" * 40
        store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
    elif damage == "transaction_baseline":
        wal["baseline_commit"] = "0" * 40
        store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
    elif damage == "expected_trailer":
        wal["expected_trailers"]["NePA-Verification-ID"] = "v-0000000000000000-1"
        store.replace_json("plan/verification_pending.json", wal, schema_name="verification-pending.schema.json")
    elif damage == "candidate":
        ref = next(iter(wal["candidate_file_refs"].values()))
        store._confined(ref["path"]).write_bytes(b"damaged")
    else:
        ref = wal["build_result_refs"][0]
        store._confined(ref["path"]).write_bytes(b"{}")
    with pytest.raises((S6ExecutionError, RunStoreError)):
        S6ExecutionController(_CurrentFilesAgent(mutate=True), FakeExecutor()).reconcile_verification_wal(store)


@pytest.mark.s6_execution
def test_migration_group_postcommit_partial_projection_corruption_fail_stops(tmp_path):
    from nepa.stages.s6_execution import S6ExecutionController, S6ExecutionError

    store, _candidate, _source_blueprint, _pointer, _report, _group = _two_member_pending_group_store(tmp_path)

    def fault_hook(point):
        if point == "s6_group_state_published":
            raise CrashInjected(point)

    with pytest.raises(CrashInjected):
        S6ExecutionController(_CurrentFilesAgent(mutate=True), FakeExecutor(), fault_hook=fault_hook).run(
            StageContext(store, "s6", store.load_run(), None)
        )
    file_ledger = store._read_json_artifact("plan/file_ledger.json", schema_name="file-ledger.schema.json")
    row = next(item for item in file_ledger["files"] if item["state"] == "realized")
    row["content_sha256"] = "0" * 64
    store.replace_json("plan/file_ledger.json", file_ledger, schema_name="file-ledger.schema.json")
    with pytest.raises((S6ExecutionError, RunStoreError)):
        S6ExecutionController(_CurrentFilesAgent(mutate=True), FakeExecutor()).reconcile_verification_wal(store)


def _completed_e0_then_f2_store(tmp_path, *, migration_mode="revalidate", case_id=None, renumber_tasks=False):
    from nepa.speclib.delivery import compile_delivery_blueprint, compile_delivery_constraints
    from nepa.speclib.lint import canonical_json_bytes
    from nepa.speclib.materialization import derive_rendering_view
    from nepa.speclib.plan import blueprint_task_semantic_projection
    from test_s5_multi_epoch import _activate_candidate
    from nepa.stages.s6_execution import S6ExecutionController

    store, config = _ready_store(tmp_path, case_id)
    if migration_mode == "amend":
        config = load_config(overrides={"run": {"until": "s6"}, "budgets": {"s6_total_attempts_cap": 16}})
        run = store.load_run()
        run["config_snapshot"] = config.snapshot
        run["config_snapshot_sha256"] = config.snapshot_sha256
        run["stages"]["s4"]["output_refs"]["config_snapshot_sha256"] = config.snapshot_sha256
        store.replace_run(run)
    initial_agent = _CurrentFilesAgent(failures=3 if migration_mode == "amend" else 0, mutate=True)
    initial = S6ExecutionController(initial_agent, FakeExecutor())
    initial.run(StageContext(store, "s6", store.load_run(), None))
    old_pointer = store._read_json_artifact("plan/active_plan.json", schema_name="active-plan.schema.json")
    old_plan = store._read_json_artifact(old_pointer["path"], schema_name="plan.schema.json")
    candidate = copy.deepcopy(old_plan)
    if renumber_tasks:
        def renumber(value):
            if isinstance(value, dict):
                return {key: renumber(item) for key, item in value.items()}
            if isinstance(value, list):
                return [renumber(item) for item in value]
            return "T-101" if value == "T-001" else value

        candidate = renumber(candidate)
    old_uid = candidate["tasks"][0]["task_uid"]
    if migration_mode == "revalidate":
        candidate["tasks"][0]["task_uid"] = "d" * 16
        lineage = {"task_mappings": [{"old_task_uid": old_uid, "new_task_uid": "d" * 16}]}
    elif migration_mode == "amend":
        candidate["tasks"][0]["obligation_digest"] = "e" * 64
        lineage = None
    elif migration_mode == "regenerate":
        candidate["tasks"][0]["task_uid"] = "f" * 16
        lineage = None
    else:
        raise ValueError(f"unsupported singleton migration fixture: {migration_mode}")
    if renumber_tasks:
        spec = store._read_json_artifact("spec/spec.json")
        target = store._read_json_artifact("inputs/target.json")
        constraints = compile_delivery_constraints(spec, target)
        rebound_blueprint = compile_delivery_blueprint(
            constraints,
            candidate["architecture"],
            candidate["work_packages"],
            blueprint_task_semantic_projection(candidate["tasks"]),
        )
        candidate["delivery_blueprint_sha256"] = hashlib.sha256(canonical_json_bytes(rebound_blueprint)).hexdigest()
    pointer, report = _activate_candidate(store, candidate, "F2", lineage=lineage)
    spec = store._read_json_artifact("spec/spec.json")
    target = store._read_json_artifact("inputs/target.json")
    constraints = compile_delivery_constraints(spec, target)
    blueprint = compile_delivery_blueprint(
        constraints,
        candidate["architecture"],
        candidate["work_packages"],
        blueprint_task_semantic_projection(candidate["tasks"]),
    )
    view = derive_rendering_view(candidate, spec, target, blueprint, constraints)
    return store, config, candidate, pointer, report, blueprint, constraints


@pytest.mark.s6_execution
def test_f2_admission_accepts_reused_epoch_after_real_task_and_provider_rebinding(tmp_path):
    from nepa.stages.s6_execution import S6ExecutionController

    store, _config, candidate, _pointer, _report, blueprint, _constraints = _completed_e0_then_f2_store(
        tmp_path, migration_mode="revalidate", renumber_tasks=True
    )
    epoch = store._read_json_artifact("plan/epochs/E0/receipt.json", schema_name="epoch-receipt.schema.json")
    assert epoch["blueprint_sha256"] != hashlib.sha256(
        json.dumps(blueprint, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    _run, admitted, _active, admitted_blueprint, _constraints, admitted_epoch = S6ExecutionController(
        _CurrentFilesAgent(), FakeExecutor()
    )._admit(store)

    assert admitted == candidate
    assert admitted_blueprint == blueprint
    assert admitted_epoch == epoch


def _two_member_pending_group_store(tmp_path, case_id=None):
    from nepa.speclib.delivery import compile_delivery_blueprint, compile_delivery_constraints
    from nepa.speclib.plan import blueprint_task_semantic_projection
    from nepa.speclib.plan_state import initialize_plan_state
    from nepa.stages.s5_materialization import S5MaterializationController
    from test_s5_multi_epoch import _CompilerFailureExecutor, _accepted_e0_store, _activate_candidate, _finish_s5

    store, _completion, _s5 = _accepted_e0_store(tmp_path, case_id)
    pointer = store._read_json_artifact("plan/active_plan.json", schema_name="active-plan.schema.json")
    plan = store._read_json_artifact(pointer["path"], schema_name="plan.schema.json")
    store.replace_json("plan/plan_state.json", initialize_plan_state(plan, plan_ref=pointer), schema_name="plan-state.schema.json")
    candidate = copy.deepcopy(plan)
    constraints = compile_delivery_constraints(store._read_json_artifact("spec/spec.json"), store._read_json_artifact("inputs/target.json"))
    blueprint = compile_delivery_blueprint(constraints, candidate["architecture"], candidate["work_packages"], blueprint_task_semantic_projection(candidate["tasks"]))
    member_uids = sorted((task["task_uid"] for task in candidate["tasks"]), key=lambda value: value.encode("utf-8"))
    affected_paths = sorted((path for task in candidate["tasks"] for path in task["deliverable_files"]), key=lambda value: value.encode("utf-8"))
    group = {
        "group_id": "g-1-1", "member_task_uids": member_uids,
        "affected_paths": affected_paths, "affected_symbols": [],
        "build_artifact_ids": [blueprint["build_artifacts"][0]["id"]],
    }
    new_pointer, report = _activate_candidate(store, candidate, "F3", migration_extensions={"pending_groups": [group]})
    executor = _CompilerFailureExecutor(affected_paths[0])
    s5 = S5MaterializationController(executor)
    result = s5.run(StageContext(store, "s5", store.load_run(), None))
    _finish_s5(store, s5, result)
    return store, candidate, blueprint, new_pointer, report, group


@pytest.mark.s6_execution
def test_later_group_uses_current_accepted_head_as_transaction_baseline(tmp_path):
    from nepa.stages.s6_execution import S6ExecutionController, _commit_descends, _git

    store, _candidate, _source_blueprint, _pointer, _report, group = _two_member_pending_group_store(tmp_path)
    epoch = store._read_json_artifact("plan/epochs/E1/receipt.json", schema_name="epoch-receipt.schema.json")
    workspace = store._confined("workspace")
    subprocess.run(["git", "-C", str(workspace), "commit", "--allow-empty", "-m", "accepted prior group"], check=True, capture_output=True)
    accepted_head = _git(workspace, "rev-parse", "HEAD")
    controller = S6ExecutionController(_CurrentFilesAgent(), FakeExecutor())
    _run, plan, _active, blueprint, _constraints, admitted_epoch = controller._admit(store)
    state = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    descriptor = controller._group_descriptor(store, plan, blueprint, state, admitted_epoch, group["group_id"])

    assert descriptor["baseline_commit"] == accepted_head
    assert descriptor["epoch_checkpoint_commit"] == epoch["checkpoint_commit"]
    assert _commit_descends(workspace, descriptor["baseline_commit"], descriptor["epoch_checkpoint_commit"])


@pytest.mark.s6_execution
@pytest.mark.parametrize("case_id", [None, "mqtt", "non_mqtt"])
def test_singleton_revalidate_publishes_same_tree_without_agent_call(tmp_path, case_id):
    from nepa.stages.s6_execution import S6ExecutionController, _git

    store, _config, _plan, _pointer, _report, _blueprint, _constraints = _completed_e0_then_f2_store(tmp_path, case_id=case_id)
    agent = _CurrentFilesAgent(mutate=True)
    controller = S6ExecutionController(agent, FakeExecutor())
    run, plan, _active, blueprint, constraints, _epoch = controller._admit(store)
    state = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    task = next(task for task in plan["tasks"] if next(row for row in state["tasks"] if row["id"] == task["id"])["execution_mode"] == "revalidate")
    parent = _git(store._confined("workspace"), "rev-parse", "HEAD")
    parent_tree = _git(store._confined("workspace"), "rev-parse", "HEAD^{tree}")

    controller._run_task(StageContext(store, "s6", run, None), plan, blueprint, constraints, task)

    assert agent.calls == []
    commit = _git(store._confined("workspace"), "rev-parse", "HEAD")
    assert commit != parent
    assert _git(store._confined("workspace"), "rev-parse", "HEAD^{tree}") == parent_tree
    row = next(row for row in store._read_json_artifact("plan/plan_state.json")["tasks"] if row["id"] == task["id"])
    assert row["status"] == "done"
    evidence = store._read_json_artifact(row["acceptance_evidence"]["task_evidence_ref"]["path"], schema_name="task-evidence.schema.json")
    assert evidence["execution_kind"] == "revalidate"
    assert evidence["attempt"] == 0
    assert evidence["changed_files"] == []
    validation = store._read_json_artifact(f"validations/{task['task_uid']}/validation_{evidence['evidence_seq']:03d}.json", schema_name="s6-validation.schema.json")
    assert validation["status"] == "succeeded"


@pytest.mark.s6_execution
@pytest.mark.parametrize("case_id", [None, "mqtt", "non_mqtt"])
def test_singleton_amend_preserves_four_attempts_and_uses_one_t1_fixer(tmp_path, case_id):
    from nepa.stages.s6_execution import S6ExecutionController

    store, _config, _plan, _pointer, report, _blueprint, _constraints = _completed_e0_then_f2_store(
        tmp_path, migration_mode="amend", case_id=case_id
    )
    assert next(item for item in report["tasks"] if item["new_task_id"] == "T-001")["classification"] == "AMEND"
    before = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    old_usage = before["s6_attempts_used"]
    agent = _CurrentFilesAgent(mutate=True)

    S6ExecutionController(agent, FakeExecutor()).run(StageContext(store, "s6", store.load_run(), None))

    assert agent.calls == [("T-001", 4, "T1")]
    assert agent.roles == ["fixer"]
    assert agent.input_snapshots[0]["execution_mode"] == "amend"
    state = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    row = next(item for item in state["tasks"] if item["id"] == "T-001")
    assert row["status"] == "done"
    assert row["attempts"] == 4
    assert row["amendment_used"] == 1
    assert state["s6_attempts_used"] == old_usage + 1
    evidence = store._read_json_artifact(row["acceptance_evidence"]["task_evidence_ref"]["path"], schema_name="task-evidence.schema.json")
    assert evidence["execution_kind"] == "amend"
    assert evidence["attempt"] == 4
    amendment = store._read_json_artifact(f"attempts/{row['task_uid']}/amendment.json", schema_name="s6-attempt.schema.json")
    assert amendment["status"] == "succeeded"


@pytest.mark.s6_execution
def test_singleton_amendment_allocation_is_spent_before_provider_io(tmp_path):
    from nepa.run_store import RunValidationError
    from nepa.stages.s6_execution import S6ExecutionController, _git
    from nepa.tools.build import _tree_sha256

    store, _config, plan, _pointer, _report, blueprint, constraints = _completed_e0_then_f2_store(
        tmp_path, migration_mode="amend"
    )
    agent = _CurrentFilesAgent(mutate=True)

    def fault_hook(point):
        if point == "s6_amendment_allocated":
            raise CrashInjected(point)

    controller = S6ExecutionController(agent, FakeExecutor(), fault_hook=fault_hook)
    run, admitted_plan, _active, admitted_blueprint, admitted_constraints, _epoch = controller._admit(store)
    task = next(item for item in admitted_plan["tasks"] if item["id"] == "T-001")
    before = store._read_json_artifact("plan/plan_state.json")
    with pytest.raises(CrashInjected):
        controller._run_task(StageContext(store, "s6", run, None), admitted_plan, admitted_blueprint, admitted_constraints, task)
    spent = store._read_json_artifact("plan/plan_state.json")
    spent_row = next(item for item in spent["tasks"] if item["id"] == task["id"])
    assert agent.calls == []
    assert spent_row["attempts"] == 4
    assert spent_row["amendment_used"] == 1
    assert spent["s6_attempts_used"] == before["s6_attempts_used"] + 1

    resumed = S6ExecutionController(agent, FakeExecutor())
    resumed._run_task(StageContext(store, "s6", store.load_run(), None), plan, blueprint, constraints, task)
    assert agent.calls == [("T-001", 4, "T1")]
    with pytest.raises(RunValidationError):
        store.allocate_s6_migration(
            task_id=task["id"], task_uid=task["task_uid"], mode="amend",
            baseline_commit=_git(store._confined("workspace"), "rev-parse", "HEAD"),
            baseline_tree=_tree_sha256(store._confined("workspace")),
        )


@pytest.mark.s6_execution
def test_singleton_amendment_precommit_resume_does_not_repeat_fixer(tmp_path):
    from nepa.stages.s6_execution import S6ExecutionController

    store, _config, _plan, _pointer, _report, _blueprint, _constraints = _completed_e0_then_f2_store(
        tmp_path, migration_mode="amend"
    )
    agent = _CurrentFilesAgent(mutate=True)

    def fault_hook(point):
        if point == "s6_wal_prepared":
            raise CrashInjected(point)

    with pytest.raises(CrashInjected):
        S6ExecutionController(agent, FakeExecutor(), fault_hook=fault_hook).run(
            StageContext(store, "s6", store.load_run(), None)
        )
    assert agent.calls == [("T-001", 4, "T1")]

    S6ExecutionController(agent, FakeExecutor()).run(StageContext(store, "s6", store.load_run(), None))
    assert agent.calls == [("T-001", 4, "T1")]
    state = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    row = next(item for item in state["tasks"] if item["id"] == "T-001")
    assert row["status"] == "done" and row["attempts"] == 4 and row["amendment_used"] == 1


@pytest.mark.s6_execution
def test_singleton_amendment_failure_consumes_its_only_allowance(tmp_path):
    from nepa.stages.s6_execution import S6ExecutionController

    store, _config, _plan, _pointer, _report, _blueprint, _constraints = _completed_e0_then_f2_store(
        tmp_path, migration_mode="amend"
    )
    agent = _CurrentFilesAgent(mutate=False)
    with pytest.raises(ControlledStageFailure, match="static-valid tasks remain unresolved"):
        S6ExecutionController(agent, FakeExecutor()).run(StageContext(store, "s6", store.load_run(), None))
    assert agent.calls == [("T-001", 4, "T1")]
    state = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    row = next(item for item in state["tasks"] if item["id"] == "T-001")
    assert row["status"] == "blocked" and row["attempts"] == 4 and row["amendment_used"] == 1
    amendment = store._read_json_artifact(f"attempts/{row['task_uid']}/amendment.json", schema_name="s6-attempt.schema.json")
    assert amendment["status"] == "failed" and amendment["failure_ref"]["path"] == row["last_error"]


@pytest.mark.s6_execution
@pytest.mark.parametrize("case_id", [None, "mqtt", "non_mqtt"])
def test_singleton_regenerate_starts_new_normal_generation_with_coder_t2(tmp_path, case_id):
    from nepa.stages.s6_execution import S6ExecutionController

    store, _config, _plan, _pointer, report, _blueprint, _constraints = _completed_e0_then_f2_store(
        tmp_path, migration_mode="regenerate", case_id=case_id
    )
    regenerated = [item for item in report["tasks"] if item.get("new_task_id") == "T-001"]
    assert len(regenerated) == 1 and regenerated[0]["classification"] == "REGENERATE"
    before = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    row_before = next(item for item in before["tasks"] if item["id"] == "T-001")
    assert row_before["status"] == "pending" and row_before["execution_mode"] == "normal" and row_before["attempts"] == 0
    assert row_before["migration_ref"] is not None
    agent = _CurrentFilesAgent(mutate=True)

    S6ExecutionController(agent, FakeExecutor()).run(StageContext(store, "s6", store.load_run(), None))

    assert agent.calls == [("T-001", 1, "T2")]
    assert agent.roles == ["coder"]
    state = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    row = next(item for item in state["tasks"] if item["id"] == "T-001")
    assert row["status"] == "done" and row["attempts"] == 1 and row["migration_ref"] == row_before["migration_ref"]
    evidence = store._read_json_artifact(row["acceptance_evidence"]["task_evidence_ref"]["path"], schema_name="task-evidence.schema.json")
    assert evidence["execution_kind"] == "normal"
    assert evidence["attempt"] == 1
    assert evidence["migration_ref"] == row_before["migration_ref"]
    assert not store._confined(f"attempts/{row['task_uid']}/attempt_002.json").exists()


@pytest.mark.s6_execution
@pytest.mark.parametrize(
    "fault_point",
    [
        "s6_validation_allocated",
        "s6_wal_prepared",
        "s6_evidence_published",
        "s6_commit_created",
        "s6_state_projection_published",
        "s6_file_ledger_published",
        "s6_event_published",
        "s6_attempt_terminal_published",
        "s6_wal_removed",
    ],
)
def test_singleton_revalidate_reconciles_every_publication_boundary(tmp_path, fault_point):
    from nepa.stages.s6_execution import S6ExecutionController, _git

    store, _config, _plan, _pointer, _report, _blueprint, _constraints = _completed_e0_then_f2_store(tmp_path)
    trace = store._confined("trace/llm_calls.ndjson")
    trace_before = trace.read_bytes() if trace.exists() else b""
    parent = _git(store._confined("workspace"), "rev-parse", "HEAD")
    parent_tree = _git(store._confined("workspace"), "rev-parse", "HEAD^{tree}")
    agent = _CurrentFilesAgent(mutate=True)

    def fault_hook(point):
        if point == fault_point:
            raise CrashInjected(point)

    with pytest.raises(CrashInjected):
        S6ExecutionController(agent, FakeExecutor(), fault_hook=fault_hook).run(
            StageContext(store, "s6", store.load_run(), None)
        )

    resumed = S6ExecutionController(agent, FakeExecutor()).run(
        StageContext(store, "s6", store.load_run(), None)
    )
    assert resumed.output_refs["s6_receipt"].path == "plan/s6_receipt.json"
    assert agent.calls == []
    assert (trace.read_bytes() if trace.exists() else b"") == trace_before
    assert not store._confined("plan/verification_pending.json").exists()
    assert _git(store._confined("workspace"), "rev-parse", "HEAD") != parent
    assert _git(store._confined("workspace"), "rev-parse", "HEAD^{tree}") == parent_tree
    state = store._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
    assert all(row["status"] == "done" for row in state["tasks"])
    old_receipts = list(store._confined("plan/s6_receipts").glob("*.json"))
    assert len(old_receipts) == 1


@pytest.mark.parametrize(
    "bound_field",
    [
        "active_plan_ref", "binding_ref", "plan_state_ref", "state_history_ref", "file_ledger_ref", "revision_ledger_ref",
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
