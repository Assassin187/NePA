"""Packaged production Schema helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_ROOT = Path(__file__).resolve().parent


def load_schema(name: str) -> dict[str, Any]:
    value = json.loads((_ROOT / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"schema {name} must be an object")
    return value


def load_example(name: str) -> Any:
    return json.loads((_ROOT / "examples" / name).read_text(encoding="utf-8"))


def architecture_draft_contract() -> tuple[dict[str, Any], Any]:
    return load_schema("architecture-draft.schema.json"), load_example("architecture-draft.example.json")


__all__ = ["architecture_draft_contract", "load_example", "load_schema"]
