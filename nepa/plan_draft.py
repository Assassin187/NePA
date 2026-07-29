"""S4 PlanDraftIR normalization and deterministic task-id assignment."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from heapq import heapify, heappop, heappush
from typing import Any


class PlanDraftError(ValueError):
    """Local shard references cannot be deterministically linked."""


@dataclass(frozen=True, slots=True)
class LinkedTasks:
    """Final tasks plus local-to-final id mapping; no Plan publication semantics."""

    tasks: list[dict[str, Any]]
    task_ids: dict[tuple[str, str], str]


def assign_stable_task_ids(shards: list[dict[str, Any]]) -> LinkedTasks:
    """Assign ``T-###`` in stable Kahn order, never trusting LLM array order.

    Local dependencies use ids from the same work package. Cross-package edges are
    intentionally rejected here; the later contract linker is their sole source.
    """
    nodes: dict[tuple[str, str], dict[str, Any]] = {}
    deps: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for shard in shards:
        wp = shard.get("work_package_id")
        tasks = shard.get("tasks")
        if not isinstance(wp, str) or not isinstance(tasks, list):
            raise PlanDraftError("invalid TaskShard")
        for task in tasks:
            local_id = task.get("local_id") if isinstance(task, dict) else None
            key = (wp, local_id) if isinstance(local_id, str) else None
            if key is None or key in nodes:
                raise PlanDraftError(f"duplicate or invalid local task in {wp}")
            nodes[key] = deepcopy(task)
            raw_deps = task.get("depends_on", [])
            if not isinstance(raw_deps, list) or not all(isinstance(item, str) for item in raw_deps):
                raise PlanDraftError(f"{wp}/{local_id}: invalid local dependencies")
            deps[key] = {(wp, item) for item in raw_deps}
    for key, values in deps.items():
        unknown = values - nodes.keys()
        if unknown:
            raise PlanDraftError(f"{key[0]}/{key[1]}: unknown local dependency {sorted(unknown)}")

    remaining = {key: set(value) for key, value in deps.items()}
    dependents: dict[tuple[str, str], set[tuple[str, str]]] = {
        key: set() for key in nodes
    }
    for key, values in remaining.items():
        for dependency in values:
            dependents[dependency].add(key)
    ready = [key for key, value in remaining.items() if not value]
    heapify(ready)
    ordered: list[tuple[str, str]] = []
    while ready:
        key = heappop(ready)
        if key not in remaining:
            continue
        remaining.pop(key)
        ordered.append(key)
        for dependent in sorted(dependents[key]):
            pending = remaining[dependent]
            pending.remove(key)
            if not pending:
                heappush(ready, dependent)
    if remaining:
        raise PlanDraftError("task shard dependencies contain a cycle")
    ids = {key: f"T-{index:03d}" for index, key in enumerate(ordered, start=1)}
    final: list[dict[str, Any]] = []
    for key in ordered:
        task = nodes[key]
        task["id"] = ids[key]
        task["work_package"] = key[0]
        task["depends_on"] = [ids[(key[0], dep)] for dep in task.pop("depends_on")]
        task.pop("local_id")
        final.append(task)
    return LinkedTasks(tasks=final, task_ids=ids)
