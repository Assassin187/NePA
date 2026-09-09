"""Production assembly for the M1 stage controllers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from .agents.base import AgentInvoker
from .config import ResolvedConfig
from .llm.client import LLMClient
from .llm.providers import AnthropicProvider, OpenAICompatibleProvider
from .orchestrator import Orchestrator
from .run_store import RunStore
from .stages.s4_planning import S4Controller
from .stages.s5_materialization import S5MaterializationController
from .stages.s6_execution import S6ExecutionController


def build_provider_map(config: ResolvedConfig) -> dict[str, Any]:
    providers: dict[str, Any] = {}
    for name, provider_config in config.providers.items():
        if provider_config.kind == "anthropic":
            providers[name] = AnthropicProvider(name, provider_config)
        elif provider_config.kind == "openai_compat":
            providers[name] = OpenAICompatibleProvider(name, provider_config)
    return providers


def build_orchestrator(
    config: ResolvedConfig,
    store: RunStore,
    *,
    providers: Mapping[str, Any] | None = None,
    agent: AgentInvoker | None = None,
    executor: Any | None = None,
    fault_hook: Any | None = None,
    lease_authorization_provider: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None,
    revision_patch_provider: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None,
) -> Orchestrator:
    orchestrator = Orchestrator(fault_hook=fault_hook)
    invoker = agent
    if invoker is None:
        client = LLMClient(
            config,
            dict(providers) if providers is not None else build_provider_map(config),
            orchestrator=orchestrator,
            store=store,
        )
        invoker = AgentInvoker(config, client)
    orchestrator.register_s4(S4Controller(invoker))
    orchestrator.register_controller("s5", S5MaterializationController(executor))
    orchestrator.register_controller("s6", S6ExecutionController(invoker, executor, fault_hook=fault_hook, lease_authorization_provider=lease_authorization_provider, revision_patch_provider=revision_patch_provider))
    return orchestrator


def run_store_path(run_root: str | Path, run_id: str) -> RunStore:
    root = Path(run_root).resolve() / run_id
    return RunStore(root)


__all__ = ["build_orchestrator", "build_provider_map", "run_store_path"]
