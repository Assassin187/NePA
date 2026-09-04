import copy
import hashlib
import json
from pathlib import Path

from nepa.speclib.delivery import compile_delivery_constraints
from nepa.speclib.lint import canonical_json_bytes
from nepa.speclib.planning import build_test_manifest_metadata, prepare_architecture_inputs
from nepa.speclib.plan import link_plan
from nepa.speclib.plan_state import execution_state_lint, initialize_plan_state, plan_state_snapshot_lint, validate_state_transition


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
