import json
from pathlib import Path

import pytest

from nepa.config import ConfigSnapshotDrift, load_config
from nepa.orchestrator import CrashInjected, ControlledStageFailure, Orchestrator, StageResult
from nepa.run_store import RunStore, SpecRunInputs


ROOT = Path(__file__).parents[1]


def _store(tmp_path):
    return RunStore.initialize_spec_run(
        tmp_path,
        SpecRunInputs(ROOT / "gold_file/specIR.json", ROOT / "gold_file/target.json", ROOT / "gold_file/test_bundle.json"),
        load_config(overrides={"run": {"until": "s6"}}),
    )


class Recording:
    def __init__(self, calls, stage):
        self.calls = calls
        self.stage = stage

    def run(self, context):
        self.calls.append(self.stage)
        ref = context.store.publish_immutable_bytes(f"receipts/{self.stage}.json", self.stage.encode())
        return StageResult(output_refs={"receipt": ref})


def _controllers(calls):
    return {stage: Recording(calls, stage) for stage in ("s4", "s5", "s6")}


def test_orphaned_running_stage_is_reconciled_then_retried(tmp_path):
    store = _store(tmp_path)
    run = store.load_run()
    run["stages"]["s4"].update({"status": "running", "started_at": "2026-08-14T00:00:00Z"})
    store.replace_run(run)
    calls = []

    assert Orchestrator(_controllers(calls)).resume(store) == 0
    assert calls == ["s4", "s5", "s6"]
    assert store.load_run()["termination_kind"] == "planned_stop"


def test_resume_rejects_configuration_snapshot_drift_before_work(tmp_path):
    store = _store(tmp_path)
    run = store.load_run()
    run["config_snapshot"]["run"]["until"] = None
    store.replace_run(run)

    with pytest.raises(ConfigSnapshotDrift):
        Orchestrator(_controllers([])).resume(store)


def test_request_resume_bypasses_ordinary_stages_and_finalization_uses_valid_s9(tmp_path):
    store = _store(tmp_path)

    class Failing:
        def run(self, context):
            raise ControlledStageFailure({"code": "EXPECTED_FAILURE", "detail": "persist me"})

    with pytest.raises(CrashInjected):
        Orchestrator({"s4": Failing()}, fault_hook=lambda point: (_ for _ in ()).throw(CrashInjected()) if point == "terminal_before_finalize" else None).run_spec(store)
    calls = []
    assert Orchestrator(_controllers(calls)).resume(RunStore(store.root)) == 20
    run = RunStore(store.root).load_run()
    assert calls == []
    assert run["termination_kind"] == "controlled_exit"
    assert run["stages"]["s9"]["status"] == "done"


def test_resume_retries_orphaned_s9_in_crash_window_without_duplicate_immutable_outputs_on_replay(tmp_path):
    store = _store(tmp_path)

    class Failing:
        def run(self, context):
            raise ControlledStageFailure({"code": "EXPECTED_FAILURE", "detail": "retry s9"})

    def crash(point):
        if point == "s9_report_published":
            raise CrashInjected()

    with pytest.raises(CrashInjected):
        Orchestrator({"s4": Failing()}, fault_hook=crash).run_spec(store)
    assert store.load_run()["stages"]["s9"]["status"] == "running"
    assert Orchestrator({}).resume(RunStore(store.root)) == 20
    run = RunStore(store.root).load_run()
    assert run["stages"]["s9"]["status"] == "done"
    assert json.loads((store.root / "report/report.json").read_text(encoding="utf-8"))["termination_reason"]["detail"] == "retry s9"


def test_corrupt_done_s9_fail_stop_preserves_stage_at_finalization(tmp_path):
    store = _store(tmp_path)

    class Failing:
        def run(self, context):
            raise ControlledStageFailure({"code": "EXPECTED_FAILURE", "detail": "corrupt s9"})

    with pytest.raises(CrashInjected):
        Orchestrator({"s4": Failing()}, fault_hook=lambda point: (_ for _ in ()).throw(CrashInjected()) if point == "terminal_before_finalize" else None).run_spec(store)
    (store.root / "report/report.json").write_text("{}", encoding="utf-8")
    assert Orchestrator({}).resume(store) == 1
    run = store.load_run()
    assert run["termination_kind"] == "internal_error"
    assert run["stages"]["s9"]["status"] == "done"


def test_crash_window_stage_running_reconciles_with_fresh_instances(tmp_path):
    store = _store(tmp_path)
    calls = []

    def crash(point):
        if point == "s4_running_committed":
            raise CrashInjected()

    with pytest.raises(CrashInjected):
        Orchestrator(_controllers(calls), fault_hook=crash).run_spec(store)
    assert store.load_run()["stages"]["s4"]["status"] == "running"

    resumed_calls = []
    assert Orchestrator(_controllers(resumed_calls)).resume(RunStore(store.root)) == 0
    assert resumed_calls == ["s4", "s5", "s6"]


def test_crash_window_output_publication_replays_immutable_output_with_fresh_instances(tmp_path):
    store = _store(tmp_path)
    calls = []

    def crash(point):
        if point == "s4_output_published":
            raise CrashInjected()

    with pytest.raises(CrashInjected):
        Orchestrator(_controllers(calls), fault_hook=crash).run_spec(store)
    output = store.root / "receipts/s4.json"
    before = output.read_bytes()
    before_files = sorted(path.relative_to(store.root).as_posix() for path in (store.root / "receipts").glob("*") )

    resumed_calls = []
    assert Orchestrator(_controllers(resumed_calls)).resume(RunStore(store.root)) == 0
    assert resumed_calls == ["s4", "s5", "s6"]
    assert output.read_bytes() == before
    assert sorted(path.relative_to(store.root).as_posix() for path in (store.root / "receipts").glob("*")) == [
        "receipts/s4.json",
        "receipts/s5.json",
        "receipts/s6.json",
    ]
    assert before_files == ["receipts/s4.json"]


def test_crash_window_stage_done_reuses_durable_commit_with_fresh_instances(tmp_path):
    store = _store(tmp_path)

    def crash(point):
        if point == "s4_done_committed":
            raise CrashInjected()

    with pytest.raises(CrashInjected):
        Orchestrator(_controllers([]), fault_hook=crash).run_spec(store)
    run = store.load_run()
    assert run["stages"]["s4"]["status"] == "done"
    assert run["stages"]["s4"]["output_refs"]

    resumed_calls = []
    assert Orchestrator(_controllers(resumed_calls)).resume(RunStore(store.root)) == 0
    assert resumed_calls == ["s5", "s6"]


def test_crash_window_request_persistence_bypasses_ordinary_stages_with_fresh_instances(tmp_path):
    store = _store(tmp_path)

    class Failing:
        def run(self, context):
            raise ControlledStageFailure({"code": "EXPECTED_FAILURE", "detail": "persist request"})

    def crash(point):
        if point == "termination_request_committed":
            raise CrashInjected()

    with pytest.raises(CrashInjected):
        Orchestrator({"s4": Failing()}, fault_hook=crash).run_spec(store)
    run = store.load_run()
    assert run["termination_request"]["stage"] == "s4"
    assert run["stages"]["s4"]["status"] == "failed"
    assert run["stages"]["s9"]["status"] == "pending"

    assert Orchestrator({}).resume(RunStore(store.root)) == 20
    assert RunStore(store.root).load_run()["stages"]["s9"]["status"] == "done"


def test_crash_window_s9_commit_finalizes_valid_receipt_with_fresh_instance(tmp_path):
    store = _store(tmp_path)

    class Failing:
        def run(self, context):
            raise ControlledStageFailure({"code": "EXPECTED_FAILURE", "detail": "s9 commit"})

    def crash(point):
        if point == "s9_done_committed":
            raise CrashInjected()

    with pytest.raises(CrashInjected):
        Orchestrator({"s4": Failing()}, fault_hook=crash).run_spec(store)
    run = store.load_run()
    refs = run["stages"]["s9"]["output_refs"]
    assert run["stages"]["s9"]["status"] == "done"
    assert "termination_kind" not in run

    assert Orchestrator({}).resume(RunStore(store.root)) == 20
    resumed = RunStore(store.root).load_run()
    assert resumed["stages"]["s9"]["output_refs"] == refs
    assert resumed["termination_kind"] == "controlled_exit"
