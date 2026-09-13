"""Phase-one acceptance checks for compiled Spec IR and sidecar evidence."""
from __future__ import annotations
import json
from pathlib import Path
from nepa.speclib.lint import lint_spec

def check_traceability(spec: dict, evidence: dict) -> dict:
    targets = {m.get('target') for m in evidence.get('claim_mappings', []) if m.get('target')}
    required=[]
    for t in spec.get('requirements', []):
        required.append(f"/requirements/{t.get('id')}")
    for t in spec.get('types', []):
        required.append(f"/types/{t.get('id')}")
    for m in spec.get('messages', []):
        required.append(f"/messages/{m.get('id')}")
        for f in m.get('fields', []):
            required.append(f"/messages/{m.get('id')}/fields/{f.get('name')}")
    hit=sum(1 for x in required if x in targets)
    return {'total':len(required),'traced':hit,'rate':hit/len(required) if required else 1.0,'missing':[x for x in required if x not in targets]}

def run(spec_path: str|Path, evidence_path: str|Path, gaps_path: str|Path|None=None) -> dict:
    if gaps_path is None:
        return {'schema_valid': False, 'lint_errors': {'valid': False, 'errors': [{'code': 'GAPS_REQUIRED'}]},
                'traceability': {'total': 0, 'traced': 0, 'rate': 0.0, 'missing': []},
                'gap_categories': {'missing_gap_ledger': 1}, 'open_gap_count': 1, 'pass': False}
    spec=json.loads(Path(spec_path).read_text()); evidence=json.loads(Path(evidence_path).read_text())
    errors=lint_spec(spec)
    trace=check_traceability(spec,evidence)
    gaps=json.loads(Path(gaps_path).read_text()) if gaps_path else []
    all_gaps = gaps if isinstance(gaps,list) else gaps.get('gaps',[])
    unresolved=[g for g in all_gaps if g.get('category') != 'out_of_scope' and g.get('status','open')=='open']
    # A target without an evidence-bearing mapping is not traceable.
    mapped = [m for m in evidence.get('claim_mappings', []) if m.get('target')]
    if any(not m.get('source_spans') for m in mapped):
        unresolved.append({'category': 'missing_evidence', 'status': 'open'})
    categorized: dict[str, int] = {}
    for g in all_gaps:
        categorized[g.get('category','unknown')] = categorized.get(g.get('category','unknown'), 0) + 1
    return {'schema_valid': bool(errors.get('valid', not errors)), 'lint_errors':errors, 'traceability':trace, 'gap_categories':categorized, 'open_gap_count':len(unresolved), 'pass':bool(errors.get('valid', not errors)) and trace['rate']>=.95 and not unresolved}
