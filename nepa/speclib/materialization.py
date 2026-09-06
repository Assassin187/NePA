"""Pure, protocol-neutral S5 E0 materialization projections."""

from __future__ import annotations

import hashlib
import importlib.resources
import re
from collections import defaultdict
from typing import Any, Mapping

from jinja2 import Environment, StrictUndefined

from .delivery import expand_file_rules
from .lint import canonical_json_bytes


class MaterializationError(ValueError):
    """A sealed S4 input cannot be rendered as the finite E0 grammar."""

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
            "sha256": _sha(data), "created_by_stage": "s5", "mutability": mutability,
            "owner_task_id": rule.get("owner_task_id") if mutability == "s6_owned" else None,
            "build_variant_ids": build_variants,
        })
    return {
        "schema_version": "2.0", "plan_version": "1.0.0", "plan_sha256": plan_ref["sha256"],
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
        "schema_version": "2.0", "plan_version": "1.0.0", "plan_sha256": plan_ref["sha256"],
        "delivery_blueprint_sha256": _sha(blueprint), "epoch": epoch, "contracts": contracts,
    }


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
]
