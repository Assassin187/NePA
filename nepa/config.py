"""Versioned runtime configuration; secrets remain in environment variables."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, Mapping

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


class ModelPrice(_Model):
    input_usd_per_million_tokens: float = Field(ge=0)
    output_usd_per_million_tokens: float = Field(ge=0)


class CoderConfig(_Model):
    provider: str = "deepseek"
    model: str = "deepseek-v4-pro"
    fast_model: str | None = None
    temperature: float = 0
    max_tokens: int = Field(default=16000, gt=0)
    context_max_bytes: int = Field(default=180000, gt=0)

    def for_task(self, kind: str, *, retry: bool = False, repair: bool = False) -> CoderConfig:
        fast = self.fast_model and kind in {"bootstrap", "message", "requirements"} and not retry and not repair
        return self.model_copy(update={"model": self.fast_model if fast else self.model})


class BudgetConfig(_Model):
    max_cost_usd: float = Field(default=100, gt=0, le=100)
    campaign_max_cost_usd: float = Field(default=300, gt=0, le=300)
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
    schema_version: str = "1.0"
    providers: dict[str, ProviderConfig]
    coder: CoderConfig = Field(default_factory=CoderConfig)
    pricing: dict[str, ModelPrice]
    budgets: BudgetConfig = Field(default_factory=BudgetConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)

    @model_validator(mode="after")
    def check_model(self) -> ResolvedConfig:
        if self.schema_version != "1.0":
            raise ValueError("unsupported configuration version; use the baseline for legacy runs")
        if self.coder.provider not in self.providers:
            raise ValueError("coder provider is not configured")
        if f"{self.coder.provider}/{self.coder.model}" not in self.pricing:
            raise ValueError("coder price must be explicitly configured")
        if self.coder.fast_model and f"{self.coder.provider}/{self.coder.fast_model}" not in self.pricing:
            raise ValueError("fast coder price must be explicitly configured")
        return self


_DEFAULTS: dict[str, Any] = {
    "schema_version": "1.0",
    "providers": {
        "deepseek": {"kind": "openai_compat", "base_url": "https://api.deepseek.com", "api_key_env": "NEPA_DS_API_KEY"},
        "qwen": {"kind": "openai_compat", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key_env": "NEPA_QWEN_API_KEY"},
        "anthropic": {"kind": "anthropic", "base_url": "https://www.sotamodel.net/v1/chat/completions", "api_key_env": "NEPA_CLAUDE_API_KEY"},
    },
    "pricing": {
        "deepseek/deepseek-v4-pro": {"input_usd_per_million_tokens": 1.32, "output_usd_per_million_tokens": 3.96},
        "deepseek/deepseek-v4-flash": {"input_usd_per_million_tokens": 0.30, "output_usd_per_million_tokens": 1.20},
        "deepseek/deepseek-flash": {"input_usd_per_million_tokens": 0.30, "output_usd_per_million_tokens": 1.20},
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
