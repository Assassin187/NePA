"""Deterministic Plan State v2 transitions and execution validation."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
from collections import deque
from pathlib import Path, PurePath
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from ..schemas import load_schema
from .lint import canonical_json_bytes


class PlanStateError(ValueError):
    """A Plan State or typed event is malformed."""


_MAX_ATTEMPTS = 4


def _read(value: Any, label: str) -> Any:
    if isinstance(value, Mapping):
        return copy.deepcopy(dict(value))
    try:
        return json.loads(Path(value).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlanStateError(f"unable to read {label}: {exc}") from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _schema_report(value: Any, name: str, prefix: str = "/") -> list[dict[str, str]]:
    errors = list(Draft202012Validator(load_schema(name)).iter_errors(value))
    return [
        {
            "code": "STATE_SCHEMA_INVALID",
            "path": (prefix + "/".join(str(part) for part in error.absolute_path)).rstrip("/") or "/",
            "message": error.message,
        }
        for error in sorted(errors, key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message))
    ]


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _report(errors: list[dict[str, str]], warnings: list[dict[str, str]] | None = None) -> dict[str, Any]:
    ordered = sorted(errors, key=lambda item: (item["code"], item["path"], item["message"]))
    return {"valid": not ordered, "errors": ordered, "warnings": warnings or []}


def _total_attempt_limit(config_snapshot: Mapping[str, Any] | None) -> int:
    if config_snapshot is None:
        return _MAX_ATTEMPTS
    budgets = config_snapshot.get("budgets", {})
    value = budgets.get("task_fix_attempts", 3) if isinstance(budgets, Mapping) else 3
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise PlanStateError("task_fix_attempts must be a non-negative integer")
    return min(_MAX_ATTEMPTS, value + 1)


def _plan_tasks(plan: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {task["id"]: task for task in plan.get("tasks", []) if isinstance(task, Mapping) and isinstance(task.get("id"), str)}


def _version_from_plan_ref(ref: Mapping[str, Any]) -> str:
    version = ref.get("version")
    return version if isinstance(version, str) and version else "1.0.0"


def _fresh_row(task: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": task["id"], "task_uid": task["task_uid"], "status": "pending", "execution_mode": "normal",
        "attempts": 0, "amendment_used": 0, "notes": "", "commit_sha": None, "last_error": None,
        "acceptance_evidence": {"task_evidence_ref": None}, "migration_ref": None, "group_id": None,
    }


def initialize_plan_state(
    plan: Mapping[str, Any] | str | Path,
    *,
    plan_ref: Mapping[str, Any] | None = None,
    s4_seal: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the unique fresh M1-6 State snapshot without persisting it."""

    plan_value = _read(plan, "Plan")
    if _schema_report(plan_value, "plan.schema.json"):
        raise PlanStateError("Plan failed Schema validation")
    if plan_ref is None:
        seal_ref = None
        if isinstance(s4_seal, Mapping):
            seal_ref = s4_seal.get("active_plan") or s4_seal.get("plan")
            output_refs = s4_seal.get("output_refs")
            if seal_ref is None and isinstance(output_refs, Mapping):
                seal_ref = output_refs.get("active_plan") or output_refs.get("plan")
        plan_ref = seal_ref if isinstance(seal_ref, Mapping) else {"path": "plan/versions/plan-1.0.0.json", "sha256": _sha(plan_value), "version": "1.0.0", "revision_seq": 0, "epoch": "E0"}
    ref = copy.deepcopy(dict(plan_ref))
    ref.setdefault("revision_seq", 0)
    ref.setdefault("version", _version_from_plan_ref(ref))
    ref.setdefault("epoch", "E0")
    if ref.get("sha256") != _sha(plan_value):
        raise PlanStateError("Plan State plan_ref does not match the supplied Plan")
    state = {
        "schema_version": "2.0", "plan_ref": ref,
        "tasks": [_fresh_row(task) for task in plan_value.get("tasks", [])],
        "evidence_counters": {task["task_uid"]: 0 for task in plan_value.get("tasks", [])},
        "s6_attempts_used": 0,
    }
    errors = _schema_report(state, "plan-state.schema.json")
    if errors:
        raise PlanStateError("initial Plan State failed Schema validation: " + errors[0]["message"])
    return state


def _seal_values(s4_seal: Mapping[str, Any] | None) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None, str | None]:
    initial = expected = None
    config_sha = None
    if isinstance(s4_seal, Mapping):
        initial = s4_seal.get("plan")
        expected = s4_seal.get("active_plan")
        config_sha = s4_seal.get("config_snapshot_sha256")
        output_refs = s4_seal.get("output_refs")
        if isinstance(output_refs, Mapping):
            initial = initial or output_refs.get("plan")
            expected = expected or output_refs.get("active_plan") or output_refs.get("plan")
            config_sha = config_sha or output_refs.get("config_snapshot_sha256")
    return initial if isinstance(initial, Mapping) else None, expected if isinstance(expected, Mapping) else None, config_sha if isinstance(config_sha, str) else None


def plan_state_snapshot_lint(
    plan: Mapping[str, Any] | str | Path,
    state: Mapping[str, Any] | str | Path,
    s4_seal: Mapping[str, Any] | None = None,
    config_snapshot: Mapping[str, Any] | str | Path | None = None,
    revision_ledger: Mapping[str, Any] | str | Path | None = None,
) -> dict[str, Any]:
    """Validate JSON State facts only; never inspect or mutate the filesystem."""

    plan_value = _read(plan, "Plan")
    state_value = _read(state, "Plan State")
    config_value = _read(config_snapshot, "config snapshot") if config_snapshot is not None else {}
    errors = _schema_report(plan_value, "plan.schema.json") + _schema_report(state_value, "plan-state.schema.json")
    if errors:
        return _report(errors)
    plan_tasks = _plan_tasks(plan_value)
    state_tasks = {row["id"]: row for row in state_value["tasks"]}
    if len(plan_tasks) != len(plan_value.get("tasks", [])):
        errors.append(_issue("STATE_PLAN_TASK_DUPLICATE", "/tasks", "Plan task ids must be unique"))
    if len(state_tasks) != len(state_value["tasks"]) or set(state_tasks) != set(plan_tasks):
        errors.append(_issue("STATE_TASK_SET_MISMATCH", "/tasks", "Plan State task ids must equal Plan task ids exactly"))
    for task_id, task in plan_tasks.items():
        row = state_tasks.get(task_id)
        if row is None:
            continue
        if row.get("task_uid") != task.get("task_uid"):
            errors.append(_issue("STATE_TASK_UID_INVALID", f"/tasks/{task_id}/task_uid", "State task_uid does not match Plan"))
        if row.get("status") == "in_progress" and row.get("attempts") < 1:
            errors.append(_issue("STATE_IN_PROGRESS_FIELDS_INVALID", f"/tasks/{task_id}", "in-progress tasks require a started attempt"))
        if row.get("execution_mode") == "normal" and row.get("amendment_used") != 0:
            errors.append(_issue("STATE_MODE_FIELDS_INVALID", f"/tasks/{task_id}", "normal tasks cannot consume amendment_used"))
        if row.get("status") == "pending" and row.get("attempts") != 0 and row.get("migration_ref") is None:
            errors.append(_issue("STATE_PENDING_ATTEMPTS_INVALID", f"/tasks/{task_id}/attempts", "fresh pending tasks have zero attempts"))
        if row.get("status") == "in_progress" and row.get("commit_sha") is not None:
            errors.append(_issue("STATE_IN_PROGRESS_FIELDS_INVALID", f"/tasks/{task_id}", "in-progress tasks cannot carry a commit"))
        if row.get("status") == "done" and (row.get("commit_sha") is None or row.get("acceptance_evidence", {}).get("task_evidence_ref") is None or row.get("last_error") is not None):
            errors.append(_issue("STATE_DONE_FIELDS_INVALID", f"/tasks/{task_id}", "done tasks require commit/evidence and no error"))
        if row.get("status") == "blocked" and (row.get("last_error") is None or row.get("commit_sha") is not None or row.get("acceptance_evidence", {}).get("task_evidence_ref") is not None):
            errors.append(_issue("STATE_BLOCKED_FIELDS_INVALID", f"/tasks/{task_id}", "blocked tasks require an error and no success facts"))
        if row.get("status") == "blocked_by_dependency" and (row.get("attempts") != 0 or not row.get("last_error")):
            errors.append(_issue("STATE_DEPENDENCY_BLOCK_FIELDS_INVALID", f"/tasks/{task_id}", "dependency-blocked tasks retain zero attempts and an error"))
    if state_value["plan_ref"].get("sha256") != _sha(plan_value):
        errors.append(_issue("STATE_PLAN_REF_INVALID", "/plan_ref/sha256", "Plan State plan_ref does not match the supplied Plan"))
    _initial, expected, expected_config_sha = _seal_values(s4_seal)
    if expected is not None and any(state_value["plan_ref"].get(key) != expected.get(key) for key in ("path", "sha256", "revision_seq", "version", "epoch") if key in expected):
        errors.append(_issue("STATE_PLAN_REF_INVALID", "/plan_ref", "Plan State plan_ref does not match the S4 seal"))
    if expected_config_sha is not None and (not isinstance(config_value, Mapping) or _sha(config_value) != expected_config_sha):
        errors.append(_issue("STATE_CONFIG_REF_INVALID", "/config_snapshot", "config snapshot does not match the S4 seal"))
    try:
        limit = _total_attempt_limit(config_value)
    except PlanStateError as exc:
        errors.append(_issue("STATE_CONFIG_INVALID", "/config_snapshot", str(exc)))
        limit = _MAX_ATTEMPTS
    for index, row in enumerate(state_value["tasks"]):
        if row["attempts"] > limit:
            errors.append(_issue("STATE_ATTEMPTS_INVALID", f"/tasks/{index}/attempts", "task attempts exceed configured limit"))
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in state_value.get("evidence_counters", {}).values()):
        errors.append(_issue("STATE_EVIDENCE_COUNTER_INVALID", "/evidence_counters", "evidence counters must be non-negative integers"))
    missing_counters = sorted(
        {task.get("task_uid") for task in plan_value.get("tasks", []) if isinstance(task, Mapping)}
        - set(state_value.get("evidence_counters", {})),
        key=lambda value: str(value).encode("utf-8"),
    )
    if missing_counters:
        errors.append(_issue("STATE_EVIDENCE_COUNTER_INVALID", "/evidence_counters", "every active task uid requires an evidence counter"))
    if config_snapshot is not None and state_value.get("s6_attempts_used", 0) < sum(row.get("attempts", 0) for row in state_value["tasks"]):
        errors.append(_issue("STATE_GLOBAL_ATTEMPT_INVALID", "/s6_attempts_used", "global attempt usage cannot be below task attempts"))
    if isinstance(config_value, Mapping):
        budgets = config_value.get("budgets")
        cap = budgets.get("s6_total_attempts_cap") if isinstance(budgets, Mapping) else None
        if isinstance(cap, int) and not isinstance(cap, bool) and cap >= 0 and state_value.get("s6_attempts_used", 0) > cap:
            errors.append(_issue("STATE_GLOBAL_ATTEMPT_INVALID", "/s6_attempts_used", "global attempt usage exceeds the configured S6 cap"))
    return _report(errors)


def _dependency_ancestors(plan: Mapping[str, Any], task_id: str) -> set[str]:
    tasks = _plan_tasks(plan)
    result: set[str] = set()
    queue = deque(tasks.get(task_id, {}).get("depends_on", []) if task_id in tasks else [])
    while queue:
        parent = queue.popleft()
        if parent in result:
            continue
        result.add(parent)
        queue.extend(tasks.get(parent, {}).get("depends_on", []) if parent in tasks else [])
    return result


def validate_state_transition(
    old_state: Mapping[str, Any] | str | Path,
    new_state: Mapping[str, Any] | str | Path | None,
    event: Mapping[str, Any] | str,
    *,
    plan: Mapping[str, Any] | str | Path | None = None,
    config_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive and compare the only legal v2 State transition."""

    old = _read(old_state, "old Plan State")
    new = _read(new_state, "new Plan State") if new_state is not None else None
    event_value = {"schema_version": "2.0", "event": event, "task_id": ""} if isinstance(event, str) else copy.deepcopy(dict(event))
    event_value.setdefault("schema_version", "2.0")
    errors = _schema_report(old, "plan-state.schema.json") + ([] if new is None else _schema_report(new, "plan-state.schema.json")) + _schema_report(event_value, "plan-event.schema.json")
    if errors:
        return {**_report(errors), "state": None}
    task_id = event_value["task_id"]
    old_by_id = {row["id"]: row for row in old["tasks"]}
    new_by_id = old_by_id if new is None else {row["id"]: row for row in new["tasks"]}
    if task_id not in old_by_id or set(old_by_id) != set(new_by_id):
        return {**_report([_issue("STATE_TASK_SET_MISMATCH", "/tasks", "transition must preserve the complete task set")]), "state": None}
    current = old_by_id[task_id]
    derived = copy.deepcopy(current)
    name = event_value["event"]
    try:
        limit = _total_attempt_limit(config_snapshot)
    except PlanStateError as exc:
        return {**_report([_issue("STATE_CONFIG_INVALID", "/config_snapshot", str(exc))]), "state": None}
    proof = event_value.get("proof") if isinstance(event_value.get("proof"), Mapping) else {}
    if name == "attempt_started":
        if current["status"] not in {"pending", "in_progress"} or current["execution_mode"] != "normal" or current["attempts"] >= limit:
            errors.append(_issue("STATE_TRANSITION_INVALID", f"/tasks/{task_id}", "attempt_started is not legal from the current state"))
        elif event_value.get("attempt") is not None and not proof:
            errors.append(_issue("STATE_TRANSITION_INVALID", f"/tasks/{task_id}", "typed attempt allocation requires its persisted proof"))
        elif proof and any(key not in proof for key in ("baseline_commit", "baseline_tree", "evidence_seq", "s6_attempts_used")):
            errors.append(_issue("STATE_TRANSITION_INVALID", f"/tasks/{task_id}", "attempt allocation proof is incomplete"))
        elif current["status"] == "in_progress" and not event_value.get("previous_error") and not proof.get("previous_failure_ref"):
            errors.append(_issue("STATE_TRANSITION_INVALID", f"/tasks/{task_id}", "a retry must carry the previous failure"))
        else:
            expected_attempt = current["attempts"] + 1
            expected_sequence = int(old.get("evidence_counters", {}).get(current["task_uid"], 0)) + 1
            if event_value.get("attempt", expected_attempt) != expected_attempt or (proof and proof.get("evidence_seq") != expected_sequence):
                errors.append(_issue("STATE_TRANSITION_INVALID", f"/tasks/{task_id}/attempts", "attempt allocation is not monotonic"))
            else:
                derived.update({"status": "in_progress", "attempts": expected_attempt, "last_error": None, "commit_sha": None, "acceptance_evidence": {"task_evidence_ref": None}})
                candidate_global = old["s6_attempts_used"] + 1
                if "s6_attempts_used" in event_value and event_value["s6_attempts_used"] != candidate_global:
                    errors.append(_issue("STATE_GLOBAL_ATTEMPT_INVALID", "/s6_attempts_used", "global attempt usage must increment exactly once"))
    elif name in {"attempt_succeeded", "reconciled_commit"}:
        if current["status"] != "in_progress" or not isinstance(event_value.get("commit_sha"), str) or not isinstance(event_value.get("evidence_ref"), Mapping):
            errors.append(_issue("STATE_TRANSITION_INVALID", f"/tasks/{task_id}", f"{name} requires an in-progress task and typed commit/evidence"))
        elif name == "reconciled_commit" and any(key not in proof for key in ("commit_sha", "evidence_ref", "file_ledger_ref", "revision_ledger_ref", "event_ref")):
            errors.append(_issue("STATE_TRANSITION_INVALID", f"/tasks/{task_id}", "reconciled_commit requires a complete WAL proof"))
        elif name == "reconciled_commit" and (proof.get("commit_sha") != event_value.get("commit_sha") or proof.get("evidence_ref") != event_value.get("evidence_ref")):
            errors.append(_issue("STATE_TRANSITION_INVALID", f"/tasks/{task_id}", "reconciled_commit proof does not match the event"))
        elif name == "attempt_succeeded" and re.search(r"/evidence_[0-9]+\.json$", str(event_value.get("evidence_ref", {}).get("path", ""))) and (any(key not in proof for key in ("commit_sha", "evidence_ref", "file_ledger_ref", "revision_ledger_ref", "event_ref")) or proof.get("commit_sha") != event_value.get("commit_sha") or proof.get("evidence_ref") != event_value.get("evidence_ref")):
            errors.append(_issue("STATE_TRANSITION_INVALID", f"/tasks/{task_id}", "attempt_succeeded proof is incomplete or does not match the accepted facts"))
        else:
            derived.update({"status": "done", "commit_sha": event_value["commit_sha"], "last_error": None, "acceptance_evidence": {"task_evidence_ref": copy.deepcopy(event_value["evidence_ref"])}})
    elif name == "attempts_exhausted":
        if current["status"] != "in_progress" or current["attempts"] != limit or not isinstance(event_value.get("error"), str) or not event_value["error"] or any(key not in proof for key in ("previous_failure_ref", "evidence_seq", "s6_attempts_used")):
            errors.append(_issue("STATE_TRANSITION_INVALID", f"/tasks/{task_id}", "attempts_exhausted requires the final failed-attempt proof"))
        else:
            derived.update({"status": "blocked", "last_error": event_value["error"], "commit_sha": None, "acceptance_evidence": {"task_evidence_ref": None}})
    elif name == "dependency_blocked":
        plan_value = _read(plan, "Plan") if plan is not None else None
        task_plan = _plan_tasks(plan_value or {}).get(task_id) if plan_value else None
        states = {row["id"]: row["status"] for row in old["tasks"]}
        dependencies = task_plan.get("depends_on", []) if task_plan else []
        blocked = any(states.get(parent) in {"blocked", "blocked_by_dependency"} for parent in dependencies) or any(states.get(parent) in {"blocked", "blocked_by_dependency"} for parent in _dependency_ancestors(plan_value or {}, task_id))
        if current["status"] != "pending" or task_plan is None or not blocked or not event_value.get("error"):
            errors.append(_issue("STATE_DEPENDENCY_PROOF_INVALID", f"/tasks/{task_id}", "dependency_blocked requires a blocked transitive dependency"))
        else:
            derived.update({"status": "blocked_by_dependency", "last_error": event_value["error"], "commit_sha": None, "acceptance_evidence": {"task_evidence_ref": None}})
    else:
        errors.append(_issue("STATE_EVENT_INVALID", "/event", "event is not in the closed ordinary transition table"))
    if errors:
        return {**_report(errors), "state": None}
    candidate = copy.deepcopy(old)
    if name == "attempt_started" and ("s6_attempts_used" in event_value or proof):
        candidate["s6_attempts_used"] += 1
        candidate["evidence_counters"][current["task_uid"]] = proof["evidence_seq"]
    if "notes" in event_value:
        derived["notes"] = event_value["notes"]
    for index, row in enumerate(candidate["tasks"]):
        if row["id"] == task_id:
            candidate["tasks"][index] = derived
    if new is not None and candidate != new:
        errors.append(_issue("STATE_DERIVED_MISMATCH", f"/tasks/{task_id}", "new State is not derived from old State and event"))
    return {**_report(errors), "state": candidate if not errors else None}


def project_state_transition(
    old_state: Mapping[str, Any] | str | Path,
    event: Mapping[str, Any] | str,
    *,
    plan: Mapping[str, Any] | str | Path | None = None,
    config_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project the sole legal next State from an ordinary typed event."""

    report = validate_state_transition(
        old_state, None, event, plan=plan, config_snapshot=config_snapshot,
    )
    if not report["valid"] or report["state"] is None:
        raise PlanStateError(report["errors"][0]["message"])
    return report["state"]


def _commit_info(workspace: Any, commit: str | None) -> Mapping[str, Any] | None:
    if not isinstance(commit, str):
        return None
    if isinstance(workspace, Mapping):
        commits = workspace.get("commits", {})
        return commits.get(commit) if isinstance(commits, Mapping) else None
    root = Path(workspace)
    try:
        if subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{commit}^{{commit}}"], capture_output=True, check=False).returncode != 0:
            return None
        parents = subprocess.run(["git", "-C", str(root), "show", "-s", "--format=%P", commit], capture_output=True, text=True, check=True).stdout.strip().split()
        trailers = {}
        for key in ("NePA-Task", "NePA-Task-UID", "NePA-Plan", "NePA-Epoch", "NePA-Attempt", "NePA-Evidence-Seq", "NePA-Evidence-SHA256"):
            value = subprocess.run(["git", "-C", str(root), "show", "-s", f"--format=%(trailers:key={key},valueonly)", commit], capture_output=True, text=True, check=True).stdout.strip()
            if value:
                trailers[key] = value
        tree = subprocess.run(["git", "-C", str(root), "rev-parse", f"{commit}^{{tree}}"], capture_output=True, text=True, check=True).stdout.strip()
        return {"parents": parents, "trailers": trailers, "tree": tree}
    except (OSError, subprocess.SubprocessError):
        return None


def _commit_ancestor(workspace: Any, commit: str | None, anchor: str | None) -> bool:
    if not isinstance(commit, str) or not isinstance(anchor, str):
        return False
    if isinstance(workspace, Mapping):
        commits = workspace.get("commits", {})
        seen: set[str] = set()
        stack = [commit]
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
    relative_path = PurePath(ref["path"])
    if relative_path.is_absolute() or ".." in relative_path.parts:
        return None
    if isinstance(store, Mapping):
        value = store.get(ref["path"])
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8")
        if isinstance(value, (Mapping, list)):
            return canonical_json_bytes(value)
        return None
    try:
        return (Path(store) / ref["path"]).read_bytes()
    except OSError:
        return None


def _workspace_clean(workspace: Any) -> bool:
    if isinstance(workspace, Mapping):
        return not bool(workspace.get("dirty_paths", []))
    try:
        return subprocess.run(
            ["git", "-C", str(Path(workspace)), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        ).stdout.strip() == ""
    except OSError:
        return False


def _workspace_file_hash(workspace: Any, relative: str) -> str | None:
    if isinstance(workspace, Mapping):
        files = workspace.get("files", {})
        value = files.get(relative) if isinstance(files, Mapping) else None
        if isinstance(value, bytes):
            return hashlib.sha256(value).hexdigest()
        if isinstance(value, str):
            return hashlib.sha256(value.encode("utf-8")).hexdigest()
        return None
    try:
        return hashlib.sha256((Path(workspace) / relative).read_bytes()).hexdigest()
    except OSError:
        return None


def _commit_content_tree_hash(workspace: Any, commit: str | None) -> str | None:
    """Hash the committed non-.git file tree with the S6 content-tree format."""

    if not isinstance(commit, str):
        return None
    if isinstance(workspace, Mapping):
        info = _commit_info(workspace, commit)
        value = info.get("content_tree_hash") if isinstance(info, Mapping) else None
        return value if isinstance(value, str) else None
    root = Path(workspace)
    try:
        names = subprocess.run(
            ["git", "-C", str(root), "ls-tree", "-r", "-z", "--name-only", commit],
            capture_output=True,
            check=True,
        ).stdout.split(b"\0")
        digest = hashlib.sha256()
        for raw_name in sorted((item for item in names if item), key=lambda item: item):
            name = raw_name.decode("utf-8")
            data = subprocess.run(
                ["git", "-C", str(root), "show", f"{commit}:{name}"],
                capture_output=True,
                check=True,
            ).stdout
            digest.update(len(raw_name).to_bytes(4, "big"))
            digest.update(raw_name)
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
        return digest.hexdigest()
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return None


def _attempt_history_errors(root: Path, state: Mapping[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    counters = state.get("evidence_counters", {})
    for row in state.get("tasks", []):
        task_id = row.get("id")
        task_uid = row.get("task_uid")
        if not isinstance(task_id, str) or not isinstance(task_uid, str):
            continue
        directory = root / "attempts" / task_uid
        paths = sorted(directory.glob("attempt_[0-9][0-9][0-9].json"), key=lambda path: path.name)
        sequences: set[int] = set()
        records: dict[int, Mapping[str, Any]] = {}
        for path in paths:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                errors.append(_issue("EXEC_ATTEMPT_INVALID", f"/tasks/{task_id}/attempts", "attempt record is not readable JSON"))
                continue
            schema_errors = _schema_report(value, "s6-attempt.schema.json", f"/tasks/{task_id}/attempts")
            errors.extend(schema_errors)
            if schema_errors or not isinstance(value, Mapping):
                continue
            attempt = value.get("attempt")
            sequence = value.get("evidence_seq")
            if value.get("task_id") != task_id or value.get("task_uid") != task_uid or not isinstance(attempt, int) or not isinstance(sequence, int):
                errors.append(_issue("EXEC_ATTEMPT_IDENTITY_INVALID", f"/tasks/{task_id}/attempts", "attempt record identity does not match State"))
                continue
            if attempt > row.get("attempts", 0) or sequence in sequences:
                errors.append(_issue("EXEC_ATTEMPT_SEQUENCE_INVALID", f"/tasks/{task_id}/attempts/{attempt}", "attempt history is not monotonic"))
            sequences.add(sequence)
            records[attempt] = value
            if sequence > counters.get(task_uid, 0):
                errors.append(_issue("EXEC_ATTEMPT_COUNTER_INVALID", f"/tasks/{task_id}/attempts/{attempt}", "attempt evidence sequence exceeds its State counter"))
            status = value.get("status")
            ref_key = "failure_ref" if status in {"failed", "exhausted"} else "output_ref" if status == "succeeded" else None
            if ref_key is not None:
                ref = value.get(ref_key)
                if not isinstance(ref, Mapping) or _evidence_bytes(root, ref) is None or hashlib.sha256(_evidence_bytes(root, ref) or b"").hexdigest() != ref.get("sha256"):
                    errors.append(_issue("EXEC_ATTEMPT_REF_INVALID", f"/tasks/{task_id}/attempts/{attempt}", "terminal attempt reference is missing or drifted"))
        if row.get("status") == "done":
            record = records.get(row.get("attempts"))
            evidence_ref = row.get("acceptance_evidence", {}).get("task_evidence_ref") if isinstance(row.get("acceptance_evidence"), Mapping) else None
            if not isinstance(record, Mapping) or record.get("status") != "succeeded" or record.get("output_ref") != evidence_ref:
                errors.append(_issue("EXEC_ATTEMPT_STATE_INVALID", f"/tasks/{task_id}", "done State has no matching succeeded attempt record"))
        elif row.get("status") == "blocked":
            record = records.get(row.get("attempts"))
            if not isinstance(record, Mapping) or record.get("status") != "exhausted" or record.get("failure_ref", {}).get("path") != row.get("last_error"):
                errors.append(_issue("EXEC_ATTEMPT_STATE_INVALID", f"/tasks/{task_id}", "blocked State has no matching exhausted attempt record"))
        elif row.get("status") == "in_progress" and row.get("last_error") is not None:
            if not any(value.get("failure_ref", {}).get("path") == row.get("last_error") for value in records.values()):
                errors.append(_issue("EXEC_ATTEMPT_STATE_INVALID", f"/tasks/{task_id}", "in-progress retry does not reference its latest failure"))
    return errors


def execution_state_lint(
    plan: Mapping[str, Any] | str | Path,
    state: Mapping[str, Any] | str | Path,
    workspace: Mapping[str, Any] | str | Path,
    evidence_store: Mapping[str, Any] | str | Path,
    stage_receipts: Mapping[str, Any] | str | Path,
    *,
    config_snapshot: Mapping[str, Any] | None = None,
    revision_ledger: Mapping[str, Any] | str | Path | None = None,
    active_pointer: Mapping[str, Any] | str | Path | None = None,
) -> dict[str, Any]:
    """Validate external evidence and git facts without changing them."""

    plan_value = _read(plan, "Plan")
    state_value = _read(state, "Plan State")
    receipts = _read(stage_receipts, "stage receipts")
    errors = plan_state_snapshot_lint(plan_value, state_value, config_snapshot=config_snapshot, revision_ledger=revision_ledger).get("errors", [])
    pointer = _read(active_pointer, "active pointer") if active_pointer is not None else None
    if isinstance(pointer, Mapping) and state_value.get("plan_ref") != pointer:
        errors.append(_issue("EXEC_ACTIVE_POINTER_INVALID", "/plan_ref", "Plan State does not match the active pointer"))
    anchor = None
    if isinstance(receipts, Mapping):
        s5 = receipts.get("s5", {})
        if isinstance(s5, Mapping):
            anchor = s5.get("workspace_head") or s5.get("workspace_commit_sha") or s5.get("commit_sha")
            output = s5.get("output_refs", {})
            if isinstance(output, Mapping):
                anchor = anchor or output.get("workspace_head") or output.get("workspace_commit")
    tasks = _plan_tasks(plan_value)
    ledger = _read(revision_ledger, "revision ledger") if revision_ledger is not None else None
    file_ledger = None
    if isinstance(evidence_store, (str, Path)):
        ledger_path = Path(evidence_store) / "plan/file_ledger.json"
        if ledger_path.is_file():
            try:
                file_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                errors.append(_issue("EXEC_FILE_LEDGER_INVALID", "/file_ledger", "file ledger is not readable JSON"))
    if file_ledger is None and isinstance(evidence_store, Mapping):
        candidate = evidence_store.get("plan/file_ledger.json")
        if isinstance(candidate, Mapping):
            file_ledger = candidate
    for row in state_value.get("tasks", []):
        if row.get("status") != "done":
            continue
        task_id = row["id"]
        evidence_ref = row.get("acceptance_evidence", {}).get("task_evidence_ref")
        raw = _evidence_bytes(evidence_store, evidence_ref)
        if raw is None:
            errors.append(_issue("EXEC_EVIDENCE_MISSING", f"/tasks/{task_id}", "task evidence is missing"))
            continue
        if hashlib.sha256(raw).hexdigest() != evidence_ref.get("sha256"):
            errors.append(_issue("EXEC_EVIDENCE_HASH_INVALID", f"/tasks/{task_id}", "task evidence hash does not match"))
            continue
        try:
            evidence = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            errors.append(_issue("EXEC_EVIDENCE_INVALID", f"/tasks/{task_id}", "task evidence is not JSON"))
            continue
        legacy_evidence = isinstance(evidence, Mapping) and evidence.get("schema_version") == "1.0"
        if not legacy_evidence:
            errors.extend(_schema_report(evidence, "task-evidence.schema.json", f"/tasks/{task_id}/evidence"))
        if isinstance(evidence, Mapping):
            if evidence.get("task_id") != task_id or evidence.get("attempt") != row.get("attempts") or (not legacy_evidence and evidence.get("task_uid") != row.get("task_uid")):
                errors.append(_issue("EXEC_EVIDENCE_IDENTITY_INVALID", f"/tasks/{task_id}", "task evidence identity does not match State"))
            if evidence.get("plan_sha256") != _sha(plan_value):
                errors.append(_issue("EXEC_EVIDENCE_BINDING_INVALID", f"/tasks/{task_id}", "task evidence Plan/commit binding does not match"))
            if not legacy_evidence:
                if evidence.get("plan_ref") != state_value.get("plan_ref") or evidence.get("plan_version") != state_value.get("plan_ref", {}).get("version") or evidence.get("epoch") != state_value.get("plan_ref", {}).get("epoch"):
                    errors.append(_issue("EXEC_EVIDENCE_BINDING_INVALID", f"/tasks/{task_id}", "task evidence Plan version or epoch binding does not match State"))
                s5_output = receipts.get("s5", {}).get("output_refs", {}) if isinstance(receipts.get("s5", {}), Mapping) else {}
                if isinstance(s5_output, Mapping) and evidence.get("binding_ref") != s5_output.get("binding_receipt"):
                    errors.append(_issue("EXEC_EVIDENCE_BINDING_INVALID", f"/tasks/{task_id}", "task evidence binding receipt does not match S5"))
                input_refs = evidence.get("input_refs", {})
                if isinstance(input_refs, Mapping):
                    for input_name, input_ref in input_refs.items():
                        raw_input = _evidence_bytes(evidence_store, input_ref)
                        if raw_input is None or hashlib.sha256(raw_input).hexdigest() != input_ref.get("sha256"):
                            errors.append(_issue("EXEC_INPUT_REF_INVALID", f"/tasks/{task_id}/input_refs/{input_name}", "task evidence input reference is missing or drifted"))
            if not legacy_evidence and evidence.get("evidence_seq", 0) > state_value.get("evidence_counters", {}).get(row.get("task_uid"), 0):
                errors.append(_issue("EXEC_EVIDENCE_SEQUENCE_INVALID", f"/tasks/{task_id}", "task evidence sequence exceeds the persisted counter"))
            result_refs = [] if legacy_evidence else [(ref, "build-result.schema.json") for ref in evidence.get("build_result_refs", [])] + [(ref, "smoke-result.schema.json") for ref in evidence.get("smoke_result_refs", [])]
            for result_ref, schema_name in result_refs:
                result_raw = _evidence_bytes(evidence_store, result_ref)
                if result_raw is None or hashlib.sha256(result_raw).hexdigest() != result_ref.get("sha256"):
                    errors.append(_issue("EXEC_RESULT_REF_INVALID", f"/tasks/{task_id}", "build or smoke result reference is missing or drifted"))
                    continue
                try:
                    result_value = json.loads(result_raw)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    errors.append(_issue("EXEC_RESULT_INVALID", f"/tasks/{task_id}", "build or smoke result is not JSON"))
                    continue
                errors.extend(_schema_report(result_value, schema_name, f"/tasks/{task_id}/results"))
                if isinstance(result_value, Mapping) and result_value.get("status") != "passed":
                    errors.append(_issue("EXEC_RESULT_FAILED", f"/tasks/{task_id}", "accepted task evidence references a failed result"))
            if not legacy_evidence:
                declared = set(tasks.get(task_id, {}).get("deliverable_files", [])) if isinstance(tasks.get(task_id), Mapping) else set()
                changed = evidence.get("changed_files", [])
                for item in changed:
                    relative = item.get("path") if isinstance(item, Mapping) else None
                    if relative not in declared:
                        errors.append(_issue("EXEC_CHANGED_FILE_INVALID", f"/tasks/{task_id}", "changed file is outside the task deliverable set"))
                        continue
                    if _workspace_file_hash(workspace, relative) != item.get("sha256"):
                        errors.append(_issue("EXEC_CHANGED_FILE_INVALID", f"/tasks/{task_id}", "changed file hash does not match the accepted workspace"))
                    if isinstance(file_ledger, Mapping):
                        file_row = next((value for value in file_ledger.get("files", []) if isinstance(value, Mapping) and value.get("path") == relative), None)
                        if not isinstance(file_row, Mapping) or file_row.get("class") != "s6_owned" or file_row.get("state") != "realized" or file_row.get("last_commit_sha") != row.get("commit_sha") or file_row.get("content_sha256") != item.get("sha256"):
                            errors.append(_issue("EXEC_FILE_LEDGER_INVALID", f"/tasks/{task_id}", "changed file is not realized by the matching owner ledger row"))
        commit_info = _commit_info(workspace, row.get("commit_sha"))
        if commit_info is None:
            errors.append(_issue("EXEC_COMMIT_MISSING", f"/tasks/{task_id}", "task commit is missing"))
        else:
            trailers = commit_info.get("trailers", {})
            expected = {"NePA-Task": task_id, "NePA-Attempt": str(row.get("attempts"))}
            if not legacy_evidence:
                expected.update({"NePA-Task-UID": row.get("task_uid"), "NePA-Plan": state_value.get("plan_ref", {}).get("version"), "NePA-Epoch": state_value.get("plan_ref", {}).get("epoch"), "NePA-Evidence-Seq": str(evidence.get("evidence_seq"))})
            for key, value in expected.items():
                if trailers.get(key) != value:
                    errors.append(_issue("EXEC_COMMIT_TRAILER_INVALID", f"/tasks/{task_id}", f"commit trailer {key} does not match"))
            if trailers.get("NePA-Evidence-SHA256") != evidence_ref.get("sha256"):
                errors.append(_issue("EXEC_COMMIT_TRAILER_INVALID", f"/tasks/{task_id}", "commit evidence trailer does not match"))
            if anchor is not None and not _commit_ancestor(workspace, row.get("commit_sha"), anchor):
                errors.append(_issue("EXEC_COMMIT_ANCESTRY_INVALID", f"/tasks/{task_id}", "task commit is not descended from E0"))
            if not legacy_evidence and _commit_content_tree_hash(workspace, row.get("commit_sha")) != evidence.get("workspace_tree"):
                errors.append(_issue("EXEC_COMMIT_TREE_INVALID", f"/tasks/{task_id}", "task commit content tree does not match task evidence"))
    if not _workspace_clean(workspace):
        errors.append(_issue("EXEC_WORKSPACE_DIRTY", "/workspace", "accepted execution workspace is not clean"))
    if isinstance(ledger, Mapping):
        try:
            from .plan_revision import validate_revision_ledger
            validate_revision_ledger(ledger)
        except Exception as exc:
            errors.append(_issue("EXEC_REVISION_LEDGER_INVALID", "/revision_ledger", str(exc)))
        verification_payloads = [
            entry.get("payload", {}) for entry in ledger.get("entries", [])
            if isinstance(entry, Mapping) and entry.get("event_type") == "verification_committed"
        ]
        for row in state_value.get("tasks", []):
            if row.get("status") != "done":
                continue
            evidence_ref = row.get("acceptance_evidence", {}).get("task_evidence_ref", {})
            sequence = re.search(r"evidence_(\d+)\.json$", str(evidence_ref.get("path", "")))
            expected_id = f"v-{row.get('task_uid')}-{int(sequence.group(1))}" if sequence else None
            expected = {
                "verification_id": expected_id, "kind": "normal",
                "member_uids": [row.get("task_uid")], "evidence_refs": [evidence_ref],
                "commit_sha": row.get("commit_sha"),
                "revision_seq": state_value.get("plan_ref", {}).get("revision_seq"),
            }
            if sum(payload == expected for payload in verification_payloads) != 1:
                errors.append(_issue("EXEC_VERIFICATION_EVENT_INVALID", f"/tasks/{row.get('id')}", "accepted task does not have exactly one complete matching verification event"))
    if isinstance(evidence_store, (str, Path)):
        errors.extend(_attempt_history_errors(Path(evidence_store), state_value))
    return _report(errors)


def complete_execution_lint(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return execution_state_lint(*args, **kwargs)


__all__ = ["PlanStateError", "complete_execution_lint", "execution_state_lint", "initialize_plan_state", "plan_state_snapshot_lint", "project_state_transition", "validate_state_transition"]
