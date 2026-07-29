"""Run v2 与 Report v2 共用的机器可读 reason 值对象。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Reason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    detail: str = Field(min_length=1)


def reason_dict(code: str, detail: str) -> dict[str, str]:
    """校验并返回稳定 JSON 形态；所有 producer 必须经此入口。"""
    reason = Reason(code=code, detail=detail)
    return {"code": reason.code, "detail": reason.detail}
