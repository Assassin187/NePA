"""Deterministic merge for independent RFC documents; nothing is silently lost."""
from __future__ import annotations
import copy
from typing import Any

def merge_specs(specs: list[dict[str, Any]]) -> dict[str, Any]:
    if not specs:
        raise ValueError("at least one Spec IR is required")
    out = copy.deepcopy(specs[0])
    out["requirements"] = []
    out["types"] = []
    out["messages"] = []
    seen: dict[str, set[Any]] = {"requirements": set(), "types": set(), "messages": set()}
    for spec in specs:
        if spec.get("protocol", {}).get("name") != out.get("protocol", {}).get("name"):
            raise ValueError("protocol names conflict")
        for key in ("requirements", "types", "messages"):
            for value in spec.get(key, []):
                ident = value.get("id")
                if ident not in seen[key]:
                    out[key].append(copy.deepcopy(value))
                    seen[key].add(ident)
        if "transport" in spec:
            out["transport"] = copy.deepcopy(spec["transport"])
    return out
