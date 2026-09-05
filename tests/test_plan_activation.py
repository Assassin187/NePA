import copy
import json

import pytest

from nepa.run_store import RunStore
from nepa.speclib.plan_revision import build_revision_entry, classify_migration, project_plan_state
from nepa.speclib.plan_state import initialize_plan_state
from nepa.stages.s4_planning import publish_initial_plan
from test_s4_publication import _completion


def _prepared_revision(tmp_path):
    store, completion = _completion(tmp_path)
    initial_result = publish_initial_plan(store, completion)
    old_pointer = json.loads((store.root / "plan/active_plan.json").read_text(encoding="utf-8"))
    old_file = json.loads((store.root / "plan/file_ledger.json").read_text(encoding="utf-8"))
    old_state = initialize_plan_state(completion.plan, plan_ref=old_pointer)
    store.replace_json("plan/plan_state.json", old_state, schema_name="plan-state.schema.json")
    run = store.load_run()
    run["stages"]["s4"] = {"status": "done", "started_at": None, "ended_at": None, "error": None, "output_refs": dict(initial_result.output_refs)}
    store.replace_run(run)
    new_plan = copy.deepcopy(completion.plan)
    report = classify_migration(completion.plan, new_plan, old_state, old_file, from_version="1.0.0", to_version="1.0.1")
    new_pointer = {"version": "1.0.1", "path": "plan/versions/plan-1.0.1.json", "sha256": store._canonical_value_hash(new_plan), "revision_seq": 1, "epoch": "E0"}
    new_state = project_plan_state(old_state, new_plan, report, new_pointer)
    entry = build_revision_entry(old_pointer, new_pointer, "F2", {"code": "test", "evidence_refs": []}, [], report, gates={"RG-1": "pass", "RG-2": "pass", "RG-3": "pass", "RG-4": "pass", "RG-5": "pass"}, activated_at_commit="0" * 40)
    return store, completion, old_pointer, old_file, new_plan, report, new_state, entry, new_pointer


def test_activation_wal_order_and_immutable_s4_anchor(tmp_path):
    store, completion, old_pointer, old_file, new_plan, report, new_state, entry, new_pointer = _prepared_revision(tmp_path)
    points = []
    receipt = store.activate_revision(new_plan, report, new_state, old_file, entry, old_pointer, new_pointer=new_pointer, fault_hook=points.append)
    assert points == ["wal_written", "version_published", "state_replaced", "file_ledger_replaced", "revision_ledger_replaced", "active_pointer_replaced", "run_reference_updated"]
    assert receipt["committed"] is True
    run = store.load_run()
    assert run["stages"]["s4"]["output_refs"]["plan"]["path"] == "plan/versions/plan-1.0.0.json"
    assert json.loads((store.root / "plan/active_plan.json").read_text(encoding="utf-8")) == new_pointer


def test_recovery_after_state_write_restores_precommit_state(tmp_path):
    store, _completion_value, old_pointer, old_file, new_plan, report, new_state, entry, new_pointer = _prepared_revision(tmp_path)

    def crash(point):
        if point == "state_replaced":
            raise RuntimeError(point)

    with pytest.raises(RuntimeError, match="state_replaced"):
        store.activate_revision(new_plan, report, new_state, old_file, entry, old_pointer, new_pointer=new_pointer, fault_hook=crash)
    recovered = store.recover_revision(1)
    assert recovered["status"] == "precommit-restored"
    assert json.loads((store.root / "plan/active_plan.json").read_text(encoding="utf-8")) == old_pointer
    assert json.loads((store.root / "plan/plan_state.json").read_text(encoding="utf-8")) == json.loads((store.root / "_s4r/rev_001/activation.json").read_text(encoding="utf-8"))["old_state"]
    assert store.recover_revision(1)["status"] == "precommit-restored"


@pytest.mark.parametrize(
    "point, expected_status",
    [
        ("wal_written", "precommit-restored"),
        ("version_published", "precommit-restored"),
        ("state_replaced", "precommit-restored"),
        ("file_ledger_replaced", "precommit-restored"),
        ("revision_ledger_replaced", "commit-completed"),
        ("active_pointer_replaced", "postcommit-verified"),
        ("run_reference_updated", "postcommit-verified"),
    ],
)
def test_recovery_converges_after_every_activation_write_boundary(tmp_path, point, expected_status):
    store, _completion_value, old_pointer, _old_file, new_plan, report, new_state, entry, new_pointer = _prepared_revision(tmp_path)

    def crash(current):
        if current == point:
            raise RuntimeError(current)

    with pytest.raises(RuntimeError, match=point):
        store.activate_revision(new_plan, report, new_state, _old_file, entry, old_pointer, new_pointer=new_pointer, fault_hook=crash)
    recovered = store.recover_revision(1)
    assert recovered["status"] == expected_status
    replay = store.recover_revision(1)
    assert replay["status"] in {expected_status, "postcommit-verified"}
    active = json.loads((store.root / "plan/active_plan.json").read_text(encoding="utf-8"))
    assert active == (old_pointer if expected_status == "precommit-restored" else new_pointer)


def test_recovery_fails_closed_on_conflicting_mutable_bytes(tmp_path):
    store, _completion_value, old_pointer, old_file, new_plan, report, new_state, entry, new_pointer = _prepared_revision(tmp_path)

    def crash(current):
        if current == "state_replaced":
            raise RuntimeError(current)

    with pytest.raises(RuntimeError, match="state_replaced"):
        store.activate_revision(new_plan, report, new_state, old_file, entry, old_pointer, new_pointer=new_pointer, fault_hook=crash)
    conflicting = json.loads((store.root / "plan/plan_state.json").read_text(encoding="utf-8"))
    conflicting["tasks"][0]["notes"] = "unbound mutation"
    store.replace_json("plan/plan_state.json", conflicting, schema_name="plan-state.schema.json")
    with pytest.raises(Exception, match="conflicting mutable artifact"):
        store.recover_revision(1)
    assert json.loads((store.root / "plan/active_plan.json").read_text(encoding="utf-8")) == old_pointer


def test_s4_completion_verifies_a_later_active_revision_without_moving_the_initial_anchor(tmp_path):
    from nepa.stages.s4_planning import S4Controller

    store, completion, old_pointer, old_file, new_plan, report, new_state, entry, new_pointer = _prepared_revision(tmp_path)
    store.publish_immutable_json("plan/_s4/delivery_constraints.json", completion.constraints)
    store.activate_revision(new_plan, report, new_state, old_file, entry, old_pointer, new_pointer=new_pointer)
    object.__new__(S4Controller).verify_completed(store)
    run = store.load_run()
    assert run["stages"]["s4"]["output_refs"]["plan"]["path"] == "plan/versions/plan-1.0.0.json"
    assert run["stages"]["s4"]["output_refs"]["active_plan"]["path"] == "plan/active_plan.json"
