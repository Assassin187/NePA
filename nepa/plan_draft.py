"""S4 PlanDraftIR 规范化与确定性 Linker（设计 6.4、6.4.5）。

本模块只做确定性代码工作：把 layered/flat 两种策略的语义草稿规范化为同一
``PlanDraftIR``，再按 6.4.5 的九步链接为 canonical candidate Plan。任何
LLM 回显的最终 id、哈希、coverage、review 或执行状态都不被信任。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from heapq import heapify, heappop, heappush
from typing import Any

from nepa.delivery import compile_delivery_blueprint


class PlanDraftError(ValueError):
    """局部草稿无法确定性规范化或链接。"""


@dataclass(frozen=True, slots=True)
class LinkedTasks:
    """最终 tasks 与 local→final id 映射；不含 Plan 发布语义。"""

    tasks: list[dict[str, Any]]
    task_ids: dict[tuple[str, str], str]


@dataclass(frozen=True, slots=True)
class PlanDraftIR:
    """layered 与 flat 共用的规范化草稿。

    ``architecture``/``work_packages`` 使用 ArchitectureDraft 语义；
    ``tasks_by_work_package`` 的每项是一个 TaskShard 的 ``tasks`` 数组，键为
    工作包 id。局部 task 依赖只允许引用同包 local id。
    """

    architecture: dict[str, Any]
    work_packages: list[dict[str, Any]]
    tasks_by_work_package: dict[str, list[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class LinkResult:
    """Linker 输出：candidate Plan 与可审计的 link report。"""

    plan: dict[str, Any]
    blueprint: dict[str, Any]
    link_report: dict[str, Any]


def normalize_layered_draft(
    architecture_draft: dict[str, Any],
    shards: list[dict[str, Any]],
) -> PlanDraftIR:
    """把冻结 architecture candidate 与各工作包 shard 合并为 PlanDraftIR。"""
    architecture = architecture_draft.get("architecture")
    packages = architecture_draft.get("work_packages")
    if not isinstance(architecture, dict) or not isinstance(packages, list):
        raise PlanDraftError("ArchitectureDraft 必须含 architecture 与 work_packages")
    package_ids: list[str] = []
    for item in packages:
        value = item.get("id") if isinstance(item, dict) else None
        if not isinstance(value, str):
            raise PlanDraftError("work package id 必须为字符串")
        package_ids.append(value)
    tasks_by_package: dict[str, list[dict[str, Any]]] = {}
    for shard in shards:
        if not isinstance(shard, dict):
            raise PlanDraftError("TaskShard 必须为 object")
        work_package_id = shard.get("work_package_id")
        tasks = shard.get("tasks")
        if not isinstance(work_package_id, str) or not isinstance(tasks, list):
            raise PlanDraftError("TaskShard 必须含 work_package_id 与 tasks")
        if work_package_id in tasks_by_package:
            raise PlanDraftError(f"工作包 {work_package_id} 有多份 shard")
        if work_package_id not in package_ids:
            raise PlanDraftError(f"shard 指向未知工作包 {work_package_id}")
        tasks_by_package[work_package_id] = deepcopy(tasks)
    missing = [value for value in package_ids if value not in tasks_by_package]
    if missing:
        raise PlanDraftError(f"以下工作包缺少 shard: {sorted(missing)}")
    return PlanDraftIR(
        architecture=deepcopy(architecture),
        work_packages=deepcopy(packages),
        tasks_by_work_package=tasks_by_package,
    )


def normalize_flat_draft(draft: dict[str, Any]) -> PlanDraftIR:
    """把 FlatPlanBaseline 的单次草稿确定性拆入同一 PlanDraftIR。

    flat 草稿的 tasks 自带 ``work_package_id``；控制器按该字段分组，不为 flat
    维护第二套链接语义。
    """
    architecture = draft.get("architecture")
    packages = draft.get("work_packages")
    tasks = draft.get("tasks")
    if (
        not isinstance(architecture, dict)
        or not isinstance(packages, list)
        or not isinstance(tasks, list)
    ):
        raise PlanDraftError("flat 草稿必须含 architecture、work_packages 与 tasks")
    package_ids = {
        item["id"]
        for item in packages
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    grouped: dict[str, list[dict[str, Any]]] = {value: [] for value in sorted(package_ids)}
    for task in tasks:
        if not isinstance(task, dict):
            raise PlanDraftError("flat task 必须为 object")
        work_package_id = task.get("work_package_id")
        if not isinstance(work_package_id, str) or work_package_id not in grouped:
            raise PlanDraftError(f"flat task 指向未知工作包 {work_package_id!r}")
        local = deepcopy(task)
        del local["work_package_id"]
        grouped[work_package_id].append(local)
    empty = sorted(key for key, value in grouped.items() if not value)
    if empty:
        raise PlanDraftError(f"以下工作包没有任务: {empty}")
    return PlanDraftIR(
        architecture=deepcopy(architecture),
        work_packages=deepcopy(packages),
        tasks_by_work_package=grouped,
    )


def _shards_from_ir(draft: PlanDraftIR) -> list[dict[str, Any]]:
    return [
        {"work_package_id": work_package_id, "tasks": tasks}
        for work_package_id, tasks in sorted(draft.tasks_by_work_package.items())
    ]


def link_tasks(
    shards: list[dict[str, Any]],
    *,
    extra_deps: dict[tuple[str, str], set[tuple[str, str]]] | None = None,
) -> LinkedTasks:
    """Assign ``T-###`` in stable Kahn order, never trusting LLM array order.

    Local ``depends_on`` may only name tasks of the same work package. Cross-package
    edges must arrive through ``extra_deps``, which the contract linker derives from
    provider/consumer relations; shards can never assert them directly.
    """
    nodes: dict[tuple[str, str], dict[str, Any]] = {}
    deps: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for shard in shards:
        wp = shard.get("work_package_id")
        tasks = shard.get("tasks")
        if not isinstance(wp, str) or not isinstance(tasks, list):
            raise PlanDraftError("invalid TaskShard")
        for task in tasks:
            local_id = task.get("local_id") if isinstance(task, dict) else None
            key = (wp, local_id) if isinstance(local_id, str) else None
            if key is None or key in nodes:
                raise PlanDraftError(f"duplicate or invalid local task in {wp}")
            nodes[key] = deepcopy(task)
            raw_deps = task.get("depends_on", [])
            if not isinstance(raw_deps, list) or not all(isinstance(item, str) for item in raw_deps):
                raise PlanDraftError(f"{wp}/{local_id}: invalid local dependencies")
            deps[key] = {(wp, item) for item in raw_deps}
    for key, values in (extra_deps or {}).items():
        if key not in nodes:
            raise PlanDraftError(f"unknown task for derived dependency: {key}")
        deps[key] |= set(values)
    for key, values in deps.items():
        unknown = values - nodes.keys()
        if unknown:
            raise PlanDraftError(f"{key[0]}/{key[1]}: unknown local dependency {sorted(unknown)}")
        if key in values:
            raise PlanDraftError(f"{key[0]}/{key[1]}: task cannot depend on itself")

    remaining = {key: set(value) for key, value in deps.items()}
    dependents: dict[tuple[str, str], set[tuple[str, str]]] = {
        key: set() for key in nodes
    }
    for key, values in remaining.items():
        for dependency in values:
            dependents[dependency].add(key)
    ready = [key for key, value in remaining.items() if not value]
    heapify(ready)
    ordered: list[tuple[str, str]] = []
    while ready:
        key = heappop(ready)
        if key not in remaining:
            continue
        remaining.pop(key)
        ordered.append(key)
        for dependent in sorted(dependents[key]):
            pending = remaining[dependent]
            pending.remove(key)
            if not pending:
                heappush(ready, dependent)
    if remaining:
        raise PlanDraftError("task shard dependencies contain a cycle")
    ids = {key: f"T-{index:03d}" for index, key in enumerate(ordered, start=1)}
    final: list[dict[str, Any]] = []
    for key in ordered:
        task = nodes[key]
        task["id"] = ids[key]
        task["work_package"] = key[0]
        task.pop("depends_on")
        task["depends_on"] = sorted(ids[dep] for dep in deps[key])
        task.pop("local_id")
        final.append(task)
    return LinkedTasks(tasks=final, task_ids=ids)


def assign_stable_task_ids(shards: list[dict[str, Any]]) -> LinkedTasks:
    """仅按 shard 内局部依赖分配最终 id（无跨包 contract 边）。"""
    return link_tasks(shards)


def _check_set_equalities(draft: PlanDraftIR) -> None:
    """6.4.5 步骤 1：module→work package→task 的 contract/责任/文件集合等式。"""
    packages = {item["id"]: item for item in draft.work_packages}
    for work_package_id, tasks in draft.tasks_by_work_package.items():
        package = packages[work_package_id]
        allowed = set(package["allowed_files"])
        files: list[str] = []
        for task in tasks:
            deliverables = task.get("deliverable_files")
            if not isinstance(deliverables, list) or not deliverables:
                raise PlanDraftError(f"{work_package_id}: 任务必须声明非空 deliverable_files")
            files.extend(str(value) for value in deliverables)
        if len(files) != len(set(files)):
            raise PlanDraftError(f"{work_package_id}: 任务文件在包内不互斥")
        if set(files) != allowed:
            raise PlanDraftError(
                f"{work_package_id}: 任务文件并集必须恰等于 allowed_files"
            )
        for field in ("provides_contracts", "consumes_contracts"):
            union = {value for task in tasks for value in task.get(field, [])}
            if union != set(package[field]):
                raise PlanDraftError(
                    f"{work_package_id}/{field}: 任务集合并集必须等于工作包集合"
                )
        _check_responsibility_refinement(work_package_id, package, tasks)


def _check_responsibility_refinement(
    work_package_id: str,
    package: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> None:
    """5.2.2：每项工作包责任必须细化到本包任务，且 primary 恰落到一个任务。"""
    package_roles = {
        str(item["req_id"]): str(item["role"])
        for item in package["requirement_responsibilities"]
    }
    for task in tasks:
        seen: set[str] = set()
        for item in task.get("requirement_responsibilities", []):
            req_id = str(item["req_id"])
            if req_id in seen:
                raise PlanDraftError(f"{work_package_id}: 任务内 {req_id} 责任重复")
            seen.add(req_id)
            if req_id not in package_roles:
                raise PlanDraftError(
                    f"{work_package_id}: 任务认领了包外责任 {req_id}"
                )
            if item["role"] == "primary" and package_roles[req_id] != "primary":
                raise PlanDraftError(
                    f"{work_package_id}: supporting 工作包内不允许 primary task 责任 {req_id}"
                )
    for req_id, role in package_roles.items():
        matches = [
            item
            for task in tasks
            for item in task.get("requirement_responsibilities", [])
            if item["req_id"] == req_id
        ]
        if not matches:
            raise PlanDraftError(f"{work_package_id}: 责任 {req_id} 未细化到任何任务")
        primaries = sum(item["role"] == "primary" for item in matches)
        if role == "primary" and primaries != 1:
            raise PlanDraftError(
                f"{work_package_id}: primary 责任 {req_id} 必须恰有一个 primary task"
            )


def _resolve_provider_tasks(
    draft: PlanDraftIR,
) -> dict[str, tuple[str, str]]:
    """6.4.5 步骤 2：为每个 task-ready contract 解析唯一 provider task。"""
    providers: dict[str, tuple[str, str]] = {}
    for contract in draft.architecture["contracts"]:
        if contract["ready_gate"] != "task":
            continue
        contract_id = contract["id"]
        candidates = [
            (work_package_id, str(task["local_id"]))
            for work_package_id, tasks in sorted(draft.tasks_by_work_package.items())
            for task in tasks
            if contract_id in task.get("provides_contracts", [])
        ]
        if len(candidates) != 1:
            raise PlanDraftError(
                f"contract {contract_id} 必须恰有一个 provider task，实际 {len(candidates)}"
            )
        provider = candidates[0]
        consumer = any(
            contract_id in task.get("consumes_contracts", [])
            for task in draft.tasks_by_work_package[provider[0]]
            if str(task["local_id"]) == provider[1]
        )
        if consumer:
            raise PlanDraftError(
                f"contract {contract_id} 的 provider task 不得同时消费它"
            )
        providers[contract_id] = provider
    return providers


def _contract_edges(
    draft: PlanDraftIR,
    providers: dict[str, tuple[str, str]],
) -> dict[tuple[str, str], set[tuple[str, str]]]:
    """6.4.5 步骤 2：每个消费任务都获得指向 provider task 的确定性依赖边。"""
    edges: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for work_package_id, tasks in sorted(draft.tasks_by_work_package.items()):
        for task in tasks:
            key = (work_package_id, str(task["local_id"]))
            for contract_id in task.get("consumes_contracts", []):
                provider = providers.get(contract_id)
                if provider is None or provider == key:
                    continue
                edges.setdefault(key, set()).add(provider)
    return edges


def _requirement_closure(
    tasks: list[dict[str, Any]],
) -> dict[str, set[str]]:
    """按最终 DAG 计算每个任务的祖先闭包（含自身）。"""
    depends = {task["id"]: set(task["depends_on"]) for task in tasks}
    closure: dict[str, set[str]] = {}
    for task in tasks:  # tasks 已是稳定拓扑序，祖先必先完成
        task_id = task["id"]
        ancestors: set[str] = {task_id}
        for dependency in depends[task_id]:
            ancestors |= closure[dependency]
        closure[task_id] = ancestors
    return closure


def enabled_test_nodeids(
    manifest: dict[str, Any],
    config_snapshot: dict[str, Any],
) -> set[str]:
    """5.2.3：由本次 config snapshot 确定性派生 enabled 集合。

    当前唯一开关是 ``stages.l3_interop``；禁用测试仍保留静态 gate 映射。
    """
    stages = config_snapshot.get("stages")
    l3_enabled = bool(stages.get("l3_interop")) if isinstance(stages, dict) else False
    enabled: set[str] = set()
    for item in manifest["tests"]:
        if item.get("layer") == "l3" and not l3_enabled:
            continue
        enabled.add(str(item["nodeid"]))
    return enabled


def build_coverage(
    tasks: list[dict[str, Any]],
    *,
    spec: dict[str, Any],
    manifest: dict[str, Any],
    contracts: list[dict[str, Any]],
    providers: dict[str, str],
    work_package_by_task: dict[str, str],
    config_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """5.2.3：从责任、最终 DAG、Spec 与 Manifest 唯一确定性生成 coverage。"""
    closure = _requirement_closure(tasks)
    enabled = enabled_test_nodeids(manifest, config_snapshot)
    s5_contracts = {
        item["id"] for item in contracts if item.get("ready_gate") == "s5"
    }
    roles: dict[str, dict[str, list[str]]] = {}
    for task in tasks:
        for item in task.get("requirement_responsibilities", []):
            bucket = roles.setdefault(
                str(item["req_id"]),
                {"primary": [], "supporting": []},
            )
            bucket[str(item["role"])].append(task["id"])

    coverage_tests: list[dict[str, Any]] = []
    tests_by_req: dict[str, set[str]] = {}
    normative_test_gates: dict[str, set[str]] = {}
    for entry in sorted(manifest["tests"], key=lambda item: str(item["nodeid"])):
        nodeid = str(entry["nodeid"])
        gate = str(entry["gate"])
        task_id: str | None = None
        if gate == "s5":
            non_s5 = sorted(set(entry["required_contracts"]) - s5_contracts)
            if non_s5:
                raise PlanDraftError(
                    f"{nodeid}: gate=s5 只能依赖 s5-ready contracts，实际 {non_s5}"
                )
        if gate == "task":
            task_id = _earliest_task_gate(
                entry,
                tasks=tasks,
                closure=closure,
                providers=providers,
                s5_contracts=s5_contracts,
                roles=roles,
            )
        coverage_tests.append(
            {
                "nodeid": nodeid,
                "gate": gate,
                "enabled": nodeid in enabled,
                "task_id": task_id,
            }
        )
        for req_id in entry["req_ids"]:
            tests_by_req.setdefault(str(req_id), set()).add(nodeid)
            normative_test_gates.setdefault(str(req_id), set()).add(gate)

    requirements: list[dict[str, Any]] = []
    for requirement in spec["requirements"]:
        req_id = str(requirement["id"])
        bucket = roles.get(req_id, {"primary": [], "supporting": []})
        primaries = bucket["primary"]
        if requirement["level"] == "DEFINITION":
            if len(primaries) > 1:
                raise PlanDraftError(f"{req_id}: DEFINITION 不得有多个 primary task")
        elif len(primaries) != 1:
            raise PlanDraftError(
                f"{req_id}: 非 DEFINITION 需求必须恰有一个 primary task"
            )
        if requirement["level"] in {"MUST", "MUST NOT"} and not (
            normative_test_gates.get(req_id, set()) & {"task", "s7_only"}
        ):
            raise PlanDraftError(
                f"{req_id}: MUST/MUST NOT 必须关联至少一个 gate=task 或 s7_only 的规范测试"
            )
        primary_task_id = primaries[0] if primaries else None
        requirements.append(
            {
                "req_id": req_id,
                "primary_work_package_id": (
                    work_package_by_task[primary_task_id] if primary_task_id else None
                ),
                "primary_task_id": primary_task_id,
                "supporting_task_ids": sorted(set(bucket["supporting"])),
                "test_nodeids": sorted(tests_by_req.get(req_id, set())),
            }
        )
    return {"requirements": requirements, "tests": coverage_tests}


def _earliest_task_gate(
    entry: dict[str, Any],
    *,
    tasks: list[dict[str, Any]],
    closure: dict[str, set[str]],
    providers: dict[str, str],
    s5_contracts: set[str],
    roles: dict[str, dict[str, list[str]]],
) -> str:
    """5.2.3：选择稳定拓扑序中第一个满足 contract 与 REQ 闭包的任务。"""
    required_providers: set[str] = set()
    for contract_id in entry["required_contracts"]:
        if contract_id in s5_contracts:
            continue
        provider = providers.get(str(contract_id))
        if provider is None:
            raise PlanDraftError(
                f"{entry['nodeid']}: contract {contract_id} 没有 provider task"
            )
        required_providers.add(provider)
    required_implementers: set[str] = set()
    for req_id in entry["req_ids"]:
        bucket = roles.get(str(req_id))
        if not bucket or not bucket["primary"]:
            raise PlanDraftError(
                f"{entry['nodeid']}: 需求 {req_id} 没有 primary 实现任务"
            )
        required_implementers |= set(bucket["primary"]) | set(bucket["supporting"])
    needed = required_providers | required_implementers
    for task in tasks:
        if needed <= closure[task["id"]]:
            return str(task["id"])
    candidate_closures = {
        str(task["id"]): sorted(closure[str(task["id"])])
        for task in tasks
        if required_providers & closure[str(task["id"])]
    }
    raise PlanDraftError(
        f"{entry['nodeid']}: 不存在同时满足 contract 与 REQ 闭包的合法 task gate；"
        f"required_provider_tasks={sorted(required_providers)}，"
        f"required_implementation_tasks={sorted(required_implementers)}，"
        f"provider_candidate_closures={candidate_closures}。"
        "全局重规划必须调整工作包 contract 依赖或 requirement primary/supporting "
        "分配，使至少一个稳定下游任务同时包含上述完整祖先集合。"
    )


def _inject_requirement_context_refs(item: dict[str, Any]) -> None:
    """6.4.5 步骤 5：由责任字段确定性补入直接 requirement context_refs。"""
    refs = item.setdefault("context_refs", [])
    present = {
        str(ref["id"]) for ref in refs if ref.get("kind") == "requirement"
    }
    missing = sorted(
        {
            str(entry["req_id"])
            for entry in item.get("requirement_responsibilities", [])
        }
        - present
    )
    refs.extend({"kind": "requirement", "id": req_id} for req_id in missing)


def _required_build_variant_ids(constraints: dict[str, Any]) -> list[str]:
    required = sorted(
        str(item["id"])
        for item in constraints["build_variants"]
        if item.get("required")
    )
    if not required:
        raise PlanDraftError("Language Profile 未声明任何 required 构建变体")
    return required


def _inject_build_variants(
    tasks: list[dict[str, Any]],
    *,
    constraints: dict[str, Any],
    coverage: dict[str, Any],
) -> None:
    """6.4.5 步骤 5/7：注入精确构建变体，并反向注入 enabled task gate nodeid。

    任务必须通过 Language Profile 的 required 变体；其绑定测试额外要求的变体
    并入同一集合，因此任何任务的 ``build_variant_ids`` 都非空（5.2.2）。
    """
    variants_by_test = {
        str(item["nodeid"]): list(item["build_variant_ids"])
        for item in constraints["tests"]
    }
    bound: dict[str, list[str]] = {}
    for row in coverage["tests"]:
        if row["gate"] != "task" or not row["enabled"]:
            continue
        bound.setdefault(str(row["task_id"]), []).append(str(row["nodeid"]))
    base = _required_build_variant_ids(constraints)
    for task in tasks:
        nodeids = sorted(bound.get(task["id"], []))
        variants = set(base)
        for nodeid in nodeids:
            variants |= set(variants_by_test[nodeid])
        task["acceptance"] = {
            "build_variant_ids": sorted(variants),
            "tests": nodeids,
        }


def _check_work_package_dependencies(
    draft: PlanDraftIR,
    providers: dict[str, tuple[str, str]],
) -> None:
    """5.2.2：工作包依赖必须恰等于跨包 task-ready contract 的 provider 包集合。"""
    for package in draft.work_packages:
        expected = {
            providers[contract_id][0]
            for contract_id in package["consumes_contracts"]
            if contract_id in providers and providers[contract_id][0] != package["id"]
        }
        if set(package["depends_on"]) != expected:
            raise PlanDraftError(
                f"{package['id']}: depends_on 必须等于 {sorted(expected)}"
            )


def link_plan_draft(
    draft: PlanDraftIR,
    *,
    spec: dict[str, Any],
    manifest: dict[str, Any],
    constraints: dict[str, Any],
    input_refs: dict[str, Any],
    config_snapshot: dict[str, Any],
    review: dict[str, Any] | None = None,
) -> LinkResult:
    """按 6.4.5 的九步把 PlanDraftIR 确定性链接为 canonical candidate Plan。

    ``review`` 只在 SEAL_AND_PUBLISH 由控制器写入最终评审结论；链接阶段默认
    留空 pass 壳，Linker 自身不评审。
    """
    _check_set_equalities(draft)
    providers = _resolve_provider_tasks(draft)
    _check_work_package_dependencies(draft, providers)
    linked = link_tasks(
        _shards_from_ir(draft),
        extra_deps=_contract_edges(draft, providers),
    )
    tasks = linked.tasks
    work_packages = deepcopy(draft.work_packages)
    for item in work_packages:
        _inject_requirement_context_refs(item)
    for task in tasks:
        _inject_requirement_context_refs(task)
    provider_task_ids = {
        contract_id: linked.task_ids[key] for contract_id, key in providers.items()
    }
    architecture = deepcopy(draft.architecture)
    for contract in architecture["contracts"]:
        # ArchitectureDraft 只预留 provider 工作包；最终 provider task 由本函数注入。
        contract.pop("provider_work_package_id", None)
        if contract["id"] in provider_task_ids:
            contract["provider_task_id"] = provider_task_ids[contract["id"]]

    coverage = build_coverage(
        tasks,
        spec=spec,
        manifest=manifest,
        contracts=architecture["contracts"],
        providers=provider_task_ids,
        work_package_by_task={task["id"]: task["work_package"] for task in tasks},
        config_snapshot=config_snapshot,
    )
    _inject_build_variants(tasks, constraints=constraints, coverage=coverage)
    blueprint = compile_delivery_blueprint(
        constraints, architecture, work_packages, tasks
    )
    plan = {
        "schema_version": "3.0",
        "input_refs": deepcopy(input_refs),
        "delivery_blueprint_sha256": blueprint["content_sha256"],
        "architecture": architecture,
        "work_packages": work_packages,
        "tasks": tasks,
        "coverage": coverage,
        "review": deepcopy(review)
        if review is not None
        else {"verdict": "pass", "unresolved_minor_issues": []},
    }
    link_report = {
        "schema_version": "1.0",
        "task_order": [task["id"] for task in tasks],
        "task_ids": {
            f"{work_package_id}/{local_id}": task_id
            for (work_package_id, local_id), task_id in sorted(
                linked.task_ids.items()
            )
        },
        "contract_provider_task_ids": dict(sorted(provider_task_ids.items())),
        "task_edges": [
            {"task_id": task["id"], "depends_on": list(task["depends_on"])}
            for task in tasks
            if task["depends_on"]
        ],
        "delivery_blueprint_sha256": blueprint["content_sha256"],
        "coverage_counts": {
            "requirements": len(coverage["requirements"]),
            "tests": len(coverage["tests"]),
            "task_gated_tests": sum(
                1 for row in coverage["tests"] if row["gate"] == "task"
            ),
            "enabled_tests": sum(1 for row in coverage["tests"] if row["enabled"]),
        },
    }
    return LinkResult(plan=plan, blueprint=blueprint, link_report=link_report)
