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
GATES = tuple(f"arch_{index:02d}" for index in range(1, 16))
_LAYOUT_TOKEN_WHITELIST = {
    "adapter", "application", "app", "apps", "build", "c", "codec", "configuration", "connection", "core", "data",
    "documentation", "entry", "event", "file", "h", "header", "implementation", "include", "interface",
    "internal", "id", "main", "makefile", "md", "message", "net", "network", "source", "src", "state", "stub", "system",
    "transport", "types", "unit", "session", "test", "tests", "client", "server", "common", "error",
}


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


def _layout_slots(draft: Mapping[str, Any], constraints: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expand the draft-owned layout without creating a delivery blueprint."""

    naming = constraints.get("naming", {})
    domains = {
        "messages": sorted(set((naming.get("message_ids") or {}).values()), key=lambda value: value.encode("utf-8")),
        "types": sorted(set((naming.get("type_ids") or {}).values()), key=lambda value: value.encode("utf-8")),
    }
    result: list[dict[str, Any]] = []
    for file_index, item in enumerate(draft.get("layout", {}).get("files", [])):
        if item.get("path") is not None:
            instances = [(None, item["path"])]
        else:
            pattern = item.get("path_pattern")
            domain = item.get("expand_over")
            placeholder = "{" + ("message_id" if domain == "messages" else "type_id") + "}"
            instances = [(value, pattern.replace(placeholder, value)) for value in domains.get(domain, [])]
        for source, path in instances:
            result.append({**dict(item), "path": path, "expansion_source": source, "file_index": file_index})
    return result


def _slot_map(draft: Mapping[str, Any], constraints: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {slot["path"]: slot for slot in _layout_slots(draft, constraints) if isinstance(slot.get("path"), str)}


def _layout_slot_ids(draft: Mapping[str, Any], constraints: Mapping[str, Any]) -> dict[str, list[Mapping[str, Any]]]:
    result: dict[str, list[Mapping[str, Any]]] = {}
    for slot in _layout_slots(draft, constraints):
        result.setdefault(slot["slot_id"], []).append(slot)
    return result


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
    interface_files = {slot.get("path") for slot in _layout_slots(draft, constraints) if isinstance(slot.get("path"), str)}
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
    slots = _slot_map(draft, constraints)
    all_files: set[str] = set()
    for index, module in enumerate(draft.get("modules", [])):
        for field in ("purpose", "responsibilities", "non_goals"):
            if not module.get(field):
                issues.append(_issue("arch_02", "ARCH_MODULE_BOUNDARY_INVALID", f"/modules/{index}/{field}", f"module {module.get('id')!r} must declare {field}"))
        for file_index, path in enumerate(module.get("owns_files", [])):
            if path in all_files or path not in slots or slots[path].get("class") != "s6_owned":
                issues.append(_issue("arch_02", "ARCH_MODULE_FILE_INVALID", f"/modules/{index}/owns_files/{file_index}", f"module file {path!r} is not a unique s6_owned delivery slot"))
            all_files.add(path)
    return issues


def _validate_gate_03(draft: Mapping[str, Any], constraints: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    modules = _modules_by_id(draft)
    slots = _slot_map(draft, constraints)
    for index, contract in enumerate(draft.get("contracts", [])):
        gate = contract.get("ready_gate")
        owner = contract.get("owner")
        files = contract.get("interface_files", [])
        if gate == "s5":
            if owner != "s5" or contract.get("provider") != "s5":
                issues.append(_issue("arch_03", "ARCH_CONTRACT_GATE_INVALID", f"/contracts/{index}", "an s5-ready contract must be owned and provided by s5"))
            for file_index, path in enumerate(files):
                if slots.get(path, {}).get("class") != "s5_frozen":
                    issues.append(_issue("arch_03", "ARCH_CONTRACT_GATE_INVALID", f"/contracts/{index}/interface_files/{file_index}", "an s5-ready interface must use an s5_frozen slot"))
        elif gate == "task":
            if owner not in modules:
                continue
            owned = set(modules[owner].get("owns_files", []))
            if not files or any(path not in owned or slots.get(path, {}).get("class") != "s6_owned" for path in files):
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
    slots = _slot_map(draft, constraints)
    owned = {path for module in draft.get("modules", []) for path in module.get("owns_files", [])}
    expected = {path for path, slot in slots.items() if slot.get("class") == "s6_owned"}
    if owned != expected:
        issues.append(_issue("arch_09", "ARCH_DELIVERY_CONSTRAINT_VIOLATION", "/modules", "module file ownership does not exactly cover s6_owned delivery slots"))
    for index, module in enumerate(draft.get("modules", [])):
        for file_index, path in enumerate(module.get("owns_files", [])):
            slot = slots.get(path)
            if slot is None or slot.get("class") != "s6_owned":
                issues.append(_issue("arch_09", "ARCH_DELIVERY_CONSTRAINT_VIOLATION", f"/modules/{index}/owns_files/{file_index}", "file is not an s6_owned delivery slot"))
    contracts = _contracts_by_id(draft)
    for file_index, slot in enumerate(draft.get("layout", {}).get("files", [])):
        if slot.get("render_rule") == "header" and slot.get("contract_id") not in contracts:
            issues.append(_issue("arch_09", "ARCH_INTERFACE_SLOT_UNCLOSED", f"/layout/files/{file_index}/contract_id", "a header layout slot must bind an existing contract"))
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


def _path_tokens(value: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[A-Za-z0-9]+", value)]


def _derived_layout_tokens(constraints: Mapping[str, Any]) -> set[str]:
    naming = constraints.get("naming", {})
    tokens = {str(naming.get("symbol_prefix", "")).lower(), "message_id", "type_id"}
    for key in ("message_ids", "type_ids"):
        values = naming.get(key, {})
        if isinstance(values, Mapping):
            tokens.update(str(item).lower() for item in values)
            tokens.update(str(item).lower() for item in values.values())
    return {token for token in tokens if token}


def _validate_gate_11(draft: Mapping[str, Any], constraints: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    layout = draft.get("layout", {})
    hard = constraints.get("hard", {})
    allowed_roots = hard.get("allowed_path_roots", [])
    reserved = set(hard.get("reserved_path_segments", []))
    bounds = constraints.get("mechanical_bounds", {})
    expanded = _layout_slots(draft, constraints)

    def unsafe(path: Any) -> bool:
        return (
            not isinstance(path, str)
            or not path
            or path.startswith(("/", "~", "\\"))
            or "\\" in path
            or "\x00" in path
            or "->" in path
            or "://" in path
            or any(segment in reserved or segment in {".", ".."} for segment in path.split("/"))
            or "" in path.split("/")
        )

    def under_allowed(path: str) -> bool:
        for root in allowed_roots:
            if root == "." and "/" not in path:
                return True
            if path == root or path.startswith(str(root).rstrip("/") + "/"):
                return True
        return False

    seen_paths: set[str] = set()
    max_path_length = bounds.get("max_path_length")
    for index, item in enumerate(layout.get("files", [])):
        source_path = item.get("path") if item.get("path") is not None else item.get("path_pattern")
        if unsafe(source_path):
            issues.append(_issue("arch_11", "ARCH_LAYOUT_PATH_UNSAFE", f"/layout/files/{index}", "layout path must be a relative path without unsafe segments or link syntax"))
        elif item.get("path") is not None and not under_allowed(item["path"]):
            issues.append(_issue("arch_11", "ARCH_LAYOUT_PATH_ROOT_INVALID", f"/layout/files/{index}/path", "layout path is outside the convention hard path roots"))
        elif item.get("path_pattern") is not None and not under_allowed(re.sub(r"\{(?:message_id|type_id)\}", "placeholder", item["path_pattern"])):
            issues.append(_issue("arch_11", "ARCH_LAYOUT_PATH_ROOT_INVALID", f"/layout/files/{index}/path_pattern", "layout path pattern is outside the convention hard path roots"))
        pattern = item.get("path_pattern")
        if pattern is not None:
            placeholders = re.findall(r"\{[^{}]+\}", pattern)
            expected = "{message_id}" if item.get("expand_over") == "messages" else "{type_id}" if item.get("expand_over") == "types" else None
            if expected is None or placeholders != [expected]:
                issues.append(_issue("arch_11", "ARCH_LAYOUT_EXPANSION_INVALID", f"/layout/files/{index}/path_pattern", "path pattern must contain exactly one domain-matched placeholder"))
            if not expanded or not any(slot.get("file_index") == index for slot in expanded):
                issues.append(_issue("arch_11", "ARCH_LAYOUT_EXPANSION_EMPTY", f"/layout/files/{index}/path_pattern", "expanded layout pattern has no values in its declared Spec domain"))
        for slot in (slot for slot in expanded if slot.get("file_index") == index):
            path = slot["path"]
            if isinstance(max_path_length, int) and len(path) > max_path_length:
                issues.append(_issue("arch_11", "ARCH_LAYOUT_BOUND_EXCEEDED", f"/layout/files/{index}", "expanded layout path exceeds the convention path-length bound"))
            if not under_allowed(path):
                issues.append(_issue("arch_11", "ARCH_LAYOUT_PATH_ROOT_INVALID", f"/layout/files/{index}", "expanded layout path is outside the convention hard path roots"))
            if path in seen_paths:
                issues.append(_issue("arch_11", "ARCH_LAYOUT_PATH_DUPLICATE", f"/layout/files/{index}", f"expanded layout path {path!r} is not unique"))
            seen_paths.add(path)
    if isinstance(bounds.get("max_files"), int) and len(expanded) > bounds["max_files"]:
        issues.append(_issue("arch_11", "ARCH_LAYOUT_BOUND_EXCEEDED", "/layout/files", "layout exceeds the convention file bound"))
    return issues


def _validate_gate_12(draft: Mapping[str, Any], constraints: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    slots = _layout_slots(draft, constraints)
    modules = _modules_by_id(draft)
    slot_ids: set[str] = set()
    ownership: dict[str, list[str]] = {}
    task_files = {path for wp in draft.get("work_packages", []) for path in wp.get("allowed_files", [])}
    for index, item in enumerate(draft.get("layout", {}).get("files", [])):
        slot_id = item.get("slot_id")
        if slot_id in slot_ids:
            issues.append(_issue("arch_12", "ARCH_LAYOUT_SLOT_DUPLICATE", f"/layout/files/{index}/slot_id", f"duplicate layout slot id {slot_id!r}"))
        slot_ids.add(slot_id)
        owner = item.get("owner_module")
        if owner not in modules:
            issues.append(_issue("arch_12", "ARCH_LAYOUT_OWNER_INVALID", f"/layout/files/{index}/owner_module", "layout owner_module must name an existing module"))
        if item.get("render_rule") == "source_stub" and item.get("class") != "s6_owned":
            issues.append(_issue("arch_12", "ARCH_LAYOUT_CLASS_INVALID", f"/layout/files/{index}", "source_stub files must be s6_owned"))
        if item.get("render_rule") in {"header", "build_file", "doc", "mechanical"} and item.get("class") != "s5_frozen":
            issues.append(_issue("arch_12", "ARCH_LAYOUT_CLASS_INVALID", f"/layout/files/{index}", "non-stub rendered files must be s5_frozen"))
        for slot in (expanded for expanded in slots if expanded.get("file_index") == index):
            ownership.setdefault(slot["path"], []).extend(
                module.get("id") for module in draft.get("modules", []) if slot["path"] in module.get("owns_files", [])
            )
            if slot.get("class") == "s5_frozen" and slot["path"] in task_files:
                issues.append(_issue("arch_12", "ARCH_LAYOUT_FROZEN_TASK_FILE", f"/layout/files/{index}", "s5_frozen files cannot be task deliverables"))
    for path, owners in sorted(ownership.items()):
        slot = next(item for item in slots if item["path"] == path)
        if slot.get("class") == "s6_owned" and len(owners) != 1:
            issues.append(_issue("arch_12", "ARCH_LAYOUT_OWNER_CARDINALITY", "/layout/files", f"s6_owned file {path!r} must have exactly one module owner"))
        if slot.get("class") == "s6_owned" and owners and owners[0] != slot.get("owner_module"):
            issues.append(_issue("arch_12", "ARCH_LAYOUT_OWNER_MISMATCH", "/layout/files", f"layout owner for {path!r} disagrees with module ownership"))
    return issues


def _validate_gate_13(draft: Mapping[str, Any], constraints: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    layout = draft.get("layout", {})
    slot_ids = _layout_slot_ids(draft, constraints)
    files = {item.get("slot_id"): item for item in layout.get("files", [])}
    artifacts = layout.get("build_graph", {}).get("artifacts", [])
    artifact_ids: set[str] = set()
    output_paths: set[str] = set()
    entry_refs: list[str] = []
    linked_refs: list[str] = []
    for index, artifact in enumerate(artifacts):
        artifact_id = artifact.get("artifact_id")
        if artifact_id in artifact_ids:
            issues.append(_issue("arch_13", "ARCH_ARTIFACT_ID_DUPLICATE", f"/layout/build_graph/artifacts/{index}/artifact_id", "artifact ids must be unique"))
        artifact_ids.add(artifact_id)
        output = artifact.get("output_path")
        if output in output_paths:
            issues.append(_issue("arch_13", "ARCH_ARTIFACT_OUTPUT_DUPLICATE", f"/layout/build_graph/artifacts/{index}/output_path", "artifact output paths must be unique"))
        output_paths.add(output)
        entry = artifact.get("entry_file_slot")
        if entry not in files:
            issues.append(_issue("arch_13", "ARCH_BUILD_GRAPH_REFERENCE_INVALID", f"/layout/build_graph/artifacts/{index}/entry_file_slot", "artifact entry slot must exist"))
        else:
            entry_refs.append(entry)
            if files[entry].get("build_role") != "entry_point":
                issues.append(_issue("arch_13", "ARCH_BUILD_GRAPH_ROLE_INVALID", f"/layout/build_graph/artifacts/{index}/entry_file_slot", "artifact entry slot must have build_role entry_point"))
        for source_index, source in enumerate(artifact.get("link_source_slots", [])):
            if source not in files:
                issues.append(_issue("arch_13", "ARCH_BUILD_GRAPH_REFERENCE_INVALID", f"/layout/build_graph/artifacts/{index}/link_source_slots/{source_index}", "link source slot must exist"))
            elif files[source].get("build_role") != "link_source":
                issues.append(_issue("arch_13", "ARCH_BUILD_GRAPH_ROLE_INVALID", f"/layout/build_graph/artifacts/{index}/link_source_slots/{source_index}", "referenced slot must have build_role link_source"))
            linked_refs.append(source)
    hard_shape = constraints.get("hard", {}).get("delivery_shape", {})
    expected_entries = hard_shape.get("entry_point_count", 1)
    if len(entry_refs) != expected_entries:
        issues.append(_issue("arch_13", "ARCH_ENTRY_POINT_CARDINALITY", "/layout/build_graph/artifacts", "build graph entry point count does not match delivery form"))
    expected_sources = {item.get("slot_id") for item in layout.get("files", []) if item.get("build_role") == "link_source"}
    if len(linked_refs) != len(set(linked_refs)) or set(linked_refs) != expected_sources:
        issues.append(_issue("arch_13", "ARCH_LINK_SOURCE_CLOSURE", "/layout/build_graph/artifacts", "every link_source slot must enter exactly one artifact"))
    expected_artifacts = hard_shape.get("executable_artifact_count", 1)
    if len(artifacts) != expected_artifacts:
        issues.append(_issue("arch_13", "ARCH_ARTIFACT_CARDINALITY", "/layout/build_graph/artifacts", "artifact count does not match delivery form"))
    if isinstance(constraints.get("mechanical_bounds", {}).get("max_artifacts"), int) and len(artifacts) > constraints["mechanical_bounds"]["max_artifacts"]:
        issues.append(_issue("arch_13", "ARCH_LAYOUT_BOUND_EXCEEDED", "/layout/build_graph/artifacts", "build graph exceeds the convention artifact bound"))
    return issues


def _validate_gate_14(draft: Mapping[str, Any], constraints: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    order = list(constraints.get("hard", {}).get("layer_order", []))
    ranks = {name: index for index, name in enumerate(order)}
    modules = _modules_by_id(draft)

    def module_layer(module_id: str) -> str | None:
        module = modules.get(module_id, {})
        values = [module_id, module.get("name", ""), module.get("purpose", ""), *module.get("responsibilities", [])]
        values.extend(slot.get("purpose", "") for slot in _layout_slots(draft, constraints) if slot.get("owner_module") == module_id)
        tokens = {token for value in values if isinstance(value, str) for token in _path_tokens(value)}
        return next((layer for layer in order if layer in tokens), None)

    layers = {module_id: module_layer(module_id) for module_id in modules}
    edges: set[tuple[str, str]] = set()
    for index, contract in enumerate(draft.get("contracts", [])):
        provider = contract.get("provider")
        if provider not in modules:
            continue
        for consumer in contract.get("consumers", []):
            if consumer not in modules:
                continue
            edges.add((provider, consumer))
            if layers.get(provider) is None or layers.get(consumer) is None:
                issues.append(_issue("arch_14", "ARCH_LAYER_UNKNOWN", f"/contracts/{index}", "contract modules must map to the convention layer order"))
            elif ranks[layers[provider]] > ranks[layers[consumer]]:
                issues.append(_issue("arch_14", "ARCH_LAYER_DIRECTION_INVALID", f"/contracts/{index}", "contract provider must not depend on a higher layer"))
    state: dict[str, int] = {}
    def visit(node: str) -> bool:
        if state.get(node) == 1:
            return True
        if state.get(node) == 2:
            return False
        state[node] = 1
        if any(visit(child) for source, child in edges if source == node):
            return True
        state[node] = 2
        return False
    if any(visit(node) for node in modules):
        issues.append(_issue("arch_14", "ARCH_LAYER_CYCLE", "/contracts", "module layer dependency graph contains a cycle"))
    return issues


def _validate_gate_15(draft: Mapping[str, Any], constraints: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    allowed = _LAYOUT_TOKEN_WHITELIST | _derived_layout_tokens(constraints)
    for index, item in enumerate(draft.get("layout", {}).get("files", [])):
        value = item.get("path") if item.get("path") is not None else item.get("path_pattern", "")
        for token in _path_tokens(value) + _path_tokens(item.get("purpose", "")):
            if token not in allowed:
                issues.append(_issue("arch_15", "ARCH_PATH_TOKEN_INVALID", f"/layout/files/{index}", f"layout token {token!r} is not in the shared responsibility whitelist or derived identifiers"))
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
        "arch_11": _validate_gate_11(loaded, constraints),
        "arch_12": _validate_gate_12(loaded, constraints),
        "arch_13": _validate_gate_13(loaded, constraints),
        "arch_14": _validate_gate_14(loaded, constraints),
        "arch_15": _validate_gate_15(loaded, constraints),
    }
    issues = [item for gate in GATES for item in gate_issues[gate]]
    issues.sort(key=lambda item: (item["gate"], item["code"], item["path"], item["message"], canonical_json_bytes(item["context_refs"])))
    gates = [{"id": gate, "verdict": "fail" if gate_issues[gate] else "pass", "issue_codes": sorted({item["code"] for item in gate_issues[gate]})} for gate in GATES]
    result = {"schema_version": "2.0", "verdict": "fail" if issues else "pass", "parent_refs": bound_parent_refs, "gates": gates, "issues": issues}
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
