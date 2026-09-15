"""Read-only, reproducible summaries of durable NePA run evidence."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import re
from typing import Any

from .llm.client import LLMResponse, decode_action
from .schemas import load_schema


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_bytes())


def _execution_seconds(value: Any) -> float:
    if not isinstance(value, dict):
        return 0.0
    total = 0.0
    execution = value.get("execution")
    if isinstance(execution, dict):
        total += float(execution.get("duration_ms", 0)) / 1000
    for key in ("builds", "variants"):
        for row in value.get(key, []) if isinstance(value.get(key), list) else []:
            total += _execution_seconds(row)
    return total


def _decision_key(request: dict[str, Any]) -> tuple[Any, ...] | None:
    context = request.get("context") or {}
    if all(key in context for key in ("session", "decision")):
        return request.get("task_id"), context["session"], context["decision"]
    messages = request.get("wire", {}).get("messages", [])
    if not messages:
        return None
    content = messages[-1].get("content")
    if not isinstance(content, str):
        return None
    match = re.search(r"Decision budget:(\{.*\})\s*$", content, re.DOTALL)
    if not match:
        return None
    try:
        progress = json.loads(match.group(1))
        return request.get("task_id"), progress.get("session"), progress.get("decisions_left")
    except (TypeError, ValueError):
        return None


def _action_failed(result: dict[str, Any]) -> bool:
    if result.get("error") or result.get("timed_out") is True or result.get("returncode", 0) not in (0, None):
        return True
    if isinstance(result.get("build"), dict) and result["build"].get("passed") is False:
        return True
    if isinstance(result.get("verification"), dict) and result["verification"].get("passed") is False:
        return True
    return result.get("accepted") is False


def summarize_run(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    state = _json(root / "run.json")
    plan = _json(root / state["active_plan"]["path"])
    task_kinds = {task["id"]: task["kind"] for task in plan["tasks"]}
    action_format = state["config_snapshot"]["coder"]["action_format"]
    schema = load_schema("agent-action.schema.json")

    requests = {path.name.split(".")[0]: _json(path)
                for path in (root / "evidence/calls").glob("*.request.json")}
    task_calls: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "calls": 0, "api_elapsed_s": 0.0, "rejected_actions": 0,
        "rejected_api_elapsed_s": 0.0, "first_started_at": None, "last_finished_at": None,
    })
    models: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "calls": 0, "api_elapsed_s": 0.0, "tokens_in": 0, "tokens_out": 0,
        "cost_cny": 0.0, "rejected_actions": 0,
    })
    error_categories: Counter[str] = Counter()
    error_details: Counter[str] = Counter()
    route_reasons: Counter[str] = Counter()
    decision_keys: set[tuple[Any, ...]] = set()
    responses = rejected = undecodable = 0
    api_elapsed_s = rejected_elapsed_s = 0.0
    cache_hits = cache_misses = 0

    for sequence, request in requests.items():
        key = _decision_key(request)
        if key is not None:
            decision_keys.add(key)
        reason = (request.get("context") or {}).get("route", {}).get("reason")
        if reason:
            route_reasons[reason] += 1
        path = root / "evidence/calls" / f"{sequence}.response.json"
        if not path.is_file():
            continue
        envelope = _json(path)
        elapsed = float(envelope.get("elapsed_s", 0))
        started_at = float(request.get("started_at", 0))
        task_id = request.get("task_id", "unknown")
        task = task_calls[task_id]
        task["calls"] += 1
        task["api_elapsed_s"] += elapsed
        task["first_started_at"] = started_at if task["first_started_at"] is None else min(task["first_started_at"], started_at)
        finished_at = started_at + elapsed
        task["last_finished_at"] = finished_at if task["last_finished_at"] is None else max(task["last_finished_at"], finished_at)
        responses += 1
        api_elapsed_s += elapsed
        try:
            response = LLMResponse.model_validate(envelope["response"])
        except (KeyError, ValueError, TypeError):
            undecodable += 1
            continue
        model = models[response.model]
        model["calls"] += 1
        model["api_elapsed_s"] += elapsed
        model["tokens_in"] += response.tokens_in
        model["tokens_out"] += response.tokens_out
        model["cost_cny"] += response.cost_cny
        pricing = response.pricing or {}
        cache_hits += int(pricing.get("cache_hit_tokens", 0))
        cache_misses += int(pricing.get("cache_miss_tokens", 0))
        _, errors = decode_action(response, action_format, schema)
        if errors:
            rejected += 1
            rejected_elapsed_s += elapsed
            task["rejected_actions"] += 1
            task["rejected_api_elapsed_s"] += elapsed
            model["rejected_actions"] += 1
            error_categories[errors[0].get("code", "unknown")] += 1
            if errors[0].get("detail"):
                error_details[errors[0]["detail"]] += 1

    action_counts: Counter[str] = Counter()
    action_failures: Counter[str] = Counter()
    command_seconds = action_execution_seconds = task_build_seconds = integration_verification_seconds = 0.0
    for path in (root / "evidence/actions").glob("*.json"):
        envelope = _json(path)
        pending = envelope.get("action") or {}
        action = pending.get("action") or {}
        tool = action.get("tool", "unknown")
        action_counts[tool] += 1
        result = envelope.get("result") or {}
        action_execution_seconds += float(envelope.get("execution_elapsed_s", 0))
        if _action_failed(result):
            action_failures[tool] += 1
        if tool == "run_command":
            command_seconds += float(result.get("duration_ms", 0)) / 1000
        if isinstance(result.get("build"), dict):
            task_build_seconds += _execution_seconds(result["build"])
        if pending.get("task_id") == "final-integration" and isinstance(result.get("verification"), dict):
            integration_verification_seconds += _execution_seconds(result["verification"])

    final = (state.get("final_checks") or {}).get("result") or {}
    final_build_seconds = _execution_seconds(final.get("build"))
    final_verification_seconds = _execution_seconds(final.get("verification"))
    error_attempts = list((root / "evidence/calls").glob("*.error.json"))
    failed_api_elapsed_s = sum(float(_json(path).get("elapsed_s", 0)) for path in error_attempts)
    performance_events = [_json(path) for path in (root / "evidence/performance").rglob("*.json")]
    task_invocation_elapsed: dict[str, float] = defaultdict(float)
    measured_session_ends = 0
    for event in performance_events:
        if event.get("event") == "task_invocation_finished":
            task_invocation_elapsed[event["task_id"]] += float(event.get("elapsed_s", 0))
        elif event.get("event") == "session_finished":
            measured_session_ends += 1
    check_times: dict[str, dict[str, Any]] = defaultdict(lambda: {"executions": 0, "elapsed_s": 0.0})
    verification_values = []
    for path in (root / "evidence/actions").glob("*.json"):
        result = _json(path).get("result") or {}
        if isinstance(result.get("verification"), dict):
            verification_values.append(result["verification"])
    if isinstance(final.get("verification"), dict):
        verification_values.append(final["verification"])
    for verification in verification_values:
        for variant in verification.get("variants", []):
            for check in variant.get("detail", {}).get("checks", []):
                if "elapsed_s" in check:
                    row = check_times[check["id"]]
                    row["executions"] += 1
                    row["elapsed_s"] += float(check["elapsed_s"])
    report_path = root / "report.json"
    wall_end = report_path.stat().st_mtime if report_path.is_file() else state.get("updated_at")
    tasks = []
    for task_id, task_state in state["tasks"].items():
        row = task_calls[task_id]
        span = None
        if row["first_started_at"] is not None and row["last_finished_at"] is not None:
            span = row["last_finished_at"] - row["first_started_at"]
        tasks.append({"id": task_id, "kind": task_kinds.get(task_id), "status": task_state["status"],
                      "sessions": task_state["sessions"], "decisions": task_state["decisions"],
                      "calls": row["calls"], "api_elapsed_s": row["api_elapsed_s"],
                      "rejected_actions": row["rejected_actions"],
                      "rejected_api_elapsed_s": row["rejected_api_elapsed_s"], "call_span_s": span,
                      "measured_invocation_elapsed_s": task_invocation_elapsed.get(task_id)})

    total_cache = cache_hits + cache_misses
    return {
        "schema_version": "1.0", "run_id": state["run_id"], "status": state["status"],
        "wall_time": {"seconds": wall_end - state["created_at"] if wall_end is not None else None,
                      "basis": "run.created_at_to_report_mtime" if report_path.is_file() else "run.created_at_to_updated_at"},
        "llm": {"provider_attempts": len(requests), "completed_responses": responses,
                "failed_attempts": len(error_attempts), "failed_attempt_api_elapsed_s": failed_api_elapsed_s,
                "logical_decisions_observed": len(decision_keys), "api_elapsed_s": api_elapsed_s,
                "context_assembly_elapsed_s": sum(float((request.get("context") or {}).get("context_assembly_elapsed_s", 0))
                                                    for request in requests.values()),
                "rejected_actions": rejected,
                "rejected_rate": rejected / responses if responses else None,
                "rejected_api_elapsed_s": rejected_elapsed_s, "undecodable_responses": undecodable,
                "error_categories": dict(sorted(error_categories.items())),
                "error_details": dict(sorted(error_details.items())),
                "route_reasons": dict(sorted(route_reasons.items())), "models": dict(sorted(models.items())),
                "cache_hit_tokens": cache_hits, "cache_miss_tokens": cache_misses,
                "cache_hit_rate": cache_hits / total_cache if total_cache else None},
        "sessions": {"total": sum(task["sessions"] for task in state["tasks"].values()),
                     "continuations": sum(max(0, task["sessions"] - 1) for task in state["tasks"].values()),
                     "measured_finished_sessions": measured_session_ends},
        "actions": {"total": sum(action_counts.values()), "by_tool": dict(sorted(action_counts.items())),
                    "failed_total": sum(action_failures.values()), "failures_by_tool": dict(sorted(action_failures.items())),
                    "measured_execution_elapsed_s": action_execution_seconds,
                    "run_command_elapsed_s": command_seconds},
        "phases": {"task_build_elapsed_s": task_build_seconds,
                   "integration_verification_elapsed_s": integration_verification_seconds,
                   "final_export_build_elapsed_s": final_build_seconds,
                   "final_export_verification_elapsed_s": final_verification_seconds,
                   "checks": dict(sorted(check_times.items()))},
        "accounting": {"budget": state["budget"], "unknown_calls": state.get("pending_calls", {}),
                       "unknown_reserved_cny": sum(float(value.get("reserved", 0))
                                                   for value in state.get("pending_calls", {}).values())},
        "outcomes": {"tasks_passed": sum(task["status"] == "passed" for task in state["tasks"].values()),
                     "tasks_total": len(state["tasks"]),
                     "primary_claims": sum(len(task["claims"]) for task in state["tasks"].values()),
                     "final_build_passed": (final.get("build") or {}).get("passed"),
                     "final_acceptance_passed": (final.get("verification") or {}).get("passed"),
                     "delivery_published": bool(state.get("delivery"))},
        "tasks": tasks,
        "limitations": [
            "Historical logical-decision counts are inferred from request progress metadata when available.",
            "Wall time uses report filesystem modification time because older runs did not persist a publication timestamp.",
            "Completed API elapsed time and wall spans overlap and must not be added together.",
        ],
    }
