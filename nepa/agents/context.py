"""Model working context: current observations and indivisible tool transactions."""
from __future__ import annotations

import json
from typing import Any

from ..config import CoderConfig
from ..llm.client import LLMRequest, LLMRequestError
from ..llm.providers.openai_compat import OpenAICompatibleProvider
from ..tools.workspace import WorkspaceTools


class CodingContext:
    def __init__(self, system: str, base: dict[str, Any], coder: CoderConfig, tools: WorkspaceTools):
        self.system, self.base, self.coder, self.tools = system, base, coder, tools
        self.observations: dict[str, dict[str, Any]] = {}
        self.transactions: list[tuple[str, dict[str, Any]]] = []
        self.evicted_transactions = 0
        self.latest_diagnostic: dict[str, Any] | None = None

    def record(self, response: str, feedback: dict[str, Any], *, action: dict[str, Any] | None = None) -> dict[str, Any]:
        result = feedback.get("tool_result", {})
        if "build" in result or "verification" in result or result.get("error") or result.get("returncode", 0) != 0:
            self.latest_diagnostic = feedback
        if action and action["tool"] == "read_file" and "file_sha256" in result:
            read = {**action["arguments"], "path": result["path"],
                    "offset": result["offset"], "limit": action["arguments"].get("limit", 16000)}
            key = json.dumps(read, sort_keys=True)
            self.observations[key] = {"read": read, "result": result, "evidence_ref": feedback["evidence_ref"]}
            feedback = {**feedback, "tool_result": {"read": read, "file_sha256": result["file_sha256"],
                                                   "observation": "Content is in current_observations while this file version is current.",
                                                   "next_offset": result["next_offset"]}}
        self.transactions.append((response, feedback))
        return feedback

    def request(self, progress: dict[str, Any]) -> LLMRequest:
        # The filesystem, not a prior read or agent claim, determines freshness.
        versions: dict[str, str | None] = {}
        for key, observation in list(self.observations.items()):
            path = observation["result"]["path"]
            if path not in versions:
                try:
                    versions[path] = self.tools.file_sha256(path)
                except (OSError, ValueError):
                    versions[path] = None
            if versions[path] != observation["result"]["file_sha256"]:
                del self.observations[key]

        while True:
            initial = {**self.base, "current_observations": list(self.observations.values()),
                       "latest_observed_diagnostic": self.latest_diagnostic,
                       "evicted_transactions": self.evicted_transactions}
            messages = [{"role": "user", "content": json.dumps(initial, ensure_ascii=False)}]
            for response, feedback in self.transactions:
                messages.extend([{"role": "assistant", "content": response},
                                 {"role": "user", "content": json.dumps(feedback, ensure_ascii=False)}])
            messages[-1]["content"] += "\nDecision budget:" + json.dumps(progress)
            request = LLMRequest(role="coder", system=self.system, user=json.dumps(self.base, ensure_ascii=False),
                                 messages=messages, temperature=self.coder.temperature, max_tokens=self.coder.max_tokens)
            size = len(json.dumps(OpenAICompatibleProvider._payload(request, self.coder.model, False), ensure_ascii=False).encode())
            if size <= self.coder.context_max_bytes:
                return request
            if len(self.transactions) > 1:
                self.transactions.pop(0)
                self.evicted_transactions += 1
                continue
            raise LLMRequestError(
                f"context capacity exhausted: current task facts, {len(self.observations)} file observations "
                f"and latest tool result require {size} bytes, limit={self.coder.context_max_bytes}. "
                "No current observation or latest diagnostic was silently dropped. "
                "Use a larger configured window or narrower source reads in a new run.")
