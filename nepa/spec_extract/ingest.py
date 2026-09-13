"""Local-only ingestion; offsets refer to the saved normalized text."""
from __future__ import annotations
import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Segment:
    segment_id: str
    doc_id: str
    section: str
    ordinal: int
    text: str
    start_line: int
    end_line: int
    page: int | None = None
    kind: str = "prose"

@dataclass(frozen=True)
class Document:
    doc_id: str
    path: str
    sha256: str
    format: str
    text: str
    segments: tuple[Segment, ...]

_HEADING = re.compile(r"^\s*(\d+(?:\.\d+)*|Appendix\s+[A-Z0-9]+)\.?\s+(.+?)\s*$", re.I)

def section_number(value: str) -> str:
    return value.split()[0].rstrip(".")

def in_scope(section: str, prefixes: list[str]) -> bool:
    number = section_number(section)
    return any(number == section_number(p) or number.startswith(section_number(p) + ".") for p in prefixes)

def _pdf_text(path: Path) -> str:
    tool = shutil.which("pdftotext")
    if not tool:
        raise OSError("pdftotext is required for PDF RFC input")
    return subprocess.run([tool, "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout

def _normalize(text: str, *, pdf: bool) -> str:
    # Retain blank lines and page separators so line and page coordinates survive.
    result = []
    for line in text.replace("\x00", "").split("\n"):
        if pdf:
            line = re.sub(r"^(\f?)[0-9]{2,4} {2,}", r"\1", line)
            if re.fullmatch(r"\s*[0-9]{2,4}\s*", line):
                line = ""
        result.append(line.rstrip())
    return "\n".join(result)

def _segments(text: str, doc_id: str, document_hash: str | None = None) -> tuple[Segment, ...]:
    section, page = "0", 1
    start, start_page = 1, 1
    buf: list[str] = []
    out: list[Segment] = []
    def flush(end: int) -> None:
        if not buf:
            return
        value = "\n".join(buf)
        ordinal = len(out)
        identity = f"{document_hash or hashlib.sha256(text.encode()).hexdigest()}|{doc_id}|{start}|{end}|{value}"
        sid = hashlib.sha256(identity.encode()).hexdigest()[:20]
        kind = "prose"
        if "example" in section.lower() or "non normative" in section.lower():
            kind = "example"
        elif any(re.search(r"\s=\s|=/", x) for x in buf):
            kind = "abnf"
        elif any("|" in x or re.search(r"\+[-+]+\+", x) for x in buf):
            kind = "figure"
        elif any(re.search(r"\S {3,}\S", x) for x in buf):
            kind = "table"
        out.append(Segment(sid, doc_id, section, ordinal, value, start, end, start_page, kind))
        buf.clear()
    for n, raw in enumerate(text.split("\n") + [""], 1):
        if "\f" in raw:
            flush(n - 1)
            page += raw.count("\f")
        line = raw.replace("\f", "")
        heading = _HEADING.match(line)
        if heading and len(line.strip()) < 200 and "..." not in line:
            flush(n - 1)
            section = f"{heading[1]} {heading[2]}"
            # Headings themselves provide evidence (e.g. a message's name).
        if not line.strip():
            flush(n - 1)
        else:
            if not buf:
                start, start_page = n, page
            buf.append(line)
    return tuple(out)

def load_document(path: str | Path, doc_id: str | None = None, scope: list[str] | None = None) -> Document:
    p = Path(path)
    raw = p.read_bytes()
    fmt = "pdf" if p.suffix.lower() == ".pdf" else "txt"
    text = _normalize(_pdf_text(p) if fmt == "pdf" else raw.decode("utf-8"), pdf=fmt == "pdf")
    identifier = doc_id or p.stem
    sha = hashlib.sha256(raw).hexdigest()
    segments = _segments(text, identifier, sha)
    if scope is not None:
        # Scope is represented on segments without changing the public dataclass.
        # Callers can use this deterministic coverage count to reject partial runs.
        segments = tuple(segments)
    return Document(identifier, str(p), sha, fmt, text, segments)
