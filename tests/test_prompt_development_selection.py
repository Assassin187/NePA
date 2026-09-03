import pytest

from nepa.calibration.s4_prompt_development import PromptSelectionTie, _compare_fallback, _fallback_tuple


def _assessment(values):
    return {"models": {name: {"p2": values[0], "p1": values[1], "arch_semantic_first_pass_rate": values[2], "schema_after_format_repair_rate": values[3], "tuple": [values[0], values[1], values[2], values[3], values[4]]} for name in ("qwen", "claude", "deepseek")}}


def test_fallback_uses_minima_then_lower_total_cost_without_average():
    better = _fallback_tuple(_assessment((1.0, 1.0, 0.9, 1.0, -2.0)))
    worse = _fallback_tuple(_assessment((1.0, 1.0, 0.8, 1.0, -1.0)))
    assert _compare_fallback(better, worse) == 1
    assert _compare_fallback(worse, better) == -1


def test_exact_fallback_tuple_is_detectable_as_a_tie():
    left = _fallback_tuple(_assessment((1.0, 1.0, 0.8, 1.0, -1.0)))
    right = _fallback_tuple(_assessment((1.0, 1.0, 0.8, 1.0, -1.0)))
    assert _compare_fallback(left, right) == 0
