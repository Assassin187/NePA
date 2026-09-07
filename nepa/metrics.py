"""Pure, deterministic M1 metric projections.

The module intentionally accepts already loaded artifact mappings.  It never
derives facts from directory ordering and never mutates its input package.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping


def available(value: Any) -> dict[str, Any]:
    return {"value": copy.deepcopy(value)}


def unavailable(code: str, detail: str | None = None) -> dict[str, Any]:
    reason: dict[str, str] = {"code": code}
    if detail:
        reason["detail"] = detail
    return {"value": None, "reason": reason}


def _rows(inputs: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    state = inputs.get("state") or inputs.get("plan_state") or {}
    rows = state.get("tasks", []) if isinstance(state, Mapping) else []
    return [row for row in rows if isinstance(row, Mapping)]


def _entries(inputs: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    ledger = inputs.get("revision_ledger") or inputs.get("ledger") or {}
    values = ledger.get("entries", []) if isinstance(ledger, Mapping) else []
    return [entry for entry in values if isinstance(entry, Mapping)]


def _ledger_present(inputs: Mapping[str, Any]) -> bool:
    return isinstance(inputs.get("revision_ledger"), Mapping) or isinstance(inputs.get("ledger"), Mapping)


def _fraction(numerator: int, denominator: int, empty_code: str = "EMPTY_TASK_SET") -> dict[str, Any]:
    return unavailable(empty_code) if denominator == 0 else available(numerator / denominator)


def _task_rates(inputs: Mapping[str, Any]) -> dict[str, Any]:
    rows = _rows(inputs)
    total = len(rows)
    if total == 0:
        empty = unavailable("EMPTY_TASK_SET")
        return {
            "task_completion_rate@final": empty,
            "blocked_rate@final": empty,
            "incomplete_rate@final": empty,
            "task_completion_rate@r0": copy.deepcopy(empty),
            "blocked_rate@r0": copy.deepcopy(empty),
            "incomplete_rate@r0": copy.deepcopy(empty),
            "ever_blocked_rate": copy.deepcopy(empty),
        }
    done = sum(row.get("status") == "done" for row in rows)
    blocked = sum(row.get("status") in {"blocked", "blocked_by_dependency"} for row in rows)
    final = {
        "task_completion_rate@final": _fraction(done, total),
        "blocked_rate@final": _fraction(blocked, total),
        "incomplete_rate@final": _fraction(total - done - blocked, total),
    }

    initial = inputs.get("initial_tasks")
    if isinstance(initial, Mapping):
        initial_ids = [str(uid) for uid in initial]
    elif isinstance(initial, list):
        initial_ids = [str(item.get("task_uid", item.get("id"))) for item in initial if isinstance(item, Mapping)]
    else:
        plan = inputs.get("initial_plan") or inputs.get("plan") or {}
        initial_ids = [str(item.get("task_uid")) for item in plan.get("tasks", []) if isinstance(item, Mapping) and item.get("task_uid")]
    by_uid = {str(row.get("task_uid")): row for row in rows if row.get("task_uid")}
    edges: dict[str, set[str]] = {uid: set() for uid in initial_ids}
    for mapping in inputs.get("lineage", []) if isinstance(inputs.get("lineage"), list) else []:
        if not isinstance(mapping, Mapping):
            continue
        old = mapping.get("old_task_uid") or mapping.get("source_uid")
        new = mapping.get("new_task_uid") or mapping.get("target_uid")
        old_uids = mapping.get("old_task_uids") or mapping.get("merged_from") or ([old] if old else [])
        if new:
            for source in old_uids:
                if source:
                    edges.setdefault(str(source), set()).add(str(new))
    for entry in _entries(inputs):
        if entry.get("event_type") != "revision_activated":
            continue
        payload = entry.get("payload", {}) if isinstance(entry.get("payload"), Mapping) else {}
        migration = payload.get("migration", {}) if isinstance(payload.get("migration"), Mapping) else {}
        for mapping in migration.get("tasks", []) if isinstance(migration.get("tasks"), list) else []:
            if not isinstance(mapping, Mapping) or not mapping.get("new_task_uid"):
                continue
            sources = list(mapping.get("merged_from", []))
            if mapping.get("old_task_uid"):
                sources.append(mapping["old_task_uid"])
            for source in sources:
                edges.setdefault(str(source), set()).add(str(mapping["new_task_uid"]))
    # A compact mapping form is convenient for fixture packages.
    if isinstance(inputs.get("lineage_map"), Mapping):
        for source, targets in inputs["lineage_map"].items():
            edges[str(source)] = {str(target) for target in targets} if isinstance(targets, list) else {str(targets)}

    def leaves(source: str) -> set[str]:
        pending = [source]
        seen: set[str] = set()
        result: set[str] = set()
        while pending:
            uid = pending.pop()
            if uid in seen:
                continue
            seen.add(uid)
            successors = edges.get(uid, set()) - {uid}
            if successors:
                pending.extend(successors)
            else:
                result.add(uid)
        return result
    r0_done = r0_blocked = 0
    for source in initial_ids:
        targets = leaves(source)
        target_rows = [by_uid[target] for target in targets if target in by_uid]
        if target_rows and all(row.get("status") == "done" for row in target_rows):
            r0_done += 1
        elif any(row.get("status") in {"blocked", "blocked_by_dependency"} for row in target_rows):
            r0_blocked += 1
    r0_total = len(initial_ids)
    final.update({
        "task_completion_rate@r0": _fraction(r0_done, r0_total),
        "blocked_rate@r0": _fraction(r0_blocked, r0_total),
        "incomplete_rate@r0": _fraction(r0_total - r0_done - r0_blocked, r0_total),
    })
    history_value = inputs.get("state_history") or inputs.get("historical_states") or []
    if isinstance(history_value, Mapping):
        history = [entry.get("state") for entry in history_value.get("entries", []) if isinstance(entry, Mapping)]
    else:
        history = history_value
    ever: set[str] = set()
    activated: set[str] = set(initial_ids)
    for snapshot in history if isinstance(history, list) else []:
        if not isinstance(snapshot, Mapping):
            continue
        for row in snapshot.get("tasks", []) if isinstance(snapshot.get("tasks"), list) else []:
            if not isinstance(row, Mapping) or not row.get("task_uid"):
                continue
            uid = str(row["task_uid"])
            activated.add(uid)
            if row.get("status") in {"blocked", "blocked_by_dependency"}:
                ever.add(uid)
    for row in rows:
        if row.get("task_uid"):
            activated.add(str(row["task_uid"]))
        if row.get("status") in {"blocked", "blocked_by_dependency"} and row.get("task_uid"):
            ever.add(str(row["task_uid"]))
    final["ever_blocked_rate"] = _fraction(len(ever), len(activated))
    return final


def _evidence(inputs: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    values = inputs.get("task_evidence") or inputs.get("evidence") or []
    return [item for item in values if isinstance(item, Mapping)] if isinstance(values, list) else []


def _first_pass(inputs: Mapping[str, Any]) -> dict[str, Any]:
    evidence = _evidence(inputs)
    calls = inputs.get("calls") or inputs.get("llm_calls") or []
    calls = [item for item in calls if isinstance(item, Mapping)] if isinstance(calls, list) else []
    regenerated = set(str(uid) for uid in inputs.get("regenerated_uids", []) if uid)
    for event in _entries(inputs):
        if event.get("event_type") != "revision_activated":
            continue
        payload = event.get("payload", {}) if isinstance(event.get("payload"), Mapping) else {}
        migration = payload.get("migration", {}) if isinstance(payload.get("migration"), Mapping) else {}
        for row in migration.get("tasks", []) if isinstance(migration.get("tasks"), list) else []:
            if isinstance(row, Mapping) and row.get("classification") == "REGENERATE" and row.get("new_task_uid"):
                regenerated.add(str(row["new_task_uid"]))
    attempts = [item for item in inputs.get("attempts", []) if isinstance(item, Mapping)] if isinstance(inputs.get("attempts"), list) else []
    by_uid: dict[str, list[Mapping[str, Any]]] = {}
    for item in attempts or (evidence + calls):
        uid = item.get("task_uid")
        if uid and str(uid) not in regenerated and item.get("execution_kind", "normal") == "normal":
            by_uid.setdefault(str(uid), []).append(item)
    denom = len(by_uid)
    passed = 0
    for uid, history in by_uid.items():
        first = min(history, key=lambda item: (int(item.get("attempt", 0)), int(item.get("evidence_seq", 0))))
        accepted = any(str(item.get("task_uid")) == uid and int(item.get("attempt", 0)) == 1 and item.get("accepted") is True for item in evidence)
        if int(first.get("attempt", 0)) == 1 and (accepted or first.get("accepted") is True or first.get("status") in {"passed", "success", "succeeded"} or first.get("commit_sha")):
            passed += 1
    # Regenerated generations are measured separately from the original
    # first-pass denominator, but still use their immutable ordinary attempts.
    regenerated_history: dict[str, list[Mapping[str, Any]]] = {}
    for item in attempts or (evidence + calls):
        uid = item.get("task_uid")
        if uid and str(uid) in regenerated and item.get("execution_kind", "normal") == "normal":
            regenerated_history.setdefault(str(uid), []).append(item)
    after = 0
    for uid in regenerated:
        history = regenerated_history.get(uid, [])
        if history:
            first = min(history, key=lambda item: (int(item.get("attempt", 0)), int(item.get("evidence_seq", 0))))
            accepted = any(str(item.get("task_uid")) == uid and int(item.get("attempt", 0)) == 1 and item.get("accepted") is True for item in evidence)
            after += int(bool(int(first.get("attempt", 0)) == 1 and (accepted or first.get("accepted") is True or first.get("status") in {"passed", "success", "succeeded"} or first.get("commit_sha"))))
    return {"first_pass_rate": _fraction(passed, denom), "first_pass_rate_after_revision": _fraction(after, len(regenerated))}


def _receipt(inputs: Mapping[str, Any], stage: str) -> Mapping[str, Any] | None:
    receipts = inputs.get("receipts")
    if isinstance(receipts, Mapping) and isinstance(receipts.get(stage), Mapping):
        return receipts[stage]
    value = inputs.get(f"{stage}_receipt") or inputs.get(stage)
    return value if isinstance(value, Mapping) else None


def _smoke_value(receipt: Mapping[str, Any] | None, inputs: Mapping[str, Any], stage: str) -> dict[str, Any]:
    if receipt is None:
        return unavailable("MISSING_RECEIPT")
    direct = receipt.get("smoke_pass")
    if direct is None:
        direct = receipt.get("smoke", {}).get("pass") if isinstance(receipt.get("smoke"), Mapping) else None
    if direct is None:
        direct = inputs.get("smoke", {}).get(stage, {}).get("pass") if isinstance(inputs.get("smoke"), Mapping) and isinstance(inputs["smoke"].get(stage), Mapping) else None
    if direct is None and isinstance(receipt.get("smoke_result_refs"), list):
        supplied = inputs.get("smoke_results")
        if isinstance(supplied, Mapping):
            values = [supplied.get(ref.get("path")) for ref in receipt["smoke_result_refs"] if isinstance(ref, Mapping)]
            values = [item for item in values if isinstance(item, Mapping)]
            if len(values) != len(receipt["smoke_result_refs"]):
                return unavailable("MISSING_SMOKE_RESULT")
            direct = bool(values) and all(item.get("status") == "passed" for item in values)
            checked = sum(len(item.get("artifacts", [])) if isinstance(item.get("artifacts"), list) else int(item.get("artifacts_checked", 0) or 0) for item in values)
        elif isinstance(supplied, list):
            values = [item for item in supplied if isinstance(item, Mapping)]
            direct = bool(values) and all(item.get("status") == "passed" for item in values)
            checked = sum(len(item.get("artifacts", [])) if isinstance(item.get("artifacts"), list) else int(item.get("artifacts_checked", 0) or 0) for item in values)
        else:
            return unavailable("MISSING_SMOKE_RESULT")
    else:
        checked = receipt.get("artifacts_checked", 0)
    if receipt.get("status") == "pending_repair" or receipt.get("smoke_status") == "pending_repair":
        direct = False
    if not isinstance(direct, bool):
        return unavailable("MISSING_SMOKE_RECEIPT")
    result: dict[str, Any] = {"pass": available(direct), "artifacts_checked": available(checked)}
    if not direct:
        result["failures"] = available(receipt.get("failures", []))
    return result


def _build_metrics(inputs: Mapping[str, Any]) -> dict[str, Any]:
    s6 = _receipt(inputs, "s6")
    s5 = _receipt(inputs, "s5")
    s6_ok = s6.get("s6_build_ok") if s6 else None
    build_ok: dict[str, Any]
    terminal = _receipt(inputs, "s7") or _receipt(inputs, "s8")
    if terminal is None:
        build_ok = unavailable("PLANNED_STOP_NO_TERMINAL_ROUND") if (inputs.get("run", {}).get("termination_kind") == "planned_stop" or s6 is not None) else unavailable("MISSING_TERMINAL_RECEIPT")
    else:
        value = terminal.get("build_ok")
        build_ok = available(value) if isinstance(value, bool) else unavailable("MISSING_TERMINAL_BUILD")
    return {"s6_build_ok": available(s6_ok) if isinstance(s6_ok, bool) else unavailable("MISSING_S6_RECEIPT"), "build_ok": build_ok, "smoke": {"s5": _smoke_value(s5, inputs, "s5"), "s6": _smoke_value(s6, inputs, "s6")}}


def _nearest_rank(values: list[int], percentile: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))]


def _revision_metrics(inputs: Mapping[str, Any]) -> dict[str, Any]:
    if not _ledger_present(inputs):
        missing = unavailable("MISSING_REVISION_LEDGER")
        return {"revision": {key: copy.deepcopy(missing) for key in ("count_by_level", "rejected_by_gate", "trigger_histogram", "migration_mix", "preservation_rate", "rework_cost_estimate_usd", "rework_cost_usd", "effectiveness", "ineffective_count")}}
    entries = _entries(inputs)
    activations = [e.get("payload", {}) for e in entries if e.get("event_type") == "revision_activated"]
    rejected: dict[str, int] = {}
    triggers: dict[str, int] = {}
    migration_mix: dict[str, int] = {name: 0 for name in ("INHERIT", "REVALIDATE", "AMEND", "REGENERATE")}
    preservation: list[float] = []
    estimate = actual = 0.0
    calls = [item for item in inputs.get("calls", []) if isinstance(item, Mapping)] if isinstance(inputs.get("calls"), list) else []
    call_by_ref = {
        str(item.get("output_path") or item.get("call_id") or item.get("id") or item.get("ref")): item
        for item in calls
        if item.get("output_path") or item.get("call_id") or item.get("id") or item.get("ref")
    }
    consumed_refs: set[str] = set()

    def referenced_cost(payload: Mapping[str, Any], fallback: float = 0.0, seen: set[str] | None = None) -> float:
        refs = payload.get("call_refs") or payload.get("rework_call_refs")
        if not isinstance(refs, list):
            return fallback
        total = 0.0
        for ref in refs:
            key = str(ref.get("call_id") or ref.get("id") or ref.get("path") or ref) if isinstance(ref, Mapping) else str(ref)
            target_seen = consumed_refs if seen is None else seen
            if key in target_seen:
                continue
            target_seen.add(key)
            call = call_by_ref.get(key)
            total += float((call or ref).get("cost_usd", 0.0) or 0.0) if isinstance((call or ref), Mapping) else 0.0
        return total
    for entry in entries:
        payload = entry.get("payload", {}) if isinstance(entry.get("payload"), Mapping) else {}
        if entry.get("event_type") == "candidate_rejected":
            gate = str(payload.get("failed_gate", "UNKNOWN")); rejected[gate] = rejected.get(gate, 0) + 1
        if entry.get("event_type") == "trigger_evaluated":
            codes = payload.get("hit_codes") if isinstance(payload.get("hit_codes"), list) else [payload.get("hit_code")]
            for code in {str(value) for value in codes if value}:
                triggers[code] = triggers.get(code, 0) + 1
    for payload in activations:
        migration = payload.get("migration", {}) if isinstance(payload.get("migration"), Mapping) else {}
        for row in migration.get("tasks", []) if isinstance(migration.get("tasks"), list) else []:
            if isinstance(row, Mapping) and row.get("classification") in migration_mix:
                migration_mix[row["classification"]] += 1
        if isinstance(payload.get("preservation_rate"), (int, float)):
            preservation.append(float(payload["preservation_rate"]))
        elif isinstance(migration.get("files"), list):
            old_rows = [row for row in migration["files"] if isinstance(row, Mapping) and row.get("old_owner_uid")]
            preserved = sum(row.get("classification") in {"INHERIT", "REVALIDATE"} for row in old_rows)
            preservation.append(1.0 if not old_rows else preserved / len(old_rows))
        estimate += float(payload.get("rework_cost_estimate_usd", 0) or 0)
        if isinstance(payload.get("rework_cost_usd"), (int, float)):
            actual += float(payload["rework_cost_usd"])
        else:
            actual += referenced_cost(payload)
    evaluations: list[Mapping[str, Any]] = []
    seen_evaluations: set[str] = set()
    for entry in entries:
        if entry.get("event_type") != "revision_evaluated":
            continue
        payload = entry.get("payload", {}) if isinstance(entry.get("payload"), Mapping) else {}
        identity = payload.get("evaluation_id") or payload.get("terminal_evaluation_id") or entry.get("event_seq")
        key = str(identity)
        if key in seen_evaluations:
            continue
        seen_evaluations.add(key)
        evaluations.append(payload)
    effect_refs: set[str] = set()
    related_cost = sum(referenced_cost(item, float(item.get("cost_usd", 0) or 0), effect_refs) for item in evaluations)
    resolved = sum(bool(item.get("resolved")) for item in evaluations)
    effectiveness = unavailable("ZERO_COST_DENOMINATOR") if related_cost == 0 else available(resolved / related_cost)
    return {"revision": {"count_by_level": available({level: sum(payload.get("level") == level for payload in activations) for level in ("F2", "F3")}), "rejected_by_gate": available(rejected), "trigger_histogram": available(triggers), "migration_mix": available(migration_mix), "preservation_rate": {"sequence": available(preservation), "mean": available(sum(preservation) / len(preservation)) if preservation else unavailable("NO_REVISIONS"), "min": available(min(preservation)) if preservation else unavailable("NO_REVISIONS")}, "rework_cost_estimate_usd": available(estimate), "rework_cost_usd": available(actual), "effectiveness": effectiveness, "ineffective_count": available(sum(bool(item.get("ineffective")) for item in evaluations))}}


def _lease_metrics(inputs: Mapping[str, Any]) -> dict[str, Any]:
    if not _ledger_present(inputs):
        missing = unavailable("MISSING_REVISION_LEDGER")
        return {"lease": {key: copy.deepcopy(missing) for key in ("count", "success_rate", "pending_count", "external_files_p50", "external_files_p95")}}
    entries = _entries(inputs)
    starts = [e.get("payload", {}) for e in entries if e.get("event_type") == "lease_started"]
    finishes = {e.get("payload", {}).get("lease_id"): e.get("payload", {}) for e in entries if e.get("event_type") == "lease_finished"}
    count = len(starts)
    successes = sum(bool(finishes.get(item.get("lease_id"), {}).get("success")) for item in starts)
    pending = sum(item.get("lease_id") not in finishes for item in starts)
    if count == 0:
        rate = unavailable("NO_LEASES")
        p50 = p95 = unavailable("NO_LEASES")
    else:
        counts = [len(item.get("leased_paths", item.get("external_paths", item.get("paths", [])))) for item in starts]
        rate = available(successes / count)
        p50 = available(_nearest_rank(counts, 0.50)); p95 = available(_nearest_rank(counts, 0.95))
    return {"lease": {"count": available(count), "success_rate": rate, "pending_count": available(pending), "external_files_p50": p50, "external_files_p95": p95}}


def compute_m1_metrics(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Compute the public M1 metric object from validated artifact mappings."""
    package = copy.deepcopy(dict(inputs))
    result = {}
    result.update(_task_rates(package))
    result.update(_first_pass(package))
    result.update(_build_metrics(package))
    result.update(_revision_metrics(package))
    result.update(_lease_metrics(package))
    return result


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_ref(root: Path, ref: Mapping[str, Any]) -> Any:
    relative = ref.get("path")
    digest = ref.get("sha256")
    if not isinstance(relative, str) or not isinstance(digest, str):
        raise ValueError("invalid artifact reference")
    path = (root / relative).resolve()
    if root.resolve() not in path.parents:
        raise ValueError("artifact reference escapes the run")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("artifact reference hash mismatch")
    return json.loads(data.decode("utf-8"))


def compute_run_metrics(run_dir: Path) -> dict[str, Any]:
    """Follow accepted run references and project metrics without directory inference."""
    root = Path(run_dir)
    package: dict[str, Any] = {}
    for key, relative in (("run", "run.json"), ("state", "plan/plan_state.json"), ("revision_ledger", "plan/revision_ledger.json")):
        path = root / relative
        if path.is_file():
            try:
                package[key] = _load_json(path)
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
    run = package.get("run", {})
    active_path = root / "plan/active_plan.json"
    if active_path.is_file():
        try:
            active = _load_json(active_path)
            if isinstance(active, Mapping):
                package["plan"] = _load_ref(root, active)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            pass
    try:
        initial_ref = run.get("stages", {}).get("s4", {}).get("output_refs", {}).get("plan")
        if isinstance(initial_ref, Mapping):
            package["initial_plan"] = _load_ref(root, initial_ref)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        pass

    receipts: dict[str, Any] = {}
    s6_path = root / "plan/s6_receipt.json"
    if s6_path.is_file():
        try:
            receipts["s6"] = _load_json(s6_path)
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    for stage in ("s5", "s7", "s8"):
        refs = run.get("stages", {}).get(stage, {}).get("output_refs", {}) if isinstance(run, Mapping) else {}
        receipt_ref = refs.get(f"{stage}_receipt") if isinstance(refs, Mapping) else None
        if isinstance(receipt_ref, Mapping):
            try:
                receipts[stage] = _load_ref(root, receipt_ref)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                pass
    package["receipts"] = receipts

    s6 = receipts.get("s6", {})
    if isinstance(s6, Mapping) and isinstance(s6.get("state_history_ref"), Mapping):
        try:
            package["state_history"] = _load_ref(root, s6["state_history_ref"])
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            pass

    smoke_results: dict[str, Any] = {}
    for receipt in receipts.values():
        if not isinstance(receipt, Mapping):
            continue
        for ref in receipt.get("smoke_result_refs", []) if isinstance(receipt.get("smoke_result_refs"), list) else []:
            if not isinstance(ref, Mapping) or not isinstance(ref.get("path"), str):
                continue
            try:
                smoke_results[ref["path"]] = _load_ref(root, ref)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                continue
    package["smoke_results"] = smoke_results

    evidence_refs: dict[str, Mapping[str, Any]] = {}
    state = package.get("state", {})
    for row in state.get("tasks", []) if isinstance(state, Mapping) else []:
        ref = row.get("acceptance_evidence", {}).get("task_evidence_ref") if isinstance(row, Mapping) else None
        if isinstance(ref, Mapping) and isinstance(ref.get("path"), str):
            evidence_refs[ref["path"]] = ref
    for entry in _entries(package):
        if entry.get("event_type") == "verification_committed":
            for ref in entry.get("payload", {}).get("evidence_refs", []):
                if isinstance(ref, Mapping) and isinstance(ref.get("path"), str):
                    evidence_refs[ref["path"]] = ref
    evidence: list[dict[str, Any]] = []
    for ref in evidence_refs.values():
        try:
            value = _load_ref(root, ref)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            continue
        if isinstance(value, dict):
            evidence.append(value)
    package["task_evidence"] = evidence

    attempts: list[dict[str, Any]] = []
    for row in state.get("tasks", []) if isinstance(state, Mapping) else []:
        if not isinstance(row, Mapping):
            continue
        for attempt in range(1, int(row.get("attempts", 0)) + 1):
            path = root / f"attempts/{row.get('task_uid')}/attempt_{attempt:03d}.json"
            try:
                value = _load_json(path)
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                attempts.append(value)
    package["attempts"] = attempts

    trace = root / "trace" / "llm_calls.ndjson"
    if trace.is_file():
        calls: list[dict[str, Any]] = []
        try:
            for line in trace.read_text(encoding="utf-8").splitlines():
                value = json.loads(line)
                if isinstance(value, dict):
                    calls.append(value)
        except (OSError, UnicodeError, json.JSONDecodeError):
            calls = []
        package["calls"] = calls
    return compute_m1_metrics(package)


__all__ = ["available", "compute_m1_metrics", "compute_run_metrics", "unavailable"]
