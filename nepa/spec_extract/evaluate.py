from __future__ import annotations
from typing import Any
from .metrics import requirement_metrics, source_traceability

def _canonical(value: Any) -> str:
    import re
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    text = re.sub(r"^(mqtt|http)_", "", text)
    # Human profiles use singular/plural labels interchangeably for aggregate types.
    if text.endswith("_entries"):
        text = text[:-3]
    elif text.endswith("_codes"):
        text = text[:-1]
    elif text.endswith("_list"):
        pass
    return text
def _set(items: list[dict[str, Any]], key: str) -> set[str]:
    return {_canonical(x.get("name", x.get("id", ""))) for x in items if x.get("id", x.get("name"))}

def _prf(gold: set[str], predicted: set[str]) -> dict[str, Any]:
    tp = len(gold & predicted); precision = tp / len(predicted) if predicted else (1.0 if not gold else 0.0); recall = tp / len(gold) if gold else 1.0
    return {"gold": len(gold), "predicted": len(predicted), "matched": tp, "precision": precision, "recall": recall, "f1": 2*precision*recall/(precision+recall) if precision+recall else 0.0}

def evaluate(gold: dict[str, Any], predicted: dict[str, Any], document_text: str) -> dict[str, Any]:
    result: dict[str, Any] = {"requirements": requirement_metrics(gold.get("requirements", []), predicted.get("requirements", [])),
                              "traceability": source_traceability(document_text, predicted.get("requirements", []))}
    result['types_structural'] = structural_type_metrics(gold, predicted)
    for key in ("types", "messages"):
        result[key] = _prf(_set(gold.get(key, []), key), _set(predicted.get(key, []), key))
    aliases = {"client_identifier":"client_id", "requested_qos":"requested_maximum_qos", "return_code":"return_code"}
    gold_fields = {(_canonical(m.get("name", m.get("id", ""))), aliases.get(_canonical(f.get("name")), _canonical(f.get("name")))) for m in gold.get("messages", []) for f in m.get("fields", [])}
    pred_fields = {(_canonical(m.get("name", m.get("id", ""))), aliases.get(_canonical(f.get("name")), _canonical(f.get("name")))) for m in predicted.get("messages", []) for f in m.get("fields", [])}
    result["fields"] = _prf({a+"/"+b for a,b in gold_fields}, {a+"/"+b for a,b in pred_fields})
    # HTTP profiles may represent request-line atoms as one container field.
    # Keep strict metrics above; expose a separately labelled structural metric.
    def http_structural(items):
        out=set()
        for m in items:
            mid=_canonical(m.get("name",m.get("id", "")))
            names={_canonical(f.get("name")) for f in m.get("fields", [])}
            if names & {"method","request_target","target","http_version","version","request_line"}:
                out.add(mid+"/request_line")
            if "headers" in names or any("header" in n for n in names):
                out.add(mid+"/headers")
            if "body" in names or "payload" in names:
                out.add(mid+"/body")
            if "status_line" in names or "status_code" in names:
                out.add(mid+"/status_line")
        return out
    if any(_canonical(m.get("name",m.get("id", ""))).endswith("request") for m in gold.get("messages", [])):
        result["fields_structural"] = _prf(http_structural(gold.get("messages", [])), http_structural(predicted.get("messages", [])))
    return result

def structural_type_metrics(gold: dict[str, Any], predicted: dict[str, Any]) -> dict[str, Any]:
    """Match types by frozen encoding shape, independent of human naming."""
    def shape(t: dict[str, Any]) -> tuple:
        e=t.get('encoding') or {}
        return (e.get('kind'), e.get('base_type'), e.get('length_type'), e.get('item_type'), tuple(sorted((e.get('values') or {}).keys())))
    gs={shape(t) for t in gold.get('types',[]) if t.get('encoding')}
    ps={shape(t) for t in predicted.get('types',[]) if t.get('encoding')}
    strict = _prf({repr(x) for x in gs},{repr(x) for x in ps})
    def coarse(t):
        return (t[0], t[1], t[2], t[3])
    coarse_result = _prf({repr(coarse(x)) for x in gs}, {repr(coarse(x)) for x in ps})
    strict['coarse_encoding'] = coarse_result
    return strict
