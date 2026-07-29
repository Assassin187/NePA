"""通用代码 Agent prompt 的协议中立静态门（设计文档 8.8、D1.11）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

COMMON_CODE_ROLES = ("coder", "diagnoser", "fixer")

# 模板源码的冻结硬门与设计 8.8 保持逐字同形。
MQTT_IDENTIFIER_PATTERN = re.compile(r"(?i)\bmqtt_[A-Za-z0-9_]*")

# 非 MQTT fixture 的渲染审计更严格：协议名、路径片段和接口前缀均不得残留。
MQTT_RENDERED_RESIDUE_PATTERN = re.compile(r"(?i)\bmqtt(?:_[A-Za-z0-9_]*|\b)")


@dataclass(frozen=True, slots=True)
class PromptLintFinding:
    """一个可定位的 prompt 协议身份残留。"""

    role: str
    check: str
    value: str
    line: int
    column: int


def _find(
    role: str,
    text: str,
    *,
    check: str,
    pattern: re.Pattern[str],
) -> list[PromptLintFinding]:
    findings: list[PromptLintFinding] = []
    for match in pattern.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        line_start = text.rfind("\n", 0, match.start()) + 1
        findings.append(
            PromptLintFinding(
                role=role,
                check=check,
                value=match.group(0),
                line=line,
                column=match.start() - line_start + 1,
            )
        )
    return findings


def lint_prompt_source(role: str, source: str) -> list[PromptLintFinding]:
    """检查通用模板源码中禁止固化的协议标识符。"""
    return _find(
        role,
        source,
        check="template_source_identifier",
        pattern=MQTT_IDENTIFIER_PATTERN,
    )


def lint_non_mqtt_render(role: str, rendered: str) -> list[PromptLintFinding]:
    """检查非 MQTT fixture 渲染结果中的协议身份残留。"""
    return _find(
        role,
        rendered,
        check="non_mqtt_render_residue",
        pattern=MQTT_RENDERED_RESIDUE_PATTERN,
    )


def lint_prompt_directory(prompts_dir: str | Path) -> list[PromptLintFinding]:
    """扫描全部通用代码角色模板；缺失文件由读取错误直接阻断门禁。"""
    directory = Path(prompts_dir)
    findings: list[PromptLintFinding] = []
    for role in COMMON_CODE_ROLES:
        source = (directory / f"{role}.md").read_text(encoding="utf-8")
        findings.extend(lint_prompt_source(role, source))
    return findings
