"""Round pending WAL/index 发布与恢复测试（设计 5.4）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nepa.round_store import RoundStore, RoundStoreError
from nepa.test_summary import build_test_summary


class SimulatedCrash(RuntimeError):
    pass


def _summary(
    round_id: int,
    *,
    parent_round_id: int | None,
    workspace_head: str = "a" * 40,
) -> dict[str, Any]:
    return build_test_summary(
        round_id=round_id,
        trigger="s6_task",
        task_id="T-001",
        attempt=round_id,
        workspace_head=workspace_head,
        workspace_tree="b" * 40,
        parent_round_id=parent_round_id,
        plan_sha256="ab" * 32,
        delivery_blueprint_sha256="bc" * 32,
        manifest_sha256="cd" * 32,
        bundle_tree_sha256="de" * 32,
        build_results=[
            {
                "variant_id": "release",
                "result": "pass",
                "duration_ms": 10.0,
                "warnings": 0,
                "errors": 0,
            }
        ],
        cases=[],
    )


def _context(round_id: int) -> dict[str, Any]:
    return {"task_id": "T-001", "attempt": round_id}


def test_publish_round_creates_authoritative_contiguous_index(tmp_path: Path) -> None:
    store = RoundStore(tmp_path)

    first = store.publish_round(
        _summary(1, parent_round_id=None),
        stage="s6",
        producer_context=_context(1),
        junit_bytes=b"<testsuite/>",
    )
    second = store.publish_round(
        _summary(2, parent_round_id=1, workspace_head="c" * 40),
        stage="s6",
        producer_context=_context(2),
    )

    index = store.load_index()
    assert index["rounds"] == [first, second]
    assert first["round_id"] == 1 and second["round_id"] == 2
    assert first["junit_ref"]["path"] == "test_results/round_001/junit.xml"
    assert (tmp_path / first["summary_ref"]["path"]).is_file()
    assert not store.wal_path.exists()


@pytest.mark.parametrize("crash_at", ["after_wal", "after_rename", "after_index"])
def test_reconcile_completes_each_persisted_wal_crash_window(
    tmp_path: Path,
    crash_at: str,
) -> None:
    store = RoundStore(tmp_path)

    def crash(label: str) -> None:
        if label == crash_at:
            raise SimulatedCrash(label)

    with pytest.raises(SimulatedCrash):
        store.publish_round(
            _summary(1, parent_round_id=None),
            stage="s6",
            producer_context=_context(1),
            fault_hook=crash,
        )

    resumed = RoundStore(tmp_path)
    assert resumed.reconcile_pending()
    assert [item["round_id"] for item in resumed.load_index()["rounds"]] == [1]
    assert (tmp_path / "test_results" / "round_001" / "summary.json").is_file()
    assert not resumed.wal_path.exists()


def test_crash_before_wal_quarantines_temp_and_reuses_round_id(tmp_path: Path) -> None:
    store = RoundStore(tmp_path)

    def crash(label: str) -> None:
        if label == "after_temp":
            raise SimulatedCrash(label)

    with pytest.raises(SimulatedCrash):
        store.publish_round(
            _summary(1, parent_round_id=None),
            stage="s6",
            producer_context=_context(1),
            fault_hook=crash,
        )
    assert not store.wal_path.exists()

    assert store.next_round_id() == 1
    orphaned = list((tmp_path / "test_results" / "orphaned").iterdir())
    assert len(orphaned) == 1
    entry = store.publish_round(
        _summary(1, parent_round_id=None),
        stage="s6",
        producer_context=_context(1),
    )
    assert entry["round_id"] == 1


def test_unregistered_final_round_is_quarantined_not_accepted(tmp_path: Path) -> None:
    store = RoundStore(tmp_path)
    orphan = tmp_path / "test_results" / "round_009"
    orphan.mkdir(parents=True)
    (orphan / "summary.json").write_text("{}", encoding="utf-8")

    assert store.next_round_id() == 1

    assert not orphan.exists()
    assert store.load_index()["rounds"] == []
    assert any((tmp_path / "test_results" / "orphaned").iterdir())


def test_corrupt_persisted_wal_artifact_is_quarantined_and_id_is_reusable(
    tmp_path: Path,
) -> None:
    store = RoundStore(tmp_path)

    def crash(label: str) -> None:
        if label == "after_rename":
            raise SimulatedCrash(label)

    with pytest.raises(SimulatedCrash):
        store.publish_round(
            _summary(1, parent_round_id=None),
            stage="s6",
            producer_context=_context(1),
            fault_hook=crash,
        )
    summary_path = tmp_path / "test_results" / "round_001" / "summary.json"
    summary_path.write_text("{}", encoding="utf-8")

    resumed = RoundStore(tmp_path)
    assert resumed.reconcile_pending() is False
    assert resumed.load_index()["rounds"] == []
    assert not resumed.wal_path.exists()
    quarantined = list((tmp_path / "test_results" / "orphaned").iterdir())
    assert any(path.name.startswith("pending_round.json.") for path in quarantined)
    assert any(path.name.startswith("round_001.") for path in quarantined)

    entry = resumed.publish_round(
        _summary(1, parent_round_id=None),
        stage="s6",
        producer_context=_context(1),
    )
    assert entry["round_id"] == 1


def test_publish_rejects_wrong_next_id_and_parent(tmp_path: Path) -> None:
    store = RoundStore(tmp_path)
    with pytest.raises(RoundStoreError, match="下一编号"):
        store.publish_round(
            _summary(2, parent_round_id=1),
            stage="s6",
            producer_context=_context(2),
        )

    store.publish_round(
        _summary(1, parent_round_id=None),
        stage="s6",
        producer_context=_context(1),
    )
    with pytest.raises(RoundStoreError, match="parent_round_id"):
        store.publish_round(
            _summary(2, parent_round_id=None),
            stage="s6",
            producer_context=_context(2),
        )
