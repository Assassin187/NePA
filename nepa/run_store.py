"""Run v2 目录布局与 run.json 存取（system_design.md 4.4、4.8、5.6.2）。

run.json 的所有写入一律走"临时文件 + os.replace 原子改名"（4.8），
字段严格按 5.6.2；阶段状态机 pending/running/done/failed/skipped。
"""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nepa.canonical import canonical_sha256
from nepa.reasons import Reason

SCHEMA_VERSION = "2.0"  # 5.6.2

Entry = Literal["spec-run", "doc-run"]  # 5.6.2 entry 枚举
StageStatus = Literal["pending", "running", "done", "failed", "skipped"]  # 4.8
Outcome = Literal["success", "degraded", "failed"]  # 9.1.2 三值
TerminationKind = Literal["completed", "planned_stop", "controlled_exit", "internal_error"]
ControlledStage = Literal["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8"]

_ENTRIES: frozenset[str] = frozenset({"spec-run", "doc-run"})

# run.json 的稳定工件键。具体实现模块名（如 s1_ingest.py）不得泄漏进
# 跨阶段工件；这组键与 run.schema.json/设计文档中的 S1～S9 对齐。
STAGE_NAMES: tuple[str, ...] = tuple(f"s{i}" for i in range(1, 10))

# 合法状态迁移（4.8）。done/skipped 为终态：重复执行已完成阶段
# 应是无害空操作而非状态回退；failed→running 供 resume 重试（4.8）。
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"running", "skipped"}),
    "running": frozenset({"done", "failed"}),
    "failed": frozenset({"running"}),
    "done": frozenset(),
    "skipped": frozenset(),
}

# outcome ↔ exit_code 绑定（8.7、9.1.2）；退出码 1（NePA 自身错误）无 outcome
_OUTCOME_EXIT_CODES: dict[str, int] = {"success": 0, "degraded": 10, "failed": 20}

# 4.4 运行目录树中需要预建的子目录（doc/ 仅 doc-run；round_xxx 由测试阶段按轮创建）
_COMMON_SUBDIRS: tuple[str, ...] = (
    "inputs",
    "spec",
    "plan/_s4",
    "workspace",
    "test_results/task_evidence",
    "repair/evidence",
    "report",
    "trace/prompts",
    "trace/outputs",
    "cache",
)


class InvalidTransitionError(ValueError):
    """非法的阶段状态迁移（4.8 状态机）。"""


def _utc_now_iso() -> str:
    """UTC ISO8601 时间戳（5.6.2 created_at 等）。"""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class StageState(BaseModel):
    """单阶段状态项（5.6.2 stages）。"""

    model_config = ConfigDict(extra="forbid")

    status: StageStatus = "pending"
    started_at: str | None = None
    ended_at: str | None = None
    error: str | None = None
    output_refs: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_status_payload(self) -> StageState:
        if self.error is not None and self.status != "failed":
            raise ValueError("stage error 只允许出现在 failed 状态")
        if self.output_refs is not None and (self.status != "done" or not self.output_refs):
            raise ValueError("stage output_refs 只允许出现在 done 状态且不得为空")
        return self


class SourceFileRef(BaseModel):
    """调用方源文件的原始字节引用。"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AssetInputRef(BaseModel):
    """run 内冻结解析描述的版本化引用。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    version: str = Field(min_length=1)
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _CommonInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_profile: AssetInputRef
    language_profile: AssetInputRef
    test_bundle: AssetInputRef

    @model_validator(mode="after")
    def _check_frozen_asset_paths(self) -> _CommonInputs:
        expected = {
            "target_profile": "inputs/target.json",
            "language_profile": "inputs/language.json",
            "test_bundle": "inputs/test_bundle.json",
        }
        for name, path in expected.items():
            actual = getattr(self, name).path
            if actual != path:
                raise ValueError(f"inputs.{name}.path 必须为 {path!r}，实际 {actual!r}")
        return self


class SpecRunInputs(_CommonInputs):
    """spec-run：协议规格源 + 三项 run 内冻结描述。"""

    spec: SourceFileRef


class DocRunInputs(_CommonInputs):
    """doc-run：文档与必填 scope 源 + 三项 run 内冻结描述。"""

    doc: SourceFileRef
    scope: SourceFileRef


class BudgetUsed(BaseModel):
    """预算消耗累计（5.6.2 budget_used，随运行原子更新）。"""

    model_config = ConfigDict(extra="forbid")

    wall_clock_s: float = 0.0
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0


class TerminationRequest(BaseModel):
    """受控退出的单次决策记录；S9 只消费，不重新推导原因。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["controlled_exit"] = "controlled_exit"
    stage: ControlledStage
    requested_at: str
    reason: Reason


class RunMeta(BaseModel):
    """run.json 全量结构，字段严格按 5.6.2。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    run_id: str
    entry: Entry
    created_at: str
    inputs: SpecRunInputs | DocRunInputs
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    config_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stages: dict[str, StageState]
    budget_used: BudgetUsed = Field(default_factory=BudgetUsed)
    flags: dict[str, Any] = Field(default_factory=dict)
    termination_request: TerminationRequest | None = None
    termination_kind: TerminationKind | None = None
    outcome: Outcome | None = None  # 终态写入（9.1.2）
    exit_code: int | None = None  # 终态写入（8.7）

    @model_validator(mode="after")
    def _check_entry_inputs_and_hash(self) -> RunMeta:
        if self.entry == "spec-run" and not isinstance(self.inputs, SpecRunInputs):
            raise ValueError("spec-run inputs 必须含 spec，且禁止 doc/scope")
        if self.entry == "doc-run" and not isinstance(self.inputs, DocRunInputs):
            raise ValueError("doc-run inputs 必须含 doc/scope，且禁止 spec")
        expected_hash = canonical_sha256(self.config_snapshot)
        if self.config_snapshot_sha256 != expected_hash:
            raise ValueError("config_snapshot_sha256 与 config_snapshot canonical 内容不一致")
        if set(self.stages) != set(STAGE_NAMES):
            raise ValueError(f"stages 必须恰好包含 {STAGE_NAMES}")
        request = self.termination_request
        if request is not None and self.stages[request.stage].status not in ("failed", "pending"):
            raise ValueError(
                "termination_request.stage 对应阶段必须为 failed 或 pending"
            )
        if self.termination_kind is None:
            if self.outcome is not None or self.exit_code is not None:
                raise ValueError("未终结 run 禁止写 outcome/exit_code")
        elif self.termination_kind == "planned_stop":
            if request is not None or self.outcome is not None or self.exit_code != 0:
                raise ValueError(
                    "planned_stop 必须 exit_code=0，且不得写 termination_request/outcome"
                )
        elif self.termination_kind == "internal_error":
            if self.outcome is not None or self.exit_code != 1:
                raise ValueError("internal_error 必须 exit_code=1 且不得写 outcome")
        elif self.termination_kind == "controlled_exit":
            if request is None:
                raise ValueError("controlled_exit 必须有 termination_request")
            if self.outcome not in ("degraded", "failed"):
                raise ValueError("controlled_exit outcome 必须为 degraded/failed")
            if self.exit_code != _OUTCOME_EXIT_CODES[self.outcome]:
                raise ValueError("outcome 与 exit_code 不匹配")
        else:
            if request is not None:
                raise ValueError("completed 禁止写 termination_request")
            if self.outcome not in _OUTCOME_EXIT_CODES:
                raise ValueError(f"{self.termination_kind} 必须写合法 outcome")
            if self.exit_code != _OUTCOME_EXIT_CODES[self.outcome]:
                raise ValueError("outcome 与 exit_code 不匹配")
        return self


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """写临时文件 + os.replace 原子改名（4.8）。临时文件与目标同目录。"""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


class RunStore:
    """单个运行目录的 run.json 读写器（4.4、4.8、5.6.2）。

    所有变更方法先改内存模型、再原子落盘，落盘失败即抛错不留半态文件。
    """

    def __init__(self, run_dir: str | Path, meta: RunMeta) -> None:
        self.run_dir: Path = Path(run_dir)
        self.run_json_path: Path = self.run_dir / "run.json"
        self._meta: RunMeta = meta

    @classmethod
    def load(cls, run_dir: str | Path) -> RunStore:
        """从磁盘加载已有运行（resume/status 入口，4.8）。"""
        run_dir = Path(run_dir)
        raw = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        meta = RunMeta.model_validate(raw)
        if meta.run_id != run_dir.name:
            raise ValueError(f"run_id {meta.run_id!r} 与运行目录 {run_dir.name!r} 不一致")
        return cls(run_dir, meta)

    @property
    def run_id(self) -> str:
        return self._meta.run_id

    @property
    def meta(self) -> RunMeta:
        return self._meta

    def read(self) -> dict[str, Any]:
        """返回当前 run.json 内容（与落盘形态一致的 dict）。"""
        return self._dump()

    def _dump(self) -> dict[str, Any]:
        # 可选时间/错误/终态字段在未赋值时必须省略；JSON Schema 将它们定义
        # 为“可选 string/enum”，而不是显式 null。
        return self._meta.model_dump(mode="json", exclude_none=True)

    def save(self) -> None:
        """原子落盘 run.json（4.8）。"""
        _atomic_write_json(self.run_json_path, self._dump())

    # ---- 5.6.2 各字段写入接口 -------------------------------------------

    def set_inputs(self, inputs: dict[str, Any]) -> None:
        """替换并校验 entry 绑定的嵌套冻结输入引用（5.6.2）。"""
        model = SpecRunInputs if self._meta.entry == "spec-run" else DocRunInputs
        self._meta.inputs = model.model_validate(inputs)
        self.save()

    def set_config_snapshot(self, snapshot: dict[str, Any]) -> None:
        """写入配置快照及同一对象的 canonical SHA-256（8.3）。"""
        copied = deepcopy(snapshot)
        snapshot_sha256 = canonical_sha256(copied)
        self._meta.config_snapshot = copied
        self._meta.config_snapshot_sha256 = snapshot_sha256
        self.save()

    def set_stage_status(
        self,
        stage: str,
        status: StageStatus,
        *,
        error: str | None = None,
        output_refs: dict[str, Any] | None = None,
    ) -> None:
        """按 4.8 状态机迁移阶段状态并原子落盘；非法迁移抛错。"""
        if stage not in self._meta.stages:
            raise KeyError(f"未知阶段: {stage!r}（合法值: {STAGE_NAMES}）")
        state = self._meta.stages[stage]
        if status not in _ALLOWED_TRANSITIONS:
            raise ValueError(f"未知阶段状态: {status!r}")
        if status not in _ALLOWED_TRANSITIONS[state.status]:
            raise InvalidTransitionError(f"阶段 {stage!r} 不允许 {state.status!r} → {status!r}")
        if error is not None and status != "failed":
            raise ValueError("error 只随 failed 状态写入")
        if output_refs is not None and (status != "done" or not output_refs):
            raise ValueError("非空 output_refs 只随 done 状态原子写入")
        now = _utc_now_iso()
        state.status = status
        if status == "running":
            state.started_at = now
            state.ended_at = None
            state.error = None  # failed→running 重试时清除旧错误（4.8）
            state.output_refs = None
        elif status in ("done", "failed", "skipped"):
            state.ended_at = now
            state.error = error
            state.output_refs = dict(output_refs) if output_refs is not None else None
        self.save()

    def first_incomplete_stage(self) -> str | None:
        """按固定流水线顺序返回首个未完成阶段；done/skipped 视为完成。"""
        for stage in STAGE_NAMES:
            if self._meta.stages[stage].status not in ("done", "skipped"):
                return stage
        return None

    def begin_stage(self, stage: str) -> bool:
        """进入或恢复阶段；已完成阶段返回 False，running 恢复返回 True。"""
        if stage not in self._meta.stages:
            raise KeyError(f"未知阶段: {stage!r}（合法值: {STAGE_NAMES}）")
        status = self._meta.stages[stage].status
        if status in ("done", "skipped"):
            return False
        if status in ("pending", "failed"):
            self.set_stage_status(stage, "running")
        return True

    def add_budget_used(
        self,
        *,
        wall_clock_s: float = 0.0,
        cost_usd: float = 0.0,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> BudgetUsed:
        """累加预算消耗并原子落盘（5.6.2 budget_used）。"""
        if min(wall_clock_s, cost_usd, tokens_in, tokens_out) < 0:
            raise ValueError("预算增量不允许为负")
        used = self._meta.budget_used
        used.wall_clock_s += wall_clock_s
        used.cost_usd += cost_usd
        used.tokens_in += tokens_in
        used.tokens_out += tokens_out
        self.save()
        return used

    def set_flag(self, name: str, value: Any) -> None:
        """写入运行标志（5.6.2 flags，如 degraded_segmentation）。"""
        self._meta.flags[name] = value
        self.save()

    def request_controlled_exit(
        self,
        stage: ControlledStage,
        code: str,
        detail: str,
        *,
        error: str | None = None,
    ) -> TerminationRequest:
        """原子记录受控退出决定及触发阶段失败状态（4.7）。

        pending 表示在阶段入口预算门耗尽；running 会与 request 在同一次
        run.json 写入中转为 failed。failed 可用于已由阶段控制器记错的路径。
        """
        if self._meta.termination_kind is not None:
            raise InvalidTransitionError("run 已终结，禁止新增 termination_request")
        existing = self._meta.termination_request
        requested_reason = Reason(code=code, detail=detail)
        if existing is not None:
            if existing.stage == stage and existing.reason == requested_reason:
                return existing
            raise InvalidTransitionError("run 已有不同的 termination_request")
        state = self._meta.stages[stage]
        if state.status not in ("pending", "running", "failed"):
            raise InvalidTransitionError(
                f"termination_request.stage {stage!r} 状态必须为 failed/pending，"
                f"或可原子转 failed 的 running；实际 {state.status!r}"
            )
        now = _utc_now_iso()
        if state.status == "running":
            state.status = "failed"
            state.ended_at = now
            state.error = error or detail
            state.output_refs = None
        request = TerminationRequest(
            stage=stage,
            requested_at=now,
            reason=requested_reason,
        )
        self._meta.termination_request = request
        self.save()
        return request

    def recover_orphaned_running_stages(self) -> tuple[str, ...]:
        """确认原 controller 已不存在后，把所有 running 原子记为 crashed。

        调用方负责活跃进程互斥检查；此方法不创建 termination_request。
        """
        recovered: list[str] = []
        now = _utc_now_iso()
        for stage, state in self._meta.stages.items():
            if state.status != "running":
                continue
            state.status = "failed"
            state.ended_at = now
            state.error = "process crashed mid-stage"
            state.output_refs = None
            recovered.append(stage)
        if recovered:
            self.save()
        return tuple(recovered)

    def finalize(
        self,
        termination_kind: TerminationKind,
        exit_code: int,
        *,
        outcome: Outcome | None = None,
    ) -> None:
        """按 Run v2 条件终态一次写入 termination/outcome/exit_code。"""
        if self._meta.termination_kind is not None:
            raise InvalidTransitionError("run 已终结，禁止重复 finalize")
        request = self._meta.termination_request
        if termination_kind == "planned_stop":
            if request is not None or exit_code != 0 or outcome is not None:
                raise ValueError(
                    "planned_stop 必须 exit_code=0，且不得有 termination_request/outcome"
                )
        elif termination_kind == "internal_error":
            if exit_code != 1 or outcome is not None:
                raise ValueError("internal_error 必须 exit_code=1 且不得写 outcome")
        elif termination_kind == "controlled_exit":
            if request is None:
                raise ValueError("controlled_exit 必须先写 termination_request")
            if outcome not in ("degraded", "failed"):
                raise ValueError("controlled_exit outcome 必须为 degraded/failed")
            expected = _OUTCOME_EXIT_CODES[outcome]
            if exit_code != expected:
                raise ValueError(
                    f"outcome {outcome!r} 对应退出码 {expected}（8.7），实际 {exit_code}"
                )
        elif termination_kind == "completed":
            if request is not None:
                raise ValueError("completed 禁止有 termination_request")
            if outcome not in _OUTCOME_EXIT_CODES:
                raise ValueError(f"{termination_kind} 必须写合法 outcome")
            expected = _OUTCOME_EXIT_CODES[outcome]
            if exit_code != expected:
                raise ValueError(
                    f"outcome {outcome!r} 对应退出码 {expected}（8.7），实际 {exit_code}"
                )
        else:
            raise ValueError(f"未知 termination_kind: {termination_kind!r}")
        self._meta.termination_kind = termination_kind
        self._meta.outcome = outcome
        self._meta.exit_code = exit_code
        self.save()


def create_run(
    runs_root: str | Path,
    protocol: str,
    entry: str,
    *,
    inputs: dict[str, Any],
    config_snapshot: dict[str, Any] | None = None,
) -> RunStore:
    """在 runs_root 下按 4.4 建立运行目录树并写初始 run.json。

    目录命名 <UTC时间戳>_<协议>_<入口>；同分钟冲突时在时间戳后追加
    "-N" 去重（文档未规定冲突处理，取不破坏三段结构的最小偏离）。
    """
    if entry not in _ENTRIES:
        raise ValueError(f"entry 必须为 spec-run/doc-run（5.6.2），实际 {entry!r}")
    if not protocol or "/" in protocol or "_" in protocol or protocol != protocol.strip():
        # 协议名参与 run_id 三段命名，禁止路径分隔符与下划线（4.4）
        raise ValueError(f"非法协议名: {protocol!r}")

    input_model = SpecRunInputs if entry == "spec-run" else DocRunInputs
    validated_inputs = input_model.model_validate(inputs)
    snapshot = deepcopy(config_snapshot or {})
    snapshot_sha256 = canonical_sha256(snapshot)

    root = Path(runs_root)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%dT%H%MZ")  # 4.4 示例：20260726T1432Z
    run_id = f"{stamp}_{protocol}_{entry}"
    n = 2
    while True:
        run_dir = root / run_id
        try:
            run_dir.mkdir()
            break
        except FileExistsError:
            run_id = f"{stamp}-{n}_{protocol}_{entry}"
            n += 1

    subdirs = _COMMON_SUBDIRS + (("doc",) if entry == "doc-run" else ())  # 4.4
    for sub in subdirs:
        (run_dir / sub).mkdir(parents=True)

    stages = {name: StageState() for name in STAGE_NAMES}
    if entry == "spec-run":
        for name in ("s1", "s2", "s3"):
            stages[name] = StageState(status="skipped")

    meta = RunMeta(
        run_id=run_id,
        entry=entry,  # type: ignore[arg-type]  # 已在上方校验枚举
        created_at=now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        inputs=validated_inputs,
        config_snapshot=snapshot,
        config_snapshot_sha256=snapshot_sha256,
        stages=stages,
    )
    store = RunStore(run_dir, meta)
    store.save()
    return store
