from pathlib import Path

from nepa.spec_extract.ingest import load_document
from nepa.spec_extract.candidates import discover
from nepa.spec_extract.compile import compile_spec
from nepa.spec_extract.evidence import evidence_ledger
from nepa.speclib.lint import lint_spec
from nepa.spec_extract.metrics import requirement_metrics, source_traceability
from nepa.spec_extract.llm import build_prompt, parse_claims
from nepa.spec_extract.evaluate import evaluate


def test_text_ingest_is_stable_and_extracts_normative_claims(tmp_path: Path):
    p = tmp_path / "rfc.txt"
    p.write_text("1 Introduction\n\nThe client MUST send a frame.\n\n2 Format\n\nThe field MAY be omitted.\n")
    doc = load_document(p, "fixture")
    claims = discover(doc.segments)
    assert len(claims) == 2
    spec = compile_spec(doc, claims, protocol_name="Fixture", protocol_version="1")
    assert lint_spec(spec)["valid"]
    assert evidence_ledger(doc, claims)["document"]["sha256"]


def test_unsupported_normative_level_is_not_silently_compiled(tmp_path: Path):
    p = tmp_path / "rfc.txt"
    p.write_text("1\n\nThe server SHOULD NOT guess.\n")
    doc = load_document(p, "fixture")
    claims = discover(doc.segments)
    assert claims[0].kind == "unsupported_candidate"
    assert compile_spec(doc, claims)["requirements"] == []

def test_metrics_and_traceability_are_explicit():
    item = {"id": "x", "text": "The client MUST send.", "level": "MUST", "source_ref": {"section": "1", "quote": "The client MUST send."}}
    assert requirement_metrics([item], [item])["f1"] == 1
    assert source_traceability(item["source_ref"]["quote"], [item])["rate"] == 1

def test_llm_claim_parser_rejects_unknown_evidence_segment(tmp_path: Path):
    p = tmp_path / "rfc.txt"; p.write_text("1\n\nThe client MUST send.\n")
    doc = load_document(p, "fixture")
    payload = [{"candidate_id":"c", "kind":"requirement", "value":{"text":"The client MUST send.","level":"MUST"}, "source_spans":[{"segment_id":"missing","section":"1","start_line":1,"end_line":2}], "quote":"The client MUST send."}]
    import pytest
    with pytest.raises(ValueError):
        parse_claims(payload, doc)
    assert "segment_id" in build_prompt(doc.segments[0])

def test_llm_claim_parser_binds_compact_claim_to_single_segment(tmp_path: Path):
    p = tmp_path / "rfc.txt"; p.write_text("1\n\nThe field is present.\n")
    doc = load_document(p, "fixture")
    payload = [{"candidate_id":"c", "kind":"field", "value":{"name":"field"}, "quote":"The field is present."}]
    assert parse_claims(payload, doc)[0].source_spans[0]["segment_id"] == next(s for s in doc.segments if "field" in s.text).segment_id

def test_evaluation_reports_requirements_and_traceability():
    item = {"id": "x", "text": "The client MUST send.", "level": "MUST", "source_ref": {"section": "1", "quote": "The client MUST send."}}
    report = evaluate({"requirements": [item]}, {"requirements": [item]}, item["text"])
    assert report["requirements"]["f1"] == 1 and report["traceability"]["rate"] == 1


def test_live_adapter_pipeline_parses_json_once(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from nepa.spec_extract import deepseek
    from nepa.spec_extract.pipeline import extract_document
    from nepa.llm.client import LLMResponse
    path = tmp_path / 'source.txt'
    path.write_text('1 Format\n\nA unit consists of one octet.\n')
    class Provider:
        def __init__(self, *args):
            pass
        def prepare(self, request, **kwargs):
            self.segment = json.loads(request.user)['segments'][-1]
            return SimpleNamespace(body=request.user.encode())
        def send(self, prepared):
            s = self.segment
            value = {'claims': [{'candidate_id': 'model-definition', 'kind': 'requirement',
                'value': {'text': 'A unit consists of one octet.', 'level': 'DEFINITION'},
                'quote': 'A unit consists of one octet.',
                'source_spans': [{'segment_id': s['segment_id'], 'quote': 'A unit consists of one octet.'}]}]}
            return LLMResponse(text=json.dumps(value), tokens_in=100, tokens_out=40,
                               cost_cny=0, model='test', parameter_support={})
    monkeypatch.setattr(deepseek, 'OpenAICompatibleProvider', Provider)
    cfg = SimpleNamespace(providers={'deepseek': None}, capabilities={'deepseek/test': SimpleNamespace(max_output_tokens=4000)})
    provider = deepseek.live_provider(cfg, model='test')
    ledger_path = tmp_path / 'evidence.json'
    spec = extract_document(path, llm_provider=provider, evidence=ledger_path)
    assert len(spec['requirements']) == 1
    ledger = json.loads(ledger_path.read_text())
    assert ledger['rejected_outputs'] == []
    assert ledger['llm_calls'][0]['response']['tokens_out'] == 40

def test_conflicting_structural_candidates_keep_richer_evidence(tmp_path):
    p = tmp_path / 'rfc.txt'; p.write_text('1 Format\n\nThe field is encoded as an octet.\n')
    doc = load_document(p, 'fixture')
    from nepa.spec_extract.candidates import Claim
    from nepa.spec_extract.compile import compile_spec
    seg = doc.segments[0]
    low = Claim('low', 'type', {'name':'Octet','base':'bytes'}, [{'segment_id':seg.segment_id,'section':seg.section,'start_line':seg.start_line,'end_line':seg.end_line}], 'The field is encoded as an octet.', .4)
    high = Claim('high', 'type', {'id':'octet','name':'Octet','encoding':{'kind':'enum','base_type':'uint8','values':{'zero':0}}}, [{'segment_id':seg.segment_id,'section':seg.section,'start_line':seg.start_line,'end_line':seg.end_line}], 'The field is encoded as an octet.', .8)
    spec = compile_spec(doc, [low, high])
    assert spec['types'] and spec['types'][0]['id'] == 'octet'

def test_traceability_checker():
    from nepa.spec_extract.acceptance import check_traceability
    spec={'requirements':[{'id':'r'}], 'types':[], 'messages':[]}
    ev={'claim_mappings':[{'target':'/requirements/r'}]}
    assert check_traceability(spec,ev)['rate']==1


def test_whitespace_quote_retains_exact_source_offsets(tmp_path):
    import pytest
    p = tmp_path / 'rfc.txt'
    p.write_text('1 Format\n\nAn endpoint MUST\n  send the token.\n')
    doc = load_document(p, 'synthetic')
    segment = next(s for s in doc.segments if 'endpoint' in s.text)
    claim = {'candidate_id': 'space', 'kind': 'requirement',
             'value': {'text': 'An endpoint MUST send the token.', 'level': 'MUST'},
             'quote': 'An endpoint MUST send the token.',
             'source_spans': [{'segment_id': segment.segment_id}]}
    result = parse_claims([claim], doc)[0]
    span = result.source_spans[0]
    assert span['quote'] == segment.text[span['start_char']:span['end_char']]
    assert '\n' in span['quote']
    claim['quote'] = 'An endpoint MUST NOT send the token.'
    with pytest.raises(ValueError):
        parse_claims([claim], doc)


def test_mixed_claim_batch_preserves_valid_and_audits_invalid(tmp_path):
    from nepa.spec_extract.llm import parse_claim_batch
    path = tmp_path / 'rfc.txt'
    path.write_text('1 Format\n\nA peer MUST send an octet.\n')
    doc = load_document(path, 'fixture')
    valid = {'candidate_id': 'good', 'kind': 'requirement',
             'value': {'text': 'A peer MUST send an octet.', 'level': 'MUST'},
             'quote': 'A peer MUST send an octet.'}
    invalid = {**valid, 'candidate_id': 'bad', 'quote': 'A peer MUST send two octets.'}
    accepted, rejected = parse_claim_batch({'claims': [invalid, valid]}, doc)
    assert [claim.candidate_id for claim in accepted] == ['good']
    assert rejected[0]['rejected_claim'] == invalid
    assert rejected[0]['claim_index'] == 0


def test_compiler_does_not_invent_encoding_for_string_base(tmp_path):
    from nepa.spec_extract.candidates import Claim
    from nepa.spec_extract.compile import compile_with_evidence
    path = tmp_path / 'rfc.txt'
    path.write_text('1 Format\n\nA label is a string.\n')
    doc = load_document(path, 'fixture')
    seg = next(s for s in doc.segments if 'label' in s.text)
    claim = Claim('label', 'type', {'id': 'label', 'name': 'Label', 'base': 'string'},
                  [{'segment_id': seg.segment_id, 'section': seg.section}], 'A label is a string.')
    spec, mappings, gaps = compile_with_evidence(doc, [claim])
    assert spec['types'] == []
    assert any(g['candidate_id'] == 'label' for g in gaps)


def test_structural_type_metrics_reports_coarse_encoding():
    from nepa.spec_extract.evaluate import structural_type_metrics
    gold={'types':[{'encoding':{'kind':'enum','base_type':'uint8','values':{'a':1}}}]}
    pred={'types':[{'encoding':{'kind':'enum','base_type':'uint8','values':{'a':1,'b':2}}}]}
    report=structural_type_metrics(gold,pred)
    assert report['coarse_encoding']['recall']==1.0
    assert report['precision']==0.0


def test_phase1_report_filters_message_scope(tmp_path):
    import json
    from nepa.spec_extract.phase1 import report
    gold={'messages':[{'id':'a','name':'A'},{'id':'b','name':'B'}], 'requirements':[], 'types':[]}
    pred={'messages':[{'id':'a','name':'A'}], 'requirements':[], 'types':[]}
    g=tmp_path/'g.json'; p=tmp_path/'p.json'; g.write_text(json.dumps(gold)); p.write_text(json.dumps(pred))
    assert report(g,p,message_ids=['a'])['messages']['recall']==1.0
