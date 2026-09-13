"""Strict projection from evidence-rich Spec IR v4 to coder Spec IR v3."""
from __future__ import annotations
import copy
import json
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator

class ProjectionError(ValueError):
    pass

def validate_v4(spec: dict[str, Any]) -> list[str]:
    schema_path = Path(__file__).resolve().parents[1] / "schemas" / "spec-ir-v4.schema.json"
    schema = json.loads(schema_path.read_bytes())
    return [e.message for e in Draft202012Validator(schema).iter_errors(spec)]

def project_to_v3(spec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if str(spec.get("schema_version")) not in {"4.0", "4"}:
        raise ProjectionError("input is not Spec IR v4")
    errors = validate_v4(spec)
    if errors:
        raise ProjectionError("invalid Spec IR v4: " + "; ".join(errors))
    losses: list[str] = []
    for g in spec.get("gaps", []):
        if g.get("status", "open") == "open" and g.get("category") not in {"out_of_scope"}:
            losses.append(f"open gap: {g.get('gap_id', 'unknown')}")
    for r in spec.get("requirements", []):
        if r.get("level") == "SHOULD NOT" or r.get("condition") or r.get("exception"):
            losses.append(f"requirement {r.get('id')} not representable in v3")
    if losses:
        raise ProjectionError("; ".join(losses))
    out = copy.deepcopy(spec)
    out["schema_version"] = "3.0"
    for key in ("evidence", "gaps", "approval", "conflicts", "relations", "source_snapshot"):
        out.pop(key, None)
    return out, {"from": "4.0", "to": "3.0", "losses": []}
