"""Test round 的 pending WAL、权威 index 与 crash reconciliation（设计 5.4）。"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from nepa.canonical import atomic_write_canonical_json, canonical_json_bytes
from nepa.test_summary import TestSummaryValidationError, validate_test_summary

FaultHook = Callable[[str], None]
_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


class RoundStoreError(RuntimeError):
    """round WAL/index 或其文件系统事实损坏。"""


@cache
def _validator(name: str) -> Draft202012Validator:
    path = _SCHEMA_DIR / name
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))


def _validate_schema(value: Any, schema_name: str) -> dict[str, Any]:
    errors = sorted(
        _validator(schema_name).iter_errors(value),
        key=lambda item: [str(part) for part in item.absolute_path],
    )
    if errors:
        detail = "; ".join(
            f"{'/'.join(map(str, item.absolute_path)) or '<root>'}: {item.message}"
            for item in errors[:8]
        )
        raise RoundStoreError(f"{schema_name}: {detail}")
    if not isinstance(value, dict):
        raise RoundStoreError(f"{schema_name}: root must be object")
    return value


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class RoundStore:
    """单写者 round 发布器；调用方必须持有本 run 的 round 互斥锁。"""

    def __init__(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)
        self.test_results_dir = self.run_dir / "test_results"
        self.index_path = self.test_results_dir / "index.json"
        self.wal_path = self.test_results_dir / "pending_round.json"
        self.test_results_dir.mkdir(parents=True, exist_ok=True)

    def _load_json(self, path: Path, description: str) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RoundStoreError(f"{description} 不是合法 JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise RoundStoreError(f"{description} 顶层必须为 object")
        return value

    def load_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {"schema_version": "1.0", "rounds": []}
        value = _validate_schema(
            self._load_json(self.index_path, "round index"),
            "round-index.schema.json",
        )
        rounds = value["rounds"]
        ids = [item["round_id"] for item in rounds]
        if ids != list(range(1, len(ids) + 1)):
            raise RoundStoreError("round index ids 必须从 1 开始严格连续递增")
        seen: set[int] = set()
        for entry in rounds:
            round_id = entry["round_id"]
            parent = entry["parent_round_id"]
            if round_id == 1 and parent is not None:
                raise RoundStoreError("首轮 parent_round_id 必须为 null")
            if round_id > 1 and parent not in seen:
                raise RoundStoreError("非首轮 parent_round_id 必须引用已接受的更早轮次")
            expected_summary = f"test_results/round_{round_id:03d}/summary.json"
            if entry["summary_ref"]["path"] != expected_summary:
                raise RoundStoreError("round index summary_ref path 与 round_id 不一致")
            seen.add(round_id)
        return value

    def _write_index(self, index: dict[str, Any]) -> None:
        self.load_index() if self.index_path.exists() else None
        _validate_schema(index, "round-index.schema.json")
        atomic_write_canonical_json(self.index_path, index)
        _fsync_dir(self.test_results_dir)

    def _load_wal(self) -> dict[str, Any] | None:
        if not self.wal_path.exists():
            return None
        return _validate_schema(
            self._load_json(self.wal_path, "pending round WAL"),
            "pending-round.schema.json",
        )

    @staticmethod
    def _index_entry(wal: dict[str, Any]) -> dict[str, Any]:
        entry = {
            "round_id": wal["round_id"],
            "trigger": wal["trigger"],
            "workspace_head": wal["workspace_head"],
            "parent_round_id": wal["parent_round_id"],
            "summary_ref": wal["summary_ref"],
        }
        if "junit_ref" in wal:
            entry["junit_ref"] = wal["junit_ref"]
        return entry

    def _validate_round_dir(self, directory: Path, wal: dict[str, Any]) -> None:
        summary_path = directory / "summary.json"
        if not summary_path.is_file():
            raise RoundStoreError("round summary.json 不存在")
        if _sha256_file(summary_path) != wal["summary_ref"]["sha256"]:
            raise RoundStoreError("round summary hash 与 WAL 不一致")
        try:
            summary = validate_test_summary(
                self._load_json(summary_path, "round summary")
            )
        except TestSummaryValidationError as exc:
            raise RoundStoreError(f"round summary 不合法: {exc}") from exc
        if summary_path.read_bytes() != canonical_json_bytes(summary):
            raise RoundStoreError("round summary 必须为 canonical JSON")
        for key in (
            "round_id",
            "trigger",
            "workspace_head",
            "workspace_tree",
            "parent_round_id",
        ):
            if summary.get(key) != wal.get(key):
                raise RoundStoreError(f"round summary {key} 与 WAL 不一致")
        context = wal["producer_context"]
        for key in ("task_id", "attempt", "repair_id"):
            if summary.get(key) != context.get(key):
                raise RoundStoreError(f"round summary {key} 与 WAL producer_context 不一致")
        junit_ref = wal.get("junit_ref")
        junit_path = directory / "junit.xml"
        if junit_ref is None:
            if junit_path.exists():
                raise RoundStoreError("WAL 未登记 junit.xml，但 round 目录中存在")
        elif not junit_path.is_file() or _sha256_file(junit_path) != junit_ref["sha256"]:
            raise RoundStoreError("round junit.xml 缺失或 hash 与 WAL 不一致")

    def _validate_wal_paths(self, wal: dict[str, Any]) -> tuple[Path, Path]:
        round_id = wal["round_id"]
        final_relative = f"test_results/round_{round_id:03d}"
        if wal["final_dir"] != final_relative:
            raise RoundStoreError("WAL final_dir 与 round_id 不一致")
        if wal["summary_ref"]["path"] != f"{final_relative}/summary.json":
            raise RoundStoreError("WAL summary_ref path 与 final_dir 不一致")
        if "junit_ref" in wal and wal["junit_ref"]["path"] != f"{final_relative}/junit.xml":
            raise RoundStoreError("WAL junit_ref path 与 final_dir 不一致")
        temp = self.run_dir / wal["temp_dir"]
        final = self.run_dir / wal["final_dir"]
        if temp.parent != self.test_results_dir or final.parent != self.test_results_dir:
            raise RoundStoreError("WAL 目录越出 test_results")
        return temp, final

    def _quarantine_pending_mismatch(self) -> tuple[str, ...]:
        """隔离不可信 WAL 及所有未登记 round 事实，释放后续发布。"""
        orphan_root = self.test_results_dir / "orphaned"
        orphan_root.mkdir(exist_ok=True)
        moved: list[str] = []
        if self.wal_path.exists():
            destination = (
                orphan_root / f"pending_round.json.{uuid.uuid4().hex[:12]}"
            )
            os.replace(self.wal_path, destination)
            moved.append(destination.relative_to(self.run_dir).as_posix())
            _fsync_dir(orphan_root)
            _fsync_dir(self.test_results_dir)
        moved.extend(self.quarantine_orphans())
        return tuple(moved)

    def reconcile_pending(self) -> bool:
        """按持久事实完成 pending WAL；无 WAL 返回 False。"""
        try:
            wal = self._load_wal()
        except RoundStoreError:
            self._quarantine_pending_mismatch()
            return False
        if wal is None:
            return False
        index = self.load_index()
        entry = self._index_entry(wal)
        existing = next(
            (
                item
                for item in index["rounds"]
                if item["round_id"] == wal["round_id"]
            ),
            None,
        )
        try:
            temp, final = self._validate_wal_paths(wal)
            if temp.exists() and final.exists():
                raise RoundStoreError("WAL temp/final round 目录同时存在")
            if existing is not None:
                if existing != entry:
                    raise RoundStoreError("index 已有同 round_id 的不同条目")
                if not final.is_dir() or temp.exists():
                    raise RoundStoreError("index 已登记但 final 目录事实不完整")
                self._validate_round_dir(final, wal)
            else:
                expected_id = len(index["rounds"]) + 1
                if wal["round_id"] != expected_id:
                    raise RoundStoreError("pending WAL round_id 不是 index 下一编号")
                accepted_ids = {item["round_id"] for item in index["rounds"]}
                parent = wal["parent_round_id"]
                if expected_id == 1 and parent is not None:
                    raise RoundStoreError("首轮 parent_round_id 必须为 null")
                if expected_id > 1 and parent not in accepted_ids:
                    raise RoundStoreError("pending WAL parent_round_id 未引用已接受轮次")
                if temp.is_dir():
                    self._validate_round_dir(temp, wal)
                    os.replace(temp, final)
                    _fsync_dir(self.test_results_dir)
                elif final.is_dir():
                    self._validate_round_dir(final, wal)
                else:
                    raise RoundStoreError("pending WAL 的 temp/final 目录均不存在")
                index["rounds"].append(entry)
                self._write_index(index)
        except RoundStoreError:
            # Once an entry is authoritative, corruption is terminal and must
            # remain fail-stop. Before index acceptance, mismatched WAL/facts are
            # untrusted orphans: isolate them and allow the id to be reused.
            if existing is not None:
                raise
            self._quarantine_pending_mismatch()
            return False
        self.wal_path.unlink()
        _fsync_dir(self.test_results_dir)
        return True

    def quarantine_orphans(self) -> tuple[str, ...]:
        """无 WAL 时隔离所有未登记 round/temp 目录，绝不前向接受。"""
        if self.wal_path.exists():
            raise RoundStoreError("pending WAL 存在时必须先 reconcile")
        index = self.load_index()
        accepted = {f"round_{entry['round_id']:03d}" for entry in index["rounds"]}
        candidates = [
            path
            for path in self.test_results_dir.iterdir()
            if path.is_dir()
            and (
                (path.name.startswith("round_") and path.name not in accepted)
                or (path.name.startswith(".round_") and path.name.endswith(".tmp"))
            )
        ]
        if not candidates:
            return ()
        orphan_root = self.test_results_dir / "orphaned"
        orphan_root.mkdir(exist_ok=True)
        moved: list[str] = []
        for path in sorted(candidates):
            destination = orphan_root / f"{path.name}.{uuid.uuid4().hex[:12]}"
            os.replace(path, destination)
            moved.append(destination.relative_to(self.run_dir).as_posix())
        _fsync_dir(orphan_root)
        _fsync_dir(self.test_results_dir)
        return tuple(moved)

    def next_round_id(self) -> int:
        self.reconcile_pending()
        self.quarantine_orphans()
        return len(self.load_index()["rounds"]) + 1

    def publish_round(
        self,
        summary: dict[str, Any],
        *,
        stage: str,
        producer_context: dict[str, Any],
        junit_bytes: bytes | None = None,
        fault_hook: FaultHook | None = None,
    ) -> dict[str, Any]:
        """执行 temp→WAL→rename→index→clear 协议并返回 index entry。"""
        self.reconcile_pending()
        self.quarantine_orphans()
        index = self.load_index()
        value = validate_test_summary(summary)
        round_id = len(index["rounds"]) + 1
        if value["round_id"] != round_id:
            raise RoundStoreError(f"summary round_id 必须为下一编号 {round_id}")
        accepted_ids = {item["round_id"] for item in index["rounds"]}
        parent = value["parent_round_id"]
        if round_id == 1 and parent is not None:
            raise RoundStoreError("首轮 parent_round_id 必须为 null")
        if round_id > 1 and parent not in accepted_ids:
            raise RoundStoreError("parent_round_id 必须引用已接受的更早轮次")
        token = uuid.uuid4().hex[:12]
        temp_name = f".round_{round_id:03d}.{token}.tmp"
        final_name = f"round_{round_id:03d}"
        temp = self.test_results_dir / temp_name
        final = self.test_results_dir / final_name
        temp.mkdir()
        atomic_write_canonical_json(temp / "summary.json", value)
        if junit_bytes is not None:
            (temp / "junit.xml").write_bytes(junit_bytes)
            with (temp / "junit.xml").open("rb") as stream:
                os.fsync(stream.fileno())
        _fsync_dir(temp)
        summary_ref = {
            "path": f"test_results/{final_name}/summary.json",
            "sha256": _sha256_file(temp / "summary.json"),
        }
        wal: dict[str, Any] = {
            "schema_version": "1.0",
            "round_id": round_id,
            "stage": stage,
            "trigger": value["trigger"],
            "producer_context": producer_context,
            "workspace_head": value["workspace_head"],
            "workspace_tree": value["workspace_tree"],
            "parent_round_id": value["parent_round_id"],
            "temp_dir": f"test_results/{temp_name}",
            "final_dir": f"test_results/{final_name}",
            "summary_ref": summary_ref,
        }
        if junit_bytes is not None:
            wal["junit_ref"] = {
                "path": f"test_results/{final_name}/junit.xml",
                "sha256": _sha256_file(temp / "junit.xml"),
            }
        _validate_schema(wal, "pending-round.schema.json")
        self._validate_round_dir(temp, wal)
        if fault_hook is not None:
            fault_hook("after_temp")
        atomic_write_canonical_json(self.wal_path, wal)
        _fsync_dir(self.test_results_dir)
        if fault_hook is not None:
            fault_hook("after_wal")
        os.replace(temp, final)
        _fsync_dir(self.test_results_dir)
        if fault_hook is not None:
            fault_hook("after_rename")
        entry = self._index_entry(wal)
        index["rounds"].append(entry)
        self._write_index(index)
        if fault_hook is not None:
            fault_hook("after_index")
        self.wal_path.unlink()
        _fsync_dir(self.test_results_dir)
        return entry
