"""Closed, deterministic runtime configuration for M1-1."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .speclib.lint import canonical_json_bytes


class ConfigError(ValueError):
    """Raised when runtime configuration cannot be resolved or verified."""


class ConfigSnapshotDrift(ConfigError):
    """Raised when a persisted public snapshot does not match its hash."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderConfig(_Model):
    kind: str
    base_url: str
    api_key_env: str | None = None


class ModelConfig(_Model):
    provider: str
    model: str
    temperature: float = 0.0
    max_tokens: int = Field(gt=0)


class ModelPrice(_Model):
    input_usd_per_million_tokens: float = Field(ge=0)
    output_usd_per_million_tokens: float = Field(ge=0)


class PricingConfig(_Model):
    models: dict[str, ModelPrice]

    @field_validator("models")
    @classmethod
    def canonical_model_keys(cls, values: dict[str, ModelPrice]) -> dict[str, ModelPrice]:
        for key in values:
            parts = key.split("/")
            if len(parts) != 2 or not all(parts):
                raise ValueError("pricing model keys must be canonical <provider>/<model> strings")
        return values


class TierConfig(ModelConfig):
    pass


class RoleConfig(_Model):
    tier: str
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, gt=0)
    escalate_to: str | None = None


class BudgetConfig(_Model):
    wall_clock_hours: float = Field(ge=0)
    max_cost_usd: float = Field(ge=0)
    plan_architecture_repairs: int = Field(ge=0)
    plan_task_shard_repairs: int = Field(ge=0)
    plan_critic_repairs: int = Field(ge=0)
    plan_global_replans: int = Field(ge=0)
    coder_context_max_tokens: int = Field(gt=0)
    task_fix_attempts: int = Field(ge=0)
    repair_rounds: int = Field(ge=0)


class PlanningConfig(_Model):
    strategy: str
    max_task_files: int = Field(gt=0)
    context_safety_margin_ratio: float = Field(ge=0, le=1)


class RunConfig(_Model):
    until: str | None = None


class StageConfig(_Model):
    l0: bool = True
    l1: bool = True
    l2: bool = True
    l3: bool = False


class AssetsConfig(_Model):
    target_profile: str
    test_bundle: str


class SandboxConfig(_Model):
    image: str
    cpu: int = Field(gt=0)
    mem_gb: float = Field(gt=0)


class ResolvedConfig(_Model):
    providers: dict[str, ProviderConfig]
    calibration_models: dict[str, ModelConfig]
    tiers: dict[str, TierConfig]
    roles: dict[str, RoleConfig]
    pricing: PricingConfig
    budgets: BudgetConfig
    planning: PlanningConfig
    run: RunConfig
    stages: StageConfig
    assets: AssetsConfig
    sandbox: SandboxConfig

    @property
    def snapshot(self) -> dict[str, Any]:
        return public_config_snapshot(self)

    @property
    def snapshot_sha256(self) -> str:
        return config_snapshot_sha256(self.snapshot)


_DEFAULTS: dict[str, Any] = {
    "providers": {
        "anthropic": {
            "kind": "anthropic",
            "base_url": "https://www.sotamodel.net/v1/chat/completions",
            "api_key_env": "NEPA_CLAUDE_API_KEY",
        },
        "deepseek": {
            "kind": "openai_compat",
            "base_url": "https://api.deepseek.com",
            "api_key_env": "NEPA_DS_API_KEY",
        },
        "qwen": {
            "kind": "openai_compat",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key_env": "NEPA_QWEN_API_KEY",
        },
    },
    "calibration_models": {
        "claude": {"provider": "anthropic", "model": "claude-opus-5", "temperature": 0.0, "max_tokens": 16000},
        "qwen": {"provider": "qwen", "model": "qwen3.7-max-2026-06-08", "temperature": 0.0, "max_tokens": 16000},
        "deepseek": {"provider": "deepseek", "model": "deepseek-v4-flash", "temperature": 0.0, "max_tokens": 16000},
    },
    "tiers": {
        "T1": {"provider": "anthropic", "model": "claude-opus-5", "temperature": 0.0, "max_tokens": 16000},
        "T2": {"provider": "deepseek", "model": "deepseek-v4-flash", "temperature": 0.1, "max_tokens": 16000},
        "T3": {"provider": "qwen", "model": "qwen3.7-max-2026-06-08", "temperature": 0.0, "max_tokens": 4000},
    },
    "roles": {},
    "pricing": {"models": {}},
    "budgets": {
        "wall_clock_hours": 4,
        "max_cost_usd": 20,
        "plan_architecture_repairs": 1,
        "plan_task_shard_repairs": 1,
        "plan_critic_repairs": 2,
        "plan_global_replans": 1,
        "coder_context_max_tokens": 24000,
        "task_fix_attempts": 3,
        "repair_rounds": 3,
    },
    "planning": {"strategy": "layered", "max_task_files": 4, "context_safety_margin_ratio": 0.15},
    "run": {"until": None},
    "stages": {"l0": True, "l1": True, "l2": True, "l3": False},
    "assets": {"target_profile": "gold_file/target.json", "test_bundle": "gold_file/test_bundle.json"},
    "sandbox": {"image": "nepa-sandbox:latest", "cpu": 2, "mem_gb": 4},
}

_DEFAULT_ROLES = {
    "doc_segmenter": {"tier": "T3"},
    "segment_classifier": {"tier": "T3"},
    "spec_extractor": {"tier": "T1"},
    "spec_merger": {"tier": "T1"},
    "spec_critic": {"tier": "T1", "provider": "deepseek", "model": "deepseek-v4-flash"},
    "architecture_planner": {"tier": "T1"},
    "task_planner": {"tier": "T1"},
    "plan_critic": {"tier": "T1", "provider": "deepseek", "model": "deepseek-v4-flash"},
    "flat_plan_baseline": {"tier": "T1"},
    "coder": {"tier": "T2"},
    "diagnoser": {"tier": "T2", "escalate_to": "T1"},
    "fixer": {"tier": "T2", "escalate_to": "T1"},
    "reporter": {"tier": "T3"},
}
_DEFAULTS["roles"] = _DEFAULT_ROLES


def _merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if not isinstance(key, str):
            raise ConfigError("configuration keys must be strings")
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _read_yaml(path: Path) -> Mapping[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"unable to read configuration {path}: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, Mapping):
        raise ConfigError("configuration root must be an object")
    return loaded


def load_config(path: Path | str | None = None, overrides: Mapping[str, object] | None = None) -> ResolvedConfig:
    """Resolve defaults, an optional YAML file, and explicit nested overrides."""

    values = copy.deepcopy(_DEFAULTS)
    if path is not None:
        values = _merge(values, _read_yaml(Path(path)))
    if overrides is not None:
        values = _merge(values, overrides)
    try:
        return ResolvedConfig.model_validate(values)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc


def public_config_snapshot(config: ResolvedConfig | Mapping[str, Any]) -> dict[str, Any]:
    """Return the canonical, secret-free configuration representation."""

    if isinstance(config, ResolvedConfig):
        return config.model_dump(mode="json")
    try:
        return ResolvedConfig.model_validate(config).model_dump(mode="json")
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc


def config_snapshot_sha256(snapshot: Mapping[str, Any]) -> str:
    try:
        encoded = canonical_json_bytes(dict(snapshot))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"configuration snapshot is not canonical JSON: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()


def verify_config_snapshot(snapshot: Mapping[str, Any], expected_sha256: str) -> None:
    actual = config_snapshot_sha256(snapshot)
    if actual != expected_sha256:
        raise ConfigSnapshotDrift(
            f"configuration snapshot hash mismatch: expected {expected_sha256}, got {actual}"
        )


def configured_model_price(config: ResolvedConfig, provider: str, model: str) -> ModelPrice:
    key = f"{provider}/{model}"
    try:
        return config.pricing.models[key]
    except KeyError as exc:
        raise ConfigError(f"missing configured price for {key}") from exc
