"""Protocol-neutral Agent roles and one-call invocation boundary."""

from .base import (
    AGENT_SYSTEM_INSTRUCTION,
    AgentAvailabilityError,
    AgentConfigurationError,
    AgentContractError,
    AgentError,
    AgentInvoker,
    AgentRequestError,
    AgentResult,
    AgentRoleError,
    InvocationContract,
    PromptRenderer,
    RenderedPrompt,
    ResolvedRoute,
    RoleDefinition,
    render_prompt,
    resolve_route,
)
from .roles import ROLE_REGISTRY, get_role, registered_roles

__all__ = [
    "AGENT_SYSTEM_INSTRUCTION",
    "AgentAvailabilityError",
    "AgentConfigurationError",
    "AgentContractError",
    "AgentError",
    "AgentInvoker",
    "AgentRequestError",
    "AgentResult",
    "AgentRoleError",
    "InvocationContract",
    "PromptRenderer",
    "RenderedPrompt",
    "ResolvedRoute",
    "ROLE_REGISTRY",
    "RoleDefinition",
    "get_role",
    "registered_roles",
    "render_prompt",
    "resolve_route",
]
