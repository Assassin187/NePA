"""The deterministic, protocol-neutral first half of the Delivery Compiler."""

from __future__ import annotations

import hashlib
import re
import json
from pathlib import Path
from typing import Any, Mapping

from .lint import canonical_json_bytes, lint_target


class DeliveryConstraintError(ValueError):
    """A target or deterministic delivery rule cannot be compiled."""

    def __init__(self, message: str, *, code: str = "DELIVERY_CONSTRAINT_INVALID") -> None:
        self.code = code
        super().__init__(message)


_BUILTIN_TYPES = {"uint8", "uint16_be", "uint32_be", "bytes", "bitfield8"}
_LAYOUT_CONVENTION_DIR = Path(__file__).resolve().parents[1] / "assets" / "layout_conventions"


def normalize_identifier(value: str) -> str:
    """Apply the C99 identifier rule declared in system design §5.6.5.2."""

    if not isinstance(value, str):
        raise DeliveryConstraintError("identifier source must be text", code="DERIVED_IDENTIFIER_EMPTY")
    text = value.strip()
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    text = text.strip("_").lower()
    if text and text[0].isdigit():
        text = "p_" + text
    if not text:
        raise DeliveryConstraintError(f"identifier derived from {value!r} is empty", code="DERIVED_IDENTIFIER_EMPTY")
    return text


def _target_or_raise(spec: Mapping[str, Any], target: Mapping[str, Any]) -> None:
    report = lint_target(dict(target), dict(spec))
    if report.get("valid"):
        return
    item = report.get("errors", [{}])[0]
    code = item.get("code", "DELIVERY_CONSTRAINT_INVALID")
    raise DeliveryConstraintError(item.get("message", "target is unsupported"), code=code)


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    try:
        loaded = json.loads(Path(value).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeliveryConstraintError(f"unable to read {label}: {exc}") from exc
    if not isinstance(loaded, Mapping):
        raise DeliveryConstraintError(f"{label} must be a JSON object")
    return loaded


def _derive_ids(spec: Mapping[str, Any]) -> tuple[str, dict[str, str], dict[str, str]]:
    prefix = normalize_identifier(spec["protocol"]["name"])
    message_ids: dict[str, str] = {}
    type_ids: dict[str, str] = {}
    for item in spec.get("messages", []):
        source = item["id"]
        derived = normalize_identifier(source)
        if derived in message_ids.values():
            raise DeliveryConstraintError(f"message identifiers collide at {derived!r}", code="DERIVED_IDENTIFIER_COLLISION")
        message_ids[source] = derived
    for item in spec.get("types", []):
        source = item["id"]
        derived = normalize_identifier(source)
        if derived in type_ids.values():
            raise DeliveryConstraintError(f"type identifiers collide at {derived!r}", code="DERIVED_IDENTIFIER_COLLISION")
        type_ids[source] = derived
    # The final C namespace is shared by generated message/type declarations;
    # a collision must not be hidden by the source category that produced it.
    combined: dict[str, str] = {}
    for namespace, values in (("message", message_ids), ("type", type_ids)):
        for source, derived in values.items():
            previous = combined.get(derived)
            if previous is not None:
                raise DeliveryConstraintError(
                    f"{namespace} identifier {derived!r} collides with {previous}",
                    code="DERIVED_IDENTIFIER_COLLISION",
                )
            combined[derived] = f"{namespace}:{source}"
    return prefix, message_ids, type_ids


def layout_convention_id(target_profile: Mapping[str, Any]) -> str:
    """Derive the versioned convention id from the two target fields."""

    language = target_profile.get("language", {})
    roles = target_profile.get("roles", [])
    if not isinstance(language, Mapping) or not isinstance(roles, list) or len(roles) != 1:
        raise DeliveryConstraintError("target must contain one language and one delivery role", code="LAYOUT_CONVENTION_INVALID")
    language_id = normalize_identifier(language.get("version") or language.get("name", ""))
    role_id = normalize_identifier(roles[0])
    return f"{language_id}-{role_id}-v1"


def load_layout_convention(target_profile: Mapping[str, Any]) -> dict[str, Any]:
    """Load the repository-owned convention selected by a target profile."""

    convention_id = layout_convention_id(target_profile)
    path = _LAYOUT_CONVENTION_DIR / f"{convention_id}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeliveryConstraintError(f"unable to load layout convention {convention_id!r}: {exc}", code="LAYOUT_CONVENTION_MISSING") from exc
    if not isinstance(value, dict) or value.get("convention_id") != convention_id:
        raise DeliveryConstraintError(f"layout convention {convention_id!r} has an invalid identity", code="LAYOUT_CONVENTION_INVALID")
    expected_language = target_profile.get("language")
    if value.get("language") != expected_language or value.get("delivery_form") != target_profile.get("roles", [None])[0]:
        raise DeliveryConstraintError(f"layout convention {convention_id!r} does not match the target", code="LAYOUT_CONVENTION_INVALID")
    if not isinstance(value.get("advisory"), dict) or not isinstance(value.get("hard"), dict):
        raise DeliveryConstraintError("layout convention must separate advisory and hard projections", code="LAYOUT_CONVENTION_INVALID")
    return value


def canonical_layout_convention(target_profile: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """Return the convention and its canonical SHA-256 without external lookups."""

    value = load_layout_convention(target_profile)
    return value, hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def compile_delivery_constraints(spec: Mapping[str, Any], target_profile: Mapping[str, Any]) -> dict[str, Any]:
    """Compile the target-independent portion of the C99 server delivery rules."""

    spec = _object(spec, "Spec IR")
    target_profile = _object(target_profile, "Target Profile")
    _target_or_raise(spec, target_profile)
    prefix, message_ids, type_ids = _derive_ids(spec)
    convention, convention_sha256 = canonical_layout_convention(target_profile)
    patterns = {
        "message_struct": f"{prefix}_{{message_id}}_t",
        "encode_fn": f"{prefix}_encode_{{message_id}}",
        "decode_fn": f"{prefix}_decode_{{message_id}}",
        "type_alias": f"{prefix}_{{type_id}}_t",
        "error_enum": f"{prefix}_result_t",
        "packet_type_enum": f"{prefix}_packet_type_t",
    }
    final_identifiers: dict[str, str] = {}
    def claim(namespace: str, identifier: str) -> None:
        previous = final_identifiers.get(identifier)
        if previous is not None:
            raise DeliveryConstraintError(
                f"derived C identifier {identifier!r} collides between {previous} and {namespace}",
                code="DERIVED_IDENTIFIER_COLLISION",
            )
        final_identifiers[identifier] = namespace
    for source, identifier in message_ids.items():
        claim(f"message:{source}", identifier)
        claim(f"message_struct:{source}", patterns["message_struct"].replace("{message_id}", identifier))
        claim(f"encode_fn:{source}", patterns["encode_fn"].replace("{message_id}", identifier))
        claim(f"decode_fn:{source}", patterns["decode_fn"].replace("{message_id}", identifier))
    for source, identifier in type_ids.items():
        claim(f"type:{source}", identifier)
        claim(f"type_alias:{source}", patterns["type_alias"].replace("{type_id}", identifier))
    for namespace in ("error_enum", "packet_type_enum"):
        claim(namespace, patterns[namespace])
    # The generic server ABI is emitted into the same C namespace as the
    # message/type declarations, so its public types must participate in the
    # collision check before the constraint projection is returned.
    for namespace, identifier in (
        ("server_abi:opaque_server_type", f"{prefix}_server_t"),
        ("server_abi:connection_id_type", f"{prefix}_conn_id_t"),
        ("server_abi:output_batch_type", f"{prefix}_out_batch_t"),
    ):
        claim(namespace, identifier)
    variants = ["release", "san"]
    server_abi = {
        "opaque_server_type": f"{prefix}_server_t",
        "connection_id_type": f"{prefix}_conn_id_t",
        "output_batch_type": f"{prefix}_out_batch_t",
        "events": ["connect", "bytes", "disconnect", "tick"],
        "bytes_event_requires_connection_id": True,
        "output_item_fields": ["connection_id", "bytes", "length", "close"],
        "caller_provided_storage": True,
        "net_layer_owns_io_only": True,
        "limits": {"max_connections": 16, "max_event_targets": 16, "max_output_item_bytes": 4096, "max_output_batch_bytes": 65536},
        "empty_batch_required": True,
        "capacity_failure": "capacity_error",
        "resource_failure": "resource_limit_error_and_close_source",
    }
    return {
        "schema_version": "2.0",
        "target_support": {"roles": sorted(target_profile["roles"]), "language": dict(target_profile["language"]), "supported": True},
        "target_profile": {"roles": sorted(target_profile["roles"]), "language": dict(target_profile["language"])},
        "application_layer_rule": {"language": "C99", "backend": "application-layer", "target_roles": ["server"]},
        "layout_convention_id": convention["convention_id"],
        "layout_convention_sha256": convention_sha256,
        "advisory": convention["advisory"],
        "hard": convention["hard"],
        "mechanical_bounds": convention["mechanical_bounds"],
        "template_roots": convention["template_roots"],
        "naming": {"symbol_prefix": prefix, "identifier_style": "snake_case", "patterns": patterns, "message_ids": message_ids, "type_ids": type_ids},
        "resource_limits": {"max_connections": 16, "max_event_targets": 16, "max_output_item_bytes": 4096, "max_output_batch_bytes": 65536},
        "build_variant_ids": variants,
        "default_build_variant_ids": ["san"],
        "server_abi": server_abi,
        "mechanical_generation_contracts": [
            {"contract_id": "types", "input_kinds": ["spec_types", "derived_naming", "language_type_mappings", "resource_limits"], "template_path": "nepa/templates/types.h"},
            {"contract_id": "codec", "input_kinds": ["spec_messages", "derived_naming"], "template_path": "nepa/templates/codec.h"},
        ],
    }


__all__ = [
    "DeliveryConstraintError",
    "canonical_layout_convention",
    "compile_delivery_constraints",
    "layout_convention_id",
    "load_layout_convention",
    "normalize_identifier",
]
