"""Deterministic M1-1 stage lifecycle, budget, termination, and resume control."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from .config import ConfigSnapshotDrift
from .run_store import ArtifactRef, RunStore, RunStoreError
from .stages.s9_report import publish_controlled_exit_report, validate_controlled_exit_report


class OrchestrationError(RuntimeError):
    """Base class for deterministic controller failures."""


class BudgetExhausted(OrchestrationError):
    """A global budget stopped further ordinary work."""


class CrashInjected(BaseException):
    """Test-only interruption that leaves the last durable commit on disk."""


class ControlledStageFailure(OrchestrationError):
    """A stage reported an expected, controlled process failure."""

    def __init__(self, reason: Mapping[str, str]):
        self.reason = dict(reason)
        super().__init__(self.reason["detail"])


StageFailure = ControlledStageFailure


@dataclass(frozen=True)
class UsageDelta:
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    cached: bool = False

    def __post_init__(self) -> None:
        if self.tokens_in < 0 or self.tokens_out < 0 or self.cost_usd < 0:
            raise ValueError("usage deltas cannot be negative")


@dataclass(frozen=True)
class StageResult:
    output_refs: Mapping[str, ArtifactRef | Mapping[str, Any]] = field(default_factory=dict)
    usage: UsageDelta | None = None


@dataclass(frozen=True)
class StageContext:
    store: RunStore
    stage: str
    run: Mapping[str, Any]
    orchestrator: "Orchestrator"


class StageController(Protocol):
    def run(self, context: StageContext) -> StageResult: ...


def _reason(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


class Orchestrator:
    """Own stage admission, lifecycle transitions, budgets, and terminal routing."""

    def __init__(
        self,
        controllers: Mapping[str, StageController] | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] | None = None,
        clock: Any | None = None,
        fault_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.controllers = dict(controllers or {})
        if clock is not None:
            monotonic = getattr(clock, "monotonic", monotonic)
            utcnow = getattr(clock, "utcnow", utcnow)
        self._monotonic = monotonic
        self._utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self._fault_hook = fault_hook
        self._session_store: RunStore | None = None
        self._last_mono: float | None = None
        self._active_stage: str | None = None

    def _fault(self, point: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(point)

    def _timestamp(self) -> str:
        current = self._utcnow()
        if isinstance(current, str):
            return current
        return current.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _ensure_session(self, store: RunStore) -> None:
        if self._session_store is not store:
            self._session_store = store
            self._last_mono = self._monotonic()

    def _sync_budget(self, store: RunStore, *, enforce: bool = True) -> dict[str, Any]:
        self._ensure_session(store)
        now = self._monotonic()
        previous = self._last_mono if self._last_mono is not None else now
        elapsed = max(0.0, now - previous)
        self._last_mono = now
        run = store.load_run()
        budget = run["budget_used"]
        if elapsed:
            updated = copy.deepcopy(run)
            updated["budget_used"]["wall_clock_s"] += elapsed
            store.replace_run(updated)
            run = updated
        if enforce and self._budget_exhausted(run):
            raise BudgetExhausted("global budget exhausted")
        return run

    @staticmethod
    def _budget_exhausted(run: Mapping[str, Any]) -> bool:
        snapshot = run["config_snapshot"]
        budgets = snapshot["budgets"]
        used = run["budget_used"]
        return (
            used["wall_clock_s"] >= float(budgets["wall_clock_hours"]) * 3600
            or used["cost_usd"] >= float(budgets["max_cost_usd"])
        )

    def admit_external_call(self, store: RunStore) -> None:
        """Synchronize active time and reject a call before it has side effects."""

        self._sync_budget(store, enforce=True)

    def record_external_usage(self, store: RunStore, usage: UsageDelta) -> None:
        """Persist returned usage before allowing a controller to continue."""

        self._ensure_session(store)
        now = self._monotonic()
        previous = self._last_mono if self._last_mono is not None else now
        elapsed = max(0.0, now - previous)
        self._last_mono = now
        run = store.load_run()
        updated = copy.deepcopy(run)
        updated["budget_used"]["wall_clock_s"] += elapsed
        if not usage.cached:
            updated["budget_used"]["tokens_in"] += usage.tokens_in
            updated["budget_used"]["tokens_out"] += usage.tokens_out
            updated["budget_used"]["cost_usd"] += usage.cost_usd
        store.replace_run(updated)
        if self._budget_exhausted(updated):
            raise BudgetExhausted("global budget exhausted after external call")

    @staticmethod
    def _stage_done(store: RunStore, stage: Mapping[str, Any]) -> bool:
        if stage.get("status") != "done":
            return False
        refs = stage.get("output_refs")
        if not isinstance(refs, Mapping) or not refs:
            raise RunStoreError("completed S4-S6 stage has no output_refs")
        store.verify_stage_refs(stage)
        return True

    @staticmethod
    def _normalise_result(result: StageResult | Mapping[str, Any] | None) -> StageResult:
        if result is None:
            return StageResult()
        if isinstance(result, StageResult):
            return result
        if isinstance(result, Mapping):
            usage = result.get("usage")
            if isinstance(usage, Mapping):
                usage = UsageDelta(**dict(usage))
            return StageResult(output_refs=result.get("output_refs", {}), usage=usage)
        raise OrchestrationError("stage controller returned an unsupported result")

    def _transition_running(self, store: RunStore, run: dict[str, Any], stage_name: str) -> dict[str, Any]:
        stage = run["stages"][stage_name]
        if stage["status"] not in {"pending", "failed"}:
            raise OrchestrationError(f"invalid transition {stage_name}:{stage['status']} -> running")
        order = ("s4", "s5", "s6")
        index = order.index(stage_name)
        if index and run["stages"][order[index - 1]]["status"] != "done":
            raise OrchestrationError(f"upstream stage {order[index - 1]} is not committed")
        updated = copy.deepcopy(run)
        updated["stages"][stage_name].update({"status": "running", "started_at": self._timestamp(), "ended_at": None, "error": None})
        updated["stages"][stage_name].pop("output_refs", None)
        store.replace_run(updated)
        self._fault(f"{stage_name}_running_committed")
        store.append_stage_event({"run_id": run["run_id"], "stage": stage_name, "event": "started"})
        return updated

    def _commit_stage(self, store: RunStore, run: dict[str, Any], stage_name: str, result: StageResult) -> dict[str, Any]:
        refs: dict[str, dict[str, str]] = {}
        if not isinstance(result.output_refs, Mapping) or not result.output_refs:
            raise OrchestrationError(f"{stage_name} cannot commit without output_refs")
        for key, value in result.output_refs.items():
            refs[str(key)] = ArtifactRef.from_value(value).as_dict()
            store.verify_ref(refs[str(key)])
        updated = copy.deepcopy(run)
        stage = updated["stages"][stage_name]
        if stage["status"] != "running":
            raise OrchestrationError(f"stage {stage_name} is not running at commit")
        stage.update({"status": "done", "ended_at": self._timestamp(), "error": None})
        if refs:
            stage["output_refs"] = refs
        else:
            stage.pop("output_refs", None)
        store.replace_run(updated)
        self._fault(f"{stage_name}_done_committed")
        store.append_stage_event({"run_id": run["run_id"], "stage": stage_name, "event": "done", "output_refs": refs})
        return updated

    def _persist_request(self, store: RunStore, run: dict[str, Any], stage_name: str, reason: Mapping[str, str], *, failed: bool) -> dict[str, Any]:
        request = {
            "kind": "controlled_exit",
            "stage": stage_name,
            "requested_at": self._timestamp(),
            "reason": dict(reason),
        }
        existing = run.get("termination_request")
        if existing is not None and existing != request:
            # requested_at is not a decision input; identical reason/stage is an idempotent replay.
            if existing.get("stage") != stage_name or existing.get("reason") != dict(reason):
                raise OrchestrationError("conflicting controlled-exit request")
            request = existing
        updated = copy.deepcopy(run)
        stage = updated["stages"][stage_name]
        if failed:
            stage.update({"status": "failed", "ended_at": self._timestamp(), "error": reason["detail"]})
        else:
            stage.update({"status": "pending", "ended_at": None, "error": reason["detail"]})
        updated["termination_request"] = request
        updated.pop("termination_kind", None)
        updated.pop("outcome", None)
        updated.pop("exit_code", None)
        store.replace_run(updated)
        self._fault("termination_request_committed")
        store.append_stage_event({"run_id": run["run_id"], "stage": stage_name, "event": "controlled_exit_requested", "reason": dict(reason)})
        return updated

    def _finalize_internal_error(self, store: RunStore, run: dict[str, Any], detail: str) -> int:
        updated = copy.deepcopy(run)
        updated["termination_kind"] = "internal_error"
        updated["exit_code"] = 1
        updated.pop("outcome", None)
        store.replace_run(updated)
        store.append_stage_event({"run_id": run["run_id"], "event": "internal_error", "detail": detail})
        return 1

    def _finalize_planned_stop(self, store: RunStore, run: dict[str, Any]) -> int:
        updated = copy.deepcopy(run)
        updated["termination_kind"] = "planned_stop"
        updated["exit_code"] = 0
        updated.pop("outcome", None)
        updated.pop("termination_request", None)
        store.replace_run(updated)
        return 0

    def _finalize_controlled_exit(self, store: RunStore, run: dict[str, Any]) -> int:
        request = run["termination_request"]
        outcome = "degraded" if "BUDGET" in request["reason"]["code"] else "failed"
        updated = copy.deepcopy(run)
        updated["termination_kind"] = "controlled_exit"
        updated["outcome"] = outcome
        updated["exit_code"] = 10 if outcome == "degraded" else 20
        store.replace_run(updated)
        return updated["exit_code"]

    def _run_s9(self, store: RunStore, run: dict[str, Any]) -> int:
        request = run.get("termination_request")
        if not isinstance(request, dict) or run["stages"][request["stage"]]["status"] not in {"failed", "pending"}:
            return self._finalize_internal_error(store, run, "controlled-exit request is not bound to a failed or pending stage")
        if run["stages"]["s9"]["status"] == "done":
            if validate_controlled_exit_report(store, run):
                return self._finalize_controlled_exit(store, run)
            return self._finalize_internal_error(store, run, "completed S9 report is corrupt")
        updated = copy.deepcopy(run)
        updated["stages"]["s9"].update({"status": "running", "started_at": self._timestamp(), "ended_at": None, "error": None})
        store.replace_run(updated)
        store.append_stage_event({"run_id": run["run_id"], "stage": "s9", "event": "started", "enforce": False})
        try:
            report_ref = publish_controlled_exit_report(store)
            self._fault("s9_report_published")
            md_path = store._confined("report/report.md")
            md_ref = ArtifactRef("report/report.md", __import__("hashlib").sha256(md_path.read_bytes()).hexdigest())
            committed = copy.deepcopy(updated)
            committed["stages"]["s9"].update({
                "status": "done",
                "ended_at": self._timestamp(),
                "error": None,
                "output_refs": {"report_json": report_ref.as_dict(), "report_md": md_ref.as_dict()},
            })
            store.replace_run(committed)
            self._fault("s9_done_committed")
            store.append_stage_event({"run_id": run["run_id"], "stage": "s9", "event": "done"})
            self._sync_budget(store, enforce=False)
            self._fault("terminal_before_finalize")
            return self._finalize_controlled_exit(store, committed)
        except Exception as exc:
            return self._finalize_internal_error(store, store.load_run(), f"S9 failed: {exc}")

    def _planned_target_reached(self, run: Mapping[str, Any], stage_name: str) -> bool:
        until = run["config_snapshot"]["run"].get("until")
        return until == stage_name or (until == "s3" and stage_name == "s3")

    def _run_locked(self, store: RunStore, *, resume: bool) -> int:
        self._ensure_session(store)
        run = store.load_run()
        if "termination_kind" in run:
            if run["termination_kind"] == "controlled_exit" and not validate_controlled_exit_report(store, run):
                return self._finalize_internal_error(store, run, "terminal controlled-exit report is corrupt")
            return int(run["exit_code"])
        try:
            store.verify_frozen_inputs()
        except RunStoreError as exc:
            run = store.load_run()
            if run.get("termination_request"):
                return self._run_s9(store, run)
            run = self._persist_request(
                store,
                run,
                "s4",
                _reason("FROZEN_INPUT_DRIFT", str(exc)),
                failed=False,
            )
            return self._run_s9(store, run)
        if resume:
            run = self._reconcile_orphaned(store, run)
        if run.get("termination_request"):
            return self._run_s9(store, run)
        if self._planned_target_reached(run, "s3"):
            return self._finalize_planned_stop(store, run)
        for stage_name in ("s4", "s5", "s6"):
            run = store.load_run()
            if run.get("termination_request"):
                return self._run_s9(store, run)
            stage = run["stages"][stage_name]
            if stage["status"] == "done":
                try:
                    self._stage_done(store, stage)
                except RunStoreError as exc:
                    return self._finalize_internal_error(store, run, str(exc))
            else:
                try:
                    self._sync_budget(store, enforce=True)
                except BudgetExhausted:
                    run = store.load_run()
                    run = self._persist_request(store, run, stage_name, _reason("BUDGET_EXHAUSTED", f"Global budget exhausted before {stage_name}."), failed=False)
                    return self._run_s9(store, run)
                controller = self.controllers.get(stage_name)
                if controller is None:
                    return self._finalize_internal_error(store, run, f"no controller registered for {stage_name}")
                self._active_stage = stage_name
                try:
                    running = self._transition_running(store, run, stage_name)
                    result = self._normalise_result(controller.run(StageContext(store, stage_name, running, self)))
                    self._fault(f"{stage_name}_output_published")
                    if result.usage is not None:
                        self.record_external_usage(store, result.usage)
                    run = self._commit_stage(store, store.load_run(), stage_name, result)
                except ControlledStageFailure as exc:
                    run = self._persist_request(store, store.load_run(), stage_name, exc.reason, failed=True)
                    return self._run_s9(store, run)
                except BudgetExhausted:
                    run = self._persist_request(store, store.load_run(), stage_name, _reason("BUDGET_EXHAUSTED", f"Global budget exhausted during {stage_name}."), failed=True)
                    return self._run_s9(store, run)
                except Exception as exc:
                    return self._finalize_internal_error(store, store.load_run(), str(exc))
                finally:
                    self._active_stage = None
            self._sync_budget(store, enforce=False)
            run = store.load_run()
            if self._planned_target_reached(run, stage_name):
                return self._finalize_planned_stop(store, run)
        # M1-1 owns S4-S6 only. A caller that did not seal an explicit M1 stop is left non-terminal.
        return 0

    def run_spec(self, store: RunStore) -> int:
        with store.controller_lock():
            return self._run_locked(store, resume=False)

    def _reconcile_orphaned(self, store: RunStore, run: dict[str, Any]) -> dict[str, Any]:
        changed = False
        updated = copy.deepcopy(run)
        for stage_name, stage in updated["stages"].items():
            if stage["status"] == "running":
                stage.update({"status": "failed", "ended_at": self._timestamp(), "error": "process crashed mid-stage"})
                changed = True
        if changed:
            store.replace_run(updated)
        return updated

    def resume(self, store: RunStore) -> int:
        with store.controller_lock():
            try:
                run = store.load_run()
            except ConfigSnapshotDrift:
                raise
            if "termination_kind" in run:
                if run["termination_kind"] == "controlled_exit" and not validate_controlled_exit_report(store, run):
                    return self._finalize_internal_error(store, run, "terminal controlled-exit report is corrupt")
                return int(run["exit_code"])
            return self._run_locked(store, resume=True)
