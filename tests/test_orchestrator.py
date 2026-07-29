"""M1-1 可恢复全局预算测试（设计文档 4.7、4.8）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nepa.config import BudgetsConfig
from nepa.llm.client import LLMResponse
from nepa.orchestrator import BudgetExhausted, M1ResumeCoordinator, RunBudget
from nepa.reporting import write_partial_report
from nepa.run_store import RunStore, create_run


class _Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _inputs() -> dict[str, Any]:
    def asset(name: str, path: str) -> dict[str, str]:
        return {
            "id": name,
            "version": "1.0",
            "path": path,
            "sha256": "ab" * 32,
        }

    return {
        "spec": {"path": "spec.json", "sha256": "cd" * 32},
        "target_profile": asset("target", "inputs/target.json"),
        "language_profile": asset("language", "inputs/language.json"),
        "test_bundle": asset("tests", "inputs/test_bundle.json"),
    }


def _store(tmp_path: Path) -> RunStore:
    return create_run(tmp_path, "sample", "spec-run", inputs=_inputs())


def test_active_time_accumulates_across_resume_but_offline_time_does_not(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first_clock = _Clock(100.0)
    first = RunBudget(store, BudgetsConfig(), clock=first_clock)
    first_clock.advance(12.5)
    first.checkpoint()

    reloaded = RunStore.load(store.run_dir)
    resumed_clock = _Clock(50_000.0)
    resumed = RunBudget(reloaded, BudgetsConfig(), clock=resumed_clock)
    resumed_clock.advance(2.5)
    snapshot = resumed.checkpoint()

    assert snapshot.wall_clock_s == pytest.approx(15.0)


def test_llm_usage_and_active_time_are_persisted_in_one_budget_update(tmp_path: Path) -> None:
    store = _store(tmp_path)
    clock = _Clock()
    budget = RunBudget(store, BudgetsConfig(), clock=clock)
    clock.advance(3.0)

    snapshot = budget.record_llm_response(
        LLMResponse(text="ok", tokens_in=120, tokens_out=30, cost_usd=0.25)
    )

    assert snapshot.wall_clock_s == pytest.approx(3.0)
    assert snapshot.cost_usd == pytest.approx(0.25)
    assert snapshot.tokens_in == 120
    assert snapshot.tokens_out == 30
    assert RunStore.load(store.run_dir).meta.budget_used.cost_usd == pytest.approx(0.25)


def test_cached_replay_counts_active_time_but_not_provider_usage(tmp_path: Path) -> None:
    store = _store(tmp_path)
    clock = _Clock()
    budget = RunBudget(store, BudgetsConfig(), clock=clock)
    clock.advance(0.5)

    snapshot = budget.record_llm_response(
        LLMResponse(
            text="cached",
            tokens_in=120,
            tokens_out=30,
            cost_usd=0.0,
            cached=True,
        )
    )

    assert snapshot.wall_clock_s == pytest.approx(0.5)
    assert snapshot.cost_usd == 0
    assert snapshot.tokens_in == 0
    assert snapshot.tokens_out == 0


@pytest.mark.parametrize(
    ("limits", "advance", "response", "dimension"),
    [
        (
            BudgetsConfig(wall_clock_hours=1 / 3600, max_cost_usd=20),
            1.0,
            None,
            "wall_clock_s",
        ),
        (
            BudgetsConfig(wall_clock_hours=4, max_cost_usd=0.5),
            0.1,
            LLMResponse(text="ok", cost_usd=0.5),
            "cost_usd",
        ),
    ],
)
def test_budget_limit_is_persisted_before_controlled_exhaustion_signal(
    tmp_path: Path,
    limits: BudgetsConfig,
    advance: float,
    response: LLMResponse | None,
    dimension: str,
) -> None:
    store = _store(tmp_path)
    clock = _Clock()
    budget = RunBudget(store, limits, clock=clock)
    clock.advance(advance)

    with pytest.raises(BudgetExhausted) as raised:
        if response is None:
            budget.checkpoint()
        else:
            budget.record_llm_response(response)

    assert raised.value.dimension == dimension
    persisted = RunStore.load(store.run_dir).meta.budget_used
    if dimension == "wall_clock_s":
        assert persisted.wall_clock_s == pytest.approx(1.0)
    else:
        assert persisted.cost_usd == pytest.approx(0.5)


def test_monotonic_clock_regression_is_internal_error_not_budget_exit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    clock = _Clock()
    budget = RunBudget(store, BudgetsConfig(), clock=clock)
    clock.advance(-1)

    with pytest.raises(RuntimeError, match="moved backwards"):
        budget.checkpoint()


def _resume_coordinator(store: RunStore) -> M1ResumeCoordinator:
    return M1ResumeCoordinator(store, RunBudget(store, BudgetsConfig(), clock=_Clock()))


def test_resume_with_request_skips_to_budget_exempt_s9_and_finalizes(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.request_controlled_exit("s4", "PLAN_NOT_SEALED", "No valid Plan.")

    result = _resume_coordinator(store).resume_terminal_windows()

    assert result.action == "controlled_exit"
    assert result.exit_code == 20
    assert store.meta.stages["s9"].status == "done"
    assert store.meta.termination_kind == "controlled_exit"
    assert store.meta.outcome == "failed"
    assert (store.run_dir / "report" / "report.json").is_file()


def test_s9_remains_runnable_after_global_budget_is_already_exhausted(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.add_budget_used(wall_clock_s=1.0)
    store.request_controlled_exit("s4", "PLAN_NOT_SEALED", "No valid Plan.")
    limits = BudgetsConfig(wall_clock_hours=1 / 3600, max_cost_usd=20)
    coordinator = M1ResumeCoordinator(store, RunBudget(store, limits, clock=_Clock()))

    result = coordinator.resume_terminal_windows()

    assert result.action == "controlled_exit"
    assert result.exit_code == 20


def test_resume_recovers_orphaned_s9_then_reruns_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.request_controlled_exit("s4", "PLAN_NOT_SEALED", "No valid Plan.")
    store.set_stage_status("s9", "running")

    result = _resume_coordinator(store).resume_terminal_windows()

    assert result.recovered_stages == ("s9",)
    assert result.action == "controlled_exit"
    assert store.meta.stages["s9"].status == "done"


def test_resume_finalizes_valid_s9_done_window_without_rewriting_report(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.request_controlled_exit("s4", "PLAN_NOT_SEALED", "No valid Plan.")
    ref = write_partial_report(store, outcome="failed")
    store.set_stage_status("s9", "running")
    store.set_stage_status("s9", "done", output_refs={"report": ref})
    before = (store.run_dir / "report" / "report.json").read_bytes()

    result = _resume_coordinator(store).resume_terminal_windows()

    assert result.action == "controlled_exit"
    assert store.meta.termination_kind == "controlled_exit"
    assert (store.run_dir / "report" / "report.json").read_bytes() == before


def test_s9_done_with_invalid_receipt_becomes_internal_error_and_keeps_request(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.request_controlled_exit("s4", "PLAN_NOT_SEALED", "No valid Plan.")
    ref = write_partial_report(store, outcome="failed")
    store.set_stage_status("s9", "running")
    store.set_stage_status("s9", "done", output_refs={"report": ref})
    (store.run_dir / "report" / "report.json").write_text("{}", encoding="utf-8")

    result = _resume_coordinator(store).resume_terminal_windows()

    assert result.action == "internal_error"
    assert store.meta.termination_kind == "internal_error"
    assert store.meta.termination_request is not None


def test_s9_producer_bug_becomes_internal_error_and_keeps_request(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.request_controlled_exit("s4", "PLAN_NOT_SEALED", "No valid Plan.")

    def broken_writer(*args: object, **kwargs: object) -> dict[str, str]:
        raise RuntimeError("producer bug")

    coordinator = M1ResumeCoordinator(
        store,
        RunBudget(store, BudgetsConfig(), clock=_Clock()),
        report_writer=broken_writer,
    )
    result = coordinator.resume_terminal_windows()

    assert result.action == "internal_error"
    assert store.meta.termination_kind == "internal_error"
    assert store.meta.termination_request is not None
    assert store.meta.stages["s9"].status == "failed"


def test_resume_uses_frozen_until_to_close_planned_stop_crash_window(
    tmp_path: Path,
) -> None:
    store = create_run(
        tmp_path,
        "sample",
        "spec-run",
        inputs=_inputs(),
        config_snapshot={"run": {"until": "s6"}},
    )
    store.set_stage_status("s6", "running")
    store.set_stage_status("s6", "done")

    result = _resume_coordinator(store).resume_terminal_windows()

    assert result.action == "planned_stop"
    assert result.exit_code == 0
    assert store.meta.termination_kind == "planned_stop"
    assert store.meta.outcome is None
    assert store.meta.termination_request is None
    assert store.meta.stages["s7"].status == "pending"
