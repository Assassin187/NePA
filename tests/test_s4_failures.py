from pathlib import Path

from nepa.config import load_config
from nepa.llm.client import StructuredOutputError
from nepa.orchestrator import Orchestrator
from nepa.run_store import RunStore, SpecRunInputs
from nepa.stages.s4_planning import S4Controller


ROOT = Path(__file__).parents[1]


def _store(tmp_path):
    return RunStore.initialize_spec_run(
        tmp_path,
        SpecRunInputs(ROOT / "gold_file/specIR.json", ROOT / "gold_file/target.json", ROOT / "gold_file/test_bundle.json"),
        load_config(overrides={"run": {"until": "s4"}}),
    )


class _NoProviderCall:
    def __init__(self):
        self.config = load_config()
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs["role"])
        raise StructuredOutputError("final structured output was truncated")


def test_final_structured_output_failure_is_a_controlled_s4_failure(tmp_path):
    store = _store(tmp_path)
    invoker = _NoProviderCall()

    assert Orchestrator({"s4": S4Controller(invoker)}).run_spec(store) == 20
    run = store.load_run()
    assert run["termination_request"]["reason"]["code"] == "S4_STRUCTURED_OUTPUT_INVALID"
    assert not (store.root / "plan/versions/plan-1.0.0.json").exists()


def test_architecture_context_overflow_stops_before_architecture_provider_call(tmp_path):
    store = _store(tmp_path)
    invoker = _NoProviderCall()

    controller = S4Controller(invoker, context_window_tokens={"claude-opus-5": 1})
    assert Orchestrator({"s4": controller}).run_spec(store) == 20
    run = store.load_run()
    assert run["termination_request"]["reason"]["code"] == "PLAN_CONTEXT_TOO_LARGE"
    assert invoker.calls == []
    assert not (store.root / "plan/active_plan.json").exists()
