from nepa.calibration.s4_prompt_development import screen_recovery_report


def _report(p0=0.2, p1=1.0):
    return {"status": "complete", "trial_count": 5, "metrics": {"schema_after_format_repair_rate": 1.0, "p0": p0, "p1": p1}, "usage": {"truncated": 0}}


def test_low_p0_is_diagnostic_when_bounded_repair_gate_passes():
    assert screen_recovery_report(_report(p0=0.2), locality_complete=True, repair_regressions=0) is True


def test_p1_locality_regression_and_truncation_are_independent_hard_gates():
    assert screen_recovery_report(_report(p1=0.8), locality_complete=True, repair_regressions=0) is True
    assert screen_recovery_report(_report(), locality_complete=False, repair_regressions=0) is False
    assert screen_recovery_report(_report(), locality_complete=True, repair_regressions=1) is False
    truncated = _report()
    truncated["usage"]["truncated"] = 1
    assert screen_recovery_report(truncated, locality_complete=True, repair_regressions=0) is False
