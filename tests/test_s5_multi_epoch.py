import copy
import hashlib
import subprocess

import pytest

from nepa.orchestrator import StageContext
from nepa.speclib.delivery import compile_delivery_blueprint, compile_delivery_constraints
from nepa.speclib.lint import canonical_json_bytes
from nepa.speclib.materialization import (
    MaterializationError,
    attribute_pending_repair,
    build_epoch_context,
    build_artifact_manifest,
    build_contract_map,
    derive_rendering_view,
    plan_epoch_materialization,
    project_file_ledger,
    project_version_binding,
    render_e0_files,
)
from nepa.speclib.plan import blueprint_task_semantic_projection, derive_task_metadata
from nepa.speclib.plan_revision import build_revision_entry, classify_migration, project_plan_state
from nepa.speclib.plan_state import initialize_plan_state

pytestmark = pytest.mark.s5_epoch


def _fixture():
    from test_plan_lint import _linked

    return _linked()


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _changed_owned_path_plan(plan, constraints, old_path, new_path, *, change_frozen=False):
    candidate = copy.deepcopy(plan)
    for row in candidate["architecture"]["layout"]["files"]:
        if row.get("path") == old_path:
            row["path"] = new_path
    for package in candidate["work_packages"]:
        package["allowed_files"] = [new_path if path == old_path else path for path in package["allowed_files"]]
    if change_frozen:
        candidate["architecture"]["contracts"][0]["exports"][0]["signature"] = candidate["architecture"]["contracts"][0]["exports"][0]["signature"].replace("int", "long")
    contracts = {row["id"]: row for row in candidate["architecture"]["contracts"]}
    for task in candidate["tasks"]:
        task["deliverable_files"] = [new_path if path == old_path else path for path in task["deliverable_files"]]
        task.update(derive_task_metadata(task, contracts=contracts))
    blueprint = compile_delivery_blueprint(
        constraints,
        candidate["architecture"],
        candidate["work_packages"],
        blueprint_task_semantic_projection(candidate["tasks"]),
    )
    candidate["delivery_blueprint_sha256"] = _hash(canonical_json_bytes(blueprint))
    return candidate, blueprint


def test_epoch_negative_context_is_pure_and_rejects_unbound_e1():
    plan, blueprint, spec, _manifest, constraints, target = _fixture()
    ref = {"path": "plan/versions/plan-1.0.0.json", "sha256": _hash(canonical_json_bytes(plan))}
    pointer = {"version": "1.0.0", **ref, "revision_seq": 0, "epoch": "E0"}
    before = copy.deepcopy(plan)
    context = build_epoch_context(plan=plan, active_plan=pointer, plan_ref=ref, blueprint=blueprint, constraints=constraints, spec=spec, target=target)
    assert context["old_inventory"] == context["new_inventory"]
    assert plan == before
    with pytest.raises(MaterializationError, match="F3"):
        build_epoch_context(plan=plan, active_plan={**pointer, "epoch": "E1", "revision_seq": 1}, plan_ref=ref, blueprint=blueprint, constraints=constraints)


def test_epoch_planner_preserves_realized_owned_bytes():
    plan, blueprint, spec, _manifest, constraints, target = _fixture()
    view = derive_rendering_view(plan, spec, target, blueprint, constraints)
    rendered = render_e0_files(view, spec, target, blueprint, constraints)
    rows = [{"path": row["path"], "class": row["mutability"], "state": "slot_only"} for row in view["concrete_rules"]]
    owned = next(row for row in rows if row["path"] == "src/codec/codec.c")
    owned.update({"state": "realized", "created_in_epoch": "E0", "content_sha256": _hash(rendered[owned["path"]]), "last_commit_sha": "1" * 40, "verified_by": {"build_variant_ids": ["release"], "evidence_ref": {"path": "evidence.json", "sha256": "2" * 64}}, "owner_history": [{"plan_version": "1.0.0", "task_uid": plan["tasks"][0]["task_uid"], "task_id": plan["tasks"][0]["id"]}]})
    ledger = {"schema_version": "2.0", "files": rows}
    migration = {"to_version": "1.1.0", "files": [{"path": owned["path"], "old_owner_uid": plan["tasks"][0]["task_uid"], "new_owner_uid": plan["tasks"][0]["task_uid"], "classification": "INHERIT"}]}
    planned = plan_epoch_materialization(blueprint, blueprint, rendered, ledger, migration, constraints=constraints, workspace_files=rendered, epoch="E1")
    action = next(row for row in planned["actions"] if row.get("path") == owned["path"])
    assert action["kind"] == "preserve"
    projected = project_file_ledger(ledger, planned, {"commit_sha": "3" * 40}, {"build_variant_ids": ["release"], "evidence_ref": {"path": "build.json", "sha256": "4" * 64}}, {"path": "plan/epochs/E1/receipt.json", "sha256": "5" * 64})
    retained = next(row for row in projected["files"] if row["path"] == owned["path"])
    assert retained["content_sha256"] == owned["content_sha256"]
    assert retained["verified_by"] == owned["verified_by"]


def test_epoch_planner_quarantines_realized_retirement_and_re_adopts_explicitly():
    plan, blueprint, spec, _manifest, constraints, target = _fixture()
    view = derive_rendering_view(plan, spec, target, blueprint, constraints)
    rendered = render_e0_files(view, spec, target, blueprint, constraints)
    rows = [{"path": row["path"], "class": row["mutability"], "state": "slot_only"} for row in view["concrete_rules"]]
    owned = next(row for row in rows if row["path"] == "src/codec/codec.c")
    owned.update({"state": "realized", "created_in_epoch": "E0", "content_sha256": _hash(rendered[owned["path"]]), "last_commit_sha": "1" * 40, "verified_by": {"build_variant_ids": ["release"], "evidence_ref": {"path": "evidence.json", "sha256": "2" * 64}}, "owner_history": [{"plan_version": "1.0.0", "task_uid": plan["tasks"][0]["task_uid"], "task_id": plan["tasks"][0]["id"]}]})
    ledger = {"schema_version": "2.0", "files": rows}
    changed = copy.deepcopy(blueprint)
    removed_rule = next(row for row in changed["file_rules"] if row["path_pattern"] == owned["path"])
    changed["file_rules"].remove(removed_rule)
    migration = {"to_version": "1.1.0", "files": [{"path": owned["path"], "old_owner_uid": plan["tasks"][0]["task_uid"], "new_owner_uid": None, "classification": "REGENERATE"}]}
    planned = plan_epoch_materialization(blueprint, changed, {path: data for path, data in rendered.items() if path != owned["path"]}, ledger, migration, constraints=constraints, workspace_files=rendered, epoch="E1")
    quarantine = next(row for row in planned["actions"] if row["kind"] == "quarantine")
    assert quarantine["target_path"] == f"_orphan/E1/{owned['path']}"
    projected = project_file_ledger(ledger, planned, {"commit_sha": "3" * 40}, {"build_variant_ids": ["release"], "evidence_ref": {"path": "build.json", "sha256": "4" * 64}}, {"path": "plan/epochs/E1/receipt.json", "sha256": "5" * 64})
    retired = next(row for row in projected["files"] if row["path"] == owned["path"])
    assert retired["state"] == "quarantined"
    adopted_blueprint = copy.deepcopy(blueprint)
    adopt = plan_epoch_materialization(changed, adopted_blueprint, {**rendered}, {"schema_version": "2.0", "files": projected["files"]}, {"re_adopt": [{"quarantine_path": quarantine["target_path"], "target_path": owned["path"], "owner": {"task_uid": plan["tasks"][0]["task_uid"], "task_id": plan["tasks"][0]["id"]}, "content_sha256": owned["content_sha256"]}]}, constraints=constraints, workspace_files={**{path: data for path, data in rendered.items() if path != owned["path"]}, quarantine["target_path"]: rendered[owned["path"]]}, epoch="E2")
    assert next(row for row in adopt["actions"] if row["kind"] == "re_adopt")["target_path"] == owned["path"]


def test_pending_repair_attribution_requires_one_frozen_group():
    failure = {"status": "failed", "timed_out": False, "exit_code": 1, "stderr": "src/codec/codec.c:1: error: incompatible declaration"}
    group = {"group_id": "g-1-1", "affected_paths": ["src/codec/codec.c"], "affected_symbols": [], "member_task_uids": ["a" * 16], "build_artifact_ids": ["application"]}
    assert attribute_pending_repair([failure], {"pending_groups": [group]}) == {"publishable": True, "group_ids": ["g-1-1"], "reason": None}
    rejected = attribute_pending_repair([failure], {"pending_groups": [{**group, "affected_paths": ["other.c"]}]})
    assert rejected == {"publishable": False, "group_ids": [], "reason": "UNREGISTERED_BUILD_DIAGNOSTIC"}


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"timed_out": True}, "INELIGIBLE_BUILD_FAILURE"),
        ({"stderr": "cc: command not found"}, "TOOL_OR_SANDBOX_FAILURE"),
        ({"stderr": "collect2: error: ld returned 1 exit status"}, "UNPARSED_BUILD_DIAGNOSTIC"),
    ],
)
def test_pending_repair_rejects_non_compiler_or_unparsed_failures(changes, reason):
    failure = {"status": "failed", "timed_out": False, "exit_code": 1, "stderr": "src/codec/codec.c:1: error: incompatible declaration", **changes}
    group = {"group_id": "g-1-1", "affected_paths": ["src/codec/codec.c"], "affected_symbols": [], "member_task_uids": ["a" * 16], "build_artifact_ids": ["application"]}
    assert attribute_pending_repair([failure], {"pending_groups": [group]}) == {"publishable": False, "group_ids": [], "reason": reason}


def test_f2_binding_reuses_workspace_hashes_without_rendering():
    plan, blueprint, spec, _manifest, constraints, target = _fixture()
    view = derive_rendering_view(plan, spec, target, blueprint, constraints)
    rendered = render_e0_files(view, spec, target, blueprint, constraints)
    old_ref = {"path": "plan/versions/plan-1.0.0.json", "sha256": "1" * 64}
    new_ref = {"path": "plan/versions/plan-1.0.1.json", "sha256": "2" * 64}
    old_manifest = build_artifact_manifest(old_ref, blueprint, {**view, "rendered_files": rendered}, "E0")
    result = project_version_binding(new_ref, blueprint, view, {"epoch": "E0"}, {"file_hashes": {path: _hash(data) for path, data in rendered.items()}, "constraints": constraints}, existing_manifest=old_manifest)
    assert result["manifest"]["plan_version"] == "1.0.1"
    assert result["binding_receipt"]["epoch_receipt_ref"]["path"] == "plan/epochs/E0/receipt.json"


def test_f2_projector_rebinds_owner_metadata_but_rejects_structural_or_content_drift():
    plan, blueprint, spec, _manifest, constraints, target = _fixture()
    view = derive_rendering_view(plan, spec, target, blueprint, constraints)
    rendered = render_e0_files(view, spec, target, blueprint, constraints)
    old_ref = {"path": "plan/versions/plan-1.0.0.json", "sha256": "1" * 64}
    old_manifest = build_artifact_manifest(old_ref, blueprint, {**view, "rendered_files": rendered}, "E0")
    old_map = build_contract_map(old_ref, blueprint, {**view, "rendered_files": rendered}, "E0")
    changed_plan = copy.deepcopy(plan)
    changed_plan["architecture"]["contracts"][0]["owner"] = "new-owner"
    changed_view = derive_rendering_view(changed_plan, spec, target, blueprint, constraints)
    new_ref = {"path": "plan/versions/plan-1.0.1.json", "sha256": _hash(canonical_json_bytes(changed_plan))}
    result = project_version_binding(
        new_ref,
        blueprint,
        changed_view,
        {"epoch": "E0"},
        {"file_hashes": {path: _hash(data) for path, data in rendered.items()}, "constraints": constraints},
        existing_manifest=old_manifest,
        existing_contract_map=old_map,
    )
    assert result["contract_map"]["contracts"][0]["owner"] == "new-owner"
    bad_blueprint = copy.deepcopy(blueprint)
    bad_blueprint["file_rules"][0]["path_pattern"] = "renamed.c"
    with pytest.raises(MaterializationError, match="structural"):
        project_version_binding(new_ref, bad_blueprint, changed_view, {"epoch": "E0"}, {"file_hashes": {path: _hash(data) for path, data in rendered.items()}, "constraints": constraints}, existing_manifest=old_manifest)
    drifted = {"file_hashes": {path: ("0" * 64 if index == 0 else _hash(data)) for index, (path, data) in enumerate(rendered.items())}, "constraints": constraints}
    with pytest.raises(MaterializationError, match="content"):
        project_version_binding(new_ref, blueprint, changed_view, {"epoch": "E0"}, drifted, existing_manifest=old_manifest)


def _finish_s5(store, controller, result, *, started="2026-01-01T00:00:02Z", ended="2026-01-01T00:00:03Z"):
    run = store.load_run()
    run["stages"]["s5"].update({"status": "done", "started_at": started, "ended_at": ended, "output_refs": {key: value.as_dict() for key, value in result.output_refs.items()}})
    store.replace_run(run)
    controller.after_commit(store, result)


def _accepted_e0_store(tmp_path, case_id=None):
    from test_s5_materialization import FakeExecutor, _frozen_fixture_store, _sealed_store
    from nepa.stages.s5_materialization import S5MaterializationController

    if case_id is None:
        store, completion = _sealed_store(tmp_path)
    else:
        store = _frozen_fixture_store(tmp_path, case_id)
        completion = None
    s5 = S5MaterializationController(FakeExecutor())
    result = s5.run(StageContext(store, "s5", store.load_run(), None))
    _finish_s5(store, s5, result)
    return store, completion, s5


def _activate_candidate(store, candidate, level, *, migration_extensions=None):
    old_pointer = store._read_json_artifact("plan/active_plan.json")
    old_plan = store._read_json_artifact(old_pointer["path"])
    old_state = store._read_json_artifact("plan/plan_state.json")
    old_ledger = store._read_json_artifact("plan/file_ledger.json")
    old_version = old_pointer["version"]
    major = int(old_version.split(".")[1]) + (1 if level == "F3" else 0)
    patch = 0 if level == "F3" else int(old_version.split(".")[2]) + 1
    version = f"1.{major}.{patch}"
    epoch = f"E{int(old_pointer['epoch'][1:]) + (1 if level == 'F3' else 0)}"
    new_pointer = {"version": version, "path": f"plan/versions/plan-{version}.json", "sha256": _hash(canonical_json_bytes(candidate)), "revision_seq": old_pointer["revision_seq"] + 1, "epoch": epoch}
    report = classify_migration(old_plan, candidate, old_state, old_ledger, from_version=old_version, to_version=version)
    report.update(copy.deepcopy(migration_extensions or {}))
    new_state = project_plan_state(old_state, candidate, report, new_pointer)
    entry = build_revision_entry(old_pointer, new_pointer, level, {"code": "synthetic", "evidence_refs": []}, [], report, gates={f"RG-{index}": "pass" for index in range(1, 6)}, activated_at_commit="0" * 40)
    store.activate_revision(candidate, report, new_state, old_ledger, entry, old_pointer, new_pointer=new_pointer)
    return new_pointer, report


def _accept_e0_and_activate(tmp_path, level, case_id=None):
    store, completion, s5 = _accepted_e0_store(tmp_path, case_id)
    old_pointer = store._read_json_artifact("plan/active_plan.json")
    old_plan = store._read_json_artifact(old_pointer["path"])
    old_state = initialize_plan_state(old_plan, plan_ref=old_pointer)
    store.replace_json("plan/plan_state.json", old_state, schema_name="plan-state.schema.json")
    new_plan = copy.deepcopy(old_plan)
    new_pointer, _report = _activate_candidate(store, new_plan, level)
    return store, completion, new_plan, new_pointer, s5


def _structural_e1_store(tmp_path, *, pending_group=False, case_id=None):
    store, completion, _controller = _accepted_e0_store(tmp_path, case_id)
    pointer = store._read_json_artifact("plan/active_plan.json")
    plan = store._read_json_artifact(pointer["path"])
    state = initialize_plan_state(plan, plan_ref=pointer)
    store.replace_json("plan/plan_state.json", state, schema_name="plan-state.schema.json")
    ledger = store._read_json_artifact("plan/file_ledger.json")
    old_path = "src/codec/codec.c"
    old_bytes = store._confined(f"workspace/{old_path}").read_bytes()
    epoch = store._read_json_artifact("plan/epochs/E0/receipt.json")
    owner = next(task for task in plan["tasks"] if old_path in task["deliverable_files"])
    row = next(item for item in ledger["files"] if item["path"] == old_path)
    row.update({
        "state": "realized", "created_in_epoch": "E0", "content_sha256": _hash(old_bytes),
        "last_commit_sha": epoch["checkpoint_commit"],
        "verified_by": {"build_variant_ids": ["release", "san"], "evidence_ref": epoch["build_result_refs"][0]},
        "owner_history": [{"plan_version": "1.0.0", "task_uid": owner["task_uid"], "task_id": owner["id"]}],
    })
    store.replace_json("plan/file_ledger.json", ledger, schema_name="file-ledger.schema.json")
    spec = store._read_json_artifact("spec/spec.json")
    target = store._read_json_artifact("inputs/target.json")
    constraints = compile_delivery_constraints(spec, target)
    new_path = "src/codec/codec_v2.c"
    candidate, blueprint = _changed_owned_path_plan(plan, constraints, old_path, new_path, change_frozen=True)
    extensions = {}
    if pending_group:
        changed_owner = next(task for task in candidate["tasks"] if new_path in task["deliverable_files"])
        extensions["pending_groups"] = [{
            "group_id": "g-1-1", "member_task_uids": [changed_owner["task_uid"]],
            "affected_paths": [new_path], "affected_symbols": [], "build_artifact_ids": [blueprint["build_artifacts"][0]["id"]],
        }]
    new_pointer, report = _activate_candidate(store, candidate, "F3", migration_extensions=extensions)
    return store, candidate, blueprint, new_pointer, report, old_path, new_path, old_bytes


@pytest.mark.s5_epoch
def test_structural_e1_activation_retires_realized_content_and_changes_declared_files(tmp_path):
    from test_s5_materialization import FakeExecutor
    from nepa.stages.s5_materialization import S5MaterializationController

    store, _plan, _blueprint, _pointer, _report, old_path, new_path, old_bytes = _structural_e1_store(tmp_path)
    controller = S5MaterializationController(FakeExecutor())
    result = controller.run(StageContext(store, "s5", store.load_run(), None))
    _finish_s5(store, controller, result, started="2026-01-01T00:00:04Z", ended="2026-01-01T00:00:05Z")
    assert not store._confined(f"workspace/{old_path}").exists()
    assert store._confined(f"workspace/_orphan/E1/{old_path}").read_bytes() == old_bytes
    assert store._confined(f"workspace/{new_path}").is_file()
    ledger = store._read_json_artifact("plan/file_ledger.json")
    retired = next(row for row in ledger["files"] if row["path"] == old_path)
    assert retired["state"] == "quarantined"
    assert next(row for row in ledger["files"] if row["path"] == new_path)["state"] == "slot_only"
    assert store._read_json_artifact("plan/epochs/E1/receipt.json")["materialization_status"] == "ready"


class _CompilerFailureExecutor:
    def __init__(self, path):
        self.path = path
        self.smoke_calls = 0

    def exec(self, command, cwd, timeout_s, net="none"):
        from nepa.tools.sandbox import ExecResult

        if command[:2] == ["python3", "-c"]:
            self.smoke_calls += 1
            return ExecResult(list(command), 0, "", "", 1, False, "completed")
        if command[:1] == ["make"] and command[1:] != ["clean"]:
            return ExecResult(list(command), 1, "", f"{self.path}:1: error: incompatible declaration\n", 1, False, "completed")
        return ExecResult(list(command), 0, "", "", 1, False, "completed")


@pytest.mark.s5_epoch
def test_registered_pending_repair_is_driven_by_the_accepted_activation(tmp_path):
    from nepa.stages.s5_materialization import S5MaterializationController

    store, _plan, _blueprint, _pointer, _report, _old_path, new_path, _old_bytes = _structural_e1_store(tmp_path, pending_group=True)
    executor = _CompilerFailureExecutor(new_path)
    controller = S5MaterializationController(executor)
    result = controller.run(StageContext(store, "s5", store.load_run(), None))
    _finish_s5(store, controller, result, started="2026-01-01T00:00:04Z", ended="2026-01-01T00:00:05Z")
    receipt = store._read_json_artifact("plan/epochs/E1/receipt.json")
    assert receipt["materialization_status"] == "pending_repair"
    assert receipt["pending_group_ids"] == ["g-1-1"]
    assert receipt["smoke_result_refs"] == []
    assert executor.smoke_calls == 0


@pytest.mark.s5_epoch
def test_unregistered_compiler_failure_rejects_epoch_without_smoke_or_checkpoint(tmp_path):
    from nepa.orchestrator import ControlledStageFailure
    from nepa.stages.s5_materialization import S5MaterializationController

    store, _plan, _blueprint, _pointer, _report, _old_path, new_path, _old_bytes = _structural_e1_store(tmp_path)
    executor = _CompilerFailureExecutor(new_path)
    head_before = subprocess.run(
        ["git", "-C", str(store.root / "workspace"), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    with pytest.raises(ControlledStageFailure, match="not publishable"):
        S5MaterializationController(executor).run(StageContext(store, "s5", store.load_run(), None))
    assert executor.smoke_calls == 0
    assert not store._confined("plan/epochs/E1/receipt.json").exists()
    assert subprocess.run(
        ["git", "-C", str(store.root / "workspace"), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip() == head_before


@pytest.mark.s5_epoch
def test_re_adopt_e2_is_driven_by_activation_and_retains_historical_evidence(tmp_path):
    from test_s5_materialization import FakeExecutor
    from nepa.stages.s5_materialization import S5MaterializationController

    store, _plan, _blueprint, _pointer, _report, old_path, new_path, old_bytes = _structural_e1_store(tmp_path)
    controller = S5MaterializationController(FakeExecutor())
    e1 = controller.run(StageContext(store, "s5", store.load_run(), None))
    _finish_s5(store, controller, e1, started="2026-01-01T00:00:04Z", ended="2026-01-01T00:00:05Z")
    before = next(row for row in store._read_json_artifact("plan/file_ledger.json")["files"] if row["path"] == old_path)
    pointer = store._read_json_artifact("plan/active_plan.json")
    plan = store._read_json_artifact(pointer["path"])
    constraints = compile_delivery_constraints(store._read_json_artifact("spec/spec.json"), store._read_json_artifact("inputs/target.json"))
    candidate, _blueprint = _changed_owned_path_plan(plan, constraints, new_path, old_path)
    owner = next(task for task in candidate["tasks"] if old_path in task["deliverable_files"])
    re_adopt = [{
        "quarantine_path": f"_orphan/E1/{old_path}", "target_path": old_path,
        "owner": {"task_uid": owner["task_uid"], "task_id": owner["id"]},
        "content_sha256": _hash(old_bytes),
    }]
    _activate_candidate(store, candidate, "F3", migration_extensions={"re_adopt": re_adopt})
    e2 = controller.run(StageContext(store, "s5", store.load_run(), None))
    _finish_s5(store, controller, e2, started="2026-01-01T00:00:06Z", ended="2026-01-01T00:00:07Z")
    assert store._confined(f"workspace/{old_path}").read_bytes() == old_bytes
    assert not store._confined(f"workspace/_orphan/E1/{old_path}").exists()
    ledger = store._read_json_artifact("plan/file_ledger.json")
    restored = next(row for row in ledger["files"] if row["path"] == old_path)
    assert restored["state"] == "realized"
    assert restored["verified_by"] == before["verified_by"]
    assert restored["owner_history"][-1]["plan_version"] == "1.2.0"


@pytest.mark.parametrize(
    "fault_point",
    [
        "epoch_action:replace:Makefile",
        "epoch_action:preserve:apps/app_main.c",
        "epoch_action:quarantine:src/codec/codec.c",
        "epoch_action:replace:include/mqtt/interface.h",
        "epoch_action:new_stub:src/codec/codec_v2.c",
    ],
)
@pytest.mark.s5_epoch
def test_structural_e1_precheckpoint_actions_restore_exact_predecessor(tmp_path, fault_point):
    from test_s5_materialization import FakeExecutor
    from nepa.stages.s5_materialization import S5MaterializationController

    store, *_rest = _structural_e1_store(tmp_path)
    workspace = store._confined("workspace")
    before = {path.relative_to(workspace).as_posix(): path.read_bytes() for path in workspace.rglob("*") if path.is_file() and ".git" not in path.parts}

    def crash(point):
        if point == fault_point:
            raise RuntimeError(point)

    with pytest.raises(RuntimeError, match=fault_point):
        S5MaterializationController(FakeExecutor(), fault_hook=crash).run(StageContext(store, "s5", store.load_run(), None))
    assert store.recover_s5_epoch("E1") is None
    after = {path.relative_to(workspace).as_posix(): path.read_bytes() for path in workspace.rglob("*") if path.is_file() and ".git" not in path.parts}
    assert after == before
    assert not store._confined("plan/epochs/E1/pending.json").exists()


@pytest.mark.parametrize("fault_kind", ["re_adopt", "retire_slot"])
@pytest.mark.s5_epoch
def test_e2_re_adopt_and_retire_recovery_restore_quarantine_baseline(tmp_path, fault_kind):
    from test_s5_materialization import FakeExecutor
    from nepa.stages.s5_materialization import S5MaterializationController

    store, _plan, _blueprint, _pointer, _report, old_path, new_path, old_bytes = _structural_e1_store(tmp_path)
    controller = S5MaterializationController(FakeExecutor())
    e1 = controller.run(StageContext(store, "s5", store.load_run(), None))
    _finish_s5(store, controller, e1, started="2026-01-01T00:00:04Z", ended="2026-01-01T00:00:05Z")
    pointer = store._read_json_artifact("plan/active_plan.json")
    plan = store._read_json_artifact(pointer["path"])
    constraints = compile_delivery_constraints(store._read_json_artifact("spec/spec.json"), store._read_json_artifact("inputs/target.json"))
    candidate, _blueprint = _changed_owned_path_plan(plan, constraints, new_path, old_path)
    owner = next(task for task in candidate["tasks"] if old_path in task["deliverable_files"])
    _activate_candidate(store, candidate, "F3", migration_extensions={"re_adopt": [{
        "quarantine_path": f"_orphan/E1/{old_path}", "target_path": old_path,
        "owner": {"task_uid": owner["task_uid"], "task_id": owner["id"]}, "content_sha256": _hash(old_bytes),
    }]})
    workspace = store._confined("workspace")
    before = {path.relative_to(workspace).as_posix(): path.read_bytes() for path in workspace.rglob("*") if path.is_file() and ".git" not in path.parts}
    fault_path = f"_orphan/E1/{old_path}" if fault_kind == "re_adopt" else new_path
    fault_point = f"epoch_action:{fault_kind}:{fault_path}"

    def crash(point):
        if point == fault_point:
            raise RuntimeError(point)

    with pytest.raises(RuntimeError, match=fault_point):
        S5MaterializationController(FakeExecutor(), fault_hook=crash).run(StageContext(store, "s5", store.load_run(), None))
    assert store.recover_s5_epoch("E2") is None
    after = {path.relative_to(workspace).as_posix(): path.read_bytes() for path in workspace.rglob("*") if path.is_file() and ".git" not in path.parts}
    assert after == before


def _accepted_structural_e1(tmp_path):
    from test_s5_materialization import FakeExecutor
    from nepa.stages.s5_materialization import S5MaterializationController

    store, *_rest = _structural_e1_store(tmp_path)
    controller = S5MaterializationController(FakeExecutor())
    result = controller.run(StageContext(store, "s5", store.load_run(), None))
    _finish_s5(store, controller, result, started="2026-01-01T00:00:04Z", ended="2026-01-01T00:00:05Z")
    return store, controller


@pytest.mark.parametrize(
    "relative",
    [
        "plan/epochs/E1/receipt.json",
        "plan/bindings/1.1.0/receipt.json",
        "plan/bindings/1.1.0/artifact_manifest.json",
        "plan/bindings/1.1.0/contract_map.json",
        "plan/epochs/E1/artifact_manifest.json",
        "plan/epochs/E1/contract_map.json",
        "plan/artifact_manifest.json",
        "plan/contract_map.json",
        "plan/file_ledger.json",
    ],
)
@pytest.mark.s5_epoch
def test_completed_e1_replay_rejects_corrupt_receipt_binding_and_copies(tmp_path, relative):
    from nepa.run_store import RunStoreError

    store, controller = _accepted_structural_e1(tmp_path)
    revision_before = store._confined("plan/revision_ledger.json").read_bytes()
    store._confined(relative).write_bytes(b"{}\n")
    with pytest.raises((RunStoreError, MaterializationError)):
        controller.run(StageContext(store, "s5", store.load_run(), None))
    assert store._confined("plan/revision_ledger.json").read_bytes() == revision_before


@pytest.mark.parametrize("damage", ["workspace", "build_evidence"])
@pytest.mark.s5_epoch
def test_completed_e1_replay_rejects_workspace_and_evidence_damage(tmp_path, damage):
    from nepa.run_store import RunStoreError

    store, controller = _accepted_structural_e1(tmp_path)
    revision_before = store._confined("plan/revision_ledger.json").read_bytes()
    if damage == "workspace":
        store._confined("workspace/src/codec/codec_v2.c").write_bytes(b"damaged\n")
    else:
        receipt = store._read_json_artifact("plan/epochs/E1/receipt.json")
        store._confined(receipt["build_result_refs"][0]["path"]).write_bytes(b"{}\n")
    with pytest.raises((RunStoreError, MaterializationError)):
        controller.run(StageContext(store, "s5", store.load_run(), None))
    assert store._confined("plan/revision_ledger.json").read_bytes() == revision_before


@pytest.mark.s5_epoch
def test_completed_e1_missing_event_is_validated_then_appended_once(tmp_path):
    store, controller = _accepted_structural_e1(tmp_path)
    ledger = store._read_json_artifact("plan/revision_ledger.json")
    assert ledger["entries"][-1]["event_type"] == "epoch_materialized"
    ledger["entries"].pop()
    store.replace_json("plan/revision_ledger.json", ledger, schema_name="revision-ledger.schema.json")
    first = controller.run(StageContext(store, "s5", store.load_run(), None))
    after_first = store._confined("plan/revision_ledger.json").read_bytes()
    second = controller.run(StageContext(store, "s5", store.load_run(), None))
    assert second.output_refs == first.output_refs
    assert store._confined("plan/revision_ledger.json").read_bytes() == after_first
    events = [row for row in store._read_json_artifact("plan/revision_ledger.json")["entries"] if row["event_type"] == "epoch_materialized" and row["payload"]["revision_seq"] == 1]
    assert len(events) == 1


@pytest.mark.s5_epoch
def test_e1_multi_epoch_ready_uses_one_descendant_checkpoint_and_zero_change_replay(tmp_path):
    store, _completion, _new_plan, new_pointer, controller = _accept_e0_and_activate(tmp_path, "F3")
    first = controller.run(StageContext(store, "s5", store.load_run(), None))
    run = store.load_run()
    run["stages"]["s5"].update({"status": "done", "started_at": "2026-01-01T00:00:04Z", "ended_at": "2026-01-01T00:00:05Z", "output_refs": {key: value.as_dict() for key, value in first.output_refs.items()}})
    store.replace_run(run)
    controller.after_commit(store, first)
    before = {path.relative_to(store.root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns) for path in store.root.rglob("*") if path.is_file() and ".controller.lock" not in path.name}
    second = controller.run(StageContext(store, "s5", store.load_run(), None))
    after = {path.relative_to(store.root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns) for path in store.root.rglob("*") if path.is_file() and ".controller.lock" not in path.name}
    workspace = store.root / "workspace"
    e0 = store._read_json_artifact("plan/epochs/E0/receipt.json")
    e1 = store._read_json_artifact("plan/epochs/E1/receipt.json")
    parent = subprocess.run(["git", "-C", str(workspace), "rev-parse", f"{e1['checkpoint_commit']}^"], capture_output=True, text=True, check=True).stdout.strip()
    assert parent == e0["checkpoint_commit"]
    assert e1["materialized_plan_ref"]["path"] == new_pointer["path"]
    assert second.output_refs == first.output_refs
    assert before == after
    assert subprocess.run(["git", "-C", str(workspace), "rev-list", "--count", "HEAD"], capture_output=True, text=True, check=True).stdout.strip() == "2"


@pytest.mark.parametrize("case_id", ["mqtt", "non_mqtt"])
def test_frozen_protocol_families_multi_epoch_ready_materialize_e1_with_the_same_path(tmp_path, case_id):
    store, _completion, _plan, _pointer, controller = _accept_e0_and_activate(tmp_path / case_id, "F3", case_id)
    result = controller.run(StageContext(store, "s5", store.load_run(), None))
    run = store.load_run()
    run["stages"]["s5"].update({"status": "done", "started_at": "2026-01-01T00:00:04Z", "ended_at": "2026-01-01T00:00:05Z", "output_refs": {key: value.as_dict() for key, value in result.output_refs.items()}})
    store.replace_run(run)
    controller.after_commit(store, result)
    assert store._read_json_artifact("plan/epochs/E1/receipt.json")["materialization_status"] == "ready"
    assert not store._confined("plan/epochs/E1/pending.json").exists()
    if case_id == "non_mqtt":
        assert b"mqtt" not in b"\n".join(path.read_bytes() for path in (store.root / "workspace").rglob("*") if path.is_file() and ".git" not in path.parts).lower()


@pytest.mark.parametrize("case_id", ["mqtt", "non_mqtt"])
def test_frozen_protocol_families_multi_epoch_ready_use_real_docker_sandbox(tmp_path, case_id):
    from nepa.run_store import ArtifactRef
    from nepa.stages.s5_materialization import S5MaterializationController
    from nepa.tools.sandbox import SandboxExecutor

    store, _completion, _plan, _pointer, _controller = _accept_e0_and_activate(tmp_path / case_id, "F3", case_id)
    controller = S5MaterializationController(SandboxExecutor("nepa-sandbox:latest", 2, 4))
    result = controller.run(StageContext(store, "s5", store.load_run(), None))
    run = store.load_run()
    run["stages"]["s5"].update({"status": "done", "started_at": "2026-01-01T00:00:04Z", "ended_at": "2026-01-01T00:00:05Z", "output_refs": {key: ArtifactRef.from_value(value).as_dict() for key, value in result.output_refs.items()}})
    store.replace_run(run)
    controller.after_commit(store, result)
    assert store._read_json_artifact("plan/epochs/E1/receipt.json")["materialization_status"] == "ready"


@pytest.mark.s5_epoch
def test_f2_runstore_binding_does_not_touch_epoch_or_workspace(tmp_path):
    store, completion, new_plan, new_pointer, _controller = _accept_e0_and_activate(tmp_path, "F2")
    epoch_before = (store._confined("plan/epochs/E0/receipt.json").read_bytes(), store._confined("plan/epochs/E0/receipt.json").stat().st_mtime_ns)
    workspace_before = {path.relative_to(store.root / "workspace").as_posix(): (path.read_bytes(), path.stat().st_mtime_ns) for path in (store.root / "workspace").rglob("*") if path.is_file() and ".git" not in path.parts}
    head_before = subprocess.run(["git", "-C", str(store.root / "workspace"), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    spec = store._read_json_artifact("spec/spec.json")
    target = store._read_json_artifact("inputs/target.json")
    constraints = compile_delivery_constraints(spec, target)
    blueprint = completion.blueprint
    view = derive_rendering_view(new_plan, spec, target, blueprint, constraints)
    epoch = store._read_json_artifact("plan/epochs/E0/receipt.json")
    ref = {"path": new_pointer["path"], "sha256": new_pointer["sha256"]}
    baseline = {path.relative_to(store.root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns) for path in store.root.rglob("*") if path.is_file() and ".controller.lock" not in path.name}
    first = store.publish_version_binding({"plan_ref": ref, "blueprint": blueprint, "rendering_view": view, "epoch_receipt": epoch, "constraints": constraints})
    after_first = {path.relative_to(store.root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns) for path in store.root.rglob("*") if path.is_file() and ".controller.lock" not in path.name}
    second = store.publish_version_binding({"plan_ref": ref, "blueprint": blueprint, "rendering_view": view, "epoch_receipt": epoch, "constraints": constraints})
    after_second = {path.relative_to(store.root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns) for path in store.root.rglob("*") if path.is_file() and ".controller.lock" not in path.name}
    allowed = {f"plan/bindings/{new_pointer['version']}/artifact_manifest.json", f"plan/bindings/{new_pointer['version']}/contract_map.json", f"plan/bindings/{new_pointer['version']}/receipt.json", "plan/artifact_manifest.json", "plan/contract_map.json"}
    changed = {path for path in set(baseline) | set(after_first) if baseline.get(path) != after_first.get(path)}
    assert first == second
    assert changed <= allowed
    assert after_first == after_second
    assert epoch_before == (store._confined("plan/epochs/E0/receipt.json").read_bytes(), store._confined("plan/epochs/E0/receipt.json").stat().st_mtime_ns)
    assert workspace_before == {path.relative_to(store.root / "workspace").as_posix(): (path.read_bytes(), path.stat().st_mtime_ns) for path in (store.root / "workspace").rglob("*") if path.is_file() and ".git" not in path.parts}
    assert head_before == subprocess.run(["git", "-C", str(store.root / "workspace"), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    from nepa.run_store import ArtifactConflict
    store._confined(f"plan/bindings/{new_pointer['version']}/artifact_manifest.json").write_bytes(b"{}")
    with pytest.raises(ArtifactConflict):
        store.publish_version_binding({"plan_ref": ref, "blueprint": blueprint, "rendering_view": view, "epoch_receipt": epoch, "constraints": constraints})


@pytest.mark.parametrize(
    "fault_point",
    [
        "epoch_action:preserve:Makefile",
        "epoch_build_results_recorded",
        "epoch_checkpoint_created",
        "epoch_build_evidence_published:release",
        "epoch_manifest_map_published",
        "mutable_epoch_copies_replaced",
        "pending_accepted",
    ],
)
@pytest.mark.s5_epoch
def test_e1_publication_windows_recover_without_a_second_checkpoint(tmp_path, fault_point):
    from test_s5_materialization import FakeExecutor
    from nepa.stages.s5_materialization import S5MaterializationController

    store, _completion, _plan, _pointer, _controller = _accept_e0_and_activate(tmp_path, "F3")
    fired = False

    def crash(point):
        nonlocal fired
        if point == fault_point and not fired:
            fired = True
            raise RuntimeError(point)

    with pytest.raises(RuntimeError, match=fault_point):
        S5MaterializationController(FakeExecutor(), fault_hook=crash).run(StageContext(store, "s5", store.load_run(), None))
    controller = S5MaterializationController(FakeExecutor())
    result = controller.run(StageContext(store, "s5", store.load_run(), None))
    run = store.load_run()
    run["stages"]["s5"].update({"status": "done", "started_at": "2026-01-01T00:00:04Z", "ended_at": "2026-01-01T00:00:05Z", "output_refs": {key: value.as_dict() for key, value in result.output_refs.items()}})
    store.replace_run(run)
    controller.after_commit(store, result)
    assert not store._confined("plan/epochs/E1/pending.json").exists()
    assert subprocess.run(["git", "-C", str(store.root / "workspace"), "rev-list", "--count", "HEAD"], capture_output=True, text=True, check=True).stdout.strip() == "2"
    assert [entry["event_type"] for entry in store._read_json_artifact("plan/revision_ledger.json")["entries"] if entry["event_type"] == "epoch_materialized"] == ["epoch_materialized", "epoch_materialized"]
