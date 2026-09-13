"""Usage pricing and recursive secret redaction, independent of orchestration."""
from __future__ import annotations
from datetime import datetime
import os
from zoneinfo import ZoneInfo
from typing import Any
from ..config import ModelPrice

def price_period(started_at: float) -> str:
    local = datetime.fromtimestamp(started_at, ZoneInfo("Asia/Shanghai"))
    minute = local.hour * 60 + local.minute
    return "peak" if local.weekday() < 5 and (540 <= minute < 720 or 840 <= minute < 1080) else "off_peak"


def price_usage(price: ModelPrice, tokens_in: int, tokens_out: int, *,
                started_at: float | None = None, cache_hit_tokens: int | None = None) -> dict[str, Any]:
    if tokens_in < 0 or tokens_out < 0:
        raise ValueError("token counts cannot be negative")
    if cache_hit_tokens is not None and not 0 <= cache_hit_tokens <= tokens_in:
        raise ValueError("cache-hit tokens must be within total input tokens")
    tier = next((tier for tier in price.tiers if tokens_in <= tier.max_input_tokens), None)
    if price.tiers and tier is None:
        raise ValueError("total input exceeds configured pricing tiers")
    selected = tier or price
    period = "flat" if price.schedule == "flat" else (price_period(started_at) if started_at is not None else "peak")
    factor = price.off_peak_multiplier if period == "off_peak" else 1.0
    hits = cache_hit_tokens or 0
    rates = {"cache_hit_input": selected.cache_hit_input_cny_per_million_tokens * factor,
             "cache_miss_input": selected.input_cny_per_million_tokens * factor,
             "output": selected.output_cny_per_million_tokens * factor}
    cost = (hits * rates["cache_hit_input"] + (tokens_in - hits) * rates["cache_miss_input"] + tokens_out * rates["output"]) / 1_000_000
    return {"currency": "CNY", "cost_cny": cost, "period": period, "started_at": started_at,
            "rates_per_million_tokens": rates, "cache_hit_tokens": hits, "cache_miss_tokens": tokens_in - hits,
            "cache_usage_basis": "provider" if cache_hit_tokens is not None else "missing_assumed_all_miss",
            "tier_max_input_tokens": tier.max_input_tokens if tier else None,
            "tier_basis": "total_input_tokens",
            "time_basis": "flat" if price.schedule == "flat" else ("request_start_estimate" if started_at is not None else "peak_upper_bound")}


def calculate_cost(price: ModelPrice, tokens_in: int, tokens_out: int) -> float:
    """Conservative CNY reservation: peak rates and all input tokens cache misses."""
    # Consider every reachable tier, even if a configured later tier is cheaper.
    candidates = [tokens_in]
    candidates.extend(tier.max_input_tokens for tier in price.tiers if tier.max_input_tokens < tokens_in)
    return max(float(price_usage(price, count, tokens_out)["cost_cny"]) for count in candidates)

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
