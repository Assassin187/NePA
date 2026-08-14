import json
from pathlib import Path

from nepa.config import load_config
from nepa.orchestrator import ControlledStageFailure, Orchestrator, StageResult
from nepa.run_store import RunStore, SpecRunInputs


ROOT = Path(__file__).parents[1]


def _store(tmp_path, until="s6"):
    return RunStore.initialize_spec_run(
        tmp_path,
        SpecRunInputs(ROOT / "gold_file/specIR.json", ROOT / "gold_file/target.json", ROOT / "gold_file/test_bundle.json"),
        load_config(overrides={"run": {"until": until}}),
    )


class Failing:
    def __init__(self, reason):
        self.reason = reason

    def run(self, context):
        raise ControlledStageFailure(self.reason)


def test_structured_stage_failure_persists_request_before_s9(tmp_path):
    store = _store(tmp_path)
    controller = Orchestrator({"s4": Failing({"code": "STAGE_FAILED", "detail": "structured failure"})})

    assert controller.run_spec(store) == 20
    run = store.load_run()
    assert run["termination_kind"] == "controlled_exit"
    assert run["exit_code"] == 20
    assert run["termination_request"]["reason"] == {"code": "STAGE_FAILED", "detail": "structured failure"}
    assert run["stages"]["s4"]["status"] == "failed"
    assert run["stages"]["s9"]["status"] == "done"


def test_structured_s6_failure_uses_the_same_controlled_exit_path(tmp_path):
    store = _store(tmp_path)

    class S6Failure:
        def run(self, context):
            if context.stage == "s6":
                raise ControlledStageFailure({"code": "S6_STRUCTURED_FAILURE", "detail": "s6 failed"})
            ref = context.store.publish_immutable_bytes(f"receipts/{context.stage}.json", context.stage.encode())
            return StageResult(output_refs={"receipt": ref})

    controller = Orchestrator({stage: S6Failure() for stage in ("s4", "s5", "s6")})
    assert controller.run_spec(store) == 20
    run = store.load_run()
    assert run["termination_request"]["stage"] == "s6"
    assert run["stages"]["s6"]["status"] == "failed"
    assert run["stages"]["s4"]["status"] == "done"
    assert run["stages"]["s5"]["status"] == "done"


def test_internal_error_has_no_process_outcome_or_report(tmp_path):
    store = _store(tmp_path)

    class Broken:
        def run(self, context):
            raise RuntimeError("invariant broke")

    assert Orchestrator({"s4": Broken()}).run_spec(store) == 1
    run = store.load_run()
    assert run["termination_kind"] == "internal_error"
    assert "outcome" not in run
    assert not (store.root / "report/report.json").exists()


def test_budget_controlled_exit_ordering_enters_s9_without_enforcing_again(tmp_path):
    store = _store(tmp_path)
    budget_store = RunStore.initialize_spec_run(
        tmp_path / "budget",
        SpecRunInputs(ROOT / "gold_file/specIR.json", ROOT / "gold_file/target.json", ROOT / "gold_file/test_bundle.json"),
        load_config(overrides={"run": {"until": "s6"}, "budgets": {"max_cost_usd": 0}}),
    )

    assert Orchestrator({}).run_spec(budget_store) == 10
    events = [json.loads(line) for line in (budget_store.root / "trace/stage_events.ndjson").read_text().splitlines()]
    names = [event["event"] for event in events]
    assert names.index("controlled_exit_requested") < names.index("started", names.index("controlled_exit_requested"))
    assert budget_store.load_run()["stages"]["s9"]["status"] == "done"


def test_frozen_input_drift_is_controlled_before_s4(tmp_path):
    store = _store(tmp_path)
    (store.root / "inputs/target.json").write_text("{}", encoding="utf-8")
    calls = []

    class Unexpected:
        def run(self, context):
            calls.append(context.stage)

    assert Orchestrator({"s4": Unexpected()}).run_spec(store) == 20
    run = store.load_run()
    assert calls == []
    assert run["termination_request"]["stage"] == "s4"
    assert run["stages"]["s4"]["status"] == "pending"


def test_missing_frozen_input_is_controlled_before_s4(tmp_path):
    store = _store(tmp_path)
    (store.root / "inputs/test_bundle.json").unlink()
    calls = []

    class Unexpected:
        def run(self, context):
            calls.append(context.stage)

    assert Orchestrator({"s4": Unexpected()}).run_spec(store) == 20
    run = store.load_run()
    assert calls == []
    assert run["termination_request"]["stage"] == "s4"
    assert run["termination_request"]["reason"]["code"] == "FROZEN_INPUT_DRIFT"
    assert run["stages"]["s4"]["status"] == "pending"
