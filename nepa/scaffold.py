"""S5 确定性模板物化：只能写 Delivery Constraints 已声明的 s5 槽位。"""

from __future__ import annotations

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
    context: dict[str, Any],
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
    rendered = Environment(
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    ).from_string(template_path.read_text(encoding="utf-8")).render(**context)
    destination = resolve_workspace_path(workspace, relative)
    atomic_write_text(destination, rendered)
    return destination
