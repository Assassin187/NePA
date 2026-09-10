"""Pure Plan revision classification, ledger, successor, and projection semantics."""

from __future__ import annotations

import copy
import hashlib
import re
from typing import Any, Mapping, Sequence

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


def validate_migration_extensions(
    migration: Mapping[str, Any], *, level: str, revision_seq: int
) -> None:
    """Validate canonical activation-bound group and re-adoption declarations."""

    groups = migration.get("pending_groups", [])
    re_adopt = migration.get("re_adopt", [])
    if level == "F2" and (groups or re_adopt):
        raise PlanRevisionError("F2 migration cannot contain structural group or re-adoption rows")
    if level not in {"F2", "F3"}:
        raise PlanRevisionError("migration extensions require an F2 or F3 activation")
    if not isinstance(groups, list) or not isinstance(re_adopt, list):
        raise PlanRevisionError("migration extension rows must be arrays")
    if groups != sorted(groups, key=lambda row: _utf8(row.get("group_id", "")) if isinstance(row, Mapping) else b""):
        raise PlanRevisionError("pending migration groups must be sorted by group_id")
    task_uids = {
        row["new_task_uid"]
        for row in migration.get("tasks", [])
        if isinstance(row, Mapping) and isinstance(row.get("new_task_uid"), str)
    }
    group_ids: list[str] = []
    claimed: dict[str, set[str]] = {
        "member_task_uids": set(), "affected_paths": set(),
        "affected_symbols": set(), "build_artifact_ids": set(),
    }
    for ordinal, group in enumerate(groups, 1):
        if not isinstance(group, Mapping):
            raise PlanRevisionError("pending migration group rows must be objects")
        expected_id = f"g-{revision_seq}-{ordinal}"
        if group.get("group_id") != expected_id:
            raise PlanRevisionError("pending migration group id is not canonical for its activation")
        group_ids.append(expected_id)
        for key in ("member_task_uids", "affected_paths", "affected_symbols", "build_artifact_ids"):
            values = group.get(key)
            if not isinstance(values, list) or values != sorted(set(values), key=_utf8):
                raise PlanRevisionError(f"pending migration group {key} must be sorted and unique")
        if not set(group.get("member_task_uids", [])) <= task_uids:
            raise PlanRevisionError("pending migration group references an unknown target task")
        for key, seen in claimed.items():
            overlap = seen & set(group.get(key, []))
            if overlap:
                raise PlanRevisionError("overlapping pending migration groups must be merged")
            seen.update(group.get(key, []))
    if len(group_ids) != len(set(group_ids)):
        raise PlanRevisionError("pending migration group ids are duplicated")
    if re_adopt != sorted(re_adopt, key=lambda row: (_utf8(row.get("quarantine_path", "")), _utf8(row.get("target_path", ""))) if isinstance(row, Mapping) else (b"", b"")):
        raise PlanRevisionError("re-adoption rows must be sorted by quarantine and target path")
    sources: list[str] = []
    targets: list[str] = []
    for row in re_adopt:
        if not isinstance(row, Mapping):
            raise PlanRevisionError("re-adoption rows must be objects")
        sources.append(str(row.get("quarantine_path")))
        targets.append(str(row.get("target_path")))
        owner = row.get("owner", {})
        if not isinstance(owner, Mapping) or owner.get("task_uid") not in task_uids:
            raise PlanRevisionError("re-adoption owner is not a target Plan task")
        matching = [item for item in migration.get("tasks", []) if item.get("new_task_uid") == owner.get("task_uid")]
        if len(matching) != 1 or matching[0].get("new_task_id") != owner.get("task_id"):
            raise PlanRevisionError("re-adoption owner id and uid disagree with migration tasks")
    if len(sources) != len(set(sources)) or len(targets) != len(set(targets)):
        raise PlanRevisionError("re-adoption source and target paths must be unique")


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
    if old_state["status"] in {"blocked", "blocked_by_dependency"} and old_task["obligation_digest"] == new_task["obligation_digest"] and old_task["task_uid"] == new_task["task_uid"]:
        return "INHERIT", "INCOMPLETE_STATE_PRESERVED"
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
    quarantine_paths: list[str] = []
    for row in ledger.get("files", []):
        state = row["state"]
        if state == "slot_only" and any(key in row for key in ("created_in_epoch", "content_sha256", "last_commit_sha", "verified_by", "owner_history", "created_by_stage", "epoch_receipt_ref", "quarantined_in_epoch", "quarantine_path")):
            raise PlanRevisionError("slot_only file rows cannot carry realization evidence")
        if state == "realized" and any(key in row for key in ("quarantined_in_epoch", "quarantine_path")):
            raise PlanRevisionError("realized file rows cannot carry quarantine evidence")
        if state == "realized":
            if row["class"] == "s5_frozen" and "owner_history" in row:
                raise PlanRevisionError("S5-frozen rows cannot carry S6 owner history")
            if row["class"] == "s6_owned":
                history = row.get("owner_history")
                if not isinstance(history, list) or not history:
                    raise PlanRevisionError("realized S6-owned rows require owner history")
                seen_owners: set[tuple[Any, Any, Any]] = set()
                for owner in history:
                    if not isinstance(owner, Mapping):
                        raise PlanRevisionError("S6 owner history contains a non-object row")
                    identity = (owner.get("plan_version"), owner.get("task_uid"), owner.get("task_id"))
                    if identity in seen_owners:
                        raise PlanRevisionError("S6 owner history repeats an identity")
                    seen_owners.add(identity)
            verified = row.get("verified_by")
            if isinstance(verified, Mapping) and verified.get("build_variant_ids") != sorted(set(verified.get("build_variant_ids", [])), key=lambda item: str(item).encode("utf-8")):
                raise PlanRevisionError("file verification variants are not sorted and unique")
        if state == "quarantined":
            if row["class"] != "s6_owned":
                raise PlanRevisionError("only realized S6-owned rows may be quarantined")
            quarantine = row.get("quarantine_path")
            quarantine_epoch = row.get("quarantined_in_epoch")
            if not isinstance(quarantine, str) or not isinstance(quarantine_epoch, str) or re.fullmatch(r"E[0-9]+", quarantine_epoch) is None or quarantine != f"_orphan/{quarantine_epoch}/{row['path']}":
                raise PlanRevisionError("quarantine path is not the canonical epoch-scoped path")
            if row.get("created_in_epoch") == quarantine_epoch:
                raise PlanRevisionError("a row cannot be created and retired in the same epoch")
            quarantine_paths.append(quarantine)
    if len(quarantine_paths) != len(set(quarantine_paths)):
        raise PlanRevisionError("file ledger quarantine paths must be unique")
    active_paths = {row["path"] for row in ledger.get("files", []) if row["state"] != "quarantined"}
    if active_paths & set(quarantine_paths):
        raise PlanRevisionError("active file paths cannot collide with quarantine paths")


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
    return {
        "id": task_id,
        "task_uid": "0" * 16,
        "status": "pending",
        "execution_mode": "normal",
        "attempts": 0,
        "amendment_used": 0,
        "notes": "",
        "commit_sha": None,
        "last_error": None,
        "acceptance_evidence": {"task_evidence_ref": None},
        "migration_ref": None,
        "group_id": None,
    }


def _dependency_ancestor_ids(plan: Mapping[str, Any], task_id: str) -> set[str]:
    tasks = {task["id"]: task for task in plan.get("tasks", [])}
    result: set[str] = set()
    pending = list(tasks.get(task_id, {}).get("depends_on", []))
    while pending:
        dependency = pending.pop()
        if dependency in result:
            continue
        result.add(dependency)
        pending.extend(tasks.get(dependency, {}).get("depends_on", []))
    return result


def project_plan_state(
    old_state: Mapping[str, Any],
    new_plan: Mapping[str, Any],
    report: Mapping[str, Any],
    new_plan_ref: Mapping[str, Any],
    *,
    activation_event_seq: int | None = None,
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
    migration_rows = [row for row in report_tasks if row.get("classification") != "INHERIT"]
    if migration_rows and (not isinstance(activation_event_seq, int) or isinstance(activation_event_seq, bool) or activation_event_seq < 1):
        raise PlanRevisionError("changed migration rows require the accepted activation event sequence")
    group_by_uid: dict[str, str] = {}
    for group in report.get("pending_groups", []):
        for task_uid in group.get("member_task_uids", []):
            if task_uid in group_by_uid:
                raise PlanRevisionError("migration task belongs to more than one pending group")
            group_by_uid[str(task_uid)] = str(group["group_id"])
    migration_ref = {"revision_seq": new_plan_ref["revision_seq"], "event_seq": activation_event_seq}
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
            if old is None or old["status"] != "done":
                raise PlanRevisionError("REVALIDATE requires a completed historical task")
            row = _clean_state_row(task["id"])
            row.update({"task_uid": task["task_uid"], "execution_mode": "revalidate", "attempts": old["attempts"], "notes": f"revision classification=REVALIDATE task_uid={task['task_uid']}", "migration_ref": migration_ref, "group_id": group_by_uid.get(task["task_uid"])})
        elif classification == "AMEND":
            if old is None or old["status"] != "done":
                raise PlanRevisionError("AMEND requires a completed historical task")
            row = _clean_state_row(task["id"])
            row.update({"task_uid": task["task_uid"], "execution_mode": "amend", "attempts": old["attempts"], "notes": f"revision classification=AMEND task_uid={task['task_uid']}", "migration_ref": migration_ref, "group_id": group_by_uid.get(task["task_uid"])})
        elif classification == "REGENERATE":
            row = _clean_state_row(task["id"])
            row.update({"task_uid": task["task_uid"], "migration_ref": migration_ref, "group_id": group_by_uid.get(task["task_uid"])})
        else:
            raise PlanRevisionError("migration report contains an unknown classification")
        rows.append(row)
    for row, task in zip(rows, new_plan["tasks"]):
        row["task_uid"] = task["task_uid"]
    row_by_id = {row["id"]: row for row in rows}
    for row in rows:
        if row.get("status") != "blocked_by_dependency":
            continue
        ancestors = _dependency_ancestor_ids(new_plan, row["id"])
        if not any(row_by_id.get(task_id, {}).get("status") in {"blocked", "blocked_by_dependency"} for task_id in ancestors):
            row.update({"status": "pending", "last_error": None, "commit_sha": None, "acceptance_evidence": {"task_evidence_ref": None}})
    evidence_counters = copy.deepcopy(old_state.get("evidence_counters", {}))
    for task_uid in new_uids:
        evidence_counters.setdefault(task_uid, 0)
    state = {
        "schema_version": "2.0",
        "plan_ref": copy.deepcopy(dict(new_plan_ref)),
        "tasks": rows,
        "evidence_counters": evidence_counters,
        "s6_attempts_used": max(old_state.get("s6_attempts_used", 0), sum(row.get("attempts", 0) for row in old_state.get("tasks", []))),
    }
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
        elif path not in active_paths and old.get("state") == "slot_only":
            # The activation ledger records the predecessor slot until the new
            # S5 epoch performs and receipts its physical retirement.
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
        validate_migration_extensions(migration, level=entry["level"], revision_seq=entry["revision_seq"])
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
    materialized_revisions: set[int] = set()
    verification_ids: set[str] = set()
    verification_evidence: set[tuple[str, str]] = set()
    lease_starts: dict[str, Mapping[str, Any]] = {}
    lease_finishes: set[str] = set()
    rejected_candidates: set[str] = set()
    for entry in ledger.get("entries", []):
        if entry["event_seq"] != expected_seq or entry["prev_entry_sha256"] != previous:
            raise PlanRevisionError("revision ledger event sequence or predecessor hash is invalid")
        if entry["event_type"] not in _EVENT_TYPES:
            raise PlanRevisionError("revision ledger event type is unsupported")
        payload = entry["payload"]
        if entry["event_type"] == "candidate_rejected":
            candidate_id = str(payload["candidate_id"])
            if candidate_id != f"candidate-{payload['trigger_event_seq']}" or candidate_id in rejected_candidates:
                raise PlanRevisionError("rejected candidate identity is invalid or duplicated")
            rejected_candidates.add(candidate_id)
        if entry["event_type"] == "lease_started":
            payload = entry["payload"]
            lease_id = payload["lease_id"]
            if lease_id != f"lease-{entry['event_seq']}" or lease_id in lease_starts:
                raise PlanRevisionError("lease identity is not deterministic or is duplicated")
            if payload.get("task_uid") in payload.get("leased_uids", []):
                raise PlanRevisionError("lease current task is also listed as a lender")
            if payload.get("leased_uids") != sorted(set(payload.get("leased_uids", [])), key=lambda item: str(item).encode("utf-8")):
                raise PlanRevisionError("lease lender uids are not sorted and unique")
            if payload.get("leased_paths") != sorted(set(payload.get("leased_paths", [])), key=lambda item: str(item).encode("utf-8")):
                raise PlanRevisionError("lease paths are not sorted and unique")
            if not payload.get("leased_paths") or len(payload["leased_paths"]) > 2:
                raise PlanRevisionError("lease external file count is outside the bounded range")
            lease_starts[lease_id] = payload
        if entry["event_type"] == "lease_finished":
            payload = entry["payload"]
            lease_id = payload["lease_id"]
            if lease_id not in lease_starts or lease_id in lease_finishes:
                raise PlanRevisionError("lease finish does not pair uniquely with a start")
            lease_finishes.add(lease_id)
            if payload.get("success") and (payload.get("joint_evidence_ref") is None or payload.get("commit_sha") is None or payload.get("reason") is not None):
                raise PlanRevisionError("successful lease finish is missing its joint proof")
            if not payload.get("success") and (not payload.get("reason") or payload.get("joint_evidence_ref") is not None or payload.get("commit_sha") is not None):
                raise PlanRevisionError("failed lease finish has an invalid conditional payload")
        if entry["event_type"] == "verification_committed":
            verification_id = payload["verification_id"]
            if verification_id in verification_ids:
                raise PlanRevisionError("revision ledger contains a duplicate verification identity")
            verification_ids.add(verification_id)
            kind = payload.get("kind")
            if kind == "normal" and (len(payload.get("member_uids", [])) != 1 or len(payload.get("evidence_refs", [])) != 1):
                raise PlanRevisionError("ordinary verification must bind exactly one member and evidence")
            minimum = 2 if kind == "lease" else 1
            if kind in {"lease", "group"} and (len(payload.get("member_uids", [])) < minimum or len(payload.get("member_uids", [])) != len(payload.get("evidence_refs", []))):
                raise PlanRevisionError(f"{kind} verification must bind every member and evidence")
            if payload.get("member_uids") != sorted(set(payload.get("member_uids", [])), key=lambda item: str(item).encode("utf-8")):
                raise PlanRevisionError("verification members are not sorted and unique")
            task_uid = payload["member_uids"][0]
            evidence_ref = payload["evidence_refs"][0]
            expected_id = f"v-{task_uid}-{_evidence_sequence(evidence_ref)}"
            if verification_id != expected_id:
                raise PlanRevisionError("verification identity disagrees with its member evidence")
            evidence_key = (evidence_ref["path"], evidence_ref["sha256"])
            if evidence_key in verification_evidence:
                raise PlanRevisionError("revision ledger reuses verification evidence")
            verification_evidence.add(evidence_key)
            if payload.get("revision_seq") != latest_revision:
                raise PlanRevisionError("verification revision sequence disagrees with latest activation")
            if kind == "lease":
                matching = [start for lease_id, start in lease_starts.items() if lease_id == payload.get("lease_id") and start.get("task_uid") == task_uid and set(start.get("leased_uids", [])) | {task_uid} == set(payload["member_uids"])]
                if len(matching) != 1:
                    raise PlanRevisionError("lease verification members do not match an accepted lease start")
                if payload.get("joint_evidence_ref") is None:
                    raise PlanRevisionError("lease verification is missing its joint evidence reference")
            if kind == "group":
                activation = activation_by_seq.get(payload.get("revision_seq"))
                groups = activation.get("migration", {}).get("pending_groups", []) if isinstance(activation, Mapping) else []
                matching = [group for group in groups if group.get("group_id") == payload.get("group_id")]
                if len(matching) != 1 or sorted(matching[0].get("member_task_uids", []), key=lambda value: str(value).encode("utf-8")) != payload.get("member_uids"):
                    raise PlanRevisionError("group verification members do not match the accepted activation")
                if payload.get("joint_evidence_ref") is None:
                    raise PlanRevisionError("group verification is missing its joint evidence reference")
        if entry["event_type"] in {"candidate_rejected", "epoch_materialized", "revision_activated"}:
            ref_events = []
            for key in ("trigger_event_seq",):
                if key in payload:
                    ref_events.append(payload[key])
            if entry["event_type"] == "epoch_materialized":
                if payload["revision_seq"] != latest_revision:
                    raise PlanRevisionError("epoch materialization revision sequence disagrees with latest activation")
                revision_seq = payload["revision_seq"]
                if revision_seq in materialized_revisions:
                    raise PlanRevisionError("revision ledger contains a duplicate epoch materialization")
                receipt_path = payload["epoch_receipt_ref"].get("path")
                binding_path = payload["binding_ref"].get("path")
                receipt_match = re.fullmatch(r"plan/epochs/(E[0-9]+)/receipt\.json", str(receipt_path))
                if receipt_match is None:
                    raise PlanRevisionError("epoch materialization receipt is not epoch scoped")
                if revision_seq == 0:
                    if receipt_match.group(1) != "E0" or binding_path != "plan/bindings/1.0.0/receipt.json":
                        raise PlanRevisionError("E0 materialization refs are not canonical")
                else:
                    activation = activation_by_seq.get(revision_seq)
                    if activation is None or activation.get("level") != "F3":
                        raise PlanRevisionError("E1+ materialization requires its accepted F3 activation")
                    if receipt_match.group(1) != activation.get("epoch_after") or binding_path != f"plan/bindings/{activation.get('to_version')}/receipt.json":
                        raise PlanRevisionError("epoch materialization refs disagree with its F3 activation")
                materialized_revisions.add(revision_seq)
            if any(not isinstance(value, int) or value >= entry["event_seq"] or value not in accepted_events for value in ref_events):
                raise PlanRevisionError("revision ledger event references a future or unaccepted event")
        if entry["event_type"] == "revision_activated":
            if payload["revision_seq"] != latest_revision + 1:
                raise PlanRevisionError("revision activation sequence is not consecutive")
            if payload["pending_materialization"] is False and payload.get("binding_ref") is None:
                raise PlanRevisionError("accepted activation without pending materialization requires a binding ref")
            binding_ref = payload.get("binding_ref")
            if payload["level"] == "F2":
                if not isinstance(binding_ref, Mapping) or binding_ref.get("path") != f"plan/bindings/{payload['to_version']}/receipt.json":
                    raise PlanRevisionError("F2 activation binding ref is not candidate-version scoped")
            elif binding_ref is not None or payload.get("pending_materialization") is not True:
                raise PlanRevisionError("F3 activation must remain pending without a binding ref")
            expected_rg5 = "not_applicable" if payload["level"] == "F2" else "pass"
            expected_gates = {"RG-1": "pass", "RG-2": "pass", "RG-3": "pass", "RG-4": "pass", "RG-5": expected_rg5}
            if payload.get("gates") != expected_gates:
                raise PlanRevisionError("revision activation does not bind all applicable passed gates")
            if f"candidate-{payload['trigger_event_seq']}" in rejected_candidates:
                raise PlanRevisionError("a rejected candidate cannot be activated")
            validate_migration_extensions(payload["migration"], level=payload["level"], revision_seq=payload["revision_seq"])
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


def validate_activation_binding(activation: Mapping[str, Any], binding: Mapping[str, Any] | None) -> None:
    """Validate the dormant binding reference of an already accepted activation."""

    level = activation.get("level")
    if level not in {"F2", "F3"}:
        raise PlanRevisionError("activation level is unsupported")
    if activation.get("pending_materialization"):
        if binding is not None:
            raise PlanRevisionError("pending activation cannot claim a completed binding")
        return
    if not isinstance(binding, Mapping):
        raise PlanRevisionError("completed activation is missing its binding")
    expected_plan = activation.get("to_plan_ref")
    expected_version = activation.get("to_version")
    expected_epoch = activation.get("epoch_after")
    if binding.get("plan_ref") != expected_plan:
        raise PlanRevisionError("activation binding Plan ref disagrees")
    receipt_path = binding.get("epoch_receipt_ref", {}).get("path") if isinstance(binding.get("epoch_receipt_ref"), Mapping) else None
    if receipt_path != f"plan/epochs/{expected_epoch}/receipt.json":
        raise PlanRevisionError("activation binding epoch disagrees")
    if binding.get("manifest_ref", {}).get("path") != f"plan/bindings/{expected_version}/artifact_manifest.json" or binding.get("contract_map_ref", {}).get("path") != f"plan/bindings/{expected_version}/contract_map.json":
        raise PlanRevisionError("activation binding version paths disagree")


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
    """Append one accepted epoch fact exactly once."""

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


def append_candidate_rejected(
    ledger: Mapping[str, Any],
    *,
    candidate_id: str,
    trigger_event_seq: int,
    level: str,
    failed_gate: str,
    reason: str,
    evidence_refs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Append one deterministic rejection or reuse its byte-equivalent fact."""

    current = copy.deepcopy(dict(ledger))
    validate_revision_ledger(current)
    payload = {
        "candidate_id": candidate_id, "trigger_event_seq": trigger_event_seq,
        "level": level, "failed_gate": failed_gate, "reason": reason,
        "evidence_refs": [copy.deepcopy(dict(ref)) for ref in evidence_refs],
    }
    matches = [entry for entry in current.get("entries", []) if entry.get("event_type") == "candidate_rejected" and entry.get("payload", {}).get("candidate_id") == candidate_id]
    if matches:
        if len(matches) != 1 or matches[0].get("payload") != payload:
            raise PlanRevisionError("conflicting candidate rejection fact")
        return current
    trigger = [entry for entry in current.get("entries", []) if entry.get("event_seq") == trigger_event_seq and entry.get("event_type") == "trigger_evaluated" and entry.get("payload", {}).get("selected") is True]
    if len(trigger) != 1 or trigger[0].get("payload", {}).get("route") != level:
        raise PlanRevisionError("candidate rejection has no matching selected trigger")
    current["entries"].append(build_event_entry(current, "candidate_rejected", payload))
    validate_revision_ledger(current)
    return current


def append_verification_committed(
    ledger: Mapping[str, Any],
    *,
    task_uid: str | None = None,
    evidence_ref: Mapping[str, Any] | None = None,
    commit_sha: str,
    revision_seq: int = 0,
    kind: str = "normal",
    members: list[Mapping[str, Any]] | None = None,
    lease_id: str | None = None,
    group_id: str | None = None,
    joint_evidence_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one idempotent normal, lease, or migration-group verification fact."""

    if ledger.get("schema_version") != "2.0":
        raise PlanRevisionError("ordinary verification requires a v2 revision ledger")
    current = copy.deepcopy(dict(ledger))
    validate_revision_ledger(current)
    if members is None:
        if task_uid is None or evidence_ref is None:
            raise PlanRevisionError("normal verification requires task_uid and evidence_ref")
        members = [{"task_uid": task_uid, "evidence_ref": dict(evidence_ref)}]
    normalized = sorted(({"task_uid": item["task_uid"], "evidence_ref": dict(item["evidence_ref"])} for item in members), key=lambda item: str(item["task_uid"]).encode("utf-8"))
    if kind not in {"normal", "lease", "group"} or (kind == "normal" and len(normalized) != 1) or (kind == "lease" and len(normalized) < 2) or (kind == "group" and not normalized):
        raise PlanRevisionError("verification kind and member cardinality are invalid")
    verification_id = f"v-{normalized[0]['task_uid']}-" + str(_evidence_sequence(normalized[0]["evidence_ref"]))
    payload = {
        "verification_id": verification_id,
        "kind": kind,
        "member_uids": [item["task_uid"] for item in normalized],
        "evidence_refs": [item["evidence_ref"] for item in normalized],
        "commit_sha": commit_sha,
        "revision_seq": revision_seq,
    }
    if kind == "lease":
        if not lease_id or group_id is not None or joint_evidence_ref is None:
            raise PlanRevisionError("lease verification requires lease_id and joint_evidence_ref")
        payload.update({"lease_id": lease_id, "joint_evidence_ref": dict(joint_evidence_ref)})
    elif kind == "group":
        if not group_id or lease_id is not None or joint_evidence_ref is None:
            raise PlanRevisionError("group verification requires group_id and joint_evidence_ref")
        payload.update({"group_id": group_id, "joint_evidence_ref": dict(joint_evidence_ref)})
    elif lease_id is not None or group_id is not None or joint_evidence_ref is not None:
        raise PlanRevisionError("normal verification cannot carry lease bindings")
    for entry in current["entries"]:
        if entry.get("event_type") != "verification_committed":
            continue
        existing = entry.get("payload", {})
        if existing.get("verification_id") == verification_id:
            if existing != payload:
                raise PlanRevisionError("conflicting verification identity")
            return current
    current["entries"].append(build_event_entry(current, "verification_committed", payload))
    validate_revision_ledger(current)
    return current


def append_lease_started(
    ledger: Mapping[str, Any],
    *,
    task_uid: str,
    leased_uids: list[str],
    leased_paths: list[str],
    baseline_commit: str,
    execution_count: int,
    authorization_ref: Mapping[str, Any],
) -> dict[str, Any]:
    """Append or replay the deterministic lease start event."""
    current = copy.deepcopy(dict(ledger))
    validate_revision_ledger(current)
    event_seq = len(current.get("entries", [])) + 1
    payload = {"lease_id": f"lease-{event_seq}", "task_uid": task_uid, "leased_uids": sorted(set(leased_uids), key=lambda item: item.encode("utf-8")), "leased_paths": sorted(set(leased_paths), key=lambda item: item.encode("utf-8")), "baseline_commit": baseline_commit, "execution_count": execution_count, "authorization_ref": dict(authorization_ref)}
    for entry in current["entries"]:
        if entry.get("event_type") != "lease_started":
            continue
        existing = entry.get("payload", {})
        if existing.get("lease_id") == payload["lease_id"]:
            if existing != payload:
                raise PlanRevisionError("conflicting lease identity")
            return current
        if all(existing.get(key) == payload.get(key) for key in ("task_uid", "leased_uids", "leased_paths", "baseline_commit", "execution_count", "authorization_ref")):
            return current
    current["entries"].append(build_event_entry(current, "lease_started", payload))
    validate_revision_ledger(current)
    return current


def append_lease_finished(
    ledger: Mapping[str, Any],
    *,
    lease_id: str,
    success: bool,
    reason: str | None,
    joint_evidence_ref: Mapping[str, Any] | None,
    commit_sha: str | None,
    call_refs: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Append or replay the unique conditional lease finish event."""
    current = copy.deepcopy(dict(ledger))
    validate_revision_ledger(current)
    payload = {"lease_id": lease_id, "success": success, "reason": reason, "joint_evidence_ref": dict(joint_evidence_ref) if joint_evidence_ref is not None else None, "commit_sha": commit_sha, "call_refs": [dict(ref) for ref in call_refs]}
    for entry in current["entries"]:
        if entry.get("event_type") == "lease_finished" and entry.get("payload", {}).get("lease_id") == lease_id:
            if entry.get("payload") != payload:
                raise PlanRevisionError("conflicting lease finish identity")
            return current
    current["entries"].append(build_event_entry(current, "lease_finished", payload))
    validate_revision_ledger(current)
    return current


def _evidence_sequence(ref: Mapping[str, Any]) -> int:
    path = ref.get("path", "")
    match = re.search(r"evidence_(\d+)\.json$", str(path))
    if match is None:
        raise PlanRevisionError("verification evidence path does not contain an evidence sequence")
    return int(match.group(1))


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
    errors = _schema_errors(migration, "migration-report.schema.json")
    if errors:
        raise PlanRevisionError("migration report failed Schema validation: " + "; ".join(errors))
    migration_value = {
        key: copy.deepcopy(migration[key])
        for key in ("counts", "tasks", "files", "pending_groups", "re_adopt")
        if key in migration
    }
    validate_migration_extensions(migration_value, level=level, revision_seq=new_pointer["revision_seq"])
    entry = {"revision_seq": new_pointer["revision_seq"], "prev_entry_sha256": _ZERO_HASH, "from_plan_ref": {"path": old_pointer["path"], "sha256": old_pointer["sha256"]}, "to_plan_ref": {"path": new_pointer["path"], "sha256": new_pointer["sha256"]}, "from_version": old_pointer["version"], "to_version": new_pointer["version"], "level": level, "trigger": copy.deepcopy(dict(trigger)), "trigger_signature": _sha(trigger), "patch_ops": _sorted(patch_ops), "migration": migration_value, "preservation_rate": migration.get("preservation_rate", 1.0), "gates": copy.deepcopy(dict(gates)), "epoch_after": new_pointer["epoch"], "activated_at_commit": activated_at_commit, "cost_usd": cost_usd}
    return entry


__all__ = [
    "PlanRevisionError", "append_candidate_rejected", "append_lease_finished", "append_lease_started", "append_revision_entry", "append_verification_committed", "build_revision_entry", "classify_migration", "project_file_ledger", "project_plan_state", "successor_pointer", "validate_activation_binding", "validate_file_ledger", "validate_migration_extensions", "validate_migration_report", "validate_plan_successor", "validate_revision_ledger",
]
