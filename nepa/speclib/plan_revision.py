"""Pure Plan revision classification, ledger, successor, and projection semantics."""

from __future__ import annotations

import copy
import hashlib
import re
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from ..schemas import load_schema
from .lint import canonical_json_bytes


class PlanRevisionError(ValueError):
    """A revision artifact is incomplete, inconsistent, or not deterministic."""


_ZERO_HASH = "0" * 64
_VERSION_RE = re.compile(r"^1\.(\d+)\.(\d+)$")
_EPOCH_RE = re.compile(r"^E(\d+)$")
_CLASSIFICATIONS = ("INHERIT", "REVALIDATE", "AMEND", "REGENERATE")


def _schema_errors(value: Any, schema_name: str) -> list[str]:
    return [
        error.message
        for error in sorted(
            Draft202012Validator(load_schema(schema_name)).iter_errors(value),
            key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
        )
    ]


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _utf8(value: Any) -> bytes:
    return str(value).encode("utf-8")


def _sorted(values: Any) -> list[Any]:
    return sorted((copy.deepcopy(value) for value in (values or [])), key=canonical_json_bytes)


def _validate_complete_inputs(
    old_plan: Mapping[str, Any],
    new_plan: Mapping[str, Any],
    old_state: Mapping[str, Any],
    file_ledger: Mapping[str, Any],
) -> None:
    for value, schema, label in (
        (old_plan, "plan.schema.json", "old Plan"),
        (new_plan, "plan.schema.json", "new Plan"),
        (old_state, "plan-state.schema.json", "old Plan State"),
        (file_ledger, "file-ledger.schema.json", "file ledger"),
    ):
        errors = [] if schema == "file-ledger.schema.json" and value.get("schema_version") == "1.0" else _schema_errors(value, schema)
        if errors:
            raise PlanRevisionError(f"{label} failed Schema validation: {'; '.join(errors)}")
    _validate_unique_task_uids(old_plan, "old Plan")
    _validate_unique_task_uids(new_plan, "new Plan")
    for plan, state, label in ((old_plan, old_state, "old"),):
        plan_ids = [task.get("id") for task in plan.get("tasks", [])]
        state_ids = [task.get("id") for task in state.get("tasks", [])]
        if len(plan_ids) != len(set(plan_ids)) or len(state_ids) != len(set(state_ids)) or set(plan_ids) != set(state_ids):
            raise PlanRevisionError(f"{label} Plan and Plan State task sets must match exactly")
    validate_file_ledger(file_ledger)


def _validate_unique_task_uids(plan: Mapping[str, Any], label: str) -> None:
    uids = [task.get("task_uid") for task in plan.get("tasks", [])]
    ids = [task.get("id") for task in plan.get("tasks", [])]
    if len(uids) != len(set(uids)):
        raise PlanRevisionError(f"{label} contains colliding task_uid values")
    if len(ids) != len(set(ids)):
        raise PlanRevisionError(f"{label} contains colliding task id values")


def _task_maps(plan: Mapping[str, Any]) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    by_uid: dict[str, Mapping[str, Any]] = {}
    by_id: dict[str, Mapping[str, Any]] = {}
    for task in plan.get("tasks", []):
        by_uid[task["task_uid"]] = task
        by_id[task["id"]] = task
    return by_uid, by_id


def _lineage_groups(
    lineage: Any,
    old_by_uid: Mapping[str, Mapping[str, Any]],
    new_by_uid: Mapping[str, Mapping[str, Any]],
) -> list[tuple[tuple[str, ...], str]]:
    if lineage is None:
        return []
    if not isinstance(lineage, Mapping) or set(lineage) != {"task_mappings"}:
        raise PlanRevisionError("explicit task lineage requires only a task_mappings envelope")
    values: Any = lineage["task_mappings"]
    if not isinstance(values, list):
        raise PlanRevisionError("explicit task lineage must be an array")
    groups: list[tuple[tuple[str, ...], str]] = []
    for item in values:
        if not isinstance(item, Mapping):
            raise PlanRevisionError("explicit task lineage rows must be objects")
        if set(item) - {"new_task_uid", "derived_from", "merged_from", "old_task_uid"}:
            raise PlanRevisionError("explicit lineage rows contain unsupported fields")
        new = item.get("new_task_uid")
        source_fields = [field for field in ("derived_from", "merged_from", "old_task_uid") if field in item]
        if len(source_fields) != 1:
            raise PlanRevisionError("explicit lineage rows require exactly one source form")
        source_value = item[source_fields[0]]
        if source_fields[0] in {"derived_from", "old_task_uid"}:
            sources = [source_value]
        else:
            sources = source_value
        if not isinstance(sources, list) or not isinstance(new, str) or not sources or any(not isinstance(source, str) for source in sources):
            raise PlanRevisionError("explicit lineage rows require one or more old task uids and a new task uid")
        source_tuple = tuple(sorted(set(sources), key=_utf8))
        if len(source_tuple) != len(sources):
            raise PlanRevisionError("explicit lineage rows cannot repeat a source task uid")
        groups.append((source_tuple, new))
    seen_source: set[str] = set()
    merged_sources: set[str] = set()
    seen_new: set[str] = set()
    for sources, new in groups:
        if any(source not in old_by_uid for source in sources) or new not in new_by_uid:
            raise PlanRevisionError("explicit lineage references an unknown task uid")
        if new in seen_new:
            raise PlanRevisionError("explicit lineage assigns a new task uid more than once")
        # A one-source group may fan out to multiple new tasks (split). A
        # source cannot otherwise be consumed by two independent groups.
        if any(source in merged_sources or (source in seen_source and len(sources) != 1) for source in sources):
            raise PlanRevisionError("explicit lineage reuses a source task in a non-split group")
        if len(sources) > 1:
            merged_sources.update(sources)
        seen_source.update(sources)
        seen_new.add(new)
    return groups


def _task_pairs(old_plan: Mapping[str, Any], new_plan: Mapping[str, Any], lineage: Any) -> list[tuple[tuple[str, ...] | None, str | None]]:
    old_by_uid, _ = _task_maps(old_plan)
    new_by_uid, _ = _task_maps(new_plan)
    pairs: list[tuple[tuple[str, ...] | None, str | None]] = [((uid,), uid) for uid in sorted(set(old_by_uid) & set(new_by_uid), key=_utf8)]
    explicit = _lineage_groups(lineage, old_by_uid, new_by_uid)
    equal_old = {old[0] for old, _new in pairs if old is not None}
    used_old = set(equal_old)
    used_new = {new for _old, new in pairs}
    for sources, new in explicit:
        if new in used_new or any(source in equal_old for source in sources):
            raise PlanRevisionError("explicit lineage duplicates an equal-uid correspondence")
        pairs.append((sources, new))
        used_old.update(sources)
        used_new.add(new)
    pairs.extend(((uid,), None) for uid in sorted(set(old_by_uid) - used_old, key=_utf8))
    pairs.extend((None, uid) for uid in sorted(set(new_by_uid) - used_new, key=_utf8))
    return sorted(pairs, key=lambda item: (_utf8(item[0][0] if item[0] else ""), _utf8(item[1] or "")))


def _responsibility_set(task: Mapping[str, Any]) -> set[tuple[str, str]]:
    return {(item.get("req_id"), item.get("role")) for item in task.get("requirement_responsibilities", []) if isinstance(item, Mapping)}


def _jaccard(left: set[Any], right: set[Any]) -> float:
    union = left | right
    return 1.0 if not union else len(left & right) / len(union)


def _old_attempts(old_state_by_id: Mapping[str, Mapping[str, Any]], old_task: Mapping[str, Any] | None) -> int:
    if old_task is None:
        return 0
    state = old_state_by_id.get(old_task["id"])
    if state is None:
        raise PlanRevisionError(f"old Plan State has no row for {old_task['id']}")
    return state["attempts"]


def _row_old_uids(row: Mapping[str, Any]) -> list[str]:
    values = [row["old_task_uid"]] if isinstance(row.get("old_task_uid"), str) else []
    values.extend(value for value in row.get("merged_from", []) if isinstance(value, str))
    return values


def _row_old_ids(row: Mapping[str, Any]) -> list[str]:
    values = [row["old_task_id"]] if isinstance(row.get("old_task_id"), str) else []
    values.extend(value for value in row.get("merged_from_ids", []) if isinstance(value, str))
    return values


def _classify_task(old_task: Mapping[str, Any] | None, new_task: Mapping[str, Any] | None, old_state_by_id: Mapping[str, Mapping[str, Any]]) -> tuple[str, str]:
    if old_task is None or new_task is None:
        return "REGENERATE", "TASK_ADDED_OR_REMOVED"
    old_state = old_state_by_id[old_task["id"]]
    if old_state["status"] != "done":
        return "REGENERATE", "OLD_TASK_NOT_DONE"
    if old_task["obligation_digest"] == new_task["obligation_digest"]:
        if old_task["task_uid"] != new_task["task_uid"]:
            return "REVALIDATE", "OWNER_REASSIGNED"
        return "INHERIT", "OBLIGATION_UNCHANGED"
    if set(new_task.get("deliverable_files", [])) - set(old_task.get("deliverable_files", [])):
        return "REGENERATE", "NEW_DELIVERABLE_FILE"
    if _jaccard(_responsibility_set(old_task), _responsibility_set(new_task)) < 0.5:
        return "REGENERATE", "RESPONSIBILITY_JACCARD_BELOW_HALF"
    return "AMEND", "OBLIGATION_CHANGED"


def _file_owner_uid(ledger_row: Mapping[str, Any]) -> str | None:
    history = ledger_row.get("owner_history", [])
    if not history:
        return None
    return history[-1].get("task_uid") if isinstance(history[-1], Mapping) else None


def classify_migration(
    old_plan: Mapping[str, Any],
    new_plan: Mapping[str, Any],
    old_state: Mapping[str, Any],
    file_ledger: Mapping[str, Any],
    lineage: Any = None,
    *,
    from_version: str | None = None,
    to_version: str | None = None,
) -> dict[str, Any]:
    """Classify a complete Plan transition without inferring ancestry."""

    old_plan = copy.deepcopy(dict(old_plan))
    new_plan = copy.deepcopy(dict(new_plan))
    old_state = copy.deepcopy(dict(old_state))
    file_ledger = copy.deepcopy(dict(file_ledger))
    _validate_complete_inputs(old_plan, new_plan, old_state, file_ledger)
    old_by_uid, _ = _task_maps(old_plan)
    new_by_uid, _ = _task_maps(new_plan)
    old_state_by_id = {row["id"]: row for row in old_state["tasks"]}
    pairs = _task_pairs(old_plan, new_plan, lineage)
    task_rows: list[dict[str, Any]] = []
    classification_by_uid: dict[str, str] = {}
    for old_uids, new_uid in pairs:
        old_uid = old_uids[0] if old_uids else None
        old_task = old_by_uid.get(old_uid) if old_uid else None
        new_task = new_by_uid.get(new_uid) if new_uid else None
        source_tasks = [old_by_uid[uid] for uid in old_uids or ()]
        if len(source_tasks) <= 1:
            classification, reason = _classify_task(old_task, new_task, old_state_by_id)
        elif new_task is None:
            classification, reason = "REGENERATE", "TASK_ADDED_OR_REMOVED"
        elif any(old_state_by_id[source["id"]]["status"] != "done" for source in source_tasks):
            classification, reason = "REGENERATE", "OLD_TASK_NOT_DONE"
        elif set(new_task.get("deliverable_files", [])) - {path for source in source_tasks for path in source.get("deliverable_files", [])}:
            classification, reason = "REGENERATE", "NEW_DELIVERABLE_FILE"
        else:
            old_responsibilities = set().union(*(_responsibility_set(source) for source in source_tasks))
            if _jaccard(old_responsibilities, _responsibility_set(new_task)) < 0.5:
                classification, reason = "REGENERATE", "RESPONSIBILITY_JACCARD_BELOW_HALF"
            else:
                classification, reason = "AMEND", "EXPLICIT_MERGE"
        if new_uid:
            classification_by_uid[new_uid] = classification
        task_row = {
            "old_task_id": old_task.get("id") if old_task else None,
            "old_task_uid": old_uid,
            "new_task_id": new_task.get("id") if new_task else None,
            "new_task_uid": new_uid,
            "classification": classification,
            "reason": reason,
            "old_attempts": _old_attempts(old_state_by_id, old_task),
        }
        if len(old_uids or ()) > 1:
            task_row["merged_from"] = list(old_uids[1:])
            task_row["merged_from_ids"] = [old_by_uid[uid]["id"] for uid in old_uids[1:]]
        task_rows.append(task_row)
    new_owner_by_path = {
        path: task["task_uid"]
        for task in new_plan.get("tasks", [])
        for path in task.get("deliverable_files", [])
    }
    file_rows: list[dict[str, Any]] = []
    for row in file_ledger.get("files", []):
        if row.get("state") != "realized":
            continue
        old_owner = _file_owner_uid(row)
        new_owner = new_owner_by_path.get(row["path"])
        if new_owner is None:
            classification = "REGENERATE"
        elif old_owner is not None and old_owner in old_by_uid and new_owner in classification_by_uid:
            classification = classification_by_uid[new_owner]
            if old_owner != new_owner and classification == "INHERIT":
                classification = "REVALIDATE"
        else:
            classification = "REGENERATE"
        file_rows.append({"path": row["path"], "old_owner_uid": old_owner, "new_owner_uid": new_owner, "classification": classification})
    file_rows.sort(key=lambda row: _utf8(row["path"]))
    counts = {name.lower(): sum(row["classification"] == name for row in task_rows) for name in _CLASSIFICATIONS}
    preservation_denominator = len(file_rows)
    preserved = sum(row["classification"] in {"INHERIT", "REVALIDATE"} for row in file_rows)
    report = {
        "schema_version": "1.0",
        "from_version": from_version or old_state.get("plan_ref", {}).get("version") or _version_from_path(old_state.get("plan_ref", {}).get("path")),
        "to_version": to_version or (new_plan.get("plan_version") if new_plan.get("plan_version") else "1.0.1"),
        "counts": counts,
        "preservation_rate": 1.0 if preservation_denominator == 0 else preserved / preservation_denominator,
        "tasks": sorted(task_rows, key=lambda row: (_utf8(row["old_task_uid"] or ""), _utf8(row["new_task_uid"] or ""))),
        "files": file_rows,
    }
    errors = _schema_errors(report, "migration-report.schema.json")
    if errors:
        raise PlanRevisionError("migration report failed Schema validation: " + "; ".join(errors))
    return report


def _version_from_path(path: Any) -> str:
    if not isinstance(path, str):
        raise PlanRevisionError("Plan version is not bound by a versioned path")
    match = re.fullmatch(r"plan/versions/plan-(1\.\d+\.\d+)\.json", path)
    if not match:
        raise PlanRevisionError("Plan path is not an immutable version path")
    return match.group(1)


def validate_migration_report(
    report: Mapping[str, Any],
    old_plan: Mapping[str, Any],
    new_plan: Mapping[str, Any],
    old_state: Mapping[str, Any],
    file_ledger: Mapping[str, Any],
    lineage: Any = None,
) -> None:
    expected = classify_migration(old_plan, new_plan, old_state, file_ledger, lineage, from_version=report.get("from_version"), to_version=report.get("to_version"))
    if dict(report) != expected:
        raise PlanRevisionError("migration report does not equal deterministic recomputation")


def validate_file_ledger(ledger: Mapping[str, Any]) -> None:
    # Historical v1 ledgers remain readable by the historical revision
    # helpers.  Fresh-run publication and all S5 writers use v2 exclusively;
    # this branch is not an in-place migration path.
    if ledger.get("schema_version") == "1.0":
        if not isinstance(ledger.get("files"), list):
            raise PlanRevisionError("legacy file ledger files must be an array")
        paths = [row.get("path") for row in ledger["files"] if isinstance(row, Mapping)]
        if len(paths) != len(set(paths)):
            raise PlanRevisionError("file ledger paths must be unique")
        for row in ledger["files"]:
            if not isinstance(row, Mapping) or row.get("state") not in {"slot_only", "realized", "quarantined"}:
                raise PlanRevisionError("legacy file ledger row is invalid")
        return
    errors = _schema_errors(ledger, "file-ledger.schema.json")
    if errors:
        raise PlanRevisionError("file ledger failed Schema validation: " + "; ".join(errors))
    paths = [row["path"] for row in ledger.get("files", [])]
    if len(paths) != len(set(paths)):
        raise PlanRevisionError("file ledger paths must be unique")
    for row in ledger.get("files", []):
        state = row["state"]
        if state == "slot_only" and any(key in row for key in ("created_in_epoch", "content_sha256", "last_commit_sha", "verified_by", "owner_history", "created_by_stage", "epoch_receipt_ref", "quarantined_in_epoch", "quarantine_path")):
            raise PlanRevisionError("slot_only file rows cannot carry realization evidence")
        if state == "realized" and any(key in row for key in ("quarantined_in_epoch", "quarantine_path")):
            raise PlanRevisionError("realized file rows cannot carry quarantine evidence")


def _pointer_version(pointer: Mapping[str, Any]) -> tuple[int, int, int]:
    match = _VERSION_RE.fullmatch(str(pointer.get("version", "")))
    if match is None or pointer.get("path") != f"plan/versions/{'plan-' + pointer['version']}.json":
        raise PlanRevisionError("active pointer has an invalid immutable version binding")
    if not isinstance(pointer.get("sha256"), str) or re.fullmatch(r"[0-9a-f]{64}", pointer["sha256"]) is None:
        raise PlanRevisionError("active pointer has an invalid Plan hash")
    if not isinstance(pointer.get("revision_seq"), int) or pointer["revision_seq"] < 0:
        raise PlanRevisionError("active pointer has an invalid revision sequence")
    return (1, int(match.group(1)), int(match.group(2)))


def validate_plan_successor(old_pointer: Mapping[str, Any], new_pointer: Mapping[str, Any], level: str) -> None:
    old_c = _pointer_version(old_pointer)
    new_c = _pointer_version(new_pointer)
    if old_c[0] != 1 or new_c[0] != 1 or new_pointer.get("revision_seq") != old_pointer.get("revision_seq", 0) + 1:
        raise PlanRevisionError("revision sequence or commitment component is not a legal successor")
    old_epoch = _EPOCH_RE.fullmatch(str(old_pointer.get("epoch", "")))
    new_epoch = _EPOCH_RE.fullmatch(str(new_pointer.get("epoch", "")))
    if old_epoch is None or new_epoch is None:
        raise PlanRevisionError("active pointer epoch is invalid")
    if level == "F2":
        if new_c[:2] != old_c[:2] or new_c[2] != old_c[2] + 1 or new_epoch.group(1) != old_epoch.group(1):
            raise PlanRevisionError("F2 must increment P and preserve A and epoch")
    elif level == "F3":
        if new_c[1] != old_c[1] + 1 or new_c[2] != 0 or int(new_epoch.group(1)) != int(old_epoch.group(1)) + 1:
            raise PlanRevisionError("F3 must increment A, reset P, and advance epoch")
    else:
        raise PlanRevisionError("only F2 and F3 plan successors are supported")


def successor_pointer(old_pointer: Mapping[str, Any], new_plan_ref: Mapping[str, Any], level: str) -> dict[str, Any]:
    old_c = _pointer_version(old_pointer)
    epoch_match = _EPOCH_RE.fullmatch(str(old_pointer.get("epoch", "")))
    if epoch_match is None:
        raise PlanRevisionError("active pointer epoch is invalid")
    if level == "F2":
        version = f"1.{old_c[1]}.{old_c[2] + 1}"
        epoch = old_pointer["epoch"]
    elif level == "F3":
        version = f"1.{old_c[1] + 1}.0"
        epoch = f"E{int(epoch_match.group(1)) + 1}"
    else:
        raise PlanRevisionError("only F2 and F3 plan successors are supported")
    if not isinstance(new_plan_ref.get("sha256"), str) or re.fullmatch(r"[0-9a-f]{64}", new_plan_ref["sha256"]) is None:
        raise PlanRevisionError("new Plan ref has an invalid hash")
    pointer = {"version": version, "path": f"plan/versions/plan-{version}.json", "sha256": new_plan_ref["sha256"], "revision_seq": old_pointer.get("revision_seq", 0) + 1, "epoch": epoch}
    validate_plan_successor(old_pointer, pointer, level)
    return pointer


def _total_attempt_limit(config_snapshot: Mapping[str, Any] | None) -> int:
    budgets = (config_snapshot or {}).get("budgets", {})
    value = budgets.get("task_fix_attempts", 3) if isinstance(budgets, Mapping) else 3
    if not isinstance(value, int) or value < 0:
        raise PlanRevisionError("task_fix_attempts must be a non-negative integer")
    return value + 1


def _clean_state_row(task_id: str) -> dict[str, Any]:
    return {"id": task_id, "status": "pending", "attempts": 0, "notes": "", "commit_sha": None, "last_error": None, "acceptance_evidence": {"task_evidence_ref": None}}


def project_plan_state(
    old_state: Mapping[str, Any],
    new_plan: Mapping[str, Any],
    report: Mapping[str, Any],
    new_plan_ref: Mapping[str, Any],
    *,
    revalidation_proofs: Mapping[str, Any] | None = None,
    config_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project the complete State from one validated migration report."""

    old_state = copy.deepcopy(dict(old_state))
    new_plan = copy.deepcopy(dict(new_plan))
    if _schema_errors(old_state, "plan-state.schema.json") or _schema_errors(new_plan, "plan.schema.json"):
        raise PlanRevisionError("state projection inputs failed Schema validation")
    report = copy.deepcopy(dict(report))
    report_errors = _schema_errors(report, "migration-report.schema.json")
    if report_errors:
        raise PlanRevisionError("migration report failed Schema validation: " + "; ".join(report_errors))
    _validate_unique_task_uids(new_plan, "new Plan")
    report_tasks = report["tasks"]
    old_ids = {row["id"] for row in old_state["tasks"]}
    new_uids = {task["task_uid"] for task in new_plan["tasks"]}
    report_old_ids = [task_id for row in report_tasks for task_id in _row_old_ids(row)]
    report_new_uids = [row.get("new_task_uid") for row in report_tasks if row.get("new_task_uid") is not None]
    counts = {name.lower(): sum(row["classification"] == name for row in report_tasks) for name in _CLASSIFICATIONS}
    if counts != report["counts"]:
        raise PlanRevisionError("migration report counts do not match task rows")
    file_rows = report.get("files", [])
    if len(file_rows) != len({row["path"] for row in file_rows}):
        raise PlanRevisionError("migration report file rows are duplicated")
    preserved = sum(row["classification"] in {"INHERIT", "REVALIDATE"} for row in file_rows)
    expected_rate = 1.0 if not file_rows else preserved / len(file_rows)
    if report["preservation_rate"] != expected_rate:
        raise PlanRevisionError("migration report preservation rate does not match file rows")
    if (
        len(report_tasks) != len(set((row.get("old_task_uid"), row.get("new_task_uid")) for row in report_tasks))
        or set(report_old_ids) != old_ids
        or set(report_new_uids) != new_uids
        or len(report_new_uids) != len(set(report_new_uids))
        or len(report_tasks) < len(new_plan.get("tasks", []))
    ):
        raise PlanRevisionError("migration report is not a complete task-set projection")
    old_by_id = {row["id"]: row for row in old_state["tasks"]}
    new_by_uid = {task["task_uid"]: task for task in new_plan["tasks"]}
    rows: list[dict[str, Any]] = []
    proofs = revalidation_proofs or {}
    limit = _total_attempt_limit(config_snapshot)
    for task in new_plan["tasks"]:
        candidates = [row for row in report["tasks"] if row.get("new_task_uid") == task["task_uid"]]
        if len(candidates) != 1:
            raise PlanRevisionError("each new task must have exactly one migration row")
        migration = candidates[0]
        classification = migration["classification"]
        old = old_by_id.get(migration.get("old_task_id"))
        if classification == "INHERIT":
            if old is None:
                raise PlanRevisionError("INHERIT requires an old State row")
            row = copy.deepcopy(old)
            row["id"] = task["id"]
        elif classification == "REVALIDATE":
            proof = proofs.get(task["task_uid"])
            proof_passed = isinstance(proof, Mapping) and (
                proof.get("passed") is True
                or proof.get("build_passed") is True
                or proof.get("valid") is True
                or proof.get("status") in {"pass", "passed"}
            )
            if old is None or not proof_passed:
                raise PlanRevisionError("REVALIDATE requires typed successful build proof")
            row = copy.deepcopy(old)
            row["id"] = task["id"]
        elif classification == "AMEND":
            if old is None or old["status"] != "done" or old["attempts"] >= limit:
                raise PlanRevisionError("AMEND requires a completed task below the total attempt limit")
            row = _clean_state_row(task["id"])
            row.update({"attempts": old["attempts"], "notes": f"revision classification=AMEND task_uid={task['task_uid']}"})
        elif classification == "REGENERATE":
            row = _clean_state_row(task["id"])
        else:
            raise PlanRevisionError("migration report contains an unknown classification")
        rows.append(row)
    state = {"schema_version": "1.0", "plan_ref": copy.deepcopy(dict(new_plan_ref)), "tasks": rows}
    errors = _schema_errors(state, "plan-state.schema.json")
    if errors:
        raise PlanRevisionError("projected Plan State failed Schema validation: " + "; ".join(errors))
    return state


def project_file_ledger(
    old_ledger: Mapping[str, Any],
    new_plan: Mapping[str, Any],
    report: Mapping[str, Any],
    *,
    epoch: str,
    new_paths: set[str] | list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Project the complete active file set while retaining realized evidence."""

    validate_file_ledger(old_ledger)
    report = copy.deepcopy(dict(report))
    report_errors = _schema_errors(report, "migration-report.schema.json")
    if report_errors:
        raise PlanRevisionError("migration report failed Schema validation: " + "; ".join(report_errors))
    old_realized = {row["path"] for row in old_ledger.get("files", []) if row.get("state") == "realized"}
    report_file_rows = report.get("files", [])
    if {row["path"] for row in report_file_rows} != old_realized or len(report_file_rows) != len(old_realized):
        raise PlanRevisionError("migration report must account for every old realized file exactly once")
    preserved = sum(row["classification"] in {"INHERIT", "REVALIDATE"} for row in report_file_rows)
    expected_rate = 1.0 if not report_file_rows else preserved / len(report_file_rows)
    if report["preservation_rate"] != expected_rate:
        raise PlanRevisionError("migration report preservation rate does not match file rows")
    old_rows = {row["path"]: row for row in old_ledger.get("files", [])}
    if new_paths is not None and len(new_paths) != len(set(new_paths)):
        raise PlanRevisionError("new file paths must be unique")
    active_paths = set(new_paths) if new_paths is not None else {path for task in new_plan.get("tasks", []) for path in task.get("deliverable_files", [])}
    active_paths.update(row["path"] for row in old_ledger.get("files", []) if row.get("class") == "s5_frozen")
    new_owner_by_path = {
        path: (task["task_uid"], task["id"])
        for task in new_plan.get("tasks", [])
        for path in task.get("deliverable_files", [])
    }
    report_by_path = {row["path"]: row for row in report_file_rows}
    to_version = report["to_version"]
    result: list[dict[str, Any]] = []
    for path in sorted(active_paths, key=_utf8):
        old = old_rows.get(path)
        if old is not None:
            value = copy.deepcopy(old)
            owner = new_owner_by_path.get(path)
            if value.get("state") == "realized" and owner is not None and path in report_by_path:
                history = value.setdefault("owner_history", [])
                latest = history[-1] if history else None
                current_owner = {"plan_version": to_version, "task_uid": owner[0], "task_id": owner[1]}
                if latest != current_owner:
                    history.append(current_owner)
            result.append(value)
        else:
            result.append({"path": path, "class": "s6_owned", "state": "slot_only"})
    for path, old in sorted(old_rows.items(), key=lambda item: _utf8(item[0])):
        if path not in active_paths and old.get("state") == "realized":
            quarantined = copy.deepcopy(old)
            quarantined.update({"state": "quarantined", "quarantined_in_epoch": epoch, "quarantine_path": f"_orphan/{epoch}/{path}"})
            result.append(quarantined)
        elif path not in active_paths and old.get("state") == "quarantined":
            result.append(copy.deepcopy(old))
    ledger = {"schema_version": "2.0" if old_ledger.get("schema_version") == "2.0" else "1.0", "files": result}
    validate_file_ledger(ledger)
    return ledger


def _validate_revision_ledger_v1(ledger: Mapping[str, Any]) -> None:
    if ledger.get("schema_version") != "1.0" or not isinstance(ledger.get("entries"), list):
        raise PlanRevisionError("legacy revision ledger shape is invalid")
    required = {"revision_seq", "prev_entry_sha256", "from_plan_ref", "to_plan_ref", "from_version", "to_version", "level", "trigger", "trigger_signature", "patch_ops", "migration", "preservation_rate", "gates", "epoch_after", "activated_at_commit", "cost_usd"}
    for entry in ledger["entries"]:
        if not isinstance(entry, Mapping) or set(entry) != required:
            raise PlanRevisionError("legacy revision entry shape is invalid")
    previous = _ZERO_HASH
    expected_seq = 1
    previous_version = "1.0.0"
    previous_epoch = "E0"
    for entry in ledger.get("entries", []):
        if entry["revision_seq"] != expected_seq or entry["prev_entry_sha256"] != previous:
            raise PlanRevisionError("revision ledger sequence or predecessor hash is invalid")
        if entry["from_plan_ref"]["path"] != f"plan/versions/plan-{entry['from_version']}.json" or entry["to_plan_ref"]["path"] != f"plan/versions/plan-{entry['to_version']}.json":
            raise PlanRevisionError("revision entry Plan refs are not immutable version paths")
        if entry["from_version"] != previous_version:
            raise PlanRevisionError("revision entry does not continue the previous Plan version")
        validate_plan_successor(
            {"version": previous_version, "path": f"plan/versions/plan-{previous_version}.json", "sha256": entry["from_plan_ref"]["sha256"], "revision_seq": expected_seq - 1, "epoch": previous_epoch},
            {"version": entry["to_version"], "path": entry["to_plan_ref"]["path"], "sha256": entry["to_plan_ref"]["sha256"], "revision_seq": expected_seq, "epoch": entry["epoch_after"]},
            entry["level"],
        )
        if entry["trigger_signature"] != _sha(entry["trigger"]):
            raise PlanRevisionError("revision trigger signature drift")
        migration = entry["migration"]
        counts = {name: sum(row["classification"].lower() == name for row in migration["tasks"]) for name in ("inherit", "revalidate", "amend", "regenerate")}
        if counts != migration["counts"]:
            raise PlanRevisionError("revision migration task counts drift from rows")
        task_pairs = [(row["old_task_uid"], row["new_task_uid"]) for row in migration["tasks"]]
        source_uids = [uid for row in migration["tasks"] for uid in _row_old_uids(row)]
        new_uids = [row["new_task_uid"] for row in migration["tasks"] if row["new_task_uid"] is not None]
        if len(task_pairs) != len(set(task_pairs)) or len(new_uids) != len(set(new_uids)) or any(
            len(_row_old_uids(row)) > 1 and len(_row_old_uids(row)) != len(set(_row_old_uids(row)))
            for row in migration["tasks"]
        ):
            raise PlanRevisionError("revision migration task rows are duplicated")
        # A source uid may fan out only as a one-source split. Merged rows
        # must consume each source exactly once.
        for uid in set(source_uids):
            rows = [row for row in migration["tasks"] if uid in _row_old_uids(row)]
            if len(rows) > 1 and any(len(_row_old_uids(row)) != 1 for row in rows):
                raise PlanRevisionError("revision migration source lineage is duplicated")
        files = migration["files"]
        if len(files) != len({row["path"] for row in files}):
            raise PlanRevisionError("revision migration file rows are duplicated")
        denominator = len(files)
        preserved = sum(row["classification"] in {"INHERIT", "REVALIDATE"} for row in files)
        expected_rate = 1.0 if denominator == 0 else preserved / denominator
        if entry["preservation_rate"] != expected_rate:
            raise PlanRevisionError("revision preservation rate drifts from file rows")
        previous = _sha(entry)
        previous_version = entry["to_version"]
        previous_epoch = entry["epoch_after"]
        expected_seq += 1


_EVENT_TYPES = {
    "trigger_evaluated", "candidate_rejected", "revision_activated", "epoch_materialized",
    "lease_started", "lease_finished", "verification_committed", "revision_evaluated",
}


def _validate_revision_ledger_v2(ledger: Mapping[str, Any]) -> None:
    errors = _schema_errors(ledger, "revision-ledger.schema.json")
    if errors:
        raise PlanRevisionError("revision ledger failed Schema validation: " + "; ".join(errors))
    previous = _ZERO_HASH
    expected_seq = 1
    latest_revision = 0
    accepted_events: set[int] = set()
    activation_by_seq: dict[int, Mapping[str, Any]] = {}
    for entry in ledger.get("entries", []):
        if entry["event_seq"] != expected_seq or entry["prev_entry_sha256"] != previous:
            raise PlanRevisionError("revision ledger event sequence or predecessor hash is invalid")
        if entry["event_type"] not in _EVENT_TYPES:
            raise PlanRevisionError("revision ledger event type is unsupported")
        payload = entry["payload"]
        if entry["event_type"] in {"candidate_rejected", "epoch_materialized", "revision_activated"}:
            ref_events = []
            for key in ("trigger_event_seq",):
                if key in payload:
                    ref_events.append(payload[key])
            if entry["event_type"] == "epoch_materialized":
                if payload["revision_seq"] != latest_revision:
                    raise PlanRevisionError("epoch materialization revision sequence disagrees with latest activation")
            if any(not isinstance(value, int) or value >= entry["event_seq"] or value not in accepted_events for value in ref_events):
                raise PlanRevisionError("revision ledger event references a future or unaccepted event")
        if entry["event_type"] == "revision_activated":
            if payload["revision_seq"] != latest_revision + 1:
                raise PlanRevisionError("revision activation sequence is not consecutive")
            if payload["pending_materialization"] is False and payload.get("binding_ref") is None:
                raise PlanRevisionError("accepted activation without pending materialization requires a binding ref")
            latest_revision = payload["revision_seq"]
            activation_by_seq[latest_revision] = payload
        if entry["event_type"] == "epoch_materialized":
            if payload["revision_seq"] == 0 and latest_revision != 0:
                raise PlanRevisionError("E0 materialization cannot follow a revision activation")
        accepted_events.add(entry["event_seq"])
        previous = _sha(entry)
        expected_seq += 1


def validate_revision_ledger(ledger: Mapping[str, Any]) -> None:
    version = ledger.get("schema_version")
    if version == "1.0":
        _validate_revision_ledger_v1(ledger)
        return
    if version == "2.0":
        _validate_revision_ledger_v2(ledger)
        return
    raise PlanRevisionError("revision ledger schema version is unsupported")


def latest_activation(ledger: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return the most recent accepted activation payload, ignoring tails."""

    validate_revision_ledger(ledger)
    if ledger.get("schema_version") == "1.0":
        return ledger["entries"][-1] if ledger.get("entries") else None
    for entry in reversed(ledger.get("entries", [])):
        if entry.get("event_type") == "revision_activated":
            return entry["payload"]
    return None


def build_event_entry(ledger: Mapping[str, Any], event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if ledger.get("schema_version") != "2.0":
        raise PlanRevisionError("typed events require a v2 revision ledger")
    validate_revision_ledger(ledger)
    if event_type not in _EVENT_TYPES:
        raise PlanRevisionError(f"unsupported typed revision event {event_type!r}")
    entry = {
        "event_seq": len(ledger.get("entries", [])) + 1,
        "event_type": event_type,
        "prev_entry_sha256": _sha(ledger["entries"][-1]) if ledger.get("entries") else _ZERO_HASH,
        "payload": copy.deepcopy(dict(payload)),
    }
    errors = _schema_errors({"schema_version": "2.0", "entries": [entry]}, "revision-ledger.schema.json")
    if errors:
        raise PlanRevisionError("typed revision event failed Schema validation: " + "; ".join(errors))
    return entry


def append_epoch_materialized(
    ledger: Mapping[str, Any],
    *,
    epoch_receipt_ref: Mapping[str, Any],
    binding_ref: Mapping[str, Any],
    revision_seq: int = 0,
) -> dict[str, Any]:
    """Append the accepted E0 fact exactly once."""

    current = copy.deepcopy(dict(ledger))
    if current.get("schema_version") != "2.0":
        raise PlanRevisionError("epoch materialization requires a v2 revision ledger")
    validate_revision_ledger(current)
    payload = {"revision_seq": revision_seq, "epoch_receipt_ref": dict(epoch_receipt_ref), "binding_ref": dict(binding_ref)}
    for entry in current["entries"]:
        if entry.get("event_type") != "epoch_materialized":
            continue
        if entry.get("payload") == payload:
            return current
        if entry.get("payload", {}).get("revision_seq") == revision_seq:
            raise PlanRevisionError("conflicting epoch materialization fact")
    current["entries"].append(build_event_entry(current, "epoch_materialized", payload))
    validate_revision_ledger(current)
    return current


def append_revision_entry(ledger: Mapping[str, Any], entry: Mapping[str, Any]) -> dict[str, Any]:
    current = copy.deepcopy(dict(ledger))
    validate_revision_ledger(current)
    entries = current["entries"]
    candidate = copy.deepcopy(dict(entry))
    candidate.setdefault("revision_seq", len(entries) + 1)
    candidate.setdefault("prev_entry_sha256", _ZERO_HASH if not entries else _sha(entries[-1]))
    if candidate["revision_seq"] != len(entries) + 1 or candidate["prev_entry_sha256"] != (_ZERO_HASH if not entries else _sha(entries[-1])):
        raise PlanRevisionError("revision entry is not the unique append successor")
    entries.append(candidate)
    validate_revision_ledger(current)
    return current


def build_revision_entry(
    old_pointer: Mapping[str, Any],
    new_pointer: Mapping[str, Any],
    level: str,
    trigger: Mapping[str, Any],
    patch_ops: list[Mapping[str, Any]],
    migration: Mapping[str, Any],
    *,
    gates: Mapping[str, str],
    activated_at_commit: str,
    cost_usd: float = 0.0,
) -> dict[str, Any]:
    validate_plan_successor(old_pointer, new_pointer, level)
    migration_value = {key: copy.deepcopy(migration[key]) for key in ("counts", "tasks", "files")}
    entry = {"revision_seq": new_pointer["revision_seq"], "prev_entry_sha256": _ZERO_HASH, "from_plan_ref": {"path": old_pointer["path"], "sha256": old_pointer["sha256"]}, "to_plan_ref": {"path": new_pointer["path"], "sha256": new_pointer["sha256"]}, "from_version": old_pointer["version"], "to_version": new_pointer["version"], "level": level, "trigger": copy.deepcopy(dict(trigger)), "trigger_signature": _sha(trigger), "patch_ops": _sorted(patch_ops), "migration": migration_value, "preservation_rate": migration.get("preservation_rate", 1.0), "gates": copy.deepcopy(dict(gates)), "epoch_after": new_pointer["epoch"], "activated_at_commit": activated_at_commit, "cost_usd": cost_usd}
    return entry


__all__ = [
    "PlanRevisionError", "append_revision_entry", "build_revision_entry", "classify_migration", "project_file_ledger", "project_plan_state", "successor_pointer", "validate_file_ledger", "validate_migration_report", "validate_plan_successor", "validate_revision_ledger",
]
