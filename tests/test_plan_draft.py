from __future__ import annotations

import pytest

from nepa.plan_draft import PlanDraftError, assign_stable_task_ids


def _task(local_id: str, depends_on: list[str] | None = None) -> dict:
    return {"local_id": local_id, "depends_on": depends_on or []}


def test_task_ids_use_stable_work_package_then_local_id_order() -> None:
    result = assign_stable_task_ids(
        [{"work_package_id": "z", "tasks": [_task("b"), _task("a")]}, {"work_package_id": "a", "tasks": [_task("c")] }]
    )
    assert [task["id"] for task in result.tasks] == ["T-001", "T-002", "T-003"]
    assert [task["work_package"] for task in result.tasks] == ["a", "z", "z"]


def test_task_ids_rewrite_local_dependencies() -> None:
    result = assign_stable_task_ids([{"work_package_id": "a", "tasks": [_task("b", ["a"]), _task("a")]}])
    assert result.tasks[1]["depends_on"] == ["T-001"]


def test_task_ids_use_single_min_ready_queue_not_wave_batches() -> None:
    result = assign_stable_task_ids(
        [
            {
                "work_package_id": "a",
                "tasks": [_task("root"), _task("child", ["root"])],
            },
            {"work_package_id": "z", "tasks": [_task("free")]},
        ]
    )

    assert [
        (task["work_package"], task["id"])
        for task in result.tasks
    ] == [
        ("a", "T-001"),
        ("a", "T-002"),
        ("z", "T-003"),
    ]


def test_task_ids_reject_cycle() -> None:
    with pytest.raises(PlanDraftError, match="cycle"):
        assign_stable_task_ids([{"work_package_id": "a", "tasks": [_task("a", ["b"]), _task("b", ["a"])]}])
