"""四资产到 Delivery Constraints / planning index 的确定性编译。"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from nepa.canonical import canonical_sha256


class DeliveryCompileError(ValueError):
    """输入资产不能确定性编译为闭合约束。"""


class DeliveryBlueprintError(ValueError):
    """架构/任务不能确定性投影为完整 Delivery Blueprint。"""


def _items(value: Any, key: str) -> list[dict[str, Any]]:
    items = value.get(key, []) if isinstance(value, dict) else []
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise DeliveryCompileError(f"{key} must be an array of objects")
    return items


def _normalize_identifier(value: str, target: dict[str, Any]) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    mode = target["naming"]["normalization"]
    if mode == "ascii_lower":
        normalized = normalized.lower()
    elif mode == "ascii_upper":
        normalized = normalized.upper()
    return normalized


def _expansion_ids(
    source: str,
    *,
    spec: dict[str, Any],
    target: dict[str, Any],
) -> list[str | None]:
    if source == "none":
        return [None]
    sources = {
        "spec_messages": [item["id"] for item in _items(spec, "messages")],
        "spec_roles": list(spec.get("protocol", {}).get("roles", [])),
        "deliverables": [item["id"] for item in _items(target, "deliverables")],
        "internal_interface_slots": [
            item["id"] for item in _items(target, "internal_interface_slots")
        ],
    }
    if source not in sources:
        raise DeliveryCompileError(f"unsupported expansion_source: {source}")
    return sorted(sources[source])


def compile_delivery_constraints(
    spec: dict[str, Any],
    target: dict[str, Any],
    language: dict[str, Any],
    test_bundle: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """展开 file_rules，并冻结 contract/build/test/resource namespace。"""
    slots: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for rule in _items(target, "file_rules"):
        expansion_source = rule["expansion_source"]
        values = _expansion_ids(expansion_source, spec=spec, target=target)
        template = rule["path_template"]
        if expansion_source == "none" and "{id}" in template:
            raise DeliveryCompileError(f"{rule['id']}: none expansion cannot use {{id}}")
        if expansion_source != "none" and "{id}" not in template:
            raise DeliveryCompileError(f"{rule['id']}: expanded rule must use {{id}}")
        for expansion_id in values:
            path = (
                template
                if expansion_id is None
                else template.replace(
                    "{id}",
                    _normalize_identifier(str(expansion_id), target),
                )
            )
            if path in seen_paths:
                raise DeliveryCompileError(f"duplicate expanded file slot: {path}")
            seen_paths.add(path)
            slot = {
                "rule_id": rule["id"],
                "path": path,
                "kind": rule["kind"],
                "mutability": rule["mutability"],
                "producer": rule["producer"],
                "expansion_source": expansion_source,
            }
            if expansion_id is not None:
                slot["source_id"] = expansion_id
            if "template_id" in rule:
                slot["template_id"] = rule["template_id"]
            slots.append(slot)

    contracts = deepcopy(target["external_contracts"])
    variants = deepcopy(language["build_variants"])
    contract_ids = {item["id"] for item in contracts}
    variant_ids = {item["id"] for item in variants}
    tests: list[dict[str, Any]] = []
    for item in manifest["tests"]:
        missing_contracts = set(item["required_contracts"]) - contract_ids
        if missing_contracts:
            raise DeliveryCompileError(
                f"{item['nodeid']}: unknown contracts {sorted(missing_contracts)}"
            )
        requested_variants = item.get(
            "build_variant_ids",
            test_bundle["default_build_variant_ids"],
        )
        missing_variants = set(requested_variants) - variant_ids
        if missing_variants:
            raise DeliveryCompileError(
                f"{item['nodeid']}: unknown build variants {sorted(missing_variants)}"
            )
        tests.append(
            {
                "nodeid": item["nodeid"],
                "gate": item["gate"],
                "required_contracts": list(item["required_contracts"]),
                "build_variant_ids": list(requested_variants),
            }
        )

    result = {
        "schema_version": "1.0",
        "file_slots": sorted(slots, key=lambda item: item["path"]),
        "external_contracts": contracts,
        "internal_interface_slots": deepcopy(target["internal_interface_slots"]),
        "resource_limits": deepcopy(target["resource_limits"]),
        "build_variants": variants,
        "tests": sorted(tests, key=lambda item: item["nodeid"]),
    }
    result["content_sha256"] = canonical_sha256(result)
    return result


def compile_delivery_blueprint(
    constraints: dict[str, Any],
    architecture: dict[str, Any],
    work_packages: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    """把已链接的静态 Plan 语义投影成唯一文件 owner/contract 映射。

    该函数无副作用且不读取 workspace；它故意不接收 Plan 顶层、coverage 或
    review，避免把输出哈希引入自身语义输入。
    """
    del work_packages  # ownership is fully represented by linked task files and architecture.
    task_owner: dict[str, str] = {}
    for task in tasks:
        task_id = task.get("id")
        if not isinstance(task_id, str):
            raise DeliveryBlueprintError("task id is required")
        for path in task.get("deliverable_files", []):
            if not isinstance(path, str):
                raise DeliveryBlueprintError(f"{task_id}: invalid deliverable file")
            if path in task_owner:
                raise DeliveryBlueprintError(f"duplicate task owner for {path}")
            task_owner[path] = task_id

    files: list[dict[str, Any]] = []
    for slot in constraints.get("file_slots", []):
        path = slot.get("path")
        mutability = slot.get("mutability")
        if not isinstance(path, str) or mutability not in {"s5_frozen", "s6_owned"}:
            raise DeliveryBlueprintError("invalid delivery constraint slot")
        owner = task_owner.pop(path, None)
        if mutability == "s5_frozen":
            if owner is not None:
                raise DeliveryBlueprintError(f"s5_frozen file owned by task: {path}")
            owner = None
        elif owner is None:
            raise DeliveryBlueprintError(f"s6_owned file has no task owner: {path}")
        files.append(
            {
                "path": path,
                "mutability": mutability,
                "created_by_stage": "s5",
                "owner_task_id": owner,
                "producer": slot["producer"],
            }
        )
    if task_owner:
        raise DeliveryBlueprintError(f"task owns unconstrained files: {sorted(task_owner)}")

    contracts = architecture.get("contracts", [])
    if not isinstance(contracts, list) or not all(isinstance(item, dict) for item in contracts):
        raise DeliveryBlueprintError("architecture contracts must be objects")
    contract_map = [
        {
            key: deepcopy(contract[key])
            for key in ("id", "kind", "purpose", "owner", "interface_files", "ready_gate")
            if key in contract
        }
        for contract in sorted(contracts, key=lambda item: str(item.get("id")))
    ]
    result = {
        "schema_version": "1.0",
        "files": sorted(files, key=lambda item: item["path"]),
        "contracts": contract_map,
    }
    result["content_sha256"] = canonical_sha256(result)
    return result


def _without_quotes(value: Any) -> Any:
    if isinstance(value, list):
        return [_without_quotes(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _without_quotes(item)
            for key, item in value.items()
            if not (key == "quote" and "section" in value)
        }
    return value


def build_planning_index(
    spec: dict[str, Any],
    constraints: dict[str, Any],
    manifest: dict[str, Any],
    *,
    estimated_input_tokens: int,
    output_tokens_reserved: int,
    context_limit: int,
    safety_margin_tokens: int,
) -> dict[str, Any]:
    """生成 ArchitecturePlanner 唯一可见的压缩索引与预算预检。"""
    if min(
        estimated_input_tokens,
        output_tokens_reserved,
        context_limit,
        safety_margin_tokens,
    ) < 0:
        raise DeliveryCompileError("planning token values must be non-negative")
    required = estimated_input_tokens + output_tokens_reserved + safety_margin_tokens
    tests = [
        {
            key: deepcopy(item[key])
            for key in (
                "nodeid",
                "description",
                "req_ids",
                "gate",
                "required_contracts",
                "build_variant_ids",
            )
            if key in item
        }
        for item in manifest["tests"]
    ]
    return {
        "schema_version": "1.0",
        "protocol": deepcopy(spec["protocol"]),
        "transport": _without_quotes(deepcopy(spec.get("transport"))),
        "types": _without_quotes(deepcopy(spec.get("types", []))),
        "messages": _without_quotes(deepcopy(spec.get("messages", []))),
        "requirements": [
            {
                "id": item["id"],
                "level": item["level"],
                "text": item["text"],
                "source_sections": sorted(
                    {
                        ref["section"]
                        for ref in item.get("source_ref", [])
                        if isinstance(ref, dict) and isinstance(ref.get("section"), str)
                    }
                ),
            }
            for item in spec["requirements"]
        ],
        "external_contracts": [
            {
                key: deepcopy(item[key])
                for key in ("id", "purpose", "ready_gate", "interface_files")
            }
            for item in constraints["external_contracts"]
        ],
        "tests": sorted(tests, key=lambda item: item["nodeid"]),
        "preflight": {
            "estimated_input_tokens": estimated_input_tokens,
            "output_tokens_reserved": output_tokens_reserved,
            "safety_margin_tokens": safety_margin_tokens,
            "context_limit": context_limit,
            "required_tokens": required,
            "fits": required <= context_limit,
        },
    }
