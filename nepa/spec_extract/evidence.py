from __future__ import annotations
from typing import Any
from .candidates import Claim, claim_dict
from .ingest import Document

def evidence_ledger(document: Document, claims: list[Claim], *, model: str = "deterministic") -> dict[str, Any]:
    return {"schema_version": "1.0", "document": {"doc_id": document.doc_id, "path": document.path, "sha256": document.sha256, "format": document.format}, "model": model, "segments": [{"segment_id": s.segment_id, "section": s.section, "ordinal": s.ordinal, "start_line": s.start_line, "end_line": s.end_line, "kind": s.kind} for s in document.segments], "claims": [claim_dict(c) for c in claims]}

def gaps(document: Document, claims: list[Claim]) -> list[dict[str, Any]]:
    unsupported = [c for c in claims if c.kind == "unsupported_candidate"]
    return [{"gap_id": f"GAP-{i:03d}", "category": "unsupported_by_frozen_ir", "description": c.value["text"], "source_refs": c.source_spans, "status": "open"} for i, c in enumerate(unsupported, 1)]

def validate_evidence(document: Document, claims: list[Claim]) -> list[str]:
    """Validate that every cited span belongs to a segment and quote matches exactly."""
    by_id = {s.segment_id: s for s in document.segments}; errors=[]
    for c in claims:
        for ref in c.source_spans:
            seg = by_id.get(ref.get("segment_id"))
            if seg is None: errors.append(f"unknown segment {ref.get('segment_id')}"); continue
            quote = ref.get("quote", c.quote)
            if quote and quote not in seg.text: errors.append(f"quote not in segment {seg.segment_id}")
    return errors
