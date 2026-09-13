"""Deterministic compilation; incomplete and conflicting nodes remain in gaps."""
from __future__ import annotations
import copy
import hashlib
import json
from typing import Any
from .candidates import Claim
from .ingest import Document
from ..speclib.lint import lint_spec


def _req_id(c: Claim) -> str:
    identity = json.dumps([c.value, c.source_spans], sort_keys=True)
    return "REQ-" + hashlib.sha256(identity.encode()).hexdigest()[:20] + "-001"


def compile_with_evidence(document: Document, claims: list[Claim], *, protocol_name: str | None = None,
                          protocol_version: str | None = None) -> tuple[dict[str, Any], list[dict], list[dict]]:
    requirements: dict[str, dict[str, Any]] = {}; requirement_keys: dict[Any, str] = {}
    nodes: dict[Any, tuple[Claim, dict[str, Any]]] = {}; mappings: list[dict[str, Any]] = []; gaps: list[dict[str, Any]] = []
    def gap(c: Claim, reason: str, category: str = "ambiguous") -> None:
        gaps.append({"gap_id": f"GAP-{len(gaps)+1:05d}", "candidate_id": c.candidate_id,
                     "category": category, "description": reason, "source_refs": c.source_spans,
                     "downstream_impact": "Candidate is not compiled; coverage remains incomplete", "status": "open"})
    def req(c: Claim) -> str:
        ref = c.source_spans[0]
        import re
        section_key = re.match(r"(?:Appendix\s+[A-Z0-9]+|[0-9]+(?:\.[0-9]+)*)", ref.get("section", ""))
        key = (c.value.get("level", "DEFINITION"), " ".join(str(c.value.get("text", c.quote)).split()), section_key.group(0) if section_key else ref.get("section", ""))
        if key in requirement_keys:
            rid = requirement_keys[key]
            mappings.append({"candidate_id": c.candidate_id, "duplicate_of": rid, "source_spans": c.source_spans})
            return rid
        rid = _req_id(c)
        requirement_keys[key] = rid
        requirements[rid] = {"id": rid, "text": c.value["text"] if c.kind == "requirement" else c.quote,
            "level": c.value["level"] if c.kind == "requirement" else "DEFINITION",
            "source_ref": {"doc_id": next((s.doc_id for s in document.segments if s.segment_id == ref["segment_id"]), document.doc_id), "section": ref["section"], "quote": c.quote, "segment_id": ref["segment_id"]}}
        mappings.append({"candidate_id": c.candidate_id, "target": f"/requirements/{rid}", "source_spans": c.source_spans})
        return rid
    conflicted: set[Any] = set()
    for c in claims:
        if c.kind == "requirement":
            req(c)
            continue
        if c.kind in {"unsupported_candidate", "relationship", "constraint"}:
            gap(c, str(c.value.get("reason", c.value.get("text", c.value))), "unsupported_by_frozen_ir")
            continue
        value = copy.deepcopy(c.value)
        if c.kind == "message":
            import re
            if "id" not in value and isinstance(value.get("name"), str):
                value["id"] = re.sub(r"[^a-z0-9]+", "_", value["name"].lower()).strip("_")
            if "senders" not in value and isinstance(value.get("sender"), str):
                value["senders"] = [value.pop("sender")]
            if "receivers" not in value and isinstance(value.get("receiver"), str):
                value["receivers"] = [value.pop("receiver")]
            if "wire_layout" not in value and isinstance(value.get("layout"), list):
                value["wire_layout"] = value.pop("layout")
            value.setdefault("fields", [])
            for key in ("sender", "receiver", "layout", "description", "constraints"):
                value.pop(key, None)
        if c.kind == "type":
            if "id" not in value and isinstance(value.get("name"), str):
                import re
                value["id"] = re.sub(r"[^a-z0-9]+", "_", value["name"].lower()).strip("_")
            if isinstance(value.get("constraints"), list):
                fixed = []
                for item in value["constraints"]:
                    if isinstance(item, dict):
                        item = dict(item)
                        if "max_length" in item:
                            item["max_len"] = item.pop("max_length")
                        item.pop("kind", None); item.pop("length_unit", None); item.pop("prefix_length", None); item.pop("prefix_type", None)
                    fixed.append(item)
                value["constraints"] = fixed
        if c.kind == "message" and not {"id", "name", "senders", "receivers", "wire_layout", "fields"}.issubset(value):
            gap(c, "Message lacks required frozen IR keys after normalization")
            continue
        if c.kind == "type" and not isinstance(value.get("encoding"), dict):
            gap(c, "Type cannot be represented by frozen IR encoding", "unsupported_by_frozen_ir")
            continue
        if c.kind == "field":
            node_key: Any = ("field", value.get("message_id"), value.get("field", {}).get("name"))
        else:
            node_key = (c.kind, value.get("id", c.kind))
        if node_key in nodes:
            previous, prev_value = nodes[node_key]
            if value == prev_value:
                mappings.append({"candidate_id": c.candidate_id, "duplicate_of": previous.candidate_id, "source_spans": c.source_spans})
            else:
                def contains(container: Any, part: Any) -> bool:
                    if isinstance(container, dict) and isinstance(part, dict):
                        return all(k in container and (container[k] == v or contains(container[k], v)) for k, v in part.items())
                    if isinstance(container, list) and isinstance(part, list):
                        return all(any(x == y or contains(x, y) for x in container) for y in part)
                    return False
                # A richer candidate that strictly contains an earlier candidate
                # is a merge, not an unresolved semantic conflict.
                if contains(value, prev_value) or contains(prev_value, value):
                    richer = c if contains(value, prev_value) else previous
                    mappings.append({"candidate_id": c.candidate_id, "duplicate_of": richer.candidate_id, "source_spans": c.source_spans, "merge": "structural_superset"})
                    if richer is c:
                        nodes[node_key] = (c, value)
                    continue
                gap(previous, "Conflicting definitions for " + str(node_key))
                gap(c, "Conflicting definitions for " + str(node_key))
                # Keep the higher-confidence, evidence-bearing candidate as a
                # draft output; the conflict remains explicit in the gap ledger.
                def specificity(item: Any) -> int:
                    if isinstance(item, dict):
                        return len(item) + sum(specificity(v) for v in item.values())
                    if isinstance(item, list):
                        return sum(specificity(v) for v in item)
                    return 0
                if (c.confidence, specificity(value)) > (previous.confidence, specificity(prev_value)):
                    nodes[node_key] = (c, value)
        else:
            nodes[node_key] = (c, value)
    protocol = nodes.pop(("protocol", "protocol"), None)
    if protocol:
        proto_claim, proto = protocol
        mappings.append({"candidate_id": proto_claim.candidate_id, "target": "/protocol", "source_spans": proto_claim.source_spans})
    else:
        # Caller metadata is labelled separately and never scored as RFC evidence.
        roles = sorted({r for c, v in nodes.values() if c.kind == "message" for r in v.get("senders", []) + v.get("receivers", [])})
        proto = {"name": protocol_name or document.doc_id, "version": protocol_version or "unknown", "roles": roles or ["unspecified"]}
        mappings.append({"target": "/protocol", "origin": "caller_metadata", "verified": False})
    spec: dict[str, Any] = {"schema_version": "3.0", "protocol": proto, "types": [], "messages": [], "requirements": []}
    types_list: list[dict[str, Any]] = spec["types"]
    messages_list: list[dict[str, Any]] = spec["messages"]
    owners: dict[str, Claim] = {}
    fields: list[tuple[Claim, dict[str, Any]]] = []
    for key, (c, value) in nodes.items():
        if c.kind == "field":
            fields.append((c, value))
            continue
        if c.kind not in {"type", "message", "transport"}:
            gap(c, "Unsupported compiler claim kind")
            continue
        # Models must not invent references; each structural claim references its own evidence.
        value["req_ids"] = [req(c)]
        if c.kind == "message" and value.get("fields"):
            gap(c, "Nested fields require individual evidence-bearing field claims")
            continue
        if c.kind == "transport":
            spec["transport"] = value
            target = "/transport"
        else:
            collection: str = "types" if c.kind == "type" else "messages"
            (types_list if collection == "types" else messages_list).append(value)
            target = f"/{collection}/{value.get('id')}"
        owners[target] = c
        mappings.append({"candidate_id": c.candidate_id, "target": target, "source_spans": c.source_spans})
    for c, value in fields:
        message = next((m for m in messages_list if m.get("id") == value.get("message_id")), None)
        if message is None or not isinstance(value.get("field"), dict):
            gap(c, "Field has no compiled parent message")
            continue
        field = copy.deepcopy(value["field"])
        # Normalize only unambiguous model aliases at the frozen IR boundary.
        if "loc" not in field and isinstance(field.get("position"), str):
            field["loc"] = field.pop("position")
        if "constraint" not in field and isinstance(field.get("constraints"), list) and len(field["constraints"]) == 1 and isinstance(field["constraints"][0], dict):
            field["constraint"] = field.pop("constraints")[0]
        for key in ("constraints", "position"):
            field.pop(key, None)
        if not {"name", "loc", "type"}.issubset(field):
            gap(c, "Field lacks required frozen IR keys after normalization")
            continue
        field["req_ids"] = [req(c)]
        message["fields"].append(field)
        target = f"/messages/{message['id']}/fields/{field.get('name')}"
        owners[target] = c
        mappings.append({"candidate_id": c.candidate_id, "target": target, "source_spans": c.source_spans})
    spec["requirements"] = list(requirements.values())
    report = lint_spec(spec)
    if not report["valid"]:
        # Preserve the complete attempted result and errors for repair; never publish
        # a deceptively valid empty structure after silently dropping invalid nodes.
        gaps.append({"gap_id": f"GAP-{len(gaps)+1:05d}", "category": "invalid_compilation",
            "description": "Compilation failed frozen schema or reference validation", "errors": report["errors"],
            "source_refs": [], "downstream_impact": "Output is a draft and must not be consumed", "status": "open"})
    return spec, mappings, gaps


def compile_spec(document: Document, claims: list[Claim], *, protocol_name: str | None = None, protocol_version: str | None = None) -> dict[str, Any]:
    return compile_with_evidence(document, claims, protocol_name=protocol_name, protocol_version=protocol_version)[0]


def compile_v4(document: Document, claims: list[Claim], *, protocol_name: str | None = None,
               protocol_version: str | None = None) -> dict[str, Any]:
    """Build an evidence-bearing RFC draft without changing the v3 compiler API."""
    spec, mappings, compile_gaps = compile_with_evidence(
        document, claims, protocol_name=protocol_name, protocol_version=protocol_version)
    evidence = {
        "schema_version": "1.0",
        "document": {"doc_id": document.doc_id, "path": document.path,
                      "sha256": document.sha256, "format": document.format},
        "segments": [{"segment_id": s.segment_id, "section": s.section,
                      "ordinal": s.ordinal, "start_line": s.start_line,
                      "end_line": s.end_line, "kind": s.kind} for s in document.segments],
        "claims": [{"candidate_id": c.candidate_id, "kind": c.kind,
                    "value": c.value, "source_spans": c.source_spans,
                    "quote": c.quote, "confidence": c.confidence,
                    "status": c.status} for c in claims],
        "claim_mappings": mappings,
    }
    out = copy.deepcopy(spec)
    out["schema_version"] = "4.0"
    out["source_snapshot"] = {"doc_id": document.doc_id, "path": document.path,
                               "raw_sha256": document.sha256, "normalized_sha256": hashlib.sha256(document.text.encode()).hexdigest(),
                               "format": document.format, "parser_version": "text-sections-v2"}
    out["evidence"] = evidence
    # v4 preserves normative levels that v3 cannot represent (notably
    # SHOULD NOT) as first-class requirements instead of losing them.
    preserved_ids: set[str] = set()
    for claim in claims:
        if claim.kind != "unsupported_candidate" or not claim.value.get("level"):
            continue
        ref = claim.source_spans[0]
        rid = _req_id(claim)
        if rid in preserved_ids:
            continue
        requirement = {"id": rid, "text": claim.value.get("text", claim.quote),
                       "level": claim.value["level"],
                       "source_ref": {"doc_id": document.doc_id, "section": ref.get("section", ""),
                                      "quote": claim.quote, "segment_id": ref.get("segment_id", "")}}
        out["requirements"].append(requirement)
        preserved_ids.add(rid)
    out["gaps"] = [g for g in compile_gaps
                    if not (g.get("category") == "unsupported_by_frozen_ir"
                            and any(c.candidate_id == g.get("candidate_id") and c.value.get("level") == "SHOULD NOT" for c in claims))]
    out["approval"] = {"status": "draft"}
    return out
