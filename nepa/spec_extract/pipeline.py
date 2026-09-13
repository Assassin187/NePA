from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Callable
from .candidates import Claim, discover
from .compile import compile_with_evidence, compile_v4
from .evidence import evidence_ledger
from .ingest import load_document, in_scope
from .llm import parse_claim_batch

def extract_document(path: str | Path, *, doc_id: str | None = None, protocol_name: str | None = None,
                     protocol_version: str | None = None, output: str | Path | None = None,
                     evidence: str | Path | None = None, gap_file: str | Path | None = None,
                     claim_provider: Callable[[Any], list[Any]] | None = None,
                     llm_provider: Callable[[Any], Any] | None = None,
                     sections: list[str] | None = None,
                     ir_version: str = "3.0") -> dict[str, Any]:
    document = load_document(path, doc_id)
    if sections:
        selected = tuple(s for s in document.segments if in_scope(s.section, sections))
        document = type(document)(document.doc_id, document.path, document.sha256, document.format, document.text, selected)
    claims = discover(document.segments)
    errors: list[dict[str, Any]] = []
    if claim_provider:
        for item in claim_provider(document):
            claims.append(item if isinstance(item, Claim) else Claim(**item))
    if llm_provider:
        # Every included segment is assigned to exactly one bounded request;
        # examples are recorded as deterministic-only rather than silently lost.
        groups: list[list[Any]] = []
        for start in range(0, len(document.segments), 4):
            groups.append(list(document.segments[start:start + 4]))
        for group in groups:
            request_segment: Any = group[0] if len(group) == 1 else tuple(group)
            try:
                accepted, rejected = parse_claim_batch(llm_provider(request_segment), type(document)(document.doc_id, document.path, document.sha256, document.format, document.text, tuple(group)))
                claims.extend(accepted)
                errors.extend({"segment_id": ",".join(x.segment_id for x in group), **error} for error in rejected)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                errors.append({"segment_id": ",".join(x.segment_id for x in group), "error": str(exc), "category": "llm_output_rejected"})
    if ir_version in {"4", "4.0"}:
        spec = compile_v4(document, claims, protocol_name=protocol_name, protocol_version=protocol_version)
        mappings = spec["evidence"]["claim_mappings"]
        compile_gaps = spec.get("gaps", [])
    else:
        spec, mappings, compile_gaps = compile_with_evidence(document, claims, protocol_name=protocol_name, protocol_version=protocol_version)
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True); Path(output).write_text(json.dumps(spec, ensure_ascii=False, indent=2)+"\n")
    if evidence:
        Path(evidence).parent.mkdir(parents=True, exist_ok=True)
        ledger = evidence_ledger(document, claims, model="deepseek-v4-pro" if llm_provider else "deterministic")
        ledger["claim_mappings"] = mappings; ledger["rejected_outputs"] = errors
        ledger["llm_calls"] = getattr(llm_provider, "records", [])
        Path(evidence).write_text(json.dumps(ledger, ensure_ascii=False, indent=2)+"\n")
    if gap_file:
        Path(gap_file).parent.mkdir(parents=True, exist_ok=True)
        gaps = compile_gaps + [{"gap_id": f"GAP-LLM-{i:04d}", **e, "description": "LLM claim rejected: "+e["error"], "status": "open"} for i,e in enumerate(errors,1)]
        Path(gap_file).write_text(json.dumps({"schema_version":"1.0","gaps":gaps}, ensure_ascii=False, indent=2)+"\n")
    return spec
