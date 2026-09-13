"""Generation, resume and read-only status with explicit terminal outcomes."""
from __future__ import annotations
import json
from typing import Sequence
import typer
from .application import build_orchestrator
from .config import ConfigError, load_config
from .run_store import RunStore, RunStoreError
from .speclib.lint import lint_acceptance, lint_spec, lint_target, read_json, _schema_errors
from .spec_extract.pipeline import extract_document
from .spec_extract.projection import project_to_v3, ProjectionError, validate_v4

app = typer.Typer(add_completion=False, no_args_is_help=True)
lint_app = typer.Typer(no_args_is_help=True)
app.add_typer(lint_app, name="lint")

def _write_immutable(path: str, value: object) -> None:
    from pathlib import Path
    target = Path(path)
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    if target.exists() and target.read_bytes() != data:
        raise RunStoreError(f"immutable artifact already exists with different content: {path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(data)

@app.command("spec-review")
def spec_review_command(spec: str = typer.Option(..., "--spec"), output_path: str = typer.Option(..., "--output")) -> None:
    """Create a review record for a draft v4 specification."""
    value = read_json(spec)
    if str(value.get("schema_version")) not in {"4", "4.0"}:
        raise RunStoreError("spec-review requires Spec IR v4 draft")
    errors = validate_v4(value)
    if errors:
        raise RunStoreError("invalid Spec IR v4: " + "; ".join(errors))
    record = {"status": "reviewed", "spec": str(__import__('pathlib').Path(spec).resolve()),
              "spec_sha256": __import__('hashlib').sha256(__import__('pathlib').Path(spec).read_bytes()).hexdigest(),
              "gaps": value.get("gaps", []), "conflicts": value.get("conflicts", [])}
    _write_immutable(output_path, record)
    output(record)

@app.command("spec-approve")
def spec_approve_command(spec: str = typer.Option(..., "--spec"), review: str = typer.Option(..., "--review"), output_path: str = typer.Option(..., "--output")) -> None:
    value = read_json(spec); rev = read_json(review)
    spec_hash = __import__('hashlib').sha256(__import__('pathlib').Path(spec).read_bytes()).hexdigest()
    if str(value.get("schema_version")) not in {"4", "4.0"} or rev.get("status") != "reviewed" or rev.get("spec_sha256") != spec_hash:
        raise RunStoreError("approval requires reviewed Spec IR v4")
    open_gaps = [g for g in value.get("gaps", []) if g.get("status", "open") == "open" and g.get("category") != "out_of_scope"]
    if open_gaps:
        raise RunStoreError(f"cannot approve with open gaps: {len(open_gaps)}")
    value["approval"] = {"status": "approved", "review": str(__import__('pathlib').Path(review).resolve()), "spec_sha256": spec_hash}
    _write_immutable(output_path, value)
    output({"status": "approved", "output": output_path})

@app.command("spec-project")
def spec_project_command(spec: str = typer.Option(..., "--spec"), output_path: str = typer.Option(..., "--output")) -> None:
    value = read_json(spec)
    if value.get("approval", {}).get("status") != "approved":
        raise RunStoreError("spec-project requires approved v4 specification")
    projected, report = project_to_v3(value)
    _write_immutable(output_path, projected)
    _write_immutable(output_path + ".projection.json", report)
    output({"status": "projected", "output": output_path, "report": report})

@app.command("spec-extract")
def spec_extract_command(
    rfc: str = typer.Option(..., "--rfc"),
    output_path: str = typer.Option(..., "--output"),
    evidence_path: str | None = typer.Option(None, "--evidence"),
    gap_path: str | None = typer.Option(None, "--gaps"),
    doc_id: str | None = typer.Option(None, "--doc-id"),
    protocol: str | None = typer.Option(None, "--protocol"),
    version: str | None = typer.Option(None, "--version"),
    scope: str | None = typer.Option(None, "--scope"),
    live: bool = typer.Option(False, "--live"),
    config_path: str | None = typer.Option(None, "--config"),
) -> None:
    sections = None
    if scope:
        import yaml
        scope_data = yaml.safe_load(open(scope, encoding="utf-8"))
        sections = scope_data.get("sections", [])
    provider = None
    if live:
        from .spec_extract.deepseek import live_provider
        provider = live_provider(load_config(config_path))
    if not evidence_path or not gap_path or not sections:
        raise RunStoreError("RFC extraction requires --scope, --evidence and --gaps")
    spec = extract_document(rfc, doc_id=doc_id, protocol_name=protocol, protocol_version=version,
                            output=output_path, evidence=evidence_path, gap_file=gap_path,
                            sections=sections, llm_provider=provider, ir_version="4.0")
    report = {"valid": spec.get("schema_version") == "4.0" and not spec.get("gaps")}
    output({"valid": report["valid"], "spec": output_path, "evidence": evidence_path, "gaps": gap_path,
            "requirements": len(spec["requirements"]), "errors": report.get("errors", [])})
    if not report["valid"]:
        raise typer.Exit(20)


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
    config = load_config(config_path)
    spec_value = read_json(spec)
    if str(spec_value.get("schema_version")) != "3.0":
        raise RunStoreError("run requires Spec IR v3 (use spec-project for approved RFC v4)")
    store = RunStore.initialize(runs_root, spec, target, acceptance, config)
    code = build_orchestrator(store).run(store)
    output(status_value(store))
    raise typer.Exit(code)


@app.command("resume")
def resume_command(run_id: str, runs_root: str = typer.Option("runs/e2e", "--runs-root"),
                   config_path: str | None = typer.Option(None, "--config"),
                   accept_runtime_change: bool = typer.Option(False, "--accept-runtime-change"),
                   change_reason: str | None = typer.Option(None, "--change-reason")) -> None:
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
