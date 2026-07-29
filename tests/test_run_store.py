"""nepa/run_store.py 单元测试（system_design.md 4.4、4.8、5.6.2）。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic_core import PydanticSerializationError

import nepa.run_store as run_store_mod
from nepa.run_store import (
    STAGE_NAMES,
    InvalidTransitionError,
    RunStore,
)
from nepa.run_store import create_run as create_run_impl


def _read_run_json(store: RunStore) -> dict[str, Any]:
    return json.loads(store.run_json_path.read_text(encoding="utf-8"))


def _asset_ref(name: str) -> dict[str, str]:
    paths = {
        "target_profile": "inputs/target.json",
        "language_profile": "inputs/language.json",
        "test_bundle": "inputs/test_bundle.json",
    }
    return {
        "id": name.replace("_", "-"),
        "version": "1.0",
        "path": paths[name],
        "sha256": "ab" * 32,
    }


def _inputs(entry: str) -> dict[str, Any]:
    value: dict[str, Any] = {
        "target_profile": _asset_ref("target_profile"),
        "language_profile": _asset_ref("language_profile"),
        "test_bundle": _asset_ref("test_bundle"),
    }
    if entry == "spec-run":
        value["spec"] = {"path": "spec.json", "sha256": "cd" * 32}
    else:
        value["doc"] = {"path": "source.pdf", "sha256": "de" * 32}
        value["scope"] = {"path": "scope.yaml", "sha256": "ef" * 32}
    return value


def create_run(
    runs_root: str | Path,
    protocol: str,
    entry: str,
    **kwargs: Any,
) -> RunStore:
    kwargs.setdefault("inputs", _inputs(entry))
    return create_run_impl(runs_root, protocol, entry, **kwargs)


class TestCreateRun:
    def test_directory_tree_spec_run(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        d = store.run_dir
        # 4.4 目录树
        for sub in (
            "spec",
            "plan",
            "plan/_s4",
            "inputs",
            "workspace",
            "test_results",
            "test_results/task_evidence",
            "repair",
            "repair/evidence",
            "report",
            "trace/prompts",
            "trace/outputs",
            "cache",
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
        assert re.fullmatch(r"\d{8}T\d{4}Z-2_mqtt-min_spec-run", b.run_id)

    def test_initial_run_json_fields(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        data = _read_run_json(store)
        # 5.6.2 必填字段
        assert data["schema_version"] == "2.0"
        assert data["entry"] == "spec-run"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", data["created_at"])
        assert data["inputs"] == _inputs("spec-run")
        assert data["config_snapshot"] == {}
        assert data["config_snapshot_sha256"] == (
            "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
        )
        assert set(data["stages"]) == set(STAGE_NAMES)
        assert [data["stages"][name]["status"] for name in ("s1", "s2", "s3")] == [
            "skipped",
            "skipped",
            "skipped",
        ]
        assert all(data["stages"][name]["status"] == "pending" for name in STAGE_NAMES[3:])
        assert data["budget_used"] == {
            "wall_clock_s": 0.0,
            "cost_usd": 0.0,
            "tokens_in": 0,
            "tokens_out": 0,
        }
        # 终态字段未终结前不出现（5.6.2）
        assert "termination_kind" not in data
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

    def test_entry_binds_input_shape_and_doc_scope_is_required(self, tmp_path: Path) -> None:
        doc_inputs = _inputs("doc-run")
        doc_inputs.pop("scope")
        with pytest.raises(ValueError, match="scope"):
            create_run_impl(tmp_path, "sample", "doc-run", inputs=doc_inputs)

        spec_inputs = _inputs("spec-run")
        spec_inputs["doc"] = {"path": "source.pdf", "sha256": "ab" * 32}
        with pytest.raises(ValueError, match="doc"):
            create_run_impl(tmp_path, "sample", "spec-run", inputs=spec_inputs)

    def test_frozen_asset_paths_are_fixed(self, tmp_path: Path) -> None:
        inputs = _inputs("spec-run")
        inputs["target_profile"]["path"] = "profiles/target.json"
        with pytest.raises(ValueError, match="inputs.target_profile.path"):
            create_run_impl(tmp_path, "sample", "spec-run", inputs=inputs)


class TestAtomicWrite:
    def test_save_uses_os_replace(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        store.set_stage_status("s4", "running")
        leftovers = list(store.run_dir.glob("*.tmp"))
        assert leftovers == []

    def test_failed_dump_leaves_old_file_intact(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.set_flag("ok", 1)
        before = store.run_json_path.read_text(encoding="utf-8")
        store.meta.flags["bad"] = object()  # 不可 JSON 序列化 → save 失败
        with pytest.raises(PydanticSerializationError):
            store.save()
        assert store.run_json_path.read_text(encoding="utf-8") == before
        assert list(store.run_dir.glob("*.tmp")) == []


class TestStageStateMachine:
    def test_happy_path(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.set_stage_status("s4", "running")
        output_refs = {
            "plan": {
                "path": "plan/plan.json",
                "sha256": "ab" * 32,
            }
        }
        store.set_stage_status("s4", "done", output_refs=output_refs)
        data = _read_run_json(store)
        st = data["stages"]["s4"]
        assert st["status"] == "done"
        assert st["started_at"] is not None and st["ended_at"] is not None
        assert st["output_refs"] == output_refs

    def test_pending_to_skipped(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "doc-run")
        store.set_stage_status("s1", "skipped")
        assert store.meta.stages["s1"].status == "skipped"

    def test_failed_then_retry(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.set_stage_status("s6", "running")
        store.set_stage_status("s6", "failed", error="boom")
        assert store.meta.stages["s6"].error == "boom"
        store.set_stage_status("s6", "running")  # 4.8 resume 重试
        assert store.meta.stages["s6"].error is None

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            (None, "done"),  # pending → done
            (None, "failed"),  # pending → failed
            ("running", "running"),  # running → running
            ("running", "skipped"),  # running → skipped
        ],
    )
    def test_illegal_transitions_rejected(
        self, tmp_path: Path, first: str | None, second: str
    ) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        if first is not None:
            store.set_stage_status("s5", first)  # type: ignore[arg-type]
        with pytest.raises(InvalidTransitionError):
            store.set_stage_status("s5", second)  # type: ignore[arg-type]

    def test_done_is_terminal(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.set_stage_status("s4", "running")
        store.set_stage_status("s4", "done")
        for target in ("running", "failed", "pending"):
            with pytest.raises((InvalidTransitionError, ValueError)):
                store.set_stage_status("s4", target)  # type: ignore[arg-type]

    def test_unknown_stage_and_status(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        with pytest.raises(KeyError):
            store.set_stage_status("s0_bogus", "running")
        with pytest.raises(ValueError):
            store.set_stage_status("s4", "paused")  # type: ignore[arg-type]

    def test_error_only_with_failed(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        with pytest.raises(ValueError, match="error"):
            store.set_stage_status("s4", "running", error="x")

    def test_output_refs_only_with_done(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        with pytest.raises(ValueError, match="output_refs"):
            store.set_stage_status("s4", "running", output_refs={"x": "y"})
        store.set_stage_status("s4", "running")
        with pytest.raises(ValueError, match="output_refs"):
            store.set_stage_status("s4", "done", output_refs={})

    def test_first_incomplete_stage_respects_entry_and_terminal_states(
        self, tmp_path: Path
    ) -> None:
        spec_store = create_run(tmp_path, "sample", "spec-run")
        assert spec_store.first_incomplete_stage() == "s4"
        spec_store.set_stage_status("s4", "running")
        spec_store.set_stage_status("s4", "done")
        assert spec_store.first_incomplete_stage() == "s5"

        doc_store = create_run(tmp_path, "sample", "doc-run")
        assert doc_store.first_incomplete_stage() == "s1"

    def test_begin_stage_is_idempotent_for_resume_and_completed_stage(
        self, tmp_path: Path
    ) -> None:
        store = create_run(tmp_path, "sample", "spec-run")
        assert store.begin_stage("s4") is True
        started_at = store.meta.stages["s4"].started_at
        assert store.begin_stage("s4") is True
        assert store.meta.stages["s4"].started_at == started_at

        store.set_stage_status("s4", "done")
        assert store.begin_stage("s4") is False


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
        ("kind", "outcome", "code"),
        [
            ("completed", "success", 0),
            ("controlled_exit", "degraded", 10),
            ("controlled_exit", "failed", 20),
        ],
    )
    def test_finalize_writes_terminal_fields(
        self,
        tmp_path: Path,
        kind: str,
        outcome: str,
        code: int,
    ) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        if kind == "controlled_exit":
            store.request_controlled_exit("s4", "CONTROLLED_TEST_EXIT", "test exit")
        store.finalize(kind, code, outcome=outcome)  # type: ignore[arg-type]
        data = _read_run_json(store)
        assert data["termination_kind"] == kind
        assert data["outcome"] == outcome  # 9.1.2
        assert data["exit_code"] == code  # 8.7

    @pytest.mark.parametrize(("kind", "code"), [("planned_stop", 0), ("internal_error", 1)])
    def test_non_outcome_terminal_kinds(self, tmp_path: Path, kind: str, code: int) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.finalize(kind, code)  # type: ignore[arg-type]

        data = _read_run_json(store)
        assert data["termination_kind"] == kind
        assert data["exit_code"] == code
        assert "outcome" not in data

    def test_internal_error_may_retain_controlled_exit_request(
        self, tmp_path: Path
    ) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.request_controlled_exit("s4", "PLAN_NOT_SEALED", "No valid Plan.")
        store.finalize("internal_error", 1)

        reloaded = RunStore.load(store.run_dir)
        assert reloaded.meta.termination_kind == "internal_error"
        assert reloaded.meta.termination_request is not None
        assert reloaded.meta.termination_request.reason.code == "PLAN_NOT_SEALED"

    def test_controlled_exit_requires_request_and_rejects_success(
        self, tmp_path: Path
    ) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        with pytest.raises(ValueError, match="termination_request"):
            store.finalize("controlled_exit", 20, outcome="failed")

        store.request_controlled_exit("s4", "PLAN_NOT_SEALED", "No valid Plan.")
        with pytest.raises(ValueError, match="degraded/failed"):
            store.finalize("controlled_exit", 0, outcome="success")

    def test_completed_and_planned_stop_forbid_request(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.request_controlled_exit("s4", "PLAN_NOT_SEALED", "No valid Plan.")
        with pytest.raises(ValueError, match="termination_request"):
            store.finalize("completed", 20, outcome="failed")
        with pytest.raises(ValueError, match="termination_request"):
            store.finalize("planned_stop", 0)

    def test_finalize_rejects_mismatched_exit_code(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        with pytest.raises(ValueError, match="退出码"):
            store.finalize("completed", 10, outcome="success")
        with pytest.raises(ValueError):
            store.finalize("oops", 0)  # type: ignore[arg-type]

    def test_finalize_is_one_way(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.finalize("planned_stop", 0)
        with pytest.raises(InvalidTransitionError):
            store.finalize("planned_stop", 0)

    def test_config_snapshot_hash_recomputed_and_verified_on_load(self, tmp_path: Path) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.set_config_snapshot({"budgets": {"max_cost_usd": 20.0}})
        persisted = _read_run_json(store)
        assert persisted["config_snapshot_sha256"] == (
            "74a8dca717f67d45810242639e31e6bf9310d66f03337f99b15ef3dc06e6a808"
        )

        persisted["config_snapshot"]["budgets"]["max_cost_usd"] = 99
        store.run_json_path.write_text(json.dumps(persisted), encoding="utf-8")
        with pytest.raises(ValueError, match="config_snapshot_sha256"):
            RunStore.load(store.run_dir)


class TestLoadRoundTrip:
    def test_load_restores_full_state(self, tmp_path: Path) -> None:
        store = create_run(
            tmp_path,
            "mqtt-min",
            "doc-run",
            inputs=_inputs("doc-run"),
            config_snapshot={"budgets": {"max_cost_usd": 5}},
        )
        store.set_stage_status("s1", "running")
        store.set_stage_status("s1", "done")
        store.add_budget_used(tokens_in=7)

        reloaded = RunStore.load(store.run_dir)
        assert reloaded.run_id == store.run_id
        assert reloaded.meta.entry == "doc-run"
        assert reloaded.meta.inputs.model_dump()["doc"]["path"] == "source.pdf"
        assert reloaded.meta.stages["s1"].status == "done"
        assert reloaded.meta.budget_used.tokens_in == 7
        # 恢复后状态机继续生效（4.8）
        with pytest.raises(InvalidTransitionError):
            reloaded.set_stage_status("s1", "running")


class TestTerminationRequestAndRecovery:
    def test_running_stage_and_request_are_written_as_one_failed_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.set_stage_status("s4", "running")
        saves = 0
        original = store.save

        def spy() -> None:
            nonlocal saves
            saves += 1
            original()

        monkeypatch.setattr(store, "save", spy)
        request = store.request_controlled_exit(
            "s4",
            "PLAN_NOT_SEALED",
            "S4 exhausted its repair budget.",
        )

        assert saves == 1
        assert request.stage == "s4"
        assert store.meta.stages["s4"].status == "failed"
        assert store.meta.stages["s4"].error == "S4 exhausted its repair budget."
        RunStore.load(store.run_dir)

    def test_pending_boundary_request_is_legal_but_s9_is_not(
        self, tmp_path: Path
    ) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        request = store.request_controlled_exit(
            "s5",
            "GLOBAL_BUDGET_EXHAUSTED",
            "Budget exhausted before S5.",
        )
        assert request.stage == "s5"
        assert store.meta.stages["s5"].status == "pending"
        with pytest.raises((KeyError, ValueError)):
            store.request_controlled_exit(  # type: ignore[arg-type]
                "s9",
                "INVALID",
                "S9 is not budget gated.",
            )

    def test_request_stage_status_lint_rejects_done_running_and_skipped(
        self, tmp_path: Path
    ) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.request_controlled_exit("s4", "PLAN_NOT_SEALED", "No valid Plan.")
        raw = _read_run_json(store)
        for status in ("done", "running", "skipped"):
            damaged = json.loads(json.dumps(raw))
            damaged["stages"]["s4"] = {"status": status}
            store.run_json_path.write_text(json.dumps(damaged), encoding="utf-8")
            with pytest.raises(ValueError, match="failed 或 pending"):
                RunStore.load(store.run_dir)

    def test_orphaned_running_stages_are_failed_atomically_without_request(
        self, tmp_path: Path
    ) -> None:
        store = create_run(tmp_path, "mqtt-min", "spec-run")
        store.set_stage_status("s4", "running")
        store.set_stage_status("s9", "running")

        recovered = store.recover_orphaned_running_stages()

        assert recovered == ("s4", "s9")
        assert store.meta.termination_request is None
        for stage in recovered:
            state = store.meta.stages[stage]
            assert state.status == "failed"
            assert state.error == "process crashed mid-stage"
        RunStore.load(store.run_dir)


def test_persisted_run_store_output_validates_against_schema(tmp_path: Path) -> None:
    """真实 run_store 输出必须满足 M0-1 的 run.schema。"""
    store = create_run(
        tmp_path,
        "mqtt-min",
        "spec-run",
        inputs=_inputs("spec-run"),
        config_snapshot={"budgets": {"max_cost_usd": 20}},
    )
    schema_path = Path(__file__).resolve().parent.parent / "nepa" / "schemas" / "run.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(_read_run_json(store)))
    assert errors == []
