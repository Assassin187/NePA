"""ArchitectureDraft 的生产级跨对象 ARCH_VALIDATE。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_SCHEMA = Path(__file__).resolve().parent / "schemas" / "architecture-draft.schema.json"


@dataclass(frozen=True, slots=True)
class ArchIssue:
    gate: str
    code: str
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class ArchValidationReport:
    issues: tuple[ArchIssue, ...]
    gate_results: dict[str, bool]

    @property
    def ok(self) -> bool:
        return not self.issues


@cache
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(_SCHEMA.read_text(encoding="utf-8")))


def _duplicates(values: list[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        else:
            seen.add(value)
    return duplicates


def _acyclic(nodes: set[str], edges: dict[str, set[str]]) -> bool:
    remaining = {node: set(edges.get(node, set())) for node in nodes}
    ready = sorted(node for node, deps in remaining.items() if not deps)
    visited = 0
    while ready:
        node = ready.pop(0)
        visited += 1
        for other in sorted(remaining):
            if node in remaining[other]:
                remaining[other].remove(node)
                if not remaining[other]:
                    ready.append(other)
                    ready.sort()
    return visited == len(nodes)


def arch_validate(
    draft: dict[str, Any],
    *,
    spec: dict[str, Any],
    target: dict[str, Any],
    constraints: dict[str, Any],
    planning_index: dict[str, Any],
) -> ArchValidationReport:
    """执行 Schema 之外的十二类生产硬门，返回稳定机器码。"""
    issues: list[ArchIssue] = []

    def add(gate: str, code: str, path: str, message: str) -> None:
        issues.append(ArchIssue(gate, code, path, message))

    schema_errors = sorted(
        _validator().iter_errors(draft),
        key=lambda item: [str(part) for part in item.absolute_path],
    )
    for error in schema_errors:
        add(
            "schema",
            "ARCH_SCHEMA",
            "/".join(map(str, error.absolute_path)) or "<root>",
            error.message,
        )
    if schema_errors:
        return ArchValidationReport(
            tuple(issues),
            {"schema": False, **{f"arch_{index:02d}": False for index in range(1, 13)}},
        )

    architecture = draft["architecture"]
    modules = architecture["modules"]
    contracts = architecture["contracts"]
    packages = draft["work_packages"]
    module_by_id = {item["id"]: item for item in modules}
    contract_by_id = {item["id"]: item for item in contracts}
    package_by_id = {item["id"]: item for item in packages}

    for name, values in (
        ("module", [item["id"] for item in modules]),
        ("contract", [item["id"] for item in contracts]),
        ("work_package", [item["id"] for item in packages]),
    ):
        for duplicate in sorted(_duplicates(values)):
            add("arch_01", "ARCH_DUPLICATE_ID", name, f"duplicate {name} id {duplicate}")

    target_contracts = {item["id"]: item for item in target["external_contracts"]}
    for contract in contracts:
        if contract["kind"] != "external":
            continue
        expected = target_contracts.get(contract["id"])
        if expected is None:
            add("arch_02", "ARCH_EXTERNAL_UNKNOWN", contract["id"], "unknown external contract")
        elif (
            contract["ready_gate"] != expected["ready_gate"]
            or set(contract["interface_files"]) != set(expected["interface_files"])
        ):
            add(
                "arch_02",
                "ARCH_EXTERNAL_DRIFT",
                contract["id"],
                "ready_gate/interface_files differ from Target Profile",
            )

    for slot in target["internal_interface_slots"]:
        if not slot["required"]:
            continue
        contract = contract_by_id.get(slot["id"])
        if (
            contract is None
            or contract["kind"] != "internal"
            or set(contract["interface_files"]) != set(slot["interface_files"])
        ):
            add(
                "arch_03",
                "ARCH_INTERNAL_SLOT_MISSING",
                slot["id"],
                "required internal slot has no exact internal contract",
            )

    for contract in contracts:
        provider = contract.get("provider_work_package_id")
        if contract["ready_gate"] == "s5":
            if contract["owner"] != "s5" or provider is not None:
                add("arch_04", "ARCH_S5_PROVIDER", contract["id"], "invalid s5 ownership")
        elif contract["owner"] == "s5" or provider is None:
            add("arch_04", "ARCH_TASK_PROVIDER", contract["id"], "invalid task ownership")

    for contract in contracts:
        provider_id = contract.get("provider_work_package_id")
        if provider_id is None:
            continue
        package = package_by_id.get(provider_id)
        if package is None or package["module"] != contract["owner"]:
            add(
                "arch_05",
                "ARCH_PROVIDER_MODULE",
                contract["id"],
                "provider work package is missing or belongs to another module",
            )

    packages_by_module: dict[str, list[dict[str, Any]]] = {
        module_id: [] for module_id in module_by_id
    }
    for package in packages:
        if package["module"] not in module_by_id:
            add("arch_11", "ARCH_MODULE_REF", package["id"], "unknown module")
        else:
            packages_by_module[package["module"]].append(package)
    for module in modules:
        owned = packages_by_module[module["id"]]
        for field in ("provides_contracts", "consumes_contracts"):
            union = {value for package in owned for value in package[field]}
            if union != set(module[field]):
                add(
                    "arch_06",
                    "ARCH_MODULE_CONTRACT_UNION",
                    f"{module['id']}/{field}",
                    "module set differs from work package union",
                )

    provider_by_contract: dict[str, str] = {}
    for package in packages:
        for contract_id in package["provides_contracts"]:
            if contract_id in provider_by_contract:
                add(
                    "arch_07",
                    "ARCH_CONTRACT_MULTI_PROVIDER",
                    contract_id,
                    "contract has multiple provider work packages",
                )
            provider_by_contract[contract_id] = package["id"]
    for package in packages:
        expected_deps: set[str] = set()
        for contract_id in package["consumes_contracts"]:
            contract = contract_by_id.get(contract_id)
            provider = provider_by_contract.get(contract_id)
            if (
                contract
                and contract["ready_gate"] == "task"
                and provider != package["id"]
                and provider is not None
            ):
                expected_deps.add(provider)
        if set(package["depends_on"]) != expected_deps:
            add(
                "arch_07",
                "ARCH_DEPENDENCY_DERIVATION",
                package["id"],
                f"depends_on must equal {sorted(expected_deps)}",
            )
    package_ids = set(package_by_id)
    edges = {item["id"]: set(item["depends_on"]) for item in packages}
    if any(not deps <= package_ids for deps in edges.values()) or not _acyclic(
        package_ids, edges
    ):
        add("arch_08", "ARCH_WORK_PACKAGE_DAG", "work_packages", "invalid or cyclic DAG")

    expected_files = {
        item["path"] for item in constraints["file_slots"] if item["mutability"] == "s6_owned"
    }
    module_files = [path for item in modules for path in item["owns_files"]]
    if set(module_files) != expected_files or len(module_files) != len(set(module_files)):
        add(
            "arch_09",
            "ARCH_MODULE_FILE_PARTITION",
            "architecture/modules",
            "module files are not an exact disjoint s6_owned partition",
        )
    for module in modules:
        package_files = [
            path
            for package in packages_by_module[module["id"]]
            for path in package["allowed_files"]
        ]
        if set(package_files) != set(module["owns_files"]) or len(package_files) != len(
            set(package_files)
        ):
            add(
                "arch_09",
                "ARCH_PACKAGE_FILE_PARTITION",
                module["id"],
                "work package files are not an exact disjoint module partition",
            )

    requirements = {item["id"]: item for item in spec["requirements"]}
    for req_id, requirement in requirements.items():
        responsibilities = [
            responsibility
            for package in packages
            for responsibility in package["requirement_responsibilities"]
            if responsibility["req_id"] == req_id
        ]
        if requirement["level"] != "DEFINITION" and sum(
            item["role"] == "primary" for item in responsibilities
        ) != 1:
            add(
                "arch_10",
                "ARCH_REQ_PRIMARY",
                req_id,
                "non-definition requirement must have exactly one primary package",
            )
        pairs = [(item["req_id"], item["role"]) for item in responsibilities]
        if len(pairs) != len(set(pairs)):
            add("arch_10", "ARCH_REQ_DUPLICATE", req_id, "duplicate responsibility")

    message_ids = {item["id"] for item in spec.get("messages", [])}
    type_ids = {item["id"] for item in spec.get("types", [])}
    interface_files = {item["path"] for item in constraints["file_slots"]}
    context_namespaces = {
        "message": message_ids,
        "type": type_ids,
        "requirement": set(requirements),
        "interface_file": interface_files,
    }
    for package in packages:
        for ref in package["context_refs"]:
            if ref["id"] not in context_namespaces[ref["kind"]]:
                add("arch_11", "ARCH_CONTEXT_REF", package["id"], f"unknown context ref {ref}")
        for contract_id in package["provides_contracts"] + package["consumes_contracts"]:
            if contract_id not in contract_by_id:
                add("arch_11", "ARCH_CONTRACT_REF", package["id"], contract_id)
        for responsibility in package["requirement_responsibilities"]:
            if responsibility["req_id"] not in requirements:
                add("arch_11", "ARCH_REQ_REF", package["id"], responsibility["req_id"])

    preflight = planning_index.get("preflight", {})
    if preflight.get("fits") is not True or preflight.get("required_tokens", 1) > preflight.get(
        "context_limit", 0
    ):
        add("arch_12", "ARCH_CONTEXT_TOO_LARGE", "planning_index/preflight", "budget does not fit")

    gate_names = [f"arch_{index:02d}" for index in range(1, 13)]
    gate_results = {"schema": True}
    gate_results.update(
        {gate: not any(issue.gate == gate for issue in issues) for gate in gate_names}
    )
    return ArchValidationReport(tuple(issues), gate_results)
