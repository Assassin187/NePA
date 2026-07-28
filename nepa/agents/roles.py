"""角色注册与静态模型路由（设计文档 4.5、4.6、8.3）。"""

from __future__ import annotations

from dataclasses import dataclass

from nepa.config import NepaConfig


class UnknownRoleError(KeyError):
    """配置中不存在请求的 Agent 角色。"""


@dataclass(frozen=True)
class ResolvedRole:
    name: str
    tier: str
    provider: str
    model: str
    temperature: float
    max_tokens: int
    escalate_to: str | None


class RoleRegistry:
    """LLM 禁止自选模型；角色→档位→provider/model 由配置静态解析。"""

    def __init__(self, config: NepaConfig) -> None:
        self.config = config

    def resolve(self, role_name: str, *, tier_override: str | None = None) -> ResolvedRole:
        role = self.config.roles.get(role_name)
        if role is None:
            raise UnknownRoleError(role_name)
        tier_name = tier_override or role.tier
        tier = self.config.tiers[tier_name]
        return ResolvedRole(
            name=role_name,
            tier=tier_name,
            provider=role.provider or tier.provider,
            model=role.model or tier.model,
            temperature=tier.temperature,
            max_tokens=tier.max_tokens,
            escalate_to=role.escalate_to,
        )
