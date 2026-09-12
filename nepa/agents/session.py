"""Bounded tool loop over the retained API client."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, cast
import uuid

from .context import CodingContext
from ..llm.client import LLMClient, structured_validation_errors
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
        context = CodingContext(self.system, base, config.coder, self.tools)
        remaining = 1 if repair else config.budgets.sessions_per_task - state["sessions"]
        for _ in range(remaining):
            selected = config.coder.for_task(task["kind"], retry=state["sessions"] > 0, repair=repair)
            context.coder = selected
            route = {"model": selected.model, "task_kind": task["kind"],
                     "reason": "fast initial coding" if selected.model != config.coder.model else "complex task or retry/repair or no fast model"}
            state["sessions"] += 1
            state["status"] = "running"
            store.run["current_task"] = task["id"]
            store.save()
            for decision in range(config.budgets.decisions_per_session):
                store.check_budget()
                state["decisions"] += 1
                store.save()
                progress = {"session": state["sessions"], "decisions_left": config.budgets.decisions_per_session - decision,
                            "model_route": route,
                            "instruction": "Current file observations remain available across sessions. Implement using those facts and the latest diagnostic; do not restart source discovery."}
                current = context.request(progress)
                response = self.client.complete(current, store=store, task_id=task["id"])
                # An incomplete outer action must not become a valid inner JSON
                # object via the provider-neutral prose/JSON extraction helper.
                try:
                    action = json.loads(response.text)
                    errors = structured_validation_errors(action, self.schema)
                except json.JSONDecodeError as exc:
                    action = None
                    errors = [{"path": [], "message": f"Invalid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}. Return the complete outer action object, including all closing braces."}]
                if errors:
                    context.record(response.text,
                        {"format_errors": errors, "finish_reason": response.provider_metadata.get("finish_reason"),
                         "instruction": "No tool executed. Return JSON, never XML/tool_calls/invoke tags. Correct the intended action.",
                         "required_primary_ids": task["requirement_ids"],
                         "finish_format": '{"tool":"finish","arguments":{"summary":"explanation","claims":[...]}}',
                         "claim_format": {"id": "one of required_primary_ids", "status": "implemented",
                                          "reason": "actual implementation explanation", "code_refs": ["actual/file.c:line"]}})
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
                feedback_row = {"tool_result": result if tool == "read_file" or len(view) <= 20000 else
                                {"excerpt": view[:20000], "complete_result_ref": ref}, "evidence_ref": ref,
                                "instruction": "This action has already executed. Continue with the next necessary action."}
                state["last_feedback"] = context.record(response.text, feedback_row, action=action)
                store.save()
        state["status"] = "failed"
        store.save()
        return False
