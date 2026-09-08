import copy
import hashlib
import json
from pathlib import Path

from nepa.speclib.delivery import compile_delivery_constraints
from nepa.speclib.lint import canonical_json_bytes
from nepa.speclib.planning import build_test_manifest_metadata, prepare_architecture_inputs
from nepa.speclib.plan import link_plan
from nepa.speclib.plan_state import execution_state_lint, initialize_plan_state, plan_state_snapshot_lint, project_state_transition, validate_state_transition


ROOT = Path(__file__).parents[1]


def _linked_plan():
    prepared = prepare_architecture_inputs(
        ROOT / "tests/fixtures/non_mqtt_application/spec.json",
        ROOT / "tests/fixtures/non_mqtt_application/target.json",
        ROOT / "tests/fixtures/non_mqtt_application/test_bundle.json",
    )
    constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    manifest = build_test_manifest_metadata(prepared.test_bundle, constraints)
    architecture = json.loads((ROOT / "tests/fixtures/non_mqtt_application/architecture-draft.json").read_text(encoding="utf-8"))
    shards = []
    for package in architecture["work_packages"]:
        shards.append({
            "schema_version": "1.0",
            "work_package_id": package["id"],
            "tasks": [{
                "local_id": "implement",
                "title": package["title"],
                "goal": package["goal"],
                "kind": "app",
                "instructions": "Implement the declared behavior.",
                "deliverable_files": package["allowed_files"],
                "context_refs": package["context_refs"],
                "requirement_responsibilities": package["requirement_responsibilities"],
                "provides_contracts": package["provides_contracts"],
                "consumes_contracts": package["consumes_contracts"],
                "depends_on": [],
                "acceptance": {"build_variant_ids": ["san"], "tests": []},
            }],
        })
    return link_plan(architecture, architecture["work_packages"], shards, constraints, spec=prepared.spec, manifest=manifest)["plan"]


def test_initial_snapshot_and_status_field_invariants_are_closed():
    plan = _linked_plan()
    state = initialize_plan_state(plan)
    assert plan_state_snapshot_lint(plan, state)["valid"] is True
    damaged = copy.deepcopy(state)
    damaged["tasks"][0]["attempts"] = 1
    report = plan_state_snapshot_lint(plan, damaged)
    assert report["valid"] is False
    assert any(error["code"] == "STATE_PENDING_ATTEMPTS_INVALID" for error in report["errors"])


def test_transitions_derive_the_only_legal_next_state():
    plan = _linked_plan()
    initial = initialize_plan_state(plan)
    started = copy.deepcopy(initial)
    started["tasks"][0].update(status="in_progress", attempts=1)
    start_report = validate_state_transition(initial, started, {"event": "attempt_started", "task_id": "T-001"})
    assert start_report["valid"] is True

    evidence_ref = {"path": "test_results/task_evidence/T-001/attempt_001.json", "sha256": "0" * 64}
    done = copy.deepcopy(started)
    done["tasks"][0].update(status="done", commit_sha="a" * 40, acceptance_evidence={"task_evidence_ref": evidence_ref})
    success = validate_state_transition(started, done, {"event": "attempt_succeeded", "task_id": "T-001", "commit_sha": "a" * 40, "evidence_ref": evidence_ref})
    assert success["valid"] is True

    rejected = validate_state_transition(done, done, {"event": "attempt_started", "task_id": "T-001"})
    assert rejected["valid"] is False


def test_migration_mode_allocations_keep_independent_accounting():
    plan = _linked_plan()
    state = initialize_plan_state(plan)
    migration_ref = {"revision_seq": 1, "event_seq": 2}
    amend = state["tasks"][0]
    amend.update(status="pending", execution_mode="amend", attempts=4, migration_ref=migration_ref)
    amend_event = {"event": "amendment_started", "task_id": amend["id"], "proof": {"baseline_commit": "a" * 40, "baseline_tree": "b" * 64, "evidence_seq": 1, "s6_attempts_used": 1, "migration_ref": migration_ref}}
    amended = project_state_transition(state, amend_event)
    amended_row = amended["tasks"][0]
    assert amended_row["attempts"] == 4
    assert amended_row["amendment_used"] == 1
    assert amended["s6_attempts_used"] == 1

    revalidate = initialize_plan_state(plan)
    row = revalidate["tasks"][0]
    row.update(status="pending", execution_mode="revalidate", attempts=4, migration_ref=migration_ref)
    validation_event = {"event": "validation_started", "task_id": row["id"], "proof": {"baseline_commit": "a" * 40, "baseline_tree": "b" * 64, "evidence_seq": 1, "migration_ref": migration_ref}}
    validating = project_state_transition(revalidate, validation_event)
    assert validating["tasks"][0]["status"] == "in_progress"
    assert validating["tasks"][0]["attempts"] == 4
    assert validating["s6_attempts_used"] == 0


def test_group_transition_updates_every_frozen_member_or_rejects_partial_input():
    plan = _linked_plan()
    state = initialize_plan_state(plan)
    group_id = "g-1-1"
    migration_ref = {"revision_seq": 1, "event_seq": 2}
    for row in state["tasks"]:
        row.update(status="in_progress", execution_mode="revalidate", migration_ref=migration_ref, group_id=group_id)
    members = sorted(state["tasks"], key=lambda row: row["task_uid"].encode("utf-8"))
    refs = [{"path": f"evidence/{row['task_uid']}.json", "sha256": str(index) * 64} for index, row in enumerate(members, 1)]
    event = {"event": "group_verified", "task_id": members[0]["id"], "group_id": group_id, "member_task_ids": [row["id"] for row in members], "member_evidence_refs": refs, "commit_sha": "a" * 40, "verification_id": f"v-{members[0]['task_uid']}-1", "evidence_ref": refs[0], "proof": {"kind": "group", "commit_sha": "a" * 40, "verification_id": f"v-{members[0]['task_uid']}-1", "workspace_tree": "b" * 64, "parent_sha": "c" * 40, "group_id": group_id, "activation_ref": migration_ref, "member_uids": [row["task_uid"] for row in members], "member_evidence_refs": refs}}
    done = project_state_transition(state, event)
    assert all(row["status"] == "done" and row["commit_sha"] == "a" * 40 for row in done["tasks"])
    partial = copy.deepcopy(event)
    partial["member_task_ids"].pop()
    partial["member_evidence_refs"].pop()
    partial["proof"]["member_uids"].pop()
    partial["proof"]["member_evidence_refs"].pop()
    assert validate_state_transition(state, None, partial)["valid"] is False


def test_execution_lint_checks_commit_trailers_evidence_identity_and_stage_anchor():
    plan = _linked_plan()
    initial = initialize_plan_state(plan)
    started = copy.deepcopy(initial)
    started["tasks"][0].update(status="in_progress", attempts=1)
    evidence = {
        "schema_version": "1.0",
        "task_id": "T-001",
        "attempt": 1,
        "plan_sha256": hashlib.sha256(canonical_json_bytes(plan)).hexdigest(),
        "build_result_refs": [{"path": "build.json", "sha256": "0" * 64}],
        "build_variant_ids": ["san"],
        "build_passed": True,
    }
    evidence_bytes = canonical_json_bytes(evidence)
    evidence_ref = {"path": "test_results/task_evidence/T-001/attempt_001.json", "sha256": hashlib.sha256(evidence_bytes).hexdigest()}
    commit = "a" * 40
    anchor = "b" * 40
    done = copy.deepcopy(started)
    done["tasks"][0].update(status="done", commit_sha=commit, acceptance_evidence={"task_evidence_ref": evidence_ref})
    workspace = {"commits": {anchor: {"parents": [], "trailers": {}}, commit: {"parents": [anchor], "trailers": {"NePA-Task": "T-001", "NePA-Attempt": "1", "NePA-Evidence-SHA256": evidence_ref["sha256"]}}}}
    receipts = {"s4": {"output_refs": {"plan": initial["plan_ref"]}}, "s5": {"workspace_head": anchor}}
    store = {evidence_ref["path"]: evidence_bytes}
    report = execution_state_lint(plan, done, workspace, store, receipts)
    assert report["valid"] is True

    store[evidence_ref["path"]] = evidence_bytes + b"drift"
    assert execution_state_lint(plan, done, workspace, store, receipts)["valid"] is False
