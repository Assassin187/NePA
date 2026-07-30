"""M1 run creation, controller locking, and S4→S6 routing tests."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import nepa.m1_runtime as runtime
from nepa.config import load_config
from nepa.m1_runtime import M1RuntimeError, controller_lock, create_spec_run, drive_m1
from nepa.run_store import RunStore

ROOT = Path(__file__).resolve().parent.parent


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "profiles", repo / "profiles")
    shutil.copytree(ROOT / "golds", repo / "golds")
    return repo


def _store(tmp_path: Path) -> tuple[RunStore, Path]:
    repo = _repo(tmp_path)
    config = load_config(
        ROOT / "configs" / "default.yaml",
        {"run": {"until": "s6"}},
    )
    store = create_spec_run(
        spec_path="golds/mqtt-3.1.1-min/spec/spec.json",
        runs_root="runs",
        repo_root=repo,
        config=config,
    )
    return store, repo


def _services(closed: list[bool]) -> Any:
    return SimpleNamespace(
        runner=object(),
        build_tool=object(),
        gate_runner=lambda _workspace, _tests: [],
        close=lambda: closed.append(True),
    )


def test_create_spec_run_keeps_source_ref_and_separate_frozen_ref(
    tmp_path: Path,
) -> None:
    store, repo = _store(tmp_path)

    assert store.meta.inputs.spec.path == "golds/mqtt-3.1.1-min/spec/spec.json"
    assert (store.run_dir / "spec" / "spec.json").is_file()
    assert store.meta.inputs.spec.sha256 != ""
    assert store.run_dir.is_relative_to(repo)


def test_drive_m1_routes_all_stages_and_commits_planned_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, repo = _store(tmp_path)
    called: list[str] = []
    closed: list[bool] = []

    def s4(store: RunStore, *_args: Any, **_kwargs: Any) -> None:
        called.append("s4")
        store.begin_stage("s4")
        store.set_stage_status(
            "s4",
            "done",
            output_refs={"plan": {"path": "plan/plan.json", "sha256": "1" * 64}},
        )

    def s5(store: RunStore, *_args: Any, **_kwargs: Any) -> None:
        called.append("s5")
        store.begin_stage("s5")
        store.set_stage_status("s5", "done", output_refs={"workspace_head": "2" * 40})

    def s6(store: RunStore, *_args: Any, **_kwargs: Any) -> None:
        called.append("s6")
        store.begin_stage("s6")
        store.set_stage_status("s6", "done", output_refs={"workspace_head": "3" * 40})

    monkeypatch.setattr(runtime, "compile_plan", s4)
    monkeypatch.setattr(runtime, "scaffold_project", s5)
    monkeypatch.setattr(runtime, "execute_plan", s6)

    result = drive_m1(
        store,
        repo_root=repo,
        service_factory=lambda *_args: _services(closed),
    )

    assert called == ["s4", "s5", "s6"]
    assert closed == [True]
    assert result.action == "planned_stop"
    assert result.exit_code == 0
    assert store.meta.termination_kind == "planned_stop"
    assert store.meta.stages["s7"].status == "pending"


def test_drive_m1_terminal_resume_does_not_construct_external_services(
    tmp_path: Path,
) -> None:
    store, repo = _store(tmp_path)
    store.set_stage_status("s4", "skipped")
    store.set_stage_status("s5", "skipped")
    store.set_stage_status("s6", "running")
    store.set_stage_status("s6", "done", output_refs={"workspace_head": "3" * 40})
    store.finalize("planned_stop", 0)

    result = drive_m1(
        RunStore.load(store.run_dir),
        repo_root=repo,
        service_factory=lambda *_args: pytest.fail("services must not be built"),
    )

    assert result.action == "already_terminal"
    assert result.exit_code == 0


def test_controller_lock_rejects_a_second_active_controller(tmp_path: Path) -> None:
    store, _repo_root = _store(tmp_path)

    with controller_lock(store), pytest.raises(
        M1RuntimeError,
        match="已有活跃 controller",
    ), controller_lock(RunStore.load(store.run_dir)):
        pass
