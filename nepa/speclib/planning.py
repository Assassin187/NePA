"""Lossless structural context slices of the original Spec."""
from __future__ import annotations
from typing import Any
from .lint import type_dependencies


def planning_index(spec: dict[str, Any]) -> dict[str, Any]:
    return {"protocol": spec["protocol"], "transport": spec.get("transport"),
            "types": [{"id": t["id"], "pointer": f"/types/{i}"} for i, t in enumerate(spec["types"])],
            "messages": [{"id": m["id"], "pointer": f"/messages/{i}", "senders": m["senders"], "receivers": m["receivers"]}
                         for i, m in enumerate(spec["messages"])],
            "requirements": [{"id": r["id"], "pointer": f"/requirements/{i}"} for i, r in enumerate(spec["requirements"])]}


def referenced_requirements(spec: dict[str, Any], objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs = {ref for obj in objects for ref in obj.get("req_ids", [])}
    return [requirement for requirement in spec["requirements"] if requirement["id"] in refs]


def message_context(spec: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    definitions = {t["id"]: t for t in spec["types"]}
    needed: set[str] = set()
    pending = [f["type"] for f in message["fields"]]
    while pending:
        ref = pending.pop()
        if ref in needed or ref not in definitions:
            continue
        needed.add(ref)
        pending.extend(type_dependencies(definitions[ref]))
    types = [t for t in spec["types"] if t["id"] in needed]
    return {"message": message, "types": types,
            "requirements": referenced_requirements(spec, [message, *message["fields"], *types])}


def requirement_navigation(spec: dict[str, Any], requirement_ids: list[str]) -> list[dict[str, Any]]:
    """Index only explicit req_ids links and their structural type dependencies."""
    definitions = {value["id"]: (index, value) for index, value in enumerate(spec["types"])}
    rows = []
    for requirement_id in requirement_ids:
        direct: list[dict[str, Any]] = []
        type_roots: list[str] = []
        transport = spec.get("transport")
        if transport and requirement_id in transport.get("req_ids", []):
            direct.append({"kind": "transport", "pointer": "/transport"})
        for index, value in enumerate(spec["types"]):
            if requirement_id in value.get("req_ids", []):
                direct.append({"kind": "type", "id": value["id"], "pointer": f"/types/{index}"})
                type_roots.append(value["id"])
        for message_index, message in enumerate(spec["messages"]):
            if requirement_id in message.get("req_ids", []):
                direct.append({"kind": "message", "id": message["id"],
                               "pointer": f"/messages/{message_index}"})
                type_roots.extend(field["type"] for field in message["fields"])
            for field_index, field in enumerate(message["fields"]):
                if requirement_id in field.get("req_ids", []):
                    direct.append({"kind": "field", "message_id": message["id"], "name": field["name"],
                                   "pointer": f"/messages/{message_index}/fields/{field_index}"})
                    type_roots.append(field["type"])
        closure: set[str] = set()
        pending = list(type_roots)
        while pending:
            type_id = pending.pop()
            if type_id in closure or type_id not in definitions:
                continue
            closure.add(type_id)
            pending.extend(type_dependencies(definitions[type_id][1]))
        type_pointers = [f"/types/{index}" for type_id, (index, _) in definitions.items() if type_id in closure]
        rows.append({"requirement_id": requirement_id, "direct": direct,
                     "supporting_type_pointers": type_pointers,
                     "scope": "Explicit structural navigation only; not exhaustive semantic ownership or completion evidence."})
    return rows
