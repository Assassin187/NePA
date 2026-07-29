"""从持久工件重建运行状态，不依赖 controller 内存。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from nepa.run_store import RunStore


class StatusError(RuntimeError):
    """运行状态工件缺失或损坏。"""


def resolve_run_dir(value: str | Path, *, runs_root: str | Path = "runs") -> Path:
    """接受显式 run 目录，或默认 runs root 下的 run_id。"""
    candidate = Path(value)
    if candidate.is_dir():
        return candidate
    resolved = Path(runs_root) / candidate
    if resolved.is_dir():
        return resolved
    raise StatusError(f"找不到运行目录: {value}")


def _load_optional_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StatusError(f"{path} 不是可读的 JSON object: {exc}") from exc
    if not isinstance(value, dict):
        raise StatusError(f"{path} 顶层必须为 JSON object")
    return value


def _task_progress(plan_state: dict[str, Any] | None) -> dict[str, Any] | None:
    if plan_state is None:
        return None
    tasks = plan_state.get("tasks")
    if not isinstance(tasks, list):
        raise StatusError("plan/plan_state.json 缺少 tasks array")
    counts: Counter[str] = Counter()
    for index, task in enumerate(tasks):
        if not isinstance(task, dict) or not isinstance(task.get("status"), str):
            raise StatusError(f"plan/plan_state.json tasks[{index}] 缺少 status")
        counts[task["status"]] += 1
    ordered = (
        "pending",
        "in_progress",
        "done",
        "blocked",
        "blocked_by_dependency",
    )
    return {
        "total": len(tasks),
        "counts": {name: counts.get(name, 0) for name in ordered},
    }


def build_run_status(run_dir: str | Path) -> dict[str, Any]:
    """联合 run.json、S4 checkpoint 与 Plan State 生成只读状态快照。"""
    root = Path(run_dir)
    try:
        store = RunStore.load(root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise StatusError(f"无法加载 {root / 'run.json'}: {exc}") from exc

    s4_state = _load_optional_object(root / "plan" / "_s4" / "s4_state.json")
    plan_state = _load_optional_object(root / "plan" / "plan_state.json")
    stages = {
        name: {
            "status": state.status,
            **({"error": state.error} if state.error is not None else {}),
        }
        for name, state in store.meta.stages.items()
    }
    current_stage = None
    if store.meta.termination_kind is None:
        current_stage = store.first_incomplete_stage()

    result: dict[str, Any] = {
        "run_id": store.run_id,
        "entry": store.meta.entry,
        "termination_kind": store.meta.termination_kind,
        "outcome": store.meta.outcome,
        "exit_code": store.meta.exit_code,
        "current_stage": current_stage,
        "stages": stages,
        "budget_used": store.meta.budget_used.model_dump(mode="json"),
        "task_progress": _task_progress(plan_state),
    }
    if s4_state is not None:
        result["s4"] = {
            key: s4_state[key]
            for key in ("phase", "status")
            if key in s4_state
        }
    else:
        result["s4"] = None
    return result
