"""RFC to Spec IR extraction primitives.

The package is deliberately independent from the code-generation orchestrator.
"""
from .ingest import Document, Segment, load_document
from .pipeline import extract_document
from .llm import build_prompt, parse_claims
from .deepseek import request_parts, live_provider
from .evaluate import evaluate

__all__ = ["Document", "Segment", "load_document", "extract_document", "build_prompt", "parse_claims", "request_parts", "live_provider", "evaluate"]
