"""Usage pricing and recursive secret redaction, independent of orchestration."""
from __future__ import annotations
import os
from typing import Any
from ..config import ModelPrice

def calculate_cost(price: ModelPrice, tokens_in: int, tokens_out: int) -> float:
    if tokens_in < 0 or tokens_out < 0:
        raise ValueError("token counts cannot be negative")
    return (tokens_in * price.input_usd_per_million_tokens + tokens_out * price.output_usd_per_million_tokens) / 1_000_000

def redact(value: Any, env_names: list[str]) -> Any:
    secrets = [os.environ[name] for name in env_names if os.environ.get(name)]
    def clean(item: Any) -> Any:
        if isinstance(item, str):
            for secret in secrets:
                item = item.replace(secret, "[REDACTED]")
        elif isinstance(item, list):
            return [clean(v) for v in item]
        elif isinstance(item, dict):
            return {k: clean(v) for k, v in item.items()}
        return item
    return clean(value)
