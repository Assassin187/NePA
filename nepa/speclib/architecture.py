"""ArchitectureDraft contract, canonical serialization, and ARCH_VALIDATE."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, SchemaError

from ..schemas import load_schema
from .lint import canonical_json_bytes


class ArchitectureError(ValueError):
    pass


class ArchitectureSchemaError(ArchitectureError):
    pass


ARCHITECTURE_SCHEMA = load_schema("architecture-draft.schema.json")
VALIDATION_SCHEMA = load_schema("architecture-validation.schema.json")
GATES = tuple(f"arch_{index:02d}" for index in range(1, 11))


def _read_json(source: Any, label: str) -> Any:
    if isinstance(source, Mapping):
        return dict(source)
    try:
        return json.loads(Path(source).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchitectureError(f"unable to read {label}: {exc}") from exc


def architecture_schema_errors(draft: Any) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    try:
        validator = Draft202012Validator(ARCHITECTURE_SCHEMA)
    except SchemaError as exc:
        raise ArchitectureSchemaError(f"invalid ArchitectureDraft schema: {exc.message}") from exc
    for error in sorted(validator.iter_errors(draft), key=lambda item: (tuple(item.absolute_path), item.validator or "", item.message)):
        path = "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.absolute_path)
        errors.append({"path": path, "code": str(error.validator or "schema"), "message": error.message})
    return errors


def load_architecture_draft(source: Any) -> dict[str, Any]:
    draft = _read_json(source, "ArchitectureDraft")
    errors = architecture_schema_errors(draft)
    if errors:
        raise ArchitectureSchemaError("ArchitectureDraft failed Schema validation: " + "; ".join(item["message"] for item in errors))
    return draft


def serialize_architecture_draft(draft: Mapping[str, Any]) -> bytes:
    value = load_architecture_draft(draft)
    return canonical_json_bytes(value)


def canonical_architecture_draft(draft: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(serialize_architecture_draft(draft).decode("utf-8"))


def _ref(value: Any, label: str) -> dict[str, str]:
    if isinstance(value, Mapping):
        candidate = value.get("_artifact_ref") or value.get("artifact_ref") or value.get("parent_ref")
        if isinstance(candidate, Mapping) and isinstance(candidate.get("path"), str) and isinstance(candidate.get("sha256"), str):
            return {"path": candidate["path"], "sha256": candidate["sha256"]}
    return {"path": f"<memory>/{label}.json", "sha256": hashlib.sha256(canonical_json_bytes(value)).hexdigest()}


def _validate_parent_ref(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not isinstance(value.get("path"), str) or not isinstance(value.get("sha256"), str):
        raise ArchitectureError(f"{label} must be an artifact reference")
    path = value["path"]
    if not path or "\x00" in path or path.startswith("/") or "\\" in path or ".." in Path(path).parts:
        raise ArchitectureError(f"{label} has an unsafe path")
    if re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None:
        raise ArchitectureError(f"{label} has an invalid SHA-256")
    return {"path": path, "sha256": value["sha256"]}


def _context_sort(refs: Any) -> list[dict[str, str]]:
    if not isinstance(refs, list):
        return []
    return sorted((dict(item) for item in refs if isinstance(item, Mapping)), key=lambda item: (item.get("kind", ""), item.get("id", "")))


def _issue(gate: str, code: str, path: str, message: str, refs: Any = None) -> dict[str, Any]:
    return {"gate": gate, "code": code, "path": path, "message": message, "context_refs": _context_sort(refs or [])}


def _sets_equal(left: Any, right: Any) -> bool:
    return set(left or []) == set(right or [])


def _slot_map(constraints: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {slot["path"]: slot for slot in constraints.get("file_slots", []) if isinstance(slot, Mapping) and isinstance(slot.get("path"), str)}


def _contracts_by_id(draft: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {item["id"]: item for item in draft.get("contracts", []) if isinstance(item, Mapping) and isinstance(item.get("id"), str)}


def _modules_by_id(draft: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {item["id"]: item for item in draft.get("modules", []) if isinstance(item, Mapping) and isinstance(item.get("id"), str)}


def _work_packages_by_id(draft: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {item["id"]: item for item in draft.get("work_packages", []) if isinstance(item, Mapping) and isinstance(item.get("id"), str)}


def _duplicate_ids(items: list[Mapping[str, Any]]) -> list[tuple[str, int]]:
    seen: dict[str, int] = {}
    duplicates: list[tuple[str, int]] = []
    for index, item in enumerate(items):
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id in seen:
            duplicates.append((item_id, index))
        elif isinstance(item_id, str):
            seen[item_id] = index
    return duplicates


def _validate_gate_01(draft: Mapping[str, Any], planning: Mapping[str, Any], constraints: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    groups = [("decisions", draft.get("decisions", [])), ("modules", draft.get("modules", [])), ("contracts", draft.get("contracts", [])), ("work_packages", draft.get("work_packages", []))]
    for name, items in groups:
        for item_id, index in _duplicate_ids(items):
            issues.append(_issue("arch_01", "ARCH_ID_DUPLICATE", f"/{name}/{index}/id", f"duplicate {name[:-1]} id {item_id!r}"))
    modules = _modules_by_id(draft)
    contracts = _contracts_by_id(draft)
    work_packages = _work_packages_by_id(draft)
    req_ids = {item.get("id") for item in planning.get("requirements", []) if isinstance(item, Mapping)}
    type_ids = {item.get("id") for item in planning.get("types", []) if isinstance(item, Mapping)}
    message_ids = {item.get("id") for item in planning.get("messages", []) if isinstance(item, Mapping)}
    interface_files = {slot.get("path") for slot in constraints.get("file_slots", []) if isinstance(slot, Mapping)}
    interface_files.update({path for item in constraints.get("internal_interface_slots", []) if isinstance(item, Mapping) for path in item.get("interface_files", [])})
    def context_unknown(path: str, value: str, kind: str, known: set[Any]) -> None:
        if value not in known:
            issues.append(_issue("arch_01", "ARCH_REFERENCE_UNKNOWN", path, f"unknown {kind} reference {value!r}", [{"kind": kind, "id": value}] if kind in {"requirement", "message", "type", "interface_file"} else []))
    for index, module in enumerate(draft.get("modules", [])):
        for name in ("provides_contracts", "consumes_contracts"):
            for child_index, value in enumerate(module.get(name, [])):
                context_unknown(f"/modules/{index}/{name}/{child_index}", value, "contract", set(contracts))
    for index, contract in enumerate(draft.get("contracts", [])):
        for child_index, value in enumerate(contract.get("consumers", [])):
            context_unknown(f"/contracts/{index}/consumers/{child_index}", value, "module", set(modules))
        for child_index, value in enumerate(contract.get("interface_files", [])):
            context_unknown(f"/contracts/{index}/interface_files/{child_index}", value, "interface_file", interface_files)
    for index, work_package in enumerate(draft.get("work_packages", [])):
        context_unknown(f"/work_packages/{index}/module", work_package.get("module"), "module", set(modules))
        for name in ("provides_contracts", "consumes_contracts", "depends_on"):
            known = set(contracts) if name != "depends_on" else set(work_packages)
            for child_index, value in enumerate(work_package.get(name, [])):
                context_unknown(f"/work_packages/{index}/{name}/{child_index}", value, "contract" if name != "depends_on" else "work_package", known)
        for child_index, item in enumerate(work_package.get("requirement_responsibilities", [])):
            context_unknown(f"/work_packages/{index}/requirement_responsibilities/{child_index}/req_id", item.get("req_id"), "requirement", req_ids)
    for collection, kind, known in (("decisions", "requirement", req_ids | type_ids | message_ids), ("work_packages", "requirement", req_ids)):
        for index, item in enumerate(draft.get(collection, [])):
            for child_index, ref in enumerate(item.get("context_refs", [])):
                if ref.get("kind") == "requirement":
                    context_unknown(f"/{collection}/{index}/context_refs/{child_index}/id", ref.get("id"), "requirement", req_ids)
                elif ref.get("kind") == "type":
                    context_unknown(f"/{collection}/{index}/context_refs/{child_index}/id", ref.get("id"), "type", type_ids)
                elif ref.get("kind") == "message":
                    context_unknown(f"/{collection}/{index}/context_refs/{child_index}/id", ref.get("id"), "message", message_ids)
                elif ref.get("kind") == "interface_file":
                    context_unknown(f"/{collection}/{index}/context_refs/{child_index}/id", ref.get("id"), "interface_file", interface_files)
    return issues


def _validate_gate_02(draft: Mapping[str, Any], constraints: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    slots = _slot_map(constraints)
    all_files: set[str] = set()
    for index, module in enumerate(draft.get("modules", [])):
        for field in ("purpose", "responsibilities", "non_goals"):
            if not module.get(field):
                issues.append(_issue("arch_02", "ARCH_MODULE_BOUNDARY_INVALID", f"/modules/{index}/{field}", f"module {module.get('id')!r} must declare {field}"))
        for file_index, path in enumerate(module.get("owns_files", [])):
            if path in all_files or path not in slots or slots[path].get("mutability") != "s6_owned":
                issues.append(_issue("arch_02", "ARCH_MODULE_FILE_INVALID", f"/modules/{index}/owns_files/{file_index}", f"module file {path!r} is not a unique s6_owned delivery slot"))
            all_files.add(path)
    return issues


def _validate_gate_03(draft: Mapping[str, Any], constraints: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    modules = _modules_by_id(draft)
    slots = _slot_map(constraints)
    for index, contract in enumerate(draft.get("contracts", [])):
        gate = contract.get("ready_gate")
        owner = contract.get("owner")
        files = contract.get("interface_files", [])
        if gate == "s5":
            if owner != "s5" or contract.get("provider") != "s5":
                issues.append(_issue("arch_03", "ARCH_CONTRACT_GATE_INVALID", f"/contracts/{index}", "an s5-ready contract must be owned and provided by s5"))
            for file_index, path in enumerate(files):
                if slots.get(path, {}).get("mutability") != "s5_frozen":
                    issues.append(_issue("arch_03", "ARCH_CONTRACT_GATE_INVALID", f"/contracts/{index}/interface_files/{file_index}", "an s5-ready interface must use an s5_frozen slot"))
        elif gate == "task":
            if owner not in modules:
                continue
            owned = set(modules[owner].get("owns_files", []))
            if not files or any(path not in owned or slots.get(path, {}).get("mutability") != "s6_owned" for path in files):
                issues.append(_issue("arch_03", "ARCH_CONTRACT_GATE_INVALID", f"/contracts/{index}/interface_files", "a task-ready contract must use its module's mutable implementation boundary"))
    return issues


def _validate_gate_04(draft: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    modules = _modules_by_id(draft)
    for index, contract in enumerate(draft.get("contracts", [])):
        provider = contract.get("provider")
        if contract.get("ready_gate") == "s5":
            valid = provider == "s5"
        else:
            valid = provider in modules and provider == contract.get("owner")
        if not valid:
            issues.append(_issue("arch_04", "ARCH_CONTRACT_PROVIDER_INVALID", f"/contracts/{index}/provider", f"invalid provider {provider!r}"))
        for consumer_index, consumer in enumerate(contract.get("consumers", [])):
            if consumer not in modules:
                issues.append(_issue("arch_04", "ARCH_CONTRACT_PROVIDER_INVALID", f"/contracts/{index}/consumers/{consumer_index}", f"invalid consumer {consumer!r}"))
    return issues


def _validate_gate_05(draft: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    contracts = _contracts_by_id(draft)
    for index, module in enumerate(draft.get("modules", [])):
        provided = {cid for cid, contract in contracts.items() if contract.get("provider") == module.get("id")}
        consumed = {cid for cid, contract in contracts.items() if module.get("id") in contract.get("consumers", [])}
        if not _sets_equal(module.get("provides_contracts"), provided) or not _sets_equal(module.get("consumes_contracts"), consumed):
            issues.append(_issue("arch_05", "ARCH_MODULE_CONTRACT_SET_MISMATCH", f"/modules/{index}", f"module {module.get('id')!r} contract projections do not match contract declarations"))
    return issues


def _validate_gate_06(draft: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    modules = _modules_by_id(draft)
    all_allowed: set[str] = set()
    for index, work_package in enumerate(draft.get("work_packages", [])):
        module = modules.get(work_package.get("module"))
        files = set(work_package.get("allowed_files", []))
        if module is None or not files or not files.issubset(set(module.get("owns_files", []))) or all_allowed.intersection(files):
            issues.append(_issue("arch_06", "ARCH_WORK_PACKAGE_FILE_PARTITION", f"/work_packages/{index}/allowed_files", f"work package {work_package.get('id')!r} does not form a disjoint module file partition"))
        all_allowed.update(files)
    for module_index, module in enumerate(draft.get("modules", [])):
        owned = set(module.get("owns_files", []))
        assigned = {path for wp in draft.get("work_packages", []) if wp.get("module") == module.get("id") for path in wp.get("allowed_files", [])}
        if owned != assigned:
            issues.append(_issue("arch_06", "ARCH_WORK_PACKAGE_FILE_PARTITION", f"/modules/{module_index}/owns_files", f"module {module.get('id')!r} files are not completely partitioned"))
    return issues


def _validate_gate_07(draft: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    modules = _modules_by_id(draft)
    contracts = _contracts_by_id(draft)
    providers: dict[str, list[str]] = {}
    work_packages_by_module: dict[str, list[Mapping[str, Any]]] = {module_id: [] for module_id in modules}
    for index, wp in enumerate(draft.get("work_packages", [])):
        module = modules.get(wp.get("module"))
        if module is None:
            continue
        work_packages_by_module.setdefault(wp["module"], []).append(wp)
        if not set(wp.get("provides_contracts", [])).issubset(set(module.get("provides_contracts", []))) or not set(wp.get("consumes_contracts", [])).issubset(set(module.get("consumes_contracts", []))):
            issues.append(_issue("arch_07", "ARCH_WORK_PACKAGE_CONTRACT_SET_MISMATCH", f"/work_packages/{index}", "work-package contract sets do not refine module projections"))
        for cid in wp.get("provides_contracts", []):
            providers.setdefault(cid, []).append(wp.get("id"))
    for index, module in enumerate(draft.get("modules", [])):
        module_id = module.get("id")
        work_packages = work_packages_by_module.get(module_id, [])
        provided_projection = {cid for wp in work_packages for cid in wp.get("provides_contracts", [])}
        consumed_projection = {cid for wp in work_packages for cid in wp.get("consumes_contracts", [])}
        if provided_projection != set(module.get("provides_contracts", [])):
            issues.append(_issue("arch_07", "ARCH_WORK_PACKAGE_CONTRACT_SET_MISMATCH", f"/modules/{index}/provides_contracts", "work-package provide union must exactly equal the module provide projection"))
        if consumed_projection != set(module.get("consumes_contracts", [])):
            issues.append(_issue("arch_07", "ARCH_WORK_PACKAGE_CONTRACT_SET_MISMATCH", f"/modules/{index}/consumes_contracts", "work-package consume union must exactly equal the module consume projection"))
    for cid, contract in contracts.items():
        if contract.get("ready_gate") == "task" and len(providers.get(cid, [])) != 1:
            issues.append(_issue("arch_07", "ARCH_WORK_PACKAGE_CONTRACT_SET_MISMATCH", f"/contracts/{cid}/provider", "each task-ready contract needs exactly one provider work package"))
        elif contract.get("ready_gate") == "task" and providers[cid]:
            provider_wp = next((item for item in draft.get("work_packages", []) if item.get("id") == providers[cid][0]), None)
            if provider_wp is not None and provider_wp.get("module") != contract.get("owner"):
                issues.append(_issue("arch_07", "ARCH_WORK_PACKAGE_CONTRACT_SET_MISMATCH", f"/contracts/{cid}/provider", "provider work package must belong to the contract owner module"))
        if contract.get("ready_gate") == "s5" and providers.get(cid):
            issues.append(_issue("arch_07", "ARCH_WORK_PACKAGE_CONTRACT_SET_MISMATCH", f"/contracts/{cid}/provider", "an s5-ready contract cannot be provided by a work package"))
    return issues


def _validate_gate_08(draft: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    contracts = _contracts_by_id(draft)
    wps = _work_packages_by_id(draft)
    provider_wp = {cid: wp.get("id") for wp in draft.get("work_packages", []) for cid in wp.get("provides_contracts", [])}
    expected: dict[str, set[str]] = {wp_id: set() for wp_id in wps}
    for cid, contract in contracts.items():
        if contract.get("ready_gate") != "task" or cid not in provider_wp:
            continue
        source = provider_wp[cid]
        for consumer_module in contract.get("consumers", []):
            for wp_id, wp in wps.items():
                if wp.get("module") == consumer_module and cid in wp.get("consumes_contracts", []) and wp_id != source:
                    expected[wp_id].add(source)
    for index, wp in enumerate(draft.get("work_packages", [])):
        actual = set(wp.get("depends_on", []))
        if actual != expected.get(wp.get("id"), set()):
            issues.append(_issue("arch_08", "ARCH_DEPENDENCY_MISMATCH", f"/work_packages/{index}/depends_on", "depends_on is not the exact contract-derived dependency set"))
        if wp.get("id") in actual:
            issues.append(_issue("arch_08", "ARCH_DEPENDENCY_MISMATCH", f"/work_packages/{index}/depends_on", "a work package cannot depend on itself"))
    state: dict[str, int] = {}
    def visit(node: str) -> bool:
        if state.get(node) == 1:
            return True
        if state.get(node) == 2:
            return False
        state[node] = 1
        if any(visit(child) for child in wps.get(node, {}).get("depends_on", [])):
            return True
        state[node] = 2
        return False
    if any(visit(node) for node in wps):
        issues.append(_issue("arch_08", "ARCH_DAG_CYCLE", "/work_packages", "work-package dependency graph contains a cycle"))
    return issues


def _validate_gate_09(draft: Mapping[str, Any], constraints: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    slots = _slot_map(constraints)
    owned = {path for module in draft.get("modules", []) for path in module.get("owns_files", [])}
    expected = {path for path, slot in slots.items() if slot.get("mutability") == "s6_owned"}
    if owned != expected:
        issues.append(_issue("arch_09", "ARCH_DELIVERY_CONSTRAINT_VIOLATION", "/modules", "module file ownership does not exactly cover s6_owned delivery slots"))
    for index, module in enumerate(draft.get("modules", [])):
        for file_index, path in enumerate(module.get("owns_files", [])):
            slot = slots.get(path)
            if slot is None or slot.get("mutability") != "s6_owned":
                issues.append(_issue("arch_09", "ARCH_DELIVERY_CONSTRAINT_VIOLATION", f"/modules/{index}/owns_files/{file_index}", "file is not an s6_owned delivery slot"))
    contracts = draft.get("contracts", [])
    for slot_index, required in enumerate(constraints.get("internal_interface_slots", [])):
        if not required.get("required"):
            continue
        matches = [contract for contract in contracts if set(required.get("interface_files", [])) == set(contract.get("interface_files", []))]
        if len(matches) != 1:
            issues.append(_issue("arch_09", "ARCH_INTERFACE_SLOT_UNCLOSED", f"/internal_interface_slots/{slot_index}", "required internal interface slot must have exactly one compatible contract"))
    return issues


def _descendants(wps: Mapping[str, Mapping[str, Any]]) -> dict[str, set[str]]:
    reverse: dict[str, set[str]] = {node: set() for node in wps}
    for node, wp in wps.items():
        for dependency in wp.get("depends_on", []):
            if dependency in reverse:
                reverse[dependency].add(node)
    result: dict[str, set[str]] = {}
    for node in wps:
        seen = {node}
        stack = [node]
        while stack:
            current = stack.pop()
            for child in reverse.get(current, set()):
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        result[node] = seen
    return result


def _validate_gate_10(draft: Mapping[str, Any], planning: Mapping[str, Any], manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    requirements = {item.get("id"): item for item in planning.get("requirements", []) if isinstance(item, Mapping)}
    wps = _work_packages_by_id(draft)
    assignments: dict[str, list[tuple[str, str]]] = {req_id: [] for req_id in requirements}
    for wp in draft.get("work_packages", []):
        seen_by_requirement: dict[str, set[str]] = {}
        for item in wp.get("requirement_responsibilities", []):
            req_id = item.get("req_id")
            role = item.get("role")
            if req_id not in assignments:
                issues.append(_issue("arch_10", "ARCH_RESPONSIBILITY_INVALID", f"/work_packages/{wp.get('id')}/requirement_responsibilities", f"unknown requirement responsibility {req_id!r}"))
                continue
            seen_by_requirement.setdefault(req_id, set()).add(role)
            assignments[req_id].append((wp.get("id"), role))
        for req_id, roles in seen_by_requirement.items():
            if len(roles) > 1 or len([item for item in wp.get("requirement_responsibilities", []) if item.get("req_id") == req_id]) > 1:
                issues.append(_issue("arch_10", "ARCH_RESPONSIBILITY_INVALID", f"/work_packages/{wp.get('id')}/requirement_responsibilities", f"work package cannot declare duplicate or primary/supporting responsibility for {req_id!r}"))
    for req_id, requirement in requirements.items():
        entries = assignments.get(req_id, [])
        primary = [item for item in entries if item[1] == "primary"]
        if requirement.get("level") == "DEFINITION":
            if primary:
                issues.append(_issue("arch_10", "ARCH_REQUIREMENT_PRIMARY_INVALID", f"/requirements/{req_id}", "DEFINITION requirements cannot have primary ownership"))
        elif len(primary) != 1:
            issues.append(_issue("arch_10", "ARCH_REQUIREMENT_PRIMARY_INVALID", f"/requirements/{req_id}", "each non-DEFINITION requirement needs exactly one primary work package"))
        for wp_id, role in entries:
            if wp_id not in wps or role not in {"primary", "supporting"}:
                issues.append(_issue("arch_10", "ARCH_RESPONSIBILITY_INVALID", f"/work_packages/{wp_id}", "invalid requirement responsibility"))
    closure = _descendants(wps)
    for test_index, test in enumerate(manifest.get("tests", [])):
        if test.get("gate") != "task":
            continue
        related = {wp_id for req_id in test.get("req_ids", []) for wp_id, _role in assignments.get(req_id, [])}
        if related and not set.intersection(*(closure.get(wp_id, {wp_id}) for wp_id in sorted(related))):
            issues.append(_issue("arch_10", "ARCH_TEST_READINESS_UNCLOSED", f"/tests/{test_index}", "task-gated test requirements have no work-package convergence point"))
    return issues


def validate_architecture(
    draft: Mapping[str, Any],
    planning_index: Mapping[str, Any],
    manifest_metadata: Mapping[str, Any],
    constraints: Mapping[str, Any],
    *,
    parent_refs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run all evaluable architecture gates without short-circuiting."""

    loaded = load_architecture_draft(draft)
    computed_parent_refs = {
        "architecture_draft": _ref(loaded, "architecture_draft"),
        "planning_index": _ref(planning_index, "planning_index"),
        "manifest_metadata": _ref(manifest_metadata, "manifest_metadata"),
        "delivery_constraints": _ref(constraints, "delivery_constraints"),
    }
    if parent_refs is None:
        bound_parent_refs = computed_parent_refs
    else:
        if set(parent_refs) != set(computed_parent_refs):
            raise ArchitectureError("parent_refs must contain exactly the four validator parents")
        bound_parent_refs = {}
        for key, expected in computed_parent_refs.items():
            supplied = _validate_parent_ref(parent_refs[key], key)
            if supplied["sha256"] != expected["sha256"]:
                raise ArchitectureError(f"{key} parent reference does not match the actual input bytes")
            bound_parent_refs[key] = supplied
    gate_issues = {
        "arch_01": _validate_gate_01(loaded, planning_index, constraints),
        "arch_02": _validate_gate_02(loaded, constraints),
        "arch_03": _validate_gate_03(loaded, constraints),
        "arch_04": _validate_gate_04(loaded),
        "arch_05": _validate_gate_05(loaded),
        "arch_06": _validate_gate_06(loaded),
        "arch_07": _validate_gate_07(loaded),
        "arch_08": _validate_gate_08(loaded),
        "arch_09": _validate_gate_09(loaded, constraints),
        "arch_10": _validate_gate_10(loaded, planning_index, manifest_metadata),
    }
    issues = [item for gate in GATES for item in gate_issues[gate]]
    issues.sort(key=lambda item: (item["gate"], item["code"], item["path"], item["message"], canonical_json_bytes(item["context_refs"])))
    gates = [{"id": gate, "verdict": "fail" if gate_issues[gate] else "pass", "issue_codes": sorted({item["code"] for item in gate_issues[gate]})} for gate in GATES]
    result = {"schema_version": "1.0", "verdict": "fail" if issues else "pass", "parent_refs": bound_parent_refs, "gates": gates, "issues": issues}
    schema_errors = list(Draft202012Validator(VALIDATION_SCHEMA).iter_errors(result))
    if schema_errors:
        raise ArchitectureError("internal architecture validation envelope is invalid: " + "; ".join(item.message for item in schema_errors))
    return result


def validate_architecture_result(result: Mapping[str, Any]) -> None:
    errors = sorted(Draft202012Validator(VALIDATION_SCHEMA).iter_errors(result), key=lambda item: tuple(item.absolute_path))
    if errors:
        raise ArchitectureError("invalid validation result: " + "; ".join(item.message for item in errors))


ARCH_VALIDATE = validate_architecture


__all__ = [
    "ARCH_VALIDATE", "ARCHITECTURE_SCHEMA", "GATES", "ArchitectureError", "ArchitectureSchemaError", "architecture_schema_errors",
    "canonical_architecture_draft", "load_architecture_draft", "serialize_architecture_draft", "validate_architecture",
    "validate_architecture_result",
]
