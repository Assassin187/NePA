import copy
import json
import subprocess
from pathlib import Path

import pytest

from nepa.orchestrator import CrashInjected, ControlledStageFailure, Orchestrator, StageContext, StageResult
from nepa.config import load_config
from nepa.run_store import ArtifactRef, RunStore, SpecRunInputs, sha256_bytes
from nepa.speclib.materialization import (
    MaterializationError,
    build_artifact_manifest,
    build_contract_map,
    derive_rendering_view,
    parse_c99_declaration,
    project_e0_file_ledger,
    render_e0_files,
)
from nepa.speclib.lint import canonical_json_bytes
from nepa.speclib.plan_revision import PlanRevisionError, append_epoch_materialized, validate_revision_ledger
from nepa.stages.s4_planning import S4Controller, publish_initial_plan
from nepa.stages.s5_materialization import S5MaterializationController
from nepa.tools.sandbox import ExecResult


class FakeExecutor:
    def exec(self, command, cwd, timeout_s, net="none"):
        if command[:2] == ["python3", "-c"]:
            return ExecResult(list(command), 0, 'NEPA_SMOKE {"state":"terminated","exit_code":143}\n', "", 1, False, "completed")
        return ExecResult(list(command), 0, "", "", 1, False, "completed")


class EarlyExitExecutor(FakeExecutor):
    def exec(self, command, cwd, timeout_s, net="none"):
        if command[:2] == ["python3", "-c"]:
            return ExecResult(list(command), 0, 'NEPA_SMOKE {"state":"early_exit","exit_code":1}\n', "", 1, False, "completed")
        return super().exec(command, cwd, timeout_s, net)


def _sealed_store(tmp_path: Path):
    from test_s4_publication import _completion

    store, completion = _completion(tmp_path)
    result = publish_initial_plan(store, completion)
    store.publish_immutable_json("plan/_s4/delivery_constraints.json", completion.constraints)
    run = store.load_run()
    run["stages"]["s4"].update({"status": "done", "started_at": "2026-01-01T00:00:00Z", "ended_at": "2026-01-01T00:00:01Z", "output_refs": dict(result.output_refs)})
    store.replace_run(run)
    return store, completion


def _frozen_fixture_store(tmp_path: Path, case_id: str) -> RunStore:
    fixture = Path(__file__).parent / "fixtures" / "s5" / case_id
    source = Path(__file__).parents[1] / ("gold_file" if case_id == "mqtt" else "tests/fixtures/non_mqtt_application")
    store = RunStore.initialize_spec_run(
        tmp_path,
        SpecRunInputs(source / ("specIR.json" if case_id == "mqtt" else "spec.json"), source / "target.json", source / "test_bundle.json"),
        load_config(),
    )
    for name, relative in {
        "plan.json": "plan/versions/plan-1.0.0.json",
        "blueprint.json": "plan/_s4/delivery_blueprint.json",
        "active_plan.json": "plan/active_plan.json",
        "file_ledger.json": "plan/file_ledger.json",
        "revision_ledger.json": "plan/revision_ledger.json",
        "constraints.json": "plan/_s4/delivery_constraints.json",
    }.items():
        store.publish_immutable_bytes(relative, (fixture / name).read_bytes())
    run = store.load_run()
    plan_ref = {"path": "plan/versions/plan-1.0.0.json", "sha256": sha256_bytes((fixture / "plan.json").read_bytes())}
    active_ref = {"path": "plan/active_plan.json", "sha256": sha256_bytes((fixture / "active_plan.json").read_bytes())}
    blueprint = json.loads((fixture / "blueprint.json").read_text(encoding="utf-8"))
    run["stages"]["s4"] = {
        "status": "done",
        "started_at": "2026-01-01T00:00:00Z",
        "ended_at": "2026-01-01T00:00:01Z",
        "error": None,
        "output_refs": {
            "plan": plan_ref,
            "active_plan": active_ref,
            "delivery_blueprint_sha256": sha256_bytes(canonical_json_bytes(blueprint)),
            "config_snapshot_sha256": run["config_snapshot_sha256"],
        },
    }
    store.replace_run(run)
    return store


def test_c99_finite_parser_consumes_supported_forms_and_rejects_boundaries():
    assert parse_c99_declaration("int run(const char *data, size_t length);")["parameters"][0]["c_type"] == "const char *"
    assert parse_c99_declaration("struct item { int value; char name[8]; };")["kind"] == "type"
    assert parse_c99_declaration("enum mode { MODE_A, MODE_B = 2 };")["kind"] == "type"
    assert parse_c99_declaration("typedef unsigned int result_t;")["kind"] == "type"
    assert parse_c99_declaration("const int limit = 4;")["kind"] == "constant"
    for value in ("#define X 1", "int (*run)(void);", "int run(...);", "int run(char data[4]);", "extern int value;", "int value;", "const int value = call();", "int values[n];", "int run(void) { return 0; }"):
        with pytest.raises(MaterializationError):
            parse_c99_declaration(value)


def test_rendering_view_and_outputs_are_pure_and_byte_stable():
    from test_plan_lint import _linked

    plan, blueprint, spec, _manifest, constraints, target = _linked()
    before = copy.deepcopy(plan)
    first = derive_rendering_view(plan, spec, target, blueprint, constraints)
    second = derive_rendering_view(plan, spec, target, blueprint, constraints)
    assert first == second
    assert plan == before
    first_files = render_e0_files(first, spec, target, blueprint, constraints)
    second_files = render_e0_files(second, spec, target, blueprint, constraints)
    assert first_files == second_files
    assert set(first_files) == {row["path"] for row in first["concrete_rules"]}
    assert b"mqtt" not in first_files["apps/app_main.c"].lower()


def test_task_function_stub_uses_the_derived_not_implemented_result():
    from test_plan_lint import _linked

    plan, blueprint, spec, _manifest, constraints, target = _linked()
    plan = copy.deepcopy(plan)
    plan["architecture"]["contracts"].append({
        "id": "codec-contract", "owner": "codec", "ready_gate": "task", "provider_task_id": "T-001",
        "interface_files": ["include/orbitnet/interface.h"], "consumers": ["entry"],
        "exports": [{"interface_file": "include/orbitnet/interface.h", "symbol": "orbitnet_run", "signature": "orbitnet_result_t orbitnet_run(const char *data, size_t length);"}],
    })
    view = derive_rendering_view(plan, spec, target, blueprint, constraints)
    files = render_e0_files(view, spec, target, blueprint, constraints)
    assert b"return (orbitnet_result_t)-3;" in files["src/codec/codec.c"]


def test_renderability_rejects_missing_entry_duplicate_entry_and_missing_definition():
    from test_plan_lint import _linked

    plan, blueprint, spec, _manifest, constraints, target = _linked()
    missing_entry = copy.deepcopy(blueprint)
    missing_entry["build_artifacts"][0]["entry_file_slot"] = "not-declared"
    with pytest.raises(MaterializationError, match="entry"):
        derive_rendering_view(plan, spec, target, missing_entry, constraints)

    duplicate_entry = copy.deepcopy(blueprint)
    duplicate_entry["build_artifacts"].append({**duplicate_entry["build_artifacts"][0], "id": "second-app", "path": "build/second"})
    with pytest.raises(MaterializationError, match="entry"):
        derive_rendering_view(plan, spec, target, duplicate_entry, constraints)

    missing_definition = copy.deepcopy(plan)
    missing_definition["architecture"]["contracts"][0]["exports"].append({
        "interface_file": "include/orbitnet/interface.h", "symbol": "orbitnet_run",
        "signature": "int orbitnet_run(void);", "implementation_file": "src/missing.c",
    })
    with pytest.raises(MaterializationError, match="implementation"):
        derive_rendering_view(missing_definition, spec, target, blueprint, constraints)


def test_renderability_rejects_undeclared_cycle_extra_main_and_ambiguous_source():
    from test_plan_lint import _linked

    plan, blueprint, spec, _manifest, constraints, target = _linked()
    undeclared = copy.deepcopy(plan)
    undeclared["architecture"]["contracts"][0]["exports"].append({"interface_file": "include/orbitnet/interface.h", "symbol": "bad_t", "signature": "typedef missing_t bad_t;"})
    with pytest.raises(MaterializationError, match="undeclared"):
        derive_rendering_view(undeclared, spec, target, blueprint, constraints)

    cycle = copy.deepcopy(plan)
    cycle["architecture"]["contracts"][0]["exports"].extend([
        {"interface_file": "include/orbitnet/interface.h", "symbol": "a_t", "signature": "typedef b_t a_t;"},
        {"interface_file": "include/orbitnet/interface.h", "symbol": "b_t", "signature": "typedef a_t b_t;"},
    ])
    with pytest.raises(MaterializationError, match="cycle"):
        derive_rendering_view(cycle, spec, target, blueprint, constraints)

    extra_main = copy.deepcopy(plan)
    extra_main["architecture"]["contracts"][0]["exports"].append({"interface_file": "include/orbitnet/interface.h", "symbol": "main", "signature": "int main(void);"})
    with pytest.raises(MaterializationError, match="main"):
        derive_rendering_view(extra_main, spec, target, blueprint, constraints)

    ambiguous_plan = copy.deepcopy(plan)
    ambiguous_plan["architecture"]["contracts"].append({
        "id": "codec-contract", "owner": "codec", "ready_gate": "task", "provider_task_id": "T-001",
        "interface_files": ["include/orbitnet/interface.h"], "consumers": ["entry"],
        "exports": [{"interface_file": "include/orbitnet/interface.h", "symbol": "orbitnet_run", "signature": "int orbitnet_run(void);"}],
    })
    ambiguous_plan["tasks"][0]["deliverable_files"].append("src/codec/other.c")
    ambiguous_blueprint = copy.deepcopy(blueprint)
    second = copy.deepcopy(next(item for item in ambiguous_blueprint["file_rules"] if item["id"] == "codec-source"))
    second.update({"id": "codec-source-2", "path_pattern": "src/codec/other.c"})
    ambiguous_blueprint["file_rules"].append(second)
    ambiguous_blueprint["file_rules"].sort(key=lambda item: item["id"].encode("utf-8"))
    ambiguous_blueprint["link_source_sets"][0]["file_rule_ids"].append("codec-source-2")
    ambiguous_blueprint["link_source_sets"][0]["file_rule_ids"].sort()
    with pytest.raises(MaterializationError, match="implementation slots"):
        derive_rendering_view(ambiguous_plan, spec, target, ambiguous_blueprint, constraints)


def test_renderer_rejects_an_unapproved_declared_template_path():
    from test_plan_lint import _linked

    plan, blueprint, spec, _manifest, constraints, target = _linked()
    changed = copy.deepcopy(blueprint)
    rule = next(item for item in changed["file_rules"] if item["id"] == "interface-header")
    rule["template_path"] = "nepa/templates/unapproved.h"
    template = next(item for item in changed["layout_templates"] if "interface-header" in item["output_rule_ids"])
    template["template_path"] = "nepa/templates/unapproved.h"
    view = derive_rendering_view(plan, spec, target, changed, constraints)
    with pytest.raises(MaterializationError, match="allowed packaged template"):
        render_e0_files(view, spec, target, changed, constraints)


def test_manifest_map_and_e0_ledger_keep_s6_slots_unrealized():
    from test_plan_lint import _linked

    plan, blueprint, spec, _manifest, constraints, target = _linked()
    view = derive_rendering_view(plan, spec, target, blueprint, constraints)
    files = render_e0_files(view, spec, target, blueprint, constraints)
    plan_ref = {"path": "plan/versions/plan-1.0.0.json", "sha256": "0" * 64}
    view_with_files = {**view, "rendered_files": files}
    manifest = build_artifact_manifest(plan_ref, blueprint, view_with_files, "E0")
    contract_map = build_contract_map(plan_ref, blueprint, view_with_files, "E0")
    commit = "1" * 40
    ledger = project_e0_file_ledger(
        {"schema_version": "2.0", "files": [{"path": row["path"], "class": row["mutability"], "state": "slot_only"} for row in view["concrete_rules"]]},
        files,
        {"commit_sha": commit},
        {"build_variant_ids": ["release"], "evidence_ref": {"path": "evidence.json", "sha256": "2" * 64}},
        {"path": "plan/epochs/E0/receipt.json", "sha256": "3" * 64},
    )
    assert manifest["plan_sha256"] == plan_ref["sha256"]
    assert contract_map["epoch"] == "E0"
    assert all(row["state"] == "slot_only" for row in ledger["files"] if row["class"] == "s6_owned")
    assert all(row["state"] == "realized" for row in ledger["files"] if row["class"] == "s5_frozen")


def test_s5_controller_publishes_one_checkpoint_and_replays_without_changes(tmp_path):
    store, _completion = _sealed_store(tmp_path)
    controller = S5MaterializationController(FakeExecutor())
    first = controller.run(StageContext(store, "s5", store.load_run(), None))
    run = store.load_run()
    run["stages"]["s5"].update({"status": "done", "started_at": "2026-01-01T00:00:02Z", "ended_at": "2026-01-01T00:00:03Z", "output_refs": {key: ArtifactRef.from_value(value).as_dict() for key, value in first.output_refs.items()}})
    store.replace_run(run)
    controller.after_commit(store, first)
    controller.verify_completed(store)
    object.__new__(S4Controller).verify_completed(store)
    before = {
        path.relative_to(store.root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in store.root.rglob("*") if path.is_file() and ".controller.lock" not in path.name
    }
    second = controller.run(StageContext(store, "s5", store.load_run(), None))
    after = {
        path.relative_to(store.root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in store.root.rglob("*") if path.is_file() and ".controller.lock" not in path.name
    }
    assert second.output_refs == first.output_refs
    assert before == after
    assert subprocess.run(["git", "-C", str(store.root / "workspace"), "rev-list", "--count", "HEAD"], capture_output=True, text=True, check=True).stdout.strip() == "1"
    revision = store._read_json_artifact("plan/revision_ledger.json")
    assert [entry["event_type"] for entry in revision["entries"]] == ["epoch_materialized"]
    assert any(row["state"] == "realized" for row in store._read_json_artifact("plan/file_ledger.json")["files"])


def test_typed_epoch_event_is_hash_chained_and_idempotent():
    empty = {"schema_version": "2.0", "entries": []}
    epoch_ref = {"path": "plan/epochs/E0/receipt.json", "sha256": "1" * 64}
    binding_ref = {"path": "plan/bindings/1.0.0/receipt.json", "sha256": "2" * 64}
    first = append_epoch_materialized(empty, epoch_receipt_ref=epoch_ref, binding_ref=binding_ref)
    assert append_epoch_materialized(first, epoch_receipt_ref=epoch_ref, binding_ref=binding_ref) == first
    validate_revision_ledger(first)
    assert first["entries"][0]["prev_entry_sha256"] == "0" * 64
    with pytest.raises(PlanRevisionError):
        append_epoch_materialized(first, epoch_receipt_ref=epoch_ref, binding_ref={"path": "other", "sha256": "3" * 64})
    tampered = copy.deepcopy(first)
    tampered["entries"][0]["prev_entry_sha256"] = "f" * 64
    with pytest.raises(PlanRevisionError):
        validate_revision_ledger(tampered)


def test_reconcile_appends_missing_event_and_removes_pending(tmp_path):
    store, _completion = _sealed_store(tmp_path)
    controller = S5MaterializationController(FakeExecutor())
    result = controller.run(StageContext(store, "s5", store.load_run(), None))
    run = store.load_run()
    run["stages"]["s5"].update({"status": "done", "started_at": "2026-01-01T00:00:02Z", "ended_at": "2026-01-01T00:00:03Z", "output_refs": {key: ArtifactRef.from_value(value).as_dict() for key, value in result.output_refs.items()}})
    store.replace_run(run)
    assert store._confined("plan/epochs/E0/pending.json").exists()
    controller.reconcile(store)
    assert not store._confined("plan/epochs/E0/pending.json").exists()
    revision = store._read_json_artifact("plan/revision_ledger.json")
    assert [entry["event_type"] for entry in revision["entries"]] == ["epoch_materialized"]
    controller.reconcile(store)


def test_checkpoint_fault_reuses_the_same_git_identity(tmp_path):
    store, _completion = _sealed_store(tmp_path)
    fired = False

    def crash(point):
        nonlocal fired
        if point == "checkpoint_created" and not fired:
            fired = True
            raise RuntimeError("checkpoint interruption")

    with pytest.raises(RuntimeError, match="checkpoint interruption"):
        S5MaterializationController(FakeExecutor(), fault_hook=crash).run(StageContext(store, "s5", store.load_run(), None))
    resumed = S5MaterializationController(FakeExecutor()).run(StageContext(store, "s5", store.load_run(), None))
    run = store.load_run()
    run["stages"]["s5"].update({"status": "done", "started_at": "2026-01-01T00:00:02Z", "ended_at": "2026-01-01T00:00:03Z", "output_refs": {key: ArtifactRef.from_value(value).as_dict() for key, value in resumed.output_refs.items()}})
    store.replace_run(run)
    S5MaterializationController(FakeExecutor()).after_commit(store, resumed)
    assert subprocess.run(["git", "-C", str(store.root / "workspace"), "rev-list", "--count", "HEAD"], capture_output=True, text=True, check=True).stdout.strip() == "1"


def test_input_drift_is_controlled_before_workspace_effects(tmp_path):
    store, _completion = _sealed_store(tmp_path)
    spec = store._read_json_artifact("spec/spec.json")
    spec["metadata"] = {**spec.get("metadata", {}), "s5_drift": True}
    store.replace_json("spec/spec.json", spec)
    with pytest.raises(ControlledStageFailure) as failure:
        S5MaterializationController(FakeExecutor()).run(StageContext(store, "s5", store.load_run(), None))
    assert failure.value.reason["code"] == "S5_INPUT_DRIFT"
    assert not (store.root / "workspace").exists()


def test_build_or_smoke_failure_is_internal_and_not_ready(tmp_path):
    store, _completion = _sealed_store(tmp_path)
    with pytest.raises(RuntimeError, match="sandbox smoke failed"):
        S5MaterializationController(EarlyExitExecutor()).run(StageContext(store, "s5", store.load_run(), None))
    assert not store._confined("plan/epochs/E0/receipt.json").exists()


def test_receipt_fault_replays_the_accepted_checkpoint_without_a_second_commit(tmp_path):
    store, _completion = _sealed_store(tmp_path)
    fired = False

    def crash(point):
        nonlocal fired
        if point == "epoch_receipt_published" and not fired:
            fired = True
            raise RuntimeError("receipt interruption")

    with pytest.raises(RuntimeError, match="receipt interruption"):
        S5MaterializationController(FakeExecutor(), fault_hook=crash).run(StageContext(store, "s5", store.load_run(), None))
    resumed = S5MaterializationController(FakeExecutor()).run(StageContext(store, "s5", store.load_run(), None))
    run = store.load_run()
    run["stages"]["s5"].update({"status": "done", "started_at": "2026-01-01T00:00:02Z", "ended_at": "2026-01-01T00:00:03Z", "error": None, "output_refs": {key: ArtifactRef.from_value(value).as_dict() for key, value in resumed.output_refs.items()}})
    store.replace_run(run)
    controller = S5MaterializationController(FakeExecutor())
    controller.after_commit(store, resumed)
    controller.verify_completed(store)
    assert subprocess.run(["git", "-C", str(store.root / "workspace"), "rev-list", "--count", "HEAD"], capture_output=True, text=True, check=True).stdout.strip() == "1"


@pytest.mark.parametrize(
    "point",
    [
        "workspace_written:Makefile", "build_results_recorded", "smoke_results_recorded",
        "checkpoint_created", "build_evidence_published:release", "build_evidence_published:san",
        "smoke_evidence_published:release:build_application", "manifest_map_published",
        "epoch_receipt_published", "binding_receipt_published", "mutable_e0_copies_replaced", "pending_accepted",
    ],
)
def test_every_s5_publication_boundary_recovers_to_one_checkpoint(tmp_path, point):
    store, _completion = _sealed_store(tmp_path)
    fired = False

    def crash(current):
        nonlocal fired
        if current == point and not fired:
            fired = True
            raise RuntimeError(point)

    with pytest.raises(RuntimeError, match=point):
        S5MaterializationController(FakeExecutor(), fault_hook=crash).run(StageContext(store, "s5", store.load_run(), None))
    controller = S5MaterializationController(FakeExecutor())
    result = controller.run(StageContext(store, "s5", store.load_run(), None))
    run = store.load_run()
    run["stages"]["s5"].update({"status": "done", "started_at": "2026-01-01T00:00:02Z", "ended_at": "2026-01-01T00:00:03Z", "error": None, "output_refs": {key: ArtifactRef.from_value(value).as_dict() for key, value in result.output_refs.items()}})
    store.replace_run(run)
    controller.after_commit(store, result)
    controller.verify_completed(store)
    assert subprocess.run(["git", "-C", str(store.root / "workspace"), "rev-list", "--count", "HEAD"], capture_output=True, text=True, check=True).stdout.strip() == "1"


def test_post_checkpoint_recovery_reuses_nondeterministic_evidence(tmp_path):
    class VaryingExecutor(FakeExecutor):
        def __init__(self):
            self.calls = 0

        def exec(self, command, cwd, timeout_s, net="none"):
            self.calls += 1
            result = super().exec(command, cwd, timeout_s, net)
            return ExecResult(result.command, result.returncode, result.stdout, result.stderr, self.calls, result.timed_out, result.observation)

    store, _completion = _sealed_store(tmp_path)
    executor = VaryingExecutor()
    with pytest.raises(RuntimeError, match="build evidence interruption"):
        S5MaterializationController(
            executor,
            fault_hook=lambda point: (_ for _ in ()).throw(RuntimeError("build evidence interruption")) if point == "build_evidence_published:release" else None,
        ).run(StageContext(store, "s5", store.load_run(), None))
    calls = executor.calls
    S5MaterializationController(executor).run(StageContext(store, "s5", store.load_run(), None))
    assert executor.calls == calls


def test_checkpointed_workspace_conflict_is_not_overwritten(tmp_path):
    store, _completion = _sealed_store(tmp_path)
    with pytest.raises(RuntimeError, match="checkpoint interruption"):
        S5MaterializationController(
            FakeExecutor(),
            fault_hook=lambda point: (_ for _ in ()).throw(RuntimeError("checkpoint interruption")) if point == "checkpoint_created" else None,
        ).run(StageContext(store, "s5", store.load_run(), None))
    target = store.root / "workspace/apps/app_main.c"
    target.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(Exception, match="workspace differs"):
        S5MaterializationController(FakeExecutor()).run(StageContext(store, "s5", store.load_run(), None))
    assert target.read_text(encoding="utf-8") == "tampered\n"


def test_s5_recomputes_blueprint_without_reading_s4_candidate(tmp_path):
    store, _completion = _sealed_store(tmp_path)
    (store.root / "plan/_s4/delivery_blueprint.json").write_text("damaged", encoding="utf-8")
    assert S5MaterializationController(FakeExecutor()).run(StageContext(store, "s5", store.load_run(), None)).output_refs


def test_s5_rejects_configuration_anchor_drift_before_workspace(tmp_path):
    store, _completion = _sealed_store(tmp_path)
    run = store.load_run()
    run["stages"]["s4"]["output_refs"]["config_snapshot_sha256"] = "f" * 64
    store.replace_run(run)
    with pytest.raises(ControlledStageFailure) as failure:
        S5MaterializationController(FakeExecutor()).run(StageContext(store, "s5", store.load_run(), None))
    assert failure.value.reason["code"] == "S5_CONFIG_DRIFT"
    assert not (store.root / "workspace").exists()


class _S6Receipt:
    def run(self, context):
        example = Path(__file__).parents[1] / "nepa/schemas/examples/s6-receipt.example.json"
        ref = context.store.publish_immutable_bytes("receipts/s6.json", example.read_bytes())
        return StageResult(output_refs={"s6_receipt": ref})


@pytest.mark.parametrize("point", ["s5_output_published", "s5_done_committed"])
def test_orchestrator_resumes_s5_run_acceptance_windows(tmp_path, point):
    store, _completion = _sealed_store(tmp_path)
    with pytest.raises(CrashInjected):
        Orchestrator(
            {"s5": S5MaterializationController(FakeExecutor()), "s6": _S6Receipt()},
            fault_hook=lambda current: (_ for _ in ()).throw(CrashInjected()) if current == point else None,
        ).run_spec(store)
    assert Orchestrator({"s5": S5MaterializationController(FakeExecutor()), "s6": _S6Receipt()}).resume(RunStore(store.root)) == 0
    revision = store._read_json_artifact("plan/revision_ledger.json")
    assert [entry["event_type"] for entry in revision["entries"]] == ["epoch_materialized"]


def test_orchestrator_resumes_after_materialization_event_append(tmp_path):
    store, _completion = _sealed_store(tmp_path)
    controller = S5MaterializationController(
        FakeExecutor(),
        fault_hook=lambda point: (_ for _ in ()).throw(CrashInjected()) if point == "epoch_event_appended" else None,
    )
    with pytest.raises(CrashInjected):
        Orchestrator({"s5": controller, "s6": _S6Receipt()}).run_spec(store)
    assert store.load_run()["stages"]["s5"]["status"] == "done"
    assert Orchestrator({"s5": S5MaterializationController(FakeExecutor()), "s6": _S6Receipt()}).resume(RunStore(store.root)) == 0
    revision = store._read_json_artifact("plan/revision_ledger.json")
    assert [entry["event_type"] for entry in revision["entries"]] == ["epoch_materialized"]


@pytest.mark.s5_epoch
def test_real_docker_sandbox_builds_and_smokes_e0(tmp_path):
    from test_s4_publication import _completion
    from nepa.speclib.materialization import derive_rendering_view, render_e0_files
    from nepa.tools.build import run_build_variants, run_smoke_checks
    from nepa.tools.sandbox import SandboxExecutor

    _store, completion = _completion(tmp_path)
    view = derive_rendering_view(completion.plan, completion.spec, completion.constraints["target_profile"], completion.blueprint, completion.constraints)
    files = render_e0_files(view, completion.spec, completion.constraints["target_profile"], completion.blueprint, completion.constraints)
    workspace = tmp_path / "docker-workspace"
    for relative, data in files.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    executor = SandboxExecutor("nepa-sandbox:latest", 2, 4)
    builds = run_build_variants(executor, workspace, completion.blueprint, completion.constraints)
    smoke = run_smoke_checks(executor, workspace, completion.blueprint, builds, 1, 2)
    assert {item["variant"] for item in builds} == {"release", "san"}
    assert all(item["status"] == "passed" for item in builds + smoke)


@pytest.mark.s5_epoch
@pytest.mark.parametrize("case_id", ["mqtt", "non_mqtt"])
def test_real_docker_materializes_each_frozen_s4_fixture(tmp_path, case_id):
    store = _frozen_fixture_store(tmp_path / case_id, case_id)
    controller = S5MaterializationController()
    result = controller.run(StageContext(store, "s5", store.load_run(), None))
    run = store.load_run()
    run["stages"]["s5"].update({"status": "done", "started_at": "2026-01-01T00:00:02Z", "ended_at": "2026-01-01T00:00:03Z", "output_refs": {key: ArtifactRef.from_value(value).as_dict() for key, value in result.output_refs.items()}})
    store.replace_run(run)
    controller.after_commit(store, result)
    controller.verify_completed(store)
    workspace = store.root / "workspace"
    assert (workspace / "Makefile").is_file()
    assert not any(path.name.endswith(".o") for path in workspace.rglob("*"))
    before = {path.relative_to(store.root).as_posix(): path.read_bytes() for path in store.root.rglob("*") if path.is_file() and ".controller.lock" not in path.name}
    head_before = subprocess.run(["git", "-C", str(workspace), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    controller.run(StageContext(store, "s5", store.load_run(), None))
    after = {path.relative_to(store.root).as_posix(): path.read_bytes() for path in store.root.rglob("*") if path.is_file() and ".controller.lock" not in path.name}
    head_after = subprocess.run(["git", "-C", str(workspace), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    assert before == after
    assert head_before == head_after
    if case_id == "non_mqtt":
        assert b"mqtt" not in b"\n".join(path.read_bytes() for path in workspace.rglob("*") if path.is_file() and ".git" not in path.parts).lower()
