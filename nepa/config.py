"""Versioned runtime configuration; secrets remain in environment variables."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ConfigError(ValueError):
    pass


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderConfig(_Model):
    kind: str
    base_url: str
    api_key_env: str | None = None


class WireControls(_Model):
    output_token_field: Literal["max_tokens", "max_completion_tokens"]
    enable_thinking: bool | None = None
    preserve_thinking: bool | None = None
    parallel_tool_calls: Literal[False] | None = None
    completion_reserve_tokens: int = Field(default=0, ge=0)


class ModelCapabilities(_Model):
    stream: bool
    json_object: bool
    tool_calls: bool
    json_schema: bool = False
    json_requires_instruction: bool = False
    wire: WireControls
    max_output_tokens: int = Field(gt=0)
    context_tokens: int | None = Field(default=None, gt=0)
    max_input_tokens: int | None = Field(default=None, gt=0)
    thinking_max_input_tokens: int | None = Field(default=None, gt=0)
    temperature_min: float = 0
    temperature_max_exclusive: float | None = None
    stop: bool = False
    identity: Literal["exact"] = "exact"
    usage: Literal["deepseek", "qwen", "chat_completions"]


class PriceTier(_Model):
    max_input_tokens: int = Field(gt=0)
    input_cny_per_million_tokens: float = Field(ge=0)
    output_cny_per_million_tokens: float = Field(ge=0)
    cache_hit_input_cny_per_million_tokens: float = Field(ge=0)


class ModelPrice(_Model):
    input_cny_per_million_tokens: float = Field(ge=0)
    output_cny_per_million_tokens: float = Field(ge=0)
    cache_hit_input_cny_per_million_tokens: float = Field(ge=0)
    schedule: Literal["deepseek", "flat"] = "flat"
    off_peak_multiplier: float = Field(default=1, gt=0, le=1)
    tiers: list[PriceTier] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_tiers(self) -> ModelPrice:
        limits = [tier.max_input_tokens for tier in self.tiers]
        if limits != sorted(set(limits)):
            raise ValueError("price tiers must have strictly increasing input limits")
        if self.schedule == "flat" and self.off_peak_multiplier != 1:
            raise ValueError("flat prices require off_peak_multiplier=1")
        return self


class CampaignConfig(_Model):
    phase: Literal["generation", "capability", "public_tools"] = "generation"
    phase_max_cost_cny: dict[Literal["capability", "public_tools"], float] = {"capability": 5.0, "public_tools": 5.0}

    @model_validator(mode="after")
    def check_phase_caps(self) -> CampaignConfig:
        if set(self.phase_max_cost_cny) != {"capability", "public_tools"} or any(
            not 0 < value <= 5 for value in self.phase_max_cost_cny.values()
        ):
            raise ValueError("capability/public_tools phase caps must each be within CNY5")
        return self


class CoderConfig(_Model):
    provider: str = "deepseek"
    model: str = "deepseek-v4-pro"
    fast_model: str | None = None
    action_format: Literal["json_object", "tool_calls"] = "json_object"
    temperature: float = 0
    max_tokens: int = Field(default=16000, gt=0)
    context_max_bytes: int = Field(default=180000, gt=0)

    def for_task(self, kind: str, *, retry: bool = False, repair: bool = False) -> CoderConfig:
        fast = self.fast_model and kind in {"bootstrap", "message", "requirements"} and not retry and not repair
        return self.model_copy(update={"model": self.fast_model if fast else self.model})


class BudgetConfig(_Model):
    max_cost_cny: float = Field(default=20, gt=0, le=20)
    campaign_max_cost_cny: float = Field(default=300, gt=0, le=300)
    wall_clock_hours: float = Field(default=4, gt=0, le=4)
    decisions_per_session: int = Field(default=40, gt=0, le=40)
    sessions_per_task: int = Field(default=3, gt=0, le=3)
    followups: int = Field(default=3, ge=0, le=3)
    final_repairs: int = Field(default=3, ge=0, le=3)


class SandboxConfig(_Model):
    image: str = "nepa-sandbox:refactor"
    cpu: int = Field(default=2, gt=0)
    mem_gb: float = Field(default=4, gt=0)
    command_timeout_s: int = Field(default=120, gt=0)
    build_timeout_s: int = Field(default=180, gt=0)


class ResolvedConfig(_Model):
    schema_version: str = "3.0"
    providers: dict[str, ProviderConfig]
    coder: CoderConfig = Field(default_factory=CoderConfig)
    pricing: dict[str, ModelPrice]
    capabilities: dict[str, ModelCapabilities]
    campaign: CampaignConfig = Field(default_factory=CampaignConfig)
    budgets: BudgetConfig = Field(default_factory=BudgetConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)

    @model_validator(mode="after")
    def check_model(self) -> ResolvedConfig:
        if self.schema_version != "3.0":
            raise ValueError("unsupported configuration version; use the baseline for legacy runs")
        if self.coder.provider not in self.providers:
            raise ValueError("coder provider is not configured")
        if f"{self.coder.provider}/{self.coder.model}" not in self.pricing:
            raise ValueError("coder price must be explicitly configured")
        if self.coder.fast_model and f"{self.coder.provider}/{self.coder.fast_model}" not in self.pricing:
            raise ValueError("fast coder price must be explicitly configured")
        for model in {self.coder.model, self.coder.fast_model} - {None}:
            if f"{self.coder.provider}/{model}" not in self.capabilities:
                raise ValueError(f"model capabilities must be explicitly configured: {model}")
            if self.coder.provider != "deepseek" and self.pricing[f"{self.coder.provider}/{model}"].schedule == "deepseek":
                raise ValueError("DeepSeek schedule cannot be applied to another provider")
        return self


_DEFAULTS: dict[str, Any] = {
    "schema_version": "3.0",
    "providers": {
        "deepseek": {"kind": "openai_compat", "base_url": "https://api.deepseek.com", "api_key_env": "NEPA_DS_API_KEY"},
        "qwen": {"kind": "openai_compat", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key_env": "NEPA_QWEN_API_KEY"},
        "anthropic": {"kind": "anthropic", "base_url": "https://www.sotamodel.net/v1/chat/completions", "api_key_env": "NEPA_CLAUDE_API_KEY"},
    },
    "pricing": {
        "deepseek/deepseek-v4-pro": {"input_cny_per_million_tokens": 9.0, "output_cny_per_million_tokens": 27.0, "cache_hit_input_cny_per_million_tokens": 0.30, "schedule": "deepseek", "off_peak_multiplier": 0.5},
        "deepseek/deepseek-v4-flash": {"input_cny_per_million_tokens": 2.0, "output_cny_per_million_tokens": 8.0, "cache_hit_input_cny_per_million_tokens": 0.04, "schedule": "deepseek", "off_peak_multiplier": 0.5},
        "deepseek/deepseek-flash": {"input_cny_per_million_tokens": 2.0, "output_cny_per_million_tokens": 8.0, "cache_hit_input_cny_per_million_tokens": 0.04, "schedule": "deepseek", "off_peak_multiplier": 0.5},
    },
    "capabilities": {
        f"deepseek/{model}": {
            "stream": True, "json_object": True, "tool_calls": True,
            "wire": {"output_token_field": "max_tokens"},
            "max_output_tokens": 16000, "usage": "deepseek",
        }
        for model in ("deepseek-v4-pro", "deepseek-v4-flash", "deepseek-flash")
    },
}


def _merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if not isinstance(key, str):
            raise ConfigError("configuration keys must be strings")
        result[key] = _merge(result[key], value) if isinstance(value, Mapping) and isinstance(result.get(key), dict) else copy.deepcopy(value)
    return result


def load_config(path: str | Path | None = None, overrides: Mapping[str, Any] | None = None) -> ResolvedConfig:
    values = copy.deepcopy(_DEFAULTS)
    try:
        if path is not None:
            overlay = yaml.safe_load(Path(path).read_text()) or {}
            if not isinstance(overlay, dict):
                raise ConfigError("configuration must be an object")
            values = _merge(values, overlay)
        if overrides:
            values = _merge(values, overrides)
        return ResolvedConfig.model_validate(values)
    except (OSError, ValueError, ValidationError, yaml.YAMLError) as exc:
        raise ConfigError(f"invalid configuration (legacy configuration is not supported): {exc}") from exc


def public_config_snapshot(config: ResolvedConfig) -> dict[str, Any]:
    return config.model_dump(mode="json")


def snapshot_sha256(value: dict[str, Any]) -> str:
    from .speclib.lint import canonical_json_bytes
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def configured_model_price(config: ResolvedConfig, provider: str, model: str) -> ModelPrice:
    try:
        return config.pricing[f"{provider}/{model}"]
    except KeyError as exc:
        raise ConfigError(f"missing price for {provider}/{model}") from exc
