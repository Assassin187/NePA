"""Typer command-line entry point for the M0 lint commands."""

from __future__ import annotations

import json
from typing import Sequence

import typer

from .speclib.lint import lint_spec, lint_target, lint_test_bundle


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
