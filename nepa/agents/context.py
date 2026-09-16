"""Model working context: current observations and indivisible tool transactions."""
from __future__ import annotations

import json
from typing import Any

from ..config import CoderConfig
from ..llm.client import LLMRequest, LLMRequestError, LLMResponse
from ..llm.providers.openai_compat import OpenAICompatibleProvider
from ..tools.workspace import WorkspaceTools


class CodingContext:
    def __init__(self, system: str, base: dict[str, Any], coder: CoderConfig, tools: WorkspaceTools,
                 schema: dict[str, Any] | None = None):
        self.system, self.base, self.coder, self.tools = system, base, coder, tools
        self.observations: dict[str, dict[str, Any]] = {}
        self.schema = schema
        self.transactions: list[tuple[str | LLMResponse, dict[str, Any]]] = []
        self.evicted_transactions = 0
        self.latest_diagnostic: dict[str, Any] | None = None

    @staticmethod
    def _observation_key(read: dict[str, Any]) -> str:
        return json.dumps(read, sort_keys=True)

    @staticmethod
    def _raw_character_interval(read: dict[str, Any], result: dict[str, Any]) -> tuple[int, int] | None:
        if any(key in read for key in ("json_pointer", "start_line", "end_line")):
            return None
        offset, content = result.get("offset"), result.get("content")
        if result.get("offset_unit") != "characters" or not isinstance(offset, int) or not isinstance(content, str):
            return None
        return offset, offset + len(content)

    def _store_observation(self, read: dict[str, Any], result: dict[str, Any], evidence_ref: dict[str, Any]) -> None:
        interval = self._raw_character_interval(read, result)
        if interval is not None:
            remove = []
            for key, current in self.observations.items():
                current_result = current["result"]
                if (current_result["path"] != result["path"] or
                        current_result["file_sha256"] != result["file_sha256"]):
                    continue
                current_interval = self._raw_character_interval(current["read"], current_result)
                if current_interval is None:
                    continue
                if current_interval != interval and current_interval[0] <= interval[0] and current_interval[1] >= interval[1]:
                    return
                if interval[0] <= current_interval[0] and interval[1] >= current_interval[1]:
                    remove.append(key)
            for key in remove:
                del self.observations[key]
        self.observations[self._observation_key(read)] = {
            "read": read, "result": result, "evidence_ref": evidence_ref,
        }

    def refresh_after_edit(self, name: str) -> dict[str, Any]:
        """Reread only selections already observed for one successfully edited file."""
        canonical = self.tools.display(self.tools.path(name))
        selections = [value["read"] for value in self.observations.values()
                      if value["result"]["path"] == canonical]
        refreshed, errors = [], []
        for read in selections:
            try:
                result = self.tools.execute("read_file", read)
                refreshed.append({"read": read, "result": result})
            except (OSError, ValueError, KeyError, UnicodeError) as exc:
                errors.append({"read": read, "error": type(exc).__name__, "message": str(exc)})
        return {"path": canonical, "observations": refreshed, "errors": errors}

    def adopt_refreshed_observations(self, refresh: dict[str, Any], evidence_ref: dict[str, Any]) -> None:
        canonical = refresh["path"]
        for key, observation in list(self.observations.items()):
            if observation["result"]["path"] == canonical:
                del self.observations[key]
        for value in refresh["observations"]:
            self._store_observation(value["read"], value["result"], evidence_ref)

    def record(self, response: str | LLMResponse, feedback: dict[str, Any], *, action: dict[str, Any] | None = None) -> dict[str, Any]:
        result = feedback.get("tool_result", {})
        if "build" in result or "verification" in result or result.get("error") or result.get("returncode", 0) != 0:
            self.latest_diagnostic = feedback
        if action and action["tool"] == "read_file" and "file_sha256" in result:
            read = {**action["arguments"], "path": result["path"],
                    "offset": result["offset"], "limit": action["arguments"].get("limit", 16000)}
            self._store_observation(read, result, feedback["evidence_ref"])
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
            messages: list[dict[str, Any]] = [{"role": "user", "content": json.dumps(initial, ensure_ascii=False)}]
            for response, feedback in self.transactions:
                if isinstance(response, LLMResponse):
                    messages.append(response.assistant_message())
                    calls = response.tool_calls
                    ids = [call.get("id") for call in calls]
                    if calls and (not all(isinstance(i, str) and i for i in ids) or len(set(ids)) != len(ids)):
                        raise LLMRequestError("Native tool response has missing/duplicate IDs; cannot form matched receipts.")
                    if calls:
                        messages.extend({"role": "tool", "tool_call_id": call["id"],
                                         "content": json.dumps(feedback, ensure_ascii=False)} for call in calls)
                    else:
                        messages.append({"role": "user", "content": json.dumps(feedback, ensure_ascii=False)})
                else:
                    messages.extend([{"role": "assistant", "content": response},
                                     {"role": "user", "content": json.dumps(feedback, ensure_ascii=False)}])
            messages[-1]["content"] += "\nDecision budget:" + json.dumps(progress)
            request = LLMRequest(role="coder", model=self.coder.model, system=self.system, user=json.dumps(self.base, ensure_ascii=False),
                                 messages=messages, action_format=self.coder.action_format, json_schema=self.schema,
                                 temperature=self.coder.temperature, max_tokens=self.coder.max_tokens)
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
