"""S4 Plan Compiler 控制器（设计 6.4.2～6.4.7）。

控制器只做编排与机械字段：id、哈希、排序、依赖链接与覆盖索引全部由 L2
确定性代码生成，每次 L3 调用仍只完成一个认知任务。所有中间候选写入
``plan/_s4/``，下游不得读取；正式 ``plan/plan.json`` 只在全部硬门通过后
原子发布，并以 ``run.json`` 的 seal receipt 作为逻辑 commit point。
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator

from nepa.agents.base import AgentRunner, TruncatedOutputError
from nepa.agents.contracts import (
    architecture_draft_schema,
    flat_plan_draft_schema,
    plan_critic_schema,
    s4_state_schema,
    task_shard_schema,
)
from nepa.architecture import arch_validate
from nepa.canonical import atomic_write_canonical_json, canonical_json_bytes, canonical_sha256
from nepa.config import NepaConfig
from nepa.delivery import (
    DeliveryBlueprintError,
    DeliveryCompileError,
    build_planning_index,
    compile_delivery_constraints,
)
from nepa.llm.client import StructuredOutputError
from nepa.orchestrator import RunBudget
from nepa.plan_draft import (
    LinkResult,
    PlanDraftError,
    PlanDraftIR,
    link_plan_draft,
    normalize_flat_draft,
    normalize_layered_draft,
)
from nepa.run_store import RunStore
from nepa.speclib.lint import LintIssue, LintReport, plan_full_lint, spec_lint
from nepa.task_shard import (
    ShardIssue,
    build_task_planner_payload,
    validate_task_shard,
)

__all__ = ["S4Error", "S4Inputs", "S4Result", "compile_plan"]

STATE_FILE = "s4_state.json"
_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"
_PROMPT_DIR = Path(__file__).resolve().parent.parent / "agents" / "prompts"
_VALIDATOR_SOURCE = Path(__file__).resolve().parent.parent / "architecture.py"

_STRATEGY_ROLES: dict[str, tuple[str, ...]] = {
    "layered": ("architecture_planner", "task_planner", "plan_critic"),
    "flat": ("flat_plan_baseline", "plan_critic"),
}
_ROLE_SCHEMAS: dict[str, str] = {
    "architecture_planner": "architecture-draft.schema.json",
    "task_planner": "task-shard.schema.json",
    "plan_critic": "plan-critic.schema.json",
    "flat_plan_baseline": "flat-plan-draft.schema.json",
}
# 6.4.3：ArchitecturePlanner 的输入上下文上限；与 M1-4a spike 同形。
_CONTEXT_LIMIT_TOKENS = 64000

Route = Literal["local", "global", "mechanical"]


class S4Error(RuntimeError):
    """S4 受控失败：保存现场但不发布部分 Plan（6.4.6）。

    ``code`` 是稳定机器码，调用方按 4.7 决定受控退出与 run.json 记账。
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class S4Inputs:
    """四项冻结输入及其 run 内引用（6.4.3 步骤 1）。

    ``repo_root`` 存在时 PREPARE 会联合复核 Test Bundle 的 manifest 与
    bundle tree 双摘要及各组件引用；缺省只复核 manifest canonical 哈希。
    """

    spec: dict[str, Any]
    target: dict[str, Any]
    language: dict[str, Any]
    test_bundle: dict[str, Any]
    manifest: dict[str, Any]
    input_refs: dict[str, dict[str, str]]
    repo_root: Path | None = None


@dataclass(frozen=True, slots=True)
class S4Result:
    """S4 发布结果；``published`` 为 False 表示 done 后的只读 no-op。"""

    plan: dict[str, Any]
    plan_path: Path
    blueprint: dict[str, Any]
    state: dict[str, Any]
    published: bool


@dataclass(slots=True)
class _Issue:
    """统一的语义问题：来自 shard 门、Linker、full lint 或 PlanCritic。"""

    severity: str
    scope: str
    target_id: str
    code: str
    description: str
    route: Route
    work_package_id: str | None = None

    @property
    def signature(self) -> str:
        return f"{self.severity}|{self.scope}|{self.target_id}|{self.code}"

    def as_dict(self) -> dict[str, Any]:
        value = {
            "severity": self.severity,
            "scope": self.scope,
            "target_id": self.target_id,
            "code": self.code,
            "description": self.description,
            "route": self.route,
        }
        if self.work_package_id is not None:
            value["work_package_id"] = self.work_package_id
        return value


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _lint_code(code: str) -> str:
    return code.replace("-", "_")


def _prompt_sha256(role: str) -> str:
    return _sha256_file(_PROMPT_DIR / f"{role}.md")


@dataclass(slots=True)
class _Checkpoints:
    """`_s4` 工件的父哈希绑定读写（5.6.6）。

    只有 Schema 校验通过的工件才在 ``s4_state.json`` 标记 valid；父哈希不同
    的既有工件一律视为失效，不参与 resume 复用。
    """

    root: Path
    state: dict[str, Any]

    def persist(self) -> None:
        errors = sorted(
            Draft202012Validator(s4_state_schema()).iter_errors(self.state),
            key=lambda item: [str(part) for part in item.absolute_path],
        )
        if errors:
            detail = "; ".join(
                f"{'/'.join(map(str, item.absolute_path)) or '<root>'}: {item.message}"
                for item in errors[:4]
            )
            raise S4Error("PLAN_STATE_ARTIFACT_INVALID", f"s4_state 非法: {detail}")
        atomic_write_canonical_json(self.root / STATE_FILE, self.state)

    def write(
        self,
        relative: str,
        value: dict[str, Any],
        *,
        parent_sha256: str,
        schema_name: str | None = None,
    ) -> str:
        """canonical 发布一个检查点，并登记其 schema_version 与父哈希。"""
        if schema_name is not None:
            errors = sorted(
                Draft202012Validator(_schema(schema_name)).iter_errors(value),
                key=lambda item: [str(part) for part in item.absolute_path],
            )
            if errors:
                raise S4Error(
                    "PLAN_CHECKPOINT_INVALID",
                    f"{relative} 未通过 {schema_name}: {errors[0].message}",
                )
        atomic_write_canonical_json(self.root / relative, value)
        digest = canonical_sha256(value)
        self.state["checkpoints"][relative] = {
            "schema_version": str(value.get("schema_version", "1.0")),
            "sha256": digest,
            "parent_sha256": parent_sha256,
            "valid": True,
        }
        self.persist()
        return digest

    def reusable(self, relative: str, *, parent_sha256: str) -> dict[str, Any] | None:
        """resume 时只复用父哈希匹配且内容未漂移的 valid 工件。"""
        entry = self.state["checkpoints"].get(relative)
        if not isinstance(entry, dict) or not entry.get("valid"):
            return None
        if entry.get("parent_sha256") != parent_sha256:
            return None
        path = self.root / relative
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict) or canonical_sha256(value) != entry.get("sha256"):
            return None
        return value

    def invalidate(self, prefix: str) -> None:
        """父哈希变化后使下游检查点失效；工件留在盘上供审计。"""
        changed = False
        for relative, entry in self.state["checkpoints"].items():
            if relative.startswith(prefix) and entry.get("valid"):
                entry["valid"] = False
                changed = True
        if changed:
            self.persist()

    def set_phase(self, phase: str) -> None:
        self.state["phase"] = phase
        self.persist()

    def fail(self, code: str, detail: str) -> None:
        self.state["status"] = "failed"
        self.state["failure"] = {"code": code, "detail": detail[:500] or code}
        self.persist()


def _schema(name: str) -> dict[str, Any]:
    value = json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{name}: schema root must be object")
    return value


def _planning_fingerprint(config: NepaConfig, strategy: str) -> dict[str, Any]:
    """6.4.3/6.4.8：planning 配置与本策略角色资产的哈希指纹。"""
    roles = _STRATEGY_ROLES[strategy]
    return {
        "config_snapshot_sha256": canonical_sha256(config.config_snapshot()),
        "context_limit_tokens": _CONTEXT_LIMIT_TOKENS,
        "safety_margin_tokens": int(
            _CONTEXT_LIMIT_TOKENS * config.planning.context_safety_margin_ratio
        ),
        "prompt_sha256": {role: _prompt_sha256(role) for role in roles},
        "schema_sha256": {
            _ROLE_SCHEMAS[role].removesuffix(".schema.json").replace("-", "_"): _sha256_file(
                _SCHEMA_DIR / _ROLE_SCHEMAS[role]
            )
            for role in roles
        },
        "validator_sha256": _sha256_file(_VALIDATOR_SOURCE),
    }


def _verify_frozen_inputs(inputs: S4Inputs, store: RunStore) -> None:
    """6.4.3 步骤 1：Run v2 配置快照与四项冻结输入哈希、Bundle 双摘要复核。"""
    meta = store.meta
    if canonical_sha256(meta.config_snapshot) != meta.config_snapshot_sha256:
        raise S4Error("PLAN_INPUTS_INVALID", "run.json 的 config snapshot 与其 SHA-256 不一致")
    declared = {
        "target_profile": meta.inputs.target_profile.model_dump(mode="json"),
        "language_profile": meta.inputs.language_profile.model_dump(mode="json"),
        "test_bundle": meta.inputs.test_bundle.model_dump(mode="json"),
    }
    expected_values: dict[str, dict[str, Any]] = {
        "spec": inputs.spec,
        "target_profile": inputs.target,
        "language_profile": inputs.language,
        "test_bundle": inputs.test_bundle,
    }
    if set(inputs.input_refs) != set(expected_values):
        raise S4Error("PLAN_INPUTS_INVALID", "S4 必须提供恰好四项冻结 input_refs")
    run_dir = store.run_dir
    for kind, value in sorted(inputs.input_refs.items()):
        path = run_dir / str(value["path"])
        if not path.is_file():
            raise S4Error("PLAN_INPUTS_INVALID", f"缺少冻结输入 {kind}: {value['path']}")
        if _sha256_file(path) != value["sha256"]:
            raise S4Error("PLAN_INPUTS_INVALID", f"冻结输入 {kind} 的 SHA-256 与 run 引用不一致")
        if kind != "spec":
            entry = declared.get(kind)
            if not isinstance(entry, dict):
                raise S4Error("PLAN_INPUTS_INVALID", f"run.json 未声明冻结输入 {kind}")
            if entry.get("path") != value["path"] or entry.get("sha256") != value["sha256"]:
                raise S4Error(
                    "PLAN_INPUTS_INVALID",
                    f"冻结输入 {kind} 与 run.json inputs 声明不一致",
                )
        try:
            frozen_value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise S4Error("PLAN_INPUTS_INVALID", f"冻结输入 {kind} 不是合法 JSON: {exc}") from exc
        if not isinstance(frozen_value, dict) or canonical_sha256(frozen_value) != canonical_sha256(
            expected_values[kind]
        ):
            raise S4Error(
                "PLAN_INPUTS_INVALID",
                f"传入的 {kind} 内容与 run 内冻结文件不一致",
            )
    source_ref = meta.inputs.model_dump(mode="json").get("spec")
    if not isinstance(source_ref, dict):
        raise S4Error("PLAN_INPUTS_INVALID", "run.json 未声明调用方 Spec 来源")
    source_path = Path(str(source_ref.get("path", "")))
    source_candidates = [source_path] if source_path.is_absolute() else [
        store.run_dir / source_path,
        *(
            [Path(inputs.repo_root) / source_path]
            if inputs.repo_root is not None
            else []
        ),
    ]
    if not any(
        candidate.is_file() and _sha256_file(candidate) == source_ref.get("sha256")
        for candidate in source_candidates
    ):
        raise S4Error("PLAN_INPUTS_INVALID", "调用方 Spec 来源缺失或 SHA-256 漂移")
    manifest_ref = inputs.test_bundle.get("manifest_ref")
    if not isinstance(manifest_ref, dict):
        raise S4Error("PLAN_INPUTS_INVALID", "Test Bundle 缺少 manifest_ref")
    if manifest_ref.get("schema_version") != inputs.manifest.get("schema_version"):
        raise S4Error("PLAN_INPUTS_INVALID", "Test Manifest schema_version 与 Bundle 引用不一致")
    if inputs.repo_root is None:
        if manifest_ref.get("sha256") != hashlib.sha256(
            canonical_json_bytes(inputs.manifest)
        ).hexdigest():
            raise S4Error("PLAN_INPUTS_INVALID", "Test Manifest 与 Bundle manifest_ref SHA-256 不一致")
        return
    from nepa.assets import AssetValidationError, validate_test_bundle

    try:
        validate_test_bundle(inputs.test_bundle, workspace_root=inputs.repo_root)
        manifest_path = Path(inputs.repo_root) / str(manifest_ref["path"])
        frozen_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(frozen_manifest, dict) or canonical_sha256(frozen_manifest) != canonical_sha256(
            inputs.manifest
        ):
            raise AssetValidationError("传入 Test Manifest 与 Test Bundle 冻结引用不一致")
    except AssetValidationError as exc:
        raise S4Error("PLAN_INPUTS_INVALID", f"Test Bundle 双摘要校验失败: {exc}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise S4Error("PLAN_INPUTS_INVALID", f"无法读取冻结 Test Manifest: {exc}") from exc


def _prepare(
    inputs: S4Inputs,
    *,
    config: NepaConfig,
    store: RunStore,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """PREPARE + DELIVERY_CONSTRAINTS：冻结输入复核、spec lint、约束与索引。"""
    _verify_frozen_inputs(inputs, store)
    report = spec_lint(inputs.spec, tests_manifest=list(inputs.manifest.get("tests", [])))
    if not report.ok:
        raise S4Error(
            "PLAN_SPEC_LINT_FAILED",
            f"spec lint {len(report.errors)} error: {sorted(report.error_codes())}",
        )
    try:
        constraints = compile_delivery_constraints(
            inputs.spec,
            inputs.target,
            inputs.language,
            inputs.test_bundle,
            inputs.manifest,
        )
    except (DeliveryCompileError, KeyError, TypeError) as exc:
        raise S4Error("PLAN_CONSTRAINTS_INVALID", str(exc)) from exc

    reserved = config.tiers[config.roles["architecture_planner"].tier].max_tokens
    margin = int(_CONTEXT_LIMIT_TOKENS * config.planning.context_safety_margin_ratio)
    index = build_planning_index(
        inputs.spec,
        constraints,
        inputs.manifest,
        estimated_input_tokens=0,
        output_tokens_reserved=reserved,
        context_limit=_CONTEXT_LIMIT_TOKENS,
        safety_margin_tokens=margin,
    )
    # 先用零估计得到索引形状，再按其真实序列化规模重算 preflight（6.4.3 步骤 5）。
    estimated = max(
        1,
        len(canonical_json_bytes({"planning_index": index, "delivery_constraints": constraints}))
        // 4,
    )
    index = build_planning_index(
        inputs.spec,
        constraints,
        inputs.manifest,
        estimated_input_tokens=estimated,
        output_tokens_reserved=reserved,
        context_limit=_CONTEXT_LIMIT_TOKENS,
        safety_margin_tokens=margin,
    )
    if not index["preflight"]["fits"]:
        raise S4Error(
            "PLAN_CONTEXT_TOO_LARGE",
            f"规划上下文需要 {index['preflight']['required_tokens']} tokens，"
            f"超过上限 {_CONTEXT_LIMIT_TOKENS}",
        )
    return constraints, index


def _invoke(
    runner: AgentRunner,
    role: str,
    payload: dict[str, Any],
    schema: dict[str, Any],
    *,
    attempt: int,
    checkpoints: _Checkpoints,
    parent_sha256: str,
    work_package_id: str | None = None,
) -> dict[str, Any]:
    """一次结构化 L3 调用；8.4 的一次 Schema 修复由 LLM 层内部完成。

    二次 Schema 失败或输出截断都不允许继续，按 6.4.6 直接受控失败。trace 行
    额外带上 5.5 要求的 S4 证据：编译阶段、可选工作包、直接父工件哈希与本次
    是否已动用局部/全局修复预算。
    """
    repairs = checkpoints.state["repairs"]
    extra: dict[str, Any] = {
        "compiler_phase": checkpoints.state["phase"],
        "work_package_id": work_package_id,
        "parent_artifact_sha256": parent_sha256,
        "repair_budget_used": {
            "architecture": int(repairs["architecture"]),
            "work_package": int(dict(repairs["work_packages"]).get(work_package_id or "", 0)),
            "plan_critic": int(repairs["plan_critic"]),
            "plan_global_replans": int(repairs["plan_global_replans"]),
        },
    }
    try:
        return runner.invoke(
            role,
            payload,
            schema,
            stage="S4",
            attempt=attempt,
            trace_extra=extra,
        )
    except StructuredOutputError as exc:
        raise S4Error(
            "PLAN_STRUCTURED_OUTPUT_FAILED",
            f"{role} 结构化输出二次校验失败: {exc}",
        ) from exc
    except TruncatedOutputError as exc:
        raise S4Error("PLAN_OUTPUT_TRUNCATED", str(exc)) from exc


def _arch_issues(report: Any) -> list[_Issue]:
    return [
        _Issue(
            severity="blocker",
            scope="architecture",
            target_id=item.path or "architecture",
            code=item.code,
            description=item.message,
            route="global",
        )
        for item in report.issues
    ]


@dataclass(slots=True)
class _Semantics:
    """一次语义编译周期的候选：架构草稿与各工作包 shard。"""

    architecture_draft: dict[str, Any]
    architecture_sha256: str
    shards: dict[str, dict[str, Any]] = field(default_factory=dict)
    flat_draft: dict[str, Any] | None = None


def _architect(
    *,
    runner: AgentRunner,
    checkpoints: _Checkpoints,
    inputs: S4Inputs,
    constraints: dict[str, Any],
    index: dict[str, Any],
    budget: RunBudget,
    repair_limit: int,
    previous: dict[str, Any] | None = None,
    issues: list[_Issue] | None = None,
) -> tuple[dict[str, Any], str]:
    """ARCHITECT → ARCH_VALIDATE：失败只允许一次定点架构修复（6.4.4）。"""
    checkpoints.set_phase("REPLAN_ARCHITECTURE" if previous is not None else "ARCHITECT")
    payload: dict[str, Any] = {
        "planning_index": index,
        "delivery_constraints": constraints,
    }
    if previous is not None:
        payload["previous_candidate"] = previous
        payload["semantic_validation_errors"] = [item.as_dict() for item in (issues or [])]
        payload["instruction"] = "Repair only the listed semantic validation failures."
    parent = canonical_sha256(payload)
    reused = checkpoints.reusable("architecture_candidate.json", parent_sha256=parent)
    schema = architecture_draft_schema()
    attempts = int(checkpoints.state["attempts"]["architecture"])
    if reused is not None:
        draft = reused
    else:
        budget.checkpoint()
        attempts += 1
        checkpoints.state["attempts"]["architecture"] = attempts
        draft = _invoke(
            runner,
            "architecture_planner",
            payload,
            schema,
            attempt=attempts,
            checkpoints=checkpoints,
            parent_sha256=parent,
        )

    checkpoints.set_phase("ARCH_VALIDATE")
    report = arch_validate(
        draft,
        spec=inputs.spec,
        target=inputs.target,
        constraints=constraints,
        planning_index=index,
    )
    if report.ok:
        digest = checkpoints.write(
            "architecture_candidate.json",
            draft,
            parent_sha256=parent,
            schema_name="architecture-draft.schema.json",
        )
        return draft, digest

    used = int(checkpoints.state["repairs"]["architecture"])
    if used >= repair_limit:
        raise S4Error(
            "PLAN_ARCH_VALIDATE_FAILED",
            f"ARCH_VALIDATE 修复预算耗尽: {sorted({item.code for item in report.issues})}",
        )
    checkpoints.state["repairs"]["architecture"] = used + 1
    checkpoints.persist()
    return _architect(
        runner=runner,
        checkpoints=checkpoints,
        inputs=inputs,
        constraints=constraints,
        index=index,
        budget=budget,
        repair_limit=repair_limit,
        previous=draft,
        issues=_arch_issues(report),
    )


def _expand_work_package(
    *,
    runner: AgentRunner,
    checkpoints: _Checkpoints,
    inputs: S4Inputs,
    constraints: dict[str, Any],
    config: NepaConfig,
    budget: RunBudget,
    package: dict[str, Any],
    architecture: dict[str, Any],
    parent_sha256: str,
    repair_issues: list[_Issue] | None = None,
) -> dict[str, Any]:
    """展开一个工作包并跑 S4-G3；局部语义问题按预算重做一次（6.4.4）。"""
    work_package_id = str(package["id"])
    relative = f"task_shards/{work_package_id}.json"
    base_payload = build_task_planner_payload(
        package,
        architecture=architecture,
        spec=inputs.spec,
        manifest=inputs.manifest,
        constraints=constraints,
        max_task_files=config.planning.max_task_files,
    )
    payload = deepcopy(base_payload)
    if repair_issues:
        payload["semantic_validation_errors"] = [item.as_dict() for item in repair_issues]
        payload["instruction"] = "Repair only the listed shard validation failures."
    parent = canonical_sha256({"architecture": parent_sha256, "payload": payload})
    reused = checkpoints.reusable(relative, parent_sha256=parent)
    attempts = dict(checkpoints.state["attempts"]["work_packages"])
    if reused is not None:
        shard = reused
    else:
        budget.checkpoint()
        attempts[work_package_id] = int(attempts.get(work_package_id, 0)) + 1
        checkpoints.state["attempts"]["work_packages"] = attempts
        shard = _invoke(
            runner,
            "task_planner",
            payload,
            task_shard_schema(),
            attempt=attempts[work_package_id],
            checkpoints=checkpoints,
            parent_sha256=parent,
            work_package_id=work_package_id,
        )

    shard = _strip_redundant_cross_package_dependencies(
        shard,
        architecture=architecture,
    )
    checkpoints.set_phase("SHARD_VALIDATE")
    issues = validate_task_shard(
        shard,
        package=package,
        constraints=constraints,
        max_task_files=config.planning.max_task_files,
    )
    if not issues:
        checkpoints.write(
            relative,
            shard,
            parent_sha256=parent,
            schema_name="task-shard.schema.json",
        )
        return shard

    repairs = dict(checkpoints.state["repairs"]["work_packages"])
    used = int(repairs.get(work_package_id, 0))
    if repair_issues is not None or used >= config.budgets.plan_task_shard_repairs:
        raise S4Error(
            "PLAN_SHARD_BUDGET_EXHAUSTED",
            f"{work_package_id} 的 shard 预算耗尽: {sorted({item.code for item in issues})}",
        )
    repairs[work_package_id] = used + 1
    checkpoints.state["repairs"]["work_packages"] = repairs
    checkpoints.persist()
    checkpoints.set_phase("REEXPAND_WORK_PACKAGE")
    return _expand_work_package(
        runner=runner,
        checkpoints=checkpoints,
        inputs=inputs,
        constraints=constraints,
        config=config,
        budget=budget,
        package=package,
        architecture=architecture,
        parent_sha256=parent_sha256,
        repair_issues=_shard_issues(work_package_id, issues),
    )


def _strip_redundant_cross_package_dependencies(
    shard: dict[str, Any],
    *,
    architecture: dict[str, Any],
) -> dict[str, Any]:
    """Mechanically remove package ids from shard-local dependency lists.

    Cross-package ordering is already compiled from the architecture/package
    contract graph. A dependency that exactly names another work package is
    therefore redundant, while every other unknown id remains visible to G3.
    """
    normalized = deepcopy(shard)
    tasks = normalized.get("tasks")
    if not isinstance(tasks, list):
        return normalized
    local_ids = {
        str(task.get("local_id"))
        for task in tasks
        if isinstance(task, dict) and isinstance(task.get("local_id"), str)
    }
    package_ids = {
        str(package.get("id"))
        for package in architecture.get("work_packages", [])
        if isinstance(package, dict) and isinstance(package.get("id"), str)
    }
    redundant = package_ids - local_ids
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("depends_on"), list):
            continue
        task["depends_on"] = [
            dependency
            for dependency in task["depends_on"]
            if str(dependency) not in redundant
        ]
    return normalized


def _shard_issues(work_package_id: str, issues: list[ShardIssue]) -> list[_Issue]:
    return [
        _Issue(
            severity="blocker",
            scope="work_package",
            target_id=work_package_id,
            code=item.code,
            description=f"{item.path}: {item.message}",
            route="local",
            work_package_id=work_package_id,
        )
        for item in issues
    ]


def _flat_draft(
    *,
    runner: AgentRunner,
    checkpoints: _Checkpoints,
    constraints: dict[str, Any],
    index: dict[str, Any],
    budget: RunBudget,
    issues: list[_Issue] | None = None,
) -> tuple[dict[str, Any], str]:
    """FLAT_DRAFT（A9）：一次调用产出完整语义草稿，不复用分层修复配额。"""
    checkpoints.set_phase("REPLAN_FLAT_DRAFT" if issues else "FLAT_DRAFT")
    payload: dict[str, Any] = {
        "planning_index": index,
        "delivery_constraints": constraints,
    }
    if issues:
        payload["semantic_validation_errors"] = [item.as_dict() for item in issues]
        payload["instruction"] = "Produce a complete replacement draft that resolves every issue."
    parent = canonical_sha256(payload)
    reused = checkpoints.reusable("flat_draft.json", parent_sha256=parent)
    attempts = int(checkpoints.state["attempts"]["architecture"])
    if reused is not None:
        draft = reused
    else:
        budget.checkpoint()
        attempts += 1
        checkpoints.state["attempts"]["architecture"] = attempts
        draft = _invoke(
            runner,
            "flat_plan_baseline",
            payload,
            flat_plan_draft_schema(),
            attempt=attempts,
            checkpoints=checkpoints,
            parent_sha256=parent,
        )
    checkpoints.set_phase("FLAT_VALIDATE")
    digest = checkpoints.write(
        "flat_draft.json",
        draft,
        parent_sha256=parent,
        schema_name="flat-plan-draft.schema.json",
    )
    return draft, digest


def _flat_validate(
    draft: dict[str, Any],
    *,
    inputs: S4Inputs,
    constraints: dict[str, Any],
    index: dict[str, Any],
    config: NepaConfig,
) -> list[_Issue]:
    """flat 也必须过与 layered 相同的 S4-G2/S4-G3 生产门，不放宽校验。"""
    projection = {
        "schema_version": "1.0",
        "architecture": draft["architecture"],
        "work_packages": draft["work_packages"],
    }
    report = arch_validate(
        projection,
        spec=inputs.spec,
        target=inputs.target,
        constraints=constraints,
        planning_index=index,
    )
    issues = _arch_issues(report)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for task in draft["tasks"]:
        local = deepcopy(task)
        grouped.setdefault(str(local.pop("work_package_id")), []).append(local)
    for package in draft["work_packages"]:
        work_package_id = str(package["id"])
        shard = {
            "schema_version": "1.0",
            "work_package_id": work_package_id,
            "tasks": grouped.get(work_package_id, []),
        }
        issues.extend(
            _shard_issues(
                work_package_id,
                validate_task_shard(
                    shard,
                    package=package,
                    constraints=constraints,
                    max_task_files=config.planning.max_task_files,
                ),
            )
        )
    return issues


def _link(
    draft: PlanDraftIR,
    *,
    checkpoints: _Checkpoints,
    inputs: S4Inputs,
    constraints: dict[str, Any],
    config_snapshot: dict[str, Any],
    parent_sha256: str,
    review: dict[str, Any] | None = None,
) -> LinkResult:
    """LINK_AND_RESOLVE_BLUEPRINT：6.4.5 九步确定性链接与 blueprint 编译。"""
    checkpoints.set_phase("LINK_AND_RESOLVE_BLUEPRINT")
    try:
        result = link_plan_draft(
            draft,
            spec=inputs.spec,
            manifest=inputs.manifest,
            constraints=constraints,
            input_refs=deepcopy(inputs.input_refs),
            config_snapshot=config_snapshot,
            review=review,
        )
    except (PlanDraftError, DeliveryBlueprintError, DeliveryCompileError, KeyError) as exc:
        raise S4Error("PLAN_LINK_FAILED", str(exc)) from exc
    checkpoints.write("link_report.json", result.link_report, parent_sha256=parent_sha256)
    checkpoints.write(
        "delivery_blueprint.json",
        result.blueprint,
        parent_sha256=parent_sha256,
    )
    checkpoints.write(
        "candidate_plan.json",
        result.plan,
        parent_sha256=parent_sha256,
        schema_name="plan.schema.json",
    )
    return result


def _lint_issues(
    report: LintReport,
    *,
    plan: dict[str, Any],
) -> list[_Issue]:
    """把确定性 lint 错误映射为可路由的语义问题。

    能定位到具体任务的错误路由到其工作包做定点重做，其余按架构级处理。
    """
    package_by_task = {str(task["id"]): str(task["work_package"]) for task in plan["tasks"]}
    issues: list[_Issue] = []
    for item in report.errors:
        work_package_id = _locate_work_package(item, package_by_task)
        issues.append(
            _Issue(
                severity="blocker",
                scope="work_package" if work_package_id else "architecture",
                target_id=work_package_id or item.path or "plan",
                code=_lint_code(item.code),
                description=f"{item.path}: {item.message}",
                route="local" if work_package_id else "global",
                work_package_id=work_package_id,
            )
        )
    return issues


def _locate_work_package(
    issue: LintIssue,
    package_by_task: dict[str, str],
) -> str | None:
    for task_id, work_package_id in package_by_task.items():
        if task_id in issue.path or task_id in issue.message:
            return work_package_id
    return None


def _plan_lint(
    result: LinkResult,
    *,
    inputs: S4Inputs,
    constraints: dict[str, Any],
    index: dict[str, Any],
    config_snapshot: dict[str, Any],
    checkpoints: _Checkpoints,
) -> LintReport:
    """PLAN_LINT_AND_SIMULATE：S4-G0～S4-G6 的唯一发布门（6.4.5）。"""
    checkpoints.set_phase("PLAN_LINT_AND_SIMULATE")
    return plan_full_lint(
        result.plan,
        inputs.spec,
        constraints=constraints,
        blueprint=result.blueprint,
        tests_manifest=list(inputs.manifest["tests"]),
        config_snapshot=config_snapshot,
        expected_input_refs=deepcopy(inputs.input_refs),
        planning_index=index,
    )


def _critic_payload(result: LinkResult, report: LintReport) -> dict[str, Any]:
    """PlanCritic 只看紧凑图、REQ 矩阵与确定性报告（6.4.6、P1）。"""
    plan = result.plan
    return {
        "architecture": {
            "decisions": plan["architecture"]["decisions"],
            "assumptions": plan["architecture"]["assumptions"],
            "modules": [
                {
                    key: item[key]
                    for key in ("id", "name", "purpose", "responsibilities", "non_goals")
                }
                for item in plan["architecture"]["modules"]
            ],
            "contracts": plan["architecture"]["contracts"],
        },
        "work_packages": [
            {
                key: item[key]
                for key in (
                    "id",
                    "title",
                    "goal",
                    "module",
                    "requirement_responsibilities",
                    "provides_contracts",
                    "consumes_contracts",
                    "depends_on",
                )
            }
            for item in plan["work_packages"]
        ],
        "tasks": [
            {
                key: item[key]
                for key in (
                    "id",
                    "work_package",
                    "title",
                    "goal",
                    "instructions",
                    "deliverable_files",
                    "requirement_responsibilities",
                    "provides_contracts",
                    "consumes_contracts",
                    "depends_on",
                    "acceptance",
                )
            }
            for item in plan["tasks"]
        ],
        "coverage": plan["coverage"],
        "link_report": result.link_report,
        "lint_report": {
            "errors": [
                {"code": item.code, "path": item.path, "message": item.message}
                for item in report.errors
            ],
            "warnings": [
                {"code": item.code, "path": item.path, "message": item.message}
                for item in report.warnings
            ],
        },
    }


def _critic_issues(
    critic: dict[str, Any],
    *,
    plan: dict[str, Any],
) -> list[_Issue]:
    """把 Critic issue 映射为控制器路由；scope=task 归其工作包。"""
    package_by_task = {str(task["id"]): str(task["work_package"]) for task in plan["tasks"]}
    package_ids = {str(item["id"]) for item in plan["work_packages"]}
    issues: list[_Issue] = []
    for item in critic.get("issues", []):
        scope = str(item["scope"])
        target_id = str(item["target_id"])
        work_package_id: str | None = None
        if scope == "task":
            work_package_id = package_by_task.get(target_id)
        elif scope == "work_package" and target_id in package_ids:
            work_package_id = target_id
        route: Route = "mechanical" if scope == "mechanical" else "global"
        if work_package_id is not None:
            route = "local"
        issues.append(
            _Issue(
                severity=str(item["severity"]),
                scope=scope,
                target_id=target_id,
                code=str(item["code"]),
                description=str(item["description"]),
                route=route,
                work_package_id=work_package_id,
            )
        )
    return issues


def _controller_verdict(issues: list[_Issue]) -> str:
    """6.4.6：控制器复核 verdict，存在 blocker/major 必须 revise。"""
    return "revise" if any(item.severity in ("blocker", "major") for item in issues) else "pass"


def _unresolved_minors(issues: list[_Issue]) -> list[dict[str, Any]]:
    """把最终未解决 minor 规范化为 Plan v3 的 review 条目（5.2.1）。

    id 由控制器按稳定排序重新编号，不沿用模型自报的编号；完整 issue 历史
    只留在 `_s4/reviews/`。
    """
    minors = sorted(
        (item for item in issues if item.severity == "minor"),
        key=lambda value: (value.scope, value.target_id, value.code),
    )
    return [
        {
            "id": f"PI-{index:03d}",
            "scope": item.scope,
            "target_id": item.target_id,
            "code": item.code,
            "description": item.description,
        }
        for index, item in enumerate(minors, start=1)
    ]


def _seal_and_publish(
    result: LinkResult,
    *,
    store: RunStore,
    checkpoints: _Checkpoints,
    inputs: S4Inputs,
    constraints: dict[str, Any],
    index: dict[str, Any],
    config_snapshot: dict[str, Any],
) -> S4Result:
    """6.4.7：重跑发布门 → 原子发布 → 重读校验 → 原子写 seal receipt。"""
    report = _plan_lint(
        result,
        inputs=inputs,
        constraints=constraints,
        index=index,
        config_snapshot=config_snapshot,
        checkpoints=checkpoints,
    )
    # 复跑发布门后才进入 SEAL_AND_PUBLISH，落盘 phase 与实际动作保持一致。
    checkpoints.set_phase("SEAL_AND_PUBLISH")
    if not report.ok:
        raise S4Error(
            "PLAN_SEAL_LINT_FAILED",
            f"seal 前复跑 full lint 失败: {sorted(report.error_codes())}",
        )
    plan = result.plan
    expected_sha256 = canonical_sha256(plan)
    run_dir = store.run_dir
    plan_path = run_dir / "plan" / "plan.json"
    existing = _existing_published_plan(plan_path)
    # 6.4.7 恢复语义：正式 Plan 已在盘上但 S4 未 done 时，只有与本次已校验的
    # canonical candidate 逐字节一致才能直接补写 seal receipt；否则那份文件是
    # 上次崩溃留下的不可信残留，必须以已校验候选重新发布。
    reused_existing = existing is not None and existing == canonical_json_bytes(plan)
    if not reused_existing:
        atomic_write_canonical_json(plan_path, plan)
    checkpoints.state["seal"] = {
        "reused_existing_plan": reused_existing,
        "plan_sha256": expected_sha256,
    }
    checkpoints.persist()

    reread = json.loads(plan_path.read_text(encoding="utf-8"))
    if canonical_sha256(reread) != expected_sha256 or _sha256_file(plan_path) != hashlib.sha256(
        canonical_json_bytes(plan)
    ).hexdigest():
        raise S4Error("PLAN_PUBLISH_MISMATCH", "重读正式 Plan 的 SHA-256 与候选不一致")
    if reread.get("input_refs") != dict(inputs.input_refs):
        raise S4Error("PLAN_PUBLISH_MISMATCH", "重读正式 Plan 的四项 input_refs 不一致")
    if reread.get("delivery_blueprint_sha256") != result.blueprint["content_sha256"]:
        raise S4Error("PLAN_PUBLISH_MISMATCH", "重读正式 Plan 的顶层 blueprint 哈希不一致")

    store.set_stage_status(
        "s4",
        "done",
        output_refs={
            "plan": {"path": "plan/plan.json", "sha256": expected_sha256},
            "delivery_blueprint_sha256": result.blueprint["content_sha256"],
            "config_snapshot_sha256": canonical_sha256(config_snapshot),
        },
    )
    checkpoints.state["status"] = "sealed"
    checkpoints.persist()
    return S4Result(
        plan=plan,
        plan_path=plan_path,
        blueprint=result.blueprint,
        state=deepcopy(checkpoints.state),
        published=True,
    )


def _existing_published_plan(plan_path: Path) -> bytes | None:
    if not plan_path.is_file():
        return None
    return plan_path.read_bytes()


def _verify_sealed_receipt(store: RunStore, inputs: S4Inputs) -> S4Result:
    """S4 已 done：核对 receipt 后作为只读 no-op，绝不改写已发布 Plan。"""
    refs = store.meta.stages["s4"].output_refs or {}
    plan_ref = refs.get("plan") if isinstance(refs, dict) else None
    if not isinstance(plan_ref, dict) or not isinstance(plan_ref.get("sha256"), str):
        raise S4Error("PLAN_RECEIPT_INVALID", "S4 done 但缺少 plan receipt")
    plan_path = store.run_dir / str(plan_ref.get("path", "plan/plan.json"))
    if not plan_path.is_file():
        raise S4Error("PLAN_RECEIPT_INVALID", "S4 done 但正式 Plan 缺失")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or canonical_sha256(plan) != plan_ref["sha256"]:
        raise S4Error("PLAN_RECEIPT_INVALID", "已发布 Plan 与 seal receipt 哈希不一致")
    if plan.get("delivery_blueprint_sha256") != refs.get("delivery_blueprint_sha256"):
        raise S4Error("PLAN_RECEIPT_INVALID", "已发布 Plan 的 blueprint 封口与 receipt 不一致")
    if plan.get("input_refs") != dict(inputs.input_refs):
        raise S4Error("PLAN_RECEIPT_INVALID", "已发布 Plan 的 input_refs 与本次冻结输入不一致")
    state_path = store.run_dir / "plan" / "_s4" / STATE_FILE
    state: dict[str, Any] = {}
    if state_path.is_file():
        loaded = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            state = loaded
    return S4Result(
        plan=plan,
        plan_path=plan_path,
        blueprint={"content_sha256": plan["delivery_blueprint_sha256"]},
        state=state,
        published=False,
    )


def _load_state(
    root: Path,
    *,
    inputs: S4Inputs,
    fingerprint: dict[str, Any],
    strategy: str,
) -> dict[str, Any]:
    """加载或新建 `s4_state`；输入引用或规划指纹变化即全部检查点作废。"""
    fresh: dict[str, Any] = {
        "schema_version": "1.0",
        "phase": "PREPARE",
        "status": "running",
        "strategy": strategy,
        "input_refs": deepcopy(dict(inputs.input_refs)),
        "planning_fingerprint": deepcopy(fingerprint),
        "attempts": {"architecture": 0, "work_packages": {}},
        "repairs": {
            "architecture": 0,
            "work_packages": {},
            "plan_critic": 0,
            "plan_global_replans": 0,
        },
        "critic_rounds": 0,
        "seen_issue_signatures": [],
        "checkpoints": {},
    }
    path = root / STATE_FILE
    if not path.is_file():
        return fresh
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fresh
    if not isinstance(loaded, dict):
        return fresh
    if (
        loaded.get("input_refs") != fresh["input_refs"]
        or loaded.get("planning_fingerprint") != fresh["planning_fingerprint"]
        or loaded.get("strategy") != strategy
    ):
        return fresh
    if Draft202012Validator(s4_state_schema()).is_valid(loaded):
        loaded["status"] = "running"
        loaded.pop("failure", None)
        return loaded
    return fresh


def _record_signatures(checkpoints: _Checkpoints, issues: list[_Issue]) -> None:
    """6.4.6：相同 issue signature 再次出现即判定不收敛，不继续振荡。"""
    seen = set(checkpoints.state["seen_issue_signatures"])
    repeated = sorted({item.signature for item in issues} & seen)
    if repeated:
        raise S4Error("PLAN_NOT_CONVERGING", f"重复出现的 issue signature: {repeated}")
    seen.update(item.signature for item in issues)
    checkpoints.state["seen_issue_signatures"] = sorted(seen)
    checkpoints.persist()


def _reexpand(
    *,
    runner: AgentRunner,
    checkpoints: _Checkpoints,
    inputs: S4Inputs,
    constraints: dict[str, Any],
    config: NepaConfig,
    budget: RunBudget,
    package: dict[str, Any],
    architecture: dict[str, Any],
    parent_sha256: str,
    issues: list[_Issue],
) -> dict[str, Any]:
    """REEXPAND_WORK_PACKAGE：Critic 局部问题与 shard 门共用同一份包预算。"""
    work_package_id = str(package["id"])
    repairs = dict(checkpoints.state["repairs"]["work_packages"])
    used = int(repairs.get(work_package_id, 0))
    if used >= config.budgets.plan_task_shard_repairs:
        raise S4Error(
            "PLAN_SHARD_BUDGET_EXHAUSTED",
            f"{work_package_id} 已用尽局部重做预算，无法修复 "
            f"{sorted({item.code for item in issues})}",
        )
    repairs[work_package_id] = used + 1
    checkpoints.state["repairs"]["work_packages"] = repairs
    checkpoints.persist()
    checkpoints.set_phase("REEXPAND_WORK_PACKAGE")
    checkpoints.invalidate(f"task_shards/{work_package_id}.json")
    return _expand_work_package(
        runner=runner,
        checkpoints=checkpoints,
        inputs=inputs,
        constraints=constraints,
        config=config,
        budget=budget,
        package=package,
        architecture=architecture,
        parent_sha256=parent_sha256,
        repair_issues=issues,
    )


def compile_plan(
    store: RunStore,
    config: NepaConfig,
    inputs: S4Inputs,
    runner: AgentRunner,
    budget: RunBudget,
    *,
    strategy: str | None = None,
) -> S4Result:
    """驱动 6.4.2 的 S4 状态机直至原子发布，或受控失败并保存现场。

    ``strategy`` 缺省取 config；``flat`` 只用于 A9 显式消融，控制器绝不在
    layered 失败后自动 fallback。
    """
    if store.meta.stages["s4"].status == "done":
        return _verify_sealed_receipt(store, inputs)
    store.begin_stage("s4")
    chosen = strategy or config.planning.strategy
    if chosen not in _STRATEGY_ROLES:
        raise S4Error("PLAN_STRATEGY_INVALID", f"未知规划策略 {chosen!r}")
    root = store.run_dir / "plan" / "_s4"
    root.mkdir(parents=True, exist_ok=True)
    fingerprint = _planning_fingerprint(config, chosen)
    checkpoints = _Checkpoints(
        root,
        _load_state(root, inputs=inputs, fingerprint=fingerprint, strategy=chosen),
    )
    checkpoints.persist()
    try:
        return _run_state_machine(
            store=store,
            config=config,
            inputs=inputs,
            runner=runner,
            budget=budget,
            checkpoints=checkpoints,
            strategy=chosen,
        )
    except S4Error as exc:
        checkpoints.fail(exc.code, exc.detail)
        raise


def _build_semantics(
    *,
    runner: AgentRunner,
    checkpoints: _Checkpoints,
    inputs: S4Inputs,
    constraints: dict[str, Any],
    index: dict[str, Any],
    config: NepaConfig,
    budget: RunBudget,
    strategy: str,
) -> tuple[PlanDraftIR, str]:
    """按策略产出规范化 PlanDraftIR 与其父哈希锚（6.4.2 两条分支）。"""
    if strategy == "flat":
        draft, digest = _flat_draft(
            runner=runner,
            checkpoints=checkpoints,
            constraints=constraints,
            index=index,
            budget=budget,
        )
        issues = _flat_validate(
            draft,
            inputs=inputs,
            constraints=constraints,
            index=index,
            config=config,
        )
        if issues:
            _record_signatures(checkpoints, issues)
            draft, digest = _flat_redraft(
                runner=runner,
                checkpoints=checkpoints,
                constraints=constraints,
                index=index,
                config=config,
                budget=budget,
                issues=issues,
            )
            if _flat_validate(
                draft,
                inputs=inputs,
                constraints=constraints,
                index=index,
                config=config,
            ):
                raise S4Error("PLAN_FLAT_VALIDATE_FAILED", "flat 重绘后仍未通过生产门")
        try:
            return normalize_flat_draft(draft), digest
        except PlanDraftError as exc:
            raise S4Error("PLAN_FLAT_VALIDATE_FAILED", str(exc)) from exc

    architecture_draft, digest = _architect(
        runner=runner,
        checkpoints=checkpoints,
        inputs=inputs,
        constraints=constraints,
        index=index,
        budget=budget,
        repair_limit=config.budgets.plan_architecture_repairs,
    )
    checkpoints.set_phase("EXPAND_WORK_PACKAGES")
    shards: list[dict[str, Any]] = []
    # 4.9：M1 按稳定 work package id 串行展开，不引入并发。
    for package in sorted(architecture_draft["work_packages"], key=lambda item: str(item["id"])):
        shards.append(
            _expand_work_package(
                runner=runner,
                checkpoints=checkpoints,
                inputs=inputs,
                constraints=constraints,
                config=config,
                budget=budget,
                package=package,
                architecture=architecture_draft["architecture"],
                parent_sha256=digest,
            )
        )
    try:
        return normalize_layered_draft(architecture_draft, shards), digest
    except PlanDraftError as exc:
        raise S4Error("PLAN_LINK_FAILED", str(exc)) from exc


def _flat_redraft(
    *,
    runner: AgentRunner,
    checkpoints: _Checkpoints,
    constraints: dict[str, Any],
    index: dict[str, Any],
    config: NepaConfig,
    budget: RunBudget,
    issues: list[_Issue],
) -> tuple[dict[str, Any], str]:
    """flat 的唯一修复路径：整份重绘，同时消耗 critic 与全局重规划各一次。"""
    repairs = checkpoints.state["repairs"]
    if (
        int(repairs["plan_critic"]) >= config.budgets.plan_critic_repairs
        or int(repairs["plan_global_replans"]) >= config.budgets.plan_global_replans
    ):
        raise S4Error(
            "PLAN_FLAT_BUDGET_EXHAUSTED",
            f"flat 重绘预算耗尽: {sorted({item.code for item in issues})}",
        )
    repairs["plan_critic"] = int(repairs["plan_critic"]) + 1
    repairs["plan_global_replans"] = int(repairs["plan_global_replans"]) + 1
    checkpoints.persist()
    checkpoints.invalidate("flat_draft.json")
    return _flat_draft(
        runner=runner,
        checkpoints=checkpoints,
        constraints=constraints,
        index=index,
        budget=budget,
        issues=issues,
    )


def _run_state_machine(
    *,
    store: RunStore,
    config: NepaConfig,
    inputs: S4Inputs,
    runner: AgentRunner,
    budget: RunBudget,
    checkpoints: _Checkpoints,
    strategy: str,
) -> S4Result:
    """PREPARE → … → SEAL_AND_PUBLISH 的单一驱动循环（6.4.2）。"""
    config_snapshot = store.meta.config_snapshot
    checkpoints.set_phase("PREPARE")
    constraints, index = _prepare(inputs, config=config, store=store)
    checkpoints.set_phase("DELIVERY_CONSTRAINTS")
    checkpoints.write(
        "delivery_constraints.json",
        constraints,
        parent_sha256=canonical_sha256(dict(inputs.input_refs)),
    )
    checkpoints.write(
        "planning_index.json",
        index,
        parent_sha256=constraints["content_sha256"],
    )
    checkpoints.set_phase("SELECT_STRATEGY")

    draft, semantics_sha256 = _build_semantics(
        runner=runner,
        checkpoints=checkpoints,
        inputs=inputs,
        constraints=constraints,
        index=index,
        config=config,
        budget=budget,
        strategy=strategy,
    )
    round_number = 0
    while True:
        try:
            result = _link(
                draft,
                checkpoints=checkpoints,
                inputs=inputs,
                constraints=constraints,
                config_snapshot=config_snapshot,
                parent_sha256=semantics_sha256,
            )
        except S4Error as exc:
            if exc.code != "PLAN_LINK_FAILED":
                raise
            # 6.4.5：Linker 无法闭合的语义图必须回流修复，而不是把尚可
            # 修正的 architecture/task-shard 草稿直接终止。当前无法可靠定位
            # 到某一 shard 的错误保守走 global route；全局重规划仍受既有预算约束。
            issues = [
                _Issue(
                    severity="blocker",
                    scope="architecture",
                    target_id="plan",
                    code="PLAN_LINK_FAILED",
                    description=exc.detail,
                    route="global",
                )
            ]
            _record_signatures(checkpoints, issues)
            draft, semantics_sha256 = _repair(
                runner=runner,
                checkpoints=checkpoints,
                inputs=inputs,
                constraints=constraints,
                index=index,
                config=config,
                budget=budget,
                strategy=strategy,
                draft=draft,
                semantics_sha256=semantics_sha256,
                issues=issues,
            )
            continue
        report = _plan_lint(
            result,
            inputs=inputs,
            constraints=constraints,
            index=index,
            config_snapshot=config_snapshot,
            checkpoints=checkpoints,
        )
        issues = _lint_issues(report, plan=result.plan)
        critic: dict[str, Any] | None = None
        if not issues:
            checkpoints.set_phase("PLAN_CRITIC")
            budget.checkpoint()
            round_number = int(checkpoints.state["critic_rounds"]) + 1
            checkpoints.state["critic_rounds"] = round_number
            checkpoints.persist()
            critic = _invoke(
                runner,
                "plan_critic",
                _critic_payload(result, report),
                plan_critic_schema(),
                attempt=round_number,
                checkpoints=checkpoints,
                parent_sha256=canonical_sha256(result.plan),
            )
            issues = _critic_issues(critic, plan=result.plan)
        verdict = _controller_verdict(issues)
        _write_review(
            checkpoints,
            round_number=max(round_number, 1),
            verdict=verdict,
            issues=issues,
            critic=critic,
            report=report,
            parent_sha256=canonical_sha256(result.plan),
        )
        if verdict == "pass":
            sealed = _link(
                draft,
                checkpoints=checkpoints,
                inputs=inputs,
                constraints=constraints,
                config_snapshot=config_snapshot,
                parent_sha256=semantics_sha256,
                review={
                    "verdict": "pass",
                    "unresolved_minor_issues": _unresolved_minors(issues),
                },
            )
            return _seal_and_publish(
                sealed,
                store=store,
                checkpoints=checkpoints,
                inputs=inputs,
                constraints=constraints,
                index=index,
                config_snapshot=config_snapshot,
            )
        _record_signatures(checkpoints, issues)
        draft, semantics_sha256 = _repair(
            runner=runner,
            checkpoints=checkpoints,
            inputs=inputs,
            constraints=constraints,
            index=index,
            config=config,
            budget=budget,
            strategy=strategy,
            draft=draft,
            semantics_sha256=semantics_sha256,
            issues=issues,
        )


def _write_review(
    checkpoints: _Checkpoints,
    *,
    round_number: int,
    verdict: str,
    issues: list[_Issue],
    critic: dict[str, Any] | None,
    report: LintReport,
    parent_sha256: str,
) -> None:
    """完整评审历史留在 `_s4/reviews/`；控制器 verdict 覆盖模型自报值。"""
    checkpoints.write(
        f"reviews/round_{round_number:03d}.json",
        {
            "schema_version": "1.0",
            "round": round_number,
            "controller_verdict": verdict,
            "critic_verdict": None if critic is None else str(critic.get("verdict")),
            "lint_error_codes": sorted(report.error_codes()),
            "issues": [item.as_dict() for item in issues],
        },
        parent_sha256=parent_sha256,
    )


def _repair(
    *,
    runner: AgentRunner,
    checkpoints: _Checkpoints,
    inputs: S4Inputs,
    constraints: dict[str, Any],
    index: dict[str, Any],
    config: NepaConfig,
    budget: RunBudget,
    strategy: str,
    draft: PlanDraftIR,
    semantics_sha256: str,
    issues: list[_Issue],
) -> tuple[PlanDraftIR, str]:
    """6.4.6 定点修复路由：mechanical 重链 / local shard / global 架构 / flat 重绘。"""
    repairs = checkpoints.state["repairs"]
    if issues and all(item.route == "mechanical" for item in issues):
        # 6.4.6：机械问题由控制器修正后重新链接。机械字段全部由 L2 确定性代码
        # 生成，语义草稿不动、也不消耗任何语义修复配额；重链是幂等的，因此同一
        # 机械 signature 若再次出现，会由上一轮已登记的不收敛门直接终止，而不是
        # 拿全局重规划配额去让 ArchitecturePlanner 修控制器自己拥有的字段。
        checkpoints.set_phase("LINK_AND_RESOLVE_BLUEPRINT")
        checkpoints.invalidate("candidate_plan.json")
        return draft, semantics_sha256
    if int(repairs["plan_critic"]) >= config.budgets.plan_critic_repairs:
        raise S4Error(
            "PLAN_CRITIC_BUDGET_EXHAUSTED",
            f"critic 语义修复预算耗尽: {sorted({item.code for item in issues})}",
        )
    if strategy == "flat":
        redrafted, digest = _flat_redraft(
            runner=runner,
            checkpoints=checkpoints,
            constraints=constraints,
            index=index,
            config=config,
            budget=budget,
            issues=issues,
        )
        if _flat_validate(
            redrafted,
            inputs=inputs,
            constraints=constraints,
            index=index,
            config=config,
        ):
            raise S4Error("PLAN_FLAT_VALIDATE_FAILED", "flat 重绘后仍未通过生产门")
        try:
            return normalize_flat_draft(redrafted), digest
        except PlanDraftError as exc:
            raise S4Error("PLAN_FLAT_VALIDATE_FAILED", str(exc)) from exc

    repairs["plan_critic"] = int(repairs["plan_critic"]) + 1
    checkpoints.persist()
    local = [item for item in issues if item.route == "local" and item.work_package_id]
    global_issues = [item for item in issues if item.route == "global"]
    if global_issues or not local:
        return _replan_architecture(
            runner=runner,
            checkpoints=checkpoints,
            inputs=inputs,
            constraints=constraints,
            index=index,
            config=config,
            budget=budget,
            issues=global_issues or issues,
        )
    package_by_id = {str(item["id"]): item for item in draft.work_packages}
    shards = {
        work_package_id: {
            "schema_version": "1.0",
            "work_package_id": work_package_id,
            "tasks": tasks,
        }
        for work_package_id, tasks in draft.tasks_by_work_package.items()
    }
    for work_package_id in sorted({str(item.work_package_id) for item in local}):
        package = package_by_id[work_package_id]
        shards[work_package_id] = _reexpand(
            runner=runner,
            checkpoints=checkpoints,
            inputs=inputs,
            constraints=constraints,
            config=config,
            budget=budget,
            package=package,
            architecture=draft.architecture,
            parent_sha256=semantics_sha256,
            issues=[item for item in local if item.work_package_id == work_package_id],
        )
    architecture_draft = {
        "schema_version": "1.0",
        "architecture": draft.architecture,
        "work_packages": draft.work_packages,
    }
    try:
        return (
            normalize_layered_draft(
                architecture_draft,
                [shards[key] for key in sorted(shards)],
            ),
            semantics_sha256,
        )
    except PlanDraftError as exc:
        raise S4Error("PLAN_LINK_FAILED", str(exc)) from exc


def _replan_architecture(
    *,
    runner: AgentRunner,
    checkpoints: _Checkpoints,
    inputs: S4Inputs,
    constraints: dict[str, Any],
    index: dict[str, Any],
    config: NepaConfig,
    budget: RunBudget,
    issues: list[_Issue],
) -> tuple[PlanDraftIR, str]:
    """REPLAN_ARCHITECTURE：全局问题最多回架构一次，并作废受影响 shard。"""
    repairs = checkpoints.state["repairs"]
    if int(repairs["plan_global_replans"]) >= config.budgets.plan_global_replans:
        raise S4Error(
            "PLAN_GLOBAL_REPLAN_EXHAUSTED",
            f"全局重规划预算耗尽: {sorted({item.code for item in issues})}",
        )
    repairs["plan_global_replans"] = int(repairs["plan_global_replans"]) + 1
    checkpoints.persist()
    previous = checkpoints.reusable(
        "architecture_candidate.json",
        parent_sha256=str(
            checkpoints.state["checkpoints"]["architecture_candidate.json"]["parent_sha256"]
        ),
    )
    checkpoints.invalidate("architecture_candidate.json")
    checkpoints.invalidate("task_shards/")
    architecture_draft, digest = _architect(
        runner=runner,
        checkpoints=checkpoints,
        inputs=inputs,
        constraints=constraints,
        index=index,
        budget=budget,
        repair_limit=config.budgets.plan_architecture_repairs,
        previous=previous,
        issues=issues,
    )
    checkpoints.set_phase("EXPAND_WORK_PACKAGES")
    shards: list[dict[str, Any]] = []
    for package in sorted(architecture_draft["work_packages"], key=lambda item: str(item["id"])):
        shards.append(
            _expand_work_package(
                runner=runner,
                checkpoints=checkpoints,
                inputs=inputs,
                constraints=constraints,
                config=config,
                budget=budget,
                package=package,
                architecture=architecture_draft["architecture"],
                parent_sha256=digest,
            )
        )
    try:
        return normalize_layered_draft(architecture_draft, shards), digest
    except PlanDraftError as exc:
        raise S4Error("PLAN_LINK_FAILED", str(exc)) from exc
