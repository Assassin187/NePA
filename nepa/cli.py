"""NePA 命令行入口。

M0 只交付 ``nepa lint spec|plan``。设计文档 8.7 中其余运行命令属于 M1
及以后里程碑，本模块在 M0 阶段不得提前提供空壳或伪实现。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Any

import typer

from nepa.speclib.lint import LintReport, plan_lint, spec_lint
from nepa.status import StatusError, build_run_status, resolve_run_dir

app = typer.Typer(help="NePA protocol generation tools.", no_args_is_help=True)
lint_app = typer.Typer(help="Validate deterministic NePA artifacts.", no_args_is_help=True)
app.add_typer(lint_app, name="lint")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise typer.BadParameter(f"无法读取 {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(
            f"{path} 不是合法 JSON：第 {exc.lineno} 行第 {exc.colno} 列"
        ) from exc


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_tests(path: Path | None) -> list[Any] | None:
    if path is None:
        return None
    raw = _load_json(path)
    if isinstance(raw, dict):
        tests = raw.get("tests")
    else:
        tests = raw
    if not isinstance(tests, list):
        raise typer.BadParameter(f"{path}: tests_manifest 必须包含 tests 数组")
    return tests


def _emit_report(report: LintReport) -> None:
    for issue in report.errors:
        typer.echo(f"ERROR {issue.code} {issue.path}: {issue.message}")
    for issue in report.warnings:
        typer.echo(f"WARN  {issue.code} {issue.path}: {issue.message}")
    typer.echo(f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    if not report.ok:
        raise typer.Exit(code=1)


@app.command("status")
def run_status(
    run: Annotated[str, typer.Argument(help="Run id or path to a run directory.")],
    runs_root: Annotated[
        Path,
        typer.Option("--runs-root", help="Root used when RUN is a run id."),
    ] = Path("runs"),
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit a stable machine-readable JSON snapshot."),
    ] = False,
) -> None:
    """Rebuild run progress from persisted artifacts."""
    try:
        status = build_run_status(resolve_run_dir(run, runs_root=runs_root))
    except StatusError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if as_json:
        typer.echo(
            json.dumps(
                status,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
        )
        return

    terminal = status["termination_kind"] or "running"
    current = status["current_stage"] or "-"
    budget = status["budget_used"]
    typer.echo(f"run: {status['run_id']} ({status['entry']})")
    typer.echo(f"state: {terminal}; current_stage: {current}")
    typer.echo(
        "budget: "
        f"{budget['wall_clock_s']:.3f}s, "
        f"${budget['cost_usd']:.6f}, "
        f"{budget['tokens_in']} in / {budget['tokens_out']} out"
    )
    progress = status["task_progress"]
    if progress is not None:
        counts = progress["counts"]
        typer.echo(
            "tasks: "
            f"{counts['done']}/{progress['total']} done, "
            f"{counts['blocked'] + counts['blocked_by_dependency']} blocked, "
            f"{counts['in_progress']} in progress"
        )


@lint_app.command("spec")
def lint_spec(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    manifest: Annotated[
        Path | None,
        typer.Option(
            "--manifest",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Gold tests_manifest.json; enables test-reference validation.",
        ),
    ] = None,
    gold: Annotated[
        bool,
        typer.Option(
            "--gold",
            help="Require every MUST/MUST NOT requirement to reference tests.",
        ),
    ] = False,
) -> None:
    """Validate a Spec IR artifact."""
    raw = _load_json(path)
    if not isinstance(raw, dict):
        raise typer.BadParameter(f"{path}: spec 顶层必须是 JSON 对象")
    _emit_report(spec_lint(raw, tests_manifest=_manifest_tests(manifest), gold_mode=gold))


@lint_app.command("plan")
def lint_plan(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    spec: Annotated[
        Path,
        typer.Option(
            "--spec",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Spec IR used by this plan.",
        ),
    ],
    manifest: Annotated[
        Path | None,
        typer.Option(
            "--manifest",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Gold tests_manifest.json.",
        ),
    ] = None,
) -> None:
    """Validate a plan artifact and its references."""
    raw_plan = _load_json(path)
    raw_spec = _load_json(spec)
    if not isinstance(raw_plan, dict) or not isinstance(raw_spec, dict):
        raise typer.BadParameter("plan 与 spec 顶层都必须是 JSON 对象")
    _emit_report(
        plan_lint(
            raw_plan,
            raw_spec,
            tests_manifest=_manifest_tests(manifest),
            expected_input_refs={"spec": {"sha256": _sha256_file(spec)}},
        )
    )


if __name__ == "__main__":
    app()
