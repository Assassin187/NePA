"""S5 确定性模板物化：只能写 Delivery Constraints 已声明的 s5 槽位。"""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined

from nepa.assets import AssetValidationError, bundle_tree_sha256
from nepa.tools.fs_ops import atomic_write_text, resolve_workspace_path, sha256_file


class ScaffoldError(RuntimeError):
    """模板引用、槽位或渲染结果不闭合。"""


def _slot_map(
    constraints: dict[str, Any],
    *,
    producer: str,
) -> dict[str, dict[str, Any]]:
    return {
        item["path"]: item
        for item in constraints["file_slots"]
        if item["mutability"] == "s5_frozen" and item["producer"] == producer
    }


def materialize_target_templates(
    workspace: str | Path,
    *,
    workspace_root: str | Path,
    target: dict[str, Any],
    constraints: dict[str, Any],
) -> tuple[Path, ...]:
    """复制 Target template，模板树每个文件都必须有唯一 file_rule 槽位。"""
    root = Path(workspace_root).resolve()
    slots = _slot_map(constraints, producer="target_template")
    templates = {item["id"]: item for item in target["templates"]}
    produced: dict[str, Path] = {}
    for template_id in sorted({item["template_id"] for item in slots.values()}):
        template = templates.get(template_id)
        if template is None:
            raise ScaffoldError(f"unknown target template: {template_id}")
        source_root = (root / template["path"]).resolve()
        if not source_root.is_relative_to(root) or not source_root.is_dir():
            raise ScaffoldError(f"invalid target template path: {template['path']}")
        if bundle_tree_sha256(source_root) != template["sha256"]:
            raise ScaffoldError(f"target template hash mismatch: {template_id}")
        for source in sorted(
            (path for path in source_root.rglob("*") if path.is_file()),
            key=lambda path: path.relative_to(source_root).as_posix(),
        ):
            relative = source.relative_to(source_root).as_posix()
            slot = slots.get(relative)
            if slot is None or slot.get("template_id") != template_id:
                raise ScaffoldError(f"template file has no matching file_rule slot: {relative}")
            if relative in produced:
                raise ScaffoldError(f"multiple templates produce slot: {relative}")
            destination = resolve_workspace_path(workspace, relative)
            atomic_write_text(destination, source.read_text(encoding="utf-8"))
            produced[relative] = destination
    missing = set(slots) - set(produced)
    if missing:
        raise ScaffoldError(f"target template slots not produced: {sorted(missing)}")
    return tuple(produced[path] for path in sorted(produced))


def materialize_language_build_file(
    workspace: str | Path,
    *,
    workspace_root: str | Path,
    language: dict[str, Any],
    constraints: dict[str, Any],
) -> Path:
    """StrictUndefined 渲染唯一 language_template 构建槽位。"""
    slots = _slot_map(constraints, producer="language_template")
    if len(slots) != 1:
        raise ScaffoldError("exactly one language_template build slot is required")
    relative, slot = next(iter(slots.items()))
    if slot["kind"] != "build":
        raise ScaffoldError("language_template slot must be kind=build")
    root = Path(workspace_root).resolve()
    template_ref = language["toolchain"]["build_file_template"]
    template_path = (root / template_ref["path"]).resolve()
    if not template_path.is_relative_to(root) or not template_path.is_file():
        raise ScaffoldError("language build template path is invalid")
    if sha256_file(template_path) != template_ref["sha256"]:
        raise AssetValidationError("language build template SHA-256 mismatch")
    artifacts: list[dict[str, Any]] = []
    for item in constraints.get("build_artifacts", []):
        artifact = deepcopy(item)
        sources: list[dict[str, str]] = []
        for source_path in item["source_paths"]:
            stem = re.sub(r"[^A-Za-z0-9_.-]", "_", source_path).removesuffix(".c")
            sources.append(
                {
                    "path": source_path,
                    "object_path": f"build/.nepa/{item['id']}/{stem}.o",
                }
            )
        artifact["sources"] = sources
        artifacts.append(artifact)
    if not artifacts:
        raise ScaffoldError("delivery constraints contain no build artifacts")
    rendered = Environment(
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    ).from_string(template_path.read_text(encoding="utf-8")).render(
        build_artifacts=artifacts
    )
    destination = resolve_workspace_path(workspace, relative)
    atomic_write_text(destination, rendered)
    return destination


def _c_identifier(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not normalized or normalized[0].isdigit():
        normalized = "_" + normalized
    return normalized


def _mechanical_context(
    *,
    spec: dict[str, Any],
    target: dict[str, Any],
    language: dict[str, Any],
    input_kinds: set[str],
    output_path: str,
    template_context: dict[str, str],
) -> dict[str, Any]:
    """只从机械契约声明的输入域构造模板上下文。"""
    required = {"spec_messages", "target_naming"}
    if not required <= input_kinds:
        raise ScaffoldError(
            f"mechanical template for {output_path} requires {sorted(required)}"
        )
    naming = target["naming"]
    symbol_prefix = str(naming["symbol_prefix"])
    symbol_prefix_upper = symbol_prefix.upper()
    type_kinds = {
        str(item["id"]): str(item.get("encoding", {}).get("kind", ""))
        for item in spec.get("types", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }

    def c_type(type_id: str) -> str:
        scalars = {
            "uint8": "uint8_t",
            "bitfield8": "uint8_t",
            "uint16_be": "uint16_t",
            "uint32_be": "uint32_t",
        }
        if type_id in scalars:
            return scalars[type_id]
        kind = type_kinds.get(type_id)
        if kind == "varint":
            return "uint32_t"
        if kind == "length_prefixed_string":
            return f"{symbol_prefix}string_t"
        return f"{symbol_prefix}bytes_t"

    messages: list[dict[str, Any]] = []
    for message in spec.get("messages", []):
        message_id = _c_identifier(str(message["id"]))
        fields = [
            {
                "declaration": (
                    f"{c_type(str(field['type']))} "
                    f"{_c_identifier(str(field['name']))}"
                )
            }
            for field in message.get("fields", [])
        ]
        if not fields:
            fields = [{"declaration": "uint8_t reserved"}]
        messages.append(
            {
                "id": message_id,
                "type_name": f"{symbol_prefix}{message_id}_t",
                "fields": fields,
            }
        )
    include_guard = re.sub(r"[^A-Za-z0-9]", "_", output_path).upper() + "_"
    context = {
        "include_guard": include_guard,
        "messages": messages,
        "symbol_prefix": symbol_prefix,
        "symbol_prefix_upper": symbol_prefix_upper,
        "language_type_mappings": (
            language.get("type_mappings", [])
            if "language_type_mappings" in input_kinds
            else []
        ),
        "resource_limits": (
            target.get("resource_limits", [])
            if "target_resource_limits" in input_kinds
            else []
        ),
        "spec_types": spec.get("types", []) if "spec_types" in input_kinds else [],
    }
    overlap = set(context) & set(template_context)
    if overlap:
        raise ScaffoldError(f"mechanical template_context overrides reserved keys: {overlap}")
    context.update(template_context)
    return context


def materialize_mechanical_files(
    workspace: str | Path,
    *,
    workspace_root: str | Path,
    spec: dict[str, Any],
    target: dict[str, Any],
    language: dict[str, Any],
    constraints: dict[str, Any],
) -> tuple[Path, ...]:
    """按独立机械契约渲染全部 mechanical_spec/s5_frozen 槽位。"""
    root = Path(workspace_root).resolve()
    templates = {item["id"]: item for item in target["templates"]}
    slots = _slot_map(constraints, producer="mechanical_spec")
    contracts = constraints.get("mechanical_generation_contracts", [])
    by_rule: dict[str, dict[str, Any]] = {}
    for contract in contracts:
        for rule_id in contract["output_rule_ids"]:
            if rule_id in by_rule:
                raise ScaffoldError(f"mechanical rule has multiple contracts: {rule_id}")
            by_rule[rule_id] = contract
    produced: list[Path] = []
    for relative, slot in sorted(slots.items()):
        rule_id = str(slot["rule_id"])
        contract = by_rule.get(rule_id)
        if contract is None:
            raise ScaffoldError(f"mechanical slot has no contract: {rule_id}")
        template_id = str(slot["template_id"])
        if contract["template_id"] != template_id:
            raise ScaffoldError(f"mechanical template mismatch: {rule_id}")
        template = templates.get(template_id)
        if template is None:
            raise ScaffoldError(f"unknown mechanical template: {template_id}")
        template_root = (root / template["path"]).resolve()
        if (
            not template_root.is_relative_to(root)
            or not template_root.is_dir()
            or bundle_tree_sha256(template_root) != template["sha256"]
        ):
            raise ScaffoldError(f"invalid mechanical template tree: {template_id}")
        template_path = (template_root / str(slot["template_path"])).resolve()
        if not template_path.is_relative_to(template_root) or not template_path.is_file():
            raise ScaffoldError(f"invalid mechanical template path: {rule_id}")
        context = _mechanical_context(
            spec=spec,
            target=target,
            language=language,
            input_kinds=set(contract["input_kinds"]),
            output_path=relative,
            template_context=dict(contract.get("template_context", {})),
        )
        rendered = Environment(
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        ).from_string(template_path.read_text(encoding="utf-8")).render(**context)
        destination = resolve_workspace_path(workspace, relative)
        atomic_write_text(destination, rendered)
        produced.append(destination)
    return tuple(produced)


def materialize_stubs(
    workspace: str | Path,
    *,
    constraints: dict[str, Any],
) -> tuple[Path, ...]:
    """创建所有 s6_owned stub 槽位；内容只由文件种类决定。"""
    produced: list[Path] = []
    for slot in constraints.get("file_slots", []):
        if slot.get("mutability") != "s6_owned" or slot.get("producer") != "stub":
            continue
        relative = str(slot["path"])
        kind = slot.get("kind")
        if kind == "app":
            content = (
                "#include <stdlib.h>\n\n"
                "int main(int argc, char **argv) {\n"
                "    (void)argc;\n"
                "    (void)argv;\n"
                "    return EXIT_FAILURE;\n"
                "}\n"
            )
        elif kind == "internal_header":
            guard = re.sub(r"[^A-Za-z0-9]", "_", relative).upper() + "_"
            content = f"#ifndef {guard}\n#define {guard}\n\n#endif\n"
        else:
            content = "/* S5 deterministic stub; implemented by the owning S6 task. */\n"
        destination = resolve_workspace_path(workspace, relative)
        atomic_write_text(destination, content)
        produced.append(destination)
    return tuple(produced)
