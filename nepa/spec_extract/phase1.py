"""Evaluation-only Phase 1 scope report; gold never enters extraction prompts."""
from __future__ import annotations
import json
from pathlib import Path
from .evaluate import evaluate

def report(gold_path, predicted_path, *, message_ids=None, document_text=''):
    gold=json.loads(Path(gold_path).read_text()); pred=json.loads(Path(predicted_path).read_text())
    if message_ids is not None:
        gold['messages']=[m for m in gold.get('messages',[]) if m.get('id') in set(message_ids)]
    result=evaluate(gold,pred,document_text)
    result['evaluation_scope']={'message_ids':list(message_ids) if message_ids is not None else None,'gold_blind_runtime':True}
    return result
