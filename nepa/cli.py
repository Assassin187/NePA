"""NePA command-line entry points."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Any

import typer

from nepa.config import ConfigError, load_config
from nepa.delivery import (
    DeliveryBlueprintError,
    DeliveryCompileError,
    compile_delivery_blueprint,
    compile_delivery_constraints,
)
from nepa.m1_runtime import M1RuntimeError, resume_m1, run_new_m1
from nepa.speclib.lint import LintReport, plan_full_lint, plan_lint, spec_lint
from nepa.status import StatusError, build_run_status, resolve_run_dir

app = typer.Typer(help="NePA protocol generation tools.", no_args_is_help=True)
lint_app = typer.Typer(help="Validate deterministic NePA artifacts.", no_args_is_help=True)
app.add_typer(lint_app, name="lint")


def _emit_runtime_result(result: Any) -> None:
    typer.echo(f"run: {result.run_id}")
    typer.echo(f"path: {result.run_dir}")
    typer.echo(f"state: {result.action}; exit_code: {result.exit_code}")
    if result.exit_code != 0:
        raise typer.Exit(code=result.exit_code)


@app.command("run")
def run_spec(
    spec: Annotated[
        Path,
        typer.Option(
            "--spec",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Validated Spec IR v3 input.",
        ),
    ],
    until: Annotated[
        str,
        typer.Option("--until", help="M1 planned-stop stage (currently s6)."),
    ] = "s6",
    config_path: Annotated[
        Path,
        typer.Option(
            "--config",
            exists=True,
            dir_okay=False,
            readable=True,
            help="NePA YAML configuration.",
        ),
    ] = Path("configs/default.yaml"),
    runs_root: Annotated[
        Path,
        typer.Option("--runs-root", help="Directory used to create the run."),
    ] = Path("runs"),
    repo_root: Annotated[
        Path,
        typer.Option(
            "--repo-root",
            exists=True,
            file_okay=False,
            readable=True,
            help="Repository root used to resolve frozen assets.",
        ),
    ] = Path("."),
) -> None:
    """Create and execute an M1 spec-run through S6."""
    if until != "s6":
        raise typer.BadParameter("M1 当前只支持 --until s6", param_hint="--until")
    try:
        config = load_config(config_path, {"run": {"until": until}})
        result = run_new_m1(
            spec_path=spec,
            runs_root=runs_root,
            repo_root=repo_root,
            config=config,
        )
    except (ConfigError, M1RuntimeError, OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit_runtime_result(result)


@app.command("resume")
def resume_run(
    run: Annotated[str, typer.Argument(help="Run id or path to a run directory.")],
    runs_root: Annotated[
        Path,
        typer.Option("--runs-root", help="Root used when RUN is a run id."),
    ] = Path("runs"),
    repo_root: Annotated[
        Path,
        typer.Option(
            "--repo-root",
            exists=True,
            file_okay=False,
            readable=True,
            help="Repository root used to resolve frozen assets.",
        ),
    ] = Path("."),
) -> None:
    """Resume an interrupted M1 spec-run from persisted state."""
    try:
        run_dir = resolve_run_dir(run, runs_root=runs_root)
        result = resume_m1(run_dir, repo_root=repo_root)
    except (M1RuntimeError, StatusError, OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit_runtime_result(result)


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
    run_dir: Annotated[
        Path | None,
        typer.Option(
            "--run-dir",
            exists=True,
            file_okay=False,
            readable=True,
            help="Run directory; rebuilds the four frozen inputs and the Delivery "
            "Blueprint so the full 6.4 acceptance gate can be claimed.",
        ),
    ] = None,
    repo_root: Annotated[
        Path,
        typer.Option(
            "--repo-root",
            exists=True,
            file_okay=False,
            readable=True,
            help="Repository root used to resolve Test Bundle component paths.",
        ),
    ] = Path("."),
) -> None:
    """Validate a plan artifact and its references (basic, or full with --run-dir)."""
    raw_plan = _load_json(path)
    raw_spec = _load_json(spec)
    if not isinstance(raw_plan, dict) or not isinstance(raw_spec, dict):
        raise typer.BadParameter("plan 与 spec 顶层都必须是 JSON 对象")
    if run_dir is None:
        _emit_report(
            plan_lint(
                raw_plan,
                raw_spec,
                tests_manifest=_manifest_tests(manifest),
                expected_input_refs={"spec": {"sha256": _sha256_file(spec)}},
            )
        )
        return
    _emit_report(_full_plan_lint(raw_plan, raw_spec, spec, run_dir, repo_root))


def _full_plan_lint(
    plan: dict[str, Any],
    spec_value: dict[str, Any],
    spec_path: Path,
    run_dir: Path,
    repo_root: Path,
) -> LintReport:
    """5.2.5：从 run 目录重建四项冻结输入与 Blueprint，再跑 stage full lint。"""
    run_meta = _load_json(run_dir / "run.json")
    if not isinstance(run_meta, dict):
        raise typer.BadParameter(f"{run_dir}/run.json 顶层必须是 JSON 对象")
    config_snapshot = run_meta.get("config_snapshot")
    if not isinstance(config_snapshot, dict):
        raise typer.BadParameter("run.json 缺少 config_snapshot")
    target = _load_json(run_dir / "inputs" / "target.json")
    language = _load_json(run_dir / "inputs" / "language.json")
    test_bundle = _load_json(run_dir / "inputs" / "test_bundle.json")
    if (
        not isinstance(target, dict)
        or not isinstance(language, dict)
        or not isinstance(test_bundle, dict)
    ):
        raise typer.BadParameter("run 内冻结的三项资产描述必须都是 JSON 对象")
    manifest_ref = test_bundle.get("manifest_ref")
    if not isinstance(manifest_ref, dict) or not isinstance(manifest_ref.get("path"), str):
        raise typer.BadParameter("inputs/test_bundle.json 缺少 manifest_ref.path")
    manifest_value = _load_json(repo_root / str(manifest_ref["path"]))
    if not isinstance(manifest_value, dict) or not isinstance(manifest_value.get("tests"), list):
        raise typer.BadParameter("Test Manifest 必须是含 tests 数组的 JSON 对象")
    try:
        constraints = compile_delivery_constraints(
            spec_value,
            target,
            language,
            test_bundle,
            manifest_value,
        )
        blueprint = compile_delivery_blueprint(
            constraints,
            plan.get("architecture", {}),
            list(plan.get("work_packages", [])),
            list(plan.get("tasks", [])),
        )
    except (DeliveryBlueprintError, DeliveryCompileError, KeyError, TypeError) as exc:
        raise typer.BadParameter(f"无法从本次 run 重建 Delivery 资产: {exc}") from exc
    expected_input_refs = {
        "spec": {"sha256": _sha256_file(spec_path)},
        "target_profile": {
            "path": "inputs/target.json",
            "sha256": _sha256_file(run_dir / "inputs" / "target.json"),
        },
        "language_profile": {
            "path": "inputs/language.json",
            "sha256": _sha256_file(run_dir / "inputs" / "language.json"),
        },
        "test_bundle": {
            "path": "inputs/test_bundle.json",
            "sha256": _sha256_file(run_dir / "inputs" / "test_bundle.json"),
        },
    }
    return plan_full_lint(
        plan,
        spec_value,
        constraints=constraints,
        blueprint=blueprint,
        tests_manifest=list(manifest_value["tests"]),
        config_snapshot=config_snapshot,
        expected_input_refs=expected_input_refs,
    )


if __name__ == "__main__":
    app()
