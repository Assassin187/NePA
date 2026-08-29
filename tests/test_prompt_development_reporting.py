import copy

from nepa.calibration.s4_prompt_development import _screen_model


def _screened(initial_failures):
    gates = {f"arch_{index:02d}": {"initial_passed": True} for index in range(1, 16)}
    for gate in initial_failures:
        gates[gate]["initial_passed"] = False
    return {"status": "complete", "metrics": {"schema_after_format_repair_rate": 1.0, "p1": 1.0, "arch_semantic_first_pass_rate": 1.0}, "usage": {"truncated": 0, "cost_usd": 0}, "model_identity": {"provider": "provider", "model": "model", "versions": ["version"], "parameter_support": {}}, "trial_metrics": [{"gates": copy.deepcopy(gates)}, {"gates": copy.deepcopy(gates)}]}


def test_screening_keeps_repeated_initial_gate_failures_as_diagnostics():
    one = _screened([])
    one["trial_metrics"][0]["gates"]["arch_07"]["initial_passed"] = False
    assert _screen_model(one)["screening_pass"] is True
    two = _screened(["arch_07"])
    assert _screen_model(two)["screening_pass"] is True
    assert _screen_model(two)["repeated_gate_failures"] == ["arch_07"]
