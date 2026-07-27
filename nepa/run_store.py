"""runs/ 目录布局与 run.json 存取（system_design.md 4.4、4.8、5.6.2）。

run.json 的所有写入一律走"临时文件 + os.replace 原子改名"（4.8），
字段严格按 5.6.2；阶段状态机 pending/running/done/failed/skipped。
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"  # 5.6.2

Entry = Literal["spec-run", "doc-run"]  # 5.6.2 entry 枚举
StageStatus = Literal["pending", "running", "done", "failed", "skipped"]  # 4.8
Outcome = Literal["success", "degraded", "failed"]  # 9.1.2 三值

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
    "spec",
    "plan",
    "workspace",
    "test_results",
    "repair",
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


class BudgetUsed(BaseModel):
    """预算消耗累计（5.6.2 budget_used，随运行原子更新）。"""

    model_config = ConfigDict(extra="forbid")

    wall_clock_s: float = 0.0
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0


class RunMeta(BaseModel):
    """run.json 全量结构，字段严格按 5.6.2。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    run_id: str
    entry: Entry
    created_at: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    stages: dict[str, StageState]
    budget_used: BudgetUsed = Field(default_factory=BudgetUsed)
    flags: dict[str, Any] = Field(default_factory=dict)
    outcome: Outcome | None = None  # 终态写入（9.1.2）
    exit_code: int | None = None  # 终态写入（8.7）


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
        return cls(run_dir, RunMeta.model_validate(raw))

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
        """记录输入（spec_path/doc_path/scope_path 及各自 sha256，5.6.2）。"""
        self._meta.inputs = dict(inputs)
        self.save()

    def set_config_snapshot(self, snapshot: dict[str, Any]) -> None:
        """写入配置快照；快照只含密钥环境变量名，禁止含密钥值（8.3）。"""
        self._meta.config_snapshot = dict(snapshot)
        self.save()

    def set_stage_status(
        self, stage: str, status: StageStatus, *, error: str | None = None
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
        now = _utc_now_iso()
        state.status = status
        if status == "running":
            state.started_at = now
            state.ended_at = None
            state.error = None  # failed→running 重试时清除旧错误（4.8）
        elif status in ("done", "failed", "skipped"):
            state.ended_at = now
            state.error = error
        self.save()

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

    def finalize(self, outcome: Outcome, exit_code: int) -> None:
        """写入终态 outcome 与 exit_code（9.1.2、8.7），两者绑定校验。"""
        if outcome not in _OUTCOME_EXIT_CODES:
            raise ValueError(f"未知 outcome: {outcome!r}")
        expected = _OUTCOME_EXIT_CODES[outcome]
        if exit_code != expected:
            raise ValueError(f"outcome {outcome!r} 对应退出码 {expected}（8.7），实际 {exit_code}")
        self._meta.outcome = outcome
        self._meta.exit_code = exit_code
        self.save()


def create_run(
    runs_root: str | Path,
    protocol: str,
    entry: str,
    *,
    inputs: dict[str, Any] | None = None,
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

    meta = RunMeta(
        run_id=run_id,
        entry=entry,  # type: ignore[arg-type]  # 已在上方校验枚举
        created_at=now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        inputs=dict(inputs or {}),
        config_snapshot=dict(config_snapshot or {}),
        stages={name: StageState() for name in STAGE_NAMES},
    )
    store = RunStore(run_dir, meta)
    store.save()
    return store
