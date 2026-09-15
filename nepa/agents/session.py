"""Bounded tool loop over the retained API client."""
from __future__ import annotations
import json
import logging
from pathlib import Path
import time
from typing import Any, cast
import uuid

from .context import CodingContext
from ..llm.client import LLMClient, decode_action
from ..run_store import RunStore
from ..schemas import load_schema
from ..speclib.plan import validate_claims
from ..tools.build import BuildRunner
from ..tools.verification import VerificationRunner
from ..tools.workspace import WorkspaceTools


logger = logging.getLogger("nepa.runtime")


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

    @staticmethod
    def _format_feedback(errors: list[dict[str, Any]], action: Any, task: dict[str, Any],
                         finish_reason: Any, response_ref: Any) -> dict[str, Any]:
        codes = {error.get("code") for error in errors}
        details = {error.get("detail") for error in errors}
        if "foreign_action_wrapper" in details:
            instruction = ('No tool executed. The response used a DSML/native wrapper, which is invalid in JSON mode. '
                           'Return exactly one complete outer object such as '
                           '{"tool":"read_file","arguments":{"path":"src/file.c"}} with no wrapper tags or '
                           'other content before or after it.')
        elif "trailing_data" in codes:
            instruction = ("No tool executed. Return exactly one complete outer JSON action with no prose, tags "
                           "or additional JSON values before or after it.")
        elif "empty_response" in codes:
            instruction = "No tool executed. Return one complete action using the configured action format."
        elif "incomplete_response" in codes:
            instruction = "No tool executed because the provider response was incomplete. Return one smaller complete action."
        elif codes.intersection({"native_call_count", "native_envelope"}):
            instruction = "No tool executed. Return exactly one complete native function call."
        elif "schema_invalid" in codes:
            instruction = "No tool executed. Correct only the fields identified by the action schema errors."
        else:
            instruction = "No tool executed. Return one complete valid JSON action with all required delimiters."
        feedback: dict[str, Any] = {"format_errors": errors, "finish_reason": finish_reason,
                                    "instruction": instruction, "response_ref": response_ref}
        if isinstance(action, dict) and action.get("tool") == "finish":
            feedback.update(
                required_primary_ids=task["requirement_ids"],
                finish_format='{"tool":"finish","arguments":{"summary":"explanation","claims":[...]}}',
                claim_format={"id": "one of required_primary_ids", "status": "implemented",
                              "reason": "actual implementation explanation", "code_refs": ["actual/file.c:line"]},
            )
        return feedback

    def _performance_event(self, category: str, identifier: str, value: dict[str, Any]) -> None:
        self.store.evidence(f"performance/{category}/{identifier}.json", value)

    def run(self, task: dict[str, Any], *, repair: bool = False, feedback: Any = None) -> bool:
        store, config = self.store, self.store.config
        state = store.run["tasks"][task["id"]]
        invocation_id = uuid.uuid4().hex
        invocation_started_at, invocation_started = time.time(), time.monotonic()
        self._performance_event("task-invocations", invocation_id + "-start", {
            "event": "task_invocation_started", "task_id": task["id"], "kind": task["kind"],
            "repair": repair, "started_at": invocation_started_at, "starting_sessions": state["sessions"],
            "starting_decisions": state["decisions"],
        })
        spec, target, acceptance = store.inputs()
        index = store.read_ref(store.run["inputs"]["index"])
        index = {key: value for key, value in index.items() if key != "requirements"}
        index["full_requirement_index"] = "inputs/index.json"
        if feedback is not None:
            state["last_diagnostic"] = feedback
        resumed_feedback = feedback if feedback is not None else state.get("last_feedback")
        resumed_diagnostic = state.get("last_diagnostic")
        base = {"task": task, "target": target, "spec_index": index,
                "pipeline": [{"id": entry["id"], "kind": entry["kind"],
                              "primary_requirement_count": len(entry["requirement_ids"])}
                             for entry in store.plan()["tasks"]],
                "initial_feedback": resumed_feedback}
        context = CodingContext(self.system, base, config.coder, self.tools, self.schema)
        context.latest_diagnostic = resumed_diagnostic if resumed_diagnostic != resumed_feedback else None
        remaining = 1 if repair else config.budgets.sessions_per_task - state["sessions"]
        for _ in range(remaining):
            selected = config.coder.for_task(task["kind"], retry=state["sessions"] > 0, repair=repair)
            context.coder = selected
            route = {"model": selected.model, "task_kind": task["kind"],
                     "reason": "fast initial coding" if selected.model != config.coder.model else "complex task or retry/repair or no fast model"}
            state["sessions"] += 1
            session_number = state["sessions"]
            session_id = uuid.uuid4().hex
            session_started_at, session_started = time.time(), time.monotonic()
            state["status"] = "running"
            store.run["current_task"] = task["id"]
            store.save()
            self._performance_event("sessions", session_id + "-start", {
                "event": "session_started", "task_invocation_id": invocation_id, "task_id": task["id"],
                "session": session_number, "repair": repair, "route": route, "started_at": session_started_at,
                "starting_decisions": state["decisions"],
            })
            logger.info("Task %s session %d/%d using model %s%s", task["id"], state["sessions"],
                        config.budgets.sessions_per_task, selected.model, " (repair)" if repair else "")
            for decision in range(config.budgets.decisions_per_session):
                store.check_budget()
                state["decisions"] += 1
                store.save()
                progress = {"session": state["sessions"], "decisions_left": config.budgets.decisions_per_session - decision,
                            "model_route": route,
                            "instruction": "Current file observations remain available across sessions. Implement using those facts and the latest diagnostic; do not restart source discovery.",
                            "edit_contract": ("For a localized change to an existing observed file, prefer replace_text "
                                              "with exact old/new text. Use write_file for new files or when most of an "
                                              "existing file must change.")}
                if selected.action_format == "json_object":
                    progress["response_contract"] = ("Return exactly one complete outer JSON object with top-level keys "
                                                     "tool and arguments. End immediately after its closing brace.")
                else:
                    progress["response_contract"] = "Return exactly one native function call with no extra call or wrapper."
                context_started = time.monotonic()
                current = context.request(progress)
                context_elapsed_s = time.monotonic() - context_started
                call_context = {"task_invocation_id": invocation_id, "session_id": session_id,
                                "session": session_number, "decision": state["decisions"],
                                "decision_in_session": decision + 1, "route": route,
                                "context_assembly_elapsed_s": context_elapsed_s}
                logger.info("Task %s decision %d: waiting for model response", task["id"], state["decisions"])
                started = time.monotonic()
                response = self.client.complete(current, store=store, task_id=task["id"],
                                                call_context=call_context)
                logger.info("Task %s decision %d: model responded in %.1fs (run calls=%d, cost=CNY %.4f)",
                            task["id"], state["decisions"], time.monotonic() - started,
                            store.run["budget"]["calls"], store.run["budget"]["cost_cny"])
                action, errors = decode_action(response, selected.action_format, self.schema)
                history = response if selected.action_format == "tool_calls" else response.text
                if errors:
                    logger.warning("Task %s decision %d: invalid action format; no tool executed",
                                   task["id"], state["decisions"])
                    correction = self._format_feedback(
                        errors, action, task, response.provider_metadata.get("finish_reason"),
                        store.run.get("last_response"),
                    )
                    if state.get("last_diagnostic", {}).get("evidence_ref"):
                        correction["latest_diagnostic_ref"] = state["last_diagnostic"]["evidence_ref"]
                    state["last_feedback"] = context.record(history, correction)
                    store.save()
                    continue
                action = cast(dict[str, Any], action)
                identifier = store.start_action(task["id"], action, context=call_context)
                action_started = time.monotonic()
                finished = False
                claims: list[dict[str, Any]] = []
                result: dict[str, Any]
                refresh: dict[str, Any] | None = None
                try:
                    tool, args = action["tool"], action["arguments"]
                    logger.info("Task %s decision %d: executing %s", task["id"], state["decisions"], tool)
                    if tool == "finish":
                        claims = args["claims"]
                        validate_claims(task, claims, store.project)
                        logger.info("Task %s: running%s builds", task["id"], " clean" if task["kind"] == "integration" else "")
                        result = {"build": self.builder.run(target, store.project, clean=task["kind"] == "integration")}
                        finished = result["build"]["passed"]
                        logger.info("Task %s: builds %s", task["id"], "passed" if finished else "failed")
                        if finished and task["kind"] == "integration":
                            logger.info("Task %s: running independent protocol checks", task["id"])
                            result["verification"] = self.verifier.run(
                                target, acceptance, store.project, store.root / "inputs/checks",
                                store.root / "evidence" / ("verification-" + uuid.uuid4().hex))
                            finished = result["verification"]["passed"]
                            logger.info("Task %s: protocol checks %s", task["id"], "passed" if finished else "failed")
                        result["accepted"] = finished
                    elif tool == "request_followup":
                        store.append_followup(args, task["id"])
                        result = {"scheduled": True, "current_task_still_requires_completion": True}
                    else:
                        result = self.tools.execute(tool, args)
                        if tool in {"write_file", "replace_text"}:
                            refresh = context.refresh_after_edit(args["path"])
                            if refresh["observations"] or refresh["errors"]:
                                result["observation_refresh"] = refresh
                except (ValueError, OSError, KeyError) as exc:
                    logger.warning("Task %s decision %d: %s failed: %s",
                                   task["id"], state["decisions"], action.get("tool", "action"), exc)
                    result = {"error": type(exc).__name__, "message": str(exc), "accepted": False}
                    if action.get("tool") == "finish":
                        result.update(
                            instruction="Correct the finish envelope and report exactly the required primary claims; do not rerun unchanged checks.",
                            required_primary_ids=task["requirement_ids"],
                            finish_format='{"tool":"finish","arguments":{"summary":"explanation","claims":[...]}}',
                        )
                    if isinstance(exc, ValueError) and ("unsafe relative path" in str(exc) or "absolute" in str(exc)):
                        result["instruction"] = ("Host file actions require project-relative paths. Read trusted checks as "
                                                 "inputs/checks/...; /inputs and /checks are only container paths inside run_command.")
                ref = store.finish_action(identifier, result,
                                          execution_elapsed_s=time.monotonic() - action_started)
                if refresh is not None:
                    context.adopt_refreshed_observations(refresh, ref)
                if finished:
                    store.accept(task["id"], claims, ref)
                    self._performance_event("sessions", session_id + "-end", {
                        "event": "session_finished", "task_invocation_id": invocation_id,
                        "task_id": task["id"], "session": session_number, "outcome": "passed",
                        "finished_at": time.time(), "elapsed_s": time.monotonic() - session_started,
                        "ending_decisions": state["decisions"],
                    })
                    self._performance_event("task-invocations", invocation_id + "-end", {
                        "event": "task_invocation_finished", "task_id": task["id"], "kind": task["kind"],
                        "repair": repair, "outcome": "passed", "finished_at": time.time(),
                        "elapsed_s": time.monotonic() - invocation_started, "ending_sessions": state["sessions"],
                        "ending_decisions": state["decisions"],
                    })
                    return True
                model_result = result
                if refresh is not None:
                    model_result = {key: value for key, value in result.items() if key != "observation_refresh"}
                    model_result["observation_refresh"] = {
                        "path": refresh["path"], "refreshed": len(refresh["observations"]),
                        "errors": refresh["errors"],
                        "observation": "Refreshed content is in current_observations with the edit evidence reference.",
                    }
                view = json.dumps(model_result, ensure_ascii=False)
                feedback_row = {"tool_result": model_result if tool == "read_file" or len(view) <= 20000 else
                                {"excerpt": view[:20000], "complete_result_ref": ref}, "evidence_ref": ref,
                                "instruction": "This action has already executed. Continue with the next necessary action."}
                state["last_feedback"] = context.record(history, feedback_row, action=action)
                if context.latest_diagnostic is not None:
                    state["last_diagnostic"] = context.latest_diagnostic
                store.save()
            self._performance_event("sessions", session_id + "-end", {
                "event": "session_finished", "task_invocation_id": invocation_id, "task_id": task["id"],
                "session": session_number, "outcome": "decision_budget_exhausted", "finished_at": time.time(),
                "elapsed_s": time.monotonic() - session_started, "ending_decisions": state["decisions"],
            })
        state["status"] = "failed"
        store.save()
        self._performance_event("task-invocations", invocation_id + "-end", {
            "event": "task_invocation_finished", "task_id": task["id"], "kind": task["kind"],
            "repair": repair, "outcome": "sessions_exhausted", "finished_at": time.time(),
            "elapsed_s": time.monotonic() - invocation_started, "ending_sessions": state["sessions"],
            "ending_decisions": state["decisions"],
        })
        logger.error("Task %s exhausted its available sessions", task["id"])
        return False
