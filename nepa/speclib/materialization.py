"""Pure, protocol-neutral S5 epoch materialization projections."""

from __future__ import annotations

import hashlib
import importlib.resources
import re
import copy
from collections import defaultdict
from typing import Any, Mapping

from jinja2 import Environment, StrictUndefined

from .delivery import expand_file_rules
from .lint import canonical_json_bytes


class MaterializationError(ValueError):
    """A sealed S4 input cannot be rendered as the finite S5 grammar."""

    def __init__(self, message: str, *, code: str = "S5_RENDERABILITY_INVALID") -> None:
        self.code = code
        super().__init__(message)


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_KEYWORDS = {
    "const", "volatile", "signed", "unsigned", "short", "long", "void", "char", "int", "float", "double",
    "struct", "enum", "typedef", "static", "extern", "sizeof", "return", "uint8_t", "uint16_t", "uint32_t",
}
_BUILTIN = _KEYWORDS | {"uint8", "uint16_be", "uint32_be", "bytes", "bitfield8", "size_t", "bool"}


def _sha(value: Any) -> str:
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _utf8(value: Any) -> bytes:
    return str(value).encode("utf-8")


def _balanced(text: str) -> bool:
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack: list[str] = []
    for char in text:
        if char in pairs:
            stack.append(pairs[char])
        elif char in pairs.values():
            if not stack or stack.pop() != char:
                return False
    return not stack


def _split_top_level(text: str, separator: str = ",") -> list[str]:
    result: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(text):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == separator and depth == 0:
            result.append(text[start:index].strip())
            start = index + 1
    result.append(text[start:].strip())
    return [item for item in result if item]


def _identifiers(text: str) -> list[str]:
    return [item for item in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text) if item not in _KEYWORDS]


def _parse_parameter(value: str) -> dict[str, str]:
    value = " ".join(value.strip().split())
    if value == "void":
        raise MaterializationError("void must be the only empty parameter list", code="S5_C99_PARAMETER_INVALID")
    if "..." in value or "(*" in value or ")(" in value:
        raise MaterializationError(f"unsupported function parameter {value!r}", code="S5_C99_PARAMETER_INVALID")
    if "[" in value:
        raise MaterializationError(f"array parameters are outside the supported status-code ABI: {value!r}", code="S5_C99_PARAMETER_INVALID")
    match = re.search(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)$", value)
    if match is None:
        raise MaterializationError(f"function parameter has no canonical name: {value!r}", code="S5_C99_PARAMETER_INVALID")
    c_type = value[:match.start("name")].strip()
    if not c_type or c_type.startswith("extern"):
        raise MaterializationError(f"unsupported function parameter type: {value!r}", code="S5_C99_PARAMETER_INVALID")
    return {"name": match.group("name"), "c_type": c_type}


def parse_c99_declaration(signature: str) -> dict[str, Any]:
    """Parse one complete declaration from the intentionally finite S5 grammar."""

    if not isinstance(signature, str) or not signature.strip():
        raise MaterializationError("declaration must be non-empty", code="S5_C99_DECLARATION_INVALID")
    text = signature.strip()
    if "#" in text or "..." in text or "(*" in text or not _balanced(text):
        raise MaterializationError("declaration contains an unsupported directive, variadic form, function pointer, or unbalanced token", code="S5_C99_DECLARATION_INVALID")
    if "{" in text and not (text.startswith("struct ") or text.startswith("enum ") or text.startswith("typedef struct ") or text.startswith("typedef enum ")):
        raise MaterializationError("function/object bodies are not part of the S5 declaration grammar", code="S5_C99_BODY_FORBIDDEN")
    if not text.endswith(";"):
        raise MaterializationError("declaration has unconsumed tokens", code="S5_C99_UNCONSUMED_TOKENS")
    normalized = " ".join(text[:-1].strip().split()) + ";"

    function = re.match(r"^(?P<return>.+?)\s+(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>.*)\);$", normalized)
    if function is not None:
        return_type = function.group("return").strip()
        if return_type.startswith("extern") or "[" in return_type or "=" in return_type:
            raise MaterializationError("function return type is outside the supported C99 subset", code="S5_C99_FUNCTION_INVALID")
        raw_params = _split_top_level(function.group("params"))
        parameters: list[dict[str, str]] = []
        if raw_params and raw_params != ["void"]:
            parameters = [_parse_parameter(item) for item in raw_params]
        required = _identifiers(return_type)
        required.extend(identifier for item in parameters for identifier in _identifiers(item["c_type"]))
        return {
            "kind": "function", "symbol": function.group("symbol"), "signature": normalized,
            "return_type": return_type, "parameters": parameters, "requires_symbols": sorted(set(required), key=_utf8),
        }

    type_match = re.match(r"^(?P<prefix>(?:typedef\s+)?(?:struct|enum)\s+[A-Za-z_][A-Za-z0-9_]*)(?:\s*\{(?P<body>.*)\})?\s*(?P<alias>[A-Za-z_][A-Za-z0-9_]*)?;$", normalized)
    if type_match is not None:
        tag = type_match.group("prefix").split()[-1]
        symbol = type_match.group("alias") or tag
        required = [] if symbol == tag else [tag]
        body = type_match.group("body")
        if body is not None:
            if normalized.startswith("enum ") or normalized.startswith("typedef enum "):
                members = _split_top_level(body)
                if not members or any(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\s*=\s*[+-]?[0-9]+)?", member) is None for member in members):
                    raise MaterializationError("enum body contains unconsumed tokens", code="S5_C99_UNCONSUMED_TOKENS")
            else:
                fields = [item for item in body.split(";") if item.strip()]
                if not fields:
                    raise MaterializationError("struct body must contain a complete field declaration", code="S5_C99_TYPE_INVALID")
                for field in fields:
                    field_match = re.fullmatch(r"(?P<ctype>[A-Za-z_][A-Za-z0-9_\s]*?(?:\s*\*)*)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:\[[1-9][0-9]*\])?", field.strip())
                    if field_match is None or field.strip().startswith("extern"):
                        raise MaterializationError("struct fields must be finite object declarations", code="S5_C99_TYPE_INVALID")
                    required.extend(item for item in _identifiers(field_match.group("ctype")) if item not in _BUILTIN)
        for reference in re.findall(r"\b(?:struct|enum)\s+([A-Za-z_][A-Za-z0-9_]*)", body or ""):
            if reference != symbol:
                required.append(reference)
        return {"kind": "type", "symbol": symbol, "signature": normalized, "requires_symbols": sorted(set(required), key=_utf8)}

    typedef = re.match(r"^typedef\s+(?P<base>.+?)\s+(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)\s*;$", normalized)
    if typedef is not None:
        return {
            "kind": "type", "symbol": typedef.group("symbol"), "signature": normalized,
            "requires_symbols": [item for item in _identifiers(typedef.group("base")) if item != typedef.group("symbol")],
        }

    constant = re.match(r"^const\s+(?P<ctype>[A-Za-z_][A-Za-z0-9_\s]*?(?:\s*\*)*)\s*(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)(?P<array>\[[1-9][0-9]*\])?\s*(?:=\s*(?P<initializer>[+-]?[0-9]+|[A-Za-z_][A-Za-z0-9_]*))?;$", normalized)
    if constant is not None:
        required = _identifiers(constant.group("ctype"))
        initializer = constant.group("initializer")
        if initializer and _IDENTIFIER.fullmatch(initializer):
            required.append(initializer)
        return {"kind": "constant", "symbol": constant.group("symbol"), "signature": normalized, "array_suffix": constant.group("array") or "", "requires_symbols": required}

    if normalized.startswith("extern"):
        raise MaterializationError("extern object declarations are not accepted", code="S5_EXTERN_OBJECT_FORBIDDEN")
    raise MaterializationError(f"unsupported or incomplete C99 declaration: {signature!r}", code="S5_C99_DECLARATION_INVALID")


def _contract_exports(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for contract in plan.get("architecture", {}).get("contracts", []):
        if not isinstance(contract, Mapping):
            continue
        contract_id = contract.get("id")
        provider = contract.get("provider_task_id", contract.get("provider"))
        provider_task_id = provider if isinstance(provider, str) and provider.startswith("T-") else None
        if provider_task_id is None and contract.get("ready_gate") == "task":
            candidates = [task.get("id") for task in plan.get("tasks", []) if contract_id in task.get("provides_contracts", [])]
            if not candidates and isinstance(provider, str):
                provider_packages = {item.get("id") for item in plan.get("work_packages", []) if item.get("module") == provider}
                candidates = [task.get("id") for task in plan.get("tasks", []) if task.get("work_package") in provider_packages]
            candidates = [item for item in candidates if isinstance(item, str) and item.startswith("T-")]
            if len(candidates) != 1:
                raise MaterializationError(f"contract {contract_id!r} has no unique provider task", code="S5_IMPLEMENTATION_AMBIGUOUS")
            provider_task_id = candidates[0]
        for export in contract.get("exports", []):
            if not isinstance(export, Mapping) or not isinstance(export.get("interface_file"), str) or not isinstance(export.get("symbol"), str):
                raise MaterializationError("contract export is missing its declared identity", code="S5_EXPORT_INVALID")
            parsed = parse_c99_declaration(str(export.get("signature", "")))
            if parsed["symbol"] != export["symbol"]:
                raise MaterializationError(f"export symbol {export['symbol']!r} disagrees with its declaration", code="S5_EXPORT_SYMBOL_DRIFT")
            result.append({
                **dict(export), **parsed, "contract_id": contract_id,
                "contract_owner": contract.get("owner"),
                "provider_task_id": provider_task_id,
                "ready_gate": contract.get("ready_gate"),
            })
    return result


def _implementation_candidates(export: Mapping[str, Any], plan: Mapping[str, Any], concrete_rules: list[Mapping[str, Any]]) -> list[str]:
    explicit = export.get("implementation_file")
    if isinstance(explicit, str):
        return [explicit]
    provider = export.get("provider_task_id")
    if not isinstance(provider, str):
        return []
    task = next((item for item in plan.get("tasks", []) if item.get("id") == provider), None)
    if not isinstance(task, Mapping):
        return []
    owned = set(task.get("deliverable_files", []))
    return sorted({row["path"] for row in concrete_rules if row.get("path") in owned and row.get("build_role") == "link_source"}, key=_utf8)


def derive_rendering_view(plan: Mapping[str, Any], spec: Mapping[str, Any], target: Mapping[str, Any], blueprint: Mapping[str, Any], constraints: Mapping[str, Any]) -> dict[str, Any]:
    """Derive the complete S5 rendering view without mutating any input."""

    del spec, target
    concrete_rules = expand_file_rules(blueprint, constraints)
    exports = _contract_exports(plan)
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_header: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for export in exports:
        by_symbol[export["symbol"]].append(export)
        by_header[export["interface_file"]].append(export)
    if any(len(items) > 1 for items in by_symbol.values()):
        raise MaterializationError("export symbols must have one unique interface binding", code="S5_EXPORT_AMBIGUOUS")
    if "main" in by_symbol:
        raise MaterializationError("main is owned exclusively by the Blueprint entry slot", code="S5_ENTRY_DUPLICATE")
    symbol_to_header = {symbol: rows[0]["interface_file"] for symbol, rows in by_symbol.items()}
    result_symbol = str(constraints.get("naming", {}).get("patterns", {}).get("error_enum", ""))
    for export in exports:
        dependencies = []
        for symbol in export.get("requires_symbols", []):
            if symbol in _BUILTIN or symbol == export["symbol"]:
                continue
            if symbol not in symbol_to_header:
                raise MaterializationError(f"export {export['symbol']!r} references undeclared symbol {symbol!r}", code="S5_SYMBOL_UNDECLARED")
            dependencies.append(symbol)
        export["requires_symbols"] = sorted(set(dependencies), key=_utf8)
        candidates = _implementation_candidates(export, plan, concrete_rules)
        explicit = export.get("implementation_file")
        if isinstance(explicit, str):
            explicit_rules = [row for row in concrete_rules if row.get("path") == explicit and row.get("build_role") == "link_source"]
            if len(explicit_rules) != 1:
                raise MaterializationError(f"export {export['symbol']!r} has no unique declared implementation source", code="S5_IMPLEMENTATION_INVALID")
        if export["kind"] == "function" and export.get("ready_gate") == "s5":
            raise MaterializationError("S5-ready contracts may contain only frozen type or constant declarations", code="S5_C99_FUNCTION_INVALID")
        if export["kind"] == "function":
            if export["return_type"] not in {"int", result_symbol}:
                raise MaterializationError(
                    f"function {export['symbol']!r} does not use the supported status-code return ABI",
                    code="S5_C99_FUNCTION_INVALID",
                )
            if len(candidates) != 1:
                raise MaterializationError(f"function {export['symbol']!r} has {len(candidates)} implementation slots", code="S5_IMPLEMENTATION_AMBIGUOUS")
            export["implementation_file"] = candidates[0]
            export["stub_rule"] = "not_implemented"
            export["not_implemented_expression"] = "-3" if export["return_type"] == "int" else f"({export['return_type']})-3"
        elif candidates:
            export["implementation_file"] = candidates[0]
    graph: dict[str, set[str]] = {export["symbol"]: set(export.get("requires_symbols", [])) for export in exports}
    ordered: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(symbol: str) -> None:
        if symbol in visiting:
            raise MaterializationError(f"declaration dependency cycle includes {symbol!r}", code="S5_DECLARATION_CYCLE")
        if symbol in visited:
            return
        visiting.add(symbol)
        for dependency in sorted(graph[symbol], key=_utf8):
            visit(dependency)
        visiting.remove(symbol)
        visited.add(symbol)
        ordered.append(symbol)

    for symbol in sorted(graph, key=_utf8):
        visit(symbol)
    for export in exports:
        export["includes"] = sorted({symbol_to_header[item] for item in export.get("requires_symbols", []) if symbol_to_header[item] != export["interface_file"]}, key=_utf8)
    artifacts: list[dict[str, Any]] = []
    entry_paths: set[str] = set()
    link_sets = {item.get("id"): item for item in blueprint.get("link_source_sets", []) if isinstance(item, Mapping)}
    for artifact in blueprint.get("build_artifacts", []):
        entry_slot = artifact.get("entry_file_slot")
        entry_rules = [row for row in concrete_rules if row.get("rule_id") == entry_slot and row.get("build_role") == "entry_point"]
        if len(entry_rules) != 1:
            raise MaterializationError(f"artifact {artifact.get('id')!r} has no unique entry source", code="S5_ENTRY_INVALID")
        entry_path = entry_rules[0]["path"]
        if entry_path in entry_paths:
            raise MaterializationError("an executable entry source is bound more than once", code="S5_ENTRY_DUPLICATE")
        entry_paths.add(entry_path)
        link_set = link_sets.get(artifact.get("link_source_set_id"), {})
        link_source_slots = list(link_set.get("file_rule_ids", artifact.get("link_source_slots", [])))
        if entry_slot in link_source_slots:
            raise MaterializationError(f"artifact {artifact.get('id')!r} links its entry source twice", code="S5_ENTRY_DUPLICATE")
        artifacts.append({"id": artifact.get("id"), "path": artifact.get("path"), "entry_file_slot": entry_slot, "entry_path": entry_path, "link_source_slots": link_source_slots, "build_variant_ids": list(artifact.get("build_variant_ids", []))})
    if not artifacts:
        raise MaterializationError("Blueprint contains no executable artifact", code="S5_ENTRY_MISSING")
    return {
        "schema_version": "1.0", "plan_sha256": _sha(plan), "blueprint_sha256": _sha(blueprint),
        "exports": sorted(exports, key=lambda item: (_utf8(item["interface_file"]), _utf8(item["symbol"]))),
        "declaration_order": ordered,
        "headers": [{"path": path, "exports": sorted([item["symbol"] for item in by_header[path]], key=_utf8), "includes": sorted({include for item in by_header[path] for include in item.get("includes", [])}, key=_utf8)} for path in sorted(by_header, key=_utf8)],
        "artifacts": sorted(artifacts, key=lambda item: _utf8(item["id"])),
        "concrete_rules": concrete_rules,
    }


def _c_declaration(export: Mapping[str, Any]) -> str:
    if export["kind"] == "function":
        params = ", ".join(f"{item['c_type']} {item['name']}{item.get('array_suffix', '')}" for item in export.get("parameters", [])) or "void"
        return f"{export['return_type']} {export['symbol']}({params});"
    return str(export["signature"])


def _stub(export: Mapping[str, Any]) -> str:
    params = ", ".join(f"{item['c_type']} {item['name']}" for item in export.get("parameters", [])) or "void"
    lines = [f"{export['return_type']} {export['symbol']}({params}) {{"]
    for item in export.get("parameters", []):
        lines.append(f"    (void){item['name']};")
    expression = export.get("not_implemented_expression")
    if not isinstance(expression, str):
        raise MaterializationError(f"function {export.get('symbol')!r} has no derived NOT_IMPLEMENTED result", code="S5_STUB_RULE_INVALID")
    lines.append(f"    return {expression};")
    lines.append("}")
    return "\n".join(lines)


def _entry_source() -> str:
    return _render_template("entry_source.c", {})


_TEMPLATE_ENV = Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)


def _render_template(name: str, context: Mapping[str, Any]) -> str:
    resource = importlib.resources.files("nepa.templates").joinpath(name)
    try:
        source = resource.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError) as exc:
        raise MaterializationError(f"packaged template is unavailable: {name}", code="S5_TEMPLATE_MISSING") from exc
    return _TEMPLATE_ENV.from_string(source).render(**dict(context))


_PACKAGED_TEMPLATES = {
    f"nepa/templates/{name}": name
    for name in (
        "layout_header.h", "layout_build_file", "layout_documentation.md",
        "types.h", "codec.h", "mechanical_source.c", "task_source_stub.c", "entry_source.c",
    )
}


def _declared_template(rule: Mapping[str, Any], blueprint: Mapping[str, Any]) -> str:
    producer = rule.get("producer")
    if producer == "layout_template":
        path = rule.get("template_path")
    elif producer == "mechanical_spec":
        matches = [
            item.get("template_path")
            for item in blueprint.get("mechanical_generation_contracts", [])
            if rule.get("rule_id") in item.get("output_rule_ids", [])
        ]
        if len(matches) != 1:
            raise MaterializationError(f"mechanical rule {rule.get('rule_id')!r} has no unique template", code="S5_TEMPLATE_BINDING_INVALID")
        path = matches[0]
    elif producer == "s6_task" and rule.get("build_role") == "entry_point":
        path = "nepa/templates/entry_source.c"
    elif producer == "s6_task" and rule.get("kind") == "source" and rule.get("build_role") == "link_source":
        path = "nepa/templates/task_source_stub.c"
    else:
        raise MaterializationError(f"rule {rule.get('rule_id')!r} has no supported explicit render binding", code="S5_TEMPLATE_BINDING_INVALID")
    if path not in _PACKAGED_TEMPLATES:
        raise MaterializationError(f"template path {path!r} is not an allowed packaged template", code="S5_TEMPLATE_BINDING_INVALID")
    return _PACKAGED_TEMPLATES[path]


def render_e0_files(rendering_view: Mapping[str, Any], spec: Mapping[str, Any], target: Mapping[str, Any], blueprint: Mapping[str, Any], constraints: Mapping[str, Any]) -> dict[str, bytes]:
    """Render the complete declared workspace as a deterministic byte map."""

    del spec, target
    exports = rendering_view.get("exports", [])
    files: dict[str, bytes] = {}
    rules = rendering_view.get("concrete_rules") or expand_file_rules(blueprint, constraints)
    declared_rules = expand_file_rules(blueprint, constraints)
    if {row["path"] for row in rules} != {row["path"] for row in declared_rules}:
        raise MaterializationError("rendering view paths do not match the expanded Blueprint", code="S5_RENDER_PATH_CLOSURE")
    for rule in rules:
        path = rule["path"]
        kind = rule.get("kind")
        template_name = _declared_template(rule, blueprint)
        if kind == "header":
            selected = [item for item in exports if item.get("interface_file") == path]
            includes = sorted({include for item in selected for include in item.get("includes", [])}, key=_utf8)
            declarations = "\n".join([*[f'#include "{include}"' for include in includes], *[_c_declaration(item) for item in selected]])
            content = _render_template(template_name, {"declarations": declarations})
        elif rule.get("build_role") == "entry_point":
            content = _render_template(template_name, {})
        elif kind == "source":
            selected = [item for item in exports if item.get("implementation_file") == path and item.get("kind") == "function"]
            includes = sorted({item.get("interface_file") for item in selected if item.get("interface_file")}, key=_utf8)
            definitions = "\n".join([*[f'#include "{include}"' for include in includes], *[_stub(item) for item in selected]])
            content = _render_template(template_name, {"definitions": definitions})
        elif kind == "build":
            artifacts = list(rendering_view.get("artifacts", []))
            if len(artifacts) != 1:
                raise MaterializationError("a build rule requires one uniquely declared artifact", code="S5_BUILD_BINDING_INVALID")
            artifact = artifacts[0]
            source_slots = set(artifact.get("link_source_slots", []))
            source_paths = [row["path"] for row in rules if row.get("id") in source_slots]
            source_paths.append(artifact["entry_path"])
            content = _render_template(template_name, {"sources": " ".join(source_paths), "target": artifact["path"]})
        elif kind == "documentation":
            content = _render_template(template_name, {})
        else:
            raise MaterializationError(f"rule {rule.get('rule_id')!r} has an unsupported render kind", code="S5_TEMPLATE_BINDING_INVALID")
        files[path] = content.replace("\r\n", "\n").encode("utf-8")
    expected = {row["path"] for row in rules}
    if set(files) != expected:
        raise MaterializationError("rendered file map is not closed over Blueprint paths", code="S5_RENDER_PATH_CLOSURE")
    return dict(sorted(files.items(), key=lambda item: item[0].encode("utf-8")))


def build_artifact_manifest(plan_ref: Mapping[str, Any], blueprint: Mapping[str, Any], rendering_view: Mapping[str, Any], epoch: str) -> dict[str, Any]:
    files = []
    for path, data in rendering_view.get("rendered_files", {}).items():
        rule = next((row for row in rendering_view.get("concrete_rules", []) if row.get("path") == path), {})
        mutability = rule.get("mutability", "s5_frozen")
        kind = rule.get("kind", "source")
        build_variants = sorted({
            variant
            for artifact in rendering_view.get("artifacts", [])
            if rule.get("rule_id") in set(artifact.get("link_source_slots", [])) or path == artifact.get("entry_path") or kind in {"header", "build"}
            for variant in artifact.get("build_variant_ids", [])
        }, key=_utf8)
        files.append({
            "rule_id": rule.get("rule_id", rule.get("id", path)), "path": path, "kind": kind,
            "sha256": rendering_view.get("content_hashes", {}).get(path, _sha(data)), "created_by_stage": "s5", "mutability": mutability,
            "owner_task_id": rule.get("owner_task_id") if mutability == "s6_owned" else None,
            "build_variant_ids": build_variants,
        })
    return {
        "schema_version": "2.0", "plan_version": _plan_version(plan_ref), "plan_sha256": plan_ref["sha256"],
        "delivery_blueprint_sha256": _sha(blueprint), "epoch": epoch, "files": files,
        "delivery_graph": {
            "deliverables": list(blueprint.get("deliverables", [])),
            "build_artifacts": list(blueprint.get("build_artifacts", [])),
            "link_source_sets": list(blueprint.get("link_source_sets", [])),
        },
        "exports": [
            {"contract_id": item.get("contract_id"), "symbol": item["symbol"], "signature": item["signature"],
             "interface_file": item["interface_file"], "kind": item["kind"], "implementation_file": item.get("implementation_file")}
            for item in rendering_view.get("exports", [])
        ],
    }


def build_contract_map(plan_ref: Mapping[str, Any], blueprint: Mapping[str, Any], rendering_view: Mapping[str, Any], epoch: str) -> dict[str, Any]:
    exports = list(rendering_view.get("exports", []))
    contracts = []
    for contract_id in sorted({item.get("contract_id") for item in exports}, key=lambda value: _utf8(value or "")):
        rows = [item for item in exports if item.get("contract_id") == contract_id]
        contracts.append({
            "contract_id": contract_id, "owner": rows[0].get("contract_owner"),
            "ready_gate": rows[0].get("ready_gate"), "provider_task_id": rows[0].get("provider_task_id"),
            "interface_files": sorted({item["interface_file"] for item in rows}, key=_utf8),
            "exports": [
                {"symbol": item["symbol"], "signature": item["signature"], "kind": item["kind"],
                 "interface_file": item["interface_file"], "implementation_file": item.get("implementation_file"),
                 "owner_task_id": item.get("provider_task_id")}
                for item in rows
            ],
        })
    return {
        "schema_version": "2.0", "plan_version": _plan_version(plan_ref), "plan_sha256": plan_ref["sha256"],
        "delivery_blueprint_sha256": _sha(blueprint), "epoch": epoch, "contracts": contracts,
    }


def _plan_version(plan_ref: Mapping[str, Any]) -> str:
    value = plan_ref.get("version")
    if isinstance(value, str) and re.fullmatch(r"1\.[0-9]+\.[0-9]+", value):
        return value
    path = plan_ref.get("path")
    match = re.fullmatch(r"plan/versions/plan-(1\.[0-9]+\.[0-9]+)\.json", str(path))
    if match is None:
        raise MaterializationError("Plan ref is not an immutable version path", code="S5_PLAN_REF_INVALID")
    return match.group(1)


def _epoch_number(epoch: Any) -> int:
    match = re.fullmatch(r"E([0-9]+)", str(epoch))
    if match is None:
        raise MaterializationError("epoch must use canonical E<n> spelling", code="S5_EPOCH_INVALID")
    return int(match.group(1))


def _safe_relative_path(value: Any, *, allow_orphan: bool = False) -> str:
    if not isinstance(value, str):
        raise MaterializationError("materialization paths must be strings", code="S5_PATH_INVALID")
    path = value
    pure = re.split(r"[/\\]", path)
    if not path or path.startswith(('/', '\\')) or any(part in {"", ".", ".."} for part in pure) or ".git" in pure:
        raise MaterializationError(f"unsafe materialization path: {path!r}", code="S5_PATH_INVALID")
    if not allow_orphan and pure[0] == "_orphan":
        raise MaterializationError("active materialization paths cannot be under _orphan", code="S5_PATH_INVALID")
    return "/".join(pure)


def _path_hash(files: Mapping[str, Any], path: str) -> str | None:
    if path not in files:
        return None
    value = files[path]
    if isinstance(value, bytes):
        return _sha(value)
    if isinstance(value, str):
        return _sha(value.encode("utf-8"))
    if isinstance(value, Mapping) and isinstance(value.get("sha256"), str):
        return value["sha256"]
    raise MaterializationError(f"workspace fact for {path!r} is not hashable", code="S5_PATH_FACT_INVALID")


def _expanded_inventory(blueprint: Mapping[str, Any], constraints: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = expand_file_rules(blueprint, constraints)
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        path = _safe_relative_path(row.get("path"))
        if path in result:
            raise MaterializationError(f"Blueprint expands the path more than once: {path}", code="S5_BLUEPRINT_PATH_COLLISION")
        result[path] = dict(row)
    return result


def build_epoch_context(
    run: Mapping[str, Any] | None = None,
    plan: Mapping[str, Any] | None = None,
    active_plan: Mapping[str, Any] | None = None,
    plan_ref: Mapping[str, Any] | None = None,
    blueprint: Mapping[str, Any] | None = None,
    constraints: Mapping[str, Any] | None = None,
    *,
    spec: Mapping[str, Any] | None = None,
    target: Mapping[str, Any] | None = None,
    state: Mapping[str, Any] | None = None,
    file_ledger: Mapping[str, Any] | None = None,
    revision_ledger: Mapping[str, Any] | None = None,
    old_plan: Mapping[str, Any] | None = None,
    old_plan_ref: Mapping[str, Any] | None = None,
    old_blueprint: Mapping[str, Any] | None = None,
    predecessor_receipt: Mapping[str, Any] | None = None,
    predecessor_binding: Mapping[str, Any] | None = None,
    activation: Mapping[str, Any] | None = None,
    migration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a copied, filesystem-free admission context for one epoch.

    The function intentionally accepts already-frozen values.  Reading a run,
    pointer, or Blueprint from disk belongs to the controller boundary; this
    projection only checks their cross-object identity and returns no aliases
    to caller-owned mutable input.
    """

    if not all(isinstance(value, Mapping) for value in (plan, active_plan, plan_ref, blueprint, constraints)):
        raise MaterializationError("epoch context is missing a sealed Plan, pointer, Blueprint, or constraints", code="S5_ADMISSION_INVALID")
    assert plan is not None and active_plan is not None and plan_ref is not None and blueprint is not None and constraints is not None
    value_plan = copy.deepcopy(dict(plan))
    value_pointer = copy.deepcopy(dict(active_plan))
    value_plan_ref = copy.deepcopy(dict(plan_ref))
    value_blueprint = copy.deepcopy(dict(blueprint))
    value_constraints = copy.deepcopy(dict(constraints))
    epoch = value_pointer.get("epoch")
    number = _epoch_number(epoch)
    version = _plan_version(value_plan_ref)
    if value_pointer.get("version") != version or value_pointer.get("path") != value_plan_ref.get("path") or value_pointer.get("sha256") != value_plan_ref.get("sha256"):
        raise MaterializationError("active Plan pointer and Plan ref disagree", code="S5_ADMISSION_INVALID")
    if value_plan_ref.get("sha256") != _sha(value_plan):
        raise MaterializationError("active Plan ref hash does not match the sealed Plan", code="S5_PLAN_DRIFT")
    if value_plan.get("delivery_blueprint_sha256") != _sha(value_blueprint):
        raise MaterializationError("active Plan Blueprint binding drifted", code="S5_BLUEPRINT_DRIFT")
    if value_pointer.get("revision_seq") != number and number == 0:
        raise MaterializationError("E0 pointer has an invalid revision sequence", code="S5_ADMISSION_INVALID")
    if file_ledger is not None:
        from .plan_revision import validate_file_ledger
        validate_file_ledger(file_ledger)
    if revision_ledger is not None:
        from .plan_revision import latest_activation, validate_revision_ledger
        validate_revision_ledger(revision_ledger)
        ledger_activation = latest_activation(revision_ledger)
        if number > 0:
            if activation is None:
                activation = ledger_activation
            if not isinstance(activation, Mapping) or dict(activation) != dict(ledger_activation or {}):
                raise MaterializationError("E1+ context is not bound to the latest accepted activation", code="S5_ACTIVATION_INVALID")
        elif ledger_activation is not None:
            raise MaterializationError("E0 context cannot contain an accepted activation", code="S5_ADMISSION_INVALID")
    if number == 0:
        if value_pointer.get("revision_seq") != 0:
            raise MaterializationError("E0 context requires revision sequence zero", code="S5_ADMISSION_INVALID")
        value_old_plan = copy.deepcopy(dict(old_plan or value_plan))
        value_old_ref = copy.deepcopy(dict(old_plan_ref or value_plan_ref))
        value_old_blueprint = copy.deepcopy(dict(old_blueprint or value_blueprint))
        value_migration: Mapping[str, Any] | None = None
    else:
        if not isinstance(activation, Mapping) or activation.get("level") != "F3":
            raise MaterializationError("E1+ materialization requires an accepted F3 activation", code="S5_ACTIVATION_INVALID")
        if activation.get("revision_seq") != value_pointer.get("revision_seq") or activation.get("to_version") != version or activation.get("epoch_after") != epoch or activation.get("to_plan_ref") != {"path": value_plan_ref.get("path"), "sha256": value_plan_ref.get("sha256")}:
            raise MaterializationError("active pointer does not match its F3 activation", code="S5_ACTIVATION_INVALID")
        value_migration = copy.deepcopy(dict(migration or activation.get("migration") or {}))
        if migration is not None and activation.get("migration") is not None and dict(migration) != dict(activation["migration"]):
            raise MaterializationError("migration input is not the frozen activation migration", code="S5_MIGRATION_INVALID")
        if not value_migration:
            raise MaterializationError("E1+ activation has no frozen migration rows", code="S5_MIGRATION_INVALID")
        if not isinstance(old_plan, Mapping) or not isinstance(old_plan_ref, Mapping) or not isinstance(old_blueprint, Mapping):
            raise MaterializationError("E1+ context is missing its predecessor Plan and Blueprint", code="S5_PREDECESSOR_INVALID")
        value_old_plan = copy.deepcopy(dict(old_plan))
        value_old_ref = copy.deepcopy(dict(old_plan_ref))
        value_old_blueprint = copy.deepcopy(dict(old_blueprint))
        if _plan_version(value_old_ref) != activation.get("from_version") or value_old_ref.get("path") != activation.get("from_plan_ref", {}).get("path") or value_old_ref.get("sha256") != activation.get("from_plan_ref", {}).get("sha256"):
            raise MaterializationError("predecessor Plan ref disagrees with the activation", code="S5_PREDECESSOR_INVALID")
        if value_old_ref.get("sha256") != _sha(value_old_plan):
            raise MaterializationError("predecessor Plan ref hash does not match the predecessor Plan", code="S5_PREDECESSOR_INVALID")
        if _epoch_number(predecessor_receipt.get("epoch") if predecessor_receipt else None) != number - 1:
            raise MaterializationError("predecessor receipt is not the immediate prior epoch", code="S5_PREDECESSOR_INVALID")
        checkpoint = predecessor_receipt.get("checkpoint_commit") if predecessor_receipt else None
        if not isinstance(checkpoint, str) or not re.fullmatch(r"[0-9a-f]{40}", checkpoint):
            raise MaterializationError("predecessor receipt has no canonical checkpoint", code="S5_PREDECESSOR_INVALID")
        if not isinstance(predecessor_binding, Mapping) or predecessor_binding.get("epoch_receipt_ref", {}).get("path") != f"plan/epochs/E{number - 1}/receipt.json":
            raise MaterializationError("predecessor binding does not reference the immediate epoch", code="S5_PREDECESSOR_INVALID")
        if predecessor_receipt.get("materialized_plan_ref") != value_old_ref:
            raise MaterializationError("predecessor receipt does not bind the predecessor Plan", code="S5_PREDECESSOR_INVALID")
        if predecessor_binding.get("plan_ref") != value_old_ref or predecessor_binding.get("epoch_receipt_ref", {}).get("sha256") != _sha(predecessor_receipt):
            raise MaterializationError("predecessor binding facts are not mutually bound", code="S5_PREDECESSOR_INVALID")
    if state is not None and isinstance(state.get("plan_ref"), Mapping):
        state_ref = state["plan_ref"]
        if state_ref.get("path") != value_plan_ref.get("path") or state_ref.get("sha256") != value_plan_ref.get("sha256"):
            raise MaterializationError("Plan State is not bound to the active Plan", code="S5_STATE_INVALID")
    old_inventory = _expanded_inventory(value_old_blueprint, value_constraints)
    new_inventory = _expanded_inventory(value_blueprint, value_constraints)
    return {
        "schema_version": "1.0", "epoch": epoch, "epoch_number": number, "plan_version": version, "run": copy.deepcopy(dict(run or {})),
        "plan": value_plan, "plan_ref": value_plan_ref, "active_plan": value_pointer,
        "old_plan": value_old_plan, "old_plan_ref": value_old_ref, "blueprint": value_blueprint,
        "old_blueprint": value_old_blueprint, "constraints": value_constraints,
        "spec": copy.deepcopy(dict(spec or {})), "target": copy.deepcopy(dict(target or {})),
        "state": copy.deepcopy(dict(state or {})), "file_ledger": copy.deepcopy(dict(file_ledger or {})),
        "revision_ledger": copy.deepcopy(dict(revision_ledger or {})),
        "activation": copy.deepcopy(dict(activation or {})), "migration": copy.deepcopy(dict(value_migration or {})),
        "predecessor_receipt": copy.deepcopy(dict(predecessor_receipt or {})),
        "predecessor_binding": copy.deepcopy(dict(predecessor_binding or {})),
        "old_inventory": old_inventory, "new_inventory": new_inventory,
    }


def _migration_file_rows(migration: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = migration.get("files", [])
    if not isinstance(rows, list):
        raise MaterializationError("migration file rows must be an array", code="S5_MIGRATION_INVALID")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            raise MaterializationError("migration file rows must name a path", code="S5_MIGRATION_INVALID")
        if row.get("classification") not in {"INHERIT", "REVALIDATE", "AMEND", "REGENERATE"}:
            raise MaterializationError("migration file rows have an invalid classification", code="S5_MIGRATION_INVALID")
        path = _safe_relative_path(row["path"])
        if path in result:
            raise MaterializationError(f"migration repeats file path {path!r}", code="S5_MIGRATION_INVALID")
        result[path] = dict(row)
    return result


def _operation_rows(migration: Mapping[str, Any], names: tuple[str, ...]) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    for name in names:
        values = migration.get(name, [])
        if values is None:
            continue
        if not isinstance(values, list):
            raise MaterializationError(f"migration {name} must be an array", code="S5_MIGRATION_INVALID")
        if any(not isinstance(value, Mapping) for value in values):
            raise MaterializationError(f"migration {name} rows must be objects", code="S5_MIGRATION_INVALID")
        result.extend(values)
    return result


def plan_epoch_materialization(
    old_blueprint: Mapping[str, Any],
    new_blueprint: Mapping[str, Any],
    rendered_files: Mapping[str, bytes],
    current_ledger: Mapping[str, Any],
    migration: Mapping[str, Any] | None = None,
    *,
    constraints: Mapping[str, Any] | None = None,
    workspace_files: Mapping[str, Any] | None = None,
    epoch: str = "E0",
    plan_version: str | None = None,
) -> dict[str, Any]:
    """Produce the complete deterministic action plan for one epoch.

    ``workspace_files`` is a path-to-bytes (or path-to-hash-fact) snapshot and
    is never read by this function.  It is only used for preimage facts and for
    protecting realized owned content from an accidental renderer overwrite.
    """

    number = _epoch_number(epoch)
    constraints_value = constraints or {}
    old_inventory = _expanded_inventory(old_blueprint, constraints_value)
    new_inventory = _expanded_inventory(new_blueprint, constraints_value)
    from .plan_revision import validate_file_ledger
    validate_file_ledger(current_ledger)
    old_rows = {row["path"]: dict(row) for row in current_ledger.get("files", [])}
    if len(old_rows) != len(current_ledger.get("files", [])):
        raise MaterializationError("current file ledger contains duplicate paths", code="S5_LEDGER_INVALID")
    migration_value = dict(migration or {})
    file_migrations = _migration_file_rows(migration_value) if number else {}
    workspace = dict(workspace_files or {})
    for path in workspace:
        _safe_relative_path(path, allow_orphan=True)
    registered_quarantine_paths = {
        row.get("quarantine_path")
        for row in old_rows.values()
        if row.get("state") == "quarantined" and isinstance(row.get("quarantine_path"), str)
    }
    expected_preimage_paths = {path for path, row in old_rows.items() if row.get("state") != "quarantined"} | registered_quarantine_paths
    if set(workspace) != expected_preimage_paths:
        raise MaterializationError("workspace contains missing or unregistered paths", code="S5_WORKSPACE_DRIFT")
    if number and any(row.get("class") == "s6_owned" and row.get("state") == "realized" and path not in file_migrations for path, row in old_rows.items() if not str(path).startswith("_orphan/")):
        # Realized owned rows are historical obligations and must be explicitly
        # accounted for by the activation. Frozen S5 output is recomputed from
        # the new Blueprint and does not require an ownership migration row.
        raise MaterializationError("migration does not account for every realized file", code="S5_MIGRATION_INVALID")
    rendered = dict(rendered_files)
    if any(not isinstance(path, str) or not isinstance(data, bytes) for path, data in rendered.items()):
        raise MaterializationError("rendered files must be a path-to-bytes map", code="S5_RENDER_PATH_INVALID")
    if set(rendered) - set(new_inventory):
        raise MaterializationError("renderer produced an undeclared path", code="S5_RENDER_PATH_CLOSURE")
    actions: list[dict[str, Any]] = []
    consumed_old: set[str] = set()
    consumed_new: set[str] = set()
    active_rows = {path: row for path, row in old_rows.items() if row.get("state") != "quarantined"}
    quarantine_rows = {path: row for path, row in old_rows.items() if row.get("state") == "quarantined"}

    def add(action: Mapping[str, Any]) -> None:
        value = {key: copy.deepcopy(item) for key, item in action.items()}
        for key in ("path", "source_path", "target_path"):
            if value.get(key) is not None:
                value[key] = _safe_relative_path(value[key], allow_orphan=key == "source_path" or str(value[key]).startswith("_orphan/"))
        actions.append(value)

    re_adopt_rows = _operation_rows(migration_value, ("re_adopt",))
    re_adopt_targets: set[str] = set()
    re_adopt_sources: set[str] = set()
    for row in re_adopt_rows:
        source = row.get("quarantine_path")
        target = row.get("target_path")
        if not isinstance(source, str) or not isinstance(target, str):
            raise MaterializationError("re_adopt must name source and target paths", code="S5_READOPT_INVALID")
        source = _safe_relative_path(source, allow_orphan=True)
        target = _safe_relative_path(target)
        source_row = next((item for item in quarantine_rows.values() if item.get("quarantine_path") == source), None)
        if source_row is None or source in re_adopt_sources or target in re_adopt_targets or target not in new_inventory:
            raise MaterializationError("re_adopt source or target is not a unique registered quarantine transition", code="S5_READOPT_INVALID")
        if source_row.get("content_sha256") != row.get("content_sha256", source_row.get("content_sha256")):
            raise MaterializationError("re_adopt source content hash disagrees with the ledger", code="S5_READOPT_INVALID")
        if target in active_rows:
            raise MaterializationError("re_adopt target collides with an active row", code="S5_READOPT_INVALID")
        declared_rule = new_inventory[target]
        owner = row.get("owner")
        if owner is not None and not isinstance(owner, Mapping):
            raise MaterializationError("re_adopt owner must be an object", code="S5_READOPT_INVALID")
        if isinstance(owner, Mapping) and declared_rule.get("owner_task_id") is not None and owner.get("task_id") not in {None, declared_rule.get("owner_task_id")}:
            raise MaterializationError("re_adopt owner does not match the destination slot", code="S5_READOPT_INVALID")
        owner_value = copy.deepcopy(owner or {"task_id": declared_rule.get("owner_task_id")})
        if plan_version is not None:
            owner_value["plan_version"] = plan_version
        add({"kind": "re_adopt", "source_path": source, "target_path": target, "sha256": source_row.get("content_sha256"), "owner": owner_value})
        re_adopt_sources.add(source); re_adopt_targets.add(target); consumed_new.add(target)

    for path in sorted(new_inventory, key=_utf8):
        if path in consumed_new:
            continue
        rule = new_inventory[path]
        row = active_rows.get(path)
        mutability = rule.get("mutability", "s5_frozen")
        new_hash = _sha(rendered[path]) if path in rendered else None
        if row is None:
            if mutability == "s5_frozen":
                if new_hash is None:
                    raise MaterializationError(f"new frozen path {path!r} has no rendered bytes", code="S5_RENDER_PATH_CLOSURE")
                add({"kind": "render", "path": path, "sha256": new_hash, "content": rendered[path].decode("utf-8")})
            else:
                if new_hash is None:
                    raise MaterializationError(f"new owned slot {path!r} has no deterministic stub", code="S5_RENDER_PATH_CLOSURE")
                add({"kind": "new_stub", "path": path, "sha256": new_hash, "content": rendered[path].decode("utf-8"), "owner": {"task_id": rule.get("owner_task_id")}})
            consumed_new.add(path)
            continue
        consumed_old.add(path); consumed_new.add(path)
        old_class = row.get("class")
        if row.get("state") == "quarantined":
            raise MaterializationError("a quarantined identity may only return through explicit re_adopt", code="S5_READOPT_INVALID")
        if old_class == "s6_owned" and row.get("state") == "realized":
            if mutability != "s6_owned":
                raise MaterializationError("materialization cannot overwrite realized owned bytes", code="S5_REALIZED_OVERWRITE")
            before = row.get("content_sha256")
            actual = _path_hash(workspace, path)
            if actual is not None and before != actual:
                raise MaterializationError(f"realized owned preimage disagrees at {path}", code="S5_PREIMAGE_INVALID")
            migration_row = file_migrations.get(path, {})
            owner = {"plan_version": str(migration_value.get("to_version", "1.0.0")), "task_uid": migration_row.get("new_owner_uid") or (row.get("owner_history", [{}])[-1].get("task_uid") if row.get("owner_history") else None), "task_id": rule.get("owner_task_id")} if rule.get("owner_task_id") else None
            add({"kind": "preserve", "path": path, "sha256": before, "owner": owner})
            continue
        if mutability == "s6_owned":
            if new_hash is None:
                raise MaterializationError(f"owned slot {path!r} has no deterministic stub", code="S5_RENDER_PATH_CLOSURE")
            if row.get("state") == "slot_only" and _path_hash(workspace, path) == new_hash:
                add({"kind": "preserve", "path": path, "sha256": new_hash, "owner": {"task_id": rule.get("owner_task_id")}})
            elif old_class == "s5_frozen":
                add({"kind": "replace", "path": path, "sha256": new_hash, "content": rendered[path].decode("utf-8")})
            else:
                add({"kind": "new_stub", "path": path, "sha256": new_hash, "content": rendered[path].decode("utf-8"), "owner": {"task_id": rule.get("owner_task_id")}})
        else:
            if new_hash is None:
                raise MaterializationError(f"frozen path {path!r} has no rendered bytes", code="S5_RENDER_PATH_CLOSURE")
            if old_class == "s6_owned" and row.get("state") == "realized":
                raise MaterializationError("frozen replacement would overwrite realized owned bytes", code="S5_REALIZED_OVERWRITE")
            add({"kind": "preserve" if row.get("content_sha256") == new_hash else "replace", "path": path, "sha256": new_hash, "content": rendered[path].decode("utf-8")})

    for path in sorted(active_rows, key=_utf8):
        if path in consumed_old:
            continue
        row = active_rows[path]
        if path in new_inventory:
            continue
        if row.get("state") == "slot_only":
            add({"kind": "retire_slot", "path": path, "sha256": None})
        elif row.get("state") == "realized":
            quarantine = f"_orphan/{epoch}/{path}"
            if quarantine in workspace or quarantine in registered_quarantine_paths:
                raise MaterializationError(f"quarantine target already exists for {path!r}", code="S5_QUARANTINE_INVALID")
            if path in file_migrations and file_migrations[path].get("classification") not in {"REGENERATE", "AMEND"}:
                raise MaterializationError(f"retirement of {path!r} is not declared by migration", code="S5_MIGRATION_INVALID")
            add({"kind": "quarantine", "path": path, "source_path": path, "target_path": quarantine, "sha256": row.get("content_sha256")})
        else:
            raise MaterializationError(f"unsupported active ledger state for {path!r}", code="S5_LEDGER_INVALID")
        consumed_old.add(path)
    for path in sorted(quarantine_rows, key=_utf8):
        row = quarantine_rows[path]
        quarantine = row.get("quarantine_path")
        if not isinstance(quarantine, str) or quarantine in re_adopt_sources:
            # A source remains a historical ledger row, but its quarantine file
            # is consumed by the explicitly named move.
            if quarantine not in re_adopt_sources:
                raise MaterializationError("quarantined row has no registered quarantine path", code="S5_QUARANTINE_INVALID")
        else:
            add({"kind": "preserve", "path": quarantine, "sha256": row.get("content_sha256")})
        consumed_old.add(path)
    if consumed_new != set(new_inventory):
        raise MaterializationError("materialization plan does not consume every new Blueprint path", code="S5_PLAN_CLOSURE")
    if consumed_old != set(old_rows):
        raise MaterializationError("materialization plan does not consume every old ledger row", code="S5_PLAN_CLOSURE")
    active_paths = sorted(set(new_inventory), key=_utf8)
    quarantine_paths = sorted({row.get("quarantine_path") for row in old_rows.values() if row.get("state") == "quarantined" and isinstance(row.get("quarantine_path"), str) and row.get("quarantine_path") not in re_adopt_sources}, key=_utf8)
    for action in actions:
        if action.get("kind") in {"render", "replace", "new_stub", "preserve"} and action.get("path") not in set(active_paths) | set(quarantine_paths):
            raise MaterializationError("action targets a path outside the projected closure", code="S5_PLAN_CLOSURE")
    actions.sort(key=lambda item: (str(item.get("target_path", item.get("path", ""))).encode("utf-8"), str(item.get("kind", "")).encode("utf-8"), str(item.get("source_path", "")).encode("utf-8")))
    touched = set()
    facts: list[dict[str, Any]] = []
    for action in actions:
        # A move action names its source twice (``path`` is retained for the
        # ledger identity), so deduplicate paths within that action before
        # checking cross-action consumption.
        paths = list(dict.fromkeys(item for item in (action.get("source_path"), action.get("target_path"), action.get("path")) if isinstance(item, str)))
        for path in paths:
            if path in touched and action.get("kind") not in {"preserve"}:
                raise MaterializationError(f"materialization action path collision at {path}", code="S5_PLAN_COLLISION")
            touched.add(path)
            facts.append({"path": path, "before_sha256": _path_hash(workspace, path), "after_sha256": action.get("sha256") if path == action.get("path") or path == action.get("target_path") else _path_hash(workspace, path), **({"before_path": action.get("source_path"), "after_path": action.get("target_path")} if action.get("kind") in {"quarantine", "re_adopt"} else {})})
    facts.sort(key=lambda item: _utf8(item["path"]))
    return {"schema_version": "1.0", "epoch": epoch, "actions": actions, "expected_path_facts": facts, "active_paths": active_paths, "quarantine_paths": quarantine_paths, "new_inventory": copy.deepcopy(new_inventory), "old_inventory": copy.deepcopy(old_inventory)}


def project_file_ledger(
    initial_ledger: Mapping[str, Any],
    materialization_plan: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    build_evidence: Mapping[str, Any],
    epoch_receipt_ref: Mapping[str, Any],
    *,
    epoch: str | None = None,
) -> dict[str, Any]:
    """Project the post-epoch ledger from a validated action plan."""

    commit = checkpoint.get("commit_sha") or checkpoint.get("sha256")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise MaterializationError("checkpoint must contain a git commit sha", code="S5_CHECKPOINT_INVALID")
    actions = [dict(item) for item in materialization_plan.get("actions", [])]
    if not isinstance(actions, list):
        raise MaterializationError("materialization action plan is not an array", code="S5_PLAN_INVALID")
    target_epoch = epoch or str(materialization_plan.get("epoch") or epoch_receipt_ref.get("epoch") or "E0")
    _epoch_number(target_epoch)
    old_rows = {row["path"]: copy.deepcopy(dict(row)) for row in initial_ledger.get("files", [])}
    by_path = {str(item.get("path")): item for item in actions if item.get("path") is not None}
    by_target = {str(item.get("target_path")): item for item in actions if item.get("target_path") is not None}
    new_inventory = materialization_plan.get("new_inventory", {})
    if not isinstance(new_inventory, Mapping):
        new_inventory = {}
    build_variants = sorted({str(item) for item in build_evidence.get("build_variant_ids", [])}, key=_utf8) or ["release"]
    evidence_ref = build_evidence.get("evidence_ref") or {"path": f"plan/epochs/{target_epoch}/receipt.json", "sha256": _sha(build_evidence)}
    verified = build_evidence.get("materialization_status", "ready") == "ready"
    rows: list[dict[str, Any]] = []
    for path in sorted(new_inventory, key=_utf8):
        rule = new_inventory[path]
        action = by_path.get(path) or by_target.get(path)
        old = old_rows.get(path)
        if action and action.get("kind") in {"render", "replace"} and verified:
            rows.append({"path": path, "class": "s5_frozen", "state": "realized", "created_in_epoch": target_epoch, "content_sha256": action.get("sha256"), "last_commit_sha": commit, "verified_by": {"build_variant_ids": build_variants, "evidence_ref": copy.deepcopy(dict(evidence_ref))}, "created_by_stage": "s5", "epoch_receipt_ref": copy.deepcopy(dict(epoch_receipt_ref))})
        elif action and action.get("kind") in {"render", "replace"}:
            rows.append({"path": path, "class": "s5_frozen", "state": "slot_only"})
        elif action and action.get("kind") == "new_stub":
            rows.append({"path": path, "class": str(rule.get("mutability", "s6_owned")), "state": "slot_only"})
        elif action and action.get("kind") == "re_adopt":
            source = next(
                (row for row in old_rows.values() if row.get("state") == "quarantined" and row.get("quarantine_path") == action.get("source_path")),
                None,
            )
            if source is None:
                raise MaterializationError("re-adoption source disappeared from the ledger", code="S5_LEDGER_DRIFT")
            value = copy.deepcopy(source)
            value["path"] = path
            value["state"] = "realized"
            value.pop("quarantined_in_epoch", None)
            value.pop("quarantine_path", None)
            owner = action.get("owner")
            if isinstance(owner, Mapping) and all(isinstance(owner.get(key), str) for key in ("task_uid", "task_id", "plan_version")):
                owner_value = {key: owner[key] for key in ("plan_version", "task_uid", "task_id")}
                history = value.setdefault("owner_history", [])
                if not history or history[-1] != owner_value:
                    history.append(owner_value)
            rows.append(value)
        elif old and old.get("class") == "s6_owned" and old.get("state") == "realized":
            value = copy.deepcopy(old)
            owner = action.get("owner") if action else None
            if isinstance(owner, Mapping) and owner.get("task_uid") and owner.get("task_id") and owner.get("plan_version"):
                history = value.setdefault("owner_history", [])
                owner_value = {"plan_version": owner["plan_version"], "task_uid": owner["task_uid"], "task_id": owner["task_id"]}
                if history[-1] != owner_value:
                    history.append(owner_value)
            rows.append(value)
        elif old and old.get("class") == "s5_frozen" and old.get("state") == "realized":
            rows.append(copy.deepcopy(old))
        else:
            rows.append({"path": path, "class": str(rule.get("mutability", "s5_frozen")), "state": "slot_only"})
    for path, old in sorted(old_rows.items(), key=lambda item: _utf8(item[0])):
        if path in new_inventory:
            continue
        action = by_path.get(path)
        if old.get("state") == "quarantined" and old.get("quarantine_path") in {item.get("source_path") for item in actions if item.get("kind") == "re_adopt"}:
            continue
        if old.get("state") == "quarantined":
            rows.append(old)
        elif action and action.get("kind") == "quarantine":
            value = copy.deepcopy(old)
            value.update({"state": "quarantined", "quarantined_in_epoch": target_epoch, "quarantine_path": action.get("target_path")})
            rows.append(value)
        elif action and action.get("kind") == "retire_slot":
            continue
        else:
            raise MaterializationError(f"old ledger row {path!r} was dropped without a declared transition", code="S5_LEDGER_DRIFT")
    projected = {"schema_version": "2.0", "files": sorted(rows, key=lambda item: _utf8(item["path"]))}
    from .plan_revision import validate_file_ledger
    validate_file_ledger(projected)
    return projected


def validate_completed_epoch(
    run: Mapping[str, Any],
    plan: Mapping[str, Any],
    blueprint: Mapping[str, Any],
    file_ledger: Mapping[str, Any],
    revision_ledger: Mapping[str, Any],
    epoch_receipt: Mapping[str, Any],
    binding_receipt: Mapping[str, Any],
    manifest: Mapping[str, Any],
    contract_map: Mapping[str, Any],
    workspace_facts: Mapping[str, Any],
    *,
    active_plan: Mapping[str, Any] | None = None,
    require_event: bool = True,
) -> None:
    """Validate an accepted epoch without reading or modifying the filesystem."""

    if run.get("stages", {}).get("s5", {}).get("status") != "done":
        raise MaterializationError("S5 is not accepted", code="S5_ACCEPTANCE_INVALID")
    epoch = epoch_receipt.get("epoch")
    number = _epoch_number(epoch)
    if epoch_receipt.get("materialization_status") not in {"ready", "pending_repair"}:
        raise MaterializationError("epoch receipt has an invalid materialization status", code="S5_RECEIPT_INVALID")
    plan_version = str(plan.get("plan_version") or "1.0.0")
    plan_ref = {"path": f"plan/versions/plan-{plan_version}.json", "sha256": _sha(plan)}
    if isinstance(active_plan, Mapping):
        plan_ref = {"path": active_plan.get("path"), "sha256": active_plan.get("sha256")}
    if epoch_receipt.get("materialized_plan_ref") != plan_ref or binding_receipt.get("plan_ref") != plan_ref:
        raise MaterializationError("epoch or binding receipt does not bind the accepted Plan", code="S5_BINDING_DRIFT")
    if epoch_receipt.get("blueprint_sha256") != _sha(blueprint):
        raise MaterializationError("epoch receipt Blueprint binding drifted", code="S5_BINDING_DRIFT")
    if binding_receipt.get("epoch_receipt_ref", {}).get("path") != f"plan/epochs/{epoch}/receipt.json" or binding_receipt.get("epoch_receipt_ref", {}).get("sha256") != _sha(epoch_receipt):
        raise MaterializationError("binding receipt does not bind the epoch receipt", code="S5_BINDING_DRIFT")
    from .plan_revision import validate_file_ledger, validate_revision_ledger
    validate_file_ledger(file_ledger); validate_revision_ledger(revision_ledger)
    expected_paths = {row["path"] for row in expand_file_rules(blueprint, workspace_facts.get("constraints", {}))} if workspace_facts.get("constraints") else {row["path"] for row in file_ledger.get("files", []) if row.get("state") != "quarantined"}
    ledger_active = {row["path"] for row in file_ledger.get("files", []) if row.get("state") != "quarantined"}
    if ledger_active != expected_paths:
        raise MaterializationError("file ledger active path set drifted", code="S5_LEDGER_DRIFT")
    for value in (manifest, contract_map):
        if value.get("plan_sha256") != plan_ref["sha256"] or value.get("plan_version") != _plan_version(plan_ref) or value.get("delivery_blueprint_sha256") != _sha(blueprint) or value.get("epoch") != epoch:
            raise MaterializationError("manifest/map binding drifted", code="S5_BINDING_DRIFT")
    if binding_receipt.get("manifest_ref", {}).get("sha256") != _sha(manifest) or binding_receipt.get("contract_map_ref", {}).get("sha256") != _sha(contract_map):
        raise MaterializationError("binding receipt does not bind manifest/map", code="S5_BINDING_DRIFT")
    builds = epoch_receipt.get("build_result_refs", []); smokes = epoch_receipt.get("smoke_result_refs", []); groups = epoch_receipt.get("pending_group_ids", [])
    if not isinstance(builds, list) or not builds:
        raise MaterializationError("accepted epoch has no build evidence", code="S5_RECEIPT_INVALID")
    if epoch_receipt.get("materialization_status") == "ready" and (groups or not isinstance(smokes, list) or not smokes):
        raise MaterializationError("ready epoch evidence is incomplete", code="S5_RECEIPT_INVALID")
    if epoch_receipt.get("materialization_status") == "pending_repair" and (not isinstance(groups, list) or groups != sorted(set(groups), key=_utf8) or smokes):
        raise MaterializationError("pending-repair evidence is incomplete", code="S5_RECEIPT_INVALID")
    manifest_paths = {row.get("path") for row in manifest.get("files", [])}
    if manifest_paths != expected_paths:
        raise MaterializationError("artifact manifest path set drifted", code="S5_MANIFEST_DRIFT")
    hashes = workspace_facts.get("file_hashes", {})
    if hashes and any(row.get("sha256") != hashes.get(row.get("path")) for row in manifest.get("files", [])):
        raise MaterializationError("artifact manifest content hash drifted", code="S5_MANIFEST_DRIFT")
    event_matches = [entry for entry in revision_ledger.get("entries", []) if entry.get("event_type") == "epoch_materialized" and entry.get("payload", {}).get("revision_seq") == (active_plan or {}).get("revision_seq", 0)]
    refs = run["stages"]["s5"].get("output_refs", {})
    if require_event and (len(event_matches) != 1 or event_matches[0].get("payload", {}).get("epoch_receipt_ref") != refs.get("epoch_receipt") or event_matches[0].get("payload", {}).get("binding_ref") != refs.get("binding_receipt")):
        raise MaterializationError("accepted epoch event is missing or conflicts", code="S5_EVENT_DRIFT")
    if not require_event and event_matches:
        raise MaterializationError("event-optional validation requires the event suffix to be absent", code="S5_EVENT_DRIFT")
    if workspace_facts.get("paths") is not None:
        allowed = expected_paths | {row.get("quarantine_path") for row in file_ledger.get("files", []) if row.get("state") == "quarantined"}
        if set(workspace_facts["paths"]) != allowed:
            raise MaterializationError("workspace path set does not match accepted ledger", code="S5_WORKSPACE_DRIFT")


def attribute_pending_repair(
    build_results: list[Mapping[str, Any]],
    migration: Mapping[str, Any],
    *,
    changed_paths: set[str] | None = None,
) -> dict[str, Any]:
    """Classify failed compiler/linker diagnostics against frozen group closures."""

    values = migration.get("pending_groups", []) if isinstance(migration, Mapping) else []
    groups = [value for value in values if isinstance(value, Mapping)] if isinstance(values, list) else []
    if not groups:
        return {"publishable": False, "group_ids": [], "reason": "NO_FROZEN_GROUPS"}
    changed = changed_paths or set()
    group_ids: set[str] = set()
    failed = False
    tool_markers = (
        "command not found", "no such file or directory", "permission denied",
        "internal compiler error", "docker:", "sandbox", "cleanup failed",
    )
    path_pattern = re.compile(r"(?m)^(?P<path>[^:\n]+):[0-9]+(?::[0-9]+)?:\s+(?:fatal\s+)?error:")
    symbol_pattern = re.compile(r"undefined reference to\s+[`'](?P<symbol>[^`']+)[`']")
    for result in build_results:
        if result.get("status") == "passed":
            continue
        failed = True
        stderr = str(result.get("stderr", ""))
        lowered = stderr.lower()
        if result.get("timed_out") is not False or not isinstance(result.get("exit_code"), int) or result.get("exit_code") == 0:
            return {"publishable": False, "group_ids": [], "reason": "INELIGIBLE_BUILD_FAILURE"}
        if any(marker in lowered for marker in tool_markers):
            return {"publishable": False, "group_ids": [], "reason": "TOOL_OR_SANDBOX_FAILURE"}
        diagnostics: list[tuple[str, str]] = []
        diagnostics.extend(("path", match.group("path")) for match in path_pattern.finditer(stderr))
        diagnostics.extend(("symbol", match.group("symbol")) for match in symbol_pattern.finditer(stderr))
        error_lines = [line for line in stderr.splitlines() if "error:" in line.lower() or "undefined reference to" in line.lower()]
        if not diagnostics or len(diagnostics) != len(error_lines):
            return {"publishable": False, "group_ids": [], "reason": "UNPARSED_BUILD_DIAGNOSTIC"}
        for kind, value in diagnostics:
            candidates: list[str] = []
            for group in groups:
                paths = {str(item) for item in group.get("affected_paths", [])}
                symbols = {str(item) for item in group.get("affected_symbols", [])}
                if changed and paths and not (paths & changed):
                    continue
                matched = (
                    kind == "path" and any(value == path or value.endswith("/" + path) for path in paths)
                ) or (kind == "symbol" and value in symbols)
                if matched:
                    candidates.append(str(group.get("group_id")))
            if len(candidates) != 1:
                reason = "AMBIGUOUS_BUILD_DIAGNOSTIC" if len(candidates) > 1 else "UNREGISTERED_BUILD_DIAGNOSTIC"
                return {"publishable": False, "group_ids": [], "reason": reason}
            group_ids.add(candidates[0])
    if not failed or not group_ids:
        return {"publishable": False, "group_ids": [], "reason": "NO_FAILED_BUILD"}
    return {"publishable": True, "group_ids": sorted(group_ids, key=_utf8), "reason": None}


def project_version_binding(
    plan_ref: Mapping[str, Any],
    blueprint: Mapping[str, Any],
    rendering_view: Mapping[str, Any],
    existing_epoch_receipt: Mapping[str, Any],
    workspace_facts: Mapping[str, Any],
    *,
    existing_manifest: Mapping[str, Any] | None = None,
    existing_contract_map: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project an F2 metadata binding without rendering or changing content."""

    epoch = existing_epoch_receipt.get("epoch")
    _epoch_number(epoch)
    hashes = workspace_facts.get("file_hashes")
    if not isinstance(hashes, Mapping):
        raise MaterializationError("F2 binding requires the accepted workspace hash map", code="S5_BINDING_INVALID")
    ignored_values = workspace_facts.get("ignored_paths", [])
    if not isinstance(ignored_values, list) or any(not isinstance(path, str) or not path.startswith("_orphan/") for path in ignored_values):
        raise MaterializationError("F2 binding ignored paths are not registered quarantine paths", code="S5_F2_STRUCTURAL_DRIFT")
    ignored = set(ignored_values)
    for path in ignored:
        _safe_relative_path(path, allow_orphan=True)
    active_hashes = {str(path): value for path, value in hashes.items() if path not in ignored}
    paths = _expanded_inventory(blueprint, workspace_facts.get("constraints", {}))
    if set(active_hashes) != set(paths):
        raise MaterializationError("F2 binding would change the structural workspace path set", code="S5_F2_STRUCTURAL_DRIFT")
    content_view = copy.deepcopy(dict(rendering_view))
    content_view["rendered_files"] = {path: b"" for path in paths}
    content_view["content_hashes"] = dict(active_hashes)
    manifest = build_artifact_manifest(plan_ref, blueprint, content_view, str(epoch))
    contract_map = build_contract_map(plan_ref, blueprint, content_view, str(epoch))
    if existing_manifest is not None:
        old_files = {row.get("path"): (row.get("rule_id"), row.get("mutability"), row.get("kind")) for row in existing_manifest.get("files", [])}
        new_files = {row.get("path"): (row.get("rule_id"), row.get("mutability"), row.get("kind")) for row in manifest.get("files", [])}
        if old_files != new_files or existing_manifest.get("delivery_graph") != manifest.get("delivery_graph"):
            raise MaterializationError("F2 binding changes structural artifact metadata", code="S5_F2_STRUCTURAL_DRIFT")
        if existing_manifest.get("epoch") != epoch:
            raise MaterializationError("F2 binding changes the accepted epoch", code="S5_F2_STRUCTURAL_DRIFT")
        frozen_hashes = {
            row.get("path"): row.get("sha256")
            for row in existing_manifest.get("files", [])
            if row.get("mutability") != "s6_owned"
        }
        if any(active_hashes.get(path) != digest for path, digest in frozen_hashes.items()):
            raise MaterializationError("F2 binding frozen source content differs from the accepted workspace", code="S5_F2_CONTENT_DRIFT")
    if existing_contract_map is not None and existing_contract_map.get("epoch") != epoch:
        raise MaterializationError("F2 binding changes the accepted epoch", code="S5_F2_STRUCTURAL_DRIFT")
    if existing_contract_map is not None:
        def contract_shape(value: Mapping[str, Any]) -> dict[str, Any]:
            return {
                "contract_id": value.get("contract_id"),
                "interface_files": value.get("interface_files", []),
                "exports": [
                    {
                        key: item.get(key)
                        for key in ("symbol", "signature", "kind", "interface_file", "implementation_file")
                    }
                    for item in value.get("exports", [])
                ],
            }
        old_contracts = sorted((contract_shape(value) for value in existing_contract_map.get("contracts", [])), key=canonical_json_bytes)
        new_contracts = sorted((contract_shape(value) for value in contract_map.get("contracts", [])), key=canonical_json_bytes)
        if old_contracts != new_contracts:
            raise MaterializationError("F2 binding changes structural contract metadata", code="S5_F2_STRUCTURAL_DRIFT")
    normalized_plan_ref = {"path": str(plan_ref["path"]), "sha256": str(plan_ref["sha256"])}
    manifest_ref = {"path": f"plan/bindings/{_plan_version(plan_ref)}/artifact_manifest.json", "sha256": _sha(manifest)}
    map_ref = {"path": f"plan/bindings/{_plan_version(plan_ref)}/contract_map.json", "sha256": _sha(contract_map)}
    epoch_ref = {"path": f"plan/epochs/{epoch}/receipt.json", "sha256": _sha(existing_epoch_receipt)}
    binding = {"schema_version": "1.0", "plan_ref": normalized_plan_ref, "epoch_receipt_ref": epoch_ref, "manifest_ref": manifest_ref, "contract_map_ref": map_ref}
    return {"manifest": manifest, "contract_map": contract_map, "binding_receipt": binding}


def project_e0_file_ledger(initial_ledger: Mapping[str, Any], rendered_files: Mapping[str, bytes], checkpoint: Mapping[str, Any], build_evidence: Mapping[str, Any], epoch_receipt_ref: Mapping[str, Any]) -> dict[str, Any]:
    commit = checkpoint.get("commit_sha") or checkpoint.get("sha256")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise MaterializationError("checkpoint must contain a git commit sha", code="S5_CHECKPOINT_INVALID")
    evidence_ref = build_evidence.get("evidence_ref") or {"path": "plan/epochs/E0/build.json", "sha256": _sha(build_evidence)}
    epoch = str(epoch_receipt_ref.get("epoch", "E0"))
    rows: list[dict[str, Any]] = []
    for row in initial_ledger.get("files", []):
        path = row["path"]
        if path not in rendered_files:
            rows.append(dict(row))
            continue
        if row.get("class") == "s5_frozen":
            rows.append({"path": path, "class": "s5_frozen", "state": "realized", "created_in_epoch": epoch, "content_sha256": hashlib.sha256(rendered_files[path]).hexdigest(), "last_commit_sha": commit, "verified_by": {"build_variant_ids": list(build_evidence.get("build_variant_ids", ["release"])), "evidence_ref": dict(evidence_ref)}, "created_by_stage": "s5", "epoch_receipt_ref": dict(epoch_receipt_ref)})
        else:
            rows.append({"path": path, "class": "s6_owned", "state": "slot_only"})
    return {"schema_version": "2.0", "files": sorted(rows, key=lambda item: item["path"].encode("utf-8"))}


def validate_completed_e0(run: Mapping[str, Any], plan: Mapping[str, Any], blueprint: Mapping[str, Any], file_ledger: Mapping[str, Any], revision_ledger: Mapping[str, Any], epoch_receipt: Mapping[str, Any], binding_receipt: Mapping[str, Any], manifest: Mapping[str, Any], contract_map: Mapping[str, Any], workspace_facts: Mapping[str, Any]) -> None:
    if run.get("stages", {}).get("s5", {}).get("status") != "done":
        raise MaterializationError("S5 is not accepted", code="S5_ACCEPTANCE_INVALID")
    if epoch_receipt.get("materialization_status") != "ready" or epoch_receipt.get("epoch") != "E0":
        raise MaterializationError("epoch receipt is not a ready E0 receipt", code="S5_RECEIPT_INVALID")
    plan_ref = {"path": "plan/versions/plan-1.0.0.json", "sha256": _sha(plan)}
    if epoch_receipt.get("materialized_plan_ref") != plan_ref or binding_receipt.get("plan_ref") != plan_ref:
        raise MaterializationError("epoch or binding receipt does not bind the accepted Plan", code="S5_BINDING_DRIFT")
    if epoch_receipt.get("blueprint_sha256") != _sha(blueprint):
        raise MaterializationError("epoch receipt Blueprint binding drifted", code="S5_BINDING_DRIFT")
    if binding_receipt.get("epoch_receipt_ref", {}).get("path") != "plan/epochs/E0/receipt.json" or binding_receipt.get("epoch_receipt_ref", {}).get("sha256") != _sha(epoch_receipt):
        raise MaterializationError("binding receipt does not bind the epoch receipt", code="S5_BINDING_DRIFT")
    expected_paths = {row["path"] for row in expand_file_rules(blueprint, workspace_facts["constraints"])} if workspace_facts.get("constraints") else {row["path"] for row in file_ledger.get("files", [])}
    if {row["path"] for row in file_ledger.get("files", [])} != expected_paths:
        raise MaterializationError("file ledger path set drifted", code="S5_LEDGER_DRIFT")
    if any(value.get("plan_version") != "1.0.0" or value.get("plan_sha256") != plan_ref["sha256"] for value in (manifest, contract_map)):
        raise MaterializationError("manifest/map Plan binding drifted", code="S5_BINDING_DRIFT")
    if manifest.get("delivery_blueprint_sha256") != _sha(blueprint) or contract_map.get("delivery_blueprint_sha256") != _sha(blueprint) or manifest.get("epoch") != "E0" or contract_map.get("epoch") != "E0":
        raise MaterializationError("manifest/map Blueprint or epoch binding drifted", code="S5_BINDING_DRIFT")
    if binding_receipt.get("manifest_ref", {}).get("sha256") != _sha(manifest) or binding_receipt.get("contract_map_ref", {}).get("sha256") != _sha(contract_map):
        raise MaterializationError("binding receipt does not bind the immutable manifest/map", code="S5_BINDING_DRIFT")
    if not isinstance(epoch_receipt.get("build_result_refs"), list) or not isinstance(epoch_receipt.get("smoke_result_refs"), list) or not epoch_receipt.get("build_result_refs") or not epoch_receipt.get("smoke_result_refs") or epoch_receipt.get("pending_group_ids") != []:
        raise MaterializationError("epoch receipt evidence is incomplete", code="S5_RECEIPT_INVALID")
    manifest_paths = {row.get("path") for row in manifest.get("files", [])}
    if manifest_paths != expected_paths:
        raise MaterializationError("artifact manifest path set drifted", code="S5_MANIFEST_DRIFT")
    manifest_by_path = {row.get("path"): row for row in manifest.get("files", [])}
    for row in file_ledger.get("files", []):
        if row.get("class") == "s5_frozen":
            if row.get("state") != "realized" or row.get("created_by_stage") != "s5" or row.get("epoch_receipt_ref") != run["stages"]["s5"]["output_refs"]["epoch_receipt"] or row.get("last_commit_sha") != epoch_receipt.get("checkpoint_commit"):
                raise MaterializationError("S5-frozen ledger proof is incomplete", code="S5_LEDGER_DRIFT")
            if row.get("content_sha256") != manifest_by_path.get(row.get("path"), {}).get("sha256"):
                raise MaterializationError("S5-frozen ledger content proof drifted", code="S5_LEDGER_DRIFT")
        elif row.get("class") == "s6_owned" and row.get("state") != "slot_only":
            raise MaterializationError("S6-owned E0 rows must remain slot-only", code="S5_LEDGER_DRIFT")
    hashes = workspace_facts.get("file_hashes", {})
    if hashes and any(row.get("sha256") != hashes.get(row.get("path")) for row in manifest.get("files", [])):
        raise MaterializationError("artifact manifest content hash drifted", code="S5_MANIFEST_DRIFT")
    events = [entry for entry in revision_ledger.get("entries", []) if entry.get("event_type") == "epoch_materialized" and entry.get("payload", {}).get("revision_seq") == 0]
    if len(events) != 1 or events[0].get("payload", {}).get("epoch_receipt_ref") != run["stages"]["s5"]["output_refs"]["epoch_receipt"] or events[0].get("payload", {}).get("binding_ref") != run["stages"]["s5"]["output_refs"]["binding_receipt"]:
        raise MaterializationError("accepted E0 event is missing or conflicts", code="S5_EVENT_DRIFT")
    if workspace_facts.get("paths") is not None and set(workspace_facts["paths"]) != {row["path"] for row in file_ledger.get("files", [])}:
        raise MaterializationError("workspace path set does not match the accepted ledger", code="S5_WORKSPACE_DRIFT")


__all__ = [
    "MaterializationError", "parse_c99_declaration", "derive_rendering_view", "render_e0_files",
    "build_artifact_manifest", "build_contract_map", "project_e0_file_ledger", "validate_completed_e0",
    "build_epoch_context", "plan_epoch_materialization", "project_file_ledger",
    "validate_completed_epoch", "attribute_pending_repair", "project_version_binding",
]
