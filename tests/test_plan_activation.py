import copy
import json
from pathlib import Path

import pytest

from nepa.run_store import ControllerLockError, RunStore
from nepa.speclib.plan_revision import classify_migration, project_plan_state
from nepa.speclib.plan_revision import build_event_entry, project_file_ledger, successor_pointer
from nepa.speclib.revision_mechanism import build_gate_result, prepare_activation_wal
from nepa.speclib.lint import canonical_json_bytes
from nepa.run_store import sha256_bytes
from nepa.speclib.plan_state import initialize_plan_state


def _prepared_v2_f3(tmp_path):
    from test_s5_multi_epoch import _accepted_e0_store

    store, _completion_value, _s5 = _accepted_e0_store(tmp_path)
    old_pointer = store._read_json_artifact("plan/active_plan.json")
    old_plan = store._read_json_artifact(old_pointer["path"])
    old_state = initialize_plan_state(old_plan, plan_ref=old_pointer)
    store.replace_json("plan/plan_state.json", old_state, schema_name="plan-state.schema.json")
    old_file = store._read_json_artifact("plan/file_ledger.json")
    candidate_plan = copy.deepcopy(old_plan)
    plan_hash = sha256_bytes(canonical_json_bytes(candidate_plan))
    new_pointer = successor_pointer(old_pointer, {"sha256": plan_hash}, "F3")
    report = classify_migration(
        old_plan, candidate_plan, old_state, old_file,
        from_version=old_pointer["version"], to_version=new_pointer["version"],
    )
    ledger = store._read_json_artifact("plan/revision_ledger.json")
    trigger_seq = len(ledger["entries"]) + 1
    signature = "1" * 64
    trigger = {
        "boundary_key": {"phase": "task_boundary", "revision_seq": old_pointer["revision_seq"], "tasks": []},
        "plan_ref": {"path": old_pointer["path"], "sha256": old_pointer["sha256"]},
        "hit_code": "TR-7", "route": "F3", "hit_signature": signature,
        "evidence_refs": [], "selected": True, "reason": "v2 activation fixture",
    }
    ledger["entries"].append(build_event_entry(ledger, "trigger_evaluated", trigger))
    store.replace_json("plan/revision_ledger.json", ledger, schema_name="revision-ledger.schema.json")
    new_state = project_plan_state(
        old_state, candidate_plan, report, new_pointer,
        activation_event_seq=len(ledger["entries"]) + 1, config_snapshot=store.load_run()["config_snapshot"],
    )
    new_file = project_file_ledger(
        old_file, candidate_plan, report, epoch=new_pointer["epoch"],
        new_paths={row["path"] for row in old_file["files"]},
    )
    old_run, new_run = store._run_with_active_pointer(new_pointer)
    old_manifest = store._read_json_artifact("plan/artifact_manifest.json")
    old_map = store._read_json_artifact("plan/contract_map.json")
    new_manifest = copy.deepcopy(old_manifest)
    new_manifest.update({"plan_version": new_pointer["version"], "plan_sha256": plan_hash, "epoch": new_pointer["epoch"]})
    new_map = copy.deepcopy(old_map)
    new_map.update({"plan_version": new_pointer["version"], "plan_sha256": plan_hash, "epoch": new_pointer["epoch"]})
    candidate = {
        "candidate_id": f"candidate-{trigger_seq}", "selected_event_seq": trigger_seq, "level": "F3",
        "source": {"plan_ref": trigger["plan_ref"]},
        "selected_trigger": {"code": "TR-7", "signature": signature},
    }
    candidate_dir = store._confined(f"plan/_s4r/candidate_{trigger_seq}")
    candidate_dir.mkdir(parents=True)
    boundary = {
        "active_pointer": store._json_artifact_hash("plan/active_plan.json"),
        "plan_state": store._json_artifact_hash("plan/plan_state.json"),
        "file_ledger": store._json_artifact_hash("plan/file_ledger.json"),
        "revision_ledger": store._json_artifact_hash("plan/revision_ledger.json"),
        "workspace_commit": "2" * 40, "workspace_tree": "3" * 40,
        "blueprint": "4" * 64, "contract_map": store._json_artifact_hash("plan/contract_map.json"),
    }
    candidate_ref = {"path": f"plan/_s4r/candidate_{trigger_seq}/candidate.json", "sha256": "5" * 64}
    rehearsal = {
        "schema_version": "1.0", "candidate_id": candidate["candidate_id"], "level": "F3",
        "baseline_commit": "2" * 40, "baseline_tree": "3" * 40,
        "candidate_plan_ref": {"path": new_pointer["path"], "sha256": plan_hash},
        "blueprint_ref": {"path": "blueprint.json", "sha256": "4" * 64},
        "migration_ref": {"path": "migration.json", "sha256": "6" * 64},
        "runs": [{"tree": "7" * 40, "result_sha256": "8" * 64, "build_result_refs": [], "smoke_result_refs": []}] * 2,
        "file_differences": [], "group_attribution": [], "verdict": "ready",
    }
    rehearsal_ref = store.publish_immutable_json(
        f"plan/_s4r/candidate_{trigger_seq}/rehearsal.json", rehearsal,
        schema_name="revision-rehearsal.schema.json",
    ).as_dict()
    gates = build_gate_result(
        candidate=candidate, candidate_ref=candidate_ref, boundary_hashes=boundary,
        statuses={f"RG-{index}": "pass" for index in range(1, 6)}, rehearsal_ref=rehearsal_ref,
    )
    gates_ref = store.publish_immutable_json(
        f"plan/_s4r/candidate_{trigger_seq}/gates.json", gates,
        schema_name="revision-gate-result.schema.json",
    ).as_dict()
    payload = {
        "revision_seq": new_pointer["revision_seq"], "from_version": old_pointer["version"], "to_version": new_pointer["version"],
        "from_plan_ref": trigger["plan_ref"], "to_plan_ref": {"path": new_pointer["path"], "sha256": plan_hash},
        "level": "F3", "trigger_event_seq": trigger_seq, "trigger_signature": signature, "patch_ops": [],
        "migration": {key: report[key] for key in ("counts", "tasks", "files")},
        "preservation_rate": report["preservation_rate"], "rework_cost_estimate_usd": 0,
        "gates": {f"RG-{index}": "pass" for index in range(1, 6)}, "epoch_after": new_pointer["epoch"],
        "activated_at_commit": "9" * 40, "binding_ref": None, "pending_materialization": True,
    }
    new_ledger = copy.deepcopy(ledger)
    new_ledger["entries"].append(build_event_entry(new_ledger, "revision_activated", payload))
    old = {"pointer": old_pointer, "state": old_state, "file_ledger": old_file, "revision_ledger": ledger, "run": old_run, "manifest": old_manifest, "contract_map": old_map}
    new = {"pointer": new_pointer, "state": new_state, "file_ledger": new_file, "revision_ledger": new_ledger, "run": new_run, "manifest": new_manifest, "contract_map": new_map}
    wal = prepare_activation_wal(
        candidate=candidate, candidate_plan=candidate_plan,
        candidate_plan_ref={"path": new_pointer["path"], "sha256": plan_hash},
        old=old, new=new, gates_ref=gates_ref, binding=None, rehearsal_ref=rehearsal_ref,
    )
    return store, wal


def _activate_v2(store, wal, *, fault_hook=None):
    with store.controller_lock():
        return store.activate_revision_v2(wal, fault_hook=fault_hook)


def test_production_run_store_has_no_v1_activation_or_recovery_entrypoint():
    import nepa.run_store

    source = Path(nepa.run_store.__file__).read_text(encoding="utf-8")
    assert "def activate_revision(" not in source
    assert "def recover_plan_revision(" not in source
    assert "def _revision_wal_path(" not in source


@pytest.mark.revision_mechanism
def test_v2_f3_activation_publishes_pending_materialization_without_binding(tmp_path):
    store, wal = _prepared_v2_f3(tmp_path)
    historical_binding = store._confined("plan/bindings/1.0.0/receipt.json").read_bytes()

    receipt = _activate_v2(store, wal)

    assert receipt["committed"] is True
    assert store._read_json_artifact("plan/active_plan.json") == wal["new"]["pointer"]
    assert wal["new"]["pointer"]["epoch"] == "E1"
    assert wal["binding"] is None and wal["pending_materialization"] is True
    run = store.load_run()
    assert run["stages"]["s5"]["status"] == "pending"
    assert run["stages"]["s5"]["instance_id"] == "E1"
    assert "output_refs" not in run["stages"]["s5"]
    assert store._confined("plan/bindings/1.0.0/receipt.json").read_bytes() == historical_binding


@pytest.mark.revision_mechanism
def test_v2_activation_requires_controller_lock_before_writing(tmp_path):
    store, wal = _prepared_v2_f3(tmp_path)

    with pytest.raises(ControllerLockError, match="requires the controller lock"):
        store.activate_revision_v2(wal)

    assert not store._confined(
        f"plan/_s4r/candidate_{wal['selected_event_seq']}/activation.json"
    ).exists()


@pytest.mark.parametrize("point", [
    "after_wal", "before_successor_plan", "after_successor_plan", "before_state", "after_state",
    "before_file_ledger", "after_file_ledger", "before_revision_ledger", "after_revision_ledger",
    "before_active_pointer",
])
@pytest.mark.revision_mechanism
def test_v2_pointer_old_recovery_rolls_back_even_after_new_ledger(tmp_path, point):
    store, wal = _prepared_v2_f3(tmp_path)

    def crash(point):
        if point == point_to_fail:
            raise RuntimeError(point)

    point_to_fail = point
    with pytest.raises(RuntimeError, match=point):
        _activate_v2(store, wal, fault_hook=crash)
    assert store._read_json_artifact("plan/active_plan.json") == wal["old"]["pointer"]
    recovered = store.recover_revision(wal["revision_seq"])
    assert recovered["status"] == "precommit-restored"
    assert store._read_json_artifact("plan/revision_ledger.json") == wal["old"]["revision_ledger"]
    replay = _activate_v2(store, wal)
    assert replay["committed"] is True
    assert store._read_json_artifact("plan/active_plan.json") == wal["new"]["pointer"]


@pytest.mark.parametrize("point", [
    "after_active_pointer", "before_run_and_current_copies", "after_run_and_current_copies",
    "before_wal_finalization", "after_wal_finalization",
])
@pytest.mark.revision_mechanism
def test_v2_pointer_new_recovery_forward_completes_projections(tmp_path, point):
    store, wal = _prepared_v2_f3(tmp_path)

    def crash(point):
        if point == point_to_fail:
            raise RuntimeError(point)

    point_to_fail = point
    with pytest.raises(RuntimeError, match=point):
        _activate_v2(store, wal, fault_hook=crash)
    recovered = store.recover_revision(wal["revision_seq"])
    expected = "already-reconciled" if point == "after_wal_finalization" else "postcommit-verified"
    assert recovered["status"] == expected
    assert store.load_run() == wal["new"]["run"]
    assert store._read_json_artifact("plan/artifact_manifest.json") == wal["new"]["manifest"]


@pytest.mark.revision_mechanism
def test_v2_fault_before_wal_has_no_transaction_to_reconcile(tmp_path):
    store, wal = _prepared_v2_f3(tmp_path)

    with pytest.raises(RuntimeError, match="before_wal"):
        _activate_v2(
            store,
            wal,
            fault_hook=lambda point: (_ for _ in ()).throw(RuntimeError(point)) if point == "before_wal" else None,
        )
    assert not store._confined(f"plan/_s4r/candidate_{wal['selected_event_seq']}/activation.json").exists()
    assert store._read_json_artifact("plan/active_plan.json") == wal["old"]["pointer"]
    assert _activate_v2(store, wal)["committed"] is True


@pytest.mark.revision_mechanism
@pytest.mark.parametrize("damage", ["third_pointer", "wal_hash", "state_bytes"])
def test_v2_activation_recovery_fails_closed_on_damage(tmp_path, damage):
    store, wal = _prepared_v2_f3(tmp_path)

    def crash(point):
        if point == "after_state":
            raise RuntimeError(point)

    with pytest.raises(RuntimeError, match="after_state"):
        _activate_v2(store, wal, fault_hook=crash)
    if damage == "third_pointer":
        third = copy.deepcopy(wal["old"]["pointer"])
        third.update({"version": "1.0.9", "path": "plan/versions/plan-1.0.9.json", "revision_seq": 9})
        store.replace_json("plan/active_plan.json", third, schema_name="active-plan.schema.json")
    elif damage == "wal_hash":
        current = store._read_json_artifact(f"plan/_s4r/candidate_{wal['selected_event_seq']}/activation.json")
        current["hashes"]["old"]["state"] = "f" * 64
        store.replace_json(
            f"plan/_s4r/candidate_{wal['selected_event_seq']}/activation.json", current,
            schema_name="plan-activation.schema.json",
        )
    else:
        current = store._read_json_artifact("plan/plan_state.json")
        current["tasks"][0]["notes"] = "unbound damage"
        store.replace_json("plan/plan_state.json", current, schema_name="plan-state.schema.json")
    with pytest.raises(Exception, match="conflict|neither|invalid"):
        store.recover_revision(wal["revision_seq"])


@pytest.mark.revision_mechanism
def test_reconciled_wal_does_not_freeze_later_mutable_state(tmp_path):
    store, wal = _prepared_v2_f3(tmp_path)
    _activate_v2(store, wal)
    state = store._read_json_artifact("plan/plan_state.json")
    state["tasks"][0]["notes"] = "legal later S5/S6 progression"
    store.replace_json("plan/plan_state.json", state, schema_name="plan-state.schema.json")

    assert store.reconcile_revision_activations() == []
    assert store.recover_revision(wal["revision_seq"])["status"] == "already-reconciled"


@pytest.mark.revision_mechanism
def test_multiple_unfinished_v2_wals_fail_closed(tmp_path):
    store, wal = _prepared_v2_f3(tmp_path)

    with pytest.raises(RuntimeError, match="after_wal"):
        _activate_v2(
            store, wal,
            fault_hook=lambda point: (_ for _ in ()).throw(RuntimeError(point)) if point == "after_wal" else None,
        )
    duplicate = copy.deepcopy(store._read_json_artifact(
        f"plan/_s4r/candidate_{wal['selected_event_seq']}/activation.json"
    ))
    store.replace_json(
        "plan/_s4r/candidate_999/activation.json", duplicate,
        schema_name="plan-activation.schema.json",
    )

    with pytest.raises(Exception, match="multiple unfinished"):
        store.reconcile_revision_activations()


@pytest.mark.revision_mechanism
def test_v2_activation_detects_schema_valid_drift_at_next_boundary(tmp_path):
    store, wal = _prepared_v2_f3(tmp_path)

    def drift(point):
        if point == "after_state":
            ledger = store._read_json_artifact("plan/file_ledger.json")
            ledger["files"][0]["content_sha256"] = "0" * 64
            store.replace_json("plan/file_ledger.json", ledger, schema_name="file-ledger.schema.json")

    with pytest.raises(Exception, match="boundary drifted for file_ledger"):
        _activate_v2(store, wal, fault_hook=drift)


@pytest.mark.revision_mechanism
def test_v2_finalization_rechecks_successor_plan_bytes(tmp_path):
    store, wal = _prepared_v2_f3(tmp_path)

    def drift(point):
        if point == "before_wal_finalization":
            plan = copy.deepcopy(wal["candidate_plan"])
            plan["architecture"]["assumptions"].append("schema-valid drift")
            store.replace_json(wal["candidate_plan_ref"]["path"], plan, schema_name="plan.schema.json")

    with pytest.raises(Exception, match="successor Plan conflicts"):
        _activate_v2(store, wal, fault_hook=drift)


@pytest.mark.revision_mechanism
def test_pointer_old_recovery_rejects_f2_binding_reference_drift(tmp_path):
    from test_s5_multi_epoch import _accepted_e0_store, _activate_candidate

    store, _completion, _controller = _accepted_e0_store(tmp_path)
    pointer = store._read_json_artifact("plan/active_plan.json")
    plan = store._read_json_artifact(pointer["path"])
    store.replace_json(
        "plan/plan_state.json", initialize_plan_state(plan, plan_ref=pointer),
        schema_name="plan-state.schema.json",
    )

    with pytest.raises(RuntimeError, match="after_f2_binding"):
        _activate_candidate(
            store, copy.deepcopy(plan), "F2",
            fault_hook=lambda point: (_ for _ in ()).throw(RuntimeError(point))
            if point == "after_f2_binding" else None,
        )
    wal_path = next(store._confined("plan/_s4r").glob("candidate_*/activation.json"))
    wal = store._read_json_artifact(wal_path.relative_to(store.root).as_posix())
    receipt_path = wal["binding"]["receipt_ref"]["path"]
    receipt = store._read_json_artifact(receipt_path)
    receipt["epoch_receipt_ref"]["sha256"] = "0" * 64
    store.replace_json(receipt_path, receipt, schema_name="binding-receipt.schema.json")

    with pytest.raises(Exception, match="hash mismatch"):
        store.recover_revision(wal["revision_seq"])
