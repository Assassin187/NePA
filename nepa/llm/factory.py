"""从已解析配置构造带缓存与 trace 的 LLM 客户端。"""

from __future__ import annotations

from pathlib import Path

from nepa.agents.roles import ResolvedRole
from nepa.config import NepaConfig
from nepa.llm.cache import ResponseCache
from nepa.llm.client import LLMClient, Provider
from nepa.llm.providers.anthropic import AnthropicProvider
from nepa.llm.providers.openai_compat import OpenAICompatProvider
from nepa.llm.telemetry import ModelPricing, TraceWriter


class LLMFactory:
    """按角色解析结果创建 provider；同 provider/model 在单次 run 内复用。"""

    def __init__(
        self,
        config: NepaConfig,
        run_dir: str | Path,
        run_id: str,
    ) -> None:
        self.config = config
        root = Path(run_dir)
        pricing = {
            model: ModelPricing(entry.input, entry.output)
            for model, entry in config.pricing.items()
        }
        self.cache = ResponseCache(root / "cache")
        self.trace = TraceWriter(root / "trace", run_id, pricing)
        self._clients: dict[tuple[str, str], LLMClient] = {}
        self._providers: list[object] = []

    def client_for(self, role: ResolvedRole) -> LLMClient:
        key = (role.provider, role.model)
        if key in self._clients:
            return self._clients[key]
        provider_cfg = self.config.providers[role.provider]
        api_key = self.config.resolve_api_key(role.provider)
        provider: Provider
        if provider_cfg.kind == "openai_compat":
            provider = OpenAICompatProvider(
                base_url=provider_cfg.base_url,
                api_key=api_key,
                model=role.model,
                name=role.provider,
            )
        else:
            provider = AnthropicProvider(
                base_url=provider_cfg.base_url,
                api_key=api_key,
                model=role.model,
                name=role.provider,
            )
        self._providers.append(provider)
        client = LLMClient(
            provider,
            provider_name=role.provider,
            model=role.model,
            cache=self.cache,
            trace=self.trace,
        )
        self._clients[key] = client
        return client

    def close(self) -> None:
        for provider in self._providers:
            close = getattr(provider, "close", None)
            if callable(close):
                close()
