"""Minimal deterministic Report v2 producer for controlled exits."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..run_store import ArtifactRef, RunStore, RunStoreError


def _reason(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _outcome(request: dict[str, Any]) -> str:
    return "degraded" if "BUDGET" in request["reason"]["code"] else "failed"


def _stage_ref(store: RunStore, run: dict[str, Any], stage_name: str) -> ArtifactRef | None:
    stage = run["stages"][stage_name]
    if stage["status"] != "done" or not isinstance(stage.get("output_refs"), dict):
        return None
    refs = list(stage["output_refs"].values())
    if not refs:
        return None
    try:
        store.verify_stage_refs(stage)
    except RunStoreError:
        return None
    return ArtifactRef.from_value(refs[0])


def _availability(store: RunStore, run: dict[str, Any], stage_name: str, name: str) -> dict[str, Any]:
    ref = _stage_ref(store, run, stage_name)
    if ref is not None:
        return {"status": "available", "evidence": ref.as_dict()}
    stage = run["stages"][stage_name]
    if stage["status"] in {"pending", "skipped"}:
        return {"status": "not_run", "reason": _reason("STAGE_NOT_RUN", f"{name} was not run.")}
    return {"status": "invalid", "reason": _reason("STAGE_OUTPUT_UNAVAILABLE", f"{name} has no verified output.")}


def _duration_map(run: dict[str, Any]) -> dict[str, float]:
    durations: dict[str, float] = {}
    for name, stage in run["stages"].items():
        if not stage.get("started_at") or not stage.get("ended_at"):
            continue
        try:
            start = datetime.fromisoformat(stage["started_at"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(stage["ended_at"].replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            continue
        durations[name] = max(0.0, (end - start).total_seconds())
    return durations


def build_controlled_exit_report(store: RunStore) -> dict[str, Any]:
    run = store.load_run()
    request = run.get("termination_request")
    if not isinstance(request, dict):
        raise RunStoreError("controlled-exit report requires a persisted termination request")
    reason = request["reason"]
    plan = _availability(store, run, "s4", "Plan")
    code = _availability(store, run, "s5", "generated code")
    tests = _availability(store, run, "s7", "final tests")
    report = {
        "schema_version": "2.0",
        "artifact_availability": {
            "spec": {"status": "available", "evidence": {"path": "spec/spec.json", "sha256": run["inputs"]["spec"]["sha256"]}},
            "target_profile": {"status": "available", "evidence": run["inputs"]["target_profile"]},
            "test_bundle": {
                "status": "available",
                "evidence": {
                    "path": run["inputs"]["test_bundle"]["path"],
                    "sha256": run["inputs"]["test_bundle"]["sha256"],
                },
            },
            "plan": plan,
            "generated_code": code,
            "test_results": tests,
        },
        "termination_kind": "controlled_exit",
        "outcome": _outcome(request),
        "termination_reason": reason,
        "summary": f"Controlled exit at {request['stage']}: {reason['detail']}",
        "req_coverage": {"status": "unavailable", "value": None, "reason": _reason("PLAN_NOT_SEALED", "Requirement coverage is unavailable before a valid Plan seal.")},
        "test_final": {"status": "not_run", "value": None, "reason": _reason("TESTS_NOT_RUN", "No terminal test round was run in M1-1.")},
        "process": {
            "stage_durations": _duration_map(run),
            "model_usage": [],
            "repair_rounds": 0,
            "convergence": [],
        },
        "assumptions": {"status": "unavailable", "value": None, "reason": _reason("ASSUMPTIONS_UNAVAILABLE", "No sealed Plan assumptions are available.")},
        "known_defects": {"status": "unavailable", "value": None, "reason": _reason("DEFECTS_UNAVAILABLE", "No terminal defect inventory is available.")},
        "reproduction": {"config_snapshot_sha256": run["config_snapshot_sha256"]},
    }
    return report


def _render_markdown(report: dict[str, Any]) -> bytes:
    reason = report["termination_reason"]
    lines = [
        "# NePA partial report",
        "",
        f"- termination: `{report['termination_kind']}`",
        f"- outcome: `{report['outcome']}`",
        f"- reason: `{reason['code']}` — {reason['detail']}",
        "",
        report["summary"],
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def publish_controlled_exit_report(store: RunStore) -> ArtifactRef:
    report = build_controlled_exit_report(store)
    report_ref = store.publish_immutable_json("report/report.json", report, schema_name="report.schema.json")
    store.publish_immutable_bytes("report/report.md", _render_markdown(report))
    return report_ref


def validate_controlled_exit_report(store: RunStore, run: dict[str, Any]) -> bool:
    stage = run["stages"]["s9"]
    refs = stage.get("output_refs")
    request = run.get("termination_request")
    if stage.get("status") != "done" or not isinstance(refs, dict) or not isinstance(request, dict):
        return False
    try:
        json_ref = refs["report_json"]
        md_ref = refs["report_md"]
        store.verify_ref(json_ref, schema_name="report.schema.json")
        store.verify_ref(md_ref)
        report_path = store._confined(json_ref["path"])
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (KeyError, TypeError, ValueError, OSError, RunStoreError, json.JSONDecodeError):
        return False
    return (
        report.get("termination_kind") == "controlled_exit"
        and report.get("termination_reason") == request.get("reason")
        and report.get("outcome") in {"degraded", "failed"}
    )
