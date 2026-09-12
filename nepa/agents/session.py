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
        index = {key: value for key, value in index.items() if key != "requirements"}
        index["full_requirement_index"] = "inputs/index.json"
        base = {"task": task, "target": target, "spec_index": index,
                "pipeline": [{"id": entry["id"], "kind": entry["kind"],
                              "primary_requirement_count": len(entry["requirement_ids"])}
                             for entry in store.plan()["tasks"]],
                "initial_feedback": feedback if feedback is not None else state.get("last_feedback")}
        messages = [{"role": "user", "content": json.dumps(base, ensure_ascii=False)}]
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
                    return LLMRequest(role="coder", system=self.system, user=json.dumps(base, ensure_ascii=False),
                                      messages=[dict(m) for m in messages],
                                      temperature=config.coder.temperature, max_tokens=config.coder.max_tokens)
                progress = {"session": state["sessions"], "decisions_left": config.budgets.decisions_per_session - decision,
                            "instruction": "Use already supplied facts. Make the next concrete implementation or validation action."}
                if messages[-1]["role"] == "user":
                    # Replace, rather than accumulate, the per-decision reminder.
                    messages[-1]["content"] = messages[-1]["content"].split("\nDecision budget:")[0] + "\nDecision budget:" + json.dumps(progress)
                current = request()
                while len(json.dumps(OpenAICompatibleProvider._payload(current, config.coder.model, False), ensure_ascii=False).encode()) > config.coder.context_max_bytes and len(messages) > 1:
                    del messages[1:3]
                    current = request()
                response = self.client.complete(current, store=store, task_id=task["id"])
                messages.append({"role": "assistant", "content": response.text})
                # An incomplete outer action must not become a valid inner JSON
                # object via the provider-neutral prose/JSON extraction helper.
                try:
                    action = json.loads(response.text)
                    errors = structured_validation_errors(action, self.schema)
                except json.JSONDecodeError as exc:
                    action = None
                    errors = [{"path": [], "message": f"Invalid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}. Return the complete outer action object, including all closing braces."}]
                if errors:
                    messages.append({"role": "user", "content": json.dumps(
                        {"format_errors": errors, "finish_reason": response.provider_metadata.get("finish_reason"),
                         "instruction": "No tool executed. Return JSON, never XML/tool_calls/invoke tags. Correct the intended action.",
                         "required_primary_ids": task["requirement_ids"],
                         "finish_format": '{"tool":"finish","arguments":{"summary":"explanation","claims":[...]}}',
                         "claim_format": {"id": "one of required_primary_ids", "status": "implemented",
                                          "reason": "actual implementation explanation", "code_refs": ["actual/file.c:line"]}})})
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
                feedback_row = {"tool_result": result if len(view) <= 20000 else
                                {"excerpt": view[:20000], "complete_result_ref": ref}, "evidence_ref": ref,
                                "instruction": "This action has already executed. Continue with the next necessary action."}
                messages.append({"role": "user", "content": json.dumps(feedback_row, ensure_ascii=False)})
                state["last_feedback"] = feedback_row
                store.save()
            messages.append({"role": "user", "content": "Session decision limit reached. Reinspect current source and change the failing approach. Prior work is retained."})
        state["status"] = "failed"
        store.save()
        return False
