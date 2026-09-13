"""Provider-neutral DeepSeek request construction for RFC extraction.

The caller supplies the existing NePA LLMClient transport; this module only
binds the extraction prompt and keeps protocol data out of the code generator.
"""
from __future__ import annotations
from typing import Any
import hashlib
import time
from .llm import PROMPT_VERSION
from .llm import SYSTEM_PROMPT, build_prompt
from .ingest import Segment
from .ingest import Document
from .llm import parse_claims
from ..llm.providers.openai_compat import OpenAICompatibleProvider
from ..llm.client import LLMRequest

def live_provider(config: Any, *, model: str = "deepseek-v4-pro"):
    provider = OpenAICompatibleProvider("deepseek", config.providers["deepseek"])
    capabilities = config.capabilities[f"deepseek/{model}"]
    records = []
    def call(segment: Segment | tuple[Segment, ...]) -> str:
        segments = (segment,) if isinstance(segment, Segment) else segment
        merged = segments[0]
        doc = Document(merged.doc_id, "", "0" * 64, "txt", "\n".join(s.text for s in segments), segments)
        request = LLMRequest(role="spec-extractor", system=SYSTEM_PROMPT, user=build_prompt(segment), model=model, action_format="json_object", temperature=0, max_tokens=min(4000, capabilities.max_output_tokens))
        prepared = provider.prepare(request, model=model, capabilities=capabilities)
        started = time.monotonic()
        response = provider.send(prepared)
        records.append({"model": model, "prompt_version": PROMPT_VERSION,
            "request_sha256": hashlib.sha256(prepared.body).hexdigest(),
            "response_sha256": hashlib.sha256(response.text.encode()).hexdigest(),
            "segment_ids": [s.segment_id for s in segments],
            "elapsed_seconds": time.monotonic() - started,
            "response": response.model_dump(mode="json")})
        return response.text
    call.records = records
    return call

def request_parts(segment: Segment) -> dict[str, Any]:
    return {"role": "spec-extractor", "system": SYSTEM_PROMPT, "user": build_prompt(segment), "model_family": "deepseek", "action_format": "json_object", "temperature": 0}
