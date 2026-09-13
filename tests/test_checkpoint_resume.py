from pathlib import Path
import json
import pytest
from nepa.config import load_config
from nepa.run_store import RunStore, RunStoreError, BudgetExhausted

ROOT = Path(__file__).parents[1]

@pytest.fixture
def store(tmp_path):
    return RunStore.initialize(tmp_path / "runs", ROOT / "gold_file/mqtt/specIR.json",
                               ROOT / "gold_file/mqtt/target.json", ROOT / "gold_file/mqtt/acceptance.json", load_config())

def test_input_snapshot_and_new_plan(store):
    spec, target, acceptance = store.inputs()
    assert len(spec["requirements"]) == 110
    assert len(store.plan()["tasks"]) == 23
    assert not list(store.project.iterdir())
    assert store.run["accepted_checkpoint"]
    assert len(store.run["runtime"]["package_sha256"]) == 64

def test_lock_excludes_second_controller(store):
    with store.lock():
        with pytest.raises(RunStoreError):
            with store.lock():
                pass

def test_input_drift_fails(store):
    (store.root / "inputs/spec.json").write_text("{}")
    with pytest.raises(RunStoreError):
        store.inputs()

def test_cost_reserved_then_settled_and_failed_call_not_free(store):
    seq = store.reserve_call("bootstrap", .05, {"messages": []})
    assert store.run["budget"]["cost_cny"] == .05
    store.settle_call(seq, {"cost_cny": .01, "tokens_in": 3, "tokens_out": 4}, elapsed_s=0)
    assert store.run["budget"]["cost_cny"] == pytest.approx(.01)
    failed = store.reserve_call("bootstrap", .02, {})
    store.fail_call(failed, RuntimeError("no response"), elapsed_s=0)
    assert store.run["budget"]["cost_cny"] == pytest.approx(.03)
    assert str(failed) in store.run["pending_calls"]
    again = RunStore(store.root)
    assert again.reserve_call("bootstrap", .01, {}) > failed

def test_campaign_counts_other_runs(store):
    with pytest.raises(BudgetExhausted):
        store.reserve_call("bootstrap", 101, {})
    other = store.root.parent / "other"
    other.mkdir()
    (other / "run.json").write_text(json.dumps({"schema_version": "7.0", "phase_cost_cny": {"generation": 0}, "budget": {"cost_cny": store.config.budgets.campaign_max_cost_cny - .01}}))
    with pytest.raises(BudgetExhausted):
        store.reserve_call("bootstrap", .02, {})

def test_orphan_request_number_not_reused(store):
    store.evidence("calls/000001.request.json", {})
    assert store.reserve_call("bootstrap", .01, {}) == 2

def test_checkpoint_recovery_preserves_incomplete_tree(store):
    (store.project / "a.c").write_text("accepted")
    ref = store.evidence("test.json", {"passed": True})
    store.accept("bootstrap", [], ref)
    store.run["current_task"] = "shared-wire"
    store.run["pending_action"] = {"tool": "write_file"}
    (store.project / "a.c").write_text("incomplete")
    store.save()
    store.recover()
    assert (store.project / "a.c").read_text() == "accepted"
    archived = store.root / store.run["history"][-1]["path"]
    assert (archived / "a.c").read_text() == "incomplete"
    assert store.run["tasks"]["bootstrap"]["status"] == "passed"

def test_unexpected_manual_change_is_not_overwritten(store):
    (store.project / "user.c").write_text("user")
    with pytest.raises(RunStoreError):
        store.recover()
    assert (store.project / "user.c").read_text() == "user"

def test_old_run_rejected(tmp_path):
    (tmp_path / "run.json").write_text('{"schema_version":"4.0"}')
    with pytest.raises(RunStoreError, match="legacy"):
        RunStore(tmp_path)

def test_existing_immutable_evidence_cannot_be_overwritten(store):
    store.evidence("immutable.json", {"a": 1})
    with pytest.raises(FileExistsError):
        store.evidence("immutable.json", {"a": 2})

def test_secrets_redacted_from_evidence(store, monkeypatch):
    monkeypatch.setenv("NEPA_DS_API_KEY", "actual-test-secret")
    ref = store.evidence("secret.json", {"message": "actual-test-secret"})
    assert store.read_ref(ref)["message"] == "[REDACTED]"

def test_invalid_run_id_rejected(store):
    with pytest.raises(ValueError):
        RunStore.open(store.root.parent, "../escape")

def test_crash_after_response_keeps_reservation_and_never_reuses_call(store, monkeypatch):
    seq = store.reserve_call("bootstrap", .05, {})
    def crash():
        raise RuntimeError("publication interrupted")
    monkeypatch.setattr(store, "save", crash)
    with pytest.raises(RuntimeError):
        store.settle_call(seq, {"cost_cny": .01, "tokens_in": 1, "tokens_out": 1}, elapsed_s=0)
    reopened = RunStore(store.root)
    assert reopened.run["budget"]["cost_cny"] == .05
    assert (store.root / "evidence/calls/000001.response.json").exists()
    assert reopened.reserve_call("bootstrap", .01, {}) == 2

def test_checkpoint_before_state_publication_is_not_accepted(store, monkeypatch):
    old_checkpoint = store.run["accepted_checkpoint"]
    store.run["current_task"] = "bootstrap"
    identifier = store.start_action("bootstrap", {"tool": "write_file"})
    (store.project / "a.c").write_text("attempt")
    ref = store.finish_action(identifier, {"passed": True})
    def crash():
        raise RuntimeError("publication interrupted")
    monkeypatch.setattr(store, "save", crash)
    with pytest.raises(RuntimeError):
        store.accept("bootstrap", [], ref)
    reopened = RunStore(store.root)
    assert reopened.run["accepted_checkpoint"] == old_checkpoint
    reopened.recover()
    assert not (reopened.project / "a.c").exists()
    assert (reopened.root / reopened.run["history"][-1]["path"] / "a.c").read_text() == "attempt"

def test_followup_is_versioned_bounded_and_does_not_claim_primary_requirements(store):
    old = (store.root / "plans/0001.json").read_bytes()
    ref = store.publish_agent_evidence("diagnostic.json", {"error": "test-only integration gap"})
    request = {"issue": "repair shared call site", "requirement_ids": [], "diagnostic_refs": [ref]}
    for _ in range(3):
        store.append_followup(request, "shared-wire")
    assert (store.root / "plans/0001.json").read_bytes() == old
    assert store.plan()["revision"] == 4
    assert len(store.plan()["primary_tasks"]) == 110
    assert store.plan()["tasks"][-2]["id"] == "followup:003"
    with pytest.raises(ValueError, match="limit"):
        store.append_followup(request, "shared-wire")

def test_wall_deadline_interrupts_an_inflight_operation(store):
    import time
    store.run["created_at"] = time.time() - store.config.budgets.wall_clock_hours * 3600 + .05
    started = time.monotonic()
    with pytest.raises(BudgetExhausted, match="external operation"):
        with store.deadline():
            time.sleep(2)
    assert time.monotonic() - started < 1

def test_explicit_reconfiguration_preserves_history_budget_and_checkpoints(store):
    from copy import deepcopy
    store.reserve_call("bootstrap", .05, {})
    store.run["tasks"]["bootstrap"]["sessions"] = 1
    store.run["tasks"]["bootstrap"]["decisions"] = 34
    store.run["runtime"]["package_sha256"] = "0" * 64
    store.save()
    previous = deepcopy(store.run)
    (store.root / "report.json").write_text('{"status":"failed"}')
    config = load_config(ROOT / "configs/default.yaml")
    with pytest.raises(RunStoreError, match="accept-runtime-change"):
        store.reconfigure(config, reason="authorized experimental continuation")
    with store.lock():
        store.reconfigure(config, reason="authorized experimental continuation", allow_runtime_change=True)
    reopened = RunStore(store.root)
    for key in ("created_at", "budget", "pending_calls", "tasks", "accepted_checkpoint", "working_hashes", "inputs", "active_plan"):
        assert reopened.run[key] == previous[key]
    change = reopened.read_ref(reopened.run["history"][-1]["evidence"])
    assert reopened.read_ref(change["previous_state"]) == previous
    assert reopened.read_ref(change["previous_report"]) == {"status": "failed"}
    assert not change["budget_and_time_reset"]
    assert reopened.config.coder.fast_model == "deepseek-flash"
    reopened.recover()
    assert reopened.run["budget"] == previous["budget"]

def test_configuration_migration_refuses_manual_drift_or_completed_delivery(store):
    config = load_config()
    (store.project / "manual.c").write_text("human work")
    with pytest.raises(RunStoreError, match="unrecorded"):
        store.reconfigure(config, reason="test")
    assert (store.project / "manual.c").read_text() == "human work"
    store.run["status"] = "success"
    with pytest.raises(RunStoreError, match="completed"):
        store.reconfigure(config, reason="test")


def test_study_completion_is_not_generation_success_or_resumable(store):
    config = store.config.model_copy(update={"campaign": store.config.campaign.model_copy(update={"phase": "capability"})})
    store.reconfigure(config, reason="offline capability fixture")
    store.run.update(status="study_complete", exit_code=0)
    store.save()
    reopened = RunStore(store.root)
    assert reopened.run["status"] != "success"
    assert all(t["status"] == "pending" for t in reopened.run["tasks"].values())
    with pytest.raises(RunStoreError, match="completed study"):
        reopened.recover()
    with pytest.raises(RunStoreError, match="completed"):
        reopened.reconfigure(store.config, reason="must start a fresh generation")
