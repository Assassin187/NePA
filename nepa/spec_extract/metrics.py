from __future__ import annotations
import re
from typing import Any

def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()
def _section(value: Any) -> str:
    return _norm(value).split()[0] if _norm(value) else ""
def _key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (_norm(item.get("level")), _norm(item.get("source_ref", {}).get("quote") or item.get("text")), _section(item.get("source_ref", {}).get("section")))
def requirement_metrics(gold: list[dict[str, Any]], predicted: list[dict[str, Any]]) -> dict[str, float]:
    # RFC quotes often wrap at different columns. Match each gold item once by
    # level, section number and normalized quote containment.
    used = set(); tp = 0
    for g in gold:
        gq = _norm(g.get("source_ref", {}).get("quote") or g.get("text")); gs = _section(g.get("source_ref", {}).get("section")); gl = _norm(g.get("level"))
        hit = next((i for i,p in enumerate(predicted) if i not in used and _norm(p.get("level")) == gl and _section(p.get("source_ref", {}).get("section")) == gs and (gq in _norm(p.get("source_ref", {}).get("quote")) or _norm(p.get("source_ref", {}).get("quote")) in gq)), None)
        if hit is not None:
            used.add(hit)
            tp += 1
    precision = tp / len(predicted) if predicted else (1.0 if not gold else 0.0); recall = tp / len(gold) if gold else 1.0
    return {"precision":precision,"recall":recall,"f1":2*precision*recall/(precision+recall) if precision+recall else 0.0,"gold":float(len(gold)),"predicted":float(len(predicted)),"matched":float(tp)}

def source_traceability(document_text: str, requirements: list[dict[str, Any]]) -> dict[str, float]:
    normalized=_norm(document_text); checked=len(requirements); located=sum(1 for r in requirements if _norm(r.get("source_ref",{}).get("quote")) in normalized)
    return {"checked":float(checked),"located":float(located),"rate":located/checked if checked else 1.0}
