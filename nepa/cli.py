"""Generation, resume and read-only status with explicit terminal outcomes."""
from __future__ import annotations
import json
import logging
from typing import Sequence
import typer
from .application import build_orchestrator
from .config import ConfigError, load_config
from .run_store import RunStore, RunStoreError
from .speclib.lint import lint_acceptance, lint_spec, lint_target, read_json, _schema_errors

app = typer.Typer(add_completion=False, no_args_is_help=True)
lint_app = typer.Typer(no_args_is_help=True)
app.add_typer(lint_app, name="lint")


def configure_progress_logging() -> None:
    """Emit human-readable progress to stderr while stdout stays machine-readable."""
    logger = logging.getLogger("nepa.runtime")
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def output(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def status_value(store: RunStore) -> dict:
    return {"run_id": store.run_id, "run_dir": str(store.root), "status": store.run["status"],
            "exit_code": store.run["exit_code"], "reason": store.run.get("reason"),
            "tasks_passed": sum(t["status"] == "passed" for t in store.run["tasks"].values()),
            "tasks_total": len(store.run["tasks"]), "budget": store.run["budget"],
            "delivery": store.run.get("delivery", {}).get("path"), "report": "report.json"}


@app.command("run")
def run_command(
    spec: str = typer.Option(..., "--spec"), target: str = typer.Option(..., "--target"),
    acceptance: str = typer.Option(..., "--acceptance"), config_path: str | None = typer.Option(None, "--config"),
    runs_root: str = typer.Option("runs/e2e", "--runs-root"),
) -> None:
    configure_progress_logging()
    store = RunStore.initialize(runs_root, spec, target, acceptance, load_config(config_path))
    code = build_orchestrator(store).run(store)
    output(status_value(store))
    raise typer.Exit(code)


@app.command("resume")
def resume_command(run_id: str, runs_root: str = typer.Option("runs/e2e", "--runs-root"),
                   config_path: str | None = typer.Option(None, "--config"),
                   accept_runtime_change: bool = typer.Option(False, "--accept-runtime-change"),
                   change_reason: str | None = typer.Option(None, "--change-reason")) -> None:
    configure_progress_logging()
    store = RunStore.open(runs_root, run_id)
    if config_path is not None:
        if not change_reason:
            raise RunStoreError("--config on resume requires --change-reason")
        with store.lock():
            store.reconfigure(load_config(config_path), reason=change_reason, allow_runtime_change=accept_runtime_change)
    elif accept_runtime_change or change_reason:
        raise RunStoreError("runtime/configuration migration requires --config")
    code = build_orchestrator(store).resume(store)
    output(status_value(store))
    raise typer.Exit(code)


@app.command("status")
def status_command(run_id: str, runs_root: str = typer.Option("runs/e2e", "--runs-root")) -> None:
    output(status_value(RunStore.open(runs_root, run_id)))


def finish(report: dict) -> None:
    output(report)
    raise typer.Exit(0 if report["valid"] else 20)


@lint_app.command("spec")
def lint_spec_command(path: str) -> None:
    finish(lint_spec(path))


@lint_app.command("target")
def lint_target_command(path: str, spec: str | None = typer.Option(None, "--spec")) -> None:
    finish(lint_target(path, spec))


@lint_app.command("acceptance")
def lint_acceptance_command(path: str, spec: str | None = typer.Option(None, "--spec")) -> None:
    finish(lint_acceptance(path, spec))


@lint_app.command("plan")
def lint_plan_command(path: str) -> None:
    errors = _schema_errors(read_json(path), "plan.schema.json")
    finish({"valid": not errors, "errors": errors, "warnings": []})


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = typer.main.get_command(app).main(args=list(argv) if argv is not None else None,
                                                 prog_name="nepa", standalone_mode=False)
        return result if isinstance(result, int) else 0
    except typer.Exit as exc:
        return exc.exit_code
    except (ConfigError, RunStoreError, ValueError, OSError) as exc:
        output({"status": "invalid", "exit_code": 20, "error": str(exc)})
        return 20
    except Exception as exc:
        output({"status": "internal_error", "exit_code": 1, "error": f"{type(exc).__name__}: {exc}"})
        return 1
