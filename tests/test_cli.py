"""M0-5 ``nepa lint`` CLI tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from nepa.cli import app
from tests.test_plan_lint import make_mini_plan
from tests.test_spec_lint import make_mini_spec

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "nepa" / "schemas" / "examples"
runner = CliRunner()


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
    spec_path.write_text(json.dumps(make_mini_spec()), encoding="utf-8")
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


def test_lint_plan_rejects_spec_hash_mismatch(tmp_path: Path) -> None:
    """CLI 必须用实际 Spec 文件哈希拒绝错位 Plan。"""
    plan_path = tmp_path / "plan.json"
    spec_path = tmp_path / "spec.json"
    plan_path.write_text(json.dumps(make_mini_plan()), encoding="utf-8")
    spec_path.write_text(json.dumps(make_mini_spec()), encoding="utf-8")
    result = runner.invoke(
        app,
        ["lint", "plan", str(plan_path), "--spec", str(spec_path)],
    )
    assert result.exit_code == 1
    assert "PLAN-INPUT-MISMATCH" in result.output
