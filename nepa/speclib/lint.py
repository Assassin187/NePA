"""Deterministic M0 validation for Spec IR and Target Profile inputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"
BUILTIN_TYPES = {"uint8", "uint16_be", "uint32_be", "bytes", "bitfield8"}
SUPPORTED_LANGUAGE = {"name": "C", "version": "C99"}
SUPPORTED_ROLES = {"server"}


def _path(parts: tuple[Any, ...] | list[Any]) -> str:
    if not parts:
        return "/"
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _report(errors: list[dict[str, str]], warnings: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {"valid": not errors, "errors": errors, "warnings": warnings or []}


def _read_json(source: str | Path | dict[str, Any]) -> tuple[Any | None, list[dict[str, str]]]:
    if isinstance(source, dict):
        return source, []
    try:
        path = Path(source)
        return json.loads(path.read_text(encoding="utf-8")), []
    except (OSError, json.JSONDecodeError) as exc:
        return None, [_issue("INPUT_INVALID", "/", str(exc))]


def _schema_errors(data: Any, schema_name: str) -> list[dict[str, str]]:
    schema_path = SCHEMA_DIR / schema_name
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
    except (OSError, json.JSONDecodeError) as exc:
        return [_issue("SCHEMA_INVALID", "/", str(exc))]

    errors = []
    for error in sorted(validator.iter_errors(data), key=lambda item: tuple(item.absolute_path)):
        errors.append(_issue("SCHEMA_INVALID", _path(list(error.absolute_path)), error.message))
    return errors


def _check_requirement_refs(
    req_ids: Any,
    location: str,
    requirements: dict[str, dict[str, Any]],
    errors: list[dict[str, str]],
) -> None:
    if not isinstance(req_ids, list) or not req_ids:
        errors.append(_issue("SPEC_EVIDENCE_MISSING", location, "req_ids must be non-empty"))
        return
    for index, req_id in enumerate(req_ids):
        requirement = requirements.get(req_id)
        if requirement is None:
            errors.append(_issue("SPEC_REQUIREMENT_UNKNOWN", f"{location}/{index}", f"unknown requirement {req_id!r}"))
        elif not requirement.get("source_ref"):
            errors.append(_issue("SPEC_EVIDENCE_MISSING", f"/requirements/{req_id}/source_ref", "requirement has no source_ref"))


def lint_spec(source: str | Path | dict[str, Any]) -> dict[str, Any]:
    """Validate Spec IR structure, references, evidence, and derived relations."""

    data, errors = _read_json(source)
    if errors:
        return _report(errors)
    errors.extend(_schema_errors(data, "specs-requirements.schema.json"))
    if errors or not isinstance(data, dict):
        return _report(errors)

    protocol = data["protocol"]
    roles = set(protocol["roles"])
    requirements: dict[str, dict[str, Any]] = {}
    for index, requirement in enumerate(data["requirements"]):
        req_id = requirement["id"]
        if req_id in requirements:
            errors.append(_issue("SPEC_REQUIREMENT_DUPLICATE", f"/requirements/{index}/id", f"duplicate requirement {req_id!r}"))
        requirements[req_id] = requirement

    type_ids: set[str] = set()
    for index, type_def in enumerate(data["types"]):
        type_id = type_def["id"]
        if type_id in type_ids:
            errors.append(_issue("SPEC_TYPE_DUPLICATE", f"/types/{index}/id", f"duplicate type {type_id!r}"))
        type_ids.add(type_id)

    known_types = BUILTIN_TYPES | type_ids
    if "transport" in data:
        _check_requirement_refs(data["transport"].get("req_ids"), "/transport/req_ids", requirements, errors)

    for index, type_def in enumerate(data["types"]):
        base = f"/types/{index}"
        _check_requirement_refs(type_def.get("req_ids"), f"{base}/req_ids", requirements, errors)
        encoding = type_def["encoding"]
        kind = encoding["kind"]
        if kind == "sequence":
            for member_index, member in enumerate(encoding["members"]):
                member_type = member.get("type") if isinstance(member, dict) else member
                if member_type not in known_types:
                    errors.append(_issue("SPEC_TYPE_UNKNOWN", f"{base}/encoding/members/{member_index}", f"unknown type {member_type!r}"))
        elif kind == "repeat" and encoding["item_type"] not in known_types:
            errors.append(_issue("SPEC_TYPE_UNKNOWN", f"{base}/encoding/item_type", f"unknown type {encoding['item_type']!r}"))

    message_ids: set[str] = set()
    for index, message in enumerate(data["messages"]):
        base = f"/messages/{index}"
        message_id = message["id"]
        if message_id in message_ids:
            errors.append(_issue("SPEC_MESSAGE_DUPLICATE", f"{base}/id", f"duplicate message {message_id!r}"))
        message_ids.add(message_id)
        for role_key in ("senders", "receivers"):
            for role_index, role in enumerate(message[role_key]):
                if role not in roles:
                    errors.append(_issue("SPEC_ROLE_UNKNOWN", f"{base}/{role_key}/{role_index}", f"unknown protocol role {role!r}"))
        _check_requirement_refs(message.get("req_ids"), f"{base}/req_ids", requirements, errors)
        wire_layout = set(message["wire_layout"])
        for field_index, field in enumerate(message["fields"]):
            field_base = f"{base}/fields/{field_index}"
            if field["loc"] not in wire_layout:
                errors.append(_issue("SPEC_FIELD_LOCATION_UNKNOWN", f"{field_base}/loc", f"field location {field['loc']!r} is not in wire_layout"))
            if field["type"] not in known_types:
                errors.append(_issue("SPEC_TYPE_UNKNOWN", f"{field_base}/type", f"unknown type {field['type']!r}"))
            _check_requirement_refs(field.get("req_ids"), f"{field_base}/req_ids", requirements, errors)
            derived = field.get("derived")
            if derived is not None and derived.get("kind") != "length_of":
                errors.append(_issue("SPEC_DERIVED_UNSUPPORTED", f"{field_base}/derived/kind", "only length_of is permitted"))

    return _report(errors)


def lint_target(source: str | Path | dict[str, Any], spec: str | Path | dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate the closed Target Profile and optional Spec role subset."""

    data, errors = _read_json(source)
    if errors:
        return _report(errors)
    errors.extend(_schema_errors(data, "target-profile.schema.json"))
    if errors or not isinstance(data, dict):
        return _report(errors)

    if data["language"] != SUPPORTED_LANGUAGE:
        errors.append(_issue("TARGET_LANGUAGE_UNSUPPORTED", "/language", "only C99 is supported"))
    for index, role in enumerate(data["roles"]):
        if role not in SUPPORTED_ROLES:
            errors.append(_issue("TARGET_ROLE_UNSUPPORTED", f"/roles/{index}", f"role {role!r} is not supported by C99"))

    if spec is not None:
        spec_data, spec_errors = _read_json(spec)
        if spec_errors:
            errors.extend(_issue("TARGET_SPEC_INVALID", item["path"], item["message"]) for item in spec_errors)
        elif not isinstance(spec_data, dict) or not isinstance(spec_data.get("protocol"), dict):
            errors.append(_issue("TARGET_SPEC_INVALID", "/protocol", "Spec IR has no protocol object"))
        else:
            spec_roles = set(spec_data["protocol"].get("roles", []))
            for index, role in enumerate(data["roles"]):
                if role not in spec_roles:
                    errors.append(_issue("TARGET_ROLE_NOT_IN_SPEC", f"/roles/{index}", f"role {role!r} is not declared by Spec IR"))

    return _report(errors)
