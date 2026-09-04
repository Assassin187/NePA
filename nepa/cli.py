"""Typer command-line entry point for the M0 lint commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import typer

from .speclib.lint import lint_spec, lint_target, lint_test_bundle
from .speclib.plan import PlanError, plan_lint


app = typer.Typer(add_completion=False, no_args_is_help=True)
lint_app = typer.Typer(no_args_is_help=True)
app.add_typer(lint_app, name="lint")


def _finish(report: dict) -> None:
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    if not report["valid"]:
        raise typer.Exit(code=20)


@lint_app.command("spec")
def lint_spec_command(
    path: str,
    gold: bool = typer.Option(False, "--gold"),
    manifest_path: str | None = typer.Option(None, "--manifest"),
) -> None:
    _finish(lint_spec(path, gold, manifest_path))


@lint_app.command("target")
def lint_target_command(
    path: str,
    spec_path: str | None = typer.Option(None, "--spec"),
) -> None:
    _finish(lint_target(path, spec_path))


@lint_app.command("test-bundle")
def lint_test_bundle_command(
    path: str,
    spec_path: str | None = typer.Option(None, "--spec"),
) -> None:
    _finish(lint_test_bundle(path, spec_path))


@lint_app.command("plan")
def lint_plan_command(
    path: str,
    spec_path: str | None = typer.Option(None, "--spec"),
    manifest_path: str | None = typer.Option(None, "--manifest"),
    run_meta_path: str | None = typer.Option(None, "--run-meta"),
    run_dir: str | None = typer.Option(None, "--run-dir"),
) -> None:
    """Run basic Plan lint, or full lint when a run directory is supplied."""

    try:
        config_snapshot = None
        if run_meta_path is not None:
            run_meta = json.loads(Path(run_meta_path).read_text(encoding="utf-8"))
            if not isinstance(run_meta, dict):
                raise PlanError("run metadata must be a JSON object", code="PLAN_INPUT_INVALID")
            config_snapshot = run_meta.get("config_snapshot", run_meta)
        level = "full" if run_dir is not None else "basic"
        report = plan_lint(
            path,
            spec_path,
            manifest_path,
            config_snapshot,
            level=level,
            run_dir=run_dir,
        )
    except PlanError as exc:
        report = {"level": "full" if run_dir is not None else "basic", "valid": False, "errors": [{"code": exc.code, "path": "/", "message": str(exc)}], "warnings": []}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        report = {"level": "full" if run_dir is not None else "basic", "valid": False, "errors": [{"code": "PLAN_INPUT_INVALID", "path": "/", "message": str(exc)}], "warnings": []}
    _finish(report)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the single Typer CLI and map validation failures to exit code 20."""

    command = typer.main.get_command(app)
    try:
        result = command.main(
            args=list(argv) if argv is not None else None,
            prog_name="nepa",
            standalone_mode=False,
        )
    except typer.Exit as exc:
        return exc.exit_code
    except Exception as exc:  # pragma: no cover - exercised through the process boundary
        report = {
            "valid": False,
            "errors": [{
                "code": "NEPA_INTERNAL_ERROR",
                "path": "/",
                "message": str(exc),
            }],
            "warnings": [],
        }
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        return 1
    return result if isinstance(result, int) else 0
