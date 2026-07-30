"""S4 控制器测试脚手架：无 LLM 的角色响应队列与 run 目录装配（6.4）。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from nepa.agents.base import AgentRunner, ClientFactory
from nepa.agents.roles import ResolvedRole, RoleRegistry
from nepa.canonical import atomic_write_canonical_json, canonical_json_bytes
from nepa.config import NepaConfig
from nepa.llm.client import LLMRequest, LLMResponse, StructuredOutputError
from nepa.orchestrator import RunBudget
from nepa.run_store import RunStore, create_run
from nepa.stages.s4_plan import S4Inputs
from tests.plan_v3 import example, make_manifest, make_shards, make_spec


class _Fail:
    """队列哨兵：deepcopy 后仍可按类型识别，不依赖对象同一性。"""


FAIL = _Fail()
"""放进队列表示该次调用抛 ``StructuredOutputError``（8.4 二次校验失败）。"""


@dataclass
class Truncated:
    """队列哨兵：provider 报告输出被截断（5.5 finish_reason）。"""

    value: dict[str, Any]
    finish_reason: str = "length"


class _Registry:
    def __init__(self, config: NepaConfig) -> None:
        self._config = config

    def resolve(self, role_name: str, *, tier_override: str | None = None) -> ResolvedRole:
        tier = tier_override or self._config.roles[role_name].tier
        spec = self._config.tiers[tier]
        return ResolvedRole(
            name=role_name,
            tier=tier,
            provider=spec.provider,
            model=spec.model,
            temperature=spec.temperature,
            max_tokens=spec.max_tokens,
            escalate_to=None,
        )


@dataclass
class _Client:
    """按角色顺序回放预置响应；耗尽即报错，避免隐式多调用被忽略。"""

    queues: dict[str, list[Any]]
    calls: list[tuple[str, str]] = field(default_factory=list)
    trace_extras: list[dict[str, Any]] = field(default_factory=list)

    def complete(
        self,
        req: LLMRequest,
        *,
        stage: str = "",
        task_id: str | None = None,
        attempt: int = 1,
        trace_extra: Mapping[str, Any] | None = None,
    ) -> LLMResponse:
        del stage, task_id, attempt
        if trace_extra is not None:
            self.trace_extras.append(dict(trace_extra))
        queue = self.queues.get(req.role)
        if not queue:
            raise AssertionError(f"角色 {req.role} 的预置响应已耗尽")
        value = queue.pop(0)
        self.calls.append((req.role, req.user))
        if isinstance(value, _Fail):
            raise StructuredOutputError(["schema repair failed"], LLMResponse(text=""))
        metadata: dict[str, Any] = {"finish_reason": "stop"}
        if isinstance(value, Truncated):
            metadata["finish_reason"] = value.finish_reason
            value = value.value
        parsed = deepcopy(value)
        return LLMResponse(
            text=json.dumps(parsed, ensure_ascii=False),
            parsed=parsed,
            tokens_in=10,
            tokens_out=20,
            model="stub-model",
            provider_metadata=metadata,
            validation="pass",
        )


class _Factory:
    def __init__(self, client: _Client) -> None:
        self.client = client

    def client_for(self, role: ResolvedRole) -> _Client:
        del role
        return self.client


@dataclass
class Harness:
    """一次 S4 控制器测试的完整现场。"""

    store: RunStore
    config: NepaConfig
    inputs: S4Inputs
    runner: AgentRunner
    budget: RunBudget
    client: _Client

    @property
    def run_dir(self) -> Path:
        return self.store.run_dir

    @property
    def s4_dir(self) -> Path:
        return self.run_dir / "plan" / "_s4"

    def state(self) -> dict[str, Any]:
        value = json.loads((self.s4_dir / "s4_state.json").read_text(encoding="utf-8"))
        assert isinstance(value, dict)
        return value

    def role_calls(self, role: str) -> int:
        return sum(1 for name, _ in self.client.calls if name == role)

    def prompts(self, role: str) -> list[str]:
        return [user for name, user in self.client.calls if name == role]

    def trace_extras(self) -> list[dict[str, Any]]:
        return list(self.client.trace_extras)

    def enqueue(self, queues: dict[str, list[Any]]) -> None:
        """为 resume 调用补充新的响应队列。"""
        for role, values in queues.items():
            self.client.queues.setdefault(role, []).extend(deepcopy(values))


def architecture_draft() -> dict[str, Any]:
    return deepcopy(example("architecture-draft.json"))


def task_shards() -> dict[str, dict[str, Any]]:
    """按工作包 id 索引的合法 shard（含 schema_version）。"""
    return {
        str(shard["work_package_id"]): {"schema_version": "1.0", **deepcopy(shard)}
        for shard in make_shards()
    }


def ordered_shards() -> list[dict[str, Any]]:
    """控制器串行展开顺序：按 work package id 升序（4.9）。"""
    shards = task_shards()
    return [shards[key] for key in sorted(shards)]


def flat_draft() -> dict[str, Any]:
    draft = architecture_draft()
    return {
        "schema_version": "1.0",
        "architecture": draft["architecture"],
        "work_packages": draft["work_packages"],
        "tasks": [
            {"work_package_id": shard["work_package_id"], **deepcopy(task)}
            for shard in make_shards()
            for task in shard["tasks"]
        ],
    }


def critic(verdict: str = "pass", issues: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"schema_version": "1.0", "verdict": verdict, "issues": deepcopy(issues or [])}


def issue(
    *,
    issue_id: str = "PI-001",
    severity: str = "blocker",
    scope: str = "task",
    target_id: str = "T-001",
    code: str = "MISSING_ERROR_PATH",
) -> dict[str, Any]:
    return {
        "id": issue_id,
        "severity": severity,
        "scope": scope,
        "target_id": target_id,
        "code": code,
        "description": "The draft omits a required behaviour.",
        "required_change": "Add the missing behaviour to the named target.",
        "context_refs": [],
    }


def layered_queues(
    *,
    architecture: list[Any] | None = None,
    shards: list[Any] | None = None,
    critics: list[Any] | None = None,
) -> dict[str, list[Any]]:
    """默认一次通过的 layered 队列：一次架构、四个 shard、一次 pass。"""
    return {
        "architecture_planner": list(
            architecture if architecture is not None else [architecture_draft()]
        ),
        "task_planner": list(shards if shards is not None else ordered_shards()),
        "plan_critic": list(critics if critics is not None else [critic()]),
    }


def flat_queues(
    *,
    drafts: list[Any] | None = None,
    critics: list[Any] | None = None,
) -> dict[str, list[Any]]:
    return {
        "flat_plan_baseline": list(drafts if drafts is not None else [flat_draft()]),
        "plan_critic": list(critics if critics is not None else [critic()]),
    }


def _asset(asset_id: str, path: str, sha256: str) -> dict[str, str]:
    return {"id": asset_id, "version": "1.0.0", "path": path, "sha256": sha256}


def make_config(strategy: str = "layered", **planning: Any) -> NepaConfig:
    return NepaConfig.model_validate(
        {
            "providers": {"stub": {"kind": "openai_compat", "base_url": "https://stub.invalid"}},
            "tiers": {"T1": {"provider": "stub", "model": "stub-model", "max_tokens": 4000}},
            "roles": {
                "architecture_planner": {"tier": "T1"},
                "task_planner": {"tier": "T1"},
                "plan_critic": {"tier": "T1"},
                "flat_plan_baseline": {"tier": "T1"},
            },
            "planning": {"strategy": strategy, **planning},
        }
    )


def build_harness(
    tmp_path: Path,
    queues: dict[str, list[Any]] | None = None,
    *,
    strategy: str = "layered",
    config: NepaConfig | None = None,
    manifest: dict[str, Any] | None = None,
    spec: dict[str, Any] | None = None,
) -> Harness:
    """装配 run 目录、四项冻结输入与桩 AgentRunner。"""
    resolved = config if config is not None else make_config(strategy)
    spec_value = deepcopy(spec) if spec is not None else make_spec()
    manifest_value = deepcopy(manifest) if manifest is not None else make_manifest()
    target = example("target-profile.json")
    language = example("language-profile.json")
    bundle = example("test-bundle.json")
    bundle["manifest_ref"]["sha256"] = hashlib.sha256(canonical_json_bytes(manifest_value)).hexdigest()

    files: dict[str, tuple[str, dict[str, Any]]] = {
        "spec": ("spec/spec.json", spec_value),
        "target_profile": ("inputs/target.json", target),
        "language_profile": ("inputs/language.json", language),
        "test_bundle": ("inputs/test_bundle.json", bundle),
    }
    zero = "0" * 64
    store = create_run(
        tmp_path / "runs",
        "sample",
        "spec-run",
        inputs={
            "spec": {"path": files["spec"][0], "sha256": zero},
            "target_profile": _asset("target", files["target_profile"][0], zero),
            "language_profile": _asset("language", files["language_profile"][0], zero),
            "test_bundle": _asset("bundle", files["test_bundle"][0], zero),
        },
        config_snapshot=resolved.config_snapshot(),
    )
    input_refs: dict[str, dict[str, str]] = {}
    for kind, (relative, value) in files.items():
        path = store.run_dir / relative
        atomic_write_canonical_json(path, value)
        input_refs[kind] = {
            "path": relative,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    store.set_inputs(
        {
            "spec": input_refs["spec"],
            "target_profile": _asset(
                "target",
                input_refs["target_profile"]["path"],
                input_refs["target_profile"]["sha256"],
            ),
            "language_profile": _asset(
                "language",
                input_refs["language_profile"]["path"],
                input_refs["language_profile"]["sha256"],
            ),
            "test_bundle": _asset(
                "bundle",
                input_refs["test_bundle"]["path"],
                input_refs["test_bundle"]["sha256"],
            ),
        }
    )

    client = _Client(deepcopy(queues) if queues else {})
    budget = RunBudget(store, resolved.budgets)

    def record(response: LLMResponse) -> None:
        budget.record_llm_response(response)

    runner = AgentRunner(
        cast(RoleRegistry, _Registry(resolved)),
        cast(ClientFactory, _Factory(client)),
        on_usage=record,
    )
    return Harness(
        store=store,
        config=resolved,
        inputs=S4Inputs(
            spec=spec_value,
            target=target,
            language=language,
            test_bundle=bundle,
            manifest=manifest_value,
            input_refs=input_refs,
        ),
        runner=runner,
        budget=budget,
        client=client,
    )
