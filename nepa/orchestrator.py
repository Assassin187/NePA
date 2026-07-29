"""M1 运行编排公共能力：可恢复全局预算（设计 4.7、4.8）。"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from nepa.config import BudgetsConfig
from nepa.llm.client import LLMResponse
from nepa.reporting import (
    ReportValidationError,
    classify_partial_outcome,
    validate_controlled_report_ref,
    write_partial_report,
)
from nepa.run_store import RunStore

BudgetDimension = Literal["wall_clock_s", "cost_usd"]
ResumeAction = Literal[
    "continue",
    "already_terminal",
    "planned_stop",
    "controlled_exit",
    "internal_error",
]


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    wall_clock_s: float
    cost_usd: float
    tokens_in: int
    tokens_out: int


class BudgetExhausted(RuntimeError):
    """预期的全局预算耗尽信号；上层必须路由受控 S9，而非 internal_error。"""

    def __init__(self, dimension: BudgetDimension, used: float, limit: float) -> None:
        super().__init__(f"global budget exhausted: {dimension} used={used} limit={limit}")
        self.dimension = dimension
        self.used = used
        self.limit = limit


@dataclass(frozen=True, slots=True)
class ResumeDisposition:
    """resume 终态窗口处理结果；continue 表示应进入普通阶段路由。"""

    action: ResumeAction
    exit_code: int | None
    recovered_stages: tuple[str, ...] = ()


class RunBudget:
    """把本 controller 会话的 monotonic 增量累加到 Run v2。

    构造对象不会把两次 resume 之间的离线时间计入预算。调用者必须在外部
    操作前调用 ``checkpoint``，并把 ``record_llm_response`` 作为 AgentRunner
    的 usage callback；正常退出前再 checkpoint 一次。
    """

    def __init__(
        self,
        store: RunStore,
        limits: BudgetsConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limits.wall_clock_hours <= 0 or limits.max_cost_usd <= 0:
            raise ValueError("global wall-clock and cost budgets must be positive")
        self._store = store
        self._wall_limit_s = limits.wall_clock_hours * 3600.0
        self._cost_limit = limits.max_cost_usd
        self._clock = clock
        self._last_clock = clock()

    def _active_delta(self) -> float:
        now = self._clock()
        if now < self._last_clock:
            raise RuntimeError("monotonic clock moved backwards")
        delta = now - self._last_clock
        self._last_clock = now
        return delta

    @staticmethod
    def _snapshot(store: RunStore) -> BudgetSnapshot:
        used = store.meta.budget_used
        return BudgetSnapshot(
            wall_clock_s=used.wall_clock_s,
            cost_usd=used.cost_usd,
            tokens_in=used.tokens_in,
            tokens_out=used.tokens_out,
        )

    def _enforce(self, snapshot: BudgetSnapshot) -> None:
        if snapshot.wall_clock_s >= self._wall_limit_s:
            raise BudgetExhausted(
                "wall_clock_s",
                snapshot.wall_clock_s,
                self._wall_limit_s,
            )
        if snapshot.cost_usd >= self._cost_limit:
            raise BudgetExhausted("cost_usd", snapshot.cost_usd, self._cost_limit)

    def checkpoint(self, *, enforce: bool = True) -> BudgetSnapshot:
        """登记本地活跃时间；默认同时执行调用前/阶段边界硬门。"""
        self._store.add_budget_used(wall_clock_s=self._active_delta())
        snapshot = self._snapshot(self._store)
        if enforce:
            self._enforce(snapshot)
        return snapshot

    def record_llm_response(self, response: LLMResponse) -> BudgetSnapshot:
        """登记一次逻辑 LLM 调用；缓存重放不重复累计 provider token。"""
        provider_tokens_in = 0 if response.cached else response.tokens_in
        provider_tokens_out = 0 if response.cached else response.tokens_out
        self._store.add_budget_used(
            wall_clock_s=self._active_delta(),
            cost_usd=response.cost_usd,
            tokens_in=provider_tokens_in,
            tokens_out=provider_tokens_out,
        )
        snapshot = self._snapshot(self._store)
        self._enforce(snapshot)
        return snapshot


class M1ResumeCoordinator:
    """M1 终态恢复路由：orphan running、受控 S9 与 planned stop。

    调用方必须先确认原 controller 已不活跃并持有该 run 的互斥锁。
    普通阶段的 failed→running 重试仍由具体阶段 controller 负责。
    """

    def __init__(
        self,
        store: RunStore,
        budget: RunBudget,
        *,
        report_writer: Any = write_partial_report,
    ) -> None:
        self._store = store
        self._budget = budget
        self._report_writer = report_writer

    def resume_terminal_windows(self) -> ResumeDisposition:
        """修复 crash 窗口；若无终态窗口则返回 continue。"""
        terminal = self._store.meta.termination_kind
        if terminal is not None:
            return ResumeDisposition(
                "already_terminal",
                self._store.meta.exit_code,
            )

        recovered = self._store.recover_orphaned_running_stages()
        if self._store.meta.termination_request is not None:
            return self._resume_controlled_exit(recovered)

        until = self._frozen_until()
        if until is not None and self._store.meta.stages[until].status == "done":
            self._store.finalize("planned_stop", 0)
            return ResumeDisposition("planned_stop", 0, recovered)
        return ResumeDisposition("continue", None, recovered)

    def _frozen_until(self) -> Literal["s3", "s6"] | None:
        run_config = self._store.meta.config_snapshot.get("run", {})
        if not isinstance(run_config, dict):
            raise TypeError("config_snapshot.run must be an object")
        until = run_config.get("until")
        if until is None:
            return None
        if until not in ("s3", "s6"):
            raise ValueError("config_snapshot.run.until must be s3, s6, or null")
        expected = "s6" if self._store.meta.entry == "spec-run" else "s3"
        if until != expected:
            raise ValueError(
                f"{self._store.meta.entry} does not support --until {until}; expected {expected}"
            )
        return until

    def _resume_controlled_exit(
        self,
        recovered: tuple[str, ...],
    ) -> ResumeDisposition:
        s9 = self._store.meta.stages["s9"]
        if s9.status == "done":
            refs = s9.output_refs or {}
            try:
                report = validate_controlled_report_ref(
                    self._store,
                    refs.get("report"),
                )
                outcome = classify_partial_outcome(self._store)
                if report.get("outcome") != outcome:
                    raise ReportValidationError(
                        "report outcome does not match deterministic run classification"
                    )
                self._budget.checkpoint(enforce=False)
                exit_code = 10 if outcome == "degraded" else 20
                self._store.finalize("controlled_exit", exit_code, outcome=outcome)
                return ResumeDisposition("controlled_exit", exit_code, recovered)
            # 完成判定与 finalize 同属 S9 终态保护边界。
            except Exception:  # noqa: BLE001
                if self._store.meta.termination_kind is None:
                    self._store.finalize("internal_error", 1)
                return ResumeDisposition("internal_error", 1, recovered)

        try:
            self._budget.checkpoint(enforce=False)
            if not self._store.begin_stage("s9"):
                raise RuntimeError("S9 unexpectedly became terminal before report generation")
            outcome = classify_partial_outcome(self._store)
            report_ref = self._report_writer(self._store, outcome=outcome)
            self._budget.checkpoint(enforce=False)
            self._store.set_stage_status(
                "s9",
                "done",
                output_refs={"report": report_ref},
            )
            exit_code = 10 if outcome == "degraded" else 20
            self._store.finalize("controlled_exit", exit_code, outcome=outcome)
            return ResumeDisposition("controlled_exit", exit_code, recovered)
        # S9 是终态保护边界：任何 producer 实现异常都必须落为 internal_error。
        except Exception as exc:  # noqa: BLE001
            if self._store.meta.stages["s9"].status == "running":
                self._store.set_stage_status(
                    "s9",
                    "failed",
                    error=f"S9 report generation failed: {exc}",
                )
            if self._store.meta.termination_kind is None:
                self._store.finalize("internal_error", 1)
            return ResumeDisposition("internal_error", 1, recovered)
