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
