"""M1 spec-run creation and resumable S4→S6 orchestration."""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nepa.agents.base import AgentRunner
from nepa.agents.roles import RoleRegistry
from nepa.assets import validate_profile, validate_test_bundle
from nepa.canonical import atomic_write_canonical_json
from nepa.config import NepaConfig
from nepa.llm.factory import LLMFactory
from nepa.orchestrator import (
    BudgetExhausted,
    M1ResumeCoordinator,
    ResumeDisposition,
    RunBudget,
)
from nepa.profile_build import build_default_assets
from nepa.run_store import RunStore, create_run
from nepa.stages.s4_plan import S4Error, S4Inputs, compile_plan
from nepa.stages.s5_scaffold import S5Error, S5Inputs, scaffold_project
from nepa.stages.s6_execute import S6Error, S6Inputs, execute_plan
from nepa.tools.build import BuildTool
from nepa.tools.events import StageEventWriter
from nepa.tools.fs_ops import sha256_file
from nepa.tools.sandbox import Sandbox
from nepa.tools.test_runner import PytestGateRunner

StageName = str
ServiceFactory = Callable[
    [RunStore, NepaConfig, Path, RunBudget],
    "_RuntimeServices",
]


class M1RuntimeError(RuntimeError):
    """Invalid M1 admission or an already-active controller."""


@dataclass(frozen=True, slots=True)
class M1RuntimeResult:
    run_id: str
    run_dir: Path
    action: str
    exit_code: int


@dataclass(frozen=True, slots=True)
class _FrozenInputs:
    s4: S4Inputs
    s5: S5Inputs
    s6: S6Inputs


@dataclass(slots=True)
class _RuntimeServices:
    runner: AgentRunner
    build_tool: BuildTool
    gate_runner: PytestGateRunner
    close: Callable[[], None]


def _load_object(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise M1RuntimeError(f"{description} 不是可读 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise M1RuntimeError(f"{description} 顶层必须是 JSON object")
    return value


def _relative_source(path: Path, repo_root: Path) -> str:
    resolved = path.resolve()
    if not resolved.is_relative_to(repo_root):
        raise M1RuntimeError("M1 Spec 必须位于当前仓库边界内")
    return resolved.relative_to(repo_root).as_posix()


def _protocol_slug(spec: dict[str, Any]) -> str:
    protocol = spec.get("protocol")
    name = protocol.get("name") if isinstance(protocol, dict) else None
    if not isinstance(name, str):
        raise M1RuntimeError("Spec 缺少 protocol.name")
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    if not slug:
        raise M1RuntimeError("protocol.name 无法形成合法 run id")
    return slug


def _asset_ref(value: dict[str, Any], path: str, digest: str) -> dict[str, str]:
    asset = value.get("asset")
    if not isinstance(asset, dict):
        raise M1RuntimeError(f"{path} 缺少 asset 元数据")
    asset_id = asset.get("id")
    version = asset.get("version")
    if not isinstance(asset_id, str) or not isinstance(version, str):
        raise M1RuntimeError(f"{path} 的 asset.id/version 非法")
    return {
        "id": asset_id,
        "version": version,
        "path": path,
        "sha256": digest,
    }


def _resolved_assets(
    repo_root: Path,
    config: NepaConfig,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    defaults = (
        config.assets.target_profile == "mqtt-client-broker"
        and config.assets.language_profile == "c99-posix"
        and config.assets.test_bundle == "mqtt-3-1-1-min-gold"
    )
    if defaults:
        build_default_assets(repo_root)
    paths = (
        repo_root / "profiles" / "resolved" / f"{config.assets.target_profile}.json",
        repo_root / "profiles" / "resolved" / f"{config.assets.language_profile}.json",
        repo_root / "profiles" / "resolved" / f"{config.assets.test_bundle}.json",
    )
    target, language, test_bundle = (
        _load_object(paths[0], "Target Profile"),
        _load_object(paths[1], "Language Profile"),
        _load_object(paths[2], "Test Bundle"),
    )
    validate_profile(target, kind="target", workspace_root=repo_root)
    validate_profile(language, kind="language", workspace_root=repo_root)
    validate_test_bundle(test_bundle, workspace_root=repo_root)
    expected_ids = (
        config.assets.target_profile,
        config.assets.language_profile,
        config.assets.test_bundle,
    )
    actual_ids = tuple(value["asset"]["id"] for value in (target, language, test_bundle))
    if actual_ids != expected_ids:
        raise M1RuntimeError(
            f"解析资产 id 与配置不一致: expected={expected_ids}, actual={actual_ids}"
        )
    return target, language, test_bundle


def create_spec_run(
    *,
    spec_path: str | Path,
    runs_root: str | Path,
    repo_root: str | Path,
    config: NepaConfig,
) -> RunStore:
    """Validate inputs, create Run v2, and publish the four frozen S4 inputs."""
    root = Path(repo_root).resolve()
    source = Path(spec_path)
    if not source.is_absolute():
        source = root / source
    source = source.resolve()
    source_relative = _relative_source(source, root)
    spec = _load_object(source, "Spec")
    target, language, test_bundle = _resolved_assets(root, config)

    run_root = Path(runs_root)
    if not run_root.is_absolute():
        run_root = root / run_root
    run_root = run_root.resolve()
    if not run_root.is_relative_to(root):
        raise M1RuntimeError("runs_root 必须位于仓库边界内")

    # Compute canonical frozen bytes before create_run so run.json can bind all refs
    # in its initial atomic publication.
    frozen_values = {
        "spec": spec,
        "target_profile": target,
        "language_profile": language,
        "test_bundle": test_bundle,
    }
    frozen_paths = {
        "spec": "spec/spec.json",
        "target_profile": "inputs/target.json",
        "language_profile": "inputs/language.json",
        "test_bundle": "inputs/test_bundle.json",
    }
    frozen_hashes: dict[str, str] = {}
    from nepa.canonical import canonical_json_bytes

    for kind, value in frozen_values.items():
        frozen_hashes[kind] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()

    inputs = {
        "spec": {"path": source_relative, "sha256": sha256_file(source)},
        "target_profile": _asset_ref(
            target,
            frozen_paths["target_profile"],
            frozen_hashes["target_profile"],
        ),
        "language_profile": _asset_ref(
            language,
            frozen_paths["language_profile"],
            frozen_hashes["language_profile"],
        ),
        "test_bundle": _asset_ref(
            test_bundle,
            frozen_paths["test_bundle"],
            frozen_hashes["test_bundle"],
        ),
    }
    store = create_run(
        run_root,
        _protocol_slug(spec),
        "spec-run",
        inputs=inputs,
        config_snapshot=config.config_snapshot(),
    )
    for kind, value in frozen_values.items():
        atomic_write_canonical_json(store.run_dir / frozen_paths[kind], value)
    return store


def _load_frozen_inputs(store: RunStore, repo_root: Path) -> _FrozenInputs:
    if store.meta.entry != "spec-run":
        raise M1RuntimeError("M1 runtime 只接受 spec-run")
    meta_inputs = store.meta.inputs.model_dump(mode="json")
    spec_path = store.run_dir / "spec" / "spec.json"
    target_path = store.run_dir / "inputs" / "target.json"
    language_path = store.run_dir / "inputs" / "language.json"
    bundle_path = store.run_dir / "inputs" / "test_bundle.json"
    spec = _load_object(spec_path, "冻结 Spec")
    target = _load_object(target_path, "冻结 Target Profile")
    language = _load_object(language_path, "冻结 Language Profile")
    test_bundle = _load_object(bundle_path, "冻结 Test Bundle")
    manifest_ref = test_bundle.get("manifest_ref")
    if not isinstance(manifest_ref, dict) or not isinstance(manifest_ref.get("path"), str):
        raise M1RuntimeError("冻结 Test Bundle 缺少 manifest_ref.path")
    manifest = _load_object(repo_root / manifest_ref["path"], "冻结 Test Manifest")
    input_refs = {
        "spec": {"path": "spec/spec.json", "sha256": sha256_file(spec_path)},
        "target_profile": {
            key: meta_inputs["target_profile"][key] for key in ("path", "sha256")
        },
        "language_profile": {
            key: meta_inputs["language_profile"][key] for key in ("path", "sha256")
        },
        "test_bundle": {
            key: meta_inputs["test_bundle"][key] for key in ("path", "sha256")
        },
    }
    s4 = S4Inputs(
        spec=spec,
        target=target,
        language=language,
        test_bundle=test_bundle,
        manifest=manifest,
        input_refs=input_refs,
        repo_root=repo_root,
    )
    return _FrozenInputs(
        s4=s4,
        s5=S5Inputs(
            spec=spec,
            target=target,
            language=language,
            test_bundle=test_bundle,
            manifest=manifest,
            input_refs=input_refs,
            repo_root=repo_root,
        ),
        s6=S6Inputs(
            spec=spec,
            language=language,
            test_bundle=test_bundle,
            manifest=manifest,
        ),
    )


def _default_services(
    store: RunStore,
    config: NepaConfig,
    repo_root: Path,
    budget: RunBudget,
) -> _RuntimeServices:
    events = StageEventWriter(store.run_dir / "trace", store.run_id)

    def record_sandbox_event(event: dict[str, Any]) -> None:
        events(event)
        budget.checkpoint()

    sandbox = Sandbox(
        image=config.sandbox.image,
        cpu=config.sandbox.cpu,
        mem_gb=config.sandbox.mem_gb,
        on_event=record_sandbox_event,
    )
    factory = LLMFactory(config, store.run_dir, store.run_id)

    def record_llm_usage(response: Any) -> None:
        budget.record_llm_response(response)

    runner = AgentRunner(
        RoleRegistry(config),
        factory,
        on_usage=record_llm_usage,
    )
    test_bundle = _load_object(
        store.run_dir / "inputs" / "test_bundle.json",
        "冻结 Test Bundle",
    )
    return _RuntimeServices(
        runner=runner,
        build_tool=BuildTool(sandbox),
        gate_runner=PytestGateRunner(
            sandbox,
            repo_root=repo_root,
            test_bundle=test_bundle,
        ),
        close=factory.close,
    )


def _result(store: RunStore, disposition: ResumeDisposition) -> M1RuntimeResult:
    return M1RuntimeResult(
        run_id=store.run_id,
        run_dir=store.run_dir,
        action=disposition.action,
        exit_code=disposition.exit_code if disposition.exit_code is not None else 1,
    )


def _internal_error(store: RunStore, stage: StageName, exc: BaseException) -> M1RuntimeResult:
    state = store.meta.stages[stage]
    if state.status == "running":
        store.set_stage_status(stage, "failed", error=f"{type(exc).__name__}: {exc}")
    if store.meta.termination_kind is None:
        store.finalize("internal_error", 1)
    return M1RuntimeResult(store.run_id, store.run_dir, "internal_error", 1)


def drive_m1(
    store: RunStore,
    *,
    repo_root: str | Path,
    service_factory: ServiceFactory = _default_services,
) -> M1RuntimeResult:
    """Resume a frozen M1 run through S6 and the planned-stop commit point."""
    root = Path(repo_root).resolve()
    config = NepaConfig.model_validate(store.meta.config_snapshot)
    if config.run.until != "s6":
        raise M1RuntimeError("M1 spec-run 当前必须冻结 run.until=s6")
    budget = RunBudget(store, config.budgets)
    coordinator = M1ResumeCoordinator(store, budget)
    disposition = coordinator.resume_terminal_windows()
    if disposition.action != "continue":
        return _result(store, disposition)

    inputs = _load_frozen_inputs(store, root)
    services: _RuntimeServices | None = None
    stage = "s4"
    try:
        services = service_factory(store, config, root, budget)
        budget.checkpoint()
        compile_plan(store, config, inputs.s4, services.runner, budget)

        stage = "s5"
        budget.checkpoint()
        scaffold_project(
            store,
            inputs.s5,
            build_tool=services.build_tool,
            gate_runner=services.gate_runner,
        )

        stage = "s6"
        budget.checkpoint()
        execute_plan(
            store,
            inputs.s6,
            services.runner,
            build_tool=services.build_tool,
            gate_runner=services.gate_runner,
        )
        budget.checkpoint()
        disposition = coordinator.resume_terminal_windows()
        if disposition.action != "planned_stop":
            raise RuntimeError("S6 completed without planned-stop finalization")
        return _result(store, disposition)
    except BudgetExhausted as exc:
        store.request_controlled_exit(
            stage,  # type: ignore[arg-type]
            "GLOBAL_BUDGET_EXHAUSTED",
            str(exc),
            error=str(exc),
        )
        return _result(store, coordinator.resume_terminal_windows())
    except (S4Error, S5Error, S6Error) as exc:
        store.request_controlled_exit(
            stage,  # type: ignore[arg-type]
            exc.code,
            exc.detail,
            error=str(exc),
        )
        return _result(store, coordinator.resume_terminal_windows())
    except Exception as exc:  # noqa: BLE001
        return _internal_error(store, stage, exc)
    finally:
        if services is not None:
            services.close()


@contextmanager
def controller_lock(store: RunStore) -> Iterator[None]:
    """Hold the non-blocking per-run controller lock for run/resume."""
    path = store.run_dir / "cache" / "controller.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise M1RuntimeError(f"run {store.run_id} 已有活跃 controller") from exc
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def run_new_m1(
    *,
    spec_path: str | Path,
    runs_root: str | Path,
    repo_root: str | Path,
    config: NepaConfig,
    service_factory: ServiceFactory = _default_services,
) -> M1RuntimeResult:
    store = create_spec_run(
        spec_path=spec_path,
        runs_root=runs_root,
        repo_root=repo_root,
        config=config,
    )
    with controller_lock(store):
        return drive_m1(store, repo_root=repo_root, service_factory=service_factory)


def resume_m1(
    run_dir: str | Path,
    *,
    repo_root: str | Path,
    service_factory: ServiceFactory = _default_services,
) -> M1RuntimeResult:
    store = RunStore.load(run_dir)
    with controller_lock(store):
        # Reload after acquiring the lock so a previous controller's final atomic
        # run.json write cannot be hidden by a stale in-memory model.
        store = RunStore.load(run_dir)
        return drive_m1(store, repo_root=repo_root, service_factory=service_factory)
