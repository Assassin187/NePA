"""Report v2 M1 partial producer 测试（设计文档 5.4、6.9）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from nepa.canonical import atomic_write_canonical_json, canonical_json_bytes
from nepa.reporting import (
    build_partial_report,
    classify_partial_outcome,
    write_partial_report,
)
from nepa.run_store import RunStore, create_run


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _frozen_values() -> dict[str, dict[str, Any]]:
    return {
        "target_profile": {"schema_version": "1.0", "asset": {"id": "target"}},
        "language_profile": {"schema_version": "1.0", "asset": {"id": "language"}},
        "test_bundle": {"schema_version": "1.0", "asset": {"id": "tests"}},
    }


def _spec() -> dict[str, Any]:
    return {
        "schema_version": "3.0",
        "protocol": {"name": "sample", "version": "1"},
        "types": [
            {
                "id": "frame",
                "req_ids": ["REQ-SAMPLE-001"],
            }
        ],
        "requirements": [
            {
                "id": "REQ-SAMPLE-001",
                "level": "MUST",
                "text": "The implementation must encode a frame.",
            },
            {
                "id": "REQ-SAMPLE-002",
                "level": "SHOULD",
                "text": "The implementation should reject malformed input.",
            },
        ],
    }


def _make_store(tmp_path: Path) -> RunStore:
    values = _frozen_values()
    spec = _spec()
    inputs = {
        "spec": {"path": "source-spec.json", "sha256": _sha(spec)},
        "target_profile": {
            "id": "target",
            "version": "1.0",
            "path": "inputs/target.json",
            "sha256": _sha(values["target_profile"]),
        },
        "language_profile": {
            "id": "language",
            "version": "1.0",
            "path": "inputs/language.json",
            "sha256": _sha(values["language_profile"]),
        },
        "test_bundle": {
            "id": "tests",
            "version": "1.0",
            "path": "inputs/test_bundle.json",
            "sha256": _sha(values["test_bundle"]),
        },
    }
    store = create_run(
        tmp_path,
        "sample",
        "spec-run",
        inputs=inputs,
        config_snapshot={"budgets": {"task_fix_attempts": 3}},
    )
    atomic_write_canonical_json(store.run_dir / "inputs" / "target.json", values["target_profile"])
    atomic_write_canonical_json(
        store.run_dir / "inputs" / "language.json",
        values["language_profile"],
    )
    atomic_write_canonical_json(
        store.run_dir / "inputs" / "test_bundle.json",
        values["test_bundle"],
    )
    atomic_write_canonical_json(store.run_dir / "spec" / "spec.json", spec)
    return store


def _plan() -> dict[str, Any]:
    return {
        "schema_version": "3.0",
        "architecture": {
            "assumptions": [
                {"statement": "Inputs are complete frames."},
            ]
        },
        "tasks": [{"id": "T-001"}, {"id": "T-002"}],
        "coverage": {
            "requirements": [
                {
                    "req_id": "REQ-SAMPLE-001",
                    "primary_task_id": "T-001",
                    "supporting_task_ids": [],
                },
                {
                    "req_id": "REQ-SAMPLE-002",
                    "primary_task_id": "T-002",
                    "supporting_task_ids": [],
                },
            ],
            "tests": [],
        },
        "review": {
            "verdict": "pass",
            "unresolved_minor_issues": [
                {"description": "A non-blocking optimization remains."}
            ],
        },
    }


def _seal_plan(store: RunStore) -> dict[str, str]:
    plan = _plan()
    path = store.run_dir / "plan" / "plan.json"
    atomic_write_canonical_json(path, plan)
    ref = {"path": "plan/plan.json", "sha256": _sha(plan)}
    store.set_stage_status("s4", "running")
    store.set_stage_status(
        "s4",
        "done",
        output_refs={
            "plan": ref,
            "delivery_blueprint_sha256": "ef" * 32,
        },
    )
    return ref


def _schema_validator() -> Draft202012Validator:
    path = Path(__file__).parents[1] / "nepa" / "schemas" / "report.schema.json"
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))


def test_s4_controlled_failure_writes_schema_valid_canonical_partial_report(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    store.set_stage_status("s4", "running")
    store.request_controlled_exit(
        "s4",
        "PLAN_NOT_SEALED",
        "S4 could not seal a valid Plan.",
        error="architecture budget exhausted",
    )

    ref = write_partial_report(store, outcome="failed")

    path = store.run_dir / ref["path"]
    raw = path.read_bytes()
    report = json.loads(raw)
    _schema_validator().validate(report)
    assert raw == canonical_json_bytes(report)
    assert ref["sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["termination_reason"]["code"] == "PLAN_NOT_SEALED"
    assert report["termination_reason"] == (
        store.meta.termination_request.reason.model_dump(mode="json")
        if store.meta.termination_request is not None
        else None
    )
    assert report["req_coverage"]["status"] == "unavailable"
    assert report["req_coverage"]["value"] is None
    assert report["artifact_availability"]["plan"]["status"] == "unavailable"
    assert store.meta.termination_kind is None


def test_s4_entry_budget_exhaustion_without_plan_is_degraded(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.request_controlled_exit(
        "s4",
        "GLOBAL_BUDGET_EXHAUSTED",
        "Budget exhausted before S4 admission side effects.",
    )

    assert classify_partial_outcome(store) == "degraded"


def test_s6_entry_budget_exhaustion_without_plan_state_is_degraded(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    _seal_plan(store)
    store.set_stage_status("s5", "running")
    store.set_stage_status("s5", "done")
    store.request_controlled_exit(
        "s6",
        "GLOBAL_BUDGET_EXHAUSTED",
        "Budget exhausted before Plan State initialization.",
    )

    assert classify_partial_outcome(store) == "degraded"


def test_s5_failure_uses_sealed_plan_for_static_coverage_and_assumptions(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    _seal_plan(store)
    store.set_stage_status("s5", "running")
    store.request_controlled_exit(
        "s5",
        "DELIVERY_BLUEPRINT_DRIFT",
        "S5 rejected a recomputed Delivery Blueprint.",
        error="blueprint drift",
    )

    report = build_partial_report(store, outcome="failed")

    assert report["artifact_availability"]["plan"]["status"] == "available"
    assert report["req_coverage"]["status"] == "available"
    rows = report["req_coverage"]["value"]
    assert [row["code_status"] for row in rows] == ["not_started", "not_started"]
    assert all(row["test_status"] == "not_run" for row in rows)
    assert report["assumptions_and_defects"] == {
        "status": "available",
        "value": {
            "assumptions": ["Inputs are complete frames."],
            "known_defects": ["A non-blocking optimization remains."],
        },
    }
    _schema_validator().validate(report)


def test_s6_failure_reports_live_plan_state_without_claiming_tests_or_build(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    plan_ref = _seal_plan(store)
    manifest = {"schema_version": "1.0"}
    contract_map = {"schema_version": "1.0"}
    atomic_write_canonical_json(store.run_dir / "plan" / "artifact_manifest.json", manifest)
    atomic_write_canonical_json(store.run_dir / "plan" / "contract_map.json", contract_map)
    store.set_stage_status("s5", "running")
    store.set_stage_status(
        "s5",
        "done",
        output_refs={
            "artifact_manifest": {
                "path": "plan/artifact_manifest.json",
                "sha256": _sha(manifest),
            },
            "contract_map": {
                "path": "plan/contract_map.json",
                "sha256": _sha(contract_map),
            },
            "workspace_head": "abcdef1",
        },
    )
    state = {
        "schema_version": "1.0",
        "plan_ref": plan_ref,
        "tasks": [
            {"id": "T-001", "status": "done", "attempts": 1},
            {"id": "T-002", "status": "blocked", "attempts": 4},
        ],
    }
    atomic_write_canonical_json(store.run_dir / "plan" / "plan_state.json", state)
    store.set_stage_status("s6", "running")
    store.request_controlled_exit(
        "s6",
        "TASK_BLOCKED",
        "One S6 task exhausted its attempts.",
        error="task blocked",
    )

    report = build_partial_report(store, outcome="degraded")

    assert report["artifact_availability"]["plan_state"]["status"] == "available"
    assert report["metrics"]["task_completion_rate"]["value"] == pytest.approx(0.5)
    assert report["metrics"]["first_pass_rate"]["value"] == pytest.approx(0.5)
    assert report["metrics"]["escalation_rate"]["value"] == pytest.approx(0.5)
    assert report["metrics"]["build_ok"]["value"] is None
    assert report["test_final"]["status"] == "not_run"
    assert report["reproducibility"]["git_commit"]["value"] == "abcdef1"
    _schema_validator().validate(report)


def test_partial_report_rejects_non_machine_reason_code(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    with pytest.raises(ValueError, match="code"):
        store.request_controlled_exit("s4", "plan missing", "No Plan.")


def test_partial_report_requires_persisted_termination_request(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    with pytest.raises(ValueError, match="termination_request"):
        build_partial_report(store, outcome="failed")


def test_partial_report_aggregates_raw_calls_by_stage_role_model_and_tier(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    store.set_stage_status("s4", "running")
    store.request_controlled_exit(
        "s4",
        "PLAN_NOT_SEALED",
        "S4 could not seal a valid Plan.",
        error="invalid structured output",
    )
    records = [
        {
            "stage": "S4",
            "agent_role": "architecture_planner",
            "tier": "T1",
            "model": "provider/model",
            "tokens_in": 100,
            "tokens_out": 20,
            "cost_usd": 0.01,
            "cached": False,
            "provider_call_index": 1,
            "call_kind": "initial",
            "validation": "fail",
        },
        {
            "stage": "S4",
            "agent_role": "architecture_planner",
            "tier": "T1",
            "model": "provider/model",
            "tokens_in": 80,
            "tokens_out": 10,
            "cost_usd": 0.02,
            "cached": False,
            "provider_call_index": 2,
            "call_kind": "format_repair",
            "validation": "repaired",
        },
    ]
    trace_path = store.run_dir / "trace" / "llm_calls.ndjson"
    trace_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    store.add_budget_used(cost_usd=0.03, tokens_in=180, tokens_out=30)

    report = build_partial_report(store, outcome="failed")

    assert report["process_stats"]["llm_usage"]["value"] == [
        {
            "stage": "s4",
            "role": "architecture_planner",
            "model": "provider/model",
            "tokens_in": 180,
            "tokens_out": 30,
            "cost_usd": 0.03,
        }
    ]
    assert report["metrics"]["cost"]["by_tier"]["value"][0]["key"] == "T1"
    assert report["metrics"]["validation_repair_rate"]["value"] == pytest.approx(1.0)
    assert report["reproducibility"]["model_versions"]["value"] == ["provider/model"]
    _schema_validator().validate(report)
