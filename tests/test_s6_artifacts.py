import copy
import hashlib

import pytest
from jsonschema import Draft202012Validator

from nepa.schemas import load_example, load_schema
from nepa.speclib.lint import canonical_json_bytes
from nepa.speclib.plan_revision import PlanRevisionError, append_verification_committed, validate_revision_ledger


def test_attempt_schema_enforces_terminal_reference_conditions():
    schema = load_schema("s6-attempt.schema.json")
    attempt = load_example("s6-attempt.example.json")
    assert not list(Draft202012Validator(schema).iter_errors(attempt))
    broken = copy.deepcopy(attempt)
    broken["status"] = "failed"
    assert list(Draft202012Validator(schema).iter_errors(broken))


def test_verification_wal_requires_closed_complete_precommit_projection():
    schema = load_schema("verification-pending.schema.json")
    wal = load_example("verification-pending.example.json")
    assert not list(Draft202012Validator(schema).iter_errors(wal))
    for field in ("expected_commit", "expected_git_tree", "new_state", "changed_files"):
        broken = copy.deepcopy(wal)
        broken.pop(field)
        assert list(Draft202012Validator(schema).iter_errors(broken)), field
    nested_extra = copy.deepcopy(wal)
    nested_extra["old_state"]["unexpected"] = True
    assert list(Draft202012Validator(schema).iter_errors(nested_extra))


def test_revision_ledger_rejects_duplicate_verification_identity_even_when_conflicting():
    evidence = {
        "path": "test_results/task_evidence/0123456789abcdef/evidence_001.json",
        "sha256": "1" * 64,
    }
    ledger = append_verification_committed(
        {"schema_version": "2.0", "entries": []},
        task_uid="0123456789abcdef", evidence_ref=evidence, commit_sha="2" * 40,
    )
    duplicate = copy.deepcopy(ledger["entries"][0])
    duplicate["event_seq"] = 2
    duplicate["prev_entry_sha256"] = hashlib.sha256(canonical_json_bytes(ledger["entries"][0])).hexdigest()
    duplicate["payload"]["commit_sha"] = "3" * 40
    ledger["entries"].append(duplicate)
    with pytest.raises(PlanRevisionError, match="duplicate verification identity"):
        validate_revision_ledger(ledger)


def _migration_ref():
    return {"revision_seq": 1, "event_seq": 2}


def test_task_evidence_schema_accepts_current_migration_forms_and_rejects_cross_form_fields():
    schema = load_schema("task-evidence.schema.json")
    validator = Draft202012Validator(schema)
    base = load_example("task-evidence.example.json")
    base["plan_ref"].update({"path": "plan/versions/plan-1.1.0.json", "version": "1.1.0", "revision_seq": 1, "epoch": "E1"})
    base.update({"plan_version": "1.1.0", "epoch": "E1", "migration_ref": _migration_ref()})

    revalidate = copy.deepcopy(base)
    revalidate.update({"execution_kind": "revalidate", "attempt": 0, "changed_files": []})
    assert not list(validator.iter_errors(revalidate))

    amend = copy.deepcopy(base)
    amend.update({"execution_kind": "amend", "attempt": 4, "amendment_used": 1})
    assert not list(validator.iter_errors(amend))

    group = copy.deepcopy(base)
    group.update({"execution_kind": "group", "group_id": "g-1-1", "group_member_uids": ["0123456789abcdef"]})
    assert not list(validator.iter_errors(group))

    empty_group_normal = copy.deepcopy(group)
    empty_group_normal["changed_files"] = []
    assert list(validator.iter_errors(empty_group_normal))

    broken = copy.deepcopy(revalidate)
    broken["attempt"] = 1
    assert list(validator.iter_errors(broken))
    broken = copy.deepcopy(amend)
    broken["amendment_used"] = 0
    assert list(validator.iter_errors(broken))
    broken = copy.deepcopy(group)
    broken["lease_id"] = "lease-1"
    assert list(validator.iter_errors(broken))


def test_joint_evidence_schema_discriminates_lease_and_group_members():
    schema = load_schema("joint-evidence.schema.json")
    validator = Draft202012Validator(schema)
    lease = load_example("joint-evidence.example.json")
    assert not list(validator.iter_errors(lease))

    group = copy.deepcopy(lease)
    group.update({"kind": "group", "group_id": "g-1-1"})
    for member in group["members"]:
        member.update({"execution_kind": "amend", "migration_ref": _migration_ref()})
    assert not list(validator.iter_errors(group))

    singleton = copy.deepcopy(group)
    singleton["members"] = singleton["members"][:1]
    assert not list(validator.iter_errors(singleton))

    invalid_lease = copy.deepcopy(lease)
    invalid_lease["members"] = invalid_lease["members"][:1]
    assert list(validator.iter_errors(invalid_lease))

    invalid_empty_amend = copy.deepcopy(singleton)
    invalid_empty_amend["members"][0]["changed_files"] = []
    assert list(validator.iter_errors(invalid_empty_amend))

    mixed = copy.deepcopy(lease)
    mixed["group_id"] = "g-1-1"
    assert list(validator.iter_errors(mixed))
    partial = copy.deepcopy(group)
    partial["members"][0].pop("migration_ref")
    assert list(validator.iter_errors(partial))


def test_group_verification_wal_requires_complete_group_identity_and_baseline():
    schema = load_schema("verification-pending.schema.json")
    validator = Draft202012Validator(schema)
    wal = load_example("verification-pending.example.json")
    members = ["0123456789abcdef", "fedcba9876543210"]
    wal.update({
        "kind": "group",
        "allocated_state": copy.deepcopy(wal["old_state"]),
        "activation_ref": {"event_seq": 1},
        "group_id": "g-1-1",
        "epoch_checkpoint_commit": wal["expected_parent"],
        "epoch_checkpoint_tree": wal["expected_git_tree"],
        "baseline_commit": wal["expected_parent"],
        "baseline_tree": wal["expected_tree"],
        "joint_evidence_ref": {"path": "test_results/task_evidence/joint/v-0123456789abcdef-1.json", "sha256": "4" * 64},
        "member_uids": members,
        "member_evidence": [
            {"task_uid": uid, "task_id": f"T-{index:03d}", "evidence_seq": index, "evidence_ref": {"path": f"test_results/task_evidence/{uid}/evidence_{index:03d}.json", "sha256": str(index) * 64}, "changed_files": []}
            for index, uid in enumerate(members, 1)
        ],
        "member_modes": [
            {"task_uid": uid, "execution_mode": "revalidate", "evidence_seq": index, "migration_ref": _migration_ref()}
            for index, uid in enumerate(members, 1)
        ],
        "candidate_refs": [],
        "failure_refs": [],
        "expected_trailers": {"NePA-Verification-ID": "v-0123456789abcdef-1", "NePA-Joint-Evidence-SHA256": "4" * 64},
    })
    assert not list(validator.iter_errors(wal))
    for field in ("activation_ref", "group_id", "epoch_checkpoint_commit", "epoch_checkpoint_tree", "baseline_commit", "baseline_tree", "member_modes"):
        broken = copy.deepcopy(wal)
        broken.pop(field)
        assert list(validator.iter_errors(broken)), field
    singleton = copy.deepcopy(wal)
    singleton["member_uids"] = singleton["member_uids"][:1]
    singleton["member_evidence"] = singleton["member_evidence"][:1]
    singleton["member_modes"] = singleton["member_modes"][:1]
    assert not list(validator.iter_errors(singleton))
    mixed = copy.deepcopy(wal)
    mixed["lease_id"] = "lease-1"
    assert list(validator.iter_errors(mixed))


def test_group_verification_without_accepted_activation_is_rejected():
    evidence = {"path": "test_results/task_evidence/0123456789abcdef/evidence_001.json", "sha256": "1" * 64}
    other = {"path": "test_results/task_evidence/fedcba9876543210/evidence_001.json", "sha256": "2" * 64}
    with pytest.raises(PlanRevisionError, match="accepted activation"):
        append_verification_committed(
            {"schema_version": "2.0", "entries": []},
            commit_sha="3" * 40,
            revision_seq=0,
            kind="group",
            group_id="g-1-1",
            joint_evidence_ref={"path": "test_results/task_evidence/joint/v-0123456789abcdef-1.json", "sha256": "4" * 64},
            members=[{"task_uid": "0123456789abcdef", "evidence_ref": evidence}, {"task_uid": "fedcba9876543210", "evidence_ref": other}],
        )
