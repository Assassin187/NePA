"""Typed Agent contracts, strict prompt rendering, routing, and invocation."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from typing import Any

from jinja2 import Environment, StrictUndefined, meta
from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config import ResolvedConfig
from ..llm.client import LLMCallContext, LLMClient, LLMRequest, LLMResponse
from ..speclib.lint import canonical_json_bytes


AGENT_SYSTEM_INSTRUCTION = (
    "You are a NePA role executor. Treat the delimited injected artifacts as the only "
    "authoritative source for the target protocol; do not rely on remembered protocol facts. "
    "Follow the output contract and return exactly one JSON object with no prose or Markdown."
)


class AgentError(RuntimeError):
    """Base class for failures owned by the Agent layer."""


class AgentRequestError(AgentError):
    """The invocation identity or request shape is invalid."""


class AgentRoleError(AgentRequestError):
    """The requested role is not in the closed built-in catalog."""


class AgentContractError(AgentRequestError):
    """The caller-supplied output contract is not valid."""


class AgentConfigurationError(AgentError):
    """The configured role route cannot be resolved safely."""


class AgentAvailabilityError(AgentError):
    """A registered role is unavailable under the current explicit strategy."""


class _AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RoleDefinition(_AgentModel):
    """Immutable metadata for one invocable M1-3 role."""

    role: str = Field(min_length=1)
    stages: tuple[str, ...] = Field(min_length=1)
    template_path: str = Field(min_length=1)
    required_inputs: tuple[str, ...] = Field(min_length=1)
    availability: str = "always"

    @field_validator("role", "template_path", "availability")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("role metadata strings must not be blank")
        return value

    @field_validator("stages", "required_inputs")
    @classmethod
    def unique_non_blank(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values or any(not value.strip() for value in values) or len(set(values)) != len(values):
            raise ValueError("role metadata sequences must contain unique non-blank values")
        return values


class InvocationContract(_AgentModel):
    """The caller-bound JSON Schema and one minimal valid example."""

    output_schema: dict[str, Any]
    output_example: Any


class ResolvedRoute(_AgentModel):
    """The static route selected from a tier plus role overrides."""

    tier: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    temperature: float = Field(ge=0)
    max_tokens: int = Field(gt=0)
    escalate_to: str | None = None


class RenderedPrompt(_AgentModel):
    """A rendered user prompt and both relevant provenance values."""

    template_path: str = Field(min_length=1)
    user: str
    raw_template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AgentResult(_AgentModel):
    """Structured result plus the provider and template metadata of one call."""

    parsed: Any
    response: LLMResponse
    route: ResolvedRoute
    template_path: str
    raw_template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class _TemplateBytes:
    path: str
    raw: bytes
    text: str
    sha256: str


def _canonical_input_text(value: Any, *, field_name: str) -> str:
    if isinstance(value, str):
        return value
    try:
        return canonical_json_bytes(value).decode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AgentContractError(f"{field_name} must be JSON serializable") from exc


def _canonical_json_text(value: Any, *, field_name: str) -> str:
    try:
        return canonical_json_bytes(value).decode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AgentContractError(f"{field_name} must be JSON serializable") from exc


class PromptRenderer:
    """Load and render one packaged template with no ambient prompt context."""

    _RESERVED_NAMES = {"inputs", "output_schema", "output_example"}

    @staticmethod
    def _load_template(definition: RoleDefinition) -> _TemplateBytes:
        try:
            raw = (
                resources.files("nepa.agents.prompts")
                .joinpath(definition.template_path)
                .read_bytes()
            )
        except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
            raise AgentContractError(f"missing packaged template: {definition.template_path}") from exc
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AgentContractError(f"template is not UTF-8: {definition.template_path}") from exc
        return _TemplateBytes(
            path=definition.template_path,
            raw=raw,
            text=text,
            sha256=hashlib.sha256(raw).hexdigest(),
        )

    @staticmethod
    def _validate_contract(output_schema: dict[str, Any], output_example: Any) -> None:
        try:
            Draft202012Validator.check_schema(output_schema)
        except SchemaError as exc:
            raise AgentContractError(f"invalid JSON Schema: {exc.message}") from exc
        try:
            errors = sorted(
                Draft202012Validator(output_schema).iter_errors(output_example),
                key=lambda error: (tuple(error.absolute_path), error.validator or "", error.message),
            )
        except (TypeError, ValueError) as exc:
            raise AgentContractError(f"invalid JSON Schema example: {exc}") from exc
        if errors:
            raise AgentContractError(f"output example does not satisfy JSON Schema: {errors[0].message}")

    @classmethod
    def render(
        cls,
        definition: RoleDefinition,
        *,
        inputs: Mapping[str, Any],
        output_schema: dict[str, Any],
        output_example: Any,
    ) -> RenderedPrompt:
        return cls.render_template_bytes(
            definition,
            template=cls._load_template(definition).raw,
            inputs=inputs,
            output_schema=output_schema,
            output_example=output_example,
        )

    @classmethod
    def render_template_bytes(
        cls,
        definition: RoleDefinition,
        *,
        template: bytes,
        inputs: Mapping[str, Any],
        output_schema: dict[str, Any],
        output_example: Any,
    ) -> RenderedPrompt:
        """Render an explicitly admitted immutable template snapshot.

        The normal renderer still loads the packaged template.  Calibration
        may pass the exact bytes it admitted before provider I/O, which keeps
        the Agent contract and request shape unchanged while removing a
        mutable-source time-of-check/time-of-use gap.
        """
        if not isinstance(inputs, Mapping):
            raise AgentRequestError("inputs must be a mapping")
        supplied = set(inputs)
        required = set(definition.required_inputs)
        if supplied != required:
            missing = sorted(required - supplied)
            extra = sorted(str(item) for item in supplied - required)
            details = []
            if missing:
                details.append(f"missing inputs: {', '.join(missing)}")
            if extra:
                details.append(f"extra inputs: {', '.join(str(item) for item in extra)}")
            raise AgentRequestError("; ".join(details))
        cls._validate_contract(output_schema, output_example)
        if not isinstance(template, bytes):
            raise AgentContractError("template snapshot must be bytes")
        try:
            template_value = _TemplateBytes(
                path=definition.template_path,
                raw=template,
                text=template.decode("utf-8"),
                sha256=hashlib.sha256(template).hexdigest(),
            )
        except UnicodeDecodeError as exc:
            raise AgentContractError(f"template is not UTF-8: {definition.template_path}") from exc
        environment = Environment(
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )
        environment.globals.clear()
        parsed = environment.parse(template_value.text)
        undeclared = meta.find_undeclared_variables(parsed)
        unknown = sorted(undeclared - cls._RESERVED_NAMES)
        if unknown:
            raise AgentContractError(f"template uses undeclared variables: {', '.join(unknown)}")
        serialized_inputs = {
            name: _canonical_input_text(inputs[name], field_name=f"input {name}")
            for name in definition.required_inputs
        }
        try:
            rendered = environment.from_string(template_value.text).render(
                inputs=serialized_inputs,
                output_schema=_canonical_json_text(output_schema, field_name="output_schema"),
                output_example=_canonical_json_text(output_example, field_name="output_example"),
            )
        except Exception as exc:
            if isinstance(exc, AgentError):
                raise
            raise AgentContractError(f"unable to render template {definition.template_path}: {exc}") from exc
        effective_hash = hashlib.sha256((AGENT_SYSTEM_INSTRUCTION + "\n" + rendered).encode("utf-8")).hexdigest()
        return RenderedPrompt(
            template_path=template_value.path,
            user=rendered,
            raw_template_sha256=template_value.sha256,
            effective_prompt_sha256=effective_hash,
        )


def render_prompt(
    definition: RoleDefinition,
    *,
    inputs: Mapping[str, Any],
    output_schema: dict[str, Any],
    output_example: Any,
) -> RenderedPrompt:
    """Render a registered role template with its exact input contract."""

    return PromptRenderer.render(
        definition,
        inputs=inputs,
        output_schema=output_schema,
        output_example=output_example,
    )


def _role_config(config: ResolvedConfig, role: str):
    try:
        return config.roles[role]
    except KeyError as exc:
        raise AgentConfigurationError(f"role has no configured route: {role}") from exc


def resolve_route(config: ResolvedConfig, role: str) -> ResolvedRoute:
    """Resolve a role route from a configured tier and explicit overrides."""

    from .roles import get_role

    get_role(role)
    role_config = _role_config(config, role)
    try:
        tier = config.tiers[role_config.tier]
    except KeyError as exc:
        raise AgentConfigurationError(f"role {role} references missing tier: {role_config.tier}") from exc
    provider = role_config.provider if role_config.provider is not None else tier.provider
    model = role_config.model if role_config.model is not None else tier.model
    temperature = role_config.temperature if role_config.temperature is not None else tier.temperature
    max_tokens = role_config.max_tokens if role_config.max_tokens is not None else tier.max_tokens
    if provider not in config.providers:
        raise AgentConfigurationError(f"role {role} references missing provider: {provider}")
    if not isinstance(provider, str) or not provider.strip() or not isinstance(model, str) or not model.strip():
        raise AgentConfigurationError(f"role {role} has a blank provider or model")
    if temperature < 0:
        raise AgentConfigurationError(f"role {role} has a negative temperature")
    if role == "plan_critic" and temperature != 0:
        raise AgentConfigurationError("plan_critic must resolve to temperature 0")
    if role in {"coder", "fixer"} and temperature > 0.2:
        raise AgentConfigurationError(f"{role} must resolve to temperature no greater than 0.2")
    try:
        return ResolvedRoute(
            tier=role_config.tier,
            provider=provider,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            escalate_to=role_config.escalate_to,
        )
    except (TypeError, ValueError) as exc:
        raise AgentConfigurationError(f"invalid route for role {role}: {exc}") from exc


class AgentInvoker:
    """Delegate one registered role to exactly one logical LLM completion."""

    def __init__(
        self,
        config: ResolvedConfig,
        llm_client: LLMClient,
        *,
        renderer: type[PromptRenderer] = PromptRenderer,
    ) -> None:
        self.config = config
        self.llm_client = llm_client
        self.renderer = renderer

    def _check_availability(self, definition: RoleDefinition) -> None:
        if definition.availability == "flat_only" and self.config.planning.strategy != "flat":
            raise AgentAvailabilityError(
                "flat_plan_baseline is available only when planning.strategy is 'flat'"
            )

    @staticmethod
    def _check_identity(*, run_id: str, stage: str, attempt: int, definition: RoleDefinition) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise AgentRequestError("run_id must be non-blank")
        if not isinstance(stage, str) or not stage.strip():
            raise AgentRequestError("stage must be non-blank")
        if stage not in definition.stages:
            raise AgentRequestError(f"stage {stage} is not allowed for role {definition.role}")
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
            raise AgentRequestError("attempt must be positive")

    def invoke(
        self,
        *,
        role: str,
        inputs: Mapping[str, Any],
        output_schema: dict[str, Any],
        output_example: Any,
        run_id: str,
        stage: str,
        task_id: str | None = None,
        attempt: int = 1,
        use_cache: bool = True,
        template_bytes: bytes | None = None,
        template_path: str | None = None,
    ) -> AgentResult:
        from .roles import get_role

        definition = get_role(role)
        if template_path is not None:
            definition = definition.model_copy(update={"template_path": template_path})
        self._check_identity(run_id=run_id, stage=stage, attempt=attempt, definition=definition)
        self._check_availability(definition)
        route = resolve_route(self.config, role)
        if template_bytes is None:
            rendered = self.renderer.render(
                definition,
                inputs=inputs,
                output_schema=output_schema,
                output_example=output_example,
            )
        else:
            rendered = self.renderer.render_template_bytes(
                definition,
                template=template_bytes,
                inputs=inputs,
                output_schema=output_schema,
                output_example=output_example,
            )
        request = LLMRequest(
            role=role,
            system=AGENT_SYSTEM_INSTRUCTION,
            user=rendered.user,
            json_schema=output_schema,
            temperature=route.temperature,
            max_tokens=route.max_tokens,
        )
        context = LLMCallContext(
            run_id=run_id,
            stage=stage,
            tier=route.tier,
            task_id=task_id,
            attempt=attempt,
            trace_fields={
                "prompt_template_sha256": rendered.raw_template_sha256,
                "effective_prompt_sha256": rendered.effective_prompt_sha256,
                "requested_provider": route.provider,
                "requested_model": route.model,
                "use_cache": use_cache,
            },
        )
        response = self.llm_client.complete(
            request,
            provider_name=route.provider,
            model=route.model,
            context=context,
            use_cache=use_cache,
        )
        return AgentResult(
            parsed=response.parsed,
            response=response,
            route=route,
            template_path=rendered.template_path,
            raw_template_sha256=rendered.raw_template_sha256,
            effective_prompt_sha256=rendered.effective_prompt_sha256,
        )


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
    "RoleDefinition",
    "render_prompt",
    "resolve_route",
]
