"""Protocol-neutral deterministic candidate discovery."""
from __future__ import annotations
import re
from dataclasses import dataclass, asdict
from typing import Any
from .ingest import Segment
_NORM = re.compile(r"\b(MUST NOT|SHOULD NOT|MUST|SHOULD|MAY|REQUIRED|SHALL NOT|SHALL|RECOMMENDED|OPTIONAL)\b")
@dataclass
class Claim:
    candidate_id: str
    kind: str
    value: dict[str, Any]
    source_spans: list[dict[str, Any]]
    quote: str
    confidence: float = 0.5
    status: str = "candidate"
def discover(segments: tuple[Segment, ...]) -> list[Claim]:
    claims: list[Claim] = []
    for seg in segments:
        for index, sentence in enumerate(re.split(r"(?<=[.!?])\s+", seg.text)):
            sentence = sentence.strip()
            match = _NORM.search(sentence)
            if not match:
                continue
            level = {"REQUIRED":"MUST","SHALL":"MUST","SHALL NOT":"MUST NOT","RECOMMENDED":"SHOULD","OPTIONAL":"MAY"}.get(match.group(1), match.group(1))
            kind = "requirement"
            value: dict[str, Any] = {"text": sentence, "level": level}
            if level == "SHOULD NOT":
                kind = "unsupported_candidate"
                value["reason"] = "preserved normative level requires v4 review"
            claims.append(Claim(f"{seg.segment_id}-r{index:03d}", kind, value,
                [{"segment_id":seg.segment_id,"section":seg.section,"start_line":seg.start_line,"end_line":seg.end_line,"quote":sentence}], sentence, .7))
    return claims
def claim_dict(claim: Claim) -> dict[str, Any]:
    return asdict(claim)
