"""Side-effect-free Plan State snapshot, transition, and execution lint."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from ..schemas import load_schema
from .lint import canonical_json_bytes


class PlanStateError(ValueError):
    """A Plan State or typed event is malformed."""


def _read(value: Any, label: str) -> Any:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        return json.loads(Path(value).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlanStateError(f"unable to read {label}: {exc}") from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _schema_report(value: Any, name: str, prefix: str = "/") -> list[dict[str, str]]:
    errors = list(Draft202012Validator(load_schema(name)).iter_errors(value))
    result = []
    for error in sorted(errors, key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message)):
        path = prefix + "/".join(str(part) for part in error.absolute_path)
        result.append({"code": "STATE_SCHEMA_INVALID", "path": path.rstrip("/") or "/", "message": error.message})
    return result


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _report(errors: list[dict[str, str]], warnings: list[dict[str, str]] | None = None) -> dict[str, Any]:
    errors = sorted(errors, key=lambda item: (item["code"], item["path"], item["message"]))
    return {"valid": not errors, "errors": errors, "warnings": warnings or []}


def _total_attempt_limit(config_snapshot: Mapping[str, Any] | None) -> int:
    if config_snapshot is not None and not isinstance(config_snapshot, Mapping):
        raise PlanStateError("config snapshot must be an object")
    budgets = (config_snapshot or {}).get("budgets", {})
    value = budgets.get("task_fix_attempts", 3) if isinstance(budgets, Mapping) else 3
    if not isinstance(value, int) or value < 0:
        raise PlanStateError("task_fix_attempts must be a non-negative integer")
    return value + 1


def _plan_task_ids(plan: Mapping[str, Any]) -> list[str]:
    return [task["id"] for task in plan.get("tasks", []) if isinstance(task, Mapping) and isinstance(task.get("id"), str)]


def _state_task_ids(state: Mapping[str, Any]) -> list[str]:
    return [task["id"] for task in state.get("tasks", []) if isinstance(task, Mapping) and isinstance(task.get("id"), str)]


def initialize_plan_state(
    plan: Mapping[str, Any] | str | Path,
    *,
    plan_ref: Mapping[str, Any] | None = None,
    s4_seal: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the unique all-pending initial snapshot without persisting it."""

    plan_value = _read(plan, "Plan")
    if _schema_report(plan_value, "plan.schema.json"):
        raise PlanStateError("Plan failed Schema validation")
    if plan_ref is None:
        seal_ref = None
        if isinstance(s4_seal, Mapping):
            seal_ref = s4_seal.get("plan")
            output_refs = s4_seal.get("output_refs")
            if seal_ref is None and isinstance(output_refs, Mapping):
                seal_ref = output_refs.get("plan")
        plan_ref = seal_ref if isinstance(seal_ref, Mapping) else {"path": "<memory>/plan.json", "sha256": _sha(plan_value)}
    ref = copy.deepcopy(dict(plan_ref))
    ref.setdefault("revision_seq", 0)
    if ref.get("sha256") != _sha(plan_value):
        raise PlanStateError("Plan State plan_ref does not match the supplied Plan")
    state = {
        "schema_version": "1.0",
        "plan_ref": ref,
        "tasks": [
            {"id": task_id, "status": "pending", "attempts": 0, "notes": "", "commit_sha": None, "last_error": None, "acceptance_evidence": {"task_evidence_ref": None}}
            for task_id in _plan_task_ids(plan_value)
        ],
    }
    errors = _schema_report(state, "plan-state.schema.json")
    if errors:
        raise PlanStateError("initial Plan State failed Schema validation")
    return state


def plan_state_snapshot_lint(
    plan: Mapping[str, Any] | str | Path,
    state: Mapping[str, Any] | str | Path,
    s4_seal: Mapping[str, Any] | None = None,
    config_snapshot: Mapping[str, Any] | str | Path | None = None,
    revision_ledger: Mapping[str, Any] | str | Path | None = None,
) -> dict[str, Any]:
    """Validate only JSON snapshot facts; never inspect or mutate external state."""

    plan_value = _read(plan, "Plan")
    state_value = _read(state, "Plan State")
    config_value = _read(config_snapshot, "config snapshot") if config_snapshot is not None else {}
    errors = _schema_report(plan_value, "plan.schema.json")
    errors.extend(_schema_report(state_value, "plan-state.schema.json"))
    if errors:
        return _report(errors)
    plan_ids = _plan_task_ids(plan_value)
    state_ids = _state_task_ids(state_value)
    if len(plan_ids) != len(set(plan_ids)):
        errors.append(_issue("STATE_PLAN_TASK_DUPLICATE", "/tasks", "Plan task ids must be unique"))
    if len(state_ids) != len(set(state_ids)) or set(state_ids) != set(plan_ids):
        errors.append(_issue("STATE_TASK_SET_MISMATCH", "/tasks", "Plan State task ids must equal the Plan task ids exactly"))
    expected_ref = None
    expected_config_sha = None
    if isinstance(s4_seal, Mapping):
        expected_ref = s4_seal.get("plan")
        expected_config_sha = s4_seal.get("config_snapshot_sha256")
        output_refs = s4_seal.get("output_refs")
        if isinstance(output_refs, Mapping):
            expected_ref = expected_ref or output_refs.get("plan")
            expected_config_sha = expected_config_sha or output_refs.get("config_snapshot_sha256")
    if isinstance(expected_ref, Mapping):
        actual_ref = state_value.get("plan_ref", {})
        if actual_ref.get("path") != expected_ref.get("path") or actual_ref.get("sha256") != expected_ref.get("sha256") or ("revision_seq" in expected_ref and actual_ref.get("revision_seq") != expected_ref.get("revision_seq")):
            errors.append(_issue("STATE_PLAN_REF_INVALID", "/plan_ref", "Plan State plan_ref does not match the S4 seal"))
    actual_plan_sha = _sha(plan_value)
    if state_value.get("plan_ref", {}).get("sha256") != actual_plan_sha:
        errors.append(_issue("STATE_PLAN_REF_INVALID", "/plan_ref/sha256", "Plan State plan_ref does not match the supplied Plan"))
    if expected_config_sha is not None:
        if not isinstance(config_value, Mapping):
            errors.append(_issue("STATE_CONFIG_INVALID", "/config_snapshot", "config snapshot must be an object"))
        elif _sha(config_value) != expected_config_sha:
            errors.append(_issue("STATE_CONFIG_REF_INVALID", "/config_snapshot", "config snapshot does not match the S4 seal"))
    try:
        total_limit = _total_attempt_limit(config_value)
    except PlanStateError as exc:
        errors.append(_issue("STATE_CONFIG_INVALID", "/config_snapshot", str(exc)))
        total_limit = 4
    ledger_value = _read(revision_ledger, "revision ledger") if revision_ledger is not None else None
    for index, task in enumerate(state_value.get("tasks", [])):
        base = f"/tasks/{index}"
        status = task.get("status")
        attempts = task.get("attempts")
        evidence = task.get("acceptance_evidence", {}).get("task_evidence_ref")
        commit = task.get("commit_sha")
        error = task.get("last_error")
        if status == "pending":
            reopened = _pending_revision_proof(task, ledger_value)
            if attempts != 0 and not reopened:
                errors.append(_issue("STATE_PENDING_ATTEMPTS_INVALID", f"{base}/attempts", "pending tasks may have nonzero attempts only after a proven revision reopen"))
            if reopened and not (0 <= attempts < total_limit):
                errors.append(_issue("STATE_ATTEMPTS_INVALID", f"{base}/attempts", "reopened pending attempts must remain below the total attempt limit"))
            if attempts == 0 and not reopened and task.get("notes") != "":
                errors.append(_issue("STATE_PENDING_FIELDS_INVALID", base, "initial pending tasks must have empty notes"))
            if commit is not None or evidence is not None or error is not None:
                errors.append(_issue("STATE_PENDING_FIELDS_INVALID", base, "pending tasks cannot carry commit, evidence, or error"))
        elif status == "in_progress":
            if not 1 <= attempts <= total_limit:
                errors.append(_issue("STATE_ATTEMPTS_INVALID", f"{base}/attempts", "in_progress attempts exceed the configured total limit"))
            if commit is not None or evidence is not None or error is not None:
                errors.append(_issue("STATE_IN_PROGRESS_FIELDS_INVALID", base, "in_progress tasks cannot carry commit, evidence, or error"))
        elif status == "done":
            if not 1 <= attempts <= total_limit:
                errors.append(_issue("STATE_ATTEMPTS_INVALID", f"{base}/attempts", "done attempts exceed the configured total limit"))
            if not isinstance(commit, str) or len(commit) != 40:
                errors.append(_issue("STATE_DONE_COMMIT_MISSING", f"{base}/commit_sha", "done tasks require a commit sha"))
            if not isinstance(evidence, Mapping):
                errors.append(_issue("STATE_DONE_EVIDENCE_MISSING", f"{base}/acceptance_evidence/task_evidence_ref", "done tasks require task evidence"))
            if error is not None:
                errors.append(_issue("STATE_DONE_ERROR_INVALID", f"{base}/last_error", "done tasks cannot carry last_error"))
        elif status == "blocked":
            if attempts != total_limit:
                errors.append(_issue("STATE_BLOCKED_ATTEMPTS_INVALID", f"{base}/attempts", "blocked tasks must have exhausted all attempts"))
            if not isinstance(error, str) or not error:
                errors.append(_issue("STATE_BLOCKED_ERROR_MISSING", f"{base}/last_error", "blocked tasks require a final error"))
            if commit is not None or evidence is not None:
                errors.append(_issue("STATE_BLOCKED_FIELDS_INVALID", base, "blocked tasks cannot carry commit or evidence"))
        elif status == "blocked_by_dependency":
            if attempts != 0 or task.get("notes") != "":
                errors.append(_issue("STATE_DEPENDENCY_BLOCK_FIELDS_INVALID", base, "dependency-blocked tasks have zero attempts and empty notes"))
            if not isinstance(error, str) or not error:
                errors.append(_issue("STATE_DEPENDENCY_BLOCK_ERROR_MISSING", f"{base}/last_error", "dependency-blocked tasks require an error"))
            if commit is not None or evidence is not None:
                errors.append(_issue("STATE_DEPENDENCY_BLOCK_FIELDS_INVALID", base, "dependency-blocked tasks cannot carry commit or evidence"))
    return _report(errors)


def _pending_revision_proof(task: Mapping[str, Any], ledger: Any) -> bool:
    if not isinstance(ledger, Mapping):
        return False
    task_id = task.get("id")
    notes = task.get("notes", "")
    return isinstance(notes, str) and "revision_seq" in notes and any(
        isinstance(item, Mapping) and item.get("event") == "reopened_by_revision" and item.get("task_id") == task_id
        for item in ledger.get("entries", [])
    )


def validate_state_transition(
    old_state: Mapping[str, Any] | str | Path,
    new_state: Mapping[str, Any] | str | Path,
    event: Mapping[str, Any] | str,
    *,
    plan: Mapping[str, Any] | str | Path | None = None,
    config_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive the only legal next task row and compare it with ``new_state``."""

    old = _read(old_state, "old Plan State")
    new = _read(new_state, "new Plan State")
    if isinstance(event, str):
        event = {"schema_version": "1.0", "event": event}
    else:
        event = dict(event)
    event.setdefault("schema_version", "1.0")
    errors = _schema_report(old, "plan-state.schema.json") + _schema_report(new, "plan-state.schema.json") + _schema_report(event, "plan-event.schema.json")
    if errors:
        return {"valid": False, "errors": errors, "warnings": [], "state": None}
    old_by_id = {task["id"]: task for task in old["tasks"]}
    new_by_id = {task["id"]: task for task in new["tasks"]}
    task_id = event["task_id"]
    if task_id not in old_by_id or set(old_by_id) != set(new_by_id):
        errors.append(_issue("STATE_TASK_SET_MISMATCH", "/tasks", "transition must preserve the complete task set"))
        return {"valid": False, "errors": sorted(errors, key=lambda item: (item["code"], item["path"], item["message"])), "warnings": [], "state": None}
    current = old_by_id[task_id]
    derived = copy.deepcopy(current)
    name = event["event"]
    try:
        total_limit = _total_attempt_limit(config_snapshot)
    except PlanStateError as exc:
        errors.append(_issue("STATE_CONFIG_INVALID", "/config_snapshot", str(exc)))
        return {"valid": False, "errors": sorted(errors, key=lambda item: (item["code"], item["path"], item["message"])), "warnings": [], "state": None}
    if name == "attempt_started":
        if current["status"] not in {"pending", "in_progress"} or current["attempts"] >= total_limit:
            errors.append(_issue("STATE_TRANSITION_INVALID", f"/tasks/{task_id}", "attempt_started is not legal from the current state"))
        elif current["status"] == "in_progress" and not event.get("previous_error"):
            errors.append(_issue("STATE_TRANSITION_INVALID", f"/tasks/{task_id}", "a retry attempt must carry the previous error"))
        else:
            derived.update({"status": "in_progress", "attempts": current["attempts"] + 1, "last_error": None, "commit_sha": None, "acceptance_evidence": {"task_evidence_ref": None}})
            if "notes" in event:
                derived["notes"] = event["notes"]
    elif name in {"attempt_succeeded", "reconciled_commit"}:
        if current["status"] != "in_progress" or not isinstance(event.get("commit_sha"), str) or not isinstance(event.get("evidence_ref"), Mapping) or (name == "reconciled_commit" and not isinstance(event.get("proof"), Mapping)):
            errors.append(_issue("STATE_TRANSITION_INVALID", f"/tasks/{task_id}", f"{name} requires an in-progress task and typed commit/evidence"))
        else:
            derived.update({"status": "done", "commit_sha": event["commit_sha"], "last_error": None, "acceptance_evidence": {"task_evidence_ref": copy.deepcopy(event["evidence_ref"])}})
            if "notes" in event:
                derived["notes"] = event["notes"]
    elif name == "attempts_exhausted":
        if current["status"] != "in_progress" or current["attempts"] != total_limit or not event.get("error"):
            errors.append(_issue("STATE_TRANSITION_INVALID", f"/tasks/{task_id}", "attempts_exhausted requires the total attempt limit and a final error"))
        else:
            derived.update({"status": "blocked", "last_error": event["error"], "commit_sha": None, "acceptance_evidence": {"task_evidence_ref": None}})
    elif name == "dependency_blocked":
        task_plan = _task_from_plan(plan, task_id)
        if current["status"] != "pending" or task_plan is None:
            errors.append(_issue("STATE_TRANSITION_INVALID", f"/tasks/{task_id}", "dependency_blocked requires a pending task in a Plan"))
        else:
            states = {task["id"]: task["status"] for task in old["tasks"]}
            dependencies = task_plan.get("depends_on", [])
            if not any(states.get(dependency) in {"blocked", "blocked_by_dependency"} for dependency in dependencies):
                errors.append(_issue("STATE_DEPENDENCY_PROOF_INVALID", f"/tasks/{task_id}", "no Plan dependency is currently blocked"))
            else:
                derived.update({"status": "blocked_by_dependency", "last_error": event.get("error", "dependency blocked"), "notes": "", "commit_sha": None, "acceptance_evidence": {"task_evidence_ref": None}})
    elif name in {"revalidation_passed", "amended_under_lease"}:
        expected_controller = "revision" if name == "revalidation_passed" else "lease"
        expected_classification = "REVALIDATE" if name == "revalidation_passed" else None
        if current["status"] != "done" or event.get("controller") != expected_controller or (expected_classification and event.get("classification") != expected_classification) or not isinstance(event.get("commit_sha"), str) or not isinstance(event.get("evidence_ref"), Mapping) or not isinstance(event.get("proof"), Mapping):
            errors.append(_issue("STATE_CONTROLLER_PROOF_INVALID", f"/tasks/{task_id}", f"{name} requires its designated controller proof"))
        else:
            derived.update({"status": "done", "commit_sha": event["commit_sha"], "last_error": None, "acceptance_evidence": {"task_evidence_ref": copy.deepcopy(event["evidence_ref"])}})
    elif name == "reopened_by_revision":
        if current["status"] not in {"done", "blocked"} or event.get("controller") != "revision" or event.get("classification") not in {"AMEND", "REGENERATE"} or not isinstance(event.get("revision_seq"), int) or event["revision_seq"] < 1 or not isinstance(event.get("proof"), Mapping):
            errors.append(_issue("STATE_CONTROLLER_PROOF_INVALID", f"/tasks/{task_id}", "reopened_by_revision requires revision-controller proof"))
        elif current["status"] == "blocked" and event["classification"] == "AMEND":
            errors.append(_issue("STATE_ATTEMPTS_INVALID", f"/tasks/{task_id}", "an exhausted task cannot reopen with preserved attempts"))
        else:
            attempts = current["attempts"] if event["classification"] == "AMEND" else 0
            derived.update({"status": "pending", "attempts": attempts, "notes": f"revision_seq={event['revision_seq']} classification={event['classification']}", "commit_sha": None, "last_error": None, "acceptance_evidence": {"task_evidence_ref": None}})
    else:
        errors.append(_issue("STATE_EVENT_INVALID", "/event", "event is not in the closed transition table"))
    if not errors:
        candidate = copy.deepcopy(old)
        for index, task in enumerate(candidate["tasks"]):
            if task["id"] == task_id:
                candidate["tasks"][index] = derived
        if candidate != new:
            errors.append(_issue("STATE_DERIVED_MISMATCH", f"/tasks/{task_id}", "new state is not the unique state derived from the old state and event"))
        result_state = candidate
    else:
        result_state = None
    return {"valid": not errors, "errors": sorted(errors, key=lambda item: (item["code"], item["path"], item["message"])), "warnings": [], "state": result_state}


def _task_from_plan(plan: Any, task_id: str) -> Mapping[str, Any] | None:
    if plan is None:
        return None
    value = _read(plan, "Plan")
    return next((task for task in value.get("tasks", []) if task.get("id") == task_id), None)


def execution_state_lint(
    plan: Mapping[str, Any] | str | Path,
    state: Mapping[str, Any] | str | Path,
    workspace: Mapping[str, Any] | str | Path,
    evidence_store: Mapping[str, Any] | str | Path,
    stage_receipts: Mapping[str, Any] | str | Path,
    *,
    config_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify external commit/evidence facts using read-only supplied adapters."""

    plan_value = _read(plan, "Plan")
    state_value = _read(state, "Plan State")
    receipts = _read(stage_receipts, "stage receipts")
    receipts_s4 = receipts.get("s4") if isinstance(receipts, Mapping) else None
    errors = plan_state_snapshot_lint(plan_value, state_value, s4_seal=receipts_s4 if isinstance(receipts_s4, Mapping) else None, config_snapshot=config_snapshot).get("errors", [])
    task_by_id = {task["id"]: task for task in plan_value.get("tasks", [])}
    workspace_anchor = _stage_workspace_anchor(receipts)
    for state_task in state_value.get("tasks", []):
        if state_task.get("status") != "done":
            continue
        task_id = state_task["id"]
        task = task_by_id.get(task_id, {})
        commit = state_task.get("commit_sha")
        evidence_ref = state_task.get("acceptance_evidence", {}).get("task_evidence_ref")
        commit_info = _commit_info(workspace, commit)
        if commit_info is None:
            errors.append(_issue("EXEC_COMMIT_MISSING", f"/tasks/{task_id}/commit_sha", "done task commit does not exist in the supplied workspace"))
        else:
            trailers = commit_info.get("trailers", {})
            expected = {"NePA-Task": task_id, "NePA-Attempt": str(state_task.get("attempts"))}
            for key, value in expected.items():
                if trailers.get(key) != value:
                    errors.append(_issue("EXEC_COMMIT_TRAILER_INVALID", f"/tasks/{task_id}/commit_sha", f"commit trailer {key} does not match Plan State"))
            if "NePA-Evidence-SHA256" not in trailers:
                errors.append(_issue("EXEC_COMMIT_TRAILER_INVALID", f"/tasks/{task_id}/commit_sha", "commit lacks NePA-Evidence-SHA256 trailer"))
            if workspace_anchor is None:
                errors.append(_issue("EXEC_STAGE_ANCHOR_MISSING", "/stage_receipts/s5", "S5 workspace commit anchor is missing"))
            elif not _commit_ancestor(workspace, commit, workspace_anchor):
                errors.append(_issue("EXEC_COMMIT_ANCESTRY_INVALID", f"/tasks/{task_id}/commit_sha", "task commit is not descended from the Plan anchor"))
        evidence_bytes = _evidence_bytes(evidence_store, evidence_ref)
        if evidence_bytes is None:
            errors.append(_issue("EXEC_EVIDENCE_MISSING", f"/tasks/{task_id}/acceptance_evidence/task_evidence_ref", "task evidence is missing"))
        else:
            actual_hash = hashlib.sha256(evidence_bytes).hexdigest()
            if not isinstance(evidence_ref, Mapping) or actual_hash != evidence_ref.get("sha256"):
                errors.append(_issue("EXEC_EVIDENCE_HASH_INVALID", f"/tasks/{task_id}/acceptance_evidence/task_evidence_ref", "task evidence content hash does not match its reference"))
            try:
                evidence = json.loads(evidence_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError):
                evidence = None
            if not isinstance(evidence, Mapping) or evidence.get("task_id") != task_id or evidence.get("attempt") != state_task.get("attempts"):
                errors.append(_issue("EXEC_EVIDENCE_IDENTITY_INVALID", f"/tasks/{task_id}/acceptance_evidence/task_evidence_ref", "task evidence task/attempt identity does not match"))
            if isinstance(evidence, Mapping):
                if evidence.get("plan_sha256") != _sha(plan_value):
                    errors.append(_issue("EXEC_EVIDENCE_PLAN_INVALID", f"/tasks/{task_id}/acceptance_evidence/task_evidence_ref", "task evidence does not bind the supplied Plan"))
                if not isinstance(evidence.get("build_result_refs"), list) or not evidence["build_result_refs"]:
                    errors.append(_issue("EXEC_EVIDENCE_BUILD_MISSING", f"/tasks/{task_id}/acceptance_evidence/task_evidence_ref", "task evidence must contain at least one build result reference"))
            if isinstance(commit_info, Mapping) and isinstance(commit_info.get("trailers"), Mapping) and commit_info["trailers"].get("NePA-Evidence-SHA256") != actual_hash:
                errors.append(_issue("EXEC_EVIDENCE_COMMIT_BINDING_INVALID", f"/tasks/{task_id}/commit_sha", "commit evidence trailer does not match evidence bytes"))
            if isinstance(evidence, Mapping) and not _evidence_accepts_task(evidence, task):
                errors.append(_issue("EXEC_ACCEPTANCE_INVALID", f"/tasks/{task_id}/acceptance_evidence", "task evidence does not prove the Plan acceptance"))
    if not isinstance(receipts, Mapping) or not receipts.get("s4") or not receipts.get("s5"):
        errors.append(_issue("EXEC_STAGE_ANCHOR_MISSING", "/stage_receipts", "execution lint requires S4 and S5 stage anchors"))
    return _report(errors)


def _commit_info(workspace: Any, commit: str | None) -> Mapping[str, Any] | None:
    if not isinstance(commit, str):
        return None
    if isinstance(workspace, Mapping):
        commits = workspace.get("commits", {})
        return commits.get(commit) if isinstance(commits, Mapping) else None
    root = Path(workspace)
    try:
        result = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{commit}^{{commit}}"], capture_output=True, check=False, text=True)
        if result.returncode != 0:
            return None
        parents = subprocess.run(["git", "-C", str(root), "show", "-s", "--format=%P", commit], capture_output=True, check=True, text=True).stdout.strip().split()
        raw_trailers = subprocess.run(["git", "-C", str(root), "show", "-s", "--format=%(trailers:key=NePA-Task,key=NePA-Attempt,key=NePA-Evidence-SHA256,valueonly)", commit], capture_output=True, check=True, text=True).stdout
        trailers: dict[str, str] = {}
        for key in ("NePA-Task", "NePA-Attempt", "NePA-Evidence-SHA256"):
            value = subprocess.run(["git", "-C", str(root), "show", "-s", f"--format=%(trailers:key={key},valueonly)", commit], capture_output=True, check=True, text=True).stdout.strip()
            if value:
                trailers[key] = value
        return {"parents": parents, "trailers": trailers, "raw_trailers": raw_trailers}
    except (OSError, subprocess.SubprocessError):
        return None


def _stage_workspace_anchor(receipts: Any) -> str | None:
    if not isinstance(receipts, Mapping):
        return None
    s5 = receipts.get("s5", {})
    if not isinstance(s5, Mapping):
        return None
    candidates = [s5.get("workspace_head"), s5.get("workspace_commit_sha"), s5.get("commit_sha")]
    output_refs = s5.get("output_refs", {})
    if isinstance(output_refs, Mapping):
        candidates.extend([output_refs.get("workspace_head"), output_refs.get("workspace_commit")])
    return next((value for value in candidates if isinstance(value, str) and value), None)


def _commit_ancestor(workspace: Any, commit: str | None, anchor: str | None) -> bool:
    if not isinstance(commit, str) or not isinstance(anchor, str):
        return False
    if isinstance(workspace, Mapping):
        seen: set[str] = set()
        stack = [commit]
        commits = workspace.get("commits", {})
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            if current == anchor:
                return True
            info = commits.get(current, {}) if isinstance(commits, Mapping) else {}
            stack.extend(info.get("parents", []))
        return False
    try:
        return subprocess.run(["git", "-C", str(workspace), "merge-base", "--is-ancestor", anchor, commit], check=False).returncode == 0
    except OSError:
        return False


def _evidence_bytes(store: Any, ref: Any) -> bytes | None:
    if not isinstance(ref, Mapping) or not isinstance(ref.get("path"), str):
        return None
    if isinstance(store, Mapping):
        value = store.get(ref["path"])
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8")
        if isinstance(value, Mapping) or isinstance(value, list):
            return canonical_json_bytes(value)
        return None
    path = Path(store) / ref["path"]
    try:
        return path.read_bytes()
    except OSError:
        return None


def _evidence_accepts_task(evidence: Mapping[str, Any], task: Mapping[str, Any]) -> bool:
    variants = evidence.get("build_variant_ids")
    expected = task.get("acceptance", {}).get("build_variant_ids", [])
    return isinstance(variants, list) and set(expected).issubset(set(variants)) and evidence.get("build_passed") is True


def complete_execution_lint(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return execution_state_lint(*args, **kwargs)


__all__ = [
    "PlanStateError", "complete_execution_lint", "execution_state_lint", "initialize_plan_state", "plan_state_snapshot_lint", "validate_state_transition",
]
