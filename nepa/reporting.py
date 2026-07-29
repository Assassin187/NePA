"""Report v2 的 M1 条件化 partial producer（设计文档 5.4、6.9）。"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator

from nepa.canonical import atomic_write_canonical_json
from nepa.reasons import reason_dict
from nepa.run_store import RunStore

FlowOutcome = Literal["degraded", "failed"]


class ReportValidationError(RuntimeError):
    """确定性 report producer 生成了不符合 v2 Schema 的对象。"""


def _report_validator() -> Draft202012Validator:
    schema_path = Path(__file__).resolve().parent / "schemas" / "report.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _validate_report_schema(report: Any) -> dict[str, Any]:
    errors = sorted(
        _report_validator().iter_errors(report),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        messages = "; ".join(
            f"{'/'.join(map(str, item.absolute_path)) or '<root>'}: {item.message}"
            for item in errors[:8]
        )
        raise ReportValidationError(messages)
    if not isinstance(report, dict):
        raise ReportValidationError("report root must be an object")
    return report


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def classify_partial_outcome(store: RunStore) -> FlowOutcome:
    """按 9.1.2 对 M1 S4～S6 受控出口做确定性分类。"""
    request = store.meta.termination_request
    if request is None:
        raise ValueError("partial outcome classification requires termination_request")
    plan = _sealed_artifact(
        store,
        stage="s4",
        key="plan",
        default_relative="plan/plan.json",
        absent_code="PLAN_NOT_SEALED",
        absent_detail="S4 did not publish an immutable Plan.",
    )
    budget_exhausted = request.reason.code == "GLOBAL_BUDGET_EXHAUSTED"
    if plan["status"] != "available":
        # At the S4 admission boundary no Plan is expected yet. A global budget
        # jump from that boundary is degraded by 9.1.2, not a broken artifact
        # chain. Invalid/present artifacts remain failed.
        if (
            budget_exhausted
            and request.stage == "s4"
            and plan["status"] == "unavailable"
        ):
            return "degraded"
        return "failed"
    if request.stage == "s5" and not budget_exhausted:
        return "failed"
    if request.stage == "s6":
        state = _plan_state_artifact(store, plan_available=True)
        if state["status"] != "available":
            # S6 admission initializes Plan State. Budget exhaustion before that
            # side effect legitimately leaves it absent; malformed/present State
            # is still a failed static chain.
            if budget_exhausted and state["status"] == "not_run":
                return "degraded"
            return "failed"
    return "degraded"


def validate_controlled_report_ref(
    store: RunStore,
    ref: Any,
) -> dict[str, Any]:
    """校验 S9 receipt、文件哈希、Report Schema 与 request 交叉绑定。"""
    request = store.meta.termination_request
    if request is None:
        raise ReportValidationError("controlled report requires termination_request")
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
        raise ReportValidationError("s9 report receipt must be {path, sha256}")
    if ref.get("path") != "report/report.json":
        raise ReportValidationError("s9 report receipt path must be report/report.json")
    expected_sha256 = ref.get("sha256")
    if not isinstance(expected_sha256, str):
        raise ReportValidationError("s9 report receipt sha256 must be a string")
    path = store.run_dir / "report" / "report.json"
    if not path.is_file() or _sha256_file(path) != expected_sha256:
        raise ReportValidationError("s9 report artifact is missing or does not match receipt")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportValidationError(f"report/report.json is not valid JSON: {exc}") from exc
    validated = _validate_report_schema(report)
    if validated.get("run_id") != store.run_id:
        raise ReportValidationError("report run_id does not match run.json")
    if validated.get("termination_kind") != "controlled_exit":
        raise ReportValidationError("report termination_kind is not controlled_exit")
    if validated.get("termination_reason") != request.reason.model_dump(mode="json"):
        raise ReportValidationError(
            "report termination_reason does not equal termination_request.reason"
        )
    return validated


def _reason(code: str, detail: str) -> dict[str, str]:
    return reason_dict(code, detail)


def _missing(status: str, code: str, detail: str) -> dict[str, Any]:
    return {
        "status": status,
        "value": None,
        "reason": _reason(code, detail),
    }


def _available(value: Any) -> dict[str, Any]:
    if value is None:
        raise ValueError("available report value must not be null")
    return {"status": "available", "value": value}


def _artifact_missing(status: str, code: str, detail: str) -> dict[str, Any]:
    return {"status": status, "reason": _reason(code, detail)}


def _artifact_file(path: Path, relative: str, expected_sha256: str) -> dict[str, Any]:
    if not path.is_file():
        return _artifact_missing(
            "unavailable",
            "ARTIFACT_MISSING",
            f"Expected artifact {relative} does not exist.",
        )
    actual = _sha256_file(path)
    evidence = {"path": relative, "sha256": actual}
    if actual != expected_sha256:
        return {
            "status": "invalid",
            "evidence": evidence,
            "reason": _reason(
                "ARTIFACT_HASH_MISMATCH",
                f"Artifact {relative} does not match its frozen SHA-256.",
            ),
        }
    return {"status": "available", "evidence": evidence}


def _receipt_ref(store: RunStore, stage: str, key: str) -> dict[str, Any] | None:
    refs = store.meta.stages[stage].output_refs
    if not isinstance(refs, dict):
        return None
    value = refs.get(key)
    return value if isinstance(value, dict) else None


def _sealed_artifact(
    store: RunStore,
    *,
    stage: str,
    key: str,
    default_relative: str,
    absent_code: str,
    absent_detail: str,
) -> dict[str, Any]:
    ref = _receipt_ref(store, stage, key)
    default_path = store.run_dir / default_relative
    if ref is None:
        if default_path.exists():
            evidence: dict[str, Any] = {"path": default_relative}
            if default_path.is_file():
                evidence["sha256"] = _sha256_file(default_path)
            return {
                "status": "invalid",
                "evidence": evidence,
                "reason": _reason(
                    "UNSEALED_ARTIFACT",
                    f"{default_relative} exists without a matching {stage} receipt.",
                ),
            }
        return _artifact_missing("unavailable", absent_code, absent_detail)
    relative = ref.get("path")
    expected = ref.get("sha256")
    if not isinstance(relative, str) or not isinstance(expected, str):
        return _artifact_missing(
            "invalid",
            "INVALID_STAGE_RECEIPT",
            f"{stage}.output_refs.{key} is not a valid file reference.",
        )
    return _artifact_file(store.run_dir / relative, relative, expected)


def _live_json_artifact(path: Path, relative: str, *, not_run_detail: str) -> dict[str, Any]:
    if not path.exists():
        return _artifact_missing("not_run", "STAGE_NOT_RUN", not_run_detail)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "invalid",
            "reason": _reason("ARTIFACT_INVALID_JSON", f"{relative}: {exc}"),
        }
    if not isinstance(raw, dict):
        return {
            "status": "invalid",
            "reason": _reason("ARTIFACT_INVALID_JSON", f"{relative} is not a JSON object."),
        }
    return {
        "status": "available",
        "evidence": {"path": relative, "sha256": _sha256_file(path)},
    }


def _plan_state_artifact(store: RunStore, *, plan_available: bool) -> dict[str, Any]:
    path = store.run_dir / "plan" / "plan_state.json"
    base = _live_json_artifact(
        path,
        "plan/plan_state.json",
        not_run_detail="S6 admission did not initialize Plan State.",
    )
    if base["status"] != "available":
        return base
    if not plan_available:
        return {
            "status": "invalid",
            "evidence": base["evidence"],
            "reason": _reason(
                "PLAN_STATE_WITHOUT_SEALED_PLAN",
                "Plan State exists but no valid sealed Plan is available.",
            ),
        }
    state = _load_json_object(path)
    plan = _load_json_object(store.run_dir / "plan" / "plan.json")
    plan_ref = _receipt_ref(store, "s4", "plan")
    if state is None or plan is None or plan_ref is None or state.get("plan_ref") != plan_ref:
        return {
            "status": "invalid",
            "evidence": base["evidence"],
            "reason": _reason(
                "PLAN_STATE_PLAN_MISMATCH",
                "Plan State does not bind the sealed S4 Plan reference.",
            ),
        }
    plan_ids = {
        str(item.get("id"))
        for item in plan.get("tasks", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    state_ids = {
        str(item.get("id"))
        for item in state.get("tasks", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if plan_ids != state_ids:
        return {
            "status": "invalid",
            "evidence": base["evidence"],
            "reason": _reason(
                "PLAN_STATE_TASK_SET_MISMATCH",
                "Plan State task ids do not exactly match the sealed Plan.",
            ),
        }
    return base


def _stage_durations(store: RunStore) -> dict[str, Any]:
    values: dict[str, float] = {}
    for name, state in store.meta.stages.items():
        if state.started_at is None or state.ended_at is None:
            continue
        started = datetime.fromisoformat(state.started_at)
        ended = datetime.fromisoformat(state.ended_at)
        values[name] = max(0.0, (ended - started).total_seconds())
    return _available(values)


def _trace_aggregates(store: RunStore) -> dict[str, Any]:
    path = store.run_dir / "trace" / "llm_calls.ndjson"
    if not path.exists():
        used = store.meta.budget_used
        if used.cost_usd or used.tokens_in or used.tokens_out:
            invalid = _missing(
                "invalid",
                "TRACE_MISSING",
                "run.json records provider usage but trace/llm_calls.ndjson is missing.",
            )
            return {
                "llm_usage": invalid,
                "by_stage": invalid,
                "by_role": invalid,
                "by_tier": invalid,
                "model_versions": invalid,
                "validation_repair_rate": invalid,
            }
        return {
            "llm_usage": _available([]),
            "by_stage": _available([]),
            "by_role": _available([]),
            "by_tier": _available([]),
            "model_versions": _available([]),
            "validation_repair_rate": _missing(
                "unavailable",
                "NO_LLM_CALLS",
                "No logical LLM call exists for a repair-rate denominator.",
            ),
        }

    records: list[dict[str, Any]] = []
    try:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise TypeError(f"line {line_number} is not an object")
            records.append(value)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        invalid = _missing("invalid", "TRACE_INVALID", str(exc))
        return {
            "llm_usage": invalid,
            "by_stage": invalid,
            "by_role": invalid,
            "by_tier": invalid,
            "model_versions": invalid,
            "validation_repair_rate": invalid,
        }

    usage: defaultdict[tuple[str, str, str], list[float]] = defaultdict(
        lambda: [0.0, 0.0, 0.0]
    )
    breakdowns: dict[str, defaultdict[str, list[float]]] = {
        "by_stage": defaultdict(lambda: [0.0, 0.0, 0.0]),
        "by_role": defaultdict(lambda: [0.0, 0.0, 0.0]),
        "by_tier": defaultdict(lambda: [0.0, 0.0, 0.0]),
    }
    models: set[str] = set()
    logical_calls = 0
    repaired_calls = 0
    pending_initial_validation: str | None = None
    missing_tier = False

    for record in records:
        stage = str(record.get("stage", "")).lower()
        role = str(record.get("agent_role", ""))
        model = str(record.get("model", ""))
        tier_value = record.get("tier")
        tier = str(tier_value) if isinstance(tier_value, str) and tier_value else ""
        missing_tier = missing_tier or not tier
        cached = bool(record.get("cached", False))
        tokens_in = 0 if cached else int(record.get("tokens_in", 0))
        tokens_out = 0 if cached else int(record.get("tokens_out", 0))
        cost = float(record.get("cost_usd", 0.0))
        usage[(stage, role, model)][0] += tokens_in
        usage[(stage, role, model)][1] += tokens_out
        usage[(stage, role, model)][2] += cost
        for dimension, key in (
            ("by_stage", stage),
            ("by_role", role),
            ("by_tier", tier),
        ):
            if not key:
                continue
            bucket = breakdowns[dimension][key]
            bucket[0] += tokens_in
            bucket[1] += tokens_out
            bucket[2] += cost
        if model:
            models.add(model)

        call_kind = record.get("call_kind")
        validation = record.get("validation")
        if call_kind in ("initial", "cache_replay"):
            if pending_initial_validation is not None:
                logical_calls += 1
                repaired_calls += int(pending_initial_validation == "repaired")
            pending_initial_validation = str(validation) if validation is not None else ""
        elif call_kind == "format_repair":
            pending_initial_validation = str(validation) if validation is not None else ""

    if pending_initial_validation is not None:
        logical_calls += 1
        repaired_calls += int(pending_initial_validation == "repaired")

    usage_rows = [
        {
            "stage": stage,
            "role": role,
            "model": model,
            "tokens_in": int(values[0]),
            "tokens_out": int(values[1]),
            "cost_usd": round(values[2], 8),
        }
        for (stage, role, model), values in sorted(usage.items())
    ]

    def breakdown(name: str) -> dict[str, Any]:
        if name == "by_tier" and missing_tier:
            return _missing(
                "unavailable",
                "TRACE_TIER_UNAVAILABLE",
                "LLM trace records do not contain resolved tier attribution.",
            )
        return _available(
            [
                {
                    "key": key,
                    "tokens_in": int(values[0]),
                    "tokens_out": int(values[1]),
                    "cost_usd": round(values[2], 8),
                }
                for key, values in sorted(breakdowns[name].items())
            ]
        )

    repair_rate = (
        _available(repaired_calls / logical_calls)
        if logical_calls
        else _missing(
            "unavailable",
            "NO_LLM_CALLS",
            "No logical LLM call exists for a repair-rate denominator.",
        )
    )
    return {
        "llm_usage": _available(usage_rows),
        "by_stage": breakdown("by_stage"),
        "by_role": breakdown("by_role"),
        "by_tier": breakdown("by_tier"),
        "model_versions": _available(sorted(models)),
        "validation_repair_rate": repair_rate,
    }


def _load_json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _covered_requirement_ids(spec: dict[str, Any]) -> set[str]:
    covered: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            req_ids = value.get("req_ids")
            if isinstance(req_ids, list):
                covered.update(item for item in req_ids if isinstance(item, str))
            for key, item in value.items():
                if key not in ("requirements", "req_ids"):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(spec)
    return covered


def _coverage_and_state_metrics(
    store: RunStore,
    plan_available: bool,
    state_available: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    unavailable = _missing(
        "unavailable",
        "PLAN_NOT_SEALED",
        "Requirement ownership cannot be computed without a sealed Plan.",
    )
    assumptions_unavailable = _missing(
        "unavailable",
        "PLAN_NOT_SEALED",
        "Formal architecture assumptions and review defects are unavailable.",
    )
    state_unavailable = _missing(
        "unavailable",
        "PLAN_STATE_UNAVAILABLE",
        "Task execution state is unavailable.",
    )
    if not plan_available:
        return unavailable, state_unavailable, state_unavailable, assumptions_unavailable

    plan = _load_json_object(store.run_dir / "plan" / "plan.json")
    spec = _load_json_object(store.run_dir / "spec" / "spec.json")
    if plan is None or spec is None:
        invalid = _missing(
            "invalid",
            "PLAN_OR_SPEC_INVALID",
            "Static coverage inputs are not valid JSON objects.",
        )
        return invalid, state_unavailable, state_unavailable, assumptions_unavailable

    state = (
        _load_json_object(store.run_dir / "plan" / "plan_state.json")
        if state_available
        else None
    )
    task_states: dict[str, dict[str, Any]] = {}
    if state is not None and isinstance(state.get("tasks"), list):
        task_states = {
            str(item.get("id")): item
            for item in state["tasks"]
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }

    requirements = {
        str(req.get("id")): req
        for req in spec.get("requirements", [])
        if isinstance(req, dict) and isinstance(req.get("id"), str)
    }
    coverage_root = plan.get("coverage")
    coverage_rows = (
        coverage_root.get("requirements", [])
        if isinstance(coverage_root, dict)
        else []
    )
    if not isinstance(coverage_rows, list):
        coverage_rows = []
    coverage_req_ids = {
        str(row.get("req_id"))
        for row in coverage_rows
        if isinstance(row, dict) and isinstance(row.get("req_id"), str)
    }
    if coverage_req_ids != set(requirements):
        invalid = _missing(
            "invalid",
            "PLAN_COVERAGE_INCOMPLETE",
            "Plan coverage requirement ids do not exactly match Spec requirements.",
        )
        return invalid, state_unavailable, state_unavailable, assumptions_unavailable

    plan_task_ids = {
        str(item.get("id"))
        for item in plan.get("tasks", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    covered_requirement_ids = _covered_requirement_ids(spec)
    result_rows: list[dict[str, Any]] = []
    for row in coverage_rows:
        if not isinstance(row, dict) or not isinstance(row.get("req_id"), str):
            continue
        req_id = row["req_id"]
        req = requirements.get(req_id, {})
        task_ids = [
            task_id
            for task_id in [
                row.get("primary_task_id"),
                *(row.get("supporting_task_ids", []) or []),
            ]
            if isinstance(task_id, str)
        ]
        if any(task_id not in plan_task_ids for task_id in task_ids):
            invalid = _missing(
                "invalid",
                "PLAN_COVERAGE_TASK_MISMATCH",
                f"Coverage for {req_id} references a task outside Plan tasks.",
            )
            return invalid, state_unavailable, state_unavailable, assumptions_unavailable
        statuses = [task_states[item].get("status") for item in task_ids if item in task_states]
        if not task_ids:
            code_status = "none"
        elif state is None:
            code_status = "not_started"
        elif statuses and all(value == "done" for value in statuses):
            code_status = "done"
        elif any(value in ("blocked", "blocked_by_dependency") for value in statuses):
            code_status = "blocked"
        elif any(value in ("done", "in_progress") for value in statuses):
            code_status = "partial"
        else:
            code_status = "not_started"
        result_rows.append(
            {
                "req_id": req_id,
                "level": req.get("level", "DEFINITION"),
                "spec_status": (
                    "covered" if req_id in covered_requirement_ids else "uncovered"
                ),
                "task_ids": sorted(set(task_ids)),
                "code_status": code_status,
                "test_status": "not_run",
            }
        )

    assumptions: list[str] = []
    architecture = plan.get("architecture")
    if isinstance(architecture, dict):
        for item in architecture.get("assumptions", []):
            if isinstance(item, str):
                assumptions.append(item)
            elif isinstance(item, dict):
                statement = item.get("statement")
                if isinstance(statement, str):
                    assumptions.append(statement)
    defects: list[str] = []
    review = plan.get("review")
    if isinstance(review, dict):
        for item in review.get("unresolved_minor_issues", []):
            if isinstance(item, str):
                defects.append(item)
            elif isinstance(item, dict):
                description = item.get("description")
                if isinstance(description, str):
                    defects.append(description)

    tasks = list(task_states.values())
    if state is None or not tasks:
        completion = state_unavailable
        first_pass = state_unavailable
    else:
        completion = _available(
            sum(item.get("status") == "done" for item in tasks) / len(tasks)
        )
        first_pass = _available(
            sum(item.get("status") == "done" and item.get("attempts") == 1 for item in tasks)
            / len(tasks)
        )
    return (
        _available(result_rows),
        completion,
        first_pass,
        _available({"assumptions": assumptions, "known_defects": defects}),
    )


def build_partial_report(
    store: RunStore,
    *,
    outcome: FlowOutcome,
) -> dict[str, Any]:
    """构造 S4～S6 受控早退的 Report v2，不修改 run 状态。"""
    if outcome not in ("degraded", "failed"):
        raise ValueError("partial controlled-exit report outcome must be degraded or failed")
    request = store.meta.termination_request
    if request is None:
        raise ValueError("partial controlled-exit report requires termination_request")
    terminal_reason = request.reason.model_dump(mode="json")
    detail = request.reason.detail
    inputs = store.meta.inputs.model_dump(mode="json")

    artifacts: dict[str, Any] = {
        "run": {
            "status": "available",
            # run.json 在 report 发布后还会原子写入 S9 receipt/终态；此处禁止
            # 保存会立即失效并形成 report↔run 自引用的文件哈希。
            "evidence": {"path": "run.json", "run_id": store.run_id},
        }
    }
    for key in ("target_profile", "language_profile", "test_bundle"):
        ref = inputs[key]
        artifacts[key] = _artifact_file(
            store.run_dir / ref["path"],
            ref["path"],
            ref["sha256"],
        )

    spec_path = store.run_dir / "spec" / "spec.json"
    spec_ref = inputs.get("spec")
    if isinstance(spec_ref, dict):
        artifacts["spec"] = _artifact_file(
            spec_path,
            "spec/spec.json",
            str(spec_ref["sha256"]),
        )
    elif spec_path.is_file():
        artifacts["spec"] = {
            "status": "available",
            "evidence": {
                "path": "spec/spec.json",
                "sha256": _sha256_file(spec_path),
            },
        }
    else:
        artifacts["spec"] = _artifact_missing(
            "unavailable",
            "SPEC_UNAVAILABLE",
            "No reviewed spec/spec.json is available.",
        )

    artifacts["plan"] = _sealed_artifact(
        store,
        stage="s4",
        key="plan",
        default_relative="plan/plan.json",
        absent_code="PLAN_NOT_SEALED",
        absent_detail="S4 did not publish an immutable Plan.",
    )
    artifacts["artifact_manifest"] = _sealed_artifact(
        store,
        stage="s5",
        key="artifact_manifest",
        default_relative="plan/artifact_manifest.json",
        absent_code="S5_NOT_COMPLETED",
        absent_detail="S5 did not publish an artifact manifest.",
    )
    artifacts["contract_map"] = _sealed_artifact(
        store,
        stage="s5",
        key="contract_map",
        default_relative="plan/contract_map.json",
        absent_code="S5_NOT_COMPLETED",
        absent_detail="S5 did not publish a contract map.",
    )
    workspace_head: str | None = None
    for stage in ("s6", "s5"):
        refs = store.meta.stages[stage].output_refs or {}
        candidate = refs.get("workspace_head")
        if isinstance(candidate, str):
            workspace_head = candidate
            break
    if workspace_head is not None:
        artifacts["workspace"] = {
            "status": "available",
            "evidence": {"path": "workspace", "git_commit": workspace_head},
        }
    else:
        artifacts["workspace"] = _artifact_missing(
            "not_run",
            "WORKSPACE_COMMIT_UNAVAILABLE",
            "No sealed workspace commit is available.",
        )
    artifacts["test_results"] = _artifact_missing(
        "not_run",
        "STAGE_NOT_RUN",
        "No accepted terminal test round exists.",
    )
    artifacts["repair_log"] = _artifact_missing(
        "not_run",
        "STAGE_NOT_RUN",
        "S8 repair did not run.",
    )

    plan_available = artifacts["plan"]["status"] == "available"
    artifacts["plan_state"] = _plan_state_artifact(
        store,
        plan_available=plan_available,
    )
    state_available = artifacts["plan_state"]["status"] == "available"
    coverage, task_rate, first_pass, assumptions = _coverage_and_state_metrics(
        store,
        plan_available,
        state_available,
    )
    trace = _trace_aggregates(store)

    def not_run_test(layer: str) -> dict[str, Any]:
        return _missing(
            "not_run",
            "STAGE_NOT_RUN",
            f"{layer.upper()} tests did not run.",
        )
    test_results_missing = _missing(
        "unavailable",
        "TEST_RESULTS_UNAVAILABLE",
        "No requirement-level terminal test results exist.",
    )
    build_status = _missing(
        "not_run" if store.meta.stages["s5"].status == "pending" else "unavailable",
        "BUILD_EVIDENCE_UNAVAILABLE",
        "No sealed build evidence is available for the terminal workspace.",
    )
    plan_ref = _receipt_ref(store, "s4", "plan")
    plan_sha = (
        _available(plan_ref["sha256"])
        if plan_available and isinstance(plan_ref, dict)
        else _missing("unavailable", "PLAN_NOT_SEALED", "No immutable Plan hash exists.")
    )
    s4_refs = store.meta.stages["s4"].output_refs or {}
    blueprint = s4_refs.get("delivery_blueprint_sha256")
    blueprint_sha = (
        _available(blueprint)
        if isinstance(blueprint, str)
        else _missing(
            "unavailable",
            "BLUEPRINT_NOT_SEALED",
            "No sealed Delivery Blueprint hash exists.",
        )
    )
    git_commit = (
        _available(workspace_head)
        if workspace_head is not None
        else _missing(
            "not_run",
            "WORKSPACE_COMMIT_UNAVAILABLE",
            "No workspace commit was created.",
        )
    )
    state_for_escalation = _load_json_object(store.run_dir / "plan" / "plan_state.json")
    escalation_rate: dict[str, Any]
    if state_available and state_for_escalation is not None:
        tasks = [item for item in state_for_escalation.get("tasks", []) if isinstance(item, dict)]
        if tasks:
            t2_limit = int(
                store.meta.config_snapshot.get("budgets", {}).get("task_fix_attempts", 3)
            )
            escalation_rate = _available(
                sum(int(item.get("attempts", 0)) > t2_limit for item in tasks) / len(tasks)
            )
        else:
            escalation_rate = _missing(
                "unavailable",
                "NO_TASKS",
                "No task denominator exists for escalation rate.",
            )
    else:
        escalation_rate = _missing(
            "unavailable",
            "PLAN_STATE_UNAVAILABLE",
            "No task attempts exist for escalation rate.",
        )

    return {
        "schema_version": "2.0",
        "run_id": store.run_id,
        "entry": store.meta.entry,
        "termination_kind": "controlled_exit",
        "outcome": outcome,
        "summary": detail,
        "termination_reason": terminal_reason,
        "frozen_inputs": inputs,
        "artifact_availability": artifacts,
        "req_coverage": coverage,
        "test_final": {
            "status": "not_run",
            "value": None,
            "reason": _reason(
                "STAGE_NOT_RUN",
                "No accepted terminal test round exists.",
            ),
        },
        "process_stats": {
            "stage_duration_s": _stage_durations(store),
            "llm_usage": trace["llm_usage"],
            "repair": _missing("not_run", "STAGE_NOT_RUN", "S8 repair did not run."),
        },
        "metrics": {
            "build_ok": build_status,
            "task_completion_rate": task_rate,
            "first_pass_rate": first_pass,
            "test_pass_rate": {
                layer: not_run_test(layer) for layer in ("l0", "l1", "l2", "l3")
            },
            "req_pass_rate": {
                "all": test_results_missing,
                "must": test_results_missing,
            },
            "cost": {
                "total_usd": _available(store.meta.budget_used.cost_usd),
                "by_stage": trace["by_stage"],
                "by_role": trace["by_role"],
                "by_tier": trace["by_tier"],
            },
            "cost_per_req_passed": _missing(
                "unavailable",
                "REQ_RESULTS_UNAVAILABLE",
                "No passed requirement denominator exists.",
            ),
            "escalation_rate": escalation_rate,
            "validation_repair_rate": trace["validation_repair_rate"],
            "planning": _missing(
                "unavailable",
                "PLANNING_AUDIT_UNAVAILABLE",
                "No validated aggregate planning audit is available.",
            ),
        },
        "assumptions_and_defects": assumptions,
        "reproducibility": {
            "config_snapshot_sha256": store.meta.config_snapshot_sha256,
            "plan_sha256": plan_sha,
            "delivery_blueprint_sha256": blueprint_sha,
            "git_commit": git_commit,
            "model_versions": trace["model_versions"],
        },
    }


def write_partial_report(
    store: RunStore,
    *,
    outcome: FlowOutcome,
) -> dict[str, str]:
    """构造、校验并 canonical 发布 report/report.json，返回文件引用。"""
    report = build_partial_report(store, outcome=outcome)
    _validate_report_schema(report)
    path = store.run_dir / "report" / "report.json"
    atomic_write_canonical_json(path, report)
    return {"path": "report/report.json", "sha256": _sha256_file(path)}
