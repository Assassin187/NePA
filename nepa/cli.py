"""Typer command-line entry point for the M0 lint commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import typer

from .application import build_orchestrator
from .config import ResolvedConfig, load_config
from .run_store import RunStore, SpecRunInputs
from .speclib.plan_state import execution_state_lint, plan_state_snapshot_lint
from .speclib.lint import lint_spec, lint_target, lint_test_bundle
from .speclib.plan import PlanError, plan_lint


app = typer.Typer(add_completion=False, no_args_is_help=True)
lint_app = typer.Typer(no_args_is_help=True)
app.add_typer(lint_app, name="lint")


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _run_status(store: RunStore, exit_code: int | None) -> dict:
    run = store.load_run()
    state_path = store._confined("plan/plan_state.json")
    state = None
    if state_path.exists():
        state = store._read_json_artifact("plan/plan_state.json")
    rows = state.get("tasks", []) if isinstance(state, dict) else []
    leases = {"started": 0, "finished": 0, "pending": 0, "ids": []}
    revision_path = store._confined("plan/revision_ledger.json")
    if revision_path.exists():
        try:
            revision = store._read_json_artifact("plan/revision_ledger.json")
            entries = revision.get("entries", []) if isinstance(revision, dict) else []
            starts = [item for item in entries if isinstance(item, dict) and item.get("event_type") == "lease_started"]
            finishes = {item.get("payload", {}).get("lease_id") for item in entries if isinstance(item, dict) and item.get("event_type") == "lease_finished"}
            ids = [str(item.get("payload", {}).get("lease_id")) for item in starts]
            leases = {"started": len(starts), "finished": sum(lease_id in finishes for lease_id in ids), "pending": sum(lease_id not in finishes for lease_id in ids), "ids": ids}
        except (OSError, ValueError, TypeError):
            leases = {"started": 0, "finished": 0, "pending": 0, "ids": [], "error": "LEASE_LEDGER_UNAVAILABLE"}
    return {
        "run_id": run["run_id"], "run_dir": str(store.root), "exit_code": exit_code,
        "termination_kind": run.get("termination_kind"), "stages": {key: value["status"] for key, value in run["stages"].items()},
        "budget_used": run["budget_used"], "s6": {
            "tasks": rows, "attempts_used": state.get("s6_attempts_used", 0) if isinstance(state, dict) else 0, "leases": leases,
        },
    }


@app.command("run")
def run_command(
    spec: str = typer.Option(..., "--spec"),
    target_profile: str = typer.Option(..., "--target"),
    test_bundle: str = typer.Option(..., "--test-bundle"),
    runs_root: str = typer.Option("runs", "--runs-root"),
    config_path: str | None = typer.Option(None, "--config"),
    until: str | None = typer.Option(None, "--until"),
) -> None:
    overrides = {"run": {"until": until}} if until is not None else None
    config = load_config(config_path, overrides=overrides)
    store = RunStore.initialize_spec_run(runs_root, SpecRunInputs(spec, target_profile, test_bundle), config)
    code = build_orchestrator(config, store).run_spec(store)
    _print_json(_run_status(store, code))
    raise typer.Exit(code=code)


@app.command("resume")
def resume_command(
    run_id: str,
    runs_root: str = typer.Option("runs", "--runs-root"),
) -> None:
    store = RunStore.open(runs_root, run_id)
    run = store.load_run()
    config = ResolvedConfig.model_validate(run["config_snapshot"])
    code = build_orchestrator(config, store).resume(store)
    _print_json(_run_status(store, code))
    raise typer.Exit(code=code)


@app.command("status")
def status_command(
    run_id: str,
    runs_root: str = typer.Option("runs", "--runs-root"),
) -> None:
    store = RunStore.open(runs_root, run_id)
    _print_json(_run_status(store, store.load_run().get("exit_code")))


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


@lint_app.command("state-snapshot")
def lint_state_snapshot_command(
    plan_path: str,
    state_path: str,
    run_dir: str | None = typer.Option(None, "--run-dir"),
) -> None:
    config = None
    seal = None
    revision = None
    if run_dir is not None:
        store = RunStore(run_dir)
        run = store.load_run()
        config = run["config_snapshot"]
        seal = {"plan": run["stages"]["s4"].get("output_refs", {}).get("plan"), "active_plan": store._read_json_artifact("plan/active_plan.json"), "config_snapshot_sha256": run["config_snapshot_sha256"]}
        revision = store._read_json_artifact("plan/revision_ledger.json")
    report = plan_state_snapshot_lint(plan_path, state_path, s4_seal=seal, config_snapshot=config, revision_ledger=revision)
    _finish(report)


@lint_app.command("state-execution")
def lint_state_execution_command(
    plan_path: str,
    state_path: str,
    run_dir: str,
) -> None:
    store = RunStore(run_dir)
    run = store.load_run()
    stages = json.loads(json.dumps(run["stages"]))
    epoch_ref = stages.get("s5", {}).get("output_refs", {}).get("epoch_receipt")
    if isinstance(epoch_ref, dict):
        epoch = store._read_json_artifact(epoch_ref["path"], schema_name="epoch-receipt.schema.json")
        stages.setdefault("s5", {})["workspace_head"] = epoch["checkpoint_commit"]
    report = execution_state_lint(
        plan_path, state_path, store._confined("workspace"), store.root, stages,
        config_snapshot=run["config_snapshot"], revision_ledger=store._read_json_artifact("plan/revision_ledger.json"),
        active_pointer=store._read_json_artifact("plan/active_plan.json"),
    )
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
