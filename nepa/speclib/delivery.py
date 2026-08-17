"""The deterministic, protocol-neutral first half of the Delivery Compiler."""

from __future__ import annotations

import re
import json
from pathlib import Path
from typing import Any, Mapping

from .lint import lint_target


class DeliveryConstraintError(ValueError):
    """A target or deterministic delivery rule cannot be compiled."""

    def __init__(self, message: str, *, code: str = "DELIVERY_CONSTRAINT_INVALID") -> None:
        self.code = code
        super().__init__(message)


_BUILTIN_TYPES = {"uint8", "uint16_be", "uint32_be", "bytes", "bitfield8"}


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


def _messages_for_target(spec: Mapping[str, Any], target: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    roles = set(target["roles"])
    selected = [
        message for message in spec.get("messages", [])
        if roles.intersection(message.get("senders", [])) or roles.intersection(message.get("receivers", []))
    ]
    return sorted(selected, key=lambda item: item["id"].encode("utf-8"))


def _rules(prefix: str) -> list[dict[str, Any]]:
    return [
        {"id": "build-file", "kind": "build", "producer": "layout_template", "mutability": "s5_frozen", "path_pattern": "Makefile", "expansion": "none", "purpose": "Build entry point"},
        {"id": "readme", "kind": "documentation", "producer": "layout_template", "mutability": "s5_frozen", "path_pattern": "README.md", "expansion": "none", "purpose": "Generated project description"},
        {"id": "types-header", "kind": "header", "producer": "mechanical_spec", "mutability": "s5_frozen", "path_pattern": f"include/{prefix}/{prefix}_types.h", "expansion": "none", "purpose": "Generated type declarations"},
        {"id": "codec-header", "kind": "header", "producer": "mechanical_spec", "mutability": "s5_frozen", "path_pattern": f"include/{prefix}/{prefix}_codec.h", "expansion": "none", "purpose": "Generated codec declarations"},
        {"id": "session-header", "kind": "header", "producer": "layout_template", "mutability": "s5_frozen", "path_pattern": f"include/{prefix}/{prefix}_session.h", "expansion": "none", "purpose": "Session interface"},
        {"id": "net-header", "kind": "header", "producer": "layout_template", "mutability": "s5_frozen", "path_pattern": f"include/{prefix}/{prefix}_net.h", "expansion": "none", "purpose": "Network interface"},
        {"id": "message-codecs", "kind": "source", "producer": "layout_template", "mutability": "s6_owned", "path_pattern": "src/codec/codec_{message_id}.c", "expansion": "per_message", "purpose": "Per-message codec implementation"},
        {"id": "session-source", "kind": "source", "producer": "layout_template", "mutability": "s6_owned", "path_pattern": f"src/session/{prefix}_session.c", "expansion": "none", "purpose": "Session implementation"},
        {"id": "net-source", "kind": "source", "producer": "layout_template", "mutability": "s6_owned", "path_pattern": f"src/net/{prefix}_net.c", "expansion": "none", "purpose": "Network implementation"},
        {"id": "server-entry-source", "kind": "app", "producer": "layout_template", "mutability": "s6_owned", "path_pattern": f"apps/{prefix}_server_main.c", "expansion": "none", "purpose": "Application entry point"},
    ]


def _expand_rules(rules: list[dict[str, Any]], messages: list[Mapping[str, Any]], message_ids: Mapping[str, str]) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    for rule in rules:
        pattern = rule["path_pattern"]
        if rule["expansion"] == "none":
            if "{" in pattern or "}" in pattern:
                raise DeliveryConstraintError(f"non-expanded path contains a placeholder: {pattern}", code="DELIVERY_PATH_INVALID")
            instances = [(None, pattern)]
        elif rule["expansion"] == "per_message":
            if pattern.count("{message_id}") != 1 or re.sub(r"\{message_id\}", "", pattern).find("{") >= 0:
                raise DeliveryConstraintError(f"invalid message path pattern: {pattern}", code="DELIVERY_PATH_INVALID")
            if not messages:
                raise DeliveryConstraintError("per_message rule has no selected messages", code="DELIVERY_MESSAGE_SET_EMPTY")
            instances = [(message["id"], pattern.replace("{message_id}", message_ids[message["id"]])) for message in messages]
        else:
            raise DeliveryConstraintError(f"unsupported file expansion {rule['expansion']!r}", code="DELIVERY_EXPANSION_INVALID")
        for source, path in instances:
            if not path or path.startswith("/") or ".." in path.split("/") or "//" in path:
                raise DeliveryConstraintError(f"unsafe delivery path {path!r}", code="DELIVERY_PATH_INVALID")
            slots.append({
                "rule_id": rule["id"], "path": path, "kind": rule["kind"], "producer": rule["producer"],
                "mutability": rule["mutability"], "expansion_source": source, "purpose": rule["purpose"],
            })
    seen: set[str] = set()
    for slot in slots:
        if slot["path"] in seen:
            raise DeliveryConstraintError(f"delivery path is duplicated: {slot['path']}", code="DELIVERY_PATH_DUPLICATE")
        seen.add(slot["path"])
    return sorted(slots, key=lambda item: item["path"].encode("utf-8"))


def compile_delivery_constraints(spec: Mapping[str, Any], target_profile: Mapping[str, Any]) -> dict[str, Any]:
    """Compile the target-independent portion of the C99 server delivery rules."""

    spec = _object(spec, "Spec IR")
    target_profile = _object(target_profile, "Target Profile")
    _target_or_raise(spec, target_profile)
    prefix, message_ids, type_ids = _derive_ids(spec)
    messages = _messages_for_target(spec, target_profile)
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
    rules = _rules(prefix)
    slots = _expand_rules(rules, messages, message_ids)
    variants = ["release", "san"]
    internal_slots = [
        {"id": "session-interface", "interface_files": [f"include/{prefix}/{prefix}_session.h"], "required": True, "kind": "header", "purpose": "Session boundary"},
        {"id": "network-interface", "interface_files": [f"include/{prefix}/{prefix}_net.h"], "required": True, "kind": "header", "purpose": "Network boundary"},
    ]
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
        "schema_version": "1.0",
        "target_support": {"roles": sorted(target_profile["roles"]), "language": dict(target_profile["language"]), "supported": True},
        "target_profile": {"roles": sorted(target_profile["roles"]), "language": dict(target_profile["language"])},
        "application_layer_rule": {"language": "C99", "backend": "application-layer", "target_roles": ["server"]},
        "naming": {"symbol_prefix": prefix, "identifier_style": "snake_case", "patterns": patterns, "message_ids": message_ids, "type_ids": type_ids},
        "resource_limits": {"max_connections": 16, "max_event_targets": 16, "max_output_item_bytes": 4096, "max_output_batch_bytes": 65536},
        "build_variant_ids": variants,
        "default_build_variant_ids": ["san"],
        "file_rules": rules,
        "file_slots": slots,
        "internal_interface_slots": internal_slots,
        "server_abi": server_abi,
        "mechanical_generation_contracts": [
            {"input_kinds": ["spec_types", "derived_naming", "language_type_mappings", "resource_limits"], "template_path": "nepa/templates/types.h", "output_rule_ids": ["types-header"]},
            {"input_kinds": ["spec_messages", "derived_naming"], "template_path": "nepa/templates/codec.h", "output_rule_ids": ["codec-header"]},
        ],
    }


__all__ = ["DeliveryConstraintError", "compile_delivery_constraints", "normalize_identifier"]
