"""阶段事件 NDJSON 记录器（设计文档 5.5、8.5、8.6）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class StageEventWriter:
    """向 ``trace/stage_events.ndjson`` 追加确定性工具与阶段事件。"""

    def __init__(self, trace_dir: str | Path, run_id: str) -> None:
        trace = Path(trace_dir)
        trace.mkdir(parents=True, exist_ok=True)
        self.path = trace / "stage_events.ndjson"
        self.run_id = run_id

    def record(self, event: dict[str, Any]) -> dict[str, Any]:
        line = dict(event)
        line.setdefault("ts", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
        line.setdefault("run_id", self.run_id)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        return line

    def __call__(self, event: dict[str, Any]) -> None:
        self.record(event)
