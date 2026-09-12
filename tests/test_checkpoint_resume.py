from pathlib import Path
import json
import pytest
from nepa.config import load_config
from nepa.run_store import RunStore, RunStoreError, BudgetExhausted, tree_hashes, file_lock

ROOT = Path(__file__).parents[1]

@pytest.fixture
def store(tmp_path):
    return RunStore.initialize(tmp_path / "runs", ROOT / "gold_file/specIR.json",
                               ROOT / "gold_file/target.json", ROOT / "gold_file/acceptance.json", load_config())

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
    assert store.run["budget"]["cost_usd"] == .05
    store.settle_call(seq, {"cost_usd": .01, "tokens_in": 3, "tokens_out": 4}, elapsed_s=0)
    assert store.run["budget"]["cost_usd"] == pytest.approx(.01)
    failed = store.reserve_call("bootstrap", .02, {})
    store.fail_call(failed, RuntimeError("no response"), elapsed_s=0)
    assert store.run["budget"]["cost_usd"] == pytest.approx(.03)
    assert str(failed) in store.run["pending_calls"]
    again = RunStore(store.root)
    assert again.reserve_call("bootstrap", .01, {}) > failed

def test_campaign_counts_other_runs(store):
    with pytest.raises(BudgetExhausted):
        store.reserve_call("bootstrap", 101, {})
    other = store.root.parent / "other"
    other.mkdir()
    (other / "run.json").write_text(json.dumps({"schema_version": "5.0", "budget": {"cost_usd": 99.99}}))
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
