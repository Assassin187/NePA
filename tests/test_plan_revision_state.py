import copy
import hashlib

import pytest

from nepa.speclib.lint import canonical_json_bytes
from nepa.speclib.plan import derive_task_metadata
from nepa.speclib.plan_revision import (
    build_event_entry,
    classify_migration,
    project_plan_state,
)
from nepa.speclib.plan_state import initialize_plan_state, plan_state_snapshot_lint
from test_plan_lint import _linked


def _ref(plan, version="1.0.0", sequence=0, epoch="E0"):
    return {
        "path": f"plan/versions/plan-{version}.json",
        "sha256": hashlib.sha256(canonical_json_bytes(plan)).hexdigest(),
        "version": version,
        "revision_seq": sequence,
        "epoch": epoch,
    }


def _done_state(plan, attempts=1):
    state = initialize_plan_state(plan, plan_ref=_ref(plan))
    for row in state["tasks"]:
        row.update(
            status="done",
            attempts=attempts,
            commit_sha="a" * 40,
            acceptance_evidence={"task_evidence_ref": {"path": f"evidence/{row['id']}.json", "sha256": "b" * 64}},
        )
    return state


def _typed_ledger(old_ref, new_ref, report, level="F2"):
    ledger = {"schema_version": "2.0", "entries": []}
    trigger_payload = {"boundary_key": {"phase": "task_boundary", "revision_seq": new_ref["revision_seq"], "tasks": []}, "plan_ref": {"path": old_ref["path"], "sha256": old_ref["sha256"]}, "hit_code": "TR-4", "route": level, "hit_signature": "1" * 64, "evidence_refs": [], "selected": True, "reason": "test"}
    ledger["entries"].append(build_event_entry(ledger, "trigger_evaluated", trigger_payload))
    gates = {f"RG-{index}": "pass" for index in range(1, 6)}
    gates["RG-5"] = "not_applicable" if level == "F2" else "pass"
    binding_ref = {"path": f"plan/bindings/{new_ref['version']}/receipt.json", "sha256": "d" * 64} if level == "F2" else None
    activation = {"revision_seq": new_ref["revision_seq"], "from_version": old_ref["version"], "to_version": new_ref["version"], "from_plan_ref": {"path": old_ref["path"], "sha256": old_ref["sha256"]}, "to_plan_ref": {"path": new_ref["path"], "sha256": new_ref["sha256"]}, "level": level, "trigger_event_seq": 1, "trigger_signature": "1" * 64, "patch_ops": [], "migration": {key: copy.deepcopy(report[key]) for key in ("counts", "tasks", "files", "pending_groups", "re_adopt") if key in report}, "preservation_rate": report["preservation_rate"], "rework_cost_estimate_usd": 0.0, "gates": gates, "epoch_after": new_ref["epoch"], "activated_at_commit": "c" * 40, "binding_ref": binding_ref, "pending_materialization": level == "F3"}
    ledger["entries"].append(build_event_entry(ledger, "revision_activated", activation))
    return ledger


def _amended_plan(plan):
    amended = copy.deepcopy(plan)
    task = amended["tasks"][1]
    task["requirement_responsibilities"] = task["requirement_responsibilities"][:1]
    contracts = {item["id"]: item for item in amended["architecture"]["contracts"]}
    task.update(derive_task_metadata(task, contracts=contracts))
    return amended


def test_projection_derives_all_four_migration_rows_and_preserves_only_legal_state():
    plan, _blueprint, _spec, _manifest, _constraints, _target = _linked()
    old_state = _done_state(plan)
    new_plan = _amended_plan(plan)
    new_uid = "d" * 16
    new_plan["tasks"][0]["task_uid"] = new_uid
    report = classify_migration(
        plan,
        new_plan,
        old_state,
        {"schema_version": "1.0", "files": []},
        lineage={"task_mappings": [{"old_task_uid": plan["tasks"][0]["task_uid"], "new_task_uid": new_uid}]},
        from_version="1.0.0",
        to_version="1.0.1",
    )
    assert {row["classification"] for row in report["tasks"]} == {"REVALIDATE", "AMEND"}
    new_ref = _ref(new_plan, "1.0.1", 1)
    projected = project_plan_state(old_state, new_plan, report, new_ref, activation_event_seq=2)
    assert [row["id"] for row in projected["tasks"]] == ["T-001", "T-002"]
    assert projected["tasks"][0]["status"] == "pending"
    assert projected["tasks"][0]["commit_sha"] is None
    assert projected["tasks"][1]["status"] == "pending"
    assert projected["tasks"][1]["attempts"] == old_state["tasks"][1]["attempts"]
    assert "classification=AMEND" in projected["tasks"][1]["notes"]

    ledger = _typed_ledger(_ref(plan), new_ref, report)
    snapshot = plan_state_snapshot_lint(
        new_plan,
        projected,
        s4_seal={"plan": _ref(plan), "active_plan": new_ref},
        revision_ledger=ledger,
    )
    assert snapshot["valid"] is True


def test_projection_requires_activation_event_sequence_and_rejects_partial_reports():
    plan, _blueprint, _spec, _manifest, _constraints, _target = _linked()
    old_state = _done_state(plan)
    new_plan = copy.deepcopy(plan)
    new_uid = "e" * 16
    new_plan["tasks"][0]["task_uid"] = new_uid
    report = classify_migration(
        plan,
        new_plan,
        old_state,
        {"schema_version": "1.0", "files": []},
        lineage={"task_mappings": [{"old_task_uid": plan["tasks"][0]["task_uid"], "new_task_uid": new_uid}]},
        from_version="1.0.0",
        to_version="1.0.1",
    )
    with pytest.raises(ValueError, match="activation event sequence"):
        project_plan_state(old_state, new_plan, report, _ref(new_plan, "1.0.1", 1))
    partial = copy.deepcopy(report)
    partial["tasks"].pop()
    with pytest.raises(ValueError, match="migration report counts"):
        project_plan_state(old_state, new_plan, partial, _ref(new_plan, "1.0.1", 1), activation_event_seq=2)


def test_projection_allows_amend_of_an_exhausted_task_and_resets_regeneration():
    plan, _blueprint, _spec, _manifest, _constraints, _target = _linked()
    exhausted = _done_state(plan, attempts=4)
    new_plan = _amended_plan(plan)
    report = classify_migration(plan, new_plan, exhausted, {"schema_version": "1.0", "files": []}, from_version="1.0.0", to_version="1.0.1")
    amended = project_plan_state(exhausted, new_plan, report, _ref(new_plan, "1.0.1", 1), activation_event_seq=2)
    amended_row = next(item for item in amended["tasks"] if item["id"] == "T-002")
    assert amended_row["status"] == "pending"
    assert amended_row["attempts"] == 4
    assert amended_row["amendment_used"] == 0

    regenerated = copy.deepcopy(plan)
    regenerated["tasks"][0]["deliverable_files"] = [*regenerated["tasks"][0]["deliverable_files"], "src/codec/generated.c"]
    contracts = {item["id"]: item for item in regenerated["architecture"]["contracts"]}
    regenerated["tasks"][0].update(derive_task_metadata(regenerated["tasks"][0], contracts=contracts))
    report = classify_migration(plan, regenerated, _done_state(plan), {"schema_version": "1.0", "files": []}, from_version="1.0.0", to_version="1.0.1")
    projected = project_plan_state(_done_state(plan), regenerated, report, _ref(regenerated, "1.0.1", 1), activation_event_seq=2)
    row = next(item for item in projected["tasks"] if item["id"] == "T-001")
    assert row["status"] == "pending"
    assert row["attempts"] == 0
    assert row["commit_sha"] is None
    assert row["acceptance_evidence"]["task_evidence_ref"] is None


def test_projection_reopens_dependency_only_after_blocking_ancestor_changes():
    plan, _blueprint, _spec, _manifest, _constraints, _target = _linked()
    plan["tasks"][1]["depends_on"] = ["T-001"]
    old_state = initialize_plan_state(plan, plan_ref=_ref(plan))
    old_state["tasks"][0].update(status="blocked", last_error="ATTEMPTS_EXHAUSTED", attempts=4)
    old_state["tasks"][1].update(status="blocked_by_dependency", last_error="blocked by T-001")

    unchanged_report = classify_migration(plan, plan, old_state, {"schema_version": "1.0", "files": []}, from_version="1.0.0", to_version="1.0.1")
    unchanged = project_plan_state(old_state, plan, unchanged_report, _ref(plan, "1.0.1", 1), activation_event_seq=2)
    assert unchanged["tasks"][0]["status"] == "blocked"
    assert unchanged["tasks"][1]["status"] == "blocked_by_dependency"

    changed = copy.deepcopy(plan)
    changed["tasks"][0]["deliverable_files"] = [*changed["tasks"][0]["deliverable_files"], "src/codec/reopened.c"]
    contracts = {item["id"]: item for item in changed["architecture"]["contracts"]}
    changed["tasks"][0].update(derive_task_metadata(changed["tasks"][0], contracts=contracts))
    changed_report = classify_migration(plan, changed, old_state, {"schema_version": "1.0", "files": []}, from_version="1.0.0", to_version="1.0.1")
    reopened = project_plan_state(old_state, changed, changed_report, _ref(changed, "1.0.1", 1), activation_event_seq=2)
    assert reopened["tasks"][0]["status"] == "pending"
    assert reopened["tasks"][1]["status"] == "pending"
    assert reopened["tasks"][1]["attempts"] == old_state["tasks"][1]["attempts"]


def test_migrated_snapshot_lint_is_deterministic_and_does_not_mutate_inputs():
    plan, _blueprint, _spec, _manifest, _constraints, _target = _linked()
    old_state = _done_state(plan, attempts=4)
    new_plan = _amended_plan(plan)
    report = classify_migration(
        plan,
        new_plan,
        old_state,
        {"schema_version": "1.0", "files": []},
        from_version="1.0.0",
        to_version="1.0.1",
    )
    new_ref = _ref(new_plan, "1.0.1", 1)
    state = project_plan_state(old_state, new_plan, report, new_ref, activation_event_seq=2)
    ledger = _typed_ledger(_ref(plan), new_ref, report)
    damaged = copy.deepcopy(state)
    amended_row = next(row for row in damaged["tasks"] if row["execution_mode"] == "amend")
    amended_row["migration_ref"] = {"revision_seq": 1, "event_seq": 999}
    before = copy.deepcopy(damaged)

    first = plan_state_snapshot_lint(new_plan, damaged, revision_ledger=ledger)
    second = plan_state_snapshot_lint(new_plan, damaged, revision_ledger=ledger)

    assert first == second
    assert damaged == before
    assert first["valid"] is False
    assert first["errors"] == sorted(first["errors"], key=lambda item: (item["code"], item["path"], item["message"]))
