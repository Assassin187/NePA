import copy
import hashlib

import pytest

from nepa.speclib.lint import canonical_json_bytes
from nepa.speclib.plan import derive_task_metadata
from nepa.speclib.plan_revision import (
    append_revision_entry,
    build_revision_entry,
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
    projected = project_plan_state(old_state, new_plan, report, new_ref, revalidation_proofs={new_uid: {"build_passed": True}})
    assert [row["id"] for row in projected["tasks"]] == ["T-001", "T-002"]
    assert projected["tasks"][0]["status"] == "done"
    assert projected["tasks"][0]["commit_sha"] == old_state["tasks"][0]["commit_sha"]
    assert projected["tasks"][1]["status"] == "pending"
    assert projected["tasks"][1]["attempts"] == old_state["tasks"][1]["attempts"]
    assert "classification=AMEND" in projected["tasks"][1]["notes"]

    entry = build_revision_entry(
        _ref(plan), new_ref, "F2", {"code": "test", "evidence_refs": []}, [], report,
        gates={f"RG-{index}": "pass" for index in range(1, 6)}, activated_at_commit="c" * 40,
    )
    ledger = append_revision_entry({"schema_version": "1.0", "entries": []}, entry)
    snapshot = plan_state_snapshot_lint(
        new_plan,
        projected,
        s4_seal={"plan": _ref(plan), "active_plan": new_ref},
        revision_ledger=ledger,
    )
    assert snapshot["valid"] is True


def test_projection_requires_typed_revalidation_proof_and_rejects_partial_reports():
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
    with pytest.raises(ValueError, match="REVALIDATE"):
        project_plan_state(old_state, new_plan, report, _ref(new_plan, "1.0.1", 1))
    partial = copy.deepcopy(report)
    partial["tasks"].pop()
    with pytest.raises(ValueError, match="migration report counts"):
        project_plan_state(old_state, new_plan, partial, _ref(new_plan, "1.0.1", 1), revalidation_proofs={new_uid: {"passed": True}})


def test_projection_rejects_amend_of_an_exhausted_task_and_resets_regeneration():
    plan, _blueprint, _spec, _manifest, _constraints, _target = _linked()
    exhausted = _done_state(plan, attempts=4)
    new_plan = _amended_plan(plan)
    report = classify_migration(plan, new_plan, exhausted, {"schema_version": "1.0", "files": []}, from_version="1.0.0", to_version="1.0.1")
    with pytest.raises(ValueError, match="completed task below the total attempt limit"):
        project_plan_state(exhausted, new_plan, report, _ref(new_plan, "1.0.1", 1))

    regenerated = copy.deepcopy(plan)
    regenerated["tasks"][0]["deliverable_files"] = [*regenerated["tasks"][0]["deliverable_files"], "src/codec/generated.c"]
    contracts = {item["id"]: item for item in regenerated["architecture"]["contracts"]}
    regenerated["tasks"][0].update(derive_task_metadata(regenerated["tasks"][0], contracts=contracts))
    report = classify_migration(plan, regenerated, _done_state(plan), {"schema_version": "1.0", "files": []}, from_version="1.0.0", to_version="1.0.1")
    projected = project_plan_state(_done_state(plan), regenerated, report, _ref(regenerated, "1.0.1", 1))
    row = next(item for item in projected["tasks"] if item["id"] == "T-001")
    assert row["status"] == "pending"
    assert row["attempts"] == 0
    assert row["commit_sha"] is None
    assert row["acceptance_evidence"]["task_evidence_ref"] is None
