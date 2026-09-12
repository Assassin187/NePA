"""Packaged production JSON schemas."""
import json
from pathlib import Path
from typing import Any

def load_schema(name: str) -> dict[str, Any]:
    return json.loads((Path(__file__).parent / name).read_bytes())

def load_example(name: str) -> Any:
    return json.loads((Path(__file__).parent / "examples" / name).read_bytes())
