"""The deterministic, protocol-neutral first half of the Delivery Compiler."""

from __future__ import annotations

import hashlib
import re
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from ..schemas import load_schema
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
        "message_metadata": [
            {
                "id": item["id"],
                "senders": sorted(item.get("senders", []), key=lambda value: value.encode("utf-8")),
                "receivers": sorted(item.get("receivers", []), key=lambda value: value.encode("utf-8")),
            }
            for item in sorted(spec.get("messages", []), key=lambda item: item["id"].encode("utf-8"))
        ],
        "resource_limits": {"max_connections": 16, "max_event_targets": 16, "max_output_item_bytes": 4096, "max_output_batch_bytes": 65536},
        "build_variant_ids": variants,
        "default_build_variant_ids": ["san"],
        "server_abi": server_abi,
        "mechanical_generation_contracts": [
            {"contract_id": "types", "input_kinds": ["spec_types", "derived_naming", "language_type_mappings", "resource_limits"], "template_path": "nepa/templates/types.h"},
            {"contract_id": "codec", "input_kinds": ["spec_messages", "derived_naming"], "template_path": "nepa/templates/codec.h"},
        ],
    }


_FILE_DERIVATION_TABLE: dict[tuple[str, str, bool, str], tuple[str, str]] = {
    ("header", "s5_frozen", True, "none"): ("header", "layout_template"),
    ("source_stub", "s6_owned", True, "none"): ("header", "s6_task"),
    ("source_stub", "s6_owned", False, "link_source"): ("source", "s6_task"),
    ("source_stub", "s6_owned", False, "entry_point"): ("app", "s6_task"),
    ("build_file", "s5_frozen", False, "none"): ("build", "layout_template"),
    ("doc", "s5_frozen", False, "none"): ("documentation", "layout_template"),
    ("mechanical", "s5_frozen", True, "none"): ("header", "mechanical_spec"),
    ("mechanical", "s5_frozen", False, "link_source"): ("source", "mechanical_spec"),
}

_LAYOUT_TEMPLATE_PATHS = {
    "header": "nepa/templates/layout_header.h",
    "build_file": "nepa/templates/layout_build_file",
    "doc": "nepa/templates/layout_documentation.md",
}
_MECHANICAL_INPUT_KINDS = {"spec_types", "spec_messages", "derived_naming", "language_type_mappings", "resource_limits"}


def _utf8_sorted(values: Any) -> list[Any]:
    if isinstance(values, Mapping):
        values = values.keys()
    if isinstance(values, (str, bytes)):
        return []
    try:
        return sorted(values, key=lambda value: str(value).encode("utf-8"))
    except TypeError:
        return []


def _safe_relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or value.startswith(("/", "\\", "~")) or "\\" in value:
        raise DeliveryConstraintError(f"{label} must be a safe workspace-relative path", code="BLUEPRINT_PATH_UNSAFE")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise DeliveryConstraintError(f"{label} must be a safe workspace-relative path", code="BLUEPRINT_PATH_UNSAFE")
    return value


def _blueprint_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return _object(value, label)


def _message_expansion_domain(constraints: Mapping[str, Any]) -> list[tuple[str, str]]:
    naming = constraints.get("naming", {})
    mappings = naming.get("message_ids", {}) if isinstance(naming, Mapping) else {}
    metadata = constraints.get("message_metadata", [])
    roles = set(constraints.get("target_profile", {}).get("roles", []))
    if not roles:
        roles = set(constraints.get("target_support", {}).get("roles", []))
    metadata_by_id = {item.get("id"): item for item in metadata if isinstance(item, Mapping)}
    selected: list[tuple[str, str]] = []
    for source_id in _utf8_sorted(mappings.keys() if isinstance(mappings, Mapping) else []):
        item = metadata_by_id.get(source_id)
        if item is None:
            raise DeliveryConstraintError(f"message metadata is missing for {source_id!r}", code="BLUEPRINT_EXPANSION_DOMAIN_INVALID")
        senders = set(item.get("senders", []))
        receivers = set(item.get("receivers", []))
        if senders.intersection(roles) or receivers.intersection(roles):
            selected.append((source_id, mappings[source_id]))
    return selected


def _expansion_paths(item: Mapping[str, Any], constraints: Mapping[str, Any]) -> tuple[str, list[str]]:
    path = item.get("path")
    pattern = item.get("path_pattern")
    expand_over = item.get("expand_over")
    if (path is None) == (pattern is None):
        raise DeliveryConstraintError("a layout file must declare exactly one path form", code="BLUEPRINT_PATH_FORM_INVALID")
    if path is not None:
        concrete = _safe_relative_path(path, "layout path")
        if expand_over is not None or re.search(r"\{[^{}]+\}", concrete):
            raise DeliveryConstraintError("literal layout paths cannot use expansion", code="BLUEPRINT_EXPANSION_INVALID")
        return concrete, [concrete]
    if not isinstance(pattern, str) or not pattern:
        raise DeliveryConstraintError("path_pattern must be non-empty", code="BLUEPRINT_PATH_FORM_INVALID")
    placeholders = re.findall(r"\{[^{}]+\}", pattern)
    if expand_over == "messages":
        expected = "{message_id}"
        domain = _message_expansion_domain(constraints)
        expansion = "per_message"
    elif expand_over == "types":
        expected = "{type_id}"
        mappings = constraints.get("naming", {}).get("type_ids", {})
        domain = [(source_id, mappings[source_id]) for source_id in _utf8_sorted(mappings.keys() if isinstance(mappings, Mapping) else [])]
        expansion = "per_type"
    else:
        raise DeliveryConstraintError("path_pattern must declare messages or types expansion", code="BLUEPRINT_EXPANSION_INVALID")
    if placeholders != [expected]:
        raise DeliveryConstraintError("path_pattern must contain exactly one matching placeholder", code="BLUEPRINT_PLACEHOLDER_INVALID")
    if not domain:
        raise DeliveryConstraintError("path expansion domain is empty", code="BLUEPRINT_EXPANSION_EMPTY")
    concrete_paths = [_safe_relative_path(pattern.replace(expected, derived), "expanded layout path") for _source, derived in domain]
    if len(concrete_paths) != len(set(concrete_paths)):
        raise DeliveryConstraintError("expanded layout paths are not unique", code="BLUEPRINT_PATH_DUPLICATE")
    return pattern, concrete_paths


def _task_file_owners(tasks: list[Mapping[str, Any]], concrete_paths: list[str]) -> list[str]:
    owners: list[str] = []
    for task in tasks:
        files = task.get("deliverable_files", [])
        if any(file in concrete_paths for file in files):
            task_id = task.get("id")
            if isinstance(task_id, str):
                owners.append(task_id)
    return sorted(set(owners), key=lambda value: value.encode("utf-8"))


def compile_delivery_blueprint(
    constraints: Mapping[str, Any],
    architecture: Mapping[str, Any],
    work_packages: list[Mapping[str, Any]] | None = None,
    tasks: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compile the accepted free layout into one closed Delivery Blueprint.

    The function intentionally receives the accepted layout and final task
    view as data.  It never reads the workspace and the table below is the
    sole decision point for file kind and producer.
    """

    constraints = _blueprint_mapping(constraints, "Delivery Constraints")
    architecture = _blueprint_mapping(architecture, "ArchitectureDraft")
    work_packages = list(work_packages or [])
    tasks = list(tasks or [])
    layout = architecture.get("layout")
    if not isinstance(layout, Mapping) or not isinstance(layout.get("files"), list) or not isinstance(layout.get("build_graph"), Mapping):
        raise DeliveryConstraintError("accepted architecture must contain a complete layout", code="BLUEPRINT_LAYOUT_INVALID")
    modules = {item.get("id"): item for item in architecture.get("modules", []) if isinstance(item, Mapping)}
    architecture_contracts = {item.get("id"): item for item in architecture.get("contracts", []) if isinstance(item, Mapping)}
    mechanical_contracts = {
        item.get("contract_id"): dict(item)
        for item in constraints.get("mechanical_generation_contracts", [])
        if isinstance(item, Mapping) and isinstance(item.get("contract_id"), str)
    }
    if len(mechanical_contracts) != len([item for item in constraints.get("mechanical_generation_contracts", []) if isinstance(item, Mapping)]):
        raise DeliveryConstraintError("mechanical generation contract ids must be unique", code="BLUEPRINT_MECHANICAL_CONTRACT_INVALID")

    rules: list[dict[str, Any]] = []
    concrete_by_rule: dict[str, list[str]] = {}
    rule_by_slot: dict[str, Mapping[str, Any]] = {}
    mechanical_rule_contracts: dict[str, str] = {}
    all_concrete_paths: set[str] = set()
    for index, item in enumerate(layout["files"]):
        if not isinstance(item, Mapping):
            raise DeliveryConstraintError(f"layout file {index} must be an object", code="BLUEPRINT_LAYOUT_INVALID")
        slot_id = item.get("slot_id")
        if not isinstance(slot_id, str) or slot_id in rule_by_slot:
            raise DeliveryConstraintError(f"layout slot id {slot_id!r} is not unique", code="BLUEPRINT_SLOT_DUPLICATE")
        rule_by_slot[slot_id] = item
        pattern, concrete_paths = _expansion_paths(item, constraints)
        overlap = all_concrete_paths.intersection(concrete_paths)
        if overlap:
            raise DeliveryConstraintError(f"layout path {sorted(overlap, key=lambda value: value.encode('utf-8'))[0]!r} is duplicated", code="BLUEPRINT_PATH_DUPLICATE")
        all_concrete_paths.update(concrete_paths)
        concrete_by_rule[slot_id] = concrete_paths
        render_rule = item.get("render_rule")
        file_class = item.get("class")
        build_role = item.get("build_role")
        contract_id = item.get("contract_id")
        tuple_key = (render_rule, file_class, contract_id is not None, build_role)
        derived = _FILE_DERIVATION_TABLE.get(tuple_key)
        if derived is None:
            raise DeliveryConstraintError(f"layout slot {slot_id!r} uses a table-external file tuple", code="BLUEPRINT_FILE_DERIVATION_INVALID")
        kind, producer = derived
        owner_module = item.get("owner_module")
        if owner_module not in modules:
            raise DeliveryConstraintError(f"layout slot {slot_id!r} names an unknown owner module", code="BLUEPRINT_OWNER_INVALID")
        if render_rule in {"header", "source_stub"} and contract_id is not None and contract_id not in architecture_contracts:
            raise DeliveryConstraintError(f"layout slot {slot_id!r} names an unknown internal contract", code="BLUEPRINT_CONTRACT_UNKNOWN")
        generator_contract_id = None
        if render_rule == "mechanical":
            if contract_id is not None:
                if contract_id not in mechanical_contracts:
                    raise DeliveryConstraintError(f"layout slot {slot_id!r} names an unknown mechanical contract", code="BLUEPRINT_MECHANICAL_CONTRACT_UNKNOWN")
                generator_contract_id = contract_id
            else:
                candidates = _utf8_sorted(mechanical_contracts)
                if len(candidates) != 1:
                    raise DeliveryConstraintError(f"layout slot {slot_id!r} has no unique mechanical generator contract", code="BLUEPRINT_MECHANICAL_OWNERSHIP_INVALID")
                generator_contract_id = candidates[0]
            mechanical_rule_contracts[slot_id] = generator_contract_id
        owners = _task_file_owners(tasks, concrete_paths)
        if file_class == "s6_owned":
            owners_by_path = {
                concrete_path: sorted(
                    {
                        task["id"]
                        for task in tasks
                        if concrete_path in task.get("deliverable_files", []) and isinstance(task.get("id"), str)
                    },
                    key=lambda value: value.encode("utf-8"),
                )
                for concrete_path in concrete_paths
            }
            if any(len(path_owners) != 1 for path_owners in owners_by_path.values()):
                raise DeliveryConstraintError(f"s6_owned layout slot {slot_id!r} must have exactly one task owner per concrete path", code="BLUEPRINT_TASK_OWNER_INVALID")
            owners = sorted({path_owners[0] for path_owners in owners_by_path.values()}, key=lambda value: value.encode("utf-8"))
            if len(owners) != 1:
                raise DeliveryConstraintError(f"s6_owned layout slot {slot_id!r} must have one task owner across its expansion", code="BLUEPRINT_TASK_OWNER_INVALID")
        rule = {
            "id": slot_id,
            "kind": kind,
            "producer": producer,
            "mutability": file_class,
            "path_pattern": pattern,
            "expansion": {"none": "none", "messages": "per_message", "types": "per_type"}.get(item.get("expand_over"), "none"),
            "purpose": item.get("purpose"),
            "owner_module": owner_module,
            "contract_id": contract_id,
            "build_role": build_role,
        }
        if owners:
            rule["owner_task_id"] = owners[0]
        if producer == "layout_template":
            template_path = _LAYOUT_TEMPLATE_PATHS[render_rule]
            rule["template_path"] = template_path
        elif producer == "mechanical_spec":
            rule["template_path"] = mechanical_contracts[generator_contract_id]["template_path"]
        rules.append(rule)

    file_by_id = {item["id"]: item for item in rules}
    if len(file_by_id) != len(layout["files"]):
        raise DeliveryConstraintError("layout files were not transcribed one-to-one", code="BLUEPRINT_FILE_RULE_BIJECTION")
    for task in tasks:
        for path in task.get("deliverable_files", []):
            matches = [
                rule for rule in rules
                if path == rule["path_pattern"] or path in concrete_by_rule.get(rule["id"], [])
            ]
            if len(matches) != 1 or matches[0]["mutability"] != "s6_owned":
                raise DeliveryConstraintError(f"task {task.get('id')!r} claims a non-s6 or unknown layout file", code="BLUEPRINT_TASK_FILE_INVALID")

    artifacts: list[dict[str, Any]] = []
    link_sets: list[dict[str, Any]] = []
    artifact_ids: set[str] = set()
    output_paths: set[str] = set()
    graph_artifacts = layout["build_graph"].get("artifacts", [])
    if not isinstance(graph_artifacts, list) or not graph_artifacts:
        raise DeliveryConstraintError("layout build graph must contain artifacts", code="BLUEPRINT_BUILD_GRAPH_INVALID")
    delivery_roles = _utf8_sorted(constraints.get("target_profile", {}).get("roles", [])) or _utf8_sorted(constraints.get("target_support", {}).get("roles", []))
    deliverables = [{"id": role, "title": role, "kind": role, "purpose": f"{role} delivery"} for role in delivery_roles]
    deliverable_ids = {item["id"] for item in deliverables}
    for index, artifact in enumerate(graph_artifacts):
        if not isinstance(artifact, Mapping):
            raise DeliveryConstraintError(f"build artifact {index} must be an object", code="BLUEPRINT_BUILD_GRAPH_INVALID")
        artifact_id = artifact.get("artifact_id")
        if not isinstance(artifact_id, str) or artifact_id in artifact_ids:
            raise DeliveryConstraintError(f"build artifact id {artifact_id!r} is not unique", code="BLUEPRINT_ARTIFACT_DUPLICATE")
        artifact_ids.add(artifact_id)
        output_path = _safe_relative_path(artifact.get("output_path"), "artifact output path")
        if output_path in output_paths:
            raise DeliveryConstraintError(f"artifact output path {output_path!r} is not unique", code="BLUEPRINT_ARTIFACT_OUTPUT_DUPLICATE")
        output_paths.add(output_path)
        entry_id = artifact.get("entry_file_slot")
        entry_rule = file_by_id.get(entry_id)
        if entry_rule is None or entry_rule.get("build_role") != "entry_point" or entry_rule.get("kind") != "app":
            raise DeliveryConstraintError(f"artifact {artifact_id!r} has an invalid entry slot", code="BLUEPRINT_ENTRY_INVALID")
        source_ids = list(artifact.get("link_source_slots", []))
        if not source_ids or len(source_ids) != len(set(source_ids)):
            raise DeliveryConstraintError(f"artifact {artifact_id!r} must have unique link sources", code="BLUEPRINT_LINK_SOURCE_INVALID")
        for source_id in source_ids:
            source_rule = file_by_id.get(source_id)
            if source_rule is None or source_rule.get("build_role") != "link_source" or source_rule.get("kind") not in {"source", "app"}:
                raise DeliveryConstraintError(f"artifact {artifact_id!r} names an invalid link source", code="BLUEPRINT_LINK_SOURCE_INVALID")
        link_set_id = f"{artifact_id}-sources"
        if link_set_id in {item["id"] for item in link_sets}:
            raise DeliveryConstraintError(f"link source set id {link_set_id!r} is not unique", code="BLUEPRINT_LINK_SOURCE_INVALID")
        link_sets.append({"id": link_set_id, "file_rule_ids": sorted(source_ids, key=lambda value: value.encode("utf-8"))})
        artifacts.append({"id": artifact_id, "deliverable_id": delivery_roles[0] if delivery_roles else "server", "link_source_set_id": link_set_id, "path": output_path, "build_variant_ids": sorted(constraints.get("build_variant_ids", []), key=lambda value: value.encode("utf-8"))})
    expected_shape = constraints.get("hard", {}).get("delivery_shape", {})
    if len(artifacts) != expected_shape.get("executable_artifact_count", len(artifacts)):
        raise DeliveryConstraintError("build artifact count does not match delivery shape", code="BLUEPRINT_ARTIFACT_CARDINALITY")
    if sum(1 for item in rules if item["build_role"] == "entry_point") != expected_shape.get("entry_point_count", 1):
        raise DeliveryConstraintError("entry point count does not match delivery shape", code="BLUEPRINT_ENTRY_CARDINALITY")
    referenced_deliverables = {item["deliverable_id"] for item in artifacts}
    if not deliverables or referenced_deliverables != deliverable_ids:
        raise DeliveryConstraintError("build artifact deliverables are not closed", code="BLUEPRINT_DELIVERABLE_INVALID")
    linked_rules = [rule_id for source_set in link_sets for rule_id in source_set["file_rule_ids"]]
    expected_sources = [item["id"] for item in rules if item["build_role"] == "link_source"]
    if sorted(linked_rules, key=lambda value: value.encode("utf-8")) != sorted(expected_sources, key=lambda value: value.encode("utf-8")):
        raise DeliveryConstraintError("link source slots are not a one-to-one closed set", code="BLUEPRINT_LINK_SOURCE_CLOSURE")

    layout_templates: dict[str, dict[str, Any]] = {}
    for rule in rules:
        if rule["producer"] == "layout_template":
            path = rule["template_path"]
            layout_templates.setdefault(path, {"template_path": path, "output_rule_ids": []})["output_rule_ids"].append(rule["id"])
    for value in layout_templates.values():
        value["output_rule_ids"].sort(key=lambda item: item.encode("utf-8"))
    generated: dict[str, dict[str, Any]] = {}
    for rule in rules:
        if rule["producer"] != "mechanical_spec":
            continue
        contract_id = mechanical_rule_contracts[rule["id"]]
        contract = dict(mechanical_contracts[contract_id])
        input_kinds = contract.get("input_kinds")
        template_path = contract.get("template_path")
        if not isinstance(input_kinds, list) or not input_kinds or not set(input_kinds).issubset(_MECHANICAL_INPUT_KINDS) or not isinstance(template_path, str) or not template_path.startswith(tuple(constraints.get("template_roots", ["nepa/templates"]))):
            raise DeliveryConstraintError(f"mechanical contract {contract_id!r} has an invalid input/template boundary", code="BLUEPRINT_MECHANICAL_CONTRACT_INVALID")
        entry = generated.setdefault(contract_id, {"contract_id": contract_id, "input_kinds": sorted(set(input_kinds), key=lambda item: item.encode("utf-8")), "template_path": template_path, "output_rule_ids": []})
        entry["output_rule_ids"].append(rule["id"])
    for value in generated.values():
        value["output_rule_ids"].sort(key=lambda item: item.encode("utf-8"))
        if "template_context" in mechanical_contracts[value["contract_id"]]:
            value["template_context"] = dict(mechanical_contracts[value["contract_id"]]["template_context"])
    if {rule["id"] for rule in rules if rule["producer"] == "mechanical_spec"} != {rule_id for item in generated.values() for rule_id in item["output_rule_ids"]}:
        raise DeliveryConstraintError("mechanical file rules do not have exactly one generator", code="BLUEPRINT_MECHANICAL_OWNERSHIP_INVALID")
    blueprint = {
        "schema_version": "1.0",
        "target_profile": {"roles": sorted(delivery_roles, key=lambda value: value.encode("utf-8")), "language": dict(constraints.get("target_profile", {}).get("language", {}))},
        "naming": {"symbol_prefix": constraints.get("naming", {}).get("symbol_prefix"), "identifier_style": constraints.get("naming", {}).get("identifier_style"), "patterns": dict(constraints.get("naming", {}).get("patterns", {}))},
        "resource_limits": {key: constraints.get("resource_limits", {})[key] for key in sorted(constraints.get("resource_limits", {}), key=lambda value: value.encode("utf-8"))},
        "deliverables": sorted(deliverables, key=lambda item: item["id"].encode("utf-8")),
        "build_artifacts": sorted(artifacts, key=lambda item: item["id"].encode("utf-8")),
        "link_source_sets": sorted(link_sets, key=lambda item: item["id"].encode("utf-8")),
        "file_rules": sorted(rules, key=lambda item: item["id"].encode("utf-8")),
        "layout_templates": sorted(layout_templates.values(), key=lambda item: item["template_path"].encode("utf-8")),
        "mechanical_generation_contracts": sorted(generated.values(), key=lambda item: item["contract_id"].encode("utf-8")),
    }
    schema_errors = Draft202012Validator(load_schema("delivery-blueprint.schema.json")).iter_errors(blueprint)
    errors = list(schema_errors)
    if errors:
        raise DeliveryConstraintError("compiled Delivery Blueprint failed Schema validation: " + "; ".join(error.message for error in errors), code="BLUEPRINT_SCHEMA_INVALID")
    return blueprint


def canonical_delivery_blueprint(blueprint: Mapping[str, Any]) -> dict[str, Any]:
    """Return a canonical, Schema-validated Blueprint projection."""

    errors = list(Draft202012Validator(load_schema("delivery-blueprint.schema.json")).iter_errors(blueprint))
    if errors:
        raise DeliveryConstraintError("Delivery Blueprint failed Schema validation: " + "; ".join(error.message for error in errors), code="BLUEPRINT_SCHEMA_INVALID")
    return json.loads(canonical_json_bytes(dict(blueprint)).decode("utf-8"))


__all__ = [
    "DeliveryConstraintError",
    "canonical_layout_convention",
    "canonical_delivery_blueprint",
    "compile_delivery_blueprint",
    "compile_delivery_constraints",
    "layout_convention_id",
    "load_layout_convention",
    "normalize_identifier",
]
