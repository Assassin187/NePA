"""M0-5 ``nepa lint`` CLI tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nepa.canonical import atomic_write_canonical_json
from nepa.cli import app
from nepa.m1_runtime import M1RuntimeResult
from nepa.run_store import create_run
from tests.plan_v3 import example, make_config_snapshot, make_manifest
from tests.plan_v3 import make_plan as make_mini_plan
from tests.plan_v3 import make_spec as make_plan_spec
from tests.test_spec_lint import make_mini_spec

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "nepa" / "schemas" / "examples"
runner = CliRunner()


def test_run_command_freezes_s6_and_returns_runtime_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(make_mini_spec()), encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text("{}\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run_new_m1(**kwargs: object) -> M1RuntimeResult:
        captured.update(kwargs)
        return M1RuntimeResult("run-1", tmp_path / "runs" / "run-1", "planned_stop", 0)

    monkeypatch.setattr("nepa.cli.run_new_m1", fake_run_new_m1)
    result = runner.invoke(
        app,
        [
            "run",
            "--spec",
            str(spec),
            "--config",
            str(config),
            "--repo-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["spec_path"] == spec
    assert captured["config"].run.until == "s6"  # type: ignore[union-attr]
    assert "state: planned_stop; exit_code: 0" in result.output


def test_run_command_rejects_an_unimplemented_stop_stage(tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(make_mini_spec()), encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text("{}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "run",
            "--spec",
            str(spec),
            "--until",
            "s7",
            "--config",
            str(config),
            "--repo-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 2
    assert "只支持 --until s6" in result.output


def _run_inputs() -> dict[str, object]:
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


def test_lint_spec_valid_example(tmp_path: Path) -> None:
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(make_mini_spec()), encoding="utf-8")
    result = runner.invoke(app, ["lint", "spec", str(path)])
    assert result.exit_code == 0, result.output
    assert "0 error(s)" in result.output


def test_lint_spec_invalid_returns_one(tmp_path: Path) -> None:
    bad = json.loads((EXAMPLES / "specs-requirements.json").read_text(encoding="utf-8"))
    del bad["protocol"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    result = runner.invoke(app, ["lint", "spec", str(path)])
    assert result.exit_code == 1
    assert "SPEC-SCHEMA" in result.output


def test_lint_plan_valid_example(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(make_plan_spec()), encoding="utf-8")
    plan = make_mini_plan()
    plan["input_refs"]["spec"]["sha256"] = hashlib.sha256(spec_path.read_bytes()).hexdigest()
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "lint",
            "plan",
            str(plan_path),
            "--spec",
            str(spec_path),
        ],
    )
    assert result.exit_code == 0, result.output


def _full_lint_run(tmp_path: Path) -> tuple[Path, Path, Path]:
    """构造 --run-dir full lint 需要的 run 目录、Plan 与 Spec 文件。"""
    repo_root = tmp_path / "repo"
    manifest_path = repo_root / "golds" / "sample-wire" / "tests_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(make_manifest()), encoding="utf-8")

    run_dir = tmp_path / "run"
    (run_dir / "inputs").mkdir(parents=True)
    (run_dir / "plan").mkdir()
    for name, value in (
        ("target.json", example("target-profile.json")),
        ("language.json", example("language-profile.json")),
        ("test_bundle.json", example("test-bundle.json")),
    ):
        atomic_write_canonical_json(run_dir / "inputs" / name, value)
    atomic_write_canonical_json(
        run_dir / "run.json",
        {"schema_version": "1.0", "config_snapshot": make_config_snapshot()},
    )

    spec_path = run_dir / "spec" / "spec.json"
    spec_path.parent.mkdir()
    spec_path.write_text(json.dumps(make_plan_spec()), encoding="utf-8")

    plan = make_mini_plan()
    plan["input_refs"]["spec"]["sha256"] = hashlib.sha256(spec_path.read_bytes()).hexdigest()
    for kind, name in (
        ("target_profile", "target.json"),
        ("language_profile", "language.json"),
        ("test_bundle", "test_bundle.json"),
    ):
        plan["input_refs"][kind] = {
            "path": f"inputs/{name}",
            "sha256": hashlib.sha256((run_dir / "inputs" / name).read_bytes()).hexdigest(),
        }
    plan_path = run_dir / "plan" / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return run_dir, plan_path, spec_path


def test_lint_plan_full_gate_uses_the_run_frozen_inputs(tmp_path: Path) -> None:
    """--run-dir 重建 Blueprint 与四项冻结输入，跑通 5.2.5 的 stage full lint。"""
    run_dir, plan_path, spec_path = _full_lint_run(tmp_path)
    result = runner.invoke(
        app,
        [
            "lint",
            "plan",
            str(plan_path),
            "--spec",
            str(spec_path),
            "--run-dir",
            str(run_dir),
            "--repo-root",
            str(tmp_path / "repo"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "0 error(s)" in result.output


def test_lint_plan_full_gate_rejects_blueprint_seal_drift(tmp_path: Path) -> None:
    """Plan 顶层封口哈希与重编译 Blueprint 不一致 → full lint 报错退出。"""
    run_dir, plan_path, spec_path = _full_lint_run(tmp_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["delivery_blueprint_sha256"] = "0" * 64
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "lint",
            "plan",
            str(plan_path),
            "--spec",
            str(spec_path),
            "--run-dir",
            str(run_dir),
            "--repo-root",
            str(tmp_path / "repo"),
        ],
    )
    assert result.exit_code == 1
    assert "PLAN-BLUEPRINT-HASH" in result.output


def test_lint_plan_full_gate_requires_a_config_snapshot(tmp_path: Path) -> None:
    run_dir, plan_path, spec_path = _full_lint_run(tmp_path)
    atomic_write_canonical_json(run_dir / "run.json", {"schema_version": "1.0"})
    result = runner.invoke(
        app,
        [
            "lint",
            "plan",
            str(plan_path),
            "--spec",
            str(spec_path),
            "--run-dir",
            str(run_dir),
            "--repo-root",
            str(tmp_path / "repo"),
        ],
    )
    assert result.exit_code != 0
    assert "config_snapshot" in result.output


def test_lint_plan_rejects_spec_hash_mismatch(tmp_path: Path) -> None:
    """CLI 必须用实际 Spec 文件哈希拒绝错位 Plan。"""
    plan_path = tmp_path / "plan.json"
    spec_path = tmp_path / "spec.json"
    plan_path.write_text(json.dumps(make_mini_plan()), encoding="utf-8")
    spec_path.write_text(json.dumps(make_plan_spec()), encoding="utf-8")
    result = runner.invoke(
        app,
        ["lint", "plan", str(plan_path), "--spec", str(spec_path)],
    )
    assert result.exit_code == 1
    assert "PLAN-INPUT-MISMATCH" in result.output


def test_status_json_rebuilds_progress_from_persisted_artifacts(tmp_path: Path) -> None:
    store = create_run(
        tmp_path,
        "sample",
        "spec-run",
        inputs=_run_inputs(),
    )
    store.add_budget_used(wall_clock_s=1.25, cost_usd=0.5, tokens_in=10, tokens_out=4)
    plan_state = {
        "schema_version": "1.0",
        "plan_ref": {"path": "plan/plan.json", "sha256": "ef" * 32},
        "tasks": [
            {
                "id": "T-001",
                "status": "done",
                "attempts": 1,
                "notes": "",
                "commit_sha": "12" * 20,
                "last_error": None,
                "acceptance_evidence": {
                    "task_evidence_ref": {
                        "path": "test_results/task_evidence/T-001/attempt_001.json",
                        "sha256": "34" * 32,
                    }
                },
            },
            {
                "id": "T-002",
                "status": "pending",
                "attempts": 0,
                "notes": "",
                "commit_sha": None,
                "last_error": None,
                "acceptance_evidence": {"task_evidence_ref": None},
            },
        ],
    }
    atomic_write_canonical_json(store.run_dir / "plan" / "plan_state.json", plan_state)

    result = runner.invoke(app, ["status", str(store.run_dir), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["run_id"] == store.run_id
    assert payload["current_stage"] == "s4"
    assert payload["budget_used"]["wall_clock_s"] == 1.25
    assert payload["task_progress"] == {
        "total": 2,
        "counts": {
            "pending": 1,
            "in_progress": 0,
            "done": 1,
            "blocked": 0,
            "blocked_by_dependency": 0,
        },
    }


def test_status_resolves_run_id_under_explicit_runs_root(tmp_path: Path) -> None:
    store = create_run(
        tmp_path,
        "sample",
        "spec-run",
        inputs=_run_inputs(),
    )

    result = runner.invoke(
        app,
        ["status", store.run_id, "--runs-root", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    assert f"run: {store.run_id} (spec-run)" in result.output
    assert "current_stage: s4" in result.output


def test_status_rejects_missing_or_invalid_run(tmp_path: Path) -> None:
    missing = runner.invoke(
        app,
        ["status", "missing", "--runs-root", str(tmp_path)],
    )
    assert missing.exit_code == 2
    assert "找不到运行目录" in missing.output

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "run.json").write_text("{}", encoding="utf-8")
    invalid = runner.invoke(app, ["status", str(broken)])
    assert invalid.exit_code == 2
    assert "无法加载" in invalid.output
