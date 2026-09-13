"""Bounded tool loop over the retained API client."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, cast
import uuid

from .context import CodingContext
from ..llm.client import LLMClient, decode_action
from ..run_store import RunStore
from ..schemas import load_schema
from ..speclib.plan import validate_claims
from ..tools.build import BuildRunner
from ..tools.verification import VerificationRunner, safe_feedback
from ..tools.workspace import WorkspaceTools


class CodingSession:
    def __init__(self, client: LLMClient, store: RunStore, tools: WorkspaceTools,
                 builder: BuildRunner, verifier: VerificationRunner):
        self.client, self.store, self.tools = client, store, tools
        self.builder, self.verifier = builder, verifier
        self.schema = load_schema("agent-action.schema.json")
        prompt = (Path(__file__).parent / "prompts/coder.md").read_text()
        if store.config.coder.action_format == "tool_calls":
            instructions = ("Use exactly ONE native function call per decision. The host executes the function and returns a matched tool result. "
                            "Never output XML or pretend that a tool ran. JSON envelopes below illustrate action semantics; use the supplied native functions instead.")
        else:
            instructions = ("Return exactly ONE JSON tool action conforming to the supplied action schema, without\n"
                            "Markdown fences. The host executes tools and returns actual feedback.\n"
                            "Never output XML, tool_calls or invoke tags: those do not execute here.")
        self.system = prompt.replace("{{action_instructions}}", instructions)
        if store.config.coder.action_format == "json_object":
            self.system += "\nAction schema:\n" + json.dumps(self.schema, ensure_ascii=False, separators=(",", ":"))

    def publish_feedback(self, name: str, result: dict[str, Any]) -> dict[str, Any]:
        public = safe_feedback(result)
        ref = self.store.publish_agent_evidence(name, public)
        feedback = self._feedback_view(public, ref)
        diagnostic = public.get("result", public)
        reason = None
        if ((diagnostic.get("verification") or {}).get("passed") is False or
                (diagnostic.get("exposure") == "private_isolated" and diagnostic.get("passed") is False)):
            reason = "private_acceptance_repair"
        elif (diagnostic.get("build") or {}).get("passed") is False:
            reason = "build_repair"
        elif diagnostic.get("returncode", 0) != 0 or diagnostic.get("timed_out"):
            reason = "tool_failure_repair"
        if reason:
            feedback["model_route"] = {"reason": reason}
        return feedback

    @staticmethod
    def _feedback_view(public: dict[str, Any], ref: dict[str, str]) -> dict[str, Any]:
        view = json.dumps(public, ensure_ascii=False)
        return {"tool_result": public if "file_sha256" in public or len(view) <= 20000 else
                {**{key: public[key] for key in ("returncode", "timed_out", "accepted", "passed", "error") if key in public},
                 "excerpt": view[:20000], "complete_result_ref": ref}, "evidence_ref": ref,
                "instruction": "This action has already executed. Continue with the next necessary action."}

    def _restore_feedback(self, feedback: dict[str, Any]) -> dict[str, Any]:
        # Logical evidence paths resolve only through the published tool root. A
        # valid raw-store hash must never authorize reading host action history.
        ref = feedback["evidence_ref"]
        public = self.store.read_agent_evidence(ref)
        return self._feedback_view(public, ref)

    def run(self, task: dict[str, Any], *, repair: bool = False, feedback: dict[str, Any] | None = None) -> bool:
        store, config = self.store, self.store.config
        state = store.run["tasks"][task["id"]]
        spec, target, acceptance = store.inputs()
        index = store.read_ref(store.run["inputs"]["index"])
        index = {key: value for key, value in index.items() if key != "requirements"}
        index["full_requirement_index"] = "inputs/index.json"
        saved_feedback = feedback if feedback is not None else state.get("last_feedback")
        if feedback is not None and "evidence_ref" not in feedback:
            saved_feedback = self.publish_feedback("initial/" + uuid.uuid4().hex + ".json", feedback)
        initial_feedback = self._restore_feedback(saved_feedback) if saved_feedback else None
        if initial_feedback and "file_sha256" in initial_feedback["tool_result"]:
            # Resumed processes have no validated observation set yet. Retain the
            # safe receipt/ref, not historical source content outside hash refresh.
            initial_feedback["tool_result"].pop("content", None)
            initial_feedback["instruction"] = "Previous read receipt. Use read_file to refresh current contents after resume."
        saved_diagnostic = (saved_feedback or {}).get("latest_observed_diagnostic")
        base = {"task": task, "target": target, "spec_index": index,
                "pipeline": [{"id": entry["id"], "kind": entry["kind"],
                              "primary_requirement_count": len(entry["requirement_ids"])}
                             for entry in store.plan()["tasks"]],
                "initial_feedback": initial_feedback}
        context = CodingContext(self.system, base, config.coder, self.tools, self.schema, client=self.client)
        context.latest_diagnostic = (self._restore_feedback(saved_diagnostic) if saved_diagnostic else
                                     initial_feedback if feedback is not None else None)
        repair_reason = (saved_feedback or {}).get("model_route", {}).get("reason")
        if repair_reason not in {"build_repair", "private_acceptance_repair", "tool_failure_repair"}:
            repair_reason = None
        if feedback is not None and initial_feedback is not None:
            state["last_feedback"] = {**initial_feedback, "latest_observed_diagnostic": context.latest_diagnostic,
                                      "model_route": (saved_feedback or {}).get("model_route", {})}
        remaining = 1 if repair else config.budgets.sessions_per_task - state["sessions"]
        for _ in range(remaining):
            selected = config.coder.for_task(task["kind"], retry=state["sessions"] > 0, repair=repair or repair_reason is not None)
            context.coder = selected
            reason = repair_reason or ("task_retry" if repair or state["sessions"] > 0 or task["kind"] == "followup" else
                                       {"shared-wire": "shared_interface", "integration": "integration"}.get(task["kind"], "initial_ordinary_task"))
            route = {"model": selected.model, "task_kind": task["kind"],
                     "reason": reason}
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
                response = self.client.complete(current, store=store, task_id=task["id"], prepared=context.prepared_request)
                action, errors = decode_action(response, selected.action_format, self.schema)
                history = response if selected.action_format == "tool_calls" else response.text
                if errors:
                    context.record(history,
                        {"format_errors": errors, "finish_reason": response.provider_metadata.get("finish_reason"),
                         "instruction": "No tool executed. Correct the intended action using " + selected.action_format + "; never XML/invoke tags.",
                         "required_primary_ids": task["requirement_ids"],
                         "finish_format": '{"tool":"finish","arguments":{"summary":"explanation","claims":[...]}}',
                         "claim_format": {"id": "one of required_primary_ids", "status": "implemented",
                                          "reason": "actual implementation explanation", "code_refs": ["actual/file.c:line"]}})
                    continue
                action = cast(dict[str, Any], action)
                identifier = store.start_action(task["id"], action)
                finished = False
                failed_gate = None
                error_feedback = None
                claims: list[dict[str, Any]] = []
                result: dict[str, Any]
                try:
                    tool, args = action["tool"], action["arguments"]
                    if tool == "finish":
                        claims = args["claims"]
                        validate_claims(task, claims, store.project)
                        failed_gate = "build_repair"
                        result = {"build": self.builder.run(target, store.project, clean=task["kind"] == "integration")}
                        finished = result["build"]["passed"]
                        if finished and task["kind"] == "integration":
                            failed_gate = "private_acceptance_repair"
                            result["verification"] = self.verifier.run(
                                target, acceptance, store.project, store.private_checks,
                                store.root / "evidence" / ("verification-" + uuid.uuid4().hex))
                            finished = result["verification"]["passed"]
                        result["accepted"] = finished
                    elif tool == "request_followup":
                        store.append_followup(args, task["id"])
                        result = {"scheduled": True, "current_task_still_requires_completion": True}
                    else:
                        result = self.tools.execute(tool, args)
                        if tool == "run_command" and (result["returncode"] != 0 or result.get("timed_out")):
                            failed_gate = "tool_failure_repair"
                except (ValueError, OSError, KeyError) as exc:
                    result = {"error": type(exc).__name__, "message": str(exc), "accepted": False}
                    error_feedback = {"error": type(exc).__name__, "accepted": False,
                                      "message": "Action failed. Check the logical path and action arguments. "
                                                 "Finish requires exactly the primary claims and existing project code references."}
                    finished = False
                    if tool == "run_command":
                        failed_gate = "tool_failure_repair"
                ref = store.finish_action(identifier, result)
                feedback_row = self.publish_feedback(f"actions/{identifier}.json", error_feedback or result)
                if failed_gate and not finished:
                    repair_reason = failed_gate
                    selected = config.coder.for_task(task["kind"], repair=True)
                    context.coder = selected
                    route = {"model": selected.model, "task_kind": task["kind"], "reason": repair_reason}
                receipt = context.record(history, feedback_row, action=action)
                state["last_feedback"] = {**receipt, "latest_observed_diagnostic": context.latest_diagnostic,
                                          "model_route": route}
                if finished:
                    store.accept(task["id"], claims, ref)
                    return True
                store.save()
        state["status"] = "failed"
        store.save()
        return False
