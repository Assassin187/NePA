"""Strict projection from evidence-rich Spec IR v4 to coder Spec IR v3."""
from __future__ import annotations
import copy
from typing import Any

class ProjectionError(ValueError):
    pass

def project_to_v3(spec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if str(spec.get("schema_version")) not in {"4.0", "4"}:
        raise ProjectionError("input is not Spec IR v4")
    losses: list[str] = []
    for g in spec.get("gaps", []):
        if g.get("status", "open") == "open" and g.get("category") not in {"out_of_scope"}:
            losses.append(f"open gap: {g.get('gap_id', 'unknown')}")
    for r in spec.get("requirements", []):
        if r.get("level") in {"SHOULD NOT", "MAY"} or r.get("condition") or r.get("exception"):
            losses.append(f"requirement {r.get('id')} not representable in v3")
    if losses:
        raise ProjectionError("; ".join(losses))
    out = copy.deepcopy(spec)
    out["schema_version"] = "3.0"
    out.pop("evidence", None); out.pop("gaps", None)
    return out, {"from": "4.0", "to": "3.0", "losses": []}
