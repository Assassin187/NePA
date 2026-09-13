"""Single-state run storage, durable evidence, budget reservations and checkpoints."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
import signal
from pathlib import Path
import time
from typing import Any, Iterator, Literal, cast
import uuid

from .config import ResolvedConfig, public_config_snapshot
from .speclib.lint import canonical_json_bytes, digest, lint_acceptance, lint_spec, lint_target, safe_relative, _schema_errors
from .speclib.plan import compile_plan
from .speclib.planning import planning_index
from .tools.git_ops import GitCheckpoints


class RunStoreError(ValueError):
    pass


class BudgetExhausted(RuntimeError):
    pass


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    with temporary.open("xb") as stream:
        stream.write(canonical_json_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def tree_hashes(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[name] = hashlib.sha256(("symlink:" + os.readlink(path)).encode()).hexdigest()
        elif path.is_file():
            result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def runtime_fingerprint() -> dict[str, Any]:
    package = Path(__file__).parent
    files = {name: sha for name, sha in tree_hashes(package).items()
             if "__pycache__" not in Path(name).parts and not name.endswith(".pyc")}
    return {"package_sha256": digest(files), "files": files}


def campaign_cost_cny(root: Path) -> float:
    """Sum the current CNY campaign; never reinterpret old USD records as CNY."""
    total = 0.0
    for path in root.glob("*/run.json"):
        value = json.loads(path.read_bytes())
        if value.get("schema_version") != "7.0":
            raise RunStoreError("Use a new CNY campaign root; legacy schema/currency runs remain separate: " + str(path))
        total += value["budget"]["cost_cny"]
    return total


@contextmanager
def file_lock(path: Path, *, nonblocking: bool = True) -> Iterator[None]:
    with path.open("a+b") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
        except BlockingIOError as exc:
            raise RunStoreError(f"already controlled: {path.name}") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class RunStore:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.run = json.loads((self.root / "run.json").read_bytes())
        if self.run.get("schema_version") != "7.0":
            raise RunStoreError("unsupported legacy run; use baseline code, not in-place resume")
        errors = _schema_errors(self.run, "run.schema.json")
        if errors:
            raise RunStoreError(f"invalid run state: {errors}")
        self.config = ResolvedConfig.model_validate(self.run["config_snapshot"])
        self.project = self.root / "project"
        self.git = GitCheckpoints(self.root / "checkpoints.git", self.project)

    @property
    def run_id(self) -> str:
        return str(self.run["run_id"])

    @classmethod
    def initialize(cls, runs_root: Path | str, spec_path: Path | str, target_path: Path | str,
                   acceptance_path: Path | str, config: ResolvedConfig) -> RunStore:
        sources = {"spec": Path(spec_path).resolve(), "target": Path(target_path).resolve(),
                   "acceptance": Path(acceptance_path).resolve()}
        raw = {key: path.read_bytes() for key, path in sources.items()}
        values = {key: json.loads(data) for key, data in raw.items()}
        reports = (lint_spec(values["spec"]), lint_target(values["target"], values["spec"]),
                   lint_acceptance(sources["acceptance"], values["spec"]))
        for report in reports:
            if not report["valid"]:
                raise RunStoreError(str(report["errors"]))
        runs_root = Path(runs_root).resolve()
        runs_root.mkdir(parents=True, exist_ok=True)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        staging = runs_root / (".initializing-" + run_id)
        staging.mkdir()
        (staging / "inputs").mkdir()
        (staging / "private").mkdir()
        (staging / "agent-evidence").mkdir()
        refs = {}
        for key, data in raw.items():
            path = staging / ("private" if key == "acceptance" else "inputs") / (key + ".json")
            path.write_bytes(data)
            refs[key] = {"path": str(path.relative_to(staging)), "sha256": hashlib.sha256(data).hexdigest()}
        check_root = staging / "private" / "assets"
        check_root.mkdir()
        for asset in values["acceptance"]["assets"]:
            name = safe_relative(asset)
            source = sources["acceptance"].parent / name
            if not source.resolve().is_relative_to(sources["acceptance"].parent):
                raise RunStoreError("private asset escaped its source root")
            data = source.read_bytes()
            dest = check_root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            refs["check:" + name] = {"path": str(dest.relative_to(staging)), "sha256": hashlib.sha256(data).hexdigest()}
        plan = compile_plan(values["spec"], values["target"])
        (staging / "plans").mkdir()
        atomic_json(staging / "plans/0001.json", plan)
        atomic_json(staging / "inputs/index.json", planning_index(values["spec"]))
        refs["index"] = {"path": "inputs/index.json", "sha256": hashlib.sha256((staging / "inputs/index.json").read_bytes()).hexdigest()}
        (staging / "project").mkdir()
        (staging / "evidence").mkdir()
        checkpoint = GitCheckpoints(staging / "checkpoints.git", staging / "project").initialize()
        now = time.time()
        run = {"schema_version": "7.0", "run_id": run_id, "created_at": now, "updated_at": now,
               "status": "pending", "exit_code": None, "inputs": {k: v for k, v in refs.items() if k in {"spec", "target", "index"}},
               "private_inputs": {k: v for k, v in refs.items() if k not in {"spec", "target", "index"}},
               "verification_policy": {"exposure": "private_isolated", "verifier_version": "private-isolated/1", "randomization_version": "random-inputs/1"},
               "runtime": runtime_fingerprint(),
               "config_snapshot": public_config_snapshot(config), "config_sha256": digest(public_config_snapshot(config)),
               "active_plan": {"path": "plans/0001.json", "sha256": digest(plan)}, "accepted_checkpoint": checkpoint,
               "tasks": {t["id"]: {"status": "pending", "sessions": 0, "decisions": 0, "claims": [], "evidence": []} for t in plan["tasks"]},
               "current_task": None, "pending_action": None, "working_hashes": {},
               "budget": {"cost_cny": 0.0, "settled_cny": {"peak": 0.0, "off_peak": 0.0, "flat": 0.0, "unclassified": 0.0}, "tokens_in": 0, "tokens_out": 0, "calls": 0},
               "phase_cost_cny": {"capability": 0.0, "public_tools": 0.0, "generation": 0.0},
               "liability_overflow_cny": 0.0, "call_counter": 0, "pending_calls": {}, "followups": 0, "final_repairs": 0, "history": []}
        atomic_json(staging / "run.json", run)
        for key, path in sources.items():
            if path.read_bytes() != raw[key]:
                raise RunStoreError("input changed during snapshot initialization; staging retained")
        final = runs_root / run_id
        os.replace(staging, final)
        return cls(final)

    @classmethod
    def open(cls, runs_root: Path | str, run_id: str) -> RunStore:
        if safe_relative(run_id) != run_id or "/" in run_id:
            raise RunStoreError("invalid run ID")
        return cls(Path(runs_root) / run_id)

    def save(self) -> None:
        self.run["updated_at"] = time.time()
        atomic_json(self.root / "run.json", self.run)

    @contextmanager
    def lock(self) -> Iterator[None]:
        with file_lock(self.root / ".lock"):
            yield

    @contextmanager
    def deadline(self) -> Iterator[None]:
        """Enforce the Linux CLI deadline even during a provider or tool call."""
        if self.run["status"] == "success":
            yield
            return
        def expired(signum: int, frame: Any) -> None:
            raise BudgetExhausted("run time limit reached during external operation")
        previous = signal.signal(signal.SIGALRM, expired)
        remaining = self.run["created_at"] + self.config.budgets.wall_clock_hours * 3600 - time.time()
        signal.setitimer(signal.ITIMER_REAL, max(.001, remaining))
        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)

    def read_ref(self, ref: dict[str, str]) -> Any:
        path = self.root / safe_relative(ref["path"])
        if not path.resolve().is_relative_to(self.root):
            raise RunStoreError("artifact path escaped run")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != ref["sha256"]:
            raise RunStoreError(f"artifact hash mismatch: {ref['path']}")
        return json.loads(data)

    def inputs(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        if digest(self.run["config_snapshot"]) != self.run["config_sha256"]:
            raise RunStoreError("configuration snapshot drift")
        for ref in {**self.run["inputs"], **self.run["private_inputs"]}.values():
            path = self.root / safe_relative(ref["path"])
            if not path.resolve().is_relative_to(self.root) or hashlib.sha256(path.read_bytes()).hexdigest() != ref["sha256"]:
                raise RunStoreError("input snapshot drift")
        return tuple(self.read_ref(({**self.run["inputs"], **self.run["private_inputs"]})[key]) for key in ("spec", "target", "acceptance"))  # type: ignore[return-value]

    @property
    def private_checks(self) -> Path:
        return self.root / "private/assets"

    def publish_agent_evidence(self, name: str, value: Any) -> dict[str, str]:
        """Publish an already projected value; the logical evidence root is public only."""
        name = safe_relative(name)
        path = self.root / "agent-evidence" / name
        if not path.resolve().is_relative_to(self.root / "agent-evidence"):
            raise RunStoreError("published evidence escaped its root")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = canonical_json_bytes(value)
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return {"path": "evidence/" + name, "sha256": hashlib.sha256(data).hexdigest()}

    def read_agent_evidence(self, ref: dict[str, str]) -> Any:
        name = safe_relative(ref["path"])
        if not name.startswith("evidence/"):
            raise RunStoreError("diagnostic reference is not published evidence")
        path = self.root / "agent-evidence" / name.removeprefix("evidence/")
        if not path.resolve().is_relative_to(self.root / "agent-evidence"):
            raise RunStoreError("diagnostic reference escaped published evidence")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != ref["sha256"]:
            raise RunStoreError("published diagnostic hash mismatch")
        return json.loads(data)

    def plan(self) -> dict[str, Any]:
        return self.read_ref(self.run["active_plan"])

    def evidence(self, name: str, value: Any) -> dict[str, str]:
        from .llm.telemetry import redact
        name = safe_relative(name)
        path = self.root / "evidence" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        envs = [p.api_key_env for p in self.config.providers.values() if p.api_key_env]
        data = canonical_json_bytes(redact(value, envs))
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return {"path": str(path.relative_to(self.root)), "sha256": hashlib.sha256(data).hexdigest()}

    def check_budget(self) -> None:
        if self.run["liability_overflow_cny"] > 0:
            raise BudgetExhausted("known provider liability exceeded reservation; further calls blocked")
        if time.time() - self.run["created_at"] >= self.config.budgets.wall_clock_hours * 3600:
            raise BudgetExhausted("run time limit reached")
        if self.run["budget"]["cost_cny"] >= self.config.budgets.max_cost_cny:
            raise BudgetExhausted("run cost limit reached")

    def reserve_call(self, task_id: str, reservation: float, wire: dict[str, Any], *, phase: str = "generation") -> int:
        if phase not in {"capability", "public_tools", "generation"}:
            raise RunStoreError("unknown campaign phase")
        if not isinstance(reservation, (int, float)) or not 0 <= reservation < float("inf"):
            raise RunStoreError("invalid reservation")
        self.check_budget()
        with file_lock(self.root.parent / ".campaign.lock", nonblocking=False):
            total = campaign_cost_cny(self.root.parent)
            campaign_runs = [json.loads(path.read_bytes()) for path in self.root.parent.glob("*/run.json")]
            if any(run.get("liability_overflow_cny", 0) > 0 for run in campaign_runs):
                raise BudgetExhausted("campaign has known liability above a reservation")
            phase_total = sum(run["phase_cost_cny"].get(phase, 0.0) for run in campaign_runs)
            if phase in {"capability", "public_tools"} and phase_total + reservation > self.config.campaign.phase_max_cost_cny[cast(Literal["capability", "public_tools"], phase)]:
                raise BudgetExhausted("campaign phase cost limit reached: " + phase)
            if total + reservation > self.config.budgets.campaign_max_cost_cny:
                raise BudgetExhausted("campaign cost limit reached")
            if self.run["budget"]["cost_cny"] + reservation > self.config.budgets.max_cost_cny:
                raise BudgetExhausted("run cannot reserve next call within budget")
            sequence = self.run["call_counter"] + 1
            # Never reuse orphan call evidence, including an interrupted reservation.
            while (self.root / "evidence" / f"calls/{sequence:06d}.request.json").exists():
                sequence += 1
            self.run["call_counter"] = sequence
            self.run["budget"]["calls"] += 1
            self.run["budget"]["cost_cny"] += reservation
            self.run["phase_cost_cny"][phase] += reservation
            self.run["pending_calls"][str(sequence)] = {"reserved": reservation, "task_id": task_id, "started_at": time.time(), "phase": phase}
            self.save()
            self.evidence(f"calls/{sequence:06d}.request.json", {"task_id": task_id, "wire": wire, "reserved_cny": reservation, "phase": phase,
                          "started_at": self.run["pending_calls"][str(sequence)]["started_at"]})
            return sequence

    def settle_call(self, sequence: int, response: dict[str, Any], *, elapsed_s: float) -> None:
        ref = self.evidence(f"calls/{sequence:06d}.response.json", {"response": response, "elapsed_s": elapsed_s})
        with file_lock(self.root.parent / ".campaign.lock", nonblocking=False):
            pending = self.run["pending_calls"].pop(str(sequence))
            self.run["budget"]["cost_cny"] += response["cost_cny"] - pending["reserved"]
            self.run["phase_cost_cny"][pending["phase"]] += response["cost_cny"] - pending["reserved"]
            self.run["liability_overflow_cny"] += max(0.0, response["cost_cny"] - pending["reserved"])
            period = (response.get("pricing") or {}).get("period", "unclassified")
            if period not in self.run["budget"]["settled_cny"]:
                period = "unclassified"
            self.run["budget"]["settled_cny"][period] += response["cost_cny"]
            self.run["budget"]["tokens_in"] += response["tokens_in"]
            self.run["budget"]["tokens_out"] += response["tokens_out"]
            self.run["last_response"] = ref
            self.save()

    def fail_call(self, sequence: int, error: BaseException, *, elapsed_s: float, raw_response: Any = None) -> None:
        if raw_response is not None:
            self.evidence(f"calls/{sequence:06d}.fault-response.json", raw_response)
        self.evidence(f"calls/{sequence:06d}.error.json", {"error": type(error).__name__, "failure_class": getattr(error, "failure_class", type(error).__name__), "message": str(error), "elapsed_s": elapsed_s,
                                                        "accounting": "unknown usage; reservation retained"})
        self.save()

    def start_action(self, task_id: str, action: dict[str, Any]) -> str:
        if tree_hashes(self.project) != self.run["working_hashes"]:
            raise RunStoreError("unrecorded project changes detected before action; refusing to overwrite")
        identifier = uuid.uuid4().hex
        self.run["pending_action"] = {"id": identifier, "task_id": task_id, "action": action}
        self.save()
        return identifier

    def finish_action(self, identifier: str, result: Any) -> dict[str, str]:
        ref = self.evidence(f"actions/{identifier}.json", {"action": self.run["pending_action"], "result": result})
        self.run["pending_action"] = None
        self.run["working_hashes"] = tree_hashes(self.project)
        self.save()
        return ref

    def accept(self, task_id: str, claims: list[dict[str, Any]], evidence: dict[str, str]) -> None:
        checkpoint = self.git.commit("accepted task " + task_id)
        task = self.run["tasks"][task_id]
        task.update({"status": "passed", "claims": claims, "checkpoint": checkpoint})
        task["evidence"].append(evidence)
        self.run["accepted_checkpoint"] = checkpoint
        self.run["working_hashes"] = tree_hashes(self.project)
        self.run["current_task"] = None
        self.run["pending_action"] = None
        self.save()

    def reconfigure(self, config: ResolvedConfig, *, reason: str, allow_runtime_change: bool = False) -> None:
        """Explicit development continuation; caller holds the run lock."""
        if not reason.strip():
            raise RunStoreError("configuration change requires a recorded reason")
        if self.run["status"] in {"success", "study_complete"} or self.run.get("delivery"):
            raise RunStoreError("completed delivery cannot be reconfigured")
        self.inputs()
        self.plan()
        if tree_hashes(self.project) != self.run["working_hashes"] and self.run["pending_action"] is None:
            raise RunStoreError("unrecorded project changes; refusing configuration migration")
        runtime = runtime_fingerprint()
        if runtime != self.run["runtime"] and not allow_runtime_change:
            raise RunStoreError("runtime changed; explicit --accept-runtime-change is required")
        identifier = uuid.uuid4().hex
        previous_state = self.evidence(f"configuration-changes/{identifier}/previous-run.json", self.run)
        report_path = self.root / "report.json"
        previous_report = self.evidence(f"configuration-changes/{identifier}/previous-report.json",
                                        json.loads(report_path.read_bytes())) if report_path.exists() else None
        snapshot = public_config_snapshot(config)
        change = self.evidence(f"configuration-changes/{identifier}/change.json",
                               {"reason": reason, "previous_state": previous_state, "previous_report": previous_report,
                                "new_config": snapshot, "new_runtime": runtime,
                                "budget_and_time_reset": False})
        self.run["config_snapshot"], self.run["config_sha256"] = snapshot, digest(snapshot)
        self.run["runtime"] = runtime
        self.run["history"].append({"kind": "configuration_change", "reason": reason, "evidence": change})
        self.config = config
        self.save()

    def cleanup_verification(self) -> None:
        from .tools.sandbox import SandboxExecutor
        for path in (self.root / "evidence").rglob("containers.json"):
            record = json.loads(path.read_bytes())
            if record.get("cleaned"):
                continue
            for role in ("checker", "server", "command"):
                if role in record:
                    SandboxExecutor.remove_container(record[role])
            record["cleaned"] = True
            atomic_json(path, record)

    def recover(self) -> None:
        if self.run["status"] == "study_complete":
            raise RunStoreError("completed study cannot resume as production generation")
        self.cleanup_verification()
        if self.run["runtime"]["package_sha256"] != runtime_fingerprint()["package_sha256"]:
            raise RunStoreError("runtime code/prompts changed; preserve this run and start a new one")
        self.inputs()
        self.plan()
        actual = tree_hashes(self.project)
        if actual != self.run["working_hashes"] and self.run["pending_action"] is None:
            raise RunStoreError("unrecorded project changes detected; preserve and inspect before resume")
        if self.run["current_task"] or self.run["pending_action"]:
            archive = self.root / "interrupted" / uuid.uuid4().hex
            archive.parent.mkdir(exist_ok=True)
            os.replace(self.project, archive)
            self.project.mkdir()
            self.git.restore(self.run["accepted_checkpoint"])
            self.run["history"].append({"kind": "interrupted_tree_preserved", "path": str(archive.relative_to(self.root))})
            self.run["working_hashes"] = tree_hashes(self.project)
            self.run["pending_action"] = None
            self.save()

    def append_followup(self, request: dict[str, Any], task_id: str) -> None:
        if task_id == "final-integration":
            raise ValueError("final integration repairs its own issues directly")
        if self.run["followups"] >= self.config.budgets.followups:
            raise ValueError("follow-up limit exhausted")
        if self.run["tasks"][task_id]["sessions"] >= self.config.budgets.sessions_per_task:
            raise ValueError("follow-up cannot evade exhausted task sessions")
        spec, _, _ = self.inputs()
        known = {r["id"] for r in spec["requirements"]}
        if not request.get("issue") or not request.get("diagnostic_refs") or not set(request.get("requirement_ids", [])).issubset(known):
            raise ValueError("follow-up requires issue, valid requirements and diagnostic refs")
        for ref in request["diagnostic_refs"]:
            self.read_agent_evidence(ref)
        plan = self.plan()
        number = self.run["followups"] + 1
        identifier = f"followup:{number:03d}"
        entry = {"id": identifier, "kind": "followup", "goal": request["issue"], "context": request,
                 "requirement_ids": [], "depends_on": plan["tasks"][-2]["id"]}
        plan["tasks"].insert(-1, entry)
        for index, task in enumerate(plan["tasks"]):
            task["depends_on"] = plan["tasks"][index - 1]["id"] if index else None
        plan["revision"] += 1
        plan["reason"] = f"follow-up requested by {task_id}"
        relative = f"plans/{plan['revision']:04d}.json"
        path = self.root / relative
        with path.open("xb") as stream:
            stream.write(canonical_json_bytes(plan))
        self.run["active_plan"] = {"path": relative, "sha256": digest(plan)}
        self.run["tasks"][identifier] = {"status": "pending", "sessions": 0, "decisions": 0, "claims": [], "evidence": []}
        self.run["followups"] = number
        self.save()
