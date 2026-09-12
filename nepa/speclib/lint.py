"""Structural input validation; no protocol-specific semantic gates."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"
BUILTIN_TYPES = {"uint8", "uint16_be", "uint32_be", "bytes", "bitfield8"}


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def read_json(source: str | Path | dict[str, Any]) -> dict[str, Any]:
    value = source if isinstance(source, dict) else json.loads(Path(source).read_bytes())
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or any(p == ".git" for p in path.parts):
        raise ValueError(f"unsafe relative path: {value!r}")
    return path.as_posix()


def _schema_errors(data: Any, name: str) -> list[dict[str, str]]:
    schema = json.loads((SCHEMA_DIR / name).read_bytes())
    return [{"code": "SCHEMA_INVALID", "path": "/" + "/".join(map(str, e.absolute_path)), "message": e.message}
            for e in Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(data)]


def _report(errors: list[dict[str, str]]) -> dict[str, Any]:
    return {"valid": not errors, "errors": errors, "warnings": []}


def type_dependencies(value: dict[str, Any]) -> list[str]:
    encoding = value["encoding"]
    refs = [encoding[k] for k in ("item_type", "length_type", "base_type") if k in encoding]
    refs += [m["type"] if isinstance(m, dict) else m for m in encoding.get("members", [])]
    return refs


def lint_spec(source: str | Path | dict[str, Any]) -> dict[str, Any]:
    try:
        data = read_json(source)
        errors = _schema_errors(data, "specs-requirements.schema.json")
        if errors:
            return _report(errors)
        def issue(code: str, message: str) -> None:
            errors.append({"code": code, "path": "/", "message": message})
        for name in ("requirements", "types", "messages"):
            ids = [v["id"] for v in data[name]]
            if len(ids) != len(set(ids)):
                issue("SPEC_DUPLICATE", f"duplicate {name} ID")
        reqs = {v["id"] for v in data["requirements"]}
        types = {v["id"] for v in data["types"]} | BUILTIN_TYPES
        roles = set(data["protocol"]["roles"])
        objects = [data["transport"], *data["types"], *data["messages"]]
        objects += [f for m in data["messages"] for f in m["fields"]]
        for obj in objects:
            for ref in obj.get("req_ids", []):
                if ref not in reqs:
                    issue("SPEC_REQUIREMENT_UNKNOWN", f"unknown requirement {ref}")
        for value in data["types"]:
            for ref in type_dependencies(value):
                if ref not in types:
                    issue("SPEC_TYPE_UNKNOWN", f"unknown type {ref}")
        for message in data["messages"]:
            for role in message["senders"] + message["receivers"]:
                if role not in roles:
                    issue("SPEC_ROLE_UNKNOWN", f"unknown role {role}")
            for field in message["fields"]:
                if field["type"] not in types:
                    issue("SPEC_TYPE_UNKNOWN", f"unknown type {field['type']}")
                if field["loc"] not in message["wire_layout"]:
                    issue("SPEC_FIELD_LOCATION_UNKNOWN", f"unknown field location {field['loc']}")
        return _report(errors)
    except (OSError, ValueError, TypeError) as exc:
        return _report([{"code": "INPUT_INVALID", "path": "/", "message": str(exc)}])


def lint_target(source: str | Path | dict[str, Any], spec: str | Path | dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        data = read_json(source)
        errors = _schema_errors(data, "target-profile.schema.json")
        if errors:
            return _report(errors)
        if data["language"] != {"name": "C", "version": "C99"} or data["roles"] != ["server"]:
            raise ValueError("initial implementation supports C99 server targets")
        for item in data["builds"]:
            safe_relative(item["artifact"])
        if {b["id"] for b in data["builds"]} != {"release", "san"} or len(data["builds"]) != 2:
            raise ValueError("target must declare release and san exactly once")
        if len({b["artifact"] for b in data["builds"]}) != 2:
            raise ValueError("build variants must have distinct artifacts")
        if spec is not None and not set(data["roles"]).issubset(read_json(spec)["protocol"]["roles"]):
            raise ValueError("target roles are not in Spec")
        return _report([])
    except (OSError, ValueError, TypeError) as exc:
        return _report([{"code": "TARGET_INVALID", "path": "/", "message": str(exc)}])


def lint_acceptance(source: str | Path | dict[str, Any], spec: str | Path | dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        data = read_json(source)
        errors = _schema_errors(data, "acceptance.schema.json")
        if errors:
            return _report(errors)
        for asset in data["assets"]:
            safe_relative(asset)
            if not isinstance(source, dict):
                root = Path(source).resolve().parent
                path = (root / asset).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    raise ValueError(f"missing or out-of-root acceptance asset {asset}")
        ids = [c["id"] for c in data["checks"]]
        if len(ids) != len(set(ids)) or not any(c["required"] for c in data["checks"]):
            raise ValueError("unique checks and at least one required check are necessary")
        if spec is not None:
            reqs = {v["id"] for v in read_json(spec)["requirements"]}
            for check in data["checks"]:
                if not set(check["req_ids"]).issubset(reqs):
                    raise ValueError("acceptance references unknown requirements")
        return _report([])
    except (OSError, ValueError, TypeError) as exc:
        return _report([{"code": "ACCEPTANCE_INVALID", "path": "/", "message": str(exc)}])
