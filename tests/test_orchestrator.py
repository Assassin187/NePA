from pathlib import Path

from nepa.config import load_config
import pytest

from nepa.orchestrator import OrchestrationError, Orchestrator, StageResult
from nepa.run_store import RunStore, SpecRunInputs


ROOT = Path(__file__).parents[1]


def _store(tmp_path, until="s6"):
    return RunStore.initialize_spec_run(
        tmp_path,
        SpecRunInputs(ROOT / "gold_file/specIR.json", ROOT / "gold_file/target.json", ROOT / "gold_file/test_bundle.json"),
        load_config(overrides={"run": {"until": until}}),
    )


class RecordingController:
    def __init__(self, order, stage):
        self.order = order
        self.stage = stage

    def run(self, context):
        self.order.append(self.stage)
        ref = context.store.publish_immutable_bytes(f"receipts/{self.stage}.json", self.stage.encode())
        return StageResult(output_refs={"receipt": ref})


def test_serial_s4_s5_s6_sequence_and_planned_stop(tmp_path):
    store = _store(tmp_path)
    order = []
    controller = Orchestrator({stage: RecordingController(order, stage) for stage in ("s4", "s5", "s6")})

    assert controller.run_spec(store) == 0
    run = store.load_run()
    assert order == ["s4", "s5", "s6"]
    assert [run["stages"][stage]["status"] for stage in order] == ["done"] * 3
    assert run["termination_kind"] == "planned_stop"
    assert "outcome" not in run
    assert not (store.root / "report/report.json").exists()


def test_verified_done_stage_is_a_noop(tmp_path):
    store = _store(tmp_path)
    calls = []
    first = Orchestrator({stage: RecordingController(calls, stage) for stage in ("s4", "s5", "s6")})
    first.run_spec(store)
    second = Orchestrator({stage: RecordingController(calls, stage) for stage in ("s4", "s5", "s6")})

    assert second.resume(store) == 0
    assert calls == ["s4", "s5", "s6"]


def test_output_refs_are_verified_at_stage_commit_and_invalid_upstream_transition_is_rejected(tmp_path):
    store = _store(tmp_path)
    calls = []

    class WithOutput:
        def run(self, context):
            ref = context.store.publish_immutable_bytes("plan/s4.receipt", b"receipt")
            return StageResult(output_refs={"receipt": ref})

    controller = Orchestrator({stage: RecordingController(calls, stage) for stage in ("s5", "s6")})
    with pytest.raises(OrchestrationError, match="upstream"):
        controller._transition_running(store, store.load_run(), "s5")

    assert Orchestrator({"s4": WithOutput(), "s5": RecordingController(calls, "s5"), "s6": RecordingController(calls, "s6")}).run_spec(store) == 0
    assert store.load_run()["stages"]["s4"]["output_refs"]["receipt"]["path"] == "plan/s4.receipt"


def test_empty_output_refs_finalize_as_internal_error_and_never_done(tmp_path):
    store = _store(tmp_path)

    class MissingOutput:
        def run(self, context):
            return StageResult()

    assert Orchestrator({"s4": MissingOutput()}).run_spec(store) == 1
    run = store.load_run()
    assert run["termination_kind"] == "internal_error"
    assert run["stages"]["s4"]["status"] == "running"
    assert "output_refs" not in run["stages"]["s4"]
