"""M1-4a 冻结资产校验、Test Bundle 树哈希与 canonical 发布。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

from nepa.canonical import atomic_write_canonical_json, canonical_json_bytes

_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
_IGNORED_NAMES = frozenset(
    {
        ".git",
        ".pytest_cache",
        "__pycache__",
        "build",
        "cache",
        "test_results",
        "validation",
    }
)
_IGNORED_SUFFIXES = (".pyc", ".pyo")


class AssetValidationError(ValueError):
    """冻结资产、引用或摘要不满足活动契约。"""


@cache
def _validator(schema_name: str) -> Draft202012Validator:
    schema = json.loads((_SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _schema_validate(value: Any, schema_name: str) -> dict[str, Any]:
    errors = sorted(
        _validator(schema_name).iter_errors(value),
        key=lambda item: [str(part) for part in item.absolute_path],
    )
    if errors:
        detail = "; ".join(
            f"{'/'.join(map(str, item.absolute_path)) or '<root>'}: {item.message}"
            for item in errors[:8]
        )
        raise AssetValidationError(f"{schema_name}: {detail}")
    if not isinstance(value, dict):
        raise AssetValidationError(f"{schema_name}: root must be object")
    return value


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetValidationError(f"{path} 不是可读 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise AssetValidationError(f"{path} 顶层必须为 object")
    return value


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_repo_path(workspace_root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or "\\" in relative:
        raise AssetValidationError(f"不安全的仓库相对路径: {relative!r}")
    root = workspace_root.resolve()
    target = (root / Path(*pure.parts)).resolve()
    if not target.is_relative_to(root):
        raise AssetValidationError(f"路径越出工作区: {relative!r}")
    return target


def _tree_files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise AssetValidationError(f"Test Bundle root 不存在: {root}")
    files: list[Path] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in _IGNORED_NAMES for part in relative.parts):
            continue
        if path.is_symlink():
            raise AssetValidationError(f"Test Bundle 禁止符号链接: {relative.as_posix()}")
        if path.is_file() and not path.name.endswith(_IGNORED_SUFFIXES):
            files.append(path)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def bundle_tree_sha256(root: str | Path) -> str:
    """按 relative-path + NUL + raw-file-sha + LF 计算规范树哈希。"""
    bundle_root = Path(root)
    digest = hashlib.sha256()
    for path in _tree_files(bundle_root):
        relative = path.relative_to(bundle_root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def validate_profile(
    value: Any,
    *,
    kind: str,
    workspace_root: str | Path,
) -> dict[str, Any]:
    """校验 Target/Language Profile 结构及 source_ref 原始字节锚点。"""
    schema_names = {
        "target": "target-profile.schema.json",
        "language": "language-profile.schema.json",
    }
    if kind not in schema_names:
        raise ValueError(f"unknown profile kind: {kind!r}")
    profile = _schema_validate(value, schema_names[kind])
    source_ref = profile["source_ref"]
    source_path = _safe_repo_path(Path(workspace_root), source_ref["path"])
    if not source_path.is_file() or _sha256_file(source_path) != source_ref["sha256"]:
        raise AssetValidationError(f"{kind} profile source_ref 缺失或 SHA-256 不匹配")
    return profile


def resolve_profile_source(
    source_path: str | Path,
    *,
    kind: str,
    workspace_root: str | Path,
) -> dict[str, Any]:
    """把不自引用的源 Profile 解析为带 source/template hash 的活动描述。"""
    root = Path(workspace_root).resolve()
    source = Path(source_path).resolve()
    if not source.is_relative_to(root):
        raise AssetValidationError("Profile source 必须位于工作区内")
    value = deepcopy(_load_object(source))
    value["source_ref"] = {
        "path": source.relative_to(root).as_posix(),
        "sha256": _sha256_file(source),
    }
    if kind == "target":
        for template in value.get("templates", []):
            path = _safe_repo_path(root, template["path"])
            template["sha256"] = (
                bundle_tree_sha256(path) if path.is_dir() else _sha256_file(path)
            )
    elif kind == "language":
        build_template = value["toolchain"]["build_file_template"]
        path = _safe_repo_path(root, build_template["path"])
        build_template["sha256"] = _sha256_file(path)
    return validate_profile(value, kind=kind, workspace_root=root)


def validate_test_bundle(
    value: Any,
    *,
    workspace_root: str | Path,
) -> dict[str, Any]:
    """联合验证 Test Bundle Schema、manifest canonical hash、组件与树摘要。"""
    bundle = _schema_validate(value, "test-bundle.schema.json")
    root = Path(workspace_root)
    bundle_root = _safe_repo_path(root, bundle["bundle_root"])
    manifest_ref = bundle["manifest_ref"]
    manifest_path = _safe_repo_path(root, manifest_ref["path"])
    manifest = _schema_validate(
        _load_object(manifest_path),
        "tests-manifest.schema.json",
    )
    if manifest["schema_version"] != manifest_ref["schema_version"]:
        raise AssetValidationError("Test Bundle manifest schema_version 引用不匹配")
    if manifest_path.read_bytes() != canonical_json_bytes(manifest):
        raise AssetValidationError("Test Manifest v2 必须以 canonical JSON 发布")
    if _sha256_file(manifest_path) != manifest_ref["sha256"]:
        raise AssetValidationError("Test Bundle manifest_ref SHA-256 不匹配")

    refs: list[dict[str, Any]] = []
    refs.extend(item["ref"] for item in bundle["oracle_refs"])
    refs.extend(item["ref"] for item in bundle["adapter_refs"])
    refs.extend(item["ref"] for item in bundle["reference_target_refs"])
    refs.append(
        {
            "path": bundle["runner"]["entrypoint"],
            "sha256": bundle["runner"]["sha256"],
        }
    )
    for ref in refs:
        path = _safe_repo_path(root, ref["path"])
        if not path.is_file() or _sha256_file(path) != ref["sha256"]:
            raise AssetValidationError(f"Test Bundle component ref 不匹配: {ref['path']}")
        if not path.is_relative_to(bundle_root.resolve()):
            raise AssetValidationError(f"Test Bundle component 越出 bundle_root: {ref['path']}")

    actual_tree = bundle_tree_sha256(bundle_root)
    if actual_tree != bundle["bundle_tree_sha256"]:
        raise AssetValidationError("Test Bundle tree SHA-256 不匹配")
    return bundle


def resolve_test_bundle_source(
    source_path: str | Path,
    *,
    workspace_root: str | Path,
) -> dict[str, Any]:
    """解析 Test Bundle source，计算 manifest/component/tree 全部原始字节摘要。"""
    root = Path(workspace_root).resolve()
    source = Path(source_path).resolve()
    if not source.is_relative_to(root):
        raise AssetValidationError("Test Bundle source 必须位于工作区内")
    value = deepcopy(_load_object(source))
    manifest_path = _safe_repo_path(root, value["manifest_ref"]["path"])
    manifest = _load_object(manifest_path)
    value["manifest_ref"]["sha256"] = _sha256_file(manifest_path)
    value["manifest_ref"]["schema_version"] = manifest.get("schema_version")
    runner_path = _safe_repo_path(root, value["runner"]["entrypoint"])
    value["runner"]["sha256"] = _sha256_file(runner_path)
    for collection in ("oracle_refs", "adapter_refs", "reference_target_refs"):
        for item in value[collection]:
            path = _safe_repo_path(root, item["ref"]["path"])
            item["ref"]["sha256"] = _sha256_file(path)
    bundle_root = _safe_repo_path(root, value["bundle_root"])
    value["bundle_tree_sha256"] = bundle_tree_sha256(bundle_root)
    return validate_test_bundle(value, workspace_root=root)


def publish_frozen_asset(
    destination: str | Path,
    value: dict[str, Any],
    *,
    schema_name: str,
) -> dict[str, str]:
    """Schema 校验后 canonical 发布 run 内解析描述，并返回原始字节 ref。"""
    validated = _schema_validate(value, schema_name)
    path = Path(destination)
    atomic_write_canonical_json(path, validated)
    return {"path": path.as_posix(), "sha256": _sha256_file(path)}
