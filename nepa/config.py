"""NePA 配置系统（system_design.md 8.3）。

分层加载链：模型字段默认值 ← YAML 配置文件 ← 覆盖 dict（CLI 参数）。
API 密钥只走环境变量（8.3）：配置里只保存环境变量名（api_key_env），
密钥值从不进入任何模型字段，因此任何序列化输出都天然不含密钥。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConfigError(ValueError):
    """配置错误（引用缺失、文件不合法等）。"""


class MissingAPIKeyError(ConfigError):
    """环境变量中找不到 API 密钥（8.3：密钥只走环境变量）。"""


class _StrictModel(BaseModel):
    """禁止未知字段，尽早暴露配置文件中的拼写错误。"""

    model_config = ConfigDict(extra="forbid")


class ProviderConfig(_StrictModel):
    """LLM 提供方（8.3、8.4：两个内置 provider 覆盖所有 API 型号）。"""

    kind: Literal["anthropic", "openai_compat"]
    base_url: str
    # 8.3：密钥只走环境变量；未显式配置时默认 NEPA_<PROVIDER>_API_KEY
    api_key_env: str | None = None


class TierConfig(_StrictModel):
    """档位 → 具体型号绑定（4.6、8.3）。"""

    provider: str
    model: str
    temperature: float = 0.0
    max_tokens: int = 16000


class RoleConfig(_StrictModel):
    """角色 → 档位绑定，可按角色覆盖 provider/model（4.5、4.6、8.3）。"""

    tier: str
    provider: str | None = None
    model: str | None = None
    escalate_to: str | None = None  # 4.6 规则 2：升级路径


class BudgetsConfig(_StrictModel):
    """预算默认值（4.7、8.3）。"""

    wall_clock_hours: float = 4.0
    max_cost_usd: float = 20.0  # 4.7：default.yaml 提供，实验前须显式覆盖
    coder_context_max_tokens: int = 24000
    task_fix_attempts: int = 3
    repair_rounds: int = 3


class StagesConfig(_StrictModel):
    """阶段开关（8.3）。"""

    l3_interop: bool = False


class SandboxConfig(_StrictModel):
    """沙箱资源配置（8.3、8.5）。"""

    image: str = "nepa-sandbox:latest"
    cpu: int = 2
    mem_gb: int = 4


class PricingEntry(_StrictModel):
    """型号价格：USD / 1M tokens 输入/输出单价（8.4 计费）。"""

    input: float
    output: float


class FeatureExclusion(_StrictModel):
    """scope 排除项：{feature, reason}（8.3 scope 配置）。"""

    feature: str
    reason: str


class ScopeConfig(_StrictModel):
    """doc-run 范围声明 configs/scope-<protocol>.yaml（8.3 末尾）。

    features_included 元素在 YAML 中既可能是纯字符串，也可能被解析为
    单键映射（如 "- packets: ..."），文档未约束元素结构，两者都接受。
    """

    protocol: str
    version: str
    roles: list[str]
    features_included: list[str | dict[str, str]]
    features_excluded: list[FeatureExclusion] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


def _default_api_key_env(provider_name: str) -> str:
    """8.3：默认环境变量命名 NEPA_<PROVIDER>_API_KEY。"""
    return f"NEPA_{provider_name.upper()}_API_KEY"


class NepaConfig(_StrictModel):
    """顶层配置模型（8.3 default.yaml 结构）。"""

    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    tiers: dict[str, TierConfig] = Field(default_factory=dict)
    roles: dict[str, RoleConfig] = Field(default_factory=dict)
    budgets: BudgetsConfig = Field(default_factory=BudgetsConfig)
    stages: StagesConfig = Field(default_factory=StagesConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    pricing: dict[str, PricingEntry] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_references(self) -> "NepaConfig":
        """交叉引用校验：tier→provider、role→tier/escalate_to/provider 必须存在。"""
        for tier_name, tier in self.tiers.items():
            if tier.provider not in self.providers:
                raise ValueError(
                    f"tier {tier_name!r} 引用未定义 provider {tier.provider!r}"
                )
        for role_name, role in self.roles.items():
            if role.tier not in self.tiers:
                raise ValueError(f"role {role_name!r} 引用未定义 tier {role.tier!r}")
            if role.escalate_to is not None and role.escalate_to not in self.tiers:
                raise ValueError(
                    f"role {role_name!r} 的 escalate_to 引用未定义 tier "
                    f"{role.escalate_to!r}"
                )
            if role.provider is not None and role.provider not in self.providers:
                raise ValueError(
                    f"role {role_name!r} 引用未定义 provider {role.provider!r}"
                )
        return self

    def resolve_api_key(self, provider: str) -> str:
        """从环境变量取指定 provider 的 API 密钥（8.3：密钥只走环境变量）。

        返回值只应传给 LLM 客户端，禁止写入配置、run.json 或任何日志。
        """
        if provider not in self.providers:
            raise ConfigError(f"未知 provider: {provider!r}")
        env_name = self.providers[provider].api_key_env or _default_api_key_env(provider)
        value = os.environ.get(env_name)
        if not value:
            raise MissingAPIKeyError(
                f"provider {provider!r} 的密钥环境变量 {env_name} 未设置"
            )
        return value

    def config_snapshot(self) -> dict[str, Any]:
        """返回可写入 run.json 的配置快照（5.6.2、8.3）。

        密钥只留环境变量名：模型本身不存密钥值，快照对未显式配置
        api_key_env 的 provider 补上默认环境变量名，便于精确复现（P7）。
        """
        snapshot = self.model_dump(mode="json")
        for name, provider in snapshot.get("providers", {}).items():
            if provider.get("api_key_env") is None:
                provider["api_key_env"] = _default_api_key_env(name)
        return snapshot


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """递归合并：override 中的 dict 逐键覆盖，其余类型整体替换。"""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml(path: str | Path) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: 顶层必须是映射，实际为 {type(data).__name__}")
    return data


def load_config(
    path: str | Path | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> NepaConfig:
    """按 8.3 加载链构造配置：默认值 ← YAML 文件 ← 覆盖 dict。

    默认值层由各模型字段默认值承担；path/overrides 均可省略。
    """
    data: dict[str, Any] = {}
    if path is not None:
        data = _deep_merge(data, _load_yaml(path))
    if overrides:
        data = _deep_merge(data, overrides)
    return NepaConfig.model_validate(data)


def load_scope(path: str | Path) -> ScopeConfig:
    """加载 scope 配置文件（8.3 末尾；doc-run 必需）。"""
    return ScopeConfig.model_validate(_load_yaml(path))
