import copy
import hashlib

import pytest

from nepa.speclib.lint import canonical_json_bytes
from nepa.speclib.plan_revision import (
    PlanRevisionError,
    build_revision_entry,
    classify_migration,
    project_file_ledger,
    project_plan_state,
    successor_pointer,
    validate_revision_ledger,
)
from nepa.speclib.plan_state import initialize_plan_state
from test_plan_lint import _linked


def _ref(plan, version="1.0.0", sequence=0, epoch="E0"):
    return {
        "path": f"plan/versions/plan-{version}.json",
        "sha256": hashlib.sha256(canonical_json_bytes(plan)).hexdigest(),
        "version": version,
        "revision_seq": sequence,
        "epoch": epoch,
    }


def test_classification_is_complete_and_zero_file_preservation_is_exact():
    plan, _blueprint, _spec, _manifest, _constraints, _target = _linked()
    old_state = initialize_plan_state(plan, plan_ref=_ref(plan))
    for row in old_state["tasks"]:
        row.update(
            status="done",
            attempts=1,
            commit_sha="a" * 40,
            acceptance_evidence={"task_evidence_ref": {"path": f"evidence/{row['id']}.json", "sha256": "b" * 64}},
        )
    report = classify_migration(plan, copy.deepcopy(plan), old_state, {"schema_version": "1.0", "files": []}, from_version="1.0.0", to_version="1.0.1")
    assert report["counts"] == {"inherit": len(plan["tasks"]), "revalidate": 0, "amend": 0, "regenerate": 0}
    assert report["preservation_rate"] == 1.0
    assert len(report["tasks"]) == len(plan["tasks"])
    new_ref = _ref(plan, "1.0.1", 1)
    projected = project_plan_state(old_state, plan, report, new_ref)
    assert projected["plan_ref"] == new_ref
    assert [row["id"] for row in projected["tasks"]] == [row["id"] for row in old_state["tasks"]]


def test_never_done_task_is_regenerated_even_when_its_obligation_is_unchanged():
    plan, _blueprint, _spec, _manifest, _constraints, _target = _linked()
    old_state = initialize_plan_state(plan, plan_ref=_ref(plan))
    report = classify_migration(plan, copy.deepcopy(plan), old_state, {"schema_version": "1.0", "files": []}, from_version="1.0.0", to_version="1.0.1")
    assert {row["classification"] for row in report["tasks"]} == {"REGENERATE"}


def test_explicit_split_lineage_records_each_new_task_without_inference():
    plan, _blueprint, _spec, _manifest, _constraints, _target = _linked()
    old_state = initialize_plan_state(plan, plan_ref=_ref(plan))
    old_task = copy.deepcopy(plan["tasks"][0])
    first = copy.deepcopy(old_task)
    first.update(id="T-001", task_uid="a" * 16)
    second = copy.deepcopy(old_task)
    second.update(id="T-002", task_uid="b" * 16)
    survivor = copy.deepcopy(plan["tasks"][1])
    survivor["id"] = "T-003"
    new_plan = copy.deepcopy(plan)
    new_plan["tasks"] = [first, second, survivor]
    report = classify_migration(
        plan,
        new_plan,
        old_state,
        {"schema_version": "1.0", "files": []},
        lineage={"task_mappings": [
            {"derived_from": old_task["task_uid"], "new_task_uid": first["task_uid"]},
            {"derived_from": old_task["task_uid"], "new_task_uid": second["task_uid"]},
        ]},
        from_version="1.0.0",
        to_version="1.0.1",
    )
    split_rows = [row for row in report["tasks"] if row["new_task_uid"] in {first["task_uid"], second["task_uid"]}]
    assert {(row["old_task_uid"], row["new_task_uid"]) for row in split_rows} == {
        (old_task["task_uid"], first["task_uid"]),
        (old_task["task_uid"], second["task_uid"]),
    }


def test_explicit_merge_lineage_records_all_source_tasks():
    plan, _blueprint, _spec, _manifest, _constraints, _target = _linked()
    old_state = initialize_plan_state(plan, plan_ref=_ref(plan))
    merged = copy.deepcopy(plan["tasks"][0])
    merged.update(id="T-001", task_uid="c" * 16, deliverable_files=sorted({*plan["tasks"][0]["deliverable_files"], *plan["tasks"][1]["deliverable_files"]}))
    new_plan = copy.deepcopy(plan)
    new_plan["tasks"] = [merged]
    report = classify_migration(
        plan,
        new_plan,
        old_state,
        {"schema_version": "1.0", "files": []},
        lineage={"task_mappings": [{"merged_from": [task["task_uid"] for task in plan["tasks"]], "new_task_uid": merged["task_uid"]}]},
        from_version="1.0.0",
        to_version="1.0.1",
    )
    row = report["tasks"][0]
    sources = sorted(plan["tasks"], key=lambda task: task["task_uid"])
    assert row["old_task_uid"] == sources[0]["task_uid"]
    assert row["merged_from"] == [sources[1]["task_uid"]]
    assert row["merged_from_ids"] == [sources[1]["id"]]


def test_removed_realized_file_is_regenerated_and_quarantined():
    plan, _blueprint, _spec, _manifest, _constraints, _target = _linked()
    old_state = initialize_plan_state(plan, plan_ref=_ref(plan))
    uid = plan["tasks"][0]["task_uid"]
    ledger = {
        "schema_version": "1.0",
        "files": [{
            "path": "src/removed.c", "class": "s6_owned", "state": "realized",
            "created_in_epoch": "E0", "content_sha256": "a" * 64, "last_commit_sha": "b" * 40,
            "verified_by": {"build_variant_ids": ["san"], "evidence_ref": {"path": "evidence.json", "sha256": "c" * 64}},
            "owner_history": [{"plan_version": "1.0.0", "task_uid": uid, "task_id": "T-001"}],
        }],
    }
    report = classify_migration(plan, copy.deepcopy(plan), old_state, ledger, from_version="1.0.0", to_version="1.0.1")
    assert report["files"][0]["classification"] == "REGENERATE"
    assert report["preservation_rate"] == 0.0
    projected = project_file_ledger(ledger, plan, report, epoch="E1")
    removed = next(row for row in projected["files"] if row["path"] == "src/removed.c")
    assert removed["state"] == "quarantined"


def test_successor_and_revision_chain_reject_non_monotonic_values():
    old = {"version": "1.2.3", "path": "plan/versions/plan-1.2.3.json", "sha256": "a" * 64, "revision_seq": 4, "epoch": "E2"}
    new = successor_pointer(old, {"path": "ignored", "sha256": "b" * 64}, "F2")
    assert new == {"version": "1.2.4", "path": "plan/versions/plan-1.2.4.json", "sha256": "b" * 64, "revision_seq": 5, "epoch": "E2"}
    broken = copy.deepcopy(new)
    broken["revision_seq"] += 1
    with pytest.raises(PlanRevisionError):
        from nepa.speclib.plan_revision import validate_plan_successor
        validate_plan_successor(old, broken, "F2")
    with pytest.raises(PlanRevisionError):
        validate_revision_ledger({"schema_version": "1.0", "entries": [{"revision_seq": 2}]})


def test_f1_is_rejected_by_entry_builder_and_ledger_validator():
    plan, *_ = _linked()
    old = _ref(plan)
    new = _ref(plan, "1.0.1", 1)
    state = initialize_plan_state(plan, plan_ref=old)
    report = classify_migration(plan, plan, state, {"schema_version": "1.0", "files": []})
    kwargs = {"gates": {f"RG-{i}": "pass" for i in range(1, 6)}, "activated_at_commit": "a" * 40}
    trigger = {"code": "test", "evidence_refs": []}
    with pytest.raises(PlanRevisionError, match="only F2 and F3"):
        build_revision_entry(old, {**old, "revision_seq": 1}, "F1", trigger, [], report, **kwargs)
    entry = build_revision_entry(old, new, "F2", trigger, [], report, **kwargs)
    validate_revision_ledger({"schema_version": "1.0", "entries": [entry]})
    entry.update(level="F1", to_version=entry["from_version"], to_plan_ref=entry["from_plan_ref"])
    with pytest.raises(PlanRevisionError, match="only F2 and F3"):
        validate_revision_ledger({"schema_version": "1.0", "entries": [entry]})


@pytest.mark.parametrize("lineage", [
    {"tasks": []},
    {"task_mappings": [{"new_uid": "a" * 16, "old_task_uid": "b" * 16}]},
    {"task_mappings": [{"new_task_uid": "a" * 16, "derived_from": "b" * 16, "old_task_uid": "b" * 16}]},
])
def test_lineage_rejects_aliases_and_conflicting_sources(lineage):
    plan, *_ = _linked()
    state = initialize_plan_state(plan, plan_ref=_ref(plan))
    with pytest.raises(PlanRevisionError):
        classify_migration(plan, plan, state, {"schema_version": "1.0", "files": []}, lineage)


@pytest.mark.parametrize("reverse", [False, True])
def test_lineage_cannot_consume_a_merged_source_again_as_a_split(reverse):
    plan, *_ = _linked()
    state = initialize_plan_state(plan, plan_ref=_ref(plan))
    new = copy.deepcopy(plan)
    for i, task in enumerate(new["tasks"]):
        task["task_uid"] = str(i + 1) * 16
    rows = [
        {"merged_from": [task["task_uid"] for task in plan["tasks"]], "new_task_uid": new["tasks"][0]["task_uid"]},
        {"derived_from": plan["tasks"][0]["task_uid"], "new_task_uid": new["tasks"][1]["task_uid"]},
    ]
    with pytest.raises(PlanRevisionError, match="reuses a source"):
        classify_migration(plan, new, state, {"schema_version": "1.0", "files": []}, {"task_mappings": rows[::-1] if reverse else rows})
