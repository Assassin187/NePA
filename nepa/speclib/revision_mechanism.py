"""Deterministic M1 revision trigger, patch, and candidate helpers."""

from __future__ import annotations

import copy
import hashlib
import posixpath
import re
from collections import defaultdict
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from ..schemas import load_schema
from .delivery import DeliveryConstraintError
from .lint import canonical_json_bytes
from .materialization import MaterializationError, build_artifact_manifest, build_contract_map, derive_rendering_view, render_e0_files
from .plan import PlanError, complete_plan_candidate, derive_task_metadata, normalize_plan_draft, plan_to_draft_ir
from .plan_revision import (
    PlanRevisionError,
    build_event_entry,
    classify_migration,
    validate_migration_extensions,
    validate_revision_ledger,
)


class RevisionMechanismError(ValueError):
    """A revision-mechanism input is stale, incomplete, or contradictory."""

    def __init__(self, message: str, *, code: str = "REVISION_MECHANISM_INVALID") -> None:
        self.code = code
        super().__init__(message)


_REVISION_LEVELS = {"F2", "F3"}
_ROUTE_LEVEL = {"F1": "F1", "F2": "F2", "F3": "F3", "F4": "F4"}


def _canonical(value: Any) -> Any:
    return copy.deepcopy(value)


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _schema_validate(value: Any, schema_name: str) -> None:
    errors = sorted(
        Draft202012Validator(load_schema(schema_name)).iter_errors(value),
        key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
    )
    if errors:
        detail = "; ".join(error.message for error in errors)
        raise RevisionMechanismError(f"{schema_name} validation failed: {detail}", code="REVISION_SCHEMA_INVALID")


def _safe_ref(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
        raise RevisionMechanismError(f"{label} must be a closed artifact reference", code="REVISION_BOUNDARY_INVALID")
    path, digest = value.get("path"), value.get("sha256")
    if not isinstance(path, str) or not path or path.startswith("/") or ".." in path.split("/"):
        raise RevisionMechanismError(f"{label} path is unsafe", code="REVISION_BOUNDARY_INVALID")
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise RevisionMechanismError(f"{label} hash is invalid", code="REVISION_BOUNDARY_INVALID")
    return {"path": path, "sha256": digest}


def _boundary_tasks(tasks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for task in tasks:
        row = {
            "task_uid": task.get("task_uid"),
            "mode": task.get("mode", "REGENERATE"),
            "ordinary_attempts_used": task.get("ordinary_attempts_used", task.get("attempts", 0)),
            "amendment_used": bool(task.get("amendment_used", False)),
            "status": task.get("status"),
        }
        projected.append(row)
    projected.sort(key=lambda item: str(item["task_uid"]).encode("utf-8"))
    if len({row["task_uid"] for row in projected}) != len(projected):
        raise RevisionMechanismError("boundary contains duplicate task uid", code="REVISION_BOUNDARY_INVALID")
    return projected


def project_revision_boundary(
    *,
    phase: str,
    revision_seq: int,
    tasks: Sequence[Mapping[str, Any]],
    plan_ref: Mapping[str, Any],
    epoch: str,
    state_history_ref: Mapping[str, Any],
    workspace_commit: str,
    workspace_tree: str,
    blueprint_ref: Mapping[str, Any],
    contract_map_ref: Mapping[str, Any],
    file_ledger_ref: Mapping[str, Any],
    revision_ledger: Mapping[str, Any],
    thresholds: Mapping[str, Any],
    facts: Mapping[str, Any] | None = None,
    in_flight: bool = False,
) -> dict[str, Any]:
    """Build the one stable S6 boundary projection defined by §5.6.7."""

    if phase not in {"task_boundary", "provider_submission"} or not isinstance(revision_seq, int) or revision_seq < 0:
        raise RevisionMechanismError("invalid revision boundary identity", code="REVISION_BOUNDARY_INVALID")
    if in_flight or any(task.get("status") == "running" for task in tasks):
        raise RevisionMechanismError("revision evaluation requires no work in flight", code="REVISION_BOUNDARY_IN_FLIGHT")
    if not isinstance(epoch, str) or not epoch.startswith("E") or not epoch[1:].isdigit():
        raise RevisionMechanismError("invalid epoch", code="REVISION_BOUNDARY_INVALID")
    if not isinstance(workspace_commit, str) or len(workspace_commit) != 40 or not isinstance(workspace_tree, str) or len(workspace_tree) != 40:
        raise RevisionMechanismError("workspace anchors must be git object ids", code="REVISION_BOUNDARY_INVALID")
    validate_revision_ledger(revision_ledger)
    theta_2, theta_6 = thresholds.get("theta_2"), thresholds.get("theta_6")
    if not isinstance(theta_2, (int, float)) or isinstance(theta_2, bool) or not 0 < theta_2 <= 1:
        raise RevisionMechanismError("revision thresholds must be frozen ratios", code="REVISION_THRESHOLD_INVALID")
    if not isinstance(theta_6, (int, float)) or isinstance(theta_6, bool) or not 0 < theta_6 <= 1:
        raise RevisionMechanismError("revision thresholds must be frozen ratios", code="REVISION_THRESHOLD_INVALID")
    return {
        "boundary_key": {"phase": phase, "revision_seq": revision_seq, "tasks": _boundary_tasks(tasks)},
        "plan_ref": _safe_ref(plan_ref, "plan_ref"),
        "epoch": epoch,
        "state_history_ref": _safe_ref(state_history_ref, "state_history_ref"),
        "workspace": {"commit": workspace_commit, "tree": workspace_tree},
        "blueprint_ref": _safe_ref(blueprint_ref, "blueprint_ref"),
        "contract_map_ref": _safe_ref(contract_map_ref, "contract_map_ref"),
        "file_ledger_ref": _safe_ref(file_ledger_ref, "file_ledger_ref"),
        "ledger_prefix_sha256": _sha(revision_ledger),
        "thresholds": {"theta_2": float(theta_2), "theta_6": float(theta_6)},
        "facts": _canonical(dict(facts or {})),
    }


def _refs(value: Any) -> list[dict[str, str]]:
    unique = {
        (ref["path"], ref["sha256"]): ref
        for item in (value or [])
        for ref in [_safe_ref(item, "evidence_ref")]
    }
    result = list(unique.values())
    result.sort(key=lambda item: (item["path"].encode("utf-8"), item["sha256"]))
    return result


def _anchors(fact: Mapping[str, Any], error_class: str) -> dict[str, Any]:
    def values(name: str) -> list[str]:
        return sorted({str(item) for item in fact.get(name, []) if str(item)}, key=lambda item: item.encode("utf-8"))

    paths = [posixpath.normpath(path.replace("\\", "/")) for path in values("paths")]
    lineage = values("lineage_uids") or [value for value in values("task_uids") if re.fullmatch(r"T-[0-9]{3}", value) is None]
    return {
        "obligation_uids": values("obligation_uids"),
        "lineage_uids": lineage,
        "paths": sorted(set(paths), key=lambda item: item.encode("utf-8")),
        "symbols": values("symbols") or ([str(fact["symbol"]).strip()] if fact.get("symbol") else []),
        "error_class": str(fact.get("error_class") or error_class).strip().lower().replace(" ", "_"),
    }


def _hit(code: str, route: str, fact: Mapping[str, Any], reason: str, error_class: str) -> dict[str, Any]:
    anchors = _anchors(fact, error_class)
    signature = _sha({"tr_code": code, **anchors})
    return {
        "code": code,
        "route": route,
        "level": _ROUTE_LEVEL.get(route),
        "signature": signature,
        "signature_anchors": anchors,
        "evidence_refs": _refs(fact.get("evidence_refs")),
        "selected": False,
        "reason": reason,
    }


def _trigger_hits(boundary: Mapping[str, Any]) -> list[dict[str, Any]]:
    facts = boundary.get("facts", {})
    hits: list[dict[str, Any]] = []
    undefined_by_symbol: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for fact in facts.get("undefined_symbols", []):
        symbol = str(fact.get("symbol", "")).strip()
        if symbol:
            undefined_by_symbol[symbol].append(fact)
    for symbol, rows in undefined_by_symbol.items():
        undeclared = [row for row in rows if row.get("declared") is not True]
        task_uids = {
            str(uid)
            for row in undeclared
            for uid in row.get("task_uids", [])
            if re.fullmatch(r"T-[0-9]{3}", str(uid)) is None
        }
        if not task_uids:
            continue
        fact = dict(undeclared[-1])
        fact.update({
            "symbol": symbol,
            "task_uids": sorted(task_uids, key=lambda value: value.encode("utf-8")),
            "evidence_refs": [ref for row in undeclared for ref in row.get("evidence_refs", [])],
        })
        if len(task_uids) >= 2 and all(row.get("consumes_closure_complete") is True for row in undeclared):
            hits.append(_hit("TR-1", "F3", fact, "multi-task undeclared symbol with complete consumes closure", "undeclared_symbol"))
        elif len(task_uids) == 1:
            hits.append(_hit("TR-1", "record_only", fact, "single-task undeclared-symbol diagnosis", "undeclared_symbol"))
    for fact in facts.get("blocked_providers", []):
        consumers = set(fact.get("consumer_task_uids", []))
        remaining = set(fact.get("remaining_incomplete_primary_task_uids", []))
        if fact.get("unique_provider") is True and fact.get("task_ready") is True and remaining and len(consumers & remaining) / len(remaining) >= boundary["thresholds"]["theta_2"]:
            hits.append(_hit("TR-2", "F2", fact, "blocked task-ready provider reaches theta_2", "blocked_provider"))
    grouped_rejections: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for fact in facts.get("write_rejections", []):
        grouped_rejections[(str(fact.get("task_uid")), str(fact.get("path")))].append(fact)
    for (_task_uid, _path), rows in grouped_rejections.items():
        if len(rows) < 2:
            continue
        fact = dict(rows[-1])
        fact.setdefault("task_uids", [fact.get("task_uid")])
        fact.setdefault("paths", [fact.get("path")])
        if fact.get("f1_eligible") is True and fact.get("f1_exhausted") is not True:
            hits.append(_hit("TR-3", "F1", fact, "eligible existing lease route", "foreign_owned_write"))
        elif fact.get("same_package_reassignment_legal") is True:
            hits.append(_hit("TR-3", "F2", fact, "same-package reassignment required", "foreign_owned_write"))
    truncations: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for fact in facts.get("truncations", []):
        truncations[str(fact.get("task_uid"))].append(fact)
    for task_uid, rows in truncations.items():
        if len(rows) >= 2:
            fact = dict(rows[-1]); fact.setdefault("task_uids", [task_uid])
            hits.append(_hit("TR-4", "F2", fact, "two accepted task truncations", "output_truncation"))
    for fact in facts.get("lint_output_overflows", []):
        if fact.get("projected_output_tokens", 0) > fact.get("frozen_output_tokens", 0) > 0:
            hits.append(_hit("TR-4", "F2", fact, "preflight full-lint output exceeds frozen budget", "lint_output_overflow"))
    gaps: dict[tuple[str, tuple[str, ...]], list[Mapping[str, Any]]] = defaultdict(list)
    for fact in facts.get("diagnoser_gaps", []):
        key = (str(fact.get("required_file")), tuple(sorted(set(fact.get("modules", [])))))
        gaps[key].append(fact)
    for rows in gaps.values():
        identities = {
            (ref["path"], ref["sha256"])
            for row in rows
            if isinstance(row.get("diagnoser_call_ref"), Mapping)
            for ref in [_safe_ref(row["diagnoser_call_ref"], "diagnoser_call_ref")]
        }
        fact = dict(rows[-1])
        if len(identities) >= 2 and fact.get("graph_confirmed") is True and fact.get("outside_writable_ready") is True:
            fact.setdefault("paths", [fact.get("required_file")])
            hits.append(_hit("TR-5", "record_only", fact, "independent graph-confirmed cross-module gap", "cross_module_gap"))
    blocked = set(facts.get("blocked_task_uids", [])) | set(facts.get("dependency_blocked_task_uids", []))
    all_tasks = set(facts.get("all_task_uids", []))
    if all_tasks and len(blocked & all_tasks) / len(all_tasks) >= boundary["thresholds"]["theta_6"]:
        hits.append(_hit("TR-6", "F2", {"task_uids": sorted(blocked & all_tasks), "evidence_refs": facts.get("blocked_evidence_refs", [])}, "blocked task ratio reaches theta_6", "blocked_ratio"))
    for fact in facts.get("missing_inputs", []):
        if fact.get("absent_from_blueprint") is True:
            route = "F3" if fact.get("fits_existing_architecture") is True else "F4"
            hits.append(_hit("TR-7", route, fact, "missing Blueprint input requires a legal slot" if route == "F3" else "missing input exceeds frozen architecture", "missing_blueprint_input"))
    if boundary["boundary_key"]["phase"] == "provider_submission":
        for fact in facts.get("export_drifts", []):
            if fact.get("matches_frozen_contract") is False:
                hits.append(_hit("TR-8", "submission_reject", fact, "provider export drift", "export_drift"))
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for hit in hits:
        marker = (hit["code"], hit["route"], hit["signature"])
        existing = unique.get(marker)
        if existing is None:
            unique[marker] = hit
        else:
            existing["evidence_refs"] = _refs(existing["evidence_refs"] + hit["evidence_refs"])
    return sorted(unique.values(), key=lambda item: (int(item["code"].split("-")[1]), item["route"].encode("utf-8"), item["signature"]))


def _attempted(ledger: Mapping[str, Any]) -> tuple[set[tuple[str, str]], set[str]]:
    rejected: set[tuple[str, str]] = set()
    activated: set[str] = set()
    trigger_by_seq = {entry["event_seq"]: entry["payload"] for entry in ledger.get("entries", []) if entry.get("event_type") == "trigger_evaluated"}
    for entry in ledger.get("entries", []):
        payload = entry.get("payload", {})
        if entry.get("event_type") == "candidate_rejected":
            trigger = trigger_by_seq.get(payload.get("trigger_event_seq"), {})
            signature = payload.get("trigger_signature") or trigger.get("hit_signature")
            if isinstance(signature, str):
                rejected.add((signature, str(payload.get("level"))))
        elif entry.get("event_type") == "revision_activated":
            signature = payload.get("trigger_signature")
            if isinstance(signature, str):
                activated.add(signature)
    return rejected, activated


def _validate_evaluation_semantics(evaluation: Mapping[str, Any]) -> None:
    hits = evaluation["hits"]
    ordered = sorted(
        hits,
        key=lambda item: (int(item["code"].split("-")[1]), item["route"].encode("utf-8"), item["signature"]),
    )
    identities = {(hit["code"], hit["route"], hit["signature"]) for hit in hits}
    if hits != ordered or len(identities) != len(hits):
        raise RevisionMechanismError("trigger evaluation hits are not canonical and unique", code="REVISION_EVALUATION_INVALID")
    selected = [hit for hit in hits if hit["selected"]]
    selection = evaluation.get("selection")
    if len(selected) > 1 or any(hit["route"] not in _REVISION_LEVELS for hit in selected):
        raise RevisionMechanismError("trigger evaluation has an invalid selected set", code="REVISION_EVALUATION_INVALID")
    expected = None if not selected else {
        "code": selected[0]["code"], "signature": selected[0]["signature"], "level": selected[0]["route"],
    }
    if selection != expected:
        raise RevisionMechanismError("trigger evaluation selection disagrees with its hit", code="REVISION_EVALUATION_INVALID")


def evaluate_revision_triggers(boundary: Mapping[str, Any], revision_ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate all M1 trigger hits, then choose one F2/F3 route deterministically."""

    validate_revision_ledger(revision_ledger)
    if boundary.get("ledger_prefix_sha256") != _sha(revision_ledger):
        raise RevisionMechanismError("revision-ledger prefix drift", code="REVISION_BOUNDARY_STALE")
    hits = _trigger_hits(boundary)
    rejected, activated = _attempted(revision_ledger)
    eligible = [
        hit for hit in hits
        if boundary.get("facts", {}).get("revision_ready", True) is not False
        and hit["route"] in _REVISION_LEVELS and hit["signature"] not in activated and (hit["signature"], hit["route"]) not in rejected
    ]
    eligible.sort(key=lambda item: (0 if item["route"] == "F2" else 1, int(item["code"].split("-")[1]), item["signature"]))
    selection = None
    if eligible:
        selected = eligible[0]
        selected["selected"] = True
        selection = {"code": selected["code"], "signature": selected["signature"], "level": selected["route"]}
    evaluation = {key: _canonical(boundary[key]) for key in ("boundary_key", "plan_ref", "epoch", "state_history_ref", "workspace", "blueprint_ref", "contract_map_ref", "file_ledger_ref", "ledger_prefix_sha256", "thresholds")}
    evaluation.update({"schema_version": "1.0", "hits": hits, "selection": selection})
    _schema_validate(evaluation, "revision-trigger-evaluation.schema.json")
    _validate_evaluation_semantics(evaluation)
    return evaluation


def append_trigger_batch(ledger: Mapping[str, Any], evaluation: Mapping[str, Any]) -> dict[str, Any]:
    """Return the one canonical idempotent ledger value for an evaluation batch."""

    _schema_validate(evaluation, "revision-trigger-evaluation.schema.json")
    _validate_evaluation_semantics(evaluation)
    validate_revision_ledger(ledger)
    if evaluation["ledger_prefix_sha256"] != _sha(ledger):
        existing = {
            (canonical_json_bytes(entry["payload"]["boundary_key"]), entry["payload"]["hit_code"], entry["payload"]["hit_signature"]): entry["payload"]
            for entry in ledger.get("entries", []) if entry.get("event_type") == "trigger_evaluated"
        }
        expected = []
        for hit in evaluation["hits"]:
            payload = {"boundary_key": evaluation["boundary_key"], "plan_ref": evaluation["plan_ref"], "hit_code": hit["code"], "route": hit["route"], "hit_signature": hit["signature"], "evidence_refs": hit["evidence_refs"], "selected": hit["selected"], "reason": hit["reason"]}
            expected.append(payload)
            current = existing.get((canonical_json_bytes(evaluation["boundary_key"]), hit["code"], hit["signature"]))
            if current != payload:
                raise RevisionMechanismError("trigger batch conflicts with the accepted ledger", code="REVISION_LEDGER_CONFLICT")
        if expected:
            return _canonical(dict(ledger))
        raise RevisionMechanismError("revision-ledger prefix drift", code="REVISION_BOUNDARY_STALE")
    if not evaluation["hits"]:
        return _canonical(dict(ledger))
    result = _canonical(dict(ledger))
    for hit in evaluation["hits"]:
        payload = {"boundary_key": evaluation["boundary_key"], "plan_ref": evaluation["plan_ref"], "hit_code": hit["code"], "route": hit["route"], "hit_signature": hit["signature"], "evidence_refs": hit["evidence_refs"], "selected": hit["selected"], "reason": hit["reason"]}
        result["entries"].append(build_event_entry(result, "trigger_evaluated", payload))
    validate_revision_ledger(result)
    return result


def _task_index(source_ir: Mapping[str, Any]) -> tuple[dict[str, tuple[dict[str, Any], dict[str, Any]]], dict[str, dict[str, Any]]]:
    by_uid: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    by_package: dict[str, dict[str, Any]] = {}
    for shard in source_ir.get("task_shards", []):
        by_package[shard["work_package_id"]] = shard
        for task in shard.get("tasks", []):
            uid = derive_task_metadata(task, work_package_id=shard["work_package_id"], local_task_id=task["local_id"])["task_uid"]
            if uid in by_uid:
                raise RevisionMechanismError("source IR contains duplicate task identity", code="REVISION_PATCH_SOURCE_INVALID")
            by_uid[uid] = (shard, task)
    return by_uid, by_package


def _remove_once(values: list[Any], target: Any, label: str) -> None:
    if values.count(target) != 1:
        raise RevisionMechanismError(f"{label} must exist exactly once", code="REVISION_PATCH_INVALID")
    values.remove(target)


def _validate_f3_companions(source_ir: Mapping[str, Any], operations: Sequence[Mapping[str, Any]]) -> None:
    """Reject F2 companions that do not touch the declared structural delta."""

    structural = [operation for operation in operations if operation["op"] in {
        "add_contract", "extend_contract", "add_file_slot", "add_work_package",
        "move_file_across_wp", "retire_file_slot", "re_adopt",
    }]
    if not structural:
        raise RevisionMechanismError("F3 patch has no structural delta", code="REVISION_PATCH_INVALID")
    by_uid, _by_package = _task_index(source_ir)
    package_ids: set[str] = set()
    task_uids: set[str] = set()
    paths: set[str] = set()
    contract_ids: set[str] = set()
    for operation in structural:
        for key in ("work_package_id", "provider_work_package_id", "from_work_package_id", "to_work_package_id"):
            if isinstance(operation.get(key), str):
                package_ids.add(operation[key])
        package_ids.update(str(value) for value in operation.get("consumer_work_package_ids", []))
        for key in ("owner_task_uid", "from_task_uid", "to_task_uid"):
            if isinstance(operation.get(key), str):
                task_uids.add(operation[key])
        for key in ("path", "quarantine_path"):
            if isinstance(operation.get(key), str):
                paths.add(operation[key])
        slot = operation.get("file_slot") or operation.get("target_slot")
        if isinstance(slot, Mapping) and isinstance(slot.get("path"), str):
            paths.add(slot["path"])
        contract = operation.get("contract")
        if isinstance(contract, Mapping) and isinstance(contract.get("id"), str):
            contract_ids.add(contract["id"])
        if isinstance(operation.get("contract_id"), str):
            contract_ids.add(operation["contract_id"])
        if operation["op"] == "retire_file_slot":
            for uid, (shard, task) in by_uid.items():
                if operation["path"] in task.get("deliverable_files", []):
                    task_uids.add(uid)
                    package_ids.add(shard["work_package_id"])
    for operation in operations:
        op = operation["op"]
        if operation in structural:
            continue
        touched_packages = {operation[key] for key in ("work_package_id",) if isinstance(operation.get(key), str)}
        touched_tasks = {operation[key] for key in ("task_uid", "source_task_uid", "from_task_uid", "to_task_uid") if isinstance(operation.get(key), str)}
        touched_paths = {operation[key] for key in ("path",) if isinstance(operation.get(key), str)}
        touched_contracts = {operation[key] for key in ("contract_id",) if isinstance(operation.get(key), str)}
        if not (
            touched_packages & package_ids
            or touched_tasks & task_uids
            or touched_paths & paths
            or touched_contracts & contract_ids
        ):
            raise RevisionMechanismError(
                f"F3 companion {op!r} does not close the same structural delta",
                code="REVISION_PATCH_INVALID",
            )


def apply_revision_patch(source_ir: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and atomically apply the closed F2/F3 semantic operation list."""

    _schema_validate(patch, "revision-patch.schema.json")
    if patch["source"]["plan_draft_ir_sha256"] != _sha(source_ir):
        raise RevisionMechanismError("patch source IR hash mismatch", code="REVISION_PATCH_SOURCE_INVALID")
    if patch["level"] == "F3":
        _validate_f3_companions(source_ir, patch["patch_ops"])
    result = _canonical(dict(source_ir))
    try:
        normalize_plan_draft(result["architecture"], result["work_packages"], result["task_shards"])
    except (KeyError, PlanError) as exc:
        raise RevisionMechanismError(f"invalid source PlanDraftIR: {exc}", code="REVISION_PATCH_SOURCE_INVALID") from exc
    old_uids = set(_task_index(result)[0])
    for operation in patch["patch_ops"]:
        by_uid, by_package = _task_index(result)
        op = operation["op"]
        if op == "rewrite_instructions":
            try:
                _shard, task = by_uid[operation["task_uid"]]
            except KeyError as exc:
                raise RevisionMechanismError("rewrite target is unknown", code="REVISION_PATCH_INVALID") from exc
            task.update(_canonical(operation["changes"]))
        elif op in {"move_responsibility", "move_file_owner"}:
            source = by_uid.get(operation["from_task_uid"]); target = by_uid.get(operation["to_task_uid"])
            if source is None or target is None or source[0]["work_package_id"] != operation["work_package_id"] or target[0]["work_package_id"] != operation["work_package_id"]:
                raise RevisionMechanismError("move must stay within the named work package", code="REVISION_PATCH_INVALID")
            field = "requirement_responsibilities" if op == "move_responsibility" else "deliverable_files"
            value = {"req_id": operation["req_id"], "role": operation["role"]} if op == "move_responsibility" else operation["path"]
            _remove_once(source[1][field], value, op)
            if value in target[1][field]:
                raise RevisionMechanismError("move would create duplicate ownership", code="REVISION_PATCH_INVALID")
            target[1][field].append(value)
        elif op == "insert_task":
            shard = by_package.get(operation["work_package_id"])
            source = by_uid.get(operation["from_task_uid"])
            if shard is None or source is None or source[0] is not shard:
                raise RevisionMechanismError("insert package is unknown", code="REVISION_PATCH_INVALID")
            inserted = _canonical(operation["task"])
            for path in inserted["deliverable_files"]:
                _remove_once(source[1]["deliverable_files"], path, op)
            for responsibility in inserted["requirement_responsibilities"]:
                _remove_once(source[1]["requirement_responsibilities"], responsibility, op)
            shard["tasks"].append(inserted)
        elif op == "split_task":
            source = by_uid.get(operation["source_task_uid"])
            if source is None or source[0]["work_package_id"] != operation["work_package_id"]:
                raise RevisionMechanismError("split source is unknown or outside package", code="REVISION_PATCH_INVALID")
            source[0]["tasks"].remove(source[1]); source[0]["tasks"].extend(_canonical(operation["successors"]))
        elif op == "merge_tasks":
            sources = [by_uid.get(uid) for uid in operation["source_task_uids"]]
            if any(item is None or item[0]["work_package_id"] != operation["work_package_id"] for item in sources):
                raise RevisionMechanismError("merge sources are unknown or outside package", code="REVISION_PATCH_INVALID")
            shard = by_package[operation["work_package_id"]]
            for item in sources:
                assert item is not None
                shard["tasks"].remove(item[1])
            shard["tasks"].append(_canonical(operation["successor"]))
        elif op == "reorder_dependency":
            source = by_uid.get(operation["from_task_uid"]); target = by_uid.get(operation["to_task_uid"])
            if source is None or target is None or source[0] is not target[0] or source[0]["work_package_id"] != operation["work_package_id"]:
                raise RevisionMechanismError("dependency endpoints must share the named package", code="REVISION_PATCH_INVALID")
            contract_id = operation["contract_id"]
            if contract_id not in source[1].get("provides_contracts", []) or contract_id not in target[1].get("consumes_contracts", []):
                raise RevisionMechanismError("dependency has no provider-consumer contract proof", code="REVISION_PATCH_INVALID")
            target[1]["depends_on"] = sorted(set(target[1].get("depends_on", [])) | {source[1]["local_id"]}, key=lambda value: value.encode("utf-8"))
        else:
            _apply_f3(result, operation, by_uid, by_package)
    try:
        normalized = normalize_plan_draft(result["architecture"], result["work_packages"], result["task_shards"])
    except (KeyError, PlanError) as exc:
        raise RevisionMechanismError(f"patched PlanDraftIR is not closed: {exc}", code="REVISION_PATCH_INVALID") from exc
    new_uids = set(_task_index(normalized)[0])
    lineage_by_old: dict[str, set[str]] = {}
    for row in patch["lineage"]:
        if row["old_task_uid"] in lineage_by_old:
            raise RevisionMechanismError("patch repeats an old task lineage", code="REVISION_PATCH_INVALID")
        lineage_by_old[row["old_task_uid"]] = set(row["new_task_uids"])
    for old_uid in old_uids - new_uids:
        successors = lineage_by_old.get(old_uid)
        if not successors or not successors <= new_uids:
            raise RevisionMechanismError("patch does not explicitly close changed task lineage", code="REVISION_PATCH_INVALID")
    return normalized


def _apply_f3(result: dict[str, Any], operation: Mapping[str, Any], by_uid: Mapping[str, Any], by_package: Mapping[str, Any]) -> None:
    architecture = result["architecture"]
    op = operation["op"]
    if op == "add_contract":
        if any(row["id"] == operation["contract"].get("id") for row in architecture.get("contracts", [])):
            raise RevisionMechanismError("contract already exists", code="REVISION_PATCH_INVALID")
        contract = _canonical(operation["contract"])
        contract_id = contract["id"]
        provider_package_id = operation["provider_work_package_id"]
        consumer_package_ids = set(operation["consumer_work_package_ids"])
        packages = {row["id"]: row for row in result["work_packages"]}
        architecture_packages = {row["id"]: row for row in architecture["work_packages"]}
        if provider_package_id not in packages or not consumer_package_ids <= set(packages):
            raise RevisionMechanismError("contract package binding is unknown", code="REVISION_PATCH_INVALID")
        provider_module_id = packages[provider_package_id]["module"]
        consumer_module_ids = {packages[item]["module"] for item in consumer_package_ids}
        if contract["ready_gate"] == "task":
            if provider_module_id != contract["provider"] or consumer_module_ids != set(contract["consumers"]):
                raise RevisionMechanismError("task-ready contract package bindings disagree", code="REVISION_PATCH_INVALID")
        elif contract["owner"] != "s5" or contract["provider"] != "s5" or {provider_module_id} | consumer_module_ids != set(contract["consumers"]):
            raise RevisionMechanismError("S5-ready contract package bindings disagree", code="REVISION_PATCH_INVALID")
        if not {row["interface_file"] for row in contract["exports"]} <= set(contract["interface_files"]):
            raise RevisionMechanismError("contract export is outside its interface files", code="REVISION_PATCH_INVALID")
        slot = next((row for row in architecture["layout"]["files"] if row["slot_id"] == operation["implementation_slot_id"]), None)
        if slot is None or not isinstance(slot.get("path"), str) or slot.get("class") != "s6_owned" or slot.get("owner_module") != provider_module_id:
            raise RevisionMechanismError("contract implementation slot is unknown", code="REVISION_PATCH_INVALID")
        provider_task = next((task for shard, task in by_uid.values() if shard["work_package_id"] == provider_package_id and slot["path"] in task["deliverable_files"]), None)
        if provider_task is None:
            raise RevisionMechanismError("contract implementation slot has no task owner", code="REVISION_PATCH_INVALID")
        if contract["ready_gate"] == "task":
            packages[provider_package_id]["provides_contracts"].append(contract_id)
            architecture_packages[provider_package_id]["provides_contracts"].append(contract_id)
            provider_task["provides_contracts"].append(contract_id)
            consumer_bindings = consumer_package_ids
        else:
            consumer_bindings = consumer_package_ids | {provider_package_id}
        for package_id in consumer_bindings:
            packages[package_id]["consumes_contracts"].append(contract_id)
            architecture_packages[package_id]["consumes_contracts"].append(contract_id)
            task = provider_task if package_id == provider_package_id else by_package[package_id]["tasks"][0]
            task["consumes_contracts"].append(contract_id)
        if contract["ready_gate"] == "task":
            provider_module = next(row for row in architecture["modules"] if row["id"] == contract["provider"])
            provider_module["provides_contracts"].append(contract_id)
        for module in architecture["modules"]:
            if module["id"] in contract["consumers"]:
                module["consumes_contracts"].append(contract_id)
        architecture["contracts"].append(contract)
    elif op == "extend_contract":
        contract = next((row for row in architecture.get("contracts", []) if row["id"] == operation["contract_id"]), None)
        if contract is None:
            raise RevisionMechanismError("contract is unknown", code="REVISION_PATCH_INVALID")
        old_keys = {(row["interface_file"], row["symbol"]) for row in contract["exports"]}
        new_keys = [(row.get("interface_file"), row.get("symbol")) for row in operation["exports"]]
        if len(new_keys) != len(set(new_keys)) or any(key in old_keys for key in new_keys):
            raise RevisionMechanismError("contract extension may not replace an export", code="REVISION_PATCH_INVALID")
        if not {row["interface_file"] for row in operation["exports"]} <= set(contract["interface_files"]):
            raise RevisionMechanismError("contract extension requires a declared interface file", code="REVISION_PATCH_INVALID")
        packages = {row["id"]: row for row in result["work_packages"]}
        provider_id = operation["provider_work_package_id"]
        consumer_ids = set(operation["consumer_work_package_ids"])
        if provider_id not in packages or not consumer_ids <= set(packages):
            raise RevisionMechanismError("contract extension package binding is unknown", code="REVISION_PATCH_INVALID")
        provider_module_id = packages[provider_id]["module"]
        consumer_modules = {packages[item]["module"] for item in consumer_ids}
        expected_consumers = ({provider_module_id} | consumer_modules) if contract["ready_gate"] == "s5" else consumer_modules
        if expected_consumers != set(contract["consumers"]) or (contract["ready_gate"] == "task" and provider_module_id != contract["provider"]):
            raise RevisionMechanismError("contract extension package bindings disagree", code="REVISION_PATCH_INVALID")
        for slot_id in operation["implementation_slot_ids"]:
            slot = next((row for row in architecture["layout"]["files"] if row["slot_id"] == slot_id), None)
            if slot is None or not isinstance(slot.get("path"), str) or slot.get("owner_module") != provider_module_id:
                raise RevisionMechanismError("contract extension implementation slot is invalid", code="REVISION_PATCH_INVALID")
            if not any(shard["work_package_id"] == provider_id and slot["path"] in task["deliverable_files"] for shard, task in by_uid.values()):
                raise RevisionMechanismError("contract extension slot has no provider task owner", code="REVISION_PATCH_INVALID")
        contract["exports"].extend(_canonical(operation["exports"]))
    elif op in {"add_file_slot", "re_adopt"}:
        slot = operation["file_slot"] if op == "add_file_slot" else operation["target_slot"]
        if any(row["slot_id"] == slot.get("slot_id") for row in architecture["layout"]["files"]):
            raise RevisionMechanismError("file slot already exists", code="REVISION_PATCH_INVALID")
        architecture["layout"]["files"].append(_canonical(slot))
        package_id = operation["work_package_id"]
        package = next((row for row in result["work_packages"] if row["id"] == package_id), None)
        owner = by_uid.get(operation["owner_task_uid"])
        path = slot.get("path")
        if package is None or owner is None or owner[0]["work_package_id"] != package_id or not isinstance(path, str) or slot.get("class") != "s6_owned":
            raise RevisionMechanismError("new slot owner binding is invalid", code="REVISION_PATCH_INVALID")
        if path not in package["allowed_files"]:
            package["allowed_files"].append(path)
        if path not in owner[1]["deliverable_files"]:
            owner[1]["deliverable_files"].append(path)
        architecture_package = next(row for row in architecture["work_packages"] if row["id"] == package_id)
        if path not in architecture_package["allowed_files"]:
            architecture_package["allowed_files"].append(path)
        module = next((row for row in architecture["modules"] if row["id"] == slot.get("owner_module")), None)
        if module is None:
            raise RevisionMechanismError("new slot names an unknown module", code="REVISION_PATCH_INVALID")
        if path not in module["owns_files"]:
            module["owns_files"].append(path)
        build_artifact_ids = set(operation["build_artifact_ids"])
        artifacts = {row["artifact_id"]: row for row in architecture["layout"]["build_graph"]["artifacts"]}
        if not build_artifact_ids <= set(artifacts):
            raise RevisionMechanismError("new slot names an unknown build artifact", code="REVISION_PATCH_INVALID")
        if slot.get("build_role") == "link_source":
            if not build_artifact_ids:
                raise RevisionMechanismError("link-source slot requires an explicit build artifact", code="REVISION_PATCH_INVALID")
            for artifact_id in build_artifact_ids:
                artifacts[artifact_id]["link_source_slots"].append(slot["slot_id"])
        elif build_artifact_ids:
            raise RevisionMechanismError("non-link-source slot may not alter build artifacts", code="REVISION_PATCH_INVALID")
        if slot.get("build_role") == "entry_point":
            raise RevisionMechanismError("adding an entry point requires an unavailable artifact operator", code="REVISION_PATCH_INVALID")
    elif op == "add_work_package":
        package = _canonical(operation["work_package"])
        if any(row["id"] == package.get("id") for row in result["work_packages"]):
            raise RevisionMechanismError("work package already exists", code="REVISION_PATCH_INVALID")
        if not any(module["id"] == package.get("module") for module in architecture["modules"]):
            raise RevisionMechanismError("new work package may not introduce a module", code="REVISION_PATCH_INVALID")
        owned_paths = {
            str(path)
            for old_package in result["work_packages"]
            for path in old_package.get("allowed_files", [])
        } | {
            str(path)
            for shard in result["task_shards"]
            for task in shard["tasks"]
            for path in task.get("deliverable_files", [])
        }
        if owned_paths & set(package["allowed_files"]):
            raise RevisionMechanismError("new work package may not implicitly move an owned file", code="REVISION_PATCH_INVALID")
        owned_responsibilities = {
            (str(row["req_id"]), str(row["role"]))
            for old_package in result["work_packages"]
            for row in old_package.get("requirement_responsibilities", [])
        } | {
            (str(row["req_id"]), str(row["role"]))
            for shard in result["task_shards"]
            for task in shard["tasks"]
            for row in task.get("requirement_responsibilities", [])
        }
        requested_responsibilities = {
            (str(row["req_id"]), str(row["role"]))
            for row in package["requirement_responsibilities"]
        }
        if owned_responsibilities & requested_responsibilities:
            raise RevisionMechanismError("new work package may not implicitly move a responsibility", code="REVISION_PATCH_INVALID")
        result["work_packages"].append(package); architecture["work_packages"].append(_canonical(package))
        result["task_shards"].append({"schema_version": "1.0", "work_package_id": package["id"], "tasks": _canonical(operation["tasks"])})
    elif op == "move_file_across_wp":
        source = by_uid.get(operation["from_task_uid"]); target = by_uid.get(operation["to_task_uid"])
        old_package = next((row for row in result["work_packages"] if row["id"] == operation["from_work_package_id"]), None)
        new_package = next((row for row in result["work_packages"] if row["id"] == operation["to_work_package_id"]), None)
        if source is None or target is None or old_package is None or new_package is None:
            raise RevisionMechanismError("cross-package move binding is invalid", code="REVISION_PATCH_INVALID")
        if old_package["module"] != new_package["module"]:
            raise RevisionMechanismError("cross-package move may not cross a module boundary", code="REVISION_PATCH_INVALID")
        _remove_once(source[1]["deliverable_files"], operation["path"], op); _remove_once(old_package["allowed_files"], operation["path"], op)
        target[1]["deliverable_files"].append(operation["path"]); new_package["allowed_files"].append(operation["path"])
        architecture_old = next(row for row in architecture["work_packages"] if row["id"] == operation["from_work_package_id"])
        architecture_new = next(row for row in architecture["work_packages"] if row["id"] == operation["to_work_package_id"])
        _remove_once(architecture_old["allowed_files"], operation["path"], op); architecture_new["allowed_files"].append(operation["path"])
        for req_id in operation["responsibility_ids"]:
            responsibility = next((row for row in source[1]["requirement_responsibilities"] if row["req_id"] == req_id), None)
            if responsibility is None:
                raise RevisionMechanismError("cross-package responsibility is not owned by source", code="REVISION_PATCH_INVALID")
            _remove_once(source[1]["requirement_responsibilities"], responsibility, op)
            _remove_once(old_package["requirement_responsibilities"], responsibility, op)
            _remove_once(architecture_old["requirement_responsibilities"], responsibility, op)
            target[1]["requirement_responsibilities"].append(responsibility)
            new_package["requirement_responsibilities"].append(responsibility)
            architecture_new["requirement_responsibilities"].append(responsibility)
        for contract_id in operation["provider_contract_ids"]:
            _remove_once(source[1]["provides_contracts"], contract_id, op)
            _remove_once(old_package["provides_contracts"], contract_id, op)
            _remove_once(architecture_old["provides_contracts"], contract_id, op)
            target[1]["provides_contracts"].append(contract_id)
            new_package["provides_contracts"].append(contract_id)
            architecture_new["provides_contracts"].append(contract_id)
    elif op == "retire_file_slot":
        matches = [row for row in architecture["layout"]["files"] if row["slot_id"] == operation["slot_id"] and row.get("path") == operation["path"]]
        if len(matches) != 1:
            raise RevisionMechanismError("retirement slot is unknown", code="REVISION_PATCH_INVALID")
        if any(
            operation["path"] in contract.get("interface_files", [])
            or any(export.get("interface_file") == operation["path"] for export in contract.get("exports", []))
            for contract in architecture.get("contracts", [])
        ):
            raise RevisionMechanismError("retirement may not remove a published interface", code="REVISION_PATCH_INVALID")
        architecture["layout"]["files"].remove(matches[0])
        for artifact in architecture["layout"]["build_graph"]["artifacts"]:
            if artifact["entry_file_slot"] == operation["slot_id"]:
                raise RevisionMechanismError("retirement leaves a build entry dangling", code="REVISION_PATCH_INVALID")
            artifact["link_source_slots"] = [slot for slot in artifact["link_source_slots"] if slot != operation["slot_id"]]
        for package in result["work_packages"]:
            if operation["path"] in package["allowed_files"]:
                package["allowed_files"].remove(operation["path"])
        for package in architecture["work_packages"]:
            if operation["path"] in package["allowed_files"]:
                package["allowed_files"].remove(operation["path"])
        for module in architecture["modules"]:
            if operation["path"] in module["owns_files"]:
                module["owns_files"].remove(operation["path"])
        for shard in result["task_shards"]:
            for task in shard["tasks"]:
                if operation["path"] in task["deliverable_files"]:
                    task["deliverable_files"].remove(operation["path"])
        remaining_slots = {row["slot_id"] for row in architecture["layout"]["files"]}
        if not set(operation["successor_slot_ids"]) <= remaining_slots:
            raise RevisionMechanismError("retirement successor slot is unknown", code="REVISION_PATCH_INVALID")
        remaining_requirements = {
            responsibility["req_id"]
            for shard in result["task_shards"]
            for task in shard["tasks"]
            for responsibility in task.get("requirement_responsibilities", [])
        }
        if not set(operation["successor_obligations"]) <= remaining_requirements:
            raise RevisionMechanismError("retirement obligation has no successor", code="REVISION_PATCH_INVALID")
        remaining_symbols = {
            export["symbol"]
            for contract in architecture.get("contracts", [])
            for export in contract.get("exports", [])
        }
        if not set(operation["successor_symbols"]) <= remaining_symbols:
            raise RevisionMechanismError("retirement symbol has no successor", code="REVISION_PATCH_INVALID")
    else:
        raise RevisionMechanismError(f"unsupported patch operation {op!r}", code="REVISION_PATCH_INVALID")


def validate_revision_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Validate closed candidate shape and event-scoped identity bindings."""

    value = _canonical(dict(candidate))
    _schema_validate(value, "revision-candidate.schema.json")
    if value["candidate_id"] != f"candidate-{value['selected_event_seq']}":
        raise RevisionMechanismError("candidate id does not match selected event", code="REVISION_CANDIDATE_BINDING_INVALID")
    ref_keys = {
        "patch_ref", "plan_draft_ir_ref", "candidate_plan_ref", "blueprint_ref", "manifest_ref",
        "contract_map_ref", "lineage_ref", "obligation_mapping_ref", "migration_ref",
        "lint_report_ref", "invariant_report_ref",
    }
    refs = {key: _safe_ref(value[key], key) for key in ref_keys}
    paths = [ref["path"] for ref in refs.values()]
    if len(paths) != len(set(paths)) or any("/" in path or path in {".", "..", "candidate.json"} for path in paths):
        raise RevisionMechanismError("candidate artifact refs must be unique flat filenames", code="REVISION_CANDIDATE_BINDING_INVALID")
    if set(value["content_hashes"]) != set(paths):
        raise RevisionMechanismError("candidate content-hash set does not equal its refs", code="REVISION_CANDIDATE_HASH_INVALID")
    for key, ref in refs.items():
        path = ref["path"]
        digest = ref["sha256"]
        if value["content_hashes"].get(path) != digest:
            raise RevisionMechanismError(f"candidate content hash missing for {path}", code="REVISION_CANDIDATE_HASH_INVALID")
    return value


def _primary_owners(plan: Mapping[str, Any]) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = defaultdict(list)
    for task in plan.get("tasks", []):
        for responsibility in task.get("requirement_responsibilities", []):
            if responsibility.get("role") == "primary":
                owners[str(responsibility["req_id"])].append(str(task["task_uid"]))
    return dict(owners)


def _task_obligations(task: Mapping[str, Any]) -> set[str]:
    obligations = {
        f"req:{row['req_id']}:{row['role']}"
        for row in task.get("requirement_responsibilities", [])
    }
    obligations.update(f"file:{path}" for path in task.get("deliverable_files", []))
    obligations.update(f"contract:provide:{value}" for value in task.get("provides_contracts", []))
    obligations.update(f"contract:consume:{value}" for value in task.get("consumes_contracts", []))
    obligations.update(f"build:{value}" for value in task.get("acceptance", {}).get("build_variant_ids", []))
    obligations.update(f"test:{value}" for value in task.get("acceptance", {}).get("tests", []))
    return obligations


def _validate_obligation_preservation(
    source_plan: Mapping[str, Any],
    candidate_plan: Mapping[str, Any],
    lineage: Sequence[Mapping[str, Any]],
    mappings: Sequence[Mapping[str, Any]],
    patch_ops: Sequence[Mapping[str, Any]],
) -> None:
    old_tasks = {str(task["task_uid"]): task for task in source_plan.get("tasks", [])}
    new_tasks = {str(task["task_uid"]): task for task in candidate_plan.get("tasks", [])}
    lineage_by_old = {str(row["old_task_uid"]): set(row["new_task_uids"]) for row in lineage}
    mapping_by_old: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for row in mappings:
        old = (str(row["old_task_uid"]), str(row["old_obligation"]))
        if old in mapping_by_old:
            raise RevisionMechanismError("candidate repeats an old obligation mapping", code="REVISION_INV3_INVALID")
        mapping_by_old[old] = {
            (str(target["new_task_uid"]), str(target["new_obligation"]))
            for target in row["new_targets"]
        }
    for (old_uid, old_obligation), targets in mapping_by_old.items():
        if old_uid not in old_tasks or old_obligation not in _task_obligations(old_tasks[old_uid]):
            raise RevisionMechanismError("candidate obligation mapping names an unknown source obligation", code="REVISION_INV3_INVALID")
        if old_uid in new_tasks and old_obligation in _task_obligations(new_tasks[old_uid]):
            raise RevisionMechanismError("candidate maps an obligation that remains on its original task", code="REVISION_INV3_INVALID")
        if any(uid not in new_tasks or obligation not in _task_obligations(new_tasks[uid]) for uid, obligation in targets):
            raise RevisionMechanismError("candidate obligation mapping names an unknown target obligation", code="REVISION_INV3_INVALID")

    explicit_moves: dict[tuple[str, str], set[str]] = defaultdict(set)
    for operation in patch_ops:
        op = operation.get("op")
        if op == "move_responsibility":
            explicit_moves[(str(operation["from_task_uid"]), f"req:{operation['req_id']}:{operation['role']}")].add(str(operation["to_task_uid"]))
        elif op in {"move_file_owner", "move_file_across_wp"}:
            explicit_moves[(str(operation["from_task_uid"]), f"file:{operation['path']}")].add(str(operation["to_task_uid"]))
        elif op == "insert_task":
            inserted = next((task for task in new_tasks.values() if task.get("work_package") == operation["work_package_id"] and task.get("local_task_id") == operation["task"]["local_id"]), None)
            if inserted is not None:
                for obligation in _task_obligations(inserted):
                    explicit_moves[(str(operation["from_task_uid"]), obligation)].add(str(inserted["task_uid"]))
    for old_uid, old_task in old_tasks.items():
        successors = {old_uid} if old_uid in new_tasks else lineage_by_old.get(old_uid, set())
        if not successors or not successors <= set(new_tasks):
            raise RevisionMechanismError("candidate lacks explicit task lineage", code="REVISION_INV3_INVALID")
        old_obligations = _task_obligations(old_task)
        same_task_obligations = _task_obligations(new_tasks[old_uid]) if old_uid in new_tasks else set()
        for missing in old_obligations - same_task_obligations:
            mapped_targets = mapping_by_old.get((old_uid, missing), set())
            if not mapped_targets:
                raise RevisionMechanismError("candidate omits an old obligation mapping", code="REVISION_INV3_INVALID")
            legal_uids = successors | explicit_moves.get((old_uid, missing), set())
            if any(uid not in legal_uids for uid, _obligation in mapped_targets):
                raise RevisionMechanismError("candidate maps an obligation to an unrelated task", code="REVISION_INV3_INVALID")

    old_id_to_uid = {str(task["id"]): uid for uid, task in old_tasks.items()}
    new_id_to_uid = {str(task["id"]): uid for uid, task in new_tasks.items()}
    new_tests = {str(row["nodeid"]): row for row in candidate_plan.get("coverage", {}).get("tests", [])}
    gate_rank = {"s5": 0, "task": 1, "s7_only": 2}
    for old_test in source_plan.get("coverage", {}).get("tests", []):
        nodeid = str(old_test["nodeid"])
        new_test = new_tests.get(nodeid)
        if (
            new_test is None
            or new_test.get("enabled") is not True
            or gate_rank.get(str(new_test.get("gate")), 10) > gate_rank.get(str(old_test.get("gate")), 10)
        ):
            raise RevisionMechanismError("candidate removes, disables, or postpones an acceptance gate", code="REVISION_INV3_INVALID")
        old_task_uid = old_id_to_uid.get(str(old_test.get("task_id")))
        new_task_uid = new_id_to_uid.get(str(new_test.get("task_id")))
        if old_task_uid is not None and new_task_uid is not None and old_task_uid != new_task_uid:
            declared = mapping_by_old.get((old_task_uid, f"test:{nodeid}"), set())
            legal = lineage_by_old.get(old_task_uid, set()) | explicit_moves.get((old_task_uid, f"test:{nodeid}"), set())
            if new_task_uid not in legal or (new_task_uid, f"test:{nodeid}") not in declared:
                raise RevisionMechanismError("candidate postpones an acceptance test without explicit legal lineage", code="REVISION_INV3_INVALID")


def _validate_file_migration_declarations(
    patch: Mapping[str, Any],
    file_ledger: Mapping[str, Any],
) -> None:
    rows = [row for row in file_ledger.get("files", []) if isinstance(row, Mapping)]
    for operation in patch["patch_ops"]:
        if operation["op"] == "retire_file_slot":
            realized = next((row for row in rows if row.get("path") == operation["path"] and row.get("state") == "realized"), None)
            if realized is not None and not operation["quarantine_path"].endswith("/" + operation["path"]):
                raise RevisionMechanismError("retirement quarantine path does not preserve source identity", code="REVISION_MIGRATION_INVALID")
        elif operation["op"] == "re_adopt":
            source = next((row for row in rows if row.get("state") == "quarantined" and row.get("quarantine_path") == operation["quarantine_path"]), None)
            if source is None or source.get("content_sha256") != operation["content_sha256"]:
                raise RevisionMechanismError("re-adoption source is not registered with matching content", code="REVISION_MIGRATION_INVALID")
            target_path = operation["target_slot"].get("path")
            if any(row.get("state") != "quarantined" and row.get("path") == target_path for row in rows):
                raise RevisionMechanismError("re-adoption target collides with an active ledger path", code="REVISION_MIGRATION_INVALID")


def _migration_extensions(
    patch: Mapping[str, Any],
    candidate_plan: Mapping[str, Any],
    blueprint: Mapping[str, Any],
    migration: dict[str, Any],
    *,
    revision_seq: int,
) -> None:
    if patch["level"] != "F3":
        return
    slots = {
        row["slot_id"]: row.get("path")
        for row in candidate_plan.get("architecture", {}).get("layout", {}).get("files", [])
        if isinstance(row, Mapping)
    }
    affected_paths: set[str] = set()
    affected_symbols: set[str] = set()
    affected_contracts: set[str] = set()
    symbol_seed_uids: dict[str, set[str]] = defaultdict(set)
    re_adopt: list[dict[str, Any]] = []
    tasks_by_uid = {str(task["task_uid"]): task for task in candidate_plan.get("tasks", [])}
    for operation in patch["patch_ops"]:
        op = operation["op"]
        if op == "add_file_slot":
            path = operation["file_slot"].get("path")
            if isinstance(path, str):
                affected_paths.add(path)
        elif op == "add_work_package":
            affected_paths.update(operation["work_package"]["allowed_files"])
        elif op == "move_file_across_wp":
            affected_paths.add(operation["path"])
        elif op == "retire_file_slot":
            affected_paths.update(str(slots[slot_id]) for slot_id in operation["successor_slot_ids"] if isinstance(slots.get(slot_id), str))
            affected_symbols.update(operation["successor_symbols"])
        elif op == "re_adopt":
            target_path = operation["target_slot"].get("path")
            owner = tasks_by_uid.get(operation["owner_task_uid"])
            if not isinstance(target_path, str) or owner is None:
                raise RevisionMechanismError("re-adoption target owner is absent from candidate", code="REVISION_MIGRATION_INVALID")
            affected_paths.add(target_path)
            re_adopt.append({
                "quarantine_path": operation["quarantine_path"],
                "target_path": target_path,
                "owner": {"task_uid": owner["task_uid"], "task_id": owner["id"]},
                "content_sha256": operation["content_sha256"],
            })
        elif op == "add_contract":
            affected_contracts.add(str(operation["contract"]["id"]))
            path = slots.get(operation["implementation_slot_id"])
            if isinstance(path, str):
                affected_paths.add(path)
            affected_symbols.update(row["symbol"] for row in operation["contract"]["exports"])
        elif op == "extend_contract":
            affected_contracts.add(str(operation["contract_id"]))
            affected_paths.update(str(slots[slot_id]) for slot_id in operation["implementation_slot_ids"] if isinstance(slots.get(slot_id), str))
            affected_symbols.update(row["symbol"] for row in operation["exports"])
    changed_uids = {
        str(row["new_task_uid"])
        for row in migration["tasks"]
        if row.get("new_task_uid") is not None and row.get("classification") != "INHERIT"
    }
    owned_candidate_paths = {
        str(path)
        for task in tasks_by_uid.values()
        for path in task.get("deliverable_files", [])
    }
    if affected_paths - owned_candidate_paths:
        raise RevisionMechanismError("F3 affected path has no attributable task", code="REVISION_MIGRATION_INVALID")
    seed_uids = changed_uids | {
        uid for uid, task in tasks_by_uid.items()
        if set(task.get("deliverable_files", [])) & affected_paths
    }
    tasks_by_id = {str(task["id"]): uid for uid, task in tasks_by_uid.items()}
    for contract in candidate_plan.get("architecture", {}).get("contracts", []):
        if not isinstance(contract, Mapping) or str(contract.get("id")) not in affected_contracts:
            continue
        provider_id = contract.get("provider_task_id")
        if isinstance(provider_id, str) and provider_id in tasks_by_id:
            seed_uids.add(tasks_by_id[provider_id])
        contract_id = str(contract["id"])
        contract_uids = {uid for uid, task in tasks_by_uid.items() if contract_id in task.get("consumes_contracts", [])}
        if isinstance(provider_id, str) and provider_id in tasks_by_id:
            contract_uids.add(tasks_by_id[provider_id])
        seed_uids.update(contract_uids)
        for export in contract.get("exports", []):
            if isinstance(export, Mapping) and str(export.get("symbol")) in affected_symbols:
                symbol_seed_uids[str(export["symbol"])].update(contract_uids)
    for symbol in affected_symbols - set(symbol_seed_uids):
        owners = {
            uid for uid, task in tasks_by_uid.items()
            if set(task.get("deliverable_files", [])) & affected_paths
        }
        if not owners:
            raise RevisionMechanismError("F3 affected symbol has no attributable task", code="REVISION_MIGRATION_INVALID")
        symbol_seed_uids[symbol].update(owners)
    if not seed_uids:
        raise RevisionMechanismError("F3 candidate has no affected task group", code="REVISION_MIGRATION_INVALID")

    rule_by_id = {
        str(row["id"]): row
        for row in blueprint.get("file_rules", [])
        if isinstance(row, Mapping) and isinstance(row.get("id"), str)
    }
    path_by_rule = {
        rule_id: str(row["path_pattern"])
        for rule_id, row in rule_by_id.items()
        if isinstance(row.get("path_pattern"), str) and row.get("expansion") == "none"
    }
    set_rules = {
        str(row["id"]): {str(value) for value in row.get("file_rule_ids", [])}
        for row in blueprint.get("link_source_sets", [])
        if isinstance(row, Mapping) and isinstance(row.get("id"), str)
    }
    artifact_paths: dict[str, set[str]] = {}
    for artifact in blueprint.get("build_artifacts", []):
        if not isinstance(artifact, Mapping) or not isinstance(artifact.get("id"), str):
            continue
        rule_ids = set_rules.get(str(artifact.get("link_source_set_id")), set())
        entry = artifact.get("entry_file_slot")
        if isinstance(entry, str):
            rule_ids.add(entry)
        artifact_paths[str(artifact["id"])] = {path_by_rule[rule_id] for rule_id in rule_ids if rule_id in path_by_rule}

    adjacency: dict[str, set[str]] = {uid: set() for uid in tasks_by_uid}
    for uid, task in tasks_by_uid.items():
        for dependency_id in task.get("depends_on", []):
            dependency_uid = tasks_by_id.get(str(dependency_id))
            if dependency_uid is not None:
                adjacency[uid].add(dependency_uid)
                adjacency[dependency_uid].add(uid)
    for contract in candidate_plan.get("architecture", {}).get("contracts", []):
        if not isinstance(contract, Mapping) or contract.get("ready_gate") != "task":
            continue
        provider_uid = tasks_by_id.get(str(contract.get("provider_task_id")))
        consumers = [uid for uid, task in tasks_by_uid.items() if contract.get("id") in task.get("consumes_contracts", [])]
        if provider_uid is not None:
            for consumer_uid in consumers:
                adjacency[provider_uid].add(consumer_uid)
                adjacency[consumer_uid].add(provider_uid)
    for paths in artifact_paths.values():
        members = [uid for uid, task in tasks_by_uid.items() if set(task.get("deliverable_files", [])) & paths]
        for uid in members:
            adjacency[uid].update(set(members) - {uid})
    for owners in symbol_seed_uids.values():
        for uid in owners:
            adjacency[uid].update(owners - {uid})

    components: list[set[str]] = []
    remaining = set(seed_uids)
    while remaining:
        root = min(remaining, key=lambda value: value.encode("utf-8"))
        component: set[str] = set()
        stack = [root]
        while stack:
            uid = stack.pop()
            if uid in component:
                continue
            component.add(uid)
            stack.extend(adjacency.get(uid, set()) - component)
        component &= set(tasks_by_uid)
        components.append(component)
        remaining -= component

    groups: list[dict[str, Any]] = []
    for index, member_uids in enumerate(components, start=1):
        owned_paths = {str(path) for uid in member_uids for path in tasks_by_uid[uid].get("deliverable_files", [])}
        group_paths = affected_paths & owned_paths
        group_artifacts = {
            artifact_id for artifact_id, paths in artifact_paths.items()
            if paths & (owned_paths | group_paths)
        }
        if not group_artifacts:
            raise RevisionMechanismError("F3 affected component has no attributable build artifact", code="REVISION_MIGRATION_INVALID")
        groups.append({
            "group_id": f"g-{revision_seq}-{index}",
            "member_task_uids": sorted(member_uids, key=lambda value: value.encode("utf-8")),
            "affected_paths": sorted(group_paths, key=lambda value: value.encode("utf-8")),
            "affected_symbols": sorted(
                (symbol for symbol, owners in symbol_seed_uids.items() if owners & member_uids),
                key=lambda value: value.encode("utf-8"),
            ),
            "build_artifact_ids": sorted(group_artifacts, key=lambda value: value.encode("utf-8")),
        })
    migration["pending_groups"] = groups
    if re_adopt:
        migration["re_adopt"] = sorted(re_adopt, key=lambda row: (row["quarantine_path"].encode("utf-8"), row["target_path"].encode("utf-8")))
    validate_migration_extensions(migration, level="F3", revision_seq=revision_seq)


def complete_revision_candidate(
    source_plan: Mapping[str, Any],
    source_plan_ref: Mapping[str, Any],
    patch: Mapping[str, Any],
    constraints: Mapping[str, Any],
    frozen_refs: Mapping[str, Any],
    manifest: Mapping[str, Any],
    config_snapshot: Mapping[str, Any],
    plan_state: Mapping[str, Any],
    file_ledger: Mapping[str, Any],
    *,
    ledger_prefix_sha256: str,
) -> dict[str, Any]:
    """Complete one patched, invariant-checked, non-authoritative candidate bundle."""

    source_ir = plan_to_draft_ir(source_plan)
    source_ref = {"path": source_plan_ref.get("path"), "sha256": source_plan_ref.get("sha256")}
    if patch.get("source", {}).get("plan_ref") != source_ref:
        raise RevisionMechanismError("patch source Plan ref mismatch", code="REVISION_PATCH_SOURCE_INVALID")
    if patch.get("source", {}).get("revision_seq") != source_plan_ref.get("revision_seq"):
        raise RevisionMechanismError("patch source revision mismatch", code="REVISION_PATCH_SOURCE_INVALID")
    frozen_values = {
        "spec": frozen_refs.get("spec_value") or frozen_refs.get("spec"),
        "target_profile": frozen_refs.get("target_profile_value") or frozen_refs.get("target_profile"),
        "test_bundle": frozen_refs.get("test_bundle_value") or frozen_refs.get("test_bundle"),
    }
    if any(not isinstance(value, Mapping) for value in frozen_values.values()):
        raise RevisionMechanismError("candidate has incomplete frozen commitment inputs", code="REVISION_INV1_INVALID")
    accepted_refs = frozen_refs.get("refs") or frozen_refs.get("input_refs")
    if not isinstance(accepted_refs, Mapping) or any(
        source_plan.get("input_refs", {}).get(name) != accepted_refs.get(name)
        for name in frozen_values
    ):
        raise RevisionMechanismError("source Plan refs do not match the accepted frozen inputs", code="REVISION_INV1_INVALID")
    patched_ir = apply_revision_patch(source_ir, patch)
    try:
        completion = complete_plan_candidate(patched_ir, constraints, frozen_refs, manifest, config_snapshot)
    except (DeliveryConstraintError, PlanError) as exc:
        raise RevisionMechanismError(str(exc), code="REVISION_CANDIDATE_COMPLETION_INVALID") from exc
    candidate_plan = completion.plan
    if patch["level"] == "F2" and canonical_json_bytes(candidate_plan["architecture"]) != canonical_json_bytes(source_plan["architecture"]):
        raise RevisionMechanismError("F2 candidate changed architecture", code="REVISION_INV1_INVALID")
    if source_plan.get("input_refs") != candidate_plan.get("input_refs"):
        raise RevisionMechanismError("candidate changed frozen input refs", code="REVISION_INV1_INVALID")
    old_requirements = {row["req_id"] for row in source_plan.get("coverage", {}).get("requirements", [])}
    new_requirements = {row["req_id"] for row in candidate_plan.get("coverage", {}).get("requirements", [])}
    if old_requirements != new_requirements:
        raise RevisionMechanismError("candidate changed frozen requirement coverage", code="REVISION_INV1_INVALID")
    owners = _primary_owners(candidate_plan)
    normative_requirements = {
        str(requirement["id"])
        for requirement in completion.spec.get("requirements", [])
        if requirement.get("level") != "DEFINITION"
    }
    bad_owners = sorted(req_id for req_id in normative_requirements if len(owners.get(req_id, [])) != 1)
    if bad_owners:
        raise RevisionMechanismError("candidate violates unique primary ownership: " + ", ".join(bad_owners), code="REVISION_INV2_INVALID")
    mappings = patch["obligation_mappings"]
    if any(mapping.get("acceptance_not_weaker") is not True or not mapping.get("new_targets") for mapping in mappings):
        raise RevisionMechanismError("candidate has a weakened obligation mapping", code="REVISION_INV3_INVALID")
    old_uids = {task["task_uid"] for task in source_plan.get("tasks", [])}
    new_uids = {task["task_uid"] for task in candidate_plan.get("tasks", [])}
    lineage_by_old = {row["old_task_uid"]: set(row["new_task_uids"]) for row in patch["lineage"]}
    for old_uid in old_uids - new_uids:
        if not lineage_by_old.get(old_uid) or not lineage_by_old[old_uid] <= new_uids:
            raise RevisionMechanismError("candidate lacks explicit task lineage", code="REVISION_INV3_INVALID")
    _validate_obligation_preservation(source_plan, candidate_plan, patch["lineage"], mappings, patch["patch_ops"])
    _validate_file_migration_declarations(patch, file_ledger)
    try:
        migration = classify_migration(
            source_plan,
            candidate_plan,
            plan_state,
            file_ledger,
            lineage={"task_mappings": [
                {"old_task_uid": row["old_task_uid"], "new_task_uid": new_uid}
                for row in patch["lineage"] for new_uid in row["new_task_uids"]
            ]},
            from_version=source_plan_ref.get("version"),
            to_version=source_plan_ref.get("version"),
        )
    except PlanRevisionError as exc:
        raise RevisionMechanismError(str(exc), code="REVISION_MIGRATION_INVALID") from exc
    try:
        _migration_extensions(
            patch,
            candidate_plan,
            completion.blueprint,
            migration,
            revision_seq=int(source_plan_ref["revision_seq"]) + 1,
        )
    except PlanRevisionError as exc:
        raise RevisionMechanismError(str(exc), code="REVISION_MIGRATION_INVALID") from exc
    spec = completion.spec
    target = frozen_values["target_profile"]
    if not isinstance(target, Mapping):
        raise RevisionMechanismError("candidate has no frozen Target", code="REVISION_CANDIDATE_COMPLETION_INVALID")
    try:
        view = derive_rendering_view(candidate_plan, spec, target, completion.blueprint, constraints)
        rendered = render_e0_files(view, spec, target, completion.blueprint, constraints)
    except MaterializationError as exc:
        raise RevisionMechanismError(str(exc), code="REVISION_CANDIDATE_COMPLETION_INVALID") from exc
    view = {**view, "rendered_files": rendered}
    candidate_plan_sha = _sha(candidate_plan)
    derived_plan_ref = {"version": source_plan_ref.get("version", "1.0.0"), "path": source_plan_ref["path"], "sha256": candidate_plan_sha}
    try:
        derived_manifest = build_artifact_manifest(derived_plan_ref, completion.blueprint, view, source_plan_ref.get("epoch", "E0"))
        contract_map = build_contract_map(derived_plan_ref, completion.blueprint, view, source_plan_ref.get("epoch", "E0"))
    except MaterializationError as exc:
        raise RevisionMechanismError(str(exc), code="REVISION_CANDIDATE_COMPLETION_INVALID") from exc
    commitment = {
        "input_refs": source_plan["input_refs"],
        "spec_requirements": completion.spec.get("requirements", []),
        "target_profile": target,
        "test_bundle": frozen_values["test_bundle"],
        "frozen_budgets": config_snapshot.get("budgets", {}),
    }
    commitment_hash = _sha(commitment)
    invariants = {
        "schema_version": "1.0",
        "INV-1": {"pass": True, "source_commitment_sha256": commitment_hash, "candidate_commitment_sha256": commitment_hash},
        "INV-2": {"pass": True, "requirement_ids": sorted(normative_requirements, key=lambda value: value.encode("utf-8"))},
        "INV-3": {"pass": True, "lineage_count": len(patch["lineage"]), "obligation_mapping_count": len(mappings)},
    }
    artifacts: dict[str, Any] = {
        "patch.json": _canonical(dict(patch)),
        "plan_draft_ir.json": completion.plan_draft_ir,
        "plan.json": candidate_plan,
        "blueprint.json": completion.blueprint,
        "manifest.json": derived_manifest,
        "contract_map.json": contract_map,
        "lineage.json": patch["lineage"],
        "obligations.json": mappings,
        "migration.json": migration,
        "lint.json": completion.lint_report,
        "invariants.json": invariants,
    }
    content_hashes = {name: _sha(value) for name, value in artifacts.items()}
    event_seq = patch["selected_trigger"]["event_seq"]
    refs = {name: {"path": name, "sha256": digest} for name, digest in content_hashes.items()}
    candidate = {
        "schema_version": "1.0",
        "candidate_id": f"candidate-{event_seq}",
        "selected_event_seq": event_seq,
        "level": patch["level"],
        "source": {"plan_ref": {"path": source_plan_ref["path"], "sha256": source_plan_ref["sha256"]}, "revision_seq": source_plan_ref["revision_seq"], "ledger_prefix_sha256": ledger_prefix_sha256},
        "selected_trigger": {"code": patch["selected_trigger"]["code"], "signature": patch["selected_trigger"]["signature"]},
        "patch_ref": refs["patch.json"], "plan_draft_ir_ref": refs["plan_draft_ir.json"], "candidate_plan_ref": refs["plan.json"],
        "blueprint_ref": refs["blueprint.json"], "manifest_ref": refs["manifest.json"], "contract_map_ref": refs["contract_map.json"],
        "lineage_ref": refs["lineage.json"], "obligation_mapping_ref": refs["obligations.json"], "migration_ref": refs["migration.json"],
        "lint_report_ref": refs["lint.json"], "invariant_report_ref": refs["invariants.json"], "content_hashes": content_hashes,
    }
    validate_revision_candidate(candidate)
    return {**artifacts, "candidate.json": candidate}


__all__ = [
    "RevisionMechanismError",
    "append_trigger_batch",
    "apply_revision_patch",
    "complete_revision_candidate",
    "evaluate_revision_triggers",
    "project_revision_boundary",
    "validate_revision_candidate",
]
