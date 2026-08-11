"""Deterministic M0 validation for Spec IR and Target Profile inputs."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"
BUILTIN_TYPES = {"uint8", "uint16_be", "uint32_be", "bytes", "bitfield8"}
SUPPORTED_LANGUAGE = {"name": "C", "version": "C99"}
SUPPORTED_ROLES = {"server"}
BUILTIN_BUILD_VARIANTS = {"release", "san"}
TEST_GATES = {"s5", "task", "s7_only"}
TEST_LAYERS = {"l0", "l1", "l2", "l3"}
GOLD_COVERAGE_GATES = {"task", "s7_only"}


def _path(parts: tuple[Any, ...] | list[Any]) -> str:
    if not parts:
        return "/"
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _report(errors: list[dict[str, str]], warnings: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {"valid": not errors, "errors": errors, "warnings": warnings or []}


def canonical_json_bytes(value: Any) -> bytes:
    """Encode a JSON value using the Chapter 5 canonical byte sequence."""

    def check_keys(item: Any, path: str = "/") -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError(f"object key at {path} must be a string")
                check_keys(child, f"{path}{key}/")
        elif isinstance(item, list):
            for index, child in enumerate(item):
                check_keys(child, f"{path}{index}/")

    check_keys(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _read_json(source: str | Path | dict[str, Any]) -> tuple[Any | None, list[dict[str, str]]]:
    if isinstance(source, dict):
        return source, []
    try:
        path = Path(source)
        return json.loads(path.read_text(encoding="utf-8")), []
    except (OSError, json.JSONDecodeError) as exc:
        return None, [_issue("INPUT_INVALID", "/", str(exc))]


def _read_json_with_raw(source: str | Path | dict[str, Any]) -> tuple[Any | None, bytes | None, list[dict[str, str]]]:
    if isinstance(source, dict):
        return source, None, []
    try:
        raw = Path(source).read_bytes()
        return json.loads(raw), raw, []
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, None, [_issue("INPUT_INVALID", "/", str(exc))]


def _schema_errors(data: Any, schema_name: str) -> list[dict[str, str]]:
    schema_path = SCHEMA_DIR / schema_name
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
    except (OSError, json.JSONDecodeError) as exc:
        return [_issue("SCHEMA_INVALID", "/", str(exc))]

    errors = []
    for error in sorted(validator.iter_errors(data), key=lambda item: tuple(item.absolute_path)):
        errors.append(_issue("SCHEMA_INVALID", _path(list(error.absolute_path)), error.message))
    if schema_name == "run.schema.json" and isinstance(data, dict):
        created_at = data.get("created_at")
        if isinstance(created_at, str) and re.fullmatch(
            r"[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]+)?Z",
            created_at,
        ):
            try:
                datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                errors.append(
                    _issue(
                        "SCHEMA_INVALID",
                        "/created_at",
                        "created_at must be a valid UTC ISO8601 datetime",
                    )
                )
    if schema_name == "test-summary.schema.json" and not errors and isinstance(data, dict):
        errors.extend(_test_summary_semantic_errors(data))
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


def _lint_spec_manifest_coverage(
    spec_data: dict[str, Any],
    manifest: str | Path | dict[str, Any],
    errors: list[dict[str, str]],
) -> None:
    manifest_data, manifest_errors = _read_json(manifest)
    if manifest_errors:
        errors.extend(
            _issue("SPEC_MANIFEST_INVALID", item["path"], item["message"])
            for item in manifest_errors
        )
        return

    schema_errors = _schema_errors(manifest_data, "test-bundle.schema.json")
    if schema_errors:
        errors.extend(
            _issue("SPEC_MANIFEST_INVALID", item["path"], item["message"])
            for item in schema_errors
        )
        return

    _check_requirement_coverage(spec_data, manifest_data, errors)


def _check_requirement_coverage(
    spec_data: dict[str, Any],
    manifest_data: dict[str, Any],
    errors: list[dict[str, str]],
) -> None:
    covered: set[str] = {
        req_id
        for test in manifest_data["tests"]
        if test["gate"] in GOLD_COVERAGE_GATES
        for req_id in test["req_ids"]
    }
    for index, requirement in enumerate(spec_data["requirements"]):
        if requirement["level"] in {"MUST", "MUST NOT"} and requirement["id"] not in covered:
            errors.append(
                _issue(
                    "SPEC_REQUIREMENT_UNCOVERED",
                    f"/requirements/{index}/id",
                    f"requirement {requirement['id']!r} has no task or s7_only coverage",
                )
            )


def lint_spec(
    source: str | Path | dict[str, Any],
    gold: bool = False,
    manifest: str | Path | dict[str, Any] | None = None,
) -> dict[str, Any]:
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

    warnings: list[dict[str, str]] = []
    if gold:
        if manifest is None:
            warnings.append(
                _issue(
                    "SPEC_COVERAGE_SKIPPED",
                    "/",
                    "gold coverage requires a Test Bundle manifest",
                )
            )
        else:
            _lint_spec_manifest_coverage(data, manifest, errors)
    elif manifest is not None:
        warnings.append(
            _issue(
                "SPEC_COVERAGE_SKIPPED",
                "/",
                "Test Bundle manifest is used only with --gold",
            )
        )
    return _report(errors, warnings)


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


def _round_order(round_id: str) -> tuple[Any, ...]:
    match = re.fullmatch(r"(.*?)(\d+)$", round_id)
    if match:
        return (0, match.group(1), int(match.group(2)))
    return (1, round_id)


def _test_summary_semantic_errors(data: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []

    seen_variants: dict[str, int] = {}
    for index, build_result in enumerate(data["build_results"]):
        variant_id = build_result["variant_id"]
        if variant_id in seen_variants:
            errors.append(
                _issue(
                    "TEST_SUMMARY_VARIANT_DUPLICATE",
                    f"/build_results/{index}/variant_id",
                    f"variant_id duplicates build result at /build_results/{seen_variants[variant_id]}/variant_id",
                )
            )
        else:
            seen_variants[variant_id] = index

    seen_nodeids: dict[str, int] = {}
    for index, case in enumerate(data["cases"]):
        nodeid = case["nodeid"]
        if nodeid in seen_nodeids:
            errors.append(
                _issue(
                    "TEST_SUMMARY_NODEID_DUPLICATE",
                    f"/cases/{index}/nodeid",
                    f"nodeid duplicates case at /cases/{seen_nodeids[nodeid]}/nodeid",
                )
            )
        else:
            seen_nodeids[nodeid] = index

    current_order = _round_order(data["round_id"])
    parent_round_id = data["parent_round_id"]
    if current_order[0] == 0 and current_order[2] == 1:
        if parent_round_id is not None:
            errors.append(
                _issue(
                    "TEST_SUMMARY_PARENT_ROUND_INVALID",
                    "/parent_round_id",
                    "the first round must have parent_round_id null",
                )
            )
    elif parent_round_id is None:
        errors.append(
            _issue(
                "TEST_SUMMARY_PARENT_ROUND_INVALID",
                "/parent_round_id",
                "a non-first round must have a parent_round_id",
            )
        )
    elif _round_order(parent_round_id) >= current_order:
        errors.append(
            _issue(
                "TEST_SUMMARY_PARENT_ROUND_INVALID",
                "/parent_round_id",
                "parent_round_id must be less than round_id",
            )
        )

    return errors


def lint_test_summary(source: str | Path | dict[str, Any]) -> dict[str, Any]:
    """Validate Test Summary structure and its deterministic cross-item relations."""

    data, errors = _read_json(source)
    if errors:
        return _report(errors)
    errors.extend(_schema_errors(data, "test-summary.schema.json"))
    if errors or not isinstance(data, dict):
        return _report(errors)
    return _report(errors)


def _nodeid_layer(nodeid: str) -> str | None:
    test_path = nodeid.split("::", 1)[0]
    parts = test_path.split("/")
    if len(parts) < 3 or parts[0] != "tests":
        return None
    match = re.fullmatch(r"(l[0-3])_[^/]+", parts[1])
    return match.group(1) if match else None


def lint_test_bundle(
    source: str | Path | dict[str, Any],
    spec: str | Path | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate declarative Test Bundle metadata without collecting or running tests."""

    data, raw_bytes, errors = _read_json_with_raw(source)
    if errors:
        return _report(errors)
    schema_errors = _schema_errors(data, "test-bundle.schema.json")
    errors.extend(schema_errors)
    if not isinstance(data, dict):
        return _report(errors)

    bundle = data.get("bundle")
    tests = data.get("tests")
    if isinstance(bundle, dict):
        default_variants = bundle.get("default_build_variant_ids")
        if isinstance(default_variants, list):
            for index, variant in enumerate(default_variants):
                if isinstance(variant, str) and variant not in BUILTIN_BUILD_VARIANTS:
                    errors.append(
                        _issue(
                            "TEST_BUILD_VARIANT_UNSUPPORTED",
                            f"/bundle/default_build_variant_ids/{index}",
                            f"build variant {variant!r} is not provided by the built-in C99 rules",
                        )
                    )

    requirements: dict[str, dict[str, Any]] | None = None
    spec_data_for_coverage: dict[str, Any] | None = None
    if spec is not None:
        spec_data, spec_errors = _read_json(spec)
        if spec_errors:
            errors.extend(
                _issue("TEST_SPEC_INVALID", item["path"], item["message"])
                for item in spec_errors
            )
        else:
            spec_schema_errors = _schema_errors(spec_data, "specs-requirements.schema.json")
            if spec_schema_errors:
                errors.extend(
                    _issue("TEST_SPEC_INVALID", item["path"], item["message"])
                    for item in spec_schema_errors
                )
            elif isinstance(spec_data, dict):
                requirements = {item["id"]: item for item in spec_data["requirements"]}
                spec_data_for_coverage = spec_data

    if isinstance(tests, list):
        seen_nodeids: dict[str, int] = {}
        for index, test in enumerate(tests):
            if not isinstance(test, dict):
                continue
            base = f"/tests/{index}"
            nodeid = test.get("nodeid")
            if isinstance(nodeid, str):
                if nodeid in seen_nodeids:
                    errors.append(
                        _issue(
                            "TEST_NODEID_DUPLICATE",
                            f"{base}/nodeid",
                            f"nodeid duplicates test at /tests/{seen_nodeids[nodeid]}/nodeid",
                        )
                    )
                else:
                    seen_nodeids[nodeid] = index

                nodeid_layer = _nodeid_layer(nodeid)
                layer = test.get("layer")
                if nodeid_layer is not None and isinstance(layer, str) and nodeid_layer != layer:
                    errors.append(
                        _issue(
                            "TEST_NODEID_LAYER_MISMATCH",
                            f"{base}/layer",
                            f"layer {layer!r} does not match nodeid directory layer {nodeid_layer!r}",
                        )
                    )
                elif nodeid_layer is None and isinstance(layer, str) and layer in TEST_LAYERS:
                    errors.append(
                        _issue(
                            "TEST_NODEID_LAYER_MISMATCH",
                            f"{base}/nodeid",
                            "nodeid must use a tests/l<N>_*/ directory matching layer",
                        )
                    )

            gate = test.get("gate")
            if isinstance(gate, str) and gate not in TEST_GATES:
                errors.append(
                    _issue(
                        "TEST_GATE_UNSUPPORTED",
                        f"{base}/gate",
                        f"gate {gate!r} is not one of {sorted(TEST_GATES)!r}",
                    )
                )

            if requirements is not None and isinstance(test.get("req_ids"), list):
                for req_index, req_id in enumerate(test["req_ids"]):
                    if isinstance(req_id, str) and req_id not in requirements:
                        errors.append(
                            _issue(
                                "TEST_REQUIREMENT_UNKNOWN",
                                f"{base}/req_ids/{req_index}",
                                f"unknown requirement {req_id!r}",
                            )
                        )

            variants = test.get("build_variant_ids")
            if isinstance(variants, list):
                for variant_index, variant in enumerate(variants):
                    if isinstance(variant, str) and variant not in BUILTIN_BUILD_VARIANTS:
                        errors.append(
                            _issue(
                                "TEST_BUILD_VARIANT_UNSUPPORTED",
                                f"{base}/build_variant_ids/{variant_index}",
                                f"build variant {variant!r} is not provided by the built-in C99 rules",
                            )
                        )

        if spec_data_for_coverage is not None and not schema_errors:
            _check_requirement_coverage(spec_data_for_coverage, data, errors)

    if not errors:
        try:
            canonical = canonical_json_bytes(data)
        except (TypeError, ValueError) as exc:
            errors.append(_issue("TEST_CANONICAL_JSON_INVALID", "/", str(exc)))
        else:
            if raw_bytes is not None and raw_bytes != canonical:
                errors.append(
                    _issue(
                        "TEST_CANONICAL_JSON_NONCANONICAL",
                        "/",
                        "input bytes must equal canonical JSON bytes",
                    )
                )
    return _report(errors)
