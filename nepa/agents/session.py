"""Bounded tool loop over the retained API client."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, cast
import uuid

from ..llm.client import LLMClient, LLMRequest, structured_validation_errors
from ..llm.providers.openai_compat import OpenAICompatibleProvider
from ..run_store import RunStore
from ..schemas import load_schema
from ..speclib.plan import validate_claims
from ..tools.build import BuildRunner
from ..tools.verification import VerificationRunner
from ..tools.workspace import WorkspaceTools


class CodingSession:
    def __init__(self, client: LLMClient, store: RunStore, tools: WorkspaceTools,
                 builder: BuildRunner, verifier: VerificationRunner):
        self.client, self.store, self.tools = client, store, tools
        self.builder, self.verifier = builder, verifier
        self.schema = load_schema("agent-action.schema.json")
        prompt = (Path(__file__).parent / "prompts/coder.md").read_text()
        self.system = prompt + "\nAction schema:\n" + json.dumps(self.schema, ensure_ascii=False, separators=(",", ":"))

    def run(self, task: dict[str, Any], *, repair: bool = False, feedback: Any = None) -> bool:
        store, config = self.store, self.store.config
        state = store.run["tasks"][task["id"]]
        spec, target, acceptance = store.inputs()
        index = store.read_ref(store.run["inputs"]["index"])
        histories: list[dict[str, Any]] = []
        if feedback is not None:
            histories.append({"feedback": feedback})
        elif state.get("last_feedback"):
            histories.append({"feedback": state["last_feedback"]})
        remaining = 1 if repair else config.budgets.sessions_per_task - state["sessions"]
        for _ in range(remaining):
            state["sessions"] += 1
            state["status"] = "running"
            store.run["current_task"] = task["id"]
            store.save()
            for decision in range(config.budgets.decisions_per_session):
                store.check_budget()
                state["decisions"] += 1
                store.save()
                def request() -> LLMRequest:
                    user = json.dumps({"task": task, "target": target, "spec_index": index, "history": histories,
                                       "session": state["sessions"], "decision": decision + 1}, ensure_ascii=False)
                    return LLMRequest(role="coder", system=self.system, user=user,
                                      temperature=config.coder.temperature, max_tokens=config.coder.max_tokens)
                current = request()
                while len(json.dumps(OpenAICompatibleProvider._payload(current, config.coder.model, False), ensure_ascii=False).encode()) > config.coder.context_max_bytes and histories:
                    histories.pop(0)
                    current = request()
                response = self.client.complete(current, store=store, task_id=task["id"])
                action = response.parsed
                errors = structured_validation_errors(action, self.schema)
                if errors:
                    histories.append({"invalid_response": response.text[:6000],
                                      "feedback": {"format_errors": errors, "finish_reason": response.provider_metadata.get("finish_reason")}})
                    continue
                action = cast(dict[str, Any], action)
                identifier = store.start_action(task["id"], action)
                finished = False
                claims: list[dict[str, Any]] = []
                result: dict[str, Any]
                try:
                    tool, args = action["tool"], action["arguments"]
                    if tool == "finish":
                        claims = args["claims"]
                        validate_claims(task, claims, store.project)
                        result = {"build": self.builder.run(target, store.project, clean=task["kind"] == "integration")}
                        finished = result["build"]["passed"]
                        if finished and task["kind"] == "integration":
                            result["verification"] = self.verifier.run(
                                target, acceptance, store.project, store.root / "inputs/checks",
                                store.root / "evidence" / ("verification-" + uuid.uuid4().hex))
                            finished = result["verification"]["passed"]
                        result["accepted"] = finished
                    elif tool == "request_followup":
                        store.append_followup(args, task["id"])
                        result = {"scheduled": True, "current_task_still_requires_completion": True}
                    else:
                        result = self.tools.execute(tool, args)
                except (ValueError, OSError, KeyError) as exc:
                    result = {"error": type(exc).__name__, "message": str(exc), "accepted": False}
                ref = store.finish_action(identifier, result)
                if finished:
                    store.accept(task["id"], claims, ref)
                    return True
                view = json.dumps(result, ensure_ascii=False)
                histories.append({"action": action if action["tool"] not in {"write_file", "replace_text"} else
                                  {"tool": action["tool"], "path": action["arguments"]["path"]},
                                  "result": result if len(view) <= 20000 else {"excerpt": view[:20000], "complete_result_ref": ref},
                                  "evidence_ref": ref})
                state["last_feedback"] = histories[-1]
                store.save()
            histories.append({"feedback": "Session decision limit reached. Reinspect the current source and change the failing approach. Prior work is retained."})
        state["status"] = "failed"
        store.save()
        return False
