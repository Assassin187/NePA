"""nepa/run_store.py 单元测试（system_design.md 4.4、4.8、5.6.2）。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import nepa.run_store as run_store_mod
from nepa.run_store import (
    STAGE_NAMES,
    InvalidTransitionError,
    RunStore,
    create_run,
)


def _read_run_json(store: RunStore) -> dict[str, object]:
    return json.loads(store.run_json_path.read_text(encoding="utf-8"))


class TestCreateRun:
    def test_directory_tree_spec_run(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        d = store.run_dir
        # 4.4 目录树
        for sub in (
            "spec", "plan", "workspace", "test_results", "repair",
            "report", "trace/prompts", "trace/outputs", "cache",
        ):
            assert (d / sub).is_dir(), sub
        assert not (d / "doc").exists()  # doc/ 仅 doc-run（4.4）
        assert (d / "run.json").is_file()

    def test_doc_run_has_doc_dir(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "doc-run")
        assert (store.run_dir / "doc").is_dir()

    def test_run_id_naming(self, tmp_path: Path) -> None:
        # 4.4：<UTC时间戳>_<协议>_<入口>，如 20260726T1432Z_mqtt-min_spec-run
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        assert re.fullmatch(r"\d{8}T\d{4}Z_mqtt-min_spec-run", store.run_id)
        assert store.run_dir.name == store.run_id  # 5.6.2：run_id 与目录名一致
        assert _read_run_json(store)["run_id"] == store.run_id

    def test_same_minute_collision_gets_distinct_dirs(self, tmp_path: Path) -> None:
        a = create_run(tmp_path, "mqtt-min", "spec-run")
        b = create_run(tmp_path, "mqtt-min", "spec-run")
        assert a.run_dir != b.run_dir
        assert b.run_dir.is_dir()
        assert b.run_id == b.run_dir.name

    def test_initial_run_json_fields(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        data = _read_run_json(store)
        # 5.6.2 必填字段
        assert data["schema_version"] == "1.0"
        assert data["entry"] == "spec-run"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", data["created_at"])
        assert data["inputs"] == {}
        assert data["config_snapshot"] == {}
        assert set(data["stages"]) == set(STAGE_NAMES)
        assert all(s["status"] == "pending" for s in data["stages"].values())
        assert data["budget_used"] == {
            "wall_clock_s": 0.0, "cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0,
        }
        # 终态字段未终结前不出现（5.6.2）
        assert "outcome" not in data
        assert "exit_code" not in data

    def test_invalid_entry_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="entry"):
            create_run(tmp_path, "mqtt-min", "full-run")

    def test_invalid_protocol_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            create_run(tmp_path, "a/b", "spec-run")
        with pytest.raises(ValueError):
            create_run(tmp_path, "a_b", "spec-run")


class TestAtomicWrite:
    def test_save_uses_os_replace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 4.8：写临时文件 + 原子改名
        calls: list[tuple[str, str]] = []
        real_replace = run_store_mod.os.replace

        def spy(src: str, dst: str) -> None:
            calls.append((str(src), str(dst)))
            real_replace(src, dst)

        monkeypatch.setattr(run_store_mod.os, "replace", spy)
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.set_flag("degraded_segmentation", True)
        assert calls, "落盘必须经 os.replace"
        assert all(dst.endswith("run.json") for _, dst in calls)
        # 临时文件与目标同目录（跨文件系统 rename 不原子）
        assert all(Path(src).parent == store.run_dir for src, _ in calls)

    def test_no_temp_files_left_behind(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.add_budget_used(cost_usd=0.5)
        store.set_stage_status("s4_plan", "running")
        leftovers = list(store.run_dir.glob("*.tmp"))
        assert leftovers == []

    def test_failed_dump_leaves_old_file_intact(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.set_flag("ok", 1)
        before = store.run_json_path.read_text(encoding="utf-8")
        store.meta.flags["bad"] = object()  # 不可 JSON 序列化 → save 失败
        with pytest.raises(Exception):
            store.save()
        assert store.run_json_path.read_text(encoding="utf-8") == before
        assert list(store.run_dir.glob("*.tmp")) == []


class TestStageStateMachine:
    def test_happy_path(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.set_stage_status("s4_plan", "running")
        store.set_stage_status("s4_plan", "done")
        data = _read_run_json(store)
        st = data["stages"]["s4_plan"]
        assert st["status"] == "done"
        assert st["started_at"] is not None and st["ended_at"] is not None

    def test_pending_to_skipped(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.set_stage_status("s1_ingest", "skipped")  # spec-run 跳过 S1（4.1）
        assert store.meta.stages["s1_ingest"].status == "skipped"

    def test_failed_then_retry(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.set_stage_status("s6_code", "running")
        store.set_stage_status("s6_code", "failed", error="boom")
        assert store.meta.stages["s6_code"].error == "boom"
        store.set_stage_status("s6_code", "running")  # 4.8 resume 重试
        assert store.meta.stages["s6_code"].error is None

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            (None, "done"),        # pending → done
            (None, "failed"),      # pending → failed
            ("running", "running"),  # running → running
            ("running", "skipped"),  # running → skipped
        ],
    )
    def test_illegal_transitions_rejected(
        self, tmp_path: Path, first: str | None, second: str
    ) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        if first is not None:
            store.set_stage_status("s5_scaffold", first)  # type: ignore[arg-type]
        with pytest.raises(InvalidTransitionError):
            store.set_stage_status("s5_scaffold", second)  # type: ignore[arg-type]

    def test_done_is_terminal(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.set_stage_status("s4_plan", "running")
        store.set_stage_status("s4_plan", "done")
        for target in ("running", "failed", "pending"):
            with pytest.raises((InvalidTransitionError, ValueError)):
                store.set_stage_status("s4_plan", target)  # type: ignore[arg-type]

    def test_unknown_stage_and_status(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        with pytest.raises(KeyError):
            store.set_stage_status("s0_bogus", "running")
        with pytest.raises(ValueError):
            store.set_stage_status("s4_plan", "paused")  # type: ignore[arg-type]

    def test_error_only_with_failed(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        with pytest.raises(ValueError, match="error"):
            store.set_stage_status("s4_plan", "running", error="x")


class TestBudgetAndFinalize:
    def test_budget_accumulates_and_persists(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.add_budget_used(wall_clock_s=1.5, cost_usd=0.1, tokens_in=10, tokens_out=5)
        store.add_budget_used(wall_clock_s=0.5, cost_usd=0.2, tokens_in=90, tokens_out=45)
        reloaded = RunStore.load(store.run_dir)
        used = reloaded.meta.budget_used
        assert used.wall_clock_s == pytest.approx(2.0)
        assert used.cost_usd == pytest.approx(0.3)
        assert (used.tokens_in, used.tokens_out) == (100, 50)

    def test_negative_delta_rejected(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        with pytest.raises(ValueError):
            store.add_budget_used(cost_usd=-1)

    def test_flags_persist(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.set_flag("degraded_segmentation", True)
        assert _read_run_json(store)["flags"] == {"degraded_segmentation": True}

    @pytest.mark.parametrize(
        ("outcome", "code"), [("success", 0), ("degraded", 10), ("failed", 20)]
    )
    def test_finalize_writes_terminal_fields(
        self, tmp_path: Path, outcome: str, code: int
    ) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.finalize(outcome, code)  # type: ignore[arg-type]
        data = _read_run_json(store)
        assert data["outcome"] == outcome  # 9.1.2
        assert data["exit_code"] == code  # 8.7

    def test_finalize_rejects_mismatched_exit_code(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        with pytest.raises(ValueError, match="退出码"):
            store.finalize("success", 10)
        with pytest.raises(ValueError):
            store.finalize("oops", 0)  # type: ignore[arg-type]


class TestLoadRoundTrip:
    def test_load_restores_full_state(self, tmp_path: Path) -> None:
        store = create_run(
            tmp_path, "mqtt-min", "doc-run",
            inputs={"doc_path": "a.pdf", "sha256": "ab" * 32},
            config_snapshot={"budgets": {"max_cost_usd": 5}},
        )
        store.set_stage_status("s1_ingest", "running")
        store.set_stage_status("s1_ingest", "done")
        store.add_budget_used(tokens_in=7)

        reloaded = RunStore.load(store.run_dir)
        assert reloaded.run_id == store.run_id
        assert reloaded.meta.entry == "doc-run"
        assert reloaded.meta.inputs["doc_path"] == "a.pdf"
        assert reloaded.meta.stages["s1_ingest"].status == "done"
        assert reloaded.meta.budget_used.tokens_in == 7
        # 恢复后状态机继续生效（4.8）
        with pytest.raises(InvalidTransitionError):
            reloaded.set_stage_status("s1_ingest", "running")
