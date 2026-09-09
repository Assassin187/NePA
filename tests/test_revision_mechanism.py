import copy
import hashlib

import pytest
from jsonschema import Draft202012Validator

from nepa.config import ConfigError, load_config
from nepa.speclib.lint import canonical_json_bytes
from nepa.speclib.plan import complete_plan_candidate, derive_task_metadata, normalize_plan_draft, plan_to_draft_ir
from nepa.speclib.revision_mechanism import (
    RevisionMechanismError,
    _migration_extensions,
    _validate_obligation_preservation,
    append_trigger_batch,
    apply_revision_patch,
    complete_revision_candidate,
    evaluate_revision_triggers,
    project_revision_boundary,
    validate_revision_candidate,
)
from nepa.speclib.plan_state import initialize_plan_state
from nepa.speclib.plan_revision import build_event_entry
from nepa.run_store import ArtifactConflict, RunStore
from test_plan_lint import _linked


pytestmark = pytest.mark.revision_mechanism


def _enable_revision(store):
    config = load_config(overrides={"run": {"until": "s6"}, "revision": {"theta2": 0.5, "theta6": 0.5}})
    run = store.load_run()
    run["config_snapshot"] = config.snapshot
    run["config_snapshot_sha256"] = config.snapshot_sha256
    run["stages"]["s4"]["output_refs"]["config_snapshot_sha256"] = config.snapshot_sha256
    store.replace_run(run)
    return config


def _ref(path, char):
    return {"path": path, "sha256": char * 64}


def _boundary(facts, ledger=None, *, phase="task_boundary"):
    ledger = ledger or {"schema_version": "2.0", "entries": []}
    return project_revision_boundary(
        phase=phase,
        revision_seq=0,
        tasks=[{"task_uid": "0" * 16, "mode": "REGENERATE", "attempts": 2, "amendment_used": False, "status": "blocked"}],
        plan_ref=_ref("plan/versions/plan-1.0.0.json", "1"),
        epoch="E0",
        state_history_ref=_ref("plan/state_history.json", "2"),
        workspace_commit="3" * 40,
        workspace_tree="4" * 40,
        blueprint_ref=_ref("plan/_s4/delivery_blueprint.json", "5"),
        contract_map_ref=_ref("plan/_s5/E0/contract_map.json", "6"),
        file_ledger_ref=_ref("plan/file_ledger.json", "a"),
        revision_ledger=ledger,
        thresholds={"theta_2": 0.5, "theta_6": 0.5},
        facts=facts,
    )


def _fact(**updates):
    value = {"task_uids": ["0" * 16], "obligation_uids": ["REQ-1"], "evidence_refs": [_ref("evidence/fact.json", "7")]}
    value.update(updates)
    return value


@pytest.mark.parametrize(
    ("facts", "phase", "code", "route"),
    [
        ({"undefined_symbols": [_fact(task_uids=["0" * 16, "1" * 16], symbol="missing", declared=False, consumes_closure_complete=True)]}, "task_boundary", "TR-1", "F3"),
        ({"blocked_providers": [_fact(unique_provider=True, task_ready=True, consumer_task_uids=["0" * 16], remaining_incomplete_primary_task_uids=["0" * 16])]}, "task_boundary", "TR-2", "F2"),
        ({"write_rejections": [_fact(task_uid="0" * 16, path="src/a.c", f1_eligible=True), _fact(task_uid="0" * 16, path="src/a.c", f1_eligible=True)]}, "task_boundary", "TR-3", "F1"),
        ({"truncations": [_fact(task_uid="0" * 16), _fact(task_uid="0" * 16)]}, "task_boundary", "TR-4", "F2"),
        ({"diagnoser_gaps": [_fact(required_file="src/a.c", modules=["a", "b"], diagnoser_call_ref=_ref("calls/d1.json", "a"), graph_confirmed=True, outside_writable_ready=True), _fact(required_file="src/a.c", modules=["a", "b"], diagnoser_call_ref=_ref("calls/d2.json", "b"), graph_confirmed=True, outside_writable_ready=True)]}, "task_boundary", "TR-5", "record_only"),
        ({"blocked_task_uids": ["0" * 16], "all_task_uids": ["0" * 16]}, "task_boundary", "TR-6", "F2"),
        ({"missing_inputs": [_fact(absent_from_blueprint=True, fits_existing_architecture=True, paths=["src/new.c"])]}, "task_boundary", "TR-7", "F3"),
        ({"export_drifts": [_fact(matches_frozen_contract=False, symbols=["api"])]}, "provider_submission", "TR-8", "submission_reject"),
    ],
)
def test_triggers_cover_tr1_through_tr8(facts, phase, code, route):
    ledger = {"schema_version": "2.0", "entries": []}
    evaluation = evaluate_revision_triggers(_boundary(facts, ledger, phase=phase), ledger)
    assert [(hit["code"], hit["route"]) for hit in evaluation["hits"]] == [(code, route)]
    assert all(hit["code"] != "TR-9" for hit in evaluation["hits"])


def test_selection_prefers_f2_then_tr_number_and_signature_excludes_volatile_evidence():
    ledger = {"schema_version": "2.0", "entries": []}
    facts = {
        "undefined_symbols": [_fact(task_uids=["0" * 16, "1" * 16], symbol="missing", declared=False, consumes_closure_complete=True)],
        "truncations": [_fact(task_uid="0" * 16), _fact(task_uid="0" * 16, evidence_refs=[_ref("evidence/other.json", "8")])],
        "blocked_task_uids": ["0" * 16], "all_task_uids": ["0" * 16],
    }
    evaluation = evaluate_revision_triggers(_boundary(facts, ledger), ledger)
    assert evaluation["selection"]["code"] == "TR-4"
    first_signature = next(hit["signature"] for hit in evaluation["hits"] if hit["code"] == "TR-4")
    replay_facts = copy.deepcopy(facts)
    replay_facts["truncations"][0]["evidence_refs"] = [_ref("evidence/new.json", "9")]
    replay = evaluate_revision_triggers(_boundary(replay_facts, ledger), ledger)
    assert next(hit["signature"] for hit in replay["hits"] if hit["code"] == "TR-4") == first_signature


def test_tr1_aggregates_distinct_task_evidence_and_signature_ignores_topological_ids():
    ledger = {"schema_version": "2.0", "entries": []}
    rows = [
        _fact(task_uids=["0" * 16], symbol="missing_symbol", declared=False, consumes_closure_complete=True),
        _fact(task_uids=["1" * 16, "T-099"], symbol="missing_symbol", declared=False, consumes_closure_complete=True),
    ]
    evaluation = evaluate_revision_triggers(_boundary({"undefined_symbols": rows}, ledger), ledger)
    hit = evaluation["hits"][0]
    assert hit["route"] == "F3"
    assert hit["signature_anchors"]["lineage_uids"] == ["0" * 16, "1" * 16]
    rows[1]["task_uids"][-1] = "T-001"
    replay = evaluate_revision_triggers(_boundary({"undefined_symbols": rows}, ledger), ledger)
    assert replay["hits"][0]["signature"] == hit["signature"]


def test_trigger_batch_is_atomic_idempotent_and_conflict_detecting():
    ledger = {"schema_version": "2.0", "entries": []}
    facts = {"truncations": [_fact(task_uid="0" * 16), _fact(task_uid="0" * 16)]}
    evaluation = evaluate_revision_triggers(_boundary(facts, ledger), ledger)
    appended = append_trigger_batch(ledger, evaluation)
    assert len(appended["entries"]) == 1
    assert append_trigger_batch(appended, evaluation) == appended
    conflict = copy.deepcopy(appended)
    conflict["entries"][0]["payload"]["reason"] = "different"
    with pytest.raises(RevisionMechanismError) as exc:
        append_trigger_batch(conflict, evaluation)
    assert exc.value.code == "REVISION_LEDGER_CONFLICT"
    no_hits = evaluate_revision_triggers(_boundary({}, ledger), ledger)
    assert append_trigger_batch(ledger, no_hits) == ledger
    malformed = copy.deepcopy(evaluation)
    malformed["hits"].append(copy.deepcopy(malformed["hits"][0]))
    with pytest.raises(RevisionMechanismError):
        append_trigger_batch(ledger, malformed)


def test_boundary_rejects_inflight_and_stale_ledger_and_threshold_near_misses():
    ledger = {"schema_version": "2.0", "entries": []}
    with pytest.raises(RevisionMechanismError) as exc:
        project_revision_boundary(
            phase="task_boundary", revision_seq=0,
            tasks=[{"task_uid": "0" * 16, "status": "in_progress"}],
            plan_ref=_ref("plan.json", "1"), epoch="E0", state_history_ref=_ref("state.json", "2"),
            workspace_commit="3" * 40, workspace_tree="4" * 40,
            blueprint_ref=_ref("blueprint.json", "5"), contract_map_ref=_ref("map.json", "6"),
            file_ledger_ref=_ref("file-ledger.json", "a"),
            revision_ledger=ledger, thresholds={"theta_2": 0.5, "theta_6": 0.5}, in_flight=True,
        )
    assert exc.value.code == "REVISION_BOUNDARY_IN_FLIGHT"

    facts = {"blocked_providers": [_fact(unique_provider=True, task_ready=True, consumer_task_uids=["0" * 16], remaining_incomplete_primary_task_uids=["0" * 16, "1" * 16, "2" * 16])], "blocked_task_uids": ["0" * 16], "all_task_uids": ["0" * 16, "1" * 16, "2" * 16]}
    boundary = _boundary(facts, ledger)
    assert evaluate_revision_triggers(boundary, ledger)["hits"] == []
    changed = append_trigger_batch(ledger, evaluate_revision_triggers(_boundary({"truncations": [_fact(task_uid="0" * 16), _fact(task_uid="0" * 16)]}, ledger), ledger))
    with pytest.raises(RevisionMechanismError) as exc:
        evaluate_revision_triggers(boundary, changed)
    assert exc.value.code == "REVISION_BOUNDARY_STALE"


def test_revision_config_is_explicit_closed_and_absent_by_default():
    assert "revision" not in load_config().snapshot
    configured = load_config(overrides={"revision": {"theta2": 0.3, "theta6": 0.25}})
    assert configured.snapshot["revision"] == {"theta2": 0.3, "theta6": 0.25}
    with pytest.raises(ConfigError, match="theta6"):
        load_config(overrides={"revision": {"theta2": 0.3}})
    with pytest.raises(ConfigError, match="greater than 0"):
        load_config(overrides={"revision": {"theta2": 0, "theta6": 0.25}})


def test_failed_attempt_boundary_records_hits_without_selecting_structural_revision():
    ledger = {"schema_version": "2.0", "entries": []}
    facts = {
        "revision_ready": False,
        "truncations": [_fact(task_uid="0" * 16), _fact(task_uid="0" * 16)],
    }
    evaluation = evaluate_revision_triggers(_boundary(facts, ledger), ledger)
    assert evaluation["hits"][0]["code"] == "TR-4"
    assert evaluation["hits"][0]["selected"] is False
    assert evaluation["selection"] is None


def test_production_fact_projection_uses_bound_attempt_build_and_config_facts(tmp_path):
    from nepa.stages.s6_execution import S6ExecutionController

    store = RunStore(tmp_path)
    ledger = {"schema_version": "2.0", "entries": []}
    store.replace_json("plan/revision_ledger.json", ledger, schema_name="revision-ledger.schema.json")
    plan_ref = {"path": "plan/versions/plan-1.0.0.json", "sha256": "a" * 64, "version": "1.0.0", "revision_seq": 0, "epoch": "E0"}
    tasks = [
        {"id": "T-001", "task_uid": "1" * 16, "work_package": "wp-a", "deliverable_files": ["src/a.c"], "depends_on": [], "consumes_contracts": [], "requirement_responsibilities": []},
        {"id": "T-002", "task_uid": "2" * 16, "work_package": "wp-b", "deliverable_files": ["src/b.c"], "depends_on": ["T-001"], "consumes_contracts": ["c-api"], "requirement_responsibilities": [{"req_id": "REQ-1", "role": "primary"}]},
        {"id": "T-003", "task_uid": "3" * 16, "work_package": "wp-c", "deliverable_files": ["src/c1.c", "src/c2.c", "src/c3.c", "src/c4.c"], "depends_on": [], "consumes_contracts": [], "requirement_responsibilities": []},
    ]
    plan = {"tasks": tasks, "architecture": {"contracts": [], "layout": {"roots": {"source": "src", "include": "include"}}}}
    state = {"plan_ref": plan_ref, "tasks": [
        {"id": "T-001", "task_uid": "1" * 16, "status": "blocked", "attempts": 0, "last_error": None},
        {"id": "T-002", "task_uid": "2" * 16, "status": "pending", "attempts": 0, "last_error": None},
        {"id": "T-003", "task_uid": "3" * 16, "status": "in_progress", "attempts": 2, "last_error": None},
    ]}
    for attempt in (1, 2):
        failure = {
            "attempt": attempt, "code": "AGENT_FAILURE", "detail": "not inferred from this text",
            "diagnosis": {"f1_eligible": True, "fits_existing_architecture": True},
            "controller_facts": {"finish_reason": "length"},
        }
        failure_ref = store.publish_immutable_json(f"attempts/{'3' * 16}/attempt_{attempt:03d}/failure.json", failure)
        store.replace_json(f"attempts/{'3' * 16}/attempt_{attempt:03d}.json", {
            "schema_version": "2.0", "task_id": "T-003", "task_uid": "3" * 16,
            "execution_mode": "normal", "attempt": attempt, "evidence_seq": attempt,
            "role": "coder" if attempt == 1 else "fixer", "tier": "T2", "plan_ref": plan_ref,
            "migration_ref": None, "baseline_commit": "b" * 40, "baseline_tree": "c" * 64,
            "status": "failed", "output_ref": None, "failure_ref": failure_ref.as_dict(),
        }, schema_name="s6-attempt.schema.json")
    blueprint = {"file_rules": [
        {"path_pattern": path, "expansion": "none", "mutability": "s6_owned", "owner_task_id": "T-003"}
        for path in tasks[2]["deliverable_files"]
    ], "link_source_sets": [], "build_artifacts": []}
    contract_map = {"contracts": [{"contract_id": "c-api", "ready_gate": "task", "provider_task_id": "T-001"}]}
    config = load_config(overrides={"revision": {"theta2": 0.5, "theta6": 0.5}}).snapshot
    facts = S6ExecutionController._revision_facts(store, state, plan, blueprint, contract_map, config)
    assert len(facts["truncations"]) == 2
    assert facts["lint_output_overflows"][0]["projected_output_tokens"] > facts["lint_output_overflows"][0]["frozen_output_tokens"]
    assert facts["blocked_providers"][0]["consumer_task_uids"] == ["2" * 16]
    assert "write_rejections" not in facts and "missing_inputs" not in facts


def test_production_fact_projection_requires_current_hashed_build_refs(tmp_path):
    from nepa.stages.s6_execution import S6ExecutionController

    store = RunStore(tmp_path)
    store.replace_json("plan/revision_ledger.json", {"schema_version": "2.0", "entries": []}, schema_name="revision-ledger.schema.json")
    uid = "1" * 16
    plan_ref = {"path": "plan/versions/plan-1.0.0.json", "sha256": "a" * 64, "version": "1.0.0", "revision_seq": 0, "epoch": "E0"}
    build = {
        "schema_version": "1.0", "tree_sha256": "b" * 64, "variant": "san", "command": ["make"],
        "exit_code": 1, "stdout": "", "stderr": "undefined reference to `missing_api'\nfatal error: src/new.c: No such file",
        "duration_ms": 1, "timed_out": False, "artifacts": [], "status": "failed",
    }
    build_ref = store.publish_immutable_json(f"attempts/{uid}/attempt_001/build_san.json", build, schema_name="build-result.schema.json")
    failure = {
        "attempt": 1, "code": "CANDIDATE_VALIDATION_FAILED", "detail": "build failed",
        "diagnosis": {"undefined_symbol": True}, "controller_facts": {"build_result_refs": [build_ref.as_dict()]},
    }
    failure_ref = store.publish_immutable_json(f"attempts/{uid}/attempt_001/failure.json", failure)
    attempt = {
        "schema_version": "2.0", "task_id": "T-001", "task_uid": uid, "execution_mode": "normal",
        "attempt": 1, "evidence_seq": 1, "role": "coder", "tier": "T2", "plan_ref": plan_ref,
        "migration_ref": None, "baseline_commit": "c" * 40, "baseline_tree": "d" * 64,
        "status": "failed", "output_ref": None, "failure_ref": failure_ref.as_dict(),
    }
    store.replace_json(f"attempts/{uid}/attempt_001.json", attempt, schema_name="s6-attempt.schema.json")
    plan = {
        "tasks": [{"id": "T-001", "task_uid": uid, "work_package": "wp-a", "deliverable_files": ["src/a.c"], "depends_on": [], "consumes_contracts": [], "requirement_responsibilities": []}],
        "work_packages": [{"id": "wp-a", "module": "module-a", "allowed_files": ["src/a.c"]}],
        "architecture": {"contracts": [], "modules": [{"id": "module-a", "owns_files": ["src/a.c"]}], "layout": {"roots": {"source": "src"}}},
    }
    state = {"plan_ref": plan_ref, "tasks": [{"id": "T-001", "task_uid": uid, "status": "blocked", "attempts": 1}]}
    facts = S6ExecutionController._revision_facts(store, state, plan, {"file_rules": [], "build_artifacts": []}, {"contracts": []}, load_config().snapshot)
    assert facts["undefined_symbols"][0]["symbol"] == "missing_api"
    assert facts["missing_inputs"][0]["fits_existing_architecture"] is True

    tampered = copy.deepcopy(failure)
    tampered["controller_facts"]["build_result_refs"][0]["sha256"] = "e" * 64
    tampered_ref = store.publish_immutable_json(f"attempts/{uid}/attempt_002/failure.json", {**tampered, "attempt": 2})
    attempt.update({"attempt": 2, "evidence_seq": 2, "failure_ref": tampered_ref.as_dict()})
    store.replace_json(f"attempts/{uid}/attempt_002.json", attempt, schema_name="s6-attempt.schema.json")
    state["tasks"][0]["attempts"] = 2
    replay = S6ExecutionController._revision_facts(store, state, plan, {"file_rules": [], "build_artifacts": []}, {"contracts": []}, load_config().snapshot)
    assert len(replay["undefined_symbols"]) == 1

    stale_failure_ref = store.publish_immutable_json(
        f"attempts/{uid}/attempt_003/failure.json",
        {"attempt": 3, "code": "AGENT_FAILURE", "detail": "stale", "diagnosis": {}, "controller_facts": {"finish_reason": "length"}},
    )
    stale_attempt = copy.deepcopy(attempt)
    stale_attempt.update({
        "attempt": 3, "evidence_seq": 3,
        "plan_ref": {**plan_ref, "revision_seq": 1, "version": "1.0.1", "path": "plan/versions/plan-1.0.1.json"},
        "failure_ref": stale_failure_ref.as_dict(),
    })
    store.replace_json(f"attempts/{uid}/attempt_003.json", stale_attempt, schema_name="s6-attempt.schema.json")
    started = copy.deepcopy(attempt)
    started.update({"attempt": 4, "evidence_seq": 4, "status": "started", "failure_ref": None})
    store.replace_json(f"attempts/{uid}/attempt_004.json", started, schema_name="s6-attempt.schema.json")
    state["tasks"][0]["attempts"] = 4
    final = S6ExecutionController._revision_facts(store, state, plan, {"file_rules": [], "build_artifacts": []}, {"contracts": []}, load_config().snapshot)
    assert "truncations" not in final
    assert len(final["undefined_symbols"]) == 1


def test_inv3_rejects_unrelated_task_mapping_and_weaker_test_gate():
    def task(task_id, uid, *, builds):
        return {
            "id": task_id, "task_uid": uid, "deliverable_files": [f"src/{task_id}.c"],
            "requirement_responsibilities": [], "provides_contracts": [], "consumes_contracts": [],
            "acceptance": {"build_variant_ids": builds, "tests": []},
        }

    uid_a, uid_b = "a" * 16, "b" * 16
    source = {
        "tasks": [task("T-001", uid_a, builds=["san"]), task("T-002", uid_b, builds=["release"])],
        "coverage": {"tests": [{"nodeid": "tests/test_a.py::test_a", "gate": "task", "enabled": True, "task_id": "T-001"}]},
    }
    candidate = copy.deepcopy(source)
    candidate["tasks"][0]["acceptance"]["build_variant_ids"] = ["release"]
    candidate["tasks"][1]["acceptance"]["build_variant_ids"] = ["release", "san"]
    mapping = [{
        "old_task_uid": uid_a, "old_obligation": "build:san",
        "new_targets": [{"new_task_uid": uid_b, "new_obligation": "build:san"}],
        "acceptance_not_weaker": True,
    }]
    with pytest.raises(RevisionMechanismError, match="unrelated task"):
        _validate_obligation_preservation(source, candidate, [], mapping, [])

    candidate = copy.deepcopy(source)
    candidate["coverage"]["tests"][0]["gate"] = "s7_only"
    with pytest.raises(RevisionMechanismError, match="acceptance gate"):
        _validate_obligation_preservation(source, candidate, [], [], [])

    candidate = copy.deepcopy(source)
    candidate["coverage"]["tests"][0]["gate"] = "s5"
    _validate_obligation_preservation(source, candidate, [], [], [])


def test_f3_migration_groups_stay_separate_until_they_share_an_artifact():
    uid_a, uid_b = "a" * 16, "b" * 16
    candidate = {"tasks": [
        {"id": "T-001", "task_uid": uid_a, "depends_on": [], "deliverable_files": ["src/a.c"], "consumes_contracts": []},
        {"id": "T-002", "task_uid": uid_b, "depends_on": [], "deliverable_files": ["src/b.c"], "consumes_contracts": []},
    ], "architecture": {"layout": {"files": []}, "contracts": []}}
    patch = {"level": "F3", "patch_ops": [
        {"op": "add_work_package", "work_package": {"allowed_files": ["src/a.c"]}},
        {"op": "add_work_package", "work_package": {"allowed_files": ["src/b.c"]}},
    ]}
    migration = {"tasks": [
        {"new_task_uid": uid_a, "classification": "REGENERATE"},
        {"new_task_uid": uid_b, "classification": "REGENERATE"},
    ]}
    blueprint = {
        "file_rules": [
            {"id": "a", "path_pattern": "src/a.c", "expansion": "none"},
            {"id": "b", "path_pattern": "src/b.c", "expansion": "none"},
        ],
        "link_source_sets": [{"id": "as", "file_rule_ids": ["a"]}, {"id": "bs", "file_rule_ids": ["b"]}],
        "build_artifacts": [
            {"id": "artifact-a", "link_source_set_id": "as", "entry_file_slot": "a"},
            {"id": "artifact-b", "link_source_set_id": "bs", "entry_file_slot": "b"},
        ],
    }
    _migration_extensions(patch, candidate, blueprint, migration, revision_seq=1)
    assert [row["build_artifact_ids"] for row in migration["pending_groups"]] == [["artifact-a"], ["artifact-b"]]

    shared = copy.deepcopy(blueprint)
    shared["link_source_sets"].append({"id": "both", "file_rule_ids": ["a", "b"]})
    shared["build_artifacts"].append({"id": "artifact-shared", "link_source_set_id": "both", "entry_file_slot": "a"})
    merged = copy.deepcopy({"tasks": migration["tasks"]})
    _migration_extensions(patch, candidate, shared, merged, revision_seq=1)
    assert len(merged["pending_groups"]) == 1
    assert merged["pending_groups"][0]["build_artifact_ids"] == ["artifact-a", "artifact-b", "artifact-shared"]

    unattributable = copy.deepcopy(patch)
    unattributable["patch_ops"][0]["work_package"]["allowed_files"] = ["src/missing.c"]
    with pytest.raises(RevisionMechanismError, match="no attributable task"):
        _migration_extensions(unattributable, candidate, blueprint, copy.deepcopy({"tasks": migration["tasks"]}), revision_seq=1)


def test_same_level_rejection_deduplicates_selection_but_preserves_hit():
    ledger = {"schema_version": "2.0", "entries": []}
    facts = {"truncations": [_fact(task_uid="0" * 16), _fact(task_uid="0" * 16)]}
    evaluation = evaluate_revision_triggers(_boundary(facts, ledger), ledger)
    ledger = append_trigger_batch(ledger, evaluation)
    ledger["entries"].append(build_event_entry(ledger, "candidate_rejected", {
        "candidate_id": "candidate-1", "trigger_event_seq": 1, "level": "F2", "failed_gate": "RG-1",
        "reason": "fixture rejection", "evidence_refs": [],
    }))
    replay = evaluate_revision_triggers(_boundary(facts, ledger), ledger)
    assert replay["hits"][0]["code"] == "TR-4"
    assert replay["selection"] is None
    assert replay["hits"][0]["selected"] is False


def _patch(source_ir, uid):
    return {
        "schema_version": "1.0",
        "source": {"plan_ref": _ref("plan/versions/plan-1.0.0.json", "1"), "revision_seq": 0, "plan_draft_ir_sha256": hashlib.sha256(canonical_json_bytes(source_ir)).hexdigest()},
        "selected_trigger": {"event_seq": 1, "code": "TR-4", "signature": "2" * 64},
        "level": "F2",
        "patch_ops": [{"op": "rewrite_instructions", "task_uid": uid, "changes": {"instructions": "Rewritten without changing obligations."}}],
        "lineage": [],
        "obligation_mappings": [],
        "rationale": "bounded rewrite", "expected_effect": "guidance-only delta",
    }


def _source_with_extra_codec_file():
    plan, _blueprint, _spec, _manifest, _constraints, _target = _linked()
    source = plan_to_draft_ir(plan)
    extra_path = "src/codec/extra.c"
    slot = {
        "slot_id": "codec-extra", "path": extra_path, "path_pattern": None, "expand_over": None,
        "class": "s6_owned", "render_rule": "source_stub", "owner_module": "codec",
        "contract_id": None, "build_role": "none", "purpose": "Additional codec implementation",
    }
    source["architecture"]["layout"]["files"].append(slot)
    next(module for module in source["architecture"]["modules"] if module["id"] == "codec")["owns_files"].append(extra_path)
    next(package for package in source["architecture"]["work_packages"] if package["id"] == "wp-codec")["allowed_files"].append(extra_path)
    next(package for package in source["work_packages"] if package["id"] == "wp-codec")["allowed_files"].append(extra_path)
    shard = next(item for item in source["task_shards"] if item["work_package_id"] == "wp-codec")
    shard["tasks"][0]["deliverable_files"].append(extra_path)
    return normalize_plan_draft(source["architecture"], source["work_packages"], source["task_shards"])


def test_patch_rewrite_is_atomic_and_replay_stable():
    plan, _blueprint, _spec, _manifest, _constraints, _target = _linked()
    source_ir = plan_to_draft_ir(plan)
    uid = plan["tasks"][0]["task_uid"]
    patch = _patch(source_ir, uid)
    left = apply_revision_patch(source_ir, patch)
    right = apply_revision_patch(source_ir, patch)
    assert left == right
    assert left["task_shards"][0]["tasks"][0]["instructions"] == "Rewritten without changing obligations."
    broken = copy.deepcopy(patch)
    broken["patch_ops"].append({"op": "rewrite_instructions", "task_uid": "f" * 16, "changes": {"goal": "Missing"}})
    with pytest.raises(RevisionMechanismError):
        apply_revision_patch(source_ir, broken)
    assert source_ir != left


@pytest.mark.parametrize("bad_index", [0, 1, 2])
def test_patch_failure_at_any_position_leaves_source_unchanged(bad_index):
    plan, _blueprint, _spec, _manifest, _constraints, _target = _linked()
    source = plan_to_draft_ir(plan)
    before = canonical_json_bytes(source)
    uid = plan["tasks"][0]["task_uid"]
    valid = {"op": "rewrite_instructions", "task_uid": uid, "changes": {"instructions": "Still bounded."}}
    invalid = {"op": "rewrite_instructions", "task_uid": "f" * 16, "changes": {"goal": "Unknown task"}}
    patch = _patch(source, uid)
    patch["patch_ops"] = [copy.deepcopy(valid), copy.deepcopy(valid)]
    patch["patch_ops"].insert(bad_index, invalid)
    with pytest.raises(RevisionMechanismError):
        apply_revision_patch(source, patch)
    assert canonical_json_bytes(source) == before


def test_split_merge_and_insert_preserve_package_file_partition():
    source = _source_with_extra_codec_file()
    shard = next(item for item in source["task_shards"] if item["work_package_id"] == "wp-codec")
    original = shard["tasks"][0]
    uid = derive_task_metadata(original, work_package_id="wp-codec", local_task_id=original["local_id"])["task_uid"]
    left = copy.deepcopy(original); left["local_id"] = "codec-left"; left["deliverable_files"] = ["src/codec/codec.c"]
    right = copy.deepcopy(original); right["local_id"] = "codec-right"; right["deliverable_files"] = ["src/codec/extra.c"]; right["consumes_contracts"] = []
    left_uid = derive_task_metadata(left, work_package_id="wp-codec", local_task_id=left["local_id"])["task_uid"]
    right_uid = derive_task_metadata(right, work_package_id="wp-codec", local_task_id=right["local_id"])["task_uid"]
    split = _patch(source, uid)
    split["patch_ops"] = [{"op": "split_task", "work_package_id": "wp-codec", "source_task_uid": uid, "successors": [left, right]}]
    split["lineage"] = [{"old_task_uid": uid, "new_task_uids": [left_uid, right_uid]}]
    split_result = apply_revision_patch(source, split)
    split_tasks = next(item for item in split_result["task_shards"] if item["work_package_id"] == "wp-codec")["tasks"]
    assert {task["local_id"] for task in split_tasks} == {"codec-left", "codec-right"}

    inserted = copy.deepcopy(right); inserted["local_id"] = "codec-inserted"
    insert_patch = _patch(source, uid)
    insert_patch["patch_ops"] = [{"op": "insert_task", "work_package_id": "wp-codec", "from_task_uid": uid, "task": inserted}]
    insert_result = apply_revision_patch(source, insert_patch)
    assert len(next(item for item in insert_result["task_shards"] if item["work_package_id"] == "wp-codec")["tasks"]) == 2

    merged = copy.deepcopy(original); merged["local_id"] = "codec-merged"
    merge_patch = _patch(split_result, left_uid)
    merge_patch["patch_ops"] = [{"op": "merge_tasks", "work_package_id": "wp-codec", "source_task_uids": [left_uid, right_uid], "successor": merged}]
    merge_patch["lineage"] = [{"old_task_uid": left_uid, "new_task_uids": [derive_task_metadata(merged, work_package_id="wp-codec", local_task_id=merged["local_id"])["task_uid"]]}, {"old_task_uid": right_uid, "new_task_uids": [derive_task_metadata(merged, work_package_id="wp-codec", local_task_id=merged["local_id"])["task_uid"]]}]
    merge_result = apply_revision_patch(split_result, merge_patch)
    assert [task["local_id"] for task in next(item for item in merge_result["task_shards"] if item["work_package_id"] == "wp-codec")["tasks"]] == ["codec-merged"]


def test_f3_slot_contract_extension_retirement_and_re_adoption_are_explicit():
    plan, _blueprint, _spec, _manifest, _constraints, _target = _linked()
    source = plan_to_draft_ir(plan)
    codec_task = next(task for shard in source["task_shards"] if shard["work_package_id"] == "wp-codec" for task in shard["tasks"])
    uid = derive_task_metadata(codec_task, work_package_id="wp-codec", local_task_id=codec_task["local_id"])["task_uid"]
    codec_requirement = next(
        responsibility["req_id"]
        for shard in source["task_shards"]
        for task in shard["tasks"]
        for responsibility in task["requirement_responsibilities"]
    )
    slot = {
        "slot_id": "codec-new", "path": "src/codec/new.c", "path_pattern": None, "expand_over": None,
        "class": "s6_owned", "render_rule": "source_stub", "owner_module": "codec", "contract_id": None,
        "build_role": "none", "purpose": "New legal codec slot",
    }
    patch = _patch(source, uid); patch["level"] = "F3"; patch["selected_trigger"]["code"] = "TR-7"
    patch["patch_ops"] = [
        {"op": "add_file_slot", "file_slot": slot, "work_package_id": "wp-codec", "owner_task_uid": uid, "build_artifact_ids": []},
        {"op": "extend_contract", "contract_id": "interface-contract", "exports": [{"interface_file": "include/orbitnet/interface.h", "symbol": "orbitnet_extra", "signature": "int orbitnet_extra(void);"}], "implementation_slot_ids": ["codec-new"], "provider_work_package_id": "wp-codec", "consumer_work_package_ids": ["wp-entry"]},
    ]
    result = apply_revision_patch(source, patch)
    assert any(row["slot_id"] == "codec-new" for row in result["architecture"]["layout"]["files"])
    contract = next(row for row in result["architecture"]["contracts"] if row["id"] == "interface-contract")
    assert any(row["symbol"] == "orbitnet_extra" for row in contract["exports"])

    retire = _patch(result, uid); retire["level"] = "F3"; retire["selected_trigger"]["code"] = "TR-7"
    retire["patch_ops"] = [{"op": "retire_file_slot", "slot_id": "codec-new", "path": "src/codec/new.c", "successor_slot_ids": ["codec-source"], "successor_obligations": [codec_requirement], "successor_symbols": ["orbitnet_extra"], "quarantine_path": "_orphan/E1/src/codec/new.c"}]
    retired = apply_revision_patch(result, retire)
    assert not any(row["slot_id"] == "codec-new" for row in retired["architecture"]["layout"]["files"])

    readopt = _patch(retired, uid); readopt["level"] = "F3"; readopt["selected_trigger"]["code"] = "TR-7"
    readopt["patch_ops"] = [{"op": "re_adopt", "quarantine_path": "_orphan/E1/src/codec/new.c", "content_sha256": "a" * 64, "target_slot": slot, "work_package_id": "wp-codec", "owner_task_uid": uid, "revalidation_obligations": [codec_requirement], "build_artifact_ids": []}]
    restored = apply_revision_patch(retired, readopt)
    assert any(row["slot_id"] == "codec-new" for row in restored["architecture"]["layout"]["files"])

    no_structure = _patch(source, uid); no_structure["level"] = "F3"
    with pytest.raises(RevisionMechanismError):
        apply_revision_patch(source, no_structure)


def test_move_operators_and_dependency_reorder_apply_against_stable_ids():
    plan, _blueprint, _spec, _manifest, _constraints, _target = _linked()
    source = plan_to_draft_ir(plan)
    entry_module = next(row for row in source["architecture"]["modules"] if row["id"] == "entry")
    architecture_package = next(row for row in source["architecture"]["work_packages"] if row["id"] == "wp-entry")
    package = next(row for row in source["work_packages"] if row["id"] == "wp-entry")
    shard = next(row for row in source["task_shards"] if row["work_package_id"] == "wp-entry")
    original = shard["tasks"][0]
    helper_paths = ["apps/helper.c", "apps/helper_extra.c"]
    for index, path in enumerate(helper_paths, start=1):
        source["architecture"]["layout"]["files"].append({
            "slot_id": f"entry-helper-{index}", "path": path, "path_pattern": None, "expand_over": None,
            "class": "s6_owned", "render_rule": "source_stub", "owner_module": "entry",
            "contract_id": None, "build_role": "none", "purpose": "Entry helper",
        })
    entry_module["owns_files"].extend(helper_paths)
    architecture_package["allowed_files"].extend(helper_paths)
    package["allowed_files"].extend(helper_paths)
    contract = {
        "id": "entry-local", "purpose": "Entry-local dependency", "owner": "entry",
        "interface_files": ["include/orbitnet/interface.h"],
        "exports": [{"interface_file": "include/orbitnet/interface.h", "symbol": "entry_local", "signature": "int entry_local(void);"}],
        "ready_gate": "task", "provider": "entry", "consumers": ["entry"],
    }
    source["architecture"]["contracts"].append(contract)
    entry_module["provides_contracts"].append("entry-local")
    entry_module["consumes_contracts"].append("entry-local")
    for row in (architecture_package, package):
        row["provides_contracts"].append("entry-local")
        row["consumes_contracts"].append("entry-local")
    provider = copy.deepcopy(original)
    provider["local_id"] = "provider"
    provider["deliverable_files"] = ["apps/app_main.c"]
    provider["requirement_responsibilities"] = [original["requirement_responsibilities"][0]]
    provider["provides_contracts"] = ["entry-local"]
    consumer = copy.deepcopy(original)
    consumer["local_id"] = "consumer"
    consumer["deliverable_files"] = helper_paths
    consumer["requirement_responsibilities"] = [original["requirement_responsibilities"][1]]
    consumer["consumes_contracts"] = ["entry-local", "interface-contract"]
    shard["tasks"] = [provider, consumer]
    source = normalize_plan_draft(source["architecture"], source["work_packages"], source["task_shards"])
    provider_uid = derive_task_metadata(provider, work_package_id="wp-entry", local_task_id="provider")["task_uid"]
    consumer_uid = derive_task_metadata(consumer, work_package_id="wp-entry", local_task_id="consumer")["task_uid"]
    patch = _patch(source, provider_uid)
    patch["patch_ops"] = [
        {"op": "move_responsibility", "work_package_id": "wp-entry", "req_id": original["requirement_responsibilities"][0]["req_id"], "role": "primary", "from_task_uid": provider_uid, "to_task_uid": consumer_uid},
        {"op": "move_file_owner", "work_package_id": "wp-entry", "path": helper_paths[0], "from_task_uid": consumer_uid, "to_task_uid": provider_uid},
        {"op": "reorder_dependency", "work_package_id": "wp-entry", "from_task_uid": provider_uid, "to_task_uid": consumer_uid, "contract_id": "entry-local"},
    ]
    result = apply_revision_patch(source, patch)
    tasks = {task["local_id"]: task for row in result["task_shards"] if row["work_package_id"] == "wp-entry" for task in row["tasks"]}
    assert helper_paths[0] in tasks["provider"]["deliverable_files"]
    assert tasks["consumer"]["depends_on"] == ["provider"]
    assert len(tasks["consumer"]["requirement_responsibilities"]) == 2


def test_add_contract_work_package_and_cross_package_move_are_closed():
    plan, _blueprint, _spec, _manifest, _constraints, _target = _linked()
    source = plan_to_draft_ir(plan)
    codec_uid = plan["tasks"][0]["task_uid"]
    new_contract = {
        "id": "codec-api", "purpose": "Task-ready codec API", "owner": "codec",
        "interface_files": ["include/orbitnet/interface.h"],
        "exports": [{"interface_file": "include/orbitnet/interface.h", "symbol": "codec_api", "signature": "int codec_api(void);"}],
        "ready_gate": "task", "provider": "codec", "consumers": ["entry"],
    }
    add_contract = _patch(source, codec_uid)
    add_contract["level"] = "F3"
    add_contract["selected_trigger"]["code"] = "TR-7"
    add_contract["patch_ops"] = [{
        "op": "add_contract", "contract": new_contract, "implementation_slot_id": "codec-source",
        "provider_work_package_id": "wp-codec", "consumer_work_package_ids": ["wp-entry"],
    }]
    with_contract = apply_revision_patch(source, add_contract)
    assert any(row["id"] == "codec-api" for row in with_contract["architecture"]["contracts"])

    source = _source_with_extra_codec_file()
    second_path = "src/codec/extra_two.c"
    source["architecture"]["layout"]["files"].append({
        "slot_id": "codec-extra-two", "path": second_path, "path_pattern": None, "expand_over": None,
        "class": "s6_owned", "render_rule": "source_stub", "owner_module": "codec", "contract_id": None,
        "build_role": "none", "purpose": "Second codec helper",
    })
    next(row for row in source["architecture"]["modules"] if row["id"] == "codec")["owns_files"].append(second_path)
    next(row for row in source["architecture"]["work_packages"] if row["id"] == "wp-codec")["allowed_files"].append(second_path)
    next(row for row in source["work_packages"] if row["id"] == "wp-codec")["allowed_files"].append(second_path)
    old_task = next(task for row in source["task_shards"] if row["work_package_id"] == "wp-codec" for task in row["tasks"])
    old_task["deliverable_files"].append(second_path)
    source = normalize_plan_draft(source["architecture"], source["work_packages"], source["task_shards"])
    old_uid = derive_task_metadata(old_task, work_package_id="wp-codec", local_task_id=old_task["local_id"])["task_uid"]
    moved_task = copy.deepcopy(old_task)
    moved_task["local_id"] = "extra-owner"
    package_path = "src/codec/package.c"
    moved_task["deliverable_files"] = [package_path]
    moved_task["consumes_contracts"] = ["interface-contract"]
    new_package = {
        "id": "wp-codec-extra", "title": "Codec extra", "goal": "Own codec helper work.", "module": "codec",
        "kind": "implementation", "context_refs": [], "requirement_responsibilities": [],
        "allowed_files": [package_path], "provides_contracts": [],
        "consumes_contracts": ["interface-contract"], "depends_on": [], "acceptance": {"outcome": "Helper is complete."},
    }
    add_package = _patch(source, old_uid)
    add_package["level"] = "F3"
    add_package["selected_trigger"]["code"] = "TR-7"
    new_uid = derive_task_metadata(moved_task, work_package_id="wp-codec-extra", local_task_id=moved_task["local_id"])["task_uid"]
    add_package["patch_ops"] = [
        {"op": "add_work_package", "work_package": new_package, "tasks": [moved_task]},
        {"op": "add_file_slot", "file_slot": {
            "slot_id": "codec-package", "path": package_path, "path_pattern": None, "expand_over": None,
            "class": "s6_owned", "render_rule": "source_stub", "owner_module": "codec", "contract_id": None,
            "build_role": "none", "purpose": "New package implementation",
        }, "work_package_id": "wp-codec-extra", "owner_task_uid": new_uid, "build_artifact_ids": []},
    ]
    split_packages = apply_revision_patch(source, add_package)
    current_old = next(task for row in split_packages["task_shards"] if row["work_package_id"] == "wp-codec" for task in row["tasks"])
    current_new = next(task for row in split_packages["task_shards"] if row["work_package_id"] == "wp-codec-extra" for task in row["tasks"])
    current_old_uid = derive_task_metadata(current_old, work_package_id="wp-codec", local_task_id=current_old["local_id"])["task_uid"]
    current_new_uid = derive_task_metadata(current_new, work_package_id="wp-codec-extra", local_task_id=current_new["local_id"])["task_uid"]
    move = _patch(split_packages, current_old_uid)
    move["level"] = "F3"
    move["selected_trigger"]["code"] = "TR-7"
    move["patch_ops"] = [{
        "op": "move_file_across_wp", "path": second_path, "from_work_package_id": "wp-codec",
        "to_work_package_id": "wp-codec-extra", "from_task_uid": current_old_uid, "to_task_uid": current_new_uid,
        "responsibility_ids": [], "provider_contract_ids": [],
    }]
    moved = apply_revision_patch(split_packages, move)
    target = next(task for row in moved["task_shards"] if row["work_package_id"] == "wp-codec-extra" for task in row["tasks"])
    assert second_path in target["deliverable_files"]


def test_f3_rejects_unrelated_f2_companion_and_invalid_structural_bindings():
    plan, _blueprint, _spec, _manifest, _constraints, _target = _linked()
    source = plan_to_draft_ir(plan)
    codec_task = next(task for row in source["task_shards"] if row["work_package_id"] == "wp-codec" for task in row["tasks"])
    entry_task = next(task for row in source["task_shards"] if row["work_package_id"] == "wp-entry" for task in row["tasks"])
    codec_uid = derive_task_metadata(codec_task, work_package_id="wp-codec", local_task_id=codec_task["local_id"])["task_uid"]
    entry_uid = derive_task_metadata(entry_task, work_package_id="wp-entry", local_task_id=entry_task["local_id"])["task_uid"]
    slot = {
        "slot_id": "codec-new", "path": "src/codec/new.c", "path_pattern": None, "expand_over": None,
        "class": "s6_owned", "render_rule": "source_stub", "owner_module": "codec", "contract_id": None,
        "build_role": "none", "purpose": "New codec slot",
    }
    patch = _patch(source, codec_uid)
    patch["level"] = "F3"
    patch["selected_trigger"]["code"] = "TR-7"
    patch["patch_ops"] = [
        {"op": "add_file_slot", "file_slot": slot, "work_package_id": "wp-codec", "owner_task_uid": codec_uid, "build_artifact_ids": []},
        {"op": "rewrite_instructions", "task_uid": entry_uid, "changes": {"goal": "Unrelated entry rewrite"}},
    ]
    with pytest.raises(RevisionMechanismError, match="same structural delta"):
        apply_revision_patch(source, patch)

    invalid_slot = copy.deepcopy(patch)
    invalid_slot["patch_ops"] = [{
        "op": "add_file_slot", "file_slot": {**slot, "class": "s5_frozen"},
        "work_package_id": "wp-codec", "owner_task_uid": codec_uid, "build_artifact_ids": [],
    }]
    with pytest.raises(RevisionMechanismError, match="owner binding"):
        apply_revision_patch(source, invalid_slot)

    duplicate_export = copy.deepcopy(patch)
    duplicate_export["patch_ops"] = [{
        "op": "extend_contract", "contract_id": "interface-contract",
        "exports": [copy.deepcopy(source["architecture"]["contracts"][0]["exports"][0])],
        "implementation_slot_ids": ["codec-source"], "provider_work_package_id": "wp-codec",
        "consumer_work_package_ids": ["wp-entry"],
    }]
    with pytest.raises(RevisionMechanismError, match="may not replace"):
        apply_revision_patch(source, duplicate_export)


def test_patch_schema_covers_every_closed_operator_discriminator():
    from nepa.schemas import load_example, load_schema

    base = load_example("revision-patch.example.json")
    uid_a, uid_b = "0" * 16, "1" * 16
    plan, _blueprint, _spec, _manifest, _constraints, _target = _linked()
    contract = copy.deepcopy(plan["architecture"]["contracts"][0]); contract.pop("provider_task_id", None); contract["id"] = "new-api"
    slot = copy.deepcopy(plan["architecture"]["layout"]["files"][0]); slot["slot_id"] = "new-slot"
    work_package = copy.deepcopy(plan["work_packages"][0]); work_package["id"] = "wp-new"
    task = {
        "local_id": "new", "title": "New", "goal": "Do work", "kind": "logic", "instructions": "Do work.",
        "deliverable_files": ["src/new.c"], "context_refs": [], "requirement_responsibilities": [],
        "provides_contracts": [], "consumes_contracts": [], "depends_on": [],
        "acceptance": {"build_variant_ids": ["san"], "tests": []},
    }
    operations = [
        ("F2", {"op": "split_task", "work_package_id": "wp-a", "source_task_uid": uid_a, "successors": [task, {**task, "local_id": "new-b", "deliverable_files": ["src/new-b.c"]}]}),
        ("F2", {"op": "merge_tasks", "work_package_id": "wp-a", "source_task_uids": [uid_a, uid_b], "successor": task}),
        ("F2", {"op": "move_responsibility", "work_package_id": "wp-a", "req_id": "REQ-1", "role": "primary", "from_task_uid": uid_a, "to_task_uid": uid_b}),
        ("F2", {"op": "move_file_owner", "work_package_id": "wp-a", "path": "src/a.c", "from_task_uid": uid_a, "to_task_uid": uid_b}),
        ("F2", {"op": "rewrite_instructions", "task_uid": uid_a, "changes": {"goal": "Bounded goal"}}),
        ("F2", {"op": "insert_task", "work_package_id": "wp-a", "from_task_uid": uid_a, "task": task}),
        ("F2", {"op": "reorder_dependency", "work_package_id": "wp-a", "from_task_uid": uid_a, "to_task_uid": uid_b, "contract_id": "api"}),
        ("F3", {"op": "add_contract", "contract": contract, "implementation_slot_id": "slot", "provider_work_package_id": "wp-a", "consumer_work_package_ids": ["wp-b"]}),
        ("F3", {"op": "extend_contract", "contract_id": "api", "exports": [{"interface_file": "include/new.h", "symbol": "new_api", "signature": "int new_api(void);"}], "implementation_slot_ids": ["slot"], "provider_work_package_id": "wp-a", "consumer_work_package_ids": ["wp-b"]}),
        ("F3", {"op": "add_file_slot", "file_slot": slot, "work_package_id": "wp-a", "owner_task_uid": uid_a, "build_artifact_ids": []}),
        ("F3", {"op": "add_work_package", "work_package": work_package, "tasks": [task]}),
        ("F3", {"op": "move_file_across_wp", "path": "src/a.c", "from_work_package_id": "wp-a", "to_work_package_id": "wp-b", "from_task_uid": uid_a, "to_task_uid": uid_b, "responsibility_ids": [], "provider_contract_ids": []}),
        ("F3", {"op": "retire_file_slot", "slot_id": "slot", "path": "src/a.c", "successor_slot_ids": ["slot-b"], "successor_obligations": ["REQ-1"], "successor_symbols": [], "quarantine_path": "_orphan/E1/src/a.c"}),
        ("F3", {"op": "re_adopt", "quarantine_path": "_orphan/E1/src/a.c", "content_sha256": "a" * 64, "target_slot": slot, "work_package_id": "wp-a", "owner_task_uid": uid_a, "revalidation_obligations": ["REQ-1"], "build_artifact_ids": []}),
    ]
    validator = Draft202012Validator(load_schema("revision-patch.schema.json"))
    for level, operation in operations:
        patch = copy.deepcopy(base); patch["level"] = level; patch["patch_ops"] = [operation]
        assert not list(validator.iter_errors(patch)), operation["op"]
    invalid = copy.deepcopy(base)
    invalid["patch_ops"] = [{"op": "insert_task", "work_package_id": "wp-a", "from_task_uid": uid_a, "task": {**task, "status": "pending"}}]
    assert list(validator.iter_errors(invalid))


def test_candidate_validation_binds_event_identity_and_all_content_hashes():
    from nepa.schemas import load_example

    candidate = load_example("revision-candidate.example.json")
    assert validate_revision_candidate(candidate) == candidate
    candidate["candidate_id"] = "candidate-2"
    with pytest.raises(RevisionMechanismError) as exc:
        validate_revision_candidate(candidate)
    assert exc.value.code == "REVISION_CANDIDATE_BINDING_INVALID"


def test_complete_revision_candidate_uses_shared_completion_and_invariants():
    plan, _blueprint, spec, manifest, constraints, target = _linked()
    source_ref = {
        "path": "plan/versions/plan-1.0.0.json", "sha256": hashlib.sha256(canonical_json_bytes(plan)).hexdigest(),
        "version": "1.0.0", "revision_seq": 0, "epoch": "E0",
    }
    source_ir = plan_to_draft_ir(plan)
    patch = _patch(source_ir, plan["tasks"][0]["task_uid"])
    patch["source"]["plan_ref"] = {"path": source_ref["path"], "sha256": source_ref["sha256"]}
    state = initialize_plan_state(plan, plan_ref=source_ref)
    frozen = {
        "spec_value": spec, "target_profile_value": target, "test_bundle_value": manifest,
        "refs": plan["input_refs"],
    }
    bundle = complete_revision_candidate(
        plan, source_ref, patch, constraints, frozen, manifest, {}, state,
        {"schema_version": "2.0", "files": []}, ledger_prefix_sha256="a" * 64,
    )
    assert bundle["candidate.json"]["candidate_id"] == "candidate-1"
    assert bundle["invariants.json"]["INV-1"]["pass"] is True
    assert bundle["plan.json"]["schema_version"] == "5.0"


def test_f3_candidate_emits_m1_9_group_inputs_and_inv3_rejects_unmapped_move():
    plan, _blueprint, spec, manifest, constraints, target = _linked()
    frozen = {
        "spec_value": spec, "target_profile_value": target, "test_bundle_value": manifest,
        "refs": plan["input_refs"],
    }
    source_ir = plan_to_draft_ir(plan)
    codec_task = next(task for row in source_ir["task_shards"] if row["work_package_id"] == "wp-codec" for task in row["tasks"])
    codec_uid = derive_task_metadata(codec_task, work_package_id="wp-codec", local_task_id=codec_task["local_id"])["task_uid"]
    source_ref = {
        "path": "plan/versions/plan-1.0.0.json", "sha256": hashlib.sha256(canonical_json_bytes(plan)).hexdigest(),
        "version": "1.0.0", "revision_seq": 0, "epoch": "E0",
    }
    slot = {
        "slot_id": "codec-new", "path": "src/codec/new.c", "path_pattern": None, "expand_over": None,
        "class": "s6_owned", "render_rule": "source_stub", "owner_module": "codec", "contract_id": None,
        "build_role": "link_source", "purpose": "New codec implementation",
    }
    patch = _patch(source_ir, codec_uid)
    patch["level"] = "F3"
    patch["selected_trigger"]["code"] = "TR-7"
    patch["source"]["plan_ref"] = {"path": source_ref["path"], "sha256": source_ref["sha256"]}
    patch["patch_ops"] = [{"op": "add_file_slot", "file_slot": slot, "work_package_id": "wp-codec", "owner_task_uid": codec_uid, "build_artifact_ids": ["application"]}]
    state = initialize_plan_state(plan, plan_ref=source_ref)
    bundle = complete_revision_candidate(
        plan, source_ref, patch, constraints, frozen, manifest, {}, state,
        {"schema_version": "2.0", "files": []}, ledger_prefix_sha256="a" * 64,
    )
    group = bundle["migration.json"]["pending_groups"][0]
    assert group["group_id"] == "g-1-1"
    assert group["affected_paths"] == ["src/codec/new.c"]
    assert codec_uid in group["member_task_uids"]

    quarantined = {
        "schema_version": "2.0",
        "files": [{
            "path": "src/codec/recovered.c", "class": "s6_owned", "state": "quarantined",
            "created_in_epoch": "E0", "content_sha256": "c" * 64, "last_commit_sha": "d" * 40,
            "verified_by": {"build_variant_ids": ["san"], "evidence_ref": _ref("evidence/old.json", "e")},
            "owner_history": [{"plan_version": "1.0.0", "task_uid": codec_uid, "task_id": plan["tasks"][0]["id"]}],
            "quarantined_in_epoch": "E1", "quarantine_path": "_orphan/E1/src/codec/recovered.c",
        }],
    }
    recovered_slot = {
        "slot_id": "codec-recovered", "path": "src/codec/recovered.c", "path_pattern": None, "expand_over": None,
        "class": "s6_owned", "render_rule": "source_stub", "owner_module": "codec", "contract_id": None,
        "build_role": "link_source", "purpose": "Re-adopted codec implementation",
    }
    readopt = _patch(source_ir, codec_uid)
    readopt["level"] = "F3"
    readopt["selected_trigger"]["code"] = "TR-7"
    readopt["source"]["plan_ref"] = {"path": source_ref["path"], "sha256": source_ref["sha256"]}
    readopt["patch_ops"] = [{
        "op": "re_adopt", "quarantine_path": "_orphan/E1/src/codec/recovered.c", "content_sha256": "c" * 64,
        "target_slot": recovered_slot, "work_package_id": "wp-codec", "owner_task_uid": codec_uid,
        "revalidation_obligations": ["file:src/codec/recovered.c"], "build_artifact_ids": ["application"],
    }]
    readopt_bundle = complete_revision_candidate(
        plan, source_ref, readopt, constraints, frozen, manifest, {}, state, quarantined,
        ledger_prefix_sha256="c" * 64,
    )
    assert readopt_bundle["migration.json"]["re_adopt"][0]["quarantine_path"].startswith("_orphan/E1/")
    wrong_hash = copy.deepcopy(readopt)
    wrong_hash["patch_ops"][0]["content_sha256"] = "f" * 64
    with pytest.raises(RevisionMechanismError, match="not registered"):
        complete_revision_candidate(
            plan, source_ref, wrong_hash, constraints, frozen, manifest, {}, state, quarantined,
            ledger_prefix_sha256="c" * 64,
        )

    expanded_ir = _source_with_extra_codec_file()
    extra_slot = next(row for row in expanded_ir["architecture"]["layout"]["files"] if row["slot_id"] == "codec-extra")
    extra_slot["build_role"] = "link_source"
    expanded_ir["architecture"]["layout"]["build_graph"]["artifacts"][0]["link_source_slots"].append("codec-extra")
    expanded_ir = normalize_plan_draft(expanded_ir["architecture"], expanded_ir["work_packages"], expanded_ir["task_shards"])
    expanded = complete_plan_candidate(expanded_ir, constraints, frozen, manifest, {}).plan
    expanded_ref = {**source_ref, "sha256": hashlib.sha256(canonical_json_bytes(expanded)).hexdigest()}
    expanded_state = initialize_plan_state(expanded, plan_ref=expanded_ref)
    expanded_ir = plan_to_draft_ir(expanded)
    owner = next(task for row in expanded_ir["task_shards"] if row["work_package_id"] == "wp-codec" for task in row["tasks"])
    owner_uid = derive_task_metadata(owner, work_package_id="wp-codec", local_task_id=owner["local_id"])["task_uid"]
    inserted = copy.deepcopy(owner)
    inserted["local_id"] = "extra-owner"
    inserted["deliverable_files"] = ["src/codec/extra.c"]
    inserted["consumes_contracts"] = []
    move_patch = _patch(expanded_ir, owner_uid)
    move_patch["source"]["plan_ref"] = {"path": expanded_ref["path"], "sha256": expanded_ref["sha256"]}
    move_patch["patch_ops"] = [{"op": "insert_task", "work_package_id": "wp-codec", "from_task_uid": owner_uid, "task": inserted}]
    with pytest.raises(RevisionMechanismError, match="omits an old obligation mapping"):
        complete_revision_candidate(
            expanded, expanded_ref, move_patch, constraints, frozen, manifest, {}, expanded_state,
            {"schema_version": "2.0", "files": []}, ledger_prefix_sha256="b" * 64,
        )


def test_run_store_candidate_stage_commit_and_reconcile(tmp_path):
    from nepa.schemas import load_example

    store = RunStore(tmp_path)
    ledger = {"schema_version": "2.0", "entries": []}
    evaluation = evaluate_revision_triggers(
        _boundary({"truncations": [_fact(task_uid="0" * 16), _fact(task_uid="0" * 16)]}, ledger), ledger,
    )
    ledger = append_trigger_batch(ledger, evaluation)
    store.replace_json("plan/revision_ledger.json", ledger, schema_name="revision-ledger.schema.json")
    candidate = load_example("revision-candidate.example.json")
    selected = ledger["entries"][0]["payload"]
    candidate["source"]["plan_ref"] = selected["plan_ref"]
    candidate["source"]["ledger_prefix_sha256"] = evaluation["ledger_prefix_sha256"]
    candidate["selected_trigger"] = {
        "code": selected["hit_code"],
        "signature": selected["hit_signature"],
    }
    artifacts = {name: {"artifact": name} for name in candidate["content_hashes"]}
    hashes = {name: hashlib.sha256(canonical_json_bytes(value)).hexdigest() for name, value in artifacts.items()}
    candidate["content_hashes"] = hashes
    ref_keys = {
        "patch_ref": "patch.json", "plan_draft_ir_ref": "plan_draft_ir.json", "candidate_plan_ref": "plan.json",
        "blueprint_ref": "blueprint.json", "manifest_ref": "manifest.json", "contract_map_ref": "contract_map.json",
        "lineage_ref": "lineage.json", "obligation_mapping_ref": "obligations.json", "migration_ref": "migration.json",
        "lint_report_ref": "lint.json", "invariant_report_ref": "invariants.json",
    }
    for key, name in ref_keys.items():
        candidate[key] = {"path": name, "sha256": hashes[name]}
    bundle = {**artifacts, "candidate.json": candidate}
    staged = store.stage_revision_candidate(1, bundle)
    assert staged.path.endswith(".pending/candidate.json")
    committed = store.commit_revision_candidate(1)
    assert committed.path == "plan/_s4r/candidate_1/candidate.json"
    assert store.reconcile_revision_candidate(1) == committed

    bad_store = RunStore(tmp_path / "bad-prefix")
    bad_store.replace_json("plan/revision_ledger.json", ledger, schema_name="revision-ledger.schema.json")
    bad_candidate = copy.deepcopy(candidate)
    bad_candidate["source"]["ledger_prefix_sha256"] = "f" * 64
    bad_store.stage_revision_candidate(1, {**artifacts, "candidate.json": bad_candidate})
    with pytest.raises(ArtifactConflict, match="selected trigger"):
        bad_store.commit_revision_candidate(1)


def test_revision_candidate_recovery_converges_and_detects_corruption(tmp_path):
    import shutil

    from nepa.schemas import load_example

    store = RunStore(tmp_path)
    empty = {"schema_version": "2.0", "entries": []}
    evaluation = evaluate_revision_triggers(
        _boundary({"truncations": [_fact(task_uid="0" * 16), _fact(task_uid="0" * 16)]}, empty),
        empty,
    )
    ledger = append_trigger_batch(empty, evaluation)
    selected = ledger["entries"][0]["payload"]
    candidate = load_example("revision-candidate.example.json")
    candidate["source"]["plan_ref"] = selected["plan_ref"]
    candidate["source"]["ledger_prefix_sha256"] = evaluation["ledger_prefix_sha256"]
    candidate["selected_trigger"] = {"code": selected["hit_code"], "signature": selected["hit_signature"]}
    artifacts = {name: {"artifact": name} for name in candidate["content_hashes"]}
    hashes = {name: hashlib.sha256(canonical_json_bytes(value)).hexdigest() for name, value in artifacts.items()}
    candidate["content_hashes"] = hashes
    ref_names = {
        "patch_ref": "patch.json", "plan_draft_ir_ref": "plan_draft_ir.json", "candidate_plan_ref": "plan.json",
        "blueprint_ref": "blueprint.json", "manifest_ref": "manifest.json", "contract_map_ref": "contract_map.json",
        "lineage_ref": "lineage.json", "obligation_mapping_ref": "obligations.json", "migration_ref": "migration.json",
        "lint_report_ref": "lint.json", "invariant_report_ref": "invariants.json",
    }
    for key, name in ref_names.items():
        candidate[key] = {"path": name, "sha256": hashes[name]}
    bundle = {**artifacts, "candidate.json": candidate}

    store.replace_json("plan/revision_ledger.json", empty, schema_name="revision-ledger.schema.json")
    store.stage_revision_candidate(1, bundle)
    assert store.reconcile_revision_candidate(1) is None
    store.replace_json("plan/revision_ledger.json", ledger, schema_name="revision-ledger.schema.json")
    committed = store.reconcile_revision_candidate(1)
    assert committed is not None and committed.path.endswith("candidate_1/candidate.json")

    pending = store._confined("plan/_s4r/.candidate_1.pending")
    final = store._confined("plan/_s4r/candidate_1")
    shutil.copytree(final, pending)
    assert store.reconcile_revision_candidate(1) == committed
    assert not pending.exists()

    (final / "patch.json").write_bytes(b"{}\n")
    with pytest.raises(ArtifactConflict, match="content hash differs"):
        store.reconcile_revision_candidate(1)


def test_s6_frozen_patch_provider_stages_candidate_and_returns_idempotent_handoff(tmp_path):
    from nepa.application import build_orchestrator
    from test_s5_materialization import FakeExecutor
    from test_s6_execution import _CurrentFilesAgent, _ready_store

    store, _config = _ready_store(tmp_path)
    config = _enable_revision(store)

    def provider(value):
        source = value["source_plan_draft_ir"]
        task = value["source_plan"]["tasks"][0]
        patch = _patch(source, task["task_uid"])
        active = value["source_plan_ref"]
        patch["source"] = {
            "plan_ref": {"path": active["path"], "sha256": active["sha256"]},
            "revision_seq": active["revision_seq"],
            "plan_draft_ir_sha256": hashlib.sha256(canonical_json_bytes(source)).hexdigest(),
        }
        patch["selected_trigger"] = {
            "event_seq": value["selected_event_seq"],
            "code": value["selection"]["code"],
            "signature": value["selection"]["signature"],
        }
        return patch

    orchestrator = build_orchestrator(
        config, store, agent=_CurrentFilesAgent(failures=4), executor=FakeExecutor(),
        revision_patch_provider=provider,
    )
    assert orchestrator.run_spec(store) == 0
    selected = next(entry for entry in store._read_json_artifact("plan/revision_ledger.json")["entries"] if entry["event_type"] == "trigger_evaluated" and entry["payload"]["selected"])
    marker = store._confined(f"plan/_s4r/candidate_{selected['event_seq']}/candidate.json")
    assert marker.is_file()
    ledger_before = store._read_json_artifact("plan/revision_ledger.json")
    assert orchestrator.resume(store) == 0
    assert store._read_json_artifact("plan/revision_ledger.json") == ledger_before


@pytest.mark.parametrize(
    ("fault_point", "pending_before", "final_before", "expected_provider_calls"),
    [
        ("revision_candidate_staged", True, False, 2),
        ("revision_trigger_ledger_replaced", True, False, 1),
        ("revision_candidate_committed", False, True, 1),
    ],
)
def test_s6_candidate_recovers_across_each_publication_boundary(
    tmp_path, fault_point, pending_before, final_before, expected_provider_calls
):
    from nepa.application import build_orchestrator
    from nepa.orchestrator import CrashInjected
    from test_s5_materialization import FakeExecutor
    from test_s6_execution import _CurrentFilesAgent, _ready_store

    store, _config = _ready_store(tmp_path)
    config = _enable_revision(store)
    provider_calls = 0

    def provider(value):
        nonlocal provider_calls
        provider_calls += 1
        source = value["source_plan_draft_ir"]
        task = value["source_plan"]["tasks"][0]
        patch = _patch(source, task["task_uid"])
        active = value["source_plan_ref"]
        patch["source"] = {
            "plan_ref": {"path": active["path"], "sha256": active["sha256"]},
            "revision_seq": active["revision_seq"],
            "plan_draft_ir_sha256": hashlib.sha256(canonical_json_bytes(source)).hexdigest(),
        }
        patch["selected_trigger"] = {
            "event_seq": value["selected_event_seq"], "code": value["selection"]["code"],
            "signature": value["selection"]["signature"],
        }
        return patch

    def crash(point):
        if point == fault_point:
            raise CrashInjected(point)

    agent = _CurrentFilesAgent(failures=4)
    with pytest.raises(CrashInjected):
        build_orchestrator(
            config, store, agent=agent, executor=FakeExecutor(), fault_hook=crash,
            revision_patch_provider=provider,
        ).run_spec(store)
    ledger = store._read_json_artifact("plan/revision_ledger.json")
    selected = next((entry for entry in ledger["entries"] if entry["event_type"] == "trigger_evaluated" and entry["payload"]["selected"]), None)
    pending_dirs = list(store._confined("plan/_s4r").glob(".candidate_*.pending"))
    final_dirs = list(store._confined("plan/_s4r").glob("candidate_*"))
    staged_dir = (pending_dirs or final_dirs)[0]
    event_seq = selected["event_seq"] if selected is not None else int(staged_dir.name.removeprefix(".candidate_").removeprefix("candidate_").removesuffix(".pending"))
    assert store._confined(f"plan/_s4r/.candidate_{event_seq}.pending").is_dir() is pending_before
    assert store._confined(f"plan/_s4r/candidate_{event_seq}").is_dir() is final_before
    calls_before = list(agent.calls)

    assert build_orchestrator(
        config, store, agent=agent, executor=FakeExecutor(), revision_patch_provider=provider,
    ).resume(store) == 0
    assert provider_calls == expected_provider_calls
    assert agent.calls == calls_before
    assert store._confined(f"plan/_s4r/candidate_{event_seq}/candidate.json").is_file()
    assert not store._confined(f"plan/_s4r/.candidate_{event_seq}.pending").exists()
    final_ledger = store._read_json_artifact("plan/revision_ledger.json")
    if selected is not None:
        assert final_ledger == ledger
    else:
        assert len([entry for entry in final_ledger["entries"] if entry["event_type"] == "trigger_evaluated" and entry["payload"]["selected"]]) == 1


def test_s6_selected_trigger_without_patch_provider_pauses_without_candidate(tmp_path):
    from nepa.application import build_orchestrator
    from test_s5_materialization import FakeExecutor
    from test_s6_execution import _CurrentFilesAgent, _ready_store

    store, _config = _ready_store(tmp_path)
    config = _enable_revision(store)
    orchestrator = build_orchestrator(config, store, agent=_CurrentFilesAgent(failures=4), executor=FakeExecutor())
    assert orchestrator.run_spec(store) == 0
    run = store.load_run()
    assert run["stages"]["s6"]["status"] == "pending"
    assert "termination_request" not in run
    ledger = store._read_json_artifact("plan/revision_ledger.json")
    selected = [entry for entry in ledger["entries"] if entry["event_type"] == "trigger_evaluated" and entry["payload"]["selected"]]
    assert len(selected) == 1
    assert not store._confined("plan/_s4r").exists()
    assert orchestrator.resume(store) == 0
    assert store._read_json_artifact("plan/revision_ledger.json") == ledger


def test_s6_tr8_rejects_export_drift_before_workspace_install(tmp_path):
    from nepa.stages.s6_execution import S6ExecutionController, S6ExecutionError, _validate_candidate_bindings

    plan, blueprint, _spec, _manifest, _constraints, _target = _linked()
    task = plan["tasks"][0]
    contract = copy.deepcopy(plan["architecture"]["contracts"][0])
    contract.update({
        "owner": task["work_package"].removeprefix("wp-"), "provider": task["work_package"].removeprefix("wp-"),
        "ready_gate": "task", "provider_task_id": task["id"],
        "exports": [{"interface_file": contract["interface_files"][0], "symbol": "codec_api", "signature": "int codec_api(void);"}],
    })
    plan = copy.deepcopy(plan)
    plan["architecture"]["contracts"] = [contract]
    implementation = task["deliverable_files"][0]
    contract_map = {
        "contracts": [{
            "contract_id": contract["id"], "owner": contract["owner"], "ready_gate": "task",
            "interface_files": contract["interface_files"], "provider_task_id": task["id"],
            "exports": [{
                **contract["exports"][0], "kind": "function", "owner_task_id": task["id"],
                "implementation_file": implementation,
            }],
        }],
    }
    before = {implementation: b"int codec_api(void) { return 0; }\n"}
    with pytest.raises(S6ExecutionError, match="sealed export") as exc:
        _validate_candidate_bindings(
            {implementation: b"int unrelated_symbol(void) { return 0; }\n"},
            task,
            plan,
            blueprint,
            contract_map,
        )
    assert before[implementation] == b"int codec_api(void) { return 0; }\n"

    store = RunStore(tmp_path)
    store.publish_immutable_json(
        f"attempts/{task['task_uid']}/attempt_001/failure.json",
        {"code": "CANDIDATE_FAILURE", "detail": str(exc.value), "diagnosis": {}},
    )
    # An unbound historical error string is not a machine fact. The actual S6
    # failure path records a controller-authored flag in its bound attempt.
    assert not store._confined(f"attempts/{task['task_uid']}/attempt_001.json").exists()
