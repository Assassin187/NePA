"""Durable, hash-bound filesystem storage for M1-1 runs."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any, Callable, Iterator, Mapping

from .config import ConfigSnapshotDrift, ResolvedConfig, verify_config_snapshot
from .speclib.lint import _schema_errors, canonical_json_bytes, lint_spec, lint_target, lint_test_bundle


class RunStoreError(RuntimeError):
    """Base class for durable run-store failures."""


class ArtifactConflict(RunStoreError):
    """An immutable path already contains different bytes."""


class PathConfinementError(RunStoreError):
    """A caller attempted to access a path outside the run root."""


class RunValidationError(RunStoreError):
    """A run or artifact failed its Schema or integrity contract."""


class InputValidationError(RunStoreError):
    """A source input failed the existing M0 validation path."""


class ControllerLockError(RunStoreError):
    """Another cooperating controller currently owns the run lock."""


@dataclass(frozen=True)
class ArtifactRef:
    path: str
    sha256: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}

    @classmethod
    def from_value(cls, value: "ArtifactRef | Mapping[str, Any]") -> "ArtifactRef":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping) or not isinstance(value.get("path"), str) or not isinstance(value.get("sha256"), str):
            raise RunStoreError("artifact reference must contain string path and sha256")
        return cls(value["path"], value["sha256"])


@dataclass(frozen=True, init=False)
class SpecRunInputs:
    spec: Path | str | Mapping[str, Any]
    target_profile: Path | str | Mapping[str, Any]
    test_bundle: Path | str | Mapping[str, Any]

    def __init__(
        self,
        spec: Path | str | Mapping[str, Any],
        target_profile: Path | str | Mapping[str, Any] | None = None,
        test_bundle: Path | str | Mapping[str, Any] | None = None,
        *,
        target: Path | str | Mapping[str, Any] | None = None,
    ) -> None:
        if target_profile is None:
            target_profile = target
        if target_profile is None or test_bundle is None:
            raise TypeError("spec, target_profile, and test_bundle are required")
        object.__setattr__(self, "spec", spec)
        object.__setattr__(self, "target_profile", target_profile)
        object.__setattr__(self, "test_bundle", test_bundle)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_source(source: Path | str | Mapping[str, Any]) -> tuple[Any, bytes, str]:
    if isinstance(source, Mapping):
        try:
            return dict(source), canonical_json_bytes(source), "<memory>"
        except (TypeError, ValueError) as exc:
            raise InputValidationError(f"input is not canonical JSON: {exc}") from exc
    path = Path(source)
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputValidationError(f"unable to read input {path}: {exc}") from exc
    return value, raw, os.fspath(source)


def _require_valid(report: Mapping[str, Any], label: str) -> None:
    if not report.get("valid"):
        detail = "; ".join(
            f"{item.get('code')}: {item.get('message')}" for item in report.get("errors", [])
        )
        raise InputValidationError(f"{label} failed validation: {detail}")


class RunStore:
    """Own all filesystem mutation below one committed run directory."""

    def __init__(self, run_dir: Path | str):
        self.root = Path(run_dir).resolve()
        self.run_path = self.root / "run.json"
        self._controller_lock_depth = 0

    @property
    def run_id(self) -> str:
        return self.root.name

    @classmethod
    def open(cls, runs_root: Path | str, run_id: str) -> "RunStore":
        return cls(Path(runs_root) / run_id)

    @staticmethod
    def _directory_fsync(path: Path) -> None:
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            return
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @classmethod
    def _write_atomic_at(cls, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            cls._directory_fsync(path.parent)
        except BaseException:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise

    def _confined(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path or "\x00" in relative_path:
            raise PathConfinementError("artifact path must be a non-empty relative string")
        candidate_path = PurePath(relative_path)
        if candidate_path.is_absolute() or ".." in candidate_path.parts:
            raise PathConfinementError(f"artifact path escapes run root: {relative_path!r}")
        candidate = (self.root / Path(relative_path)).resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise PathConfinementError(f"artifact path escapes run root: {relative_path!r}") from exc
        return candidate

    def _canonical_run_bytes(self, run: Mapping[str, Any]) -> bytes:
        errors = _schema_errors(dict(run), "run.schema.json")
        if errors:
            raise RunValidationError("invalid Run v4: " + "; ".join(item["message"] for item in errors))
        try:
            return canonical_json_bytes(dict(run))
        except (TypeError, ValueError) as exc:
            raise RunValidationError(str(exc)) from exc

    def load_run(self) -> dict[str, Any]:
        try:
            value = json.loads(self.run_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RunValidationError(f"unable to load run.json: {exc}") from exc
        if not isinstance(value, dict):
            raise RunValidationError("run.json must contain an object")
        self._canonical_run_bytes(value)
        try:
            verify_config_snapshot(value["config_snapshot"], value["config_snapshot_sha256"])
        except ConfigSnapshotDrift:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise RunValidationError(str(exc)) from exc
        return value

    def replace_run(self, run: Mapping[str, Any]) -> None:
        self._write_atomic_at(self.run_path, self._canonical_run_bytes(run))

    def publish_immutable_bytes(self, relative_path: str, data: bytes) -> ArtifactRef:
        path = self._confined(relative_path)
        if path.exists():
            if not path.is_file():
                raise ArtifactConflict(f"immutable artifact path is not a file: {relative_path}")
            existing = path.read_bytes()
            if existing != data:
                raise ArtifactConflict(f"immutable artifact differs at {relative_path}")
            return ArtifactRef(relative_path, sha256_bytes(existing))
        self._write_atomic_at(path, data)
        return ArtifactRef(relative_path, sha256_bytes(data))

    def publish_immutable_json(
        self,
        relative_path: str,
        value: object,
        *,
        schema_name: str | None = None,
    ) -> ArtifactRef:
        if schema_name is not None:
            errors = _schema_errors(value, schema_name)
            if errors:
                raise RunValidationError(
                    f"invalid {schema_name}: " + "; ".join(item["message"] for item in errors)
                )
        try:
            data = canonical_json_bytes(value)
        except (TypeError, ValueError) as exc:
            raise RunValidationError(f"JSON artifact is not canonical: {exc}") from exc
        return self.publish_immutable_bytes(relative_path, data)

    def replace_json(
        self,
        relative_path: str,
        value: object,
        *,
        schema_name: str | None = None,
    ) -> ArtifactRef:
        """Atomically replace one canonical mutable JSON control artifact."""

        if schema_name is not None:
            errors = _schema_errors(value, schema_name)
            if errors:
                raise RunValidationError(
                    f"invalid {schema_name}: " + "; ".join(item["message"] for item in errors)
                )
        try:
            data = canonical_json_bytes(value)
        except (TypeError, ValueError) as exc:
            raise RunValidationError(f"JSON artifact is not canonical: {exc}") from exc
        path = self._confined(relative_path)
        self._write_atomic_at(path, data)
        return ArtifactRef(relative_path, sha256_bytes(data))

    def stage_revision_candidate(self, event_seq: int, bundle: Mapping[str, object]) -> ArtifactRef:
        """Write one complete non-authoritative candidate into its pending directory."""

        from .speclib.revision_mechanism import RevisionMechanismError, validate_revision_candidate

        candidate_value = bundle.get("candidate.json")
        if not isinstance(candidate_value, Mapping):
            raise RunValidationError("revision candidate bundle has no candidate.json commit marker")
        try:
            candidate = validate_revision_candidate(candidate_value)
        except RevisionMechanismError as exc:
            raise RunValidationError(str(exc)) from exc
        if candidate["selected_event_seq"] != event_seq:
            raise RunValidationError("candidate event sequence disagrees with staging identity")
        expected_names = set(candidate["content_hashes"])
        if set(bundle) != expected_names | {"candidate.json"}:
            raise RunValidationError("candidate bundle filenames do not equal its content-hash closure")
        encoded: dict[str, bytes] = {}
        for name in expected_names:
            data = canonical_json_bytes(bundle[name])
            if sha256_bytes(data) != candidate["content_hashes"][name]:
                raise RunValidationError(f"candidate content hash differs for {name}")
            encoded[name] = data
        pending_relative = f"plan/_s4r/.candidate_{event_seq}.pending"
        pending = self._confined(pending_relative)
        final = self._confined(f"plan/_s4r/candidate_{event_seq}")
        if final.exists():
            committed, ref = self._validate_revision_candidate_directory(final, event_seq)
            if committed != candidate or any((final / name).read_bytes() != data for name, data in encoded.items()):
                raise ArtifactConflict("committed revision candidate conflicts with replay")
            return ref
        pending.mkdir(parents=True, exist_ok=True)
        for name in sorted(expected_names, key=lambda value: value.encode("utf-8")):
            target = pending / name
            data = encoded[name]
            if target.exists() and target.read_bytes() != data:
                raise ArtifactConflict(f"staged revision candidate conflicts at {name}")
            self._write_atomic_at(target, data)
        marker_bytes = canonical_json_bytes(candidate)
        marker = pending / "candidate.json"
        if marker.exists() and marker.read_bytes() != marker_bytes:
            raise ArtifactConflict("staged candidate marker conflicts with replay")
        self._write_atomic_at(marker, marker_bytes)
        self._directory_fsync(pending)
        _candidate, ref = self._validate_revision_candidate_directory(pending, event_seq)
        return ArtifactRef(f"{pending_relative}/candidate.json", ref.sha256)

    def _validate_revision_candidate_directory(
        self,
        directory: Path,
        event_seq: int,
    ) -> tuple[dict[str, Any], ArtifactRef]:
        """Validate one staged/final candidate's closed file and hash set."""

        from .speclib.revision_mechanism import RevisionMechanismError, validate_revision_candidate

        marker = directory / "candidate.json"
        if not directory.is_dir() or not marker.is_file():
            raise ArtifactConflict("revision candidate directory has no commit marker")
        try:
            marker_bytes = marker.read_bytes()
            raw = json.loads(marker_bytes)
            if canonical_json_bytes(raw) != marker_bytes:
                raise ArtifactConflict("revision candidate marker is not canonical")
            candidate = validate_revision_candidate(raw)
        except (OSError, UnicodeError, json.JSONDecodeError, RevisionMechanismError) as exc:
            raise ArtifactConflict(f"revision candidate marker is invalid: {exc}") from exc
        if candidate["selected_event_seq"] != event_seq:
            raise ArtifactConflict("revision candidate marker has the wrong event identity")
        expected = set(candidate["content_hashes"]) | {"candidate.json"}
        process_files = {"gates.json", "rehearsal.json", "activation.json", "critic.json"}
        process_directories = {"isolated"}
        actual = {path.name for path in directory.iterdir() if path.is_file()}
        directories = {path.name for path in directory.iterdir() if path.is_dir()}
        extra_files = actual - expected - process_files
        if (
            not expected.issubset(actual)
            or any(re.fullmatch(r"rehearsal-[12]-(?:build|smoke)-[1-9][0-9]*\.json", name) is None for name in extra_files)
            or directories - process_directories
        ):
            raise ArtifactConflict("revision candidate directory is not a closed bundle")
        for name, expected_hash in candidate["content_hashes"].items():
            if sha256_bytes((directory / name).read_bytes()) != expected_hash:
                raise ArtifactConflict(f"revision candidate content hash differs for {name}")
        relative = directory.relative_to(self.root).as_posix() + "/candidate.json"
        return candidate, ArtifactRef(relative, sha256_bytes(marker_bytes))

    def read_revision_candidate(self, event_seq: int) -> tuple[dict[str, Any], dict[str, Any], ArtifactRef]:
        """Read and hash-verify one committed candidate and its content closure."""

        directory = self._confined(f"plan/_s4r/candidate_{event_seq}")
        candidate, ref = self._validate_revision_candidate_directory(directory, event_seq)
        bundle = {}
        for name in candidate["content_hashes"]:
            path = directory / name
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ArtifactConflict(f"revision candidate content is invalid at {name}: {exc}") from exc
            if canonical_json_bytes(value) != path.read_bytes():
                raise ArtifactConflict(f"revision candidate content is not canonical at {name}")
            bundle[name] = value
        bundle["candidate.json"] = candidate
        return candidate, bundle, ref

    def publish_revision_candidate_evidence(
        self,
        event_seq: int,
        name: str,
        value: Mapping[str, Any],
        *,
        schema_name: str,
    ) -> ArtifactRef:
        """Idempotently publish one closed M1-11 evidence artifact."""

        if name not in {"gates.json", "rehearsal.json", "activation.json", "critic.json"}:
            raise RunValidationError("unsupported revision-candidate evidence name")
        self._validate_revision_candidate_directory(self._confined(f"plan/_s4r/candidate_{event_seq}"), event_seq)
        return self.publish_immutable_json(
            f"plan/_s4r/candidate_{event_seq}/{name}", value, schema_name=schema_name
        )

    @staticmethod
    def _candidate_matches_trigger(candidate: Mapping[str, Any], entry: Mapping[str, Any]) -> bool:
        payload = entry.get("payload", {})
        return (
            candidate.get("selected_trigger")
            == {"code": payload.get("hit_code"), "signature": payload.get("hit_signature")}
            and candidate.get("source", {}).get("plan_ref") == payload.get("plan_ref")
            and candidate.get("source", {}).get("revision_seq") == payload.get("boundary_key", {}).get("revision_seq")
        )

    @staticmethod
    def _candidate_matches_ledger_prefix(
        candidate: Mapping[str, Any], ledger: Mapping[str, Any], selected: Mapping[str, Any]
    ) -> bool:
        boundary_key = selected.get("payload", {}).get("boundary_key")
        entries = list(ledger.get("entries", []))
        selected_index = next(
            (index for index, entry in enumerate(entries) if entry.get("event_seq") == selected.get("event_seq")),
            -1,
        )
        if selected_index < 0:
            return False
        batch_start = selected_index
        while batch_start > 0:
            previous = entries[batch_start - 1]
            if previous.get("event_type") != "trigger_evaluated" or previous.get("payload", {}).get("boundary_key") != boundary_key:
                break
            batch_start -= 1
        prefix = {"schema_version": ledger.get("schema_version"), "entries": entries[:batch_start]}
        return candidate.get("source", {}).get("ledger_prefix_sha256") == sha256_bytes(canonical_json_bytes(prefix))

    def commit_revision_candidate(self, event_seq: int) -> ArtifactRef:
        """Atomically publish one staged candidate after its selected trigger exists."""

        pending = self._confined(f"plan/_s4r/.candidate_{event_seq}.pending")
        final = self._confined(f"plan/_s4r/candidate_{event_seq}")
        ledger = self._read_json_artifact("plan/revision_ledger.json", schema_name="revision-ledger.schema.json")
        selected = [entry for entry in ledger["entries"] if entry["event_seq"] == event_seq and entry["event_type"] == "trigger_evaluated" and entry["payload"]["selected"] is True]
        if len(selected) != 1:
            raise RunValidationError("candidate commit requires its unique accepted selected trigger")
        pending_candidate = None
        if pending.exists():
            pending_candidate, _pending_ref = self._validate_revision_candidate_directory(pending, event_seq)
            if not self._candidate_matches_trigger(pending_candidate, selected[0]) or not self._candidate_matches_ledger_prefix(pending_candidate, ledger, selected[0]):
                raise ArtifactConflict("staged candidate disagrees with its selected trigger")
        if final.exists():
            final_candidate, final_ref = self._validate_revision_candidate_directory(final, event_seq)
            if not self._candidate_matches_trigger(final_candidate, selected[0]) or not self._candidate_matches_ledger_prefix(final_candidate, ledger, selected[0]):
                raise ArtifactConflict("committed candidate disagrees with its selected trigger")
            if pending_candidate is not None:
                names = set(final_candidate["content_hashes"]) | {"candidate.json"}
                if final_candidate != pending_candidate or any((final / name).read_bytes() != (pending / name).read_bytes() for name in names):
                    raise ArtifactConflict("staged and committed candidate bytes conflict")
                shutil.rmtree(pending)
                self._directory_fsync(final.parent)
            return final_ref
        if pending_candidate is None:
            raise RunValidationError("revision candidate has not been completely staged")
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(pending, final)
        self._directory_fsync(final.parent)
        _candidate, ref = self._validate_revision_candidate_directory(final, event_seq)
        return ref

    def reconcile_revision_candidate(self, event_seq: int) -> ArtifactRef | None:
        """Converge the sole legal staged/final/ledger candidate state forward."""

        pending = self._confined(f"plan/_s4r/.candidate_{event_seq}.pending")
        final = self._confined(f"plan/_s4r/candidate_{event_seq}")
        ledger = self._read_json_artifact("plan/revision_ledger.json", schema_name="revision-ledger.schema.json")
        selected_entries = [entry for entry in ledger["entries"] if entry["event_seq"] == event_seq and entry["event_type"] == "trigger_evaluated" and entry["payload"]["selected"] is True]
        if len(selected_entries) > 1:
            raise ArtifactConflict("candidate has multiple selected trigger anchors")
        selected = selected_entries[0] if selected_entries else None
        pending_candidate = self._validate_revision_candidate_directory(pending, event_seq)[0] if pending.exists() else None
        final_candidate = self._validate_revision_candidate_directory(final, event_seq)[0] if final.exists() else None
        if final.exists():
            if selected is None or final_candidate is None or not self._candidate_matches_trigger(final_candidate, selected) or not self._candidate_matches_ledger_prefix(final_candidate, ledger, selected):
                raise ArtifactConflict("committed candidate has no accepted selected trigger or marker")
            if pending_candidate is not None:
                names = set(final_candidate["content_hashes"]) | {"candidate.json"}
                if final_candidate != pending_candidate or any((final / name).read_bytes() != (pending / name).read_bytes() for name in names):
                    raise ArtifactConflict("staged and committed candidate bytes conflict")
                shutil.rmtree(pending)
                self._directory_fsync(final.parent)
            marker = final / "candidate.json"
            return ArtifactRef(f"plan/_s4r/candidate_{event_seq}/candidate.json", sha256_bytes(marker.read_bytes()))
        if pending_candidate is not None and selected is not None:
            if not self._candidate_matches_trigger(pending_candidate, selected) or not self._candidate_matches_ledger_prefix(pending_candidate, ledger, selected):
                raise ArtifactConflict("staged candidate disagrees with its selected trigger")
            return self.commit_revision_candidate(event_seq)
        return None

    def append_state_history(
        self,
        state: Mapping[str, Any],
        *,
        event_type: str,
        event: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one semantic State snapshot without introducing another hash chain."""

        history_path = self._confined("plan/state_history.json")
        if history_path.exists():
            history = self._read_json_artifact("plan/state_history.json", schema_name="state-history.schema.json")
        else:
            history = {"schema_version": "1.0", "entries": []}
        entries = history["entries"]
        if entries and entries[-1]["state"] == state:
            return history
        entry = {
            "event_seq": len(entries) + 1,
            "event_type": event_type,
            "plan_ref": copy.deepcopy(dict(state["plan_ref"])),
            "event": copy.deepcopy(dict(event or {})),
            "state": copy.deepcopy(dict(state)),
        }
        updated = {"schema_version": "1.0", "entries": [*entries, entry]}
        self.replace_json("plan/state_history.json", updated, schema_name="state-history.schema.json")
        return updated

    def replace_plan_state(
        self,
        state: Mapping[str, Any],
        *,
        event_type: str,
        event: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        """Append history before replacing the mutable State projection."""

        self.append_state_history(state, event_type=event_type, event=event)
        return self.replace_json("plan/plan_state.json", state, schema_name="plan-state.schema.json")

    def allocate_s6_attempt(
        self,
        *,
        task_id: str,
        task_uid: str,
        role: str,
        tier: str,
        baseline_commit: str,
        baseline_tree: str,
        proof: Mapping[str, Any] | None = None,
        lease_authorization: Mapping[str, Any] | None = None,
        lease_authorization_ref: Mapping[str, Any] | None = None,
        fault_hook: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Atomically consume one S6 call and evidence sequence before I/O."""

        from .speclib.plan_revision import append_lease_started
        from .speclib.plan_state import PlanStateError, plan_state_snapshot_lint, project_state_transition, validate_lease_authorization

        state = self._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
        run = self.load_run()
        if not re.fullmatch(r"[0-9a-f]{40}", baseline_commit) or not re.fullmatch(r"[0-9a-f]{64}", baseline_tree):
            raise RunValidationError("S6 attempt baseline is not a canonical commit/tree binding")
        budgets = run["config_snapshot"].get("budgets", {})
        cap = budgets.get("s6_total_attempts_cap") if isinstance(budgets, Mapping) else None
        if not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0:
            raise RunValidationError("S6 total attempt cap is not configured")
        rows = {row["id"]: row for row in state.get("tasks", [])}
        row = rows.get(task_id)
        if not isinstance(row, Mapping) or row.get("task_uid") != task_uid or row.get("execution_mode") != "normal":
            raise RunValidationError("S6 task identity is not bound by Plan State")
        if row.get("status") == "in_progress" and int(row.get("attempts", 0)) > 0:
            current_attempt = int(row["attempts"])
            current_path = f"attempts/{task_uid}/attempt_{current_attempt:03d}.json"
            if self._confined(current_path).is_file():
                existing = self._read_json_artifact(current_path, schema_name="s6-attempt.schema.json")
                if existing.get("status") == "started":
                    if (
                        existing.get("task_id") != task_id
                        or existing.get("task_uid") != task_uid
                        or existing.get("role") != role
                        or existing.get("tier") != tier
                        or existing.get("baseline_commit") != baseline_commit
                        or existing.get("baseline_tree") != baseline_tree
                    ):
                        raise ArtifactConflict("in-progress S6 attempt conflicts with its persisted allocation")
                    return {"state": state, "attempt": existing, "attempt_ref": ArtifactRef(current_path, self._json_artifact_hash(current_path))}
        if state.get("s6_attempts_used") >= cap:
            raise RunValidationError("S6 total attempt cap is exhausted")
        limit = min(4, int(budgets.get("task_fix_attempts", 3)) + 1)
        if row.get("status") not in {"pending", "in_progress"} or row.get("attempts", 0) >= limit:
            raise RunValidationError("S6 task attempt limit is exhausted")
        next_attempt = int(row.get("attempts", 0)) + 1
        expected_role = "coder" if next_attempt == 1 else "fixer"
        expected_tier = "T2" if next_attempt <= 3 else "T1"
        if role != expected_role or tier != expected_tier:
            raise RunValidationError("S6 attempt role or tier does not match the bounded route")
        if lease_authorization is not None and (role != "fixer" or next_attempt < 2):
            raise RunValidationError("F1 lease requires an existing Fixer attempt")
        if lease_authorization is not None:
            plan = self._read_json_artifact(state["plan_ref"]["path"], schema_name="plan.schema.json")
            file_ledger = self._read_json_artifact("plan/file_ledger.json", schema_name="file-ledger.schema.json")
            revision_ledger = self._read_json_artifact("plan/revision_ledger.json", schema_name="revision-ledger.schema.json")
            try:
                validate_lease_authorization(
                    lease_authorization,
                    plan=plan,
                    state=state,
                    file_ledger=file_ledger,
                    revision_ledger=revision_ledger,
                    config_snapshot=run["config_snapshot"],
                    baseline_commit=baseline_commit,
                    baseline_tree=baseline_tree,
                )
            except PlanStateError as exc:
                raise RunValidationError(str(exc)) from exc
        sequence = int(state.get("evidence_counters", {}).get(task_uid, 0)) + 1
        previous_failure_ref = None
        if next_attempt > 1:
            previous_record = self._read_json_artifact(
                f"attempts/{task_uid}/attempt_{next_attempt - 1:03d}.json",
                schema_name="s6-attempt.schema.json",
            )
            previous_failure_ref = previous_record.get("failure_ref")
            if previous_record.get("status") != "failed" or not isinstance(previous_failure_ref, Mapping):
                raise RunValidationError("S6 retry requires the immediately preceding failed attempt")
        event = {
            "schema_version": "2.0", "event": "attempt_started", "task_id": task_id,
            "attempt": next_attempt,
            "proof": {
                "baseline_commit": baseline_commit, "baseline_tree": baseline_tree,
                "evidence_seq": sequence, "s6_attempts_used": state["s6_attempts_used"] + 1,
                **({"previous_failure_ref": dict(previous_failure_ref)} if previous_failure_ref else {}),
            },
        }
        next_state = project_state_transition(state, event, config_snapshot=run["config_snapshot"])
        snapshot_report = plan_state_snapshot_lint(
            self._read_json_artifact(state["plan_ref"]["path"], schema_name="plan.schema.json"),
            next_state,
            config_snapshot=run["config_snapshot"],
        )
        if not snapshot_report["valid"]:
            raise PlanStateError("S6 attempt allocation produced invalid State: " + snapshot_report["errors"][0]["message"])
        lease_record: dict[str, Any] | None = None
        next_revision = self._read_json_artifact("plan/revision_ledger.json", schema_name="revision-ledger.schema.json")
        if lease_authorization is not None:
            if not isinstance(lease_authorization_ref, Mapping):
                raise RunValidationError("F1 lease requires an authorization artifact reference")
            lenders = lease_authorization.get("lenders")
            if not isinstance(lenders, list) or not lenders:
                raise RunValidationError("F1 lease authorization has no lenders")
            leased_uids = [str(item["task_uid"]) for item in lenders if isinstance(item, Mapping)]
            leased_paths = [str(path) for item in lenders if isinstance(item, Mapping) for path in item.get("paths", [])]
            next_revision = append_lease_started(
                next_revision,
                task_uid=task_uid,
                leased_uids=leased_uids,
                leased_paths=leased_paths,
                baseline_commit=baseline_commit,
                execution_count=next_attempt,
                authorization_ref=lease_authorization_ref,
            )
            lease_id = next_revision["entries"][-1]["payload"]["lease_id"]
            lease_record = {"lease_id": lease_id, "authorization_ref": dict(lease_authorization_ref), "lenders": [{"task_uid": str(item["task_uid"]), "paths": list(item.get("paths", []))} for item in lenders]}
        attempt = {
            "schema_version": "2.0", "task_id": task_id, "task_uid": task_uid, "execution_mode": "normal",
            "attempt": next_attempt,
            "evidence_seq": sequence, "role": role, "tier": tier, "plan_ref": copy.deepcopy(state["plan_ref"]), "migration_ref": copy.deepcopy(row.get("migration_ref")), "baseline_commit": baseline_commit,
            "baseline_tree": baseline_tree, "status": "started", "output_ref": None, "failure_ref": None,
        }
        if lease_record is not None:
            attempt["lease"] = lease_record
        attempt_path = f"attempts/{task_uid}/attempt_{attempt['attempt']:03d}.json"
        if self._confined(attempt_path).exists():
            existing = self._read_json_artifact(attempt_path, schema_name="s6-attempt.schema.json")
            identity_fields = ("task_id", "task_uid", "attempt", "evidence_seq", "role", "tier", "plan_ref", "migration_ref", "baseline_commit", "baseline_tree", "lease")
            if any(existing.get(key) != attempt.get(key) for key in identity_fields):
                raise ArtifactConflict("partially published S6 attempt conflicts with the requested allocation")
            attempt = existing
            attempt_ref = ArtifactRef(attempt_path, self._json_artifact_hash(attempt_path))
        else:
            attempt_ref = self.replace_json(attempt_path, attempt, schema_name="s6-attempt.schema.json")
        if fault_hook is not None:
            fault_hook("attempt_persisted")
        self.append_state_history(next_state, event_type="attempt_started", event=event)
        if fault_hook is not None:
            fault_hook("state_history_appended")
        if lease_record is not None:
            self.replace_json("plan/revision_ledger.json", next_revision, schema_name="revision-ledger.schema.json")
            if fault_hook is not None:
                fault_hook("lease_started_persisted")
        self.replace_json("plan/plan_state.json", next_state, schema_name="plan-state.schema.json")
        if fault_hook is not None:
            fault_hook("state_replaced")
        return {"state": next_state, "attempt": attempt, "attempt_ref": attempt_ref}

    def allocate_s6_migration(
        self,
        *,
        task_id: str,
        task_uid: str,
        mode: str,
        baseline_commit: str,
        baseline_tree: str,
        fault_hook: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Allocate one AMEND call or deterministic REVALIDATE sequence before I/O."""

        from .speclib.plan_state import plan_state_snapshot_lint, project_state_transition

        if mode not in {"amend", "revalidate"}:
            raise RunValidationError("migration allocation mode is unsupported")
        if not re.fullmatch(r"[0-9a-f]{40}", baseline_commit) or not re.fullmatch(r"[0-9a-f]{64}", baseline_tree):
            raise RunValidationError("migration baseline is not a canonical commit/tree binding")
        state = self._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
        run = self.load_run()
        rows = {row["id"]: row for row in state.get("tasks", [])}
        row = rows.get(task_id)
        if not isinstance(row, Mapping) or row.get("task_uid") != task_uid or row.get("execution_mode") != mode or not isinstance(row.get("migration_ref"), Mapping):
            raise RunValidationError("migration allocation is not bound by pending Plan State")
        if row.get("status") == "in_progress":
            sequence = int(state.get("evidence_counters", {}).get(task_uid, 0))
            path = f"attempts/{task_uid}/amendment.json" if mode == "amend" else f"validations/{task_uid}/validation_{sequence:03d}.json"
            schema = "s6-attempt.schema.json" if mode == "amend" else "s6-validation.schema.json"
            existing = self._read_json_artifact(path, schema_name=schema)
            if (
                existing.get("task_id") != task_id
                or existing.get("task_uid") != task_uid
                or existing.get("evidence_seq") != sequence
                or existing.get("migration_ref") != row.get("migration_ref")
                or existing.get("baseline_commit") != baseline_commit
                or existing.get("baseline_tree") != baseline_tree
                or existing.get("status") != "started"
            ):
                raise ArtifactConflict("in-progress migration allocation conflicts with its persisted record")
            return {"state": state, "record": existing, "record_ref": ArtifactRef(path, self._json_artifact_hash(path))}
        if row.get("status") != "pending":
            raise RunValidationError("migration allocation is not pending or replayable")
        sequence = int(state.get("evidence_counters", {}).get(task_uid, 0)) + 1
        proof = {"baseline_commit": baseline_commit, "baseline_tree": baseline_tree, "evidence_seq": sequence, "migration_ref": copy.deepcopy(row["migration_ref"])}
        if mode == "amend":
            budgets = run["config_snapshot"].get("budgets", {})
            cap = budgets.get("s6_total_attempts_cap") if isinstance(budgets, Mapping) else None
            if not isinstance(cap, int) or isinstance(cap, bool) or state["s6_attempts_used"] >= cap:
                raise RunValidationError("S6 total attempt cap is exhausted")
            proof["s6_attempts_used"] = state["s6_attempts_used"] + 1
            event_name = "amendment_started"
        else:
            event_name = "validation_started"
        event = {"schema_version": "2.0", "event": event_name, "task_id": task_id, "proof": proof}
        next_state = project_state_transition(state, event, config_snapshot=run["config_snapshot"])
        plan = self._read_json_artifact(state["plan_ref"]["path"], schema_name="plan.schema.json")
        report = plan_state_snapshot_lint(plan, next_state, config_snapshot=run["config_snapshot"])
        if not report["valid"]:
            raise RunValidationError("migration allocation produced invalid State: " + report["errors"][0]["message"])
        if mode == "amend":
            record = {"schema_version": "2.0", "task_id": task_id, "task_uid": task_uid, "execution_mode": "amend", "attempt": int(row["attempts"]), "evidence_seq": sequence, "role": "fixer", "tier": "T1", "plan_ref": copy.deepcopy(state["plan_ref"]), "migration_ref": copy.deepcopy(row["migration_ref"]), "baseline_commit": baseline_commit, "baseline_tree": baseline_tree, "status": "started", "output_ref": None, "failure_ref": None}
            path = f"attempts/{task_uid}/amendment.json"
            schema = "s6-attempt.schema.json"
        else:
            record = {"schema_version": "1.0", "task_id": task_id, "task_uid": task_uid, "evidence_seq": sequence, "migration_ref": copy.deepcopy(row["migration_ref"]), "baseline_commit": baseline_commit, "baseline_tree": baseline_tree, "status": "started", "build_result_refs": [], "smoke_result_refs": [], "failure_ref": None, "evidence_ref": None}
            path = f"validations/{task_uid}/validation_{sequence:03d}.json"
            schema = "s6-validation.schema.json"
        if self._confined(path).exists():
            existing = self._read_json_artifact(path, schema_name=schema)
            if existing != record:
                raise ArtifactConflict("partially published migration allocation conflicts with requested facts")
            record_ref = ArtifactRef(path, self._json_artifact_hash(path))
        else:
            record_ref = self.replace_json(path, record, schema_name=schema)
        if fault_hook is not None:
            fault_hook("migration_record_persisted")
        self.append_state_history(next_state, event_type=event_name, event=event)
        if fault_hook is not None:
            fault_hook("state_history_appended")
        self.replace_json("plan/plan_state.json", next_state, schema_name="plan-state.schema.json")
        if fault_hook is not None:
            fault_hook("state_replaced")
        return {"state": next_state, "record": record, "record_ref": record_ref}

    @staticmethod
    def _s5_fault(fault_hook: Callable[[str], None] | None, point: str) -> None:
        if fault_hook is not None:
            fault_hook(point)

    def publish_s5_e0(self, bundle: Mapping[str, Any], fault_hook: Callable[[str], None] | None = None) -> dict[str, ArtifactRef]:
        """Publish one deterministic E0 suffix after the caller's run lock is held."""

        from .speclib.materialization import build_artifact_manifest, build_contract_map, project_e0_file_ledger
        from .tools.git_ops import checkpoint_workspace

        workspace_relative = str(bundle.get("workspace", "workspace"))
        workspace = self._confined(workspace_relative)
        rendered_files = bundle.get("rendered_files")
        view = bundle.get("rendering_view")
        blueprint = bundle.get("blueprint")
        plan_ref = bundle.get("plan_ref")
        build_results = bundle.get("build_results", [])
        smoke_results = bundle.get("smoke_results", [])
        if not isinstance(rendered_files, Mapping) or not isinstance(view, Mapping) or not isinstance(blueprint, Mapping) or not isinstance(plan_ref, Mapping):
            raise RunValidationError("S5 publication bundle is incomplete")
        if not all(isinstance(path, str) and isinstance(data, bytes) for path, data in rendered_files.items()):
            raise RunValidationError("S5 rendered files must be a path-to-bytes map")
        expected_files = [
            {"path": path, "sha256": sha256_bytes(data), "content": data.decode("utf-8")}
            for path, data in sorted(rendered_files.items(), key=lambda item: item[0].encode("utf-8"))
        ]
        run = self.load_run()
        pending = {
            "schema_version": "1.0", "epoch": "E0", "phase": "pending", "plan_ref": dict(plan_ref),
            "input_refs": {
                key: {"path": path, "sha256": run["inputs"][key]["sha256"]}
                for key, path in (("spec", "spec/spec.json"), ("target_profile", "inputs/target.json"), ("test_bundle", "inputs/test_bundle.json"))
            },
            "blueprint": dict(blueprint), "constraints": dict(bundle.get("constraints", {})),
            "blueprint_sha256": self._canonical_value_hash(blueprint), "rendering_view": dict(view),
            "rendering_view_sha256": self._canonical_value_hash(view), "expected_files": expected_files,
            "build_results": list(build_results), "smoke_results": list(smoke_results),
            "checkpoint_commit": None, "checkpoint_tree": None, "output_refs": {},
        }
        pending_path = "plan/epochs/E0/pending.json"
        existing_pending: dict[str, Any] | None = None
        if self._confined(pending_path).exists():
            existing_pending = self._read_json_artifact(pending_path, schema_name="s5-pending-state.schema.json")
            stable_keys = ("plan_ref", "input_refs", "blueprint", "constraints", "blueprint_sha256", "rendering_view", "rendering_view_sha256", "expected_files", "build_results", "smoke_results")
            if any(existing_pending.get(key) != pending[key] for key in stable_keys):
                raise ArtifactConflict("pending E0 record does not match the current sealed materialization")
        if existing_pending is None or existing_pending.get("checkpoint_commit") is None:
            self.replace_json(pending_path, pending, schema_name="s5-pending-state.schema.json")
            self._s5_fault(fault_hook, "pending_written")
        else:
            pending = existing_pending
        from .tools.git_ops import verify_checkpoint
        if pending.get("checkpoint_commit") is not None and pending.get("checkpoint_tree") is not None:
            checkpoint = {"commit_sha": pending["checkpoint_commit"], "tree_sha": pending["checkpoint_tree"]}
            verify_checkpoint(workspace, {"commit_sha": checkpoint["commit_sha"], "tree_sha": checkpoint["tree_sha"]})
            actual = {
                item.relative_to(workspace).as_posix(): item.read_bytes()
                for item in workspace.rglob("*") if item.is_file() and ".git" not in item.parts
            }
            if actual != dict(rendered_files):
                raise ArtifactConflict("checkpointed E0 workspace differs from its recorded source tree")
        else:
            workspace.mkdir(parents=True, exist_ok=True)
            for path, data in rendered_files.items():
                target = (workspace / path).resolve()
                try:
                    target.relative_to(workspace.resolve())
                except ValueError as exc:
                    raise PathConfinementError(f"rendered path escapes workspace: {path}") from exc
                if target.exists() and target.read_bytes() != data:
                    raise ArtifactConflict(f"rendered workspace file differs at {path}")
                self._write_atomic_at(target, data)
            checkpoint = checkpoint_workspace(workspace, rendered_files.keys(), plan_version="1.0.0", epoch="E0")
        pending.update({"phase": "checkpointed", "checkpoint_commit": checkpoint["commit_sha"], "checkpoint_tree": checkpoint["tree_sha"]})
        self.replace_json(pending_path, pending, schema_name="s5-pending-state.schema.json")
        self._s5_fault(fault_hook, "checkpoint_created")

        build_refs: list[dict[str, str]] = []
        for result in build_results:
            variant = str(result.get("variant", "unknown"))
            ref = self.publish_immutable_json(f"plan/epochs/E0/build/{variant}.json", result, schema_name="build-result.schema.json")
            build_refs.append(ref.as_dict())
            self._s5_fault(fault_hook, f"build_evidence_published:{variant}")
        smoke_refs: list[dict[str, str]] = []
        for index, result in enumerate(smoke_results):
            variant = str(result.get("variant", "unknown")); artifact = str(result.get("artifact", index)).replace("/", "_")
            ref = self.publish_immutable_json(f"plan/epochs/E0/smoke/{variant}_{artifact}.json", result, schema_name="smoke-result.schema.json")
            smoke_refs.append(ref.as_dict())
            self._s5_fault(fault_hook, f"smoke_evidence_published:{variant}:{artifact}")
        view_with_files = {**dict(view), "rendered_files": dict(rendered_files)}
        manifest = build_artifact_manifest(plan_ref, blueprint, view_with_files, "E0")
        contract_map = build_contract_map(plan_ref, blueprint, view_with_files, "E0")
        manifest_ref = self.publish_immutable_json("plan/bindings/1.0.0/artifact_manifest.json", manifest, schema_name="artifact-manifest.schema.json")
        map_ref = self.publish_immutable_json("plan/bindings/1.0.0/contract_map.json", contract_map, schema_name="contract-map.schema.json")
        self.publish_immutable_json("plan/epochs/E0/blueprint.json", blueprint, schema_name="delivery-blueprint.schema.json")
        self._s5_fault(fault_hook, "manifest_map_published")
        epoch_receipt = {
            "schema_version": "1.0", "epoch": "E0", "materialized_plan_ref": dict(plan_ref),
            "blueprint_sha256": self._canonical_value_hash(blueprint), "checkpoint_commit": checkpoint["commit_sha"],
            "checkpoint_tree": checkpoint["tree_sha"], "materialization_status": "ready",
            "build_result_refs": build_refs, "smoke_result_refs": smoke_refs, "pending_group_ids": [],
        }
        epoch_ref = self.publish_immutable_json("plan/epochs/E0/receipt.json", epoch_receipt, schema_name="epoch-receipt.schema.json")
        self._s5_fault(fault_hook, "epoch_receipt_published")
        binding_receipt = {
            "schema_version": "1.0", "plan_ref": dict(plan_ref), "epoch_receipt_ref": epoch_ref.as_dict(),
            "manifest_ref": manifest_ref.as_dict(), "contract_map_ref": map_ref.as_dict(),
        }
        binding_ref = self.publish_immutable_json("plan/bindings/1.0.0/receipt.json", binding_receipt, schema_name="binding-receipt.schema.json")
        self._s5_fault(fault_hook, "binding_receipt_published")
        initial_ledger = self._read_json_artifact("plan/file_ledger.json", schema_name="file-ledger.schema.json")
        build_evidence = {"build_variant_ids": [str(item.get("variant")) for item in build_results], "evidence_ref": build_refs[0] if build_refs else {"path": "plan/epochs/E0/receipt.json", "sha256": epoch_ref.sha256}}
        ledger = project_e0_file_ledger(initial_ledger, dict(rendered_files), checkpoint, build_evidence, epoch_ref.as_dict())
        self.replace_json("plan/file_ledger.json", ledger, schema_name="file-ledger.schema.json")
        self.replace_json("plan/artifact_manifest.json", manifest, schema_name="artifact-manifest.schema.json")
        self.replace_json("plan/contract_map.json", contract_map, schema_name="contract-map.schema.json")
        self._s5_fault(fault_hook, "mutable_e0_copies_replaced")
        pending.update({"phase": "accepted", "output_refs": {"epoch_receipt": epoch_ref.as_dict(), "binding_receipt": binding_ref.as_dict()}})
        self.replace_json(pending_path, pending, schema_name="s5-pending-state.schema.json")
        self._s5_fault(fault_hook, "pending_accepted")
        return {"epoch_receipt": epoch_ref, "binding_receipt": binding_ref}

    def publish_s5_epoch(self, bundle: Mapping[str, Any], fault_hook: Callable[[str], None] | None = None) -> dict[str, ArtifactRef]:
        """Publish one E1+ checkpoint and its immutable epoch-keyed suffix."""

        from .speclib.materialization import build_artifact_manifest, build_contract_map, project_file_ledger
        from .tools.git_ops import checkpoint_workspace, verify_checkpoint

        epoch = bundle.get("epoch")
        plan_ref = bundle.get("plan_ref")
        blueprint = bundle.get("blueprint")
        view = bundle.get("rendering_view")
        action_plan = bundle.get("action_plan")
        if not isinstance(epoch, str) or not re.fullmatch(r"E[1-9][0-9]*", epoch):
            raise RunValidationError("E1+ publication requires a canonical epoch")
        if not all(isinstance(value, Mapping) for value in (plan_ref, blueprint, view, action_plan)):
            raise RunValidationError("epoch publication bundle is incomplete")
        path_match = re.search(r"plan-(1\.[0-9]+\.[0-9]+)\.json$", str(plan_ref.get("path")))
        version = str(plan_ref.get("version") or (path_match.group(1) if path_match else ""))
        if not re.fullmatch(r"1\.[0-9]+\.[0-9]+", version):
            raise RunValidationError("epoch publication Plan ref has no canonical version")
        rendered_files = bundle.get("rendered_files", {})
        workspace_files = bundle.get("workspace_files", rendered_files)
        if not isinstance(rendered_files, Mapping) or not isinstance(workspace_files, Mapping) or any(not isinstance(path, str) or not isinstance(data, bytes) for path, data in rendered_files.items()) or any(not isinstance(path, str) or not isinstance(data, bytes) for path, data in workspace_files.items()):
            raise RunValidationError("epoch publication files must be path-to-bytes maps")
        pending_path = self._confined(f"plan/epochs/{epoch}/pending.json")
        if not pending_path.exists():
            raise RunValidationError("epoch publication requires its pending action plan")
        pending = self._read_json_artifact(f"plan/epochs/{epoch}/pending.json", schema_name="s5-pending-state.schema.json")
        if pending.get("epoch") != epoch or pending.get("plan_ref") != dict(plan_ref):
            raise ArtifactConflict("pending epoch record is not bound to the publication Plan")
        if pending.get("blueprint_sha256") != self._canonical_value_hash(blueprint):
            raise ArtifactConflict("pending epoch Blueprint binding drifted")
        if pending.get("action_plan") != action_plan.get("actions", []):
            raise ArtifactConflict("pending epoch action plan differs from the publication bundle")
        if pending.get("new_inventory") != action_plan.get("new_inventory", {}):
            raise ArtifactConflict("pending epoch inventory differs from the publication bundle")
        workspace = self._confined(str(bundle.get("workspace", "workspace")))
        actual_workspace = {
            item.relative_to(workspace).as_posix(): item.read_bytes()
            for item in workspace.rglob("*") if item.is_file() and ".git" not in item.parts
        } if workspace.is_dir() else {}
        if actual_workspace != dict(workspace_files):
            raise ArtifactConflict("epoch workspace differs from the recorded post-action tree")
        if pending.get("checkpoint_commit") and pending.get("checkpoint_tree"):
            checkpoint = {"commit_sha": pending["checkpoint_commit"], "tree_sha": pending["checkpoint_tree"], "plan_version": version, "epoch": epoch}
            verify_checkpoint(workspace, checkpoint)
        else:
            changed_paths = sorted({str(path) for action in action_plan.get("actions", []) if action.get("kind") != "preserve" for path in (action.get("path"), action.get("source_path"), action.get("target_path")) if isinstance(path, str)}, key=lambda value: value.encode("utf-8"))
            checkpoint = checkpoint_workspace(workspace, changed_paths, plan_version=version, epoch=epoch)
            pending.update({"phase": "checkpointed", "checkpoint_commit": checkpoint["commit_sha"], "checkpoint_tree": checkpoint["tree_sha"]})
            self.replace_json(f"plan/epochs/{epoch}/pending.json", pending, schema_name="s5-pending-state.schema.json")
            self._s5_fault(fault_hook, "epoch_checkpoint_created")
        build_results = bundle.get("build_results", [])
        smoke_results = bundle.get("smoke_results", [])
        if not isinstance(build_results, list) or not isinstance(smoke_results, list):
            raise RunValidationError("epoch build and smoke results must be arrays")
        build_refs: list[dict[str, str]] = []
        for result in build_results:
            if not isinstance(result, Mapping):
                raise RunValidationError("epoch build result is not an object")
            variant = str(result.get("variant", "unknown"))
            ref = self.publish_immutable_json(f"plan/epochs/{epoch}/build/{variant}.json", result, schema_name="build-result.schema.json")
            build_refs.append(ref.as_dict())
            self._s5_fault(fault_hook, f"epoch_build_evidence_published:{variant}")
        smoke_refs: list[dict[str, str]] = []
        for index, result in enumerate(smoke_results):
            if not isinstance(result, Mapping):
                raise RunValidationError("epoch smoke result is not an object")
            variant = str(result.get("variant", "unknown")); artifact = str(result.get("artifact", index)).replace("/", "_")
            ref = self.publish_immutable_json(f"plan/epochs/{epoch}/smoke/{variant}_{artifact}.json", result, schema_name="smoke-result.schema.json")
            smoke_refs.append(ref.as_dict())
            self._s5_fault(fault_hook, f"epoch_smoke_evidence_published:{variant}:{artifact}")
        view_with_files = {**dict(view), "rendered_files": dict(rendered_files)}
        manifest = build_artifact_manifest(plan_ref, blueprint, view_with_files, epoch)
        contract_map = build_contract_map(plan_ref, blueprint, view_with_files, epoch)
        self.publish_immutable_json(f"plan/epochs/{epoch}/artifact_manifest.json", manifest, schema_name="artifact-manifest.schema.json")
        self.publish_immutable_json(f"plan/epochs/{epoch}/contract_map.json", contract_map, schema_name="contract-map.schema.json")
        self._s5_fault(fault_hook, "epoch_manifest_map_published")
        status = str(bundle.get("materialization_status", "ready"))
        groups = bundle.get("pending_group_ids", [])
        if status not in {"ready", "pending_repair"} or not isinstance(groups, list) or groups != sorted(set(groups), key=lambda value: str(value).encode("utf-8")):
            raise RunValidationError("epoch materialization status or pending groups are invalid")
        epoch_receipt = {
            "schema_version": "1.0", "epoch": epoch, "materialized_plan_ref": dict(plan_ref),
            "blueprint_sha256": self._canonical_value_hash(blueprint), "checkpoint_commit": checkpoint["commit_sha"],
            "checkpoint_tree": checkpoint["tree_sha"], "materialization_status": status,
            "build_result_refs": build_refs, "smoke_result_refs": smoke_refs, "pending_group_ids": list(groups),
        }
        epoch_ref = self.publish_immutable_json(f"plan/epochs/{epoch}/receipt.json", epoch_receipt, schema_name="epoch-receipt.schema.json")
        self._s5_fault(fault_hook, "epoch_receipt_published")
        version_manifest_ref = self.publish_immutable_json(f"plan/bindings/{version}/artifact_manifest.json", manifest, schema_name="artifact-manifest.schema.json")
        version_map_ref = self.publish_immutable_json(f"plan/bindings/{version}/contract_map.json", contract_map, schema_name="contract-map.schema.json")
        binding_receipt = {"schema_version": "1.0", "plan_ref": dict(plan_ref), "epoch_receipt_ref": epoch_ref.as_dict(), "manifest_ref": version_manifest_ref.as_dict(), "contract_map_ref": version_map_ref.as_dict()}
        binding_ref = self.publish_immutable_json(f"plan/bindings/{version}/receipt.json", binding_receipt, schema_name="binding-receipt.schema.json")
        self._s5_fault(fault_hook, "binding_receipt_published")
        initial_ledger = pending.get("before_file_ledger")
        if not isinstance(initial_ledger, Mapping):
            initial_ledger = self._read_json_artifact("plan/file_ledger.json", schema_name="file-ledger.schema.json")
        build_evidence = {"build_variant_ids": [str(item.get("variant")) for item in build_results], "evidence_ref": build_refs[0] if build_refs else epoch_ref.as_dict(), "materialization_status": status}
        ledger = project_file_ledger(initial_ledger, action_plan, checkpoint, build_evidence, epoch_ref.as_dict(), epoch=epoch)
        if isinstance(pending.get("projected_file_ledger"), Mapping) and dict(pending["projected_file_ledger"]) != ledger:
            raise ArtifactConflict("pending projected file ledger is not deterministic")
        pending.update({"phase": "receipted", "projected_file_ledger": ledger})
        self.replace_json(f"plan/epochs/{epoch}/pending.json", pending, schema_name="s5-pending-state.schema.json")
        self._s5_fault(fault_hook, "epoch_ledger_projected")
        current_ledger = self._read_json_artifact("plan/file_ledger.json", schema_name="file-ledger.schema.json")
        if current_ledger != ledger:
            if current_ledger != dict(initial_ledger):
                raise ArtifactConflict("file ledger contains conflicting post-checkpoint bytes")
            self.replace_json("plan/file_ledger.json", ledger, schema_name="file-ledger.schema.json")
        for relative_path, value in (("plan/artifact_manifest.json", manifest), ("plan/contract_map.json", contract_map)):
            current = self._confined(relative_path)
            data = canonical_json_bytes(value)
            previous_value = pending.get("before_manifest" if relative_path.endswith("artifact_manifest.json") else "before_contract_map")
            previous_data = canonical_json_bytes(previous_value) if isinstance(previous_value, Mapping) else None
            if current.exists() and current.read_bytes() not in {data, previous_data}:
                raise ArtifactConflict(f"{relative_path} contains conflicting post-checkpoint bytes")
            if not current.exists() or current.read_bytes() != data:
                self._write_atomic_at(current, data)
        self._s5_fault(fault_hook, "mutable_epoch_copies_replaced")
        pending.update({"phase": "accepted", "materialization_status": status, "pending_group_ids": list(groups), "output_refs": {"epoch_receipt": epoch_ref.as_dict(), "binding_receipt": binding_ref.as_dict()}, "build_results": copy.deepcopy(build_results), "smoke_results": copy.deepcopy(smoke_results)})
        self.replace_json(f"plan/epochs/{epoch}/pending.json", pending, schema_name="s5-pending-state.schema.json")
        self._s5_fault(fault_hook, "pending_accepted")
        return {"epoch_receipt": epoch_ref, "binding_receipt": binding_ref}

    def recover_s5_epoch(self, epoch: str, fault_hook: Callable[[str], None] | None = None) -> dict[str, ArtifactRef] | None:
        """Recover an E1+ pending transaction using its recorded action facts."""

        from .tools.git_ops import verify_checkpoint
        if not re.fullmatch(r"E[1-9][0-9]*", epoch):
            raise RunValidationError("epoch recovery requires a canonical E1+ id")
        relative = f"plan/epochs/{epoch}/pending.json"
        path = self._confined(relative)
        if not path.exists():
            return None
        pending = self._read_json_artifact(relative, schema_name="s5-pending-state.schema.json")
        refs = pending.get("output_refs", {})
        if pending.get("phase") == "accepted" and isinstance(refs, Mapping) and set(refs) == {"epoch_receipt", "binding_receipt"}:
            for ref in refs.values():
                self.verify_ref(ref)
            self._s5_fault(fault_hook, "pending_recovered_accepted")
            return {key: ArtifactRef.from_value(value) for key, value in refs.items()}
        workspace = self._confined("workspace")
        if pending.get("checkpoint_commit") is None:
            facts = {item["path"]: item for item in pending.get("expected_path_facts", []) if isinstance(item, Mapping) and isinstance(item.get("path"), str)}
            predecessor = pending.get("predecessor_checkpoint", {})
            predecessor_commit = predecessor.get("commit_sha") if isinstance(predecessor, Mapping) else None
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=workspace, capture_output=True, text=True, check=False
            ).stdout.strip()
            if not isinstance(predecessor_commit, str) or head != predecessor_commit:
                raise ArtifactConflict("pre-checkpoint recovery is not based on the recorded predecessor")

            def predecessor_bytes(relative_path: str, expected_hash: str) -> bytes:
                result = subprocess.run(
                    ["git", "show", f"{predecessor_commit}:{relative_path}"],
                    cwd=workspace,
                    capture_output=True,
                    check=False,
                )
                if result.returncode != 0 or sha256_bytes(result.stdout) != expected_hash:
                    raise ArtifactConflict(f"predecessor preimage is unavailable at {relative_path}")
                return result.stdout

            for action in reversed(pending.get("action_plan", [])):
                if not isinstance(action, Mapping):
                    raise RunValidationError("pending action plan contains a non-object")
                kind = action.get("kind")
                source = action.get("source_path"); target = action.get("target_path"); current_path = action.get("path")
                if kind in {"quarantine", "re_adopt"}:
                    if not isinstance(source, str) or not isinstance(target, str):
                        raise ArtifactConflict("pending move has no complete source/target")
                    source_file = (workspace / source).resolve(); target_file = (workspace / target).resolve()
                    if source_file.exists() and target_file.exists():
                        raise ArtifactConflict("pending recovery found both sides of a move")
                    if target_file.exists():
                        expected = action.get("sha256")
                        if expected and sha256_bytes(target_file.read_bytes()) != expected:
                            raise ArtifactConflict("pending recovery found conflicting moved bytes")
                        source_file.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(target_file, source_file)
                    elif not source_file.exists() or sha256_bytes(source_file.read_bytes()) != action.get("sha256"):
                        raise ArtifactConflict("pending recovery found a missing or conflicting move preimage")
                    continue
                if not isinstance(current_path, str):
                    continue
                current = (workspace / current_path).resolve()
                fact = facts.get(current_path, {})
                before = fact.get("before_sha256"); after = fact.get("after_sha256")
                actual = sha256_bytes(current.read_bytes()) if current.is_file() else None
                if actual == before:
                    continue
                if actual == after:
                    if before is None:
                        if current.exists():
                            current.unlink()
                    else:
                        self._write_atomic_at(current, predecessor_bytes(current_path, before))
                    continue
                if actual is None and after is None and before is not None:
                    self._write_atomic_at(current, predecessor_bytes(current_path, before))
                    continue
                raise ArtifactConflict(f"pending recovery found conflicting bytes at {current_path}")
            expected_before = {
                item["path"]: item["sha256"]
                for item in pending.get("before_files", [])
                if isinstance(item, Mapping)
            }
            actual_before = {
                item.relative_to(workspace).as_posix(): sha256_bytes(item.read_bytes())
                for item in workspace.rglob("*")
                if item.is_file() and ".git" not in item.parts
            }
            if actual_before != expected_before:
                raise ArtifactConflict("pre-checkpoint recovery did not restore the predecessor workspace")
            path.unlink()
            self._s5_fault(fault_hook, "pending_precheckpoint_recovered")
            return None
        if pending.get("checkpoint_tree") is None:
            raise ArtifactConflict("pending epoch records a checkpoint without its tree")
        match = re.search(r"plan-(1\.[0-9]+\.[0-9]+)\.json$", pending["plan_ref"]["path"])
        if match is None:
            raise ArtifactConflict("pending epoch Plan ref is not versioned")
        verify_checkpoint(workspace, {"commit_sha": pending["checkpoint_commit"], "tree_sha": pending["checkpoint_tree"], "plan_version": match.group(1), "epoch": epoch})
        active_paths = set(pending.get("projected_active_paths", []))
        expected = {item["path"]: item["content"].encode("utf-8") for item in pending.get("expected_files", []) if item["path"] in active_paths}
        workspace_expected = {item["path"]: item["content"].encode("utf-8") for item in pending.get("expected_files", [])}
        return self.publish_s5_epoch({"epoch": epoch, "workspace": "workspace", "plan_ref": pending["plan_ref"], "blueprint": pending["blueprint"], "constraints": pending["constraints"], "rendering_view": pending["rendering_view"], "action_plan": {"actions": pending.get("action_plan", []), "new_inventory": pending.get("new_inventory", {})}, "rendered_files": expected, "workspace_files": workspace_expected, "build_results": pending.get("build_results", []), "smoke_results": pending.get("smoke_results", []), "materialization_status": pending.get("materialization_status", "ready"), "pending_group_ids": pending.get("pending_group_ids", [])}, fault_hook=fault_hook)

    def publish_version_binding(self, bundle: Mapping[str, Any]) -> ArtifactRef:
        """Publish an accepted F2 metadata binding without materialization side effects."""

        from .speclib.materialization import project_version_binding
        from .speclib.delivery import compile_delivery_blueprint, compile_delivery_constraints
        from .speclib.materialization import derive_rendering_view
        from .speclib.plan import blueprint_task_semantic_projection

        plan_ref = bundle.get("plan_ref")
        blueprint = bundle.get("blueprint")
        rendering_view = bundle.get("rendering_view", {})
        epoch_receipt = bundle.get("epoch_receipt")
        if not all(isinstance(value, Mapping) for value in (plan_ref, blueprint, rendering_view, epoch_receipt)):
            raise RunValidationError("F2 binding bundle is incomplete")
        version_match = re.search(r"plan-(1\.[0-9]+\.[0-9]+)\.json$", str(plan_ref.get("path")))
        if version_match is None:
            raise RunValidationError("F2 binding Plan ref is not an immutable version")
        version = version_match.group(1)
        plan = self._read_json_artifact(str(plan_ref["path"]), schema_name="plan.schema.json")
        if self._canonical_value_hash(plan) != plan_ref.get("sha256"):
            raise ArtifactConflict("F2 binding Plan bytes do not match the accepted Plan ref")
        active_pointer = self._read_json_artifact("plan/active_plan.json", schema_name="active-plan.schema.json")
        if active_pointer.get("version") != version or active_pointer.get("epoch") != epoch_receipt.get("epoch") or active_pointer.get("sha256") != plan_ref.get("sha256"):
            raise RunValidationError("F2 binding Plan is not the accepted active pointer")
        revision = self._read_json_artifact("plan/revision_ledger.json", schema_name="revision-ledger.schema.json")
        from .speclib.plan_revision import latest_activation
        activation = latest_activation(revision)
        if not isinstance(activation, Mapping) or activation.get("level") != "F2" or activation.get("to_plan_ref") != dict(plan_ref) or activation.get("epoch_after") != epoch_receipt.get("epoch"):
            raise RunValidationError("F2 binding requires the accepted F2 activation for the same epoch")
        workspace = self._confined("workspace")
        facts = {item.relative_to(workspace).as_posix(): sha256_bytes(item.read_bytes()) for item in workspace.rglob("*") if item.is_file() and ".git" not in item.parts} if workspace.is_dir() else {}
        current_manifest = self._read_json_artifact("plan/artifact_manifest.json", schema_name="artifact-manifest.schema.json")
        current_map = self._read_json_artifact("plan/contract_map.json", schema_name="contract-map.schema.json")
        file_ledger = self._read_json_artifact("plan/file_ledger.json", schema_name="file-ledger.schema.json")
        quarantine_paths = {
            row["quarantine_path"]
            for row in file_ledger.get("files", [])
            if row.get("state") == "quarantined" and isinstance(row.get("quarantine_path"), str)
        }
        actual_orphans = {path for path in facts if path.startswith("_orphan/")}
        if actual_orphans != quarantine_paths:
            raise ArtifactConflict("F2 binding workspace quarantine set is not registered in the file ledger")
        constraints = bundle.get("constraints", {})
        if not isinstance(constraints, Mapping):
            raise RunValidationError("F2 binding constraints are invalid")
        spec = self._read_json_artifact("spec/spec.json")
        target = self._read_json_artifact("inputs/target.json")
        frozen_constraints = compile_delivery_constraints(spec, target)
        if dict(constraints) != frozen_constraints:
            raise ArtifactConflict("F2 binding constraints do not match frozen inputs")
        frozen_blueprint = compile_delivery_blueprint(
            frozen_constraints,
            plan["architecture"],
            plan["work_packages"],
            blueprint_task_semantic_projection(plan["tasks"]),
        )
        if dict(blueprint) != frozen_blueprint:
            raise ArtifactConflict("F2 binding Blueprint does not recompute from the accepted Plan")
        frozen_view = derive_rendering_view(plan, spec, target, frozen_blueprint, frozen_constraints)
        if dict(rendering_view) != frozen_view:
            raise ArtifactConflict("F2 binding rendering metadata does not recompute from frozen inputs")
        projected = project_version_binding(plan_ref, frozen_blueprint, frozen_view, epoch_receipt, {"file_hashes": facts, "ignored_paths": sorted(quarantine_paths), "constraints": frozen_constraints}, existing_manifest=current_manifest, existing_contract_map=current_map)
        manifest = projected["manifest"]
        contract_map = projected["contract_map"]
        binding = projected["binding_receipt"]
        manifest_ref = self.publish_immutable_json(f"plan/bindings/{version}/artifact_manifest.json", manifest, schema_name="artifact-manifest.schema.json")
        map_ref = self.publish_immutable_json(f"plan/bindings/{version}/contract_map.json", contract_map, schema_name="contract-map.schema.json")
        binding["manifest_ref"] = manifest_ref.as_dict(); binding["contract_map_ref"] = map_ref.as_dict()
        binding_ref = self.publish_immutable_json(f"plan/bindings/{version}/receipt.json", binding, schema_name="binding-receipt.schema.json")

        def replace_if_changed(relative_path: str, value: Mapping[str, Any]) -> None:
            data = canonical_json_bytes(value)
            path = self._confined(relative_path)
            if path.is_file() and path.read_bytes() == data:
                return
            self._write_atomic_at(path, data)

        replace_if_changed("plan/artifact_manifest.json", manifest)
        replace_if_changed("plan/contract_map.json", contract_map)
        return binding_ref

    def recover_s5_e0(self, fault_hook: Callable[[str], None] | None = None) -> dict[str, ArtifactRef] | None:
        """Return a previously accepted S5 suffix or fail closed on a damaged pending record."""

        path = self._confined("plan/epochs/E0/pending.json")
        if not path.exists():
            return None
        pending = self._read_json_artifact("plan/epochs/E0/pending.json", schema_name="s5-pending-state.schema.json")
        refs = pending.get("output_refs", {})
        if pending.get("phase") == "accepted" and isinstance(refs, Mapping) and set(refs) == {"epoch_receipt", "binding_receipt"}:
            for key in refs:
                self.verify_ref(refs[key])
            self._s5_fault(fault_hook, "pending_recovered_accepted")
            return {key: ArtifactRef.from_value(value) for key, value in refs.items()}
        if pending.get("checkpoint_commit") is None:
            workspace = self._confined("workspace")
            for item in pending.get("expected_files", []):
                relative = item["path"]
                target = (workspace / relative).resolve()
                try:
                    target.relative_to(workspace)
                except ValueError as exc:
                    raise PathConfinementError(f"pending E0 path escapes workspace: {relative}") from exc
                if not target.exists():
                    continue
                if not target.is_file() or sha256_bytes(target.read_bytes()) != item["sha256"]:
                    raise ArtifactConflict(f"pending E0 recovery found conflicting bytes at {relative}")
                target.unlink()
            if workspace.is_dir():
                for directory in sorted((item for item in workspace.rglob("*") if item.is_dir() and item.name != ".git"), key=lambda item: len(item.parts), reverse=True):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
            git_dir = workspace / ".git"
            if git_dir.is_dir():
                try:
                    has_commit = subprocess.run(["git", "-C", str(workspace), "rev-parse", "--verify", "HEAD^{commit}"], capture_output=True, check=False).returncode == 0
                except OSError as exc:
                    raise RunStoreError("unable to inspect pre-checkpoint E0 repository") from exc
                if has_commit:
                    raise ArtifactConflict("pending E0 recovery found a checkpoint that is not recorded")
                shutil.rmtree(git_dir)
            path.unlink()
            self._s5_fault(fault_hook, "pending_precheckpoint_recovered")
            return None
        if pending.get("checkpoint_tree") is None:
            raise ArtifactConflict("pending E0 records a checkpoint commit without its tree")
        for ref in pending["input_refs"].values():
            self.verify_ref(ref)
        self.verify_ref(pending["plan_ref"], schema_name="plan.schema.json")
        if self._canonical_value_hash(pending["blueprint"]) != pending["blueprint_sha256"] or self._canonical_value_hash(pending["rendering_view"]) != pending["rendering_view_sha256"]:
            raise ArtifactConflict("pending E0 sealed values do not match their hashes")
        rendered: dict[str, bytes] = {}
        for item in pending["expected_files"]:
            data = item["content"].encode("utf-8")
            if sha256_bytes(data) != item["sha256"]:
                raise ArtifactConflict(f"pending E0 content hash drifted at {item['path']}")
            rendered[item["path"]] = data
        result = self.publish_s5_e0({
            "workspace": "workspace", "plan_ref": pending["plan_ref"], "blueprint": pending["blueprint"],
            "constraints": pending["constraints"], "rendering_view": pending["rendering_view"],
            "rendered_files": rendered, "build_results": pending["build_results"], "smoke_results": pending["smoke_results"],
        }, fault_hook=fault_hook)
        self._s5_fault(fault_hook, "pending_postcheckpoint_recovered")
        return result

    @staticmethod
    def _canonical_value_hash(value: object) -> str:
        try:
            return sha256_bytes(canonical_json_bytes(value))
        except (TypeError, ValueError) as exc:
            raise RunValidationError(f"JSON artifact is not canonical: {exc}") from exc

    def _read_json_artifact(self, relative_path: str, *, schema_name: str | None = None) -> dict[str, Any]:
        try:
            value = json.loads(self._confined(relative_path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RunValidationError(f"unable to load {relative_path}: {exc}") from exc
        if not isinstance(value, dict):
            raise RunValidationError(f"{relative_path} must contain a JSON object")
        if schema_name is not None:
            errors = _schema_errors(value, schema_name)
            if errors:
                raise RunValidationError(f"invalid {schema_name}: " + "; ".join(item["message"] for item in errors))
        return value

    def _json_artifact_hash(self, relative_path: str) -> str:
        try:
            return sha256_bytes(self._confined(relative_path).read_bytes())
        except OSError as exc:
            raise RunValidationError(f"missing artifact {relative_path}: {exc}") from exc

    def _run_with_active_pointer(self, pointer: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Prepare the sole fresh-run mutation made by revision activation."""

        run = self.load_run()
        stages = run.get("stages")
        s4 = stages.get("s4") if isinstance(stages, Mapping) else None
        refs = s4.get("output_refs") if isinstance(s4, Mapping) else None
        if not isinstance(refs, Mapping) or "plan" not in refs or "active_plan" not in refs:
            raise RunValidationError("Run S4 output_refs do not contain the independent Plan and active-pointer anchors")
        if refs["plan"] != {
            "path": "plan/versions/plan-1.0.0.json",
            "sha256": refs["plan"].get("sha256") if isinstance(refs["plan"], Mapping) else None,
        }:
            raise RunValidationError("Run S4 output_refs.plan is not the immutable 1.0.0 anchor")
        updated = json.loads(canonical_json_bytes(run).decode("utf-8"))
        updated["stages"]["s4"]["output_refs"]["active_plan"] = {
            "path": "plan/active_plan.json",
            "sha256": self._canonical_value_hash(pointer),
        }
        s6 = updated["stages"].get("s6")
        if isinstance(s6, dict) and s6.get("status") == "done":
            s6.update({"status": "pending", "started_at": None, "ended_at": None, "error": None})
            s6.pop("output_refs", None)
        current_epoch = str(updated["stages"]["s5"].get("instance_id", "E0"))
        current_match = re.fullmatch(r"E([0-9]+)", current_epoch)
        next_match = re.fullmatch(r"E([0-9]+)", str(pointer.get("epoch", "")))
        if current_match is None or next_match is None:
            raise RunValidationError("S5 and active Plan epochs must be canonical")
        current_number = int(current_match.group(1))
        next_number = int(next_match.group(1))
        if next_number < current_number:
            raise RunValidationError("activation would move the S5 instance to an older epoch")
        if next_number > current_number:
            s5 = updated["stages"]["s5"]
            if s5.get("status") != "done":
                raise RunValidationError("F3 requires the predecessor S5 epoch to be done")
            s5.update({"instance_id": str(pointer["epoch"]), "status": "pending", "started_at": None, "ended_at": None, "error": None})
            s5.pop("output_refs", None)
        return run, updated

    @staticmethod
    def _call_revision_boundary(fault_hook: Callable[[str], None] | None, boundary: str, when: str) -> None:
        if fault_hook is not None:
            fault_hook(f"{when}_{boundary}")

    def _validate_activation_wal_bindings(self, value: Mapping[str, Any]) -> None:
        """Validate the closed relations carried by one v2 activation WAL."""

        old, new = value["old"], value["new"]
        names = ("pointer", "state", "file_ledger", "revision_ledger", "run", "manifest", "contract_map")
        for side_name, view in (("old", old), ("new", new)):
            for name in names:
                if value["hashes"][side_name][name] != self._canonical_value_hash(view[name]):
                    raise RunValidationError(f"activation WAL {side_name} {name} hash is invalid")
        plan_hash = self._canonical_value_hash(value["candidate_plan"])
        if value["hashes"]["candidate_plan"] != plan_hash:
            raise RunValidationError("activation WAL candidate Plan hash is invalid")
        if value["candidate_plan_ref"] != {
            "path": new["pointer"]["path"], "sha256": new["pointer"]["sha256"]
        } or value["candidate_plan_ref"]["sha256"] != plan_hash:
            raise RunValidationError("activation WAL candidate Plan reference is invalid")
        binding = value["binding"]
        if (self._canonical_value_hash(binding) if binding is not None else None) != value["hashes"]["binding"]:
            raise RunValidationError("activation WAL binding hash is invalid")
        if binding is None:
            if value["level"] != "F3" or not value["pending_materialization"]:
                raise RunValidationError("activation WAL F3 materialization intent is invalid")
            s5 = new["run"].get("stages", {}).get("s5", {})
            if (
                s5.get("instance_id") != new["pointer"]["epoch"]
                or s5.get("status") != "pending"
                or "output_refs" in s5
            ):
                raise RunValidationError("activation WAL F3 pending S5 projection is invalid")
        else:
            if value["level"] != "F2" or value["pending_materialization"]:
                raise RunValidationError("activation WAL F2 binding intent is invalid")
            for name, ref_name in (
                ("manifest", "manifest_ref"), ("contract_map", "contract_map_ref"), ("receipt", "receipt_ref")
            ):
                if binding[ref_name]["sha256"] != self._canonical_value_hash(binding[name]):
                    raise RunValidationError(f"activation WAL F2 {name} reference is invalid")
            if binding["receipt"].get("manifest_ref") != binding["manifest_ref"] or binding["receipt"].get("contract_map_ref") != binding["contract_map_ref"]:
                raise RunValidationError("activation WAL F2 receipt references are invalid")
            if new["manifest"] != binding["manifest"] or new["contract_map"] != binding["contract_map"]:
                raise RunValidationError("activation WAL F2 current copies differ from its binding")
            s5_refs = new["run"].get("stages", {}).get("s5", {}).get("output_refs", {})
            if s5_refs.get("binding_receipt") != binding["receipt_ref"]:
                raise RunValidationError("activation WAL F2 Run binding reference is invalid")
        from .speclib.plan_revision import PlanRevisionError, latest_activation, validate_revision_ledger

        try:
            validate_revision_ledger(new["revision_ledger"])
        except PlanRevisionError as exc:
            raise RunValidationError(f"activation WAL revision ledger is invalid: {exc}") from exc
        activation = latest_activation(new["revision_ledger"])
        expected_binding = binding["receipt_ref"] if binding is not None else None
        if (
            not isinstance(activation, Mapping)
            or activation.get("revision_seq") != value["revision_seq"]
            or activation.get("from_plan_ref") != {
                "path": old["pointer"]["path"], "sha256": old["pointer"]["sha256"]
            }
            or activation.get("to_plan_ref") != value["candidate_plan_ref"]
            or activation.get("from_version") != old["pointer"]["version"]
            or activation.get("to_version") != new["pointer"]["version"]
            or activation.get("epoch_after") != new["pointer"]["epoch"]
            or activation.get("level") != value["level"]
            or activation.get("trigger_event_seq") != value["selected_event_seq"]
            or activation.get("binding_ref") != expected_binding
            or activation.get("pending_materialization") != value["pending_materialization"]
        ):
            raise RunValidationError("activation WAL revision event binding is invalid")
        if new["state"].get("plan_ref") != new["pointer"]:
            raise RunValidationError("activation WAL State pointer binding is invalid")
        expected_active_ref = {
            "path": "plan/active_plan.json", "sha256": self._canonical_value_hash(new["pointer"]),
        }
        if new["run"].get("stages", {}).get("s4", {}).get("output_refs", {}).get("active_plan") != expected_active_ref:
            raise RunValidationError("activation WAL Run active pointer reference is invalid")
        for artifact_name in ("manifest", "contract_map"):
            artifact = new[artifact_name]
            if (
                artifact.get("plan_version") != new["pointer"]["version"]
                or artifact.get("plan_sha256") != new["pointer"]["sha256"]
                or artifact.get("epoch") != new["pointer"]["epoch"]
            ):
                raise RunValidationError(f"activation WAL {artifact_name} Plan binding is invalid")
        if binding is not None:
            if binding["receipt"].get("plan_ref") != value["candidate_plan_ref"]:
                raise RunValidationError("activation WAL F2 receipt Plan reference is invalid")
            self.verify_ref(binding["receipt"]["epoch_receipt_ref"], schema_name="epoch-receipt.schema.json")

    def _assert_activation_named_view(
        self, value: Mapping[str, Any], new_names: set[str],
    ) -> None:
        paths = {
            "pointer": "plan/active_plan.json", "state": "plan/plan_state.json",
            "file_ledger": "plan/file_ledger.json", "revision_ledger": "plan/revision_ledger.json",
            "run": "run.json", "manifest": "plan/artifact_manifest.json",
            "contract_map": "plan/contract_map.json",
        }
        for name, relative in paths.items():
            side = "new" if name in new_names else "old"
            if self._read_json_artifact(relative) != value[side][name]:
                raise ArtifactConflict(f"activation boundary drifted for {name}")

    def _assert_activation_live_view(self, value: Mapping[str, Any]) -> None:
        """Re-read the exact expected mutable view at an activation boundary."""

        phase = str(value["phase"])
        new_names: set[str] = set()
        if phase in {"state_published", "file_ledger_published", "revision_ledger_published", "pointer_committed", "projections_published"}:
            new_names.add("state")
        if phase in {"file_ledger_published", "revision_ledger_published", "pointer_committed", "projections_published"}:
            new_names.add("file_ledger")
        if phase in {"revision_ledger_published", "pointer_committed", "projections_published"}:
            new_names.add("revision_ledger")
        if phase in {"pointer_committed", "projections_published"}:
            new_names.add("pointer")
        if phase == "projections_published":
            new_names.update({"run", "manifest", "contract_map"})
        self._assert_activation_named_view(value, new_names)

    def _verify_activation_new_view(self, value: Mapping[str, Any]) -> None:
        """Verify every committed activation byte before declaring reconciliation."""

        comparable = dict(value)
        comparable["phase"] = "projections_published"
        self._assert_activation_live_view(comparable)
        plan = self._read_json_artifact(value["candidate_plan_ref"]["path"], schema_name="plan.schema.json")
        if plan != value["candidate_plan"]:
            raise ArtifactConflict("committed successor Plan conflicts with activation WAL")
        gates = self._read_json_artifact(
            value["gates_ref"]["path"], schema_name="revision-gate-result.schema.json",
        )
        self.verify_ref(value["gates_ref"], schema_name="revision-gate-result.schema.json")
        if (
            gates.get("candidate_id") != value["candidate_id"]
            or gates.get("selected_event_seq") != value["selected_event_seq"]
            or gates.get("level") != value["level"]
            or gates.get("disposition") != "activate"
        ):
            raise ArtifactConflict("activation gate evidence has the wrong candidate identity")
        gate_statuses = {row["gate"]: row["status"] for row in gates["gates"]}
        activation = next(
            row["payload"] for row in reversed(value["new"]["revision_ledger"]["entries"])
            if row.get("event_type") == "revision_activated"
        )
        if activation.get("gates") != gate_statuses:
            raise ArtifactConflict("activation ledger gate payload conflicts with gate evidence")
        if value["rehearsal_ref"] is not None:
            rehearsal = self._read_json_artifact(
                value["rehearsal_ref"]["path"], schema_name="revision-rehearsal.schema.json",
            )
            self.verify_ref(value["rehearsal_ref"], schema_name="revision-rehearsal.schema.json")
            if (
                rehearsal.get("candidate_id") != value["candidate_id"]
                or rehearsal.get("level") != "F3"
                or rehearsal.get("candidate_plan_ref", {}).get("sha256")
                != value["candidate_plan_ref"]["sha256"]
            ):
                raise ArtifactConflict("activation rehearsal evidence has the wrong candidate identity")
        binding = value["binding"]
        if binding is not None:
            for name, ref_name, schema in (
                ("manifest", "manifest_ref", "artifact-manifest.schema.json"),
                ("contract_map", "contract_map_ref", "contract-map.schema.json"),
                ("receipt", "receipt_ref", "binding-receipt.schema.json"),
            ):
                self.verify_ref(binding[ref_name], schema_name=schema)
                if self._read_json_artifact(binding[ref_name]["path"], schema_name=schema) != binding[name]:
                    raise ArtifactConflict(f"committed F2 {name} conflicts with activation WAL")

    def activate_revision_v2(
        self,
        wal: Mapping[str, Any],
        *,
        fault_hook: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Publish a fully projected M1-11 activation; the pointer is the commit point."""

        if self._controller_lock_depth < 1:
            raise ControllerLockError("v2 revision activation requires the controller lock")
        value = json.loads(canonical_json_bytes(wal).decode("utf-8"))
        errors = _schema_errors(value, "plan-activation.schema.json")
        if errors:
            raise RunValidationError("invalid activation WAL: " + "; ".join(item["message"] for item in errors))
        event_seq = int(value["selected_event_seq"])
        wal_path = f"plan/_s4r/candidate_{event_seq}/activation.json"
        old, new = value["old"], value["new"]
        names = {
            "pointer": "plan/active_plan.json",
            "state": "plan/plan_state.json",
            "file_ledger": "plan/file_ledger.json",
            "revision_ledger": "plan/revision_ledger.json",
            "run": "run.json",
            "manifest": "plan/artifact_manifest.json",
            "contract_map": "plan/contract_map.json",
        }
        self._validate_activation_wal_bindings(value)
        binding = value["binding"]
        for name, relative in names.items():
            current = self._read_json_artifact(relative)
            if current != old[name]:
                raise ArtifactConflict(f"activation boundary drifted for {name}")
        self.verify_ref(value["gates_ref"], schema_name="revision-gate-result.schema.json")
        if value["rehearsal_ref"] is not None:
            self.verify_ref(value["rehearsal_ref"], schema_name="revision-rehearsal.schema.json")

        def phase(boundary: str, phase_name: str, action: Callable[[], Any]) -> Any:
            self._assert_activation_live_view(value)
            self._call_revision_boundary(fault_hook, boundary, "before")
            self._assert_activation_live_view(value)
            result = action()
            value["phase"] = phase_name
            self.replace_json(wal_path, value, schema_name="plan-activation.schema.json")
            self._call_revision_boundary(fault_hook, boundary, "after")
            return result

        self._call_revision_boundary(fault_hook, "wal", "before")
        self._assert_activation_live_view(value)
        if self._confined(wal_path).is_file():
            existing_wal = self._read_json_artifact(wal_path, schema_name="plan-activation.schema.json")
            comparable_existing = copy.deepcopy(existing_wal)
            comparable_existing["phase"] = "prepared"
            if comparable_existing != value:
                raise ArtifactConflict("activation WAL conflicts with replay")
            self.replace_json(wal_path, value, schema_name="plan-activation.schema.json")
        else:
            self.publish_immutable_json(wal_path, value, schema_name="plan-activation.schema.json")
        self._call_revision_boundary(fault_hook, "wal", "after")
        phase("successor_plan", "plan_published", lambda: self.publish_immutable_json(
            value["candidate_plan_ref"]["path"], value["candidate_plan"], schema_name="plan.schema.json"
        ))
        if binding is not None:
            def publish_binding() -> None:
                self.publish_immutable_json(binding["manifest_ref"]["path"], binding["manifest"], schema_name="artifact-manifest.schema.json")
                self._assert_activation_live_view(value)
                self.publish_immutable_json(binding["contract_map_ref"]["path"], binding["contract_map"], schema_name="contract-map.schema.json")
                self._assert_activation_live_view(value)
                self.publish_immutable_json(binding["receipt_ref"]["path"], binding["receipt"], schema_name="binding-receipt.schema.json")
                self._assert_activation_live_view(value)
            phase("f2_binding", "binding_published", publish_binding)
        phase("state", "state_published", lambda: self.replace_json("plan/plan_state.json", new["state"], schema_name="plan-state.schema.json"))
        phase("file_ledger", "file_ledger_published", lambda: self.replace_json("plan/file_ledger.json", new["file_ledger"], schema_name="file-ledger.schema.json"))
        phase("revision_ledger", "revision_ledger_published", lambda: self.replace_json("plan/revision_ledger.json", new["revision_ledger"], schema_name="revision-ledger.schema.json"))
        active_ref = phase("active_pointer", "pointer_committed", lambda: self.replace_json("plan/active_plan.json", new["pointer"], schema_name="active-plan.schema.json"))

        def projections() -> None:
            self.replace_run(new["run"])
            self._assert_activation_named_view(value, {"pointer", "state", "file_ledger", "revision_ledger", "run"})
            self.replace_json("plan/artifact_manifest.json", new["manifest"], schema_name="artifact-manifest.schema.json")
            self._assert_activation_named_view(value, {"pointer", "state", "file_ledger", "revision_ledger", "run", "manifest"})
            self.replace_json("plan/contract_map.json", new["contract_map"], schema_name="contract-map.schema.json")
            self._assert_activation_named_view(value, {"pointer", "state", "file_ledger", "revision_ledger", "run", "manifest", "contract_map"})
        phase("run_and_current_copies", "projections_published", projections)
        phase("wal_finalization", "reconciled", lambda: self._verify_activation_new_view(value))
        return {
            "revision_seq": value["revision_seq"], "active_pointer": new["pointer"],
            "active_plan_ref": active_ref.as_dict(),
            "wal_ref": {"path": wal_path, "sha256": self._json_artifact_hash(wal_path)}, "committed": True,
        }

    def _recover_revision_v2(self, wal_path: str) -> dict[str, Any]:
        wal = self._read_json_artifact(wal_path, schema_name="plan-activation.schema.json")
        try:
            self._validate_activation_wal_bindings(wal)
        except RunValidationError as exc:
            raise ArtifactConflict(str(exc)) from exc
        old, new = wal["old"], wal["new"]
        names = {
            "pointer": ("plan/active_plan.json", "active-plan.schema.json"),
            "state": ("plan/plan_state.json", "plan-state.schema.json"),
            "file_ledger": ("plan/file_ledger.json", "file-ledger.schema.json"),
            "revision_ledger": ("plan/revision_ledger.json", "revision-ledger.schema.json"),
            "run": ("run.json", "run.schema.json"),
            "manifest": ("plan/artifact_manifest.json", "artifact-manifest.schema.json"),
            "contract_map": ("plan/contract_map.json", "contract-map.schema.json"),
        }
        if wal["phase"] == "reconciled":
            return {
                "status": "already-reconciled", "revision_seq": wal["revision_seq"],
            }
        current_pointer = self._read_json_artifact("plan/active_plan.json", schema_name="active-plan.schema.json")
        if current_pointer == old["pointer"]:
            for name, (relative, schema) in names.items():
                current = self._read_json_artifact(relative, schema_name=schema)
                if current not in (old[name], new[name]):
                    raise ArtifactConflict(f"pre-commit recovery found conflicting mutable artifact bytes for {name}")
            for name, (relative, schema) in names.items():
                if name == "pointer":
                    continue
                self.replace_json(relative, old[name], schema_name=schema)
            candidate_path = self._confined(wal["candidate_plan_ref"]["path"])
            isolation = self._confined(f"plan/_s4r/candidate_{wal['selected_event_seq']}/isolated")
            isolation.mkdir(parents=True, exist_ok=True)
            if candidate_path.is_file():
                if sha256_bytes(candidate_path.read_bytes()) != wal["candidate_plan_ref"]["sha256"]:
                    raise ArtifactConflict("unactivated successor Plan bytes conflict with WAL")
                target = isolation / candidate_path.name
                if target.exists() and target.read_bytes() != candidate_path.read_bytes():
                    raise ArtifactConflict("isolated successor Plan conflicts with replay")
                if not target.exists():
                    os.replace(candidate_path, target)
            if wal["binding"] is not None:
                binding_dir = self._confined(wal["binding"]["receipt_ref"]["path"]).parent
                if binding_dir.is_dir():
                    for ref_name, schema in (("manifest_ref", "artifact-manifest.schema.json"), ("contract_map_ref", "contract-map.schema.json"), ("receipt_ref", "binding-receipt.schema.json")):
                        self.verify_ref(wal["binding"][ref_name], schema_name=schema)
                    target = isolation / "binding"
                    if target.exists():
                        raise ArtifactConflict("isolated F2 binding already conflicts with recovery")
                    os.replace(binding_dir, target)
            wal["phase"] = "reconciled"
            self.replace_json(wal_path, wal, schema_name="plan-activation.schema.json")
            return {"status": "precommit-restored", "revision_seq": wal["revision_seq"], "active_pointer": old["pointer"]}
        if current_pointer == new["pointer"]:
            plan = self._read_json_artifact(wal["candidate_plan_ref"]["path"], schema_name="plan.schema.json")
            if plan != wal["candidate_plan"]:
                raise ArtifactConflict("committed successor Plan conflicts with WAL")
            for name in ("state", "file_ledger", "revision_ledger"):
                relative, schema = names[name]
                if self._read_json_artifact(relative, schema_name=schema) != new[name]:
                    raise ArtifactConflict(f"committed activation {name} conflicts with WAL")
            if wal["binding"] is not None:
                for ref_name, schema in (("manifest_ref", "artifact-manifest.schema.json"), ("contract_map_ref", "contract-map.schema.json"), ("receipt_ref", "binding-receipt.schema.json")):
                    self.verify_ref(wal["binding"][ref_name], schema_name=schema)
            for name in ("run", "manifest", "contract_map"):
                relative, schema = names[name]
                current = self._read_json_artifact(relative, schema_name=schema)
                if current not in (old[name], new[name]):
                    raise ArtifactConflict(f"post-commit projection {name} conflicts with WAL")
                if current != new[name]:
                    self.replace_json(relative, new[name], schema_name=schema)
            self._verify_activation_new_view(wal)
            wal["phase"] = "reconciled"
            self.replace_json(wal_path, wal, schema_name="plan-activation.schema.json")
            return {"status": "postcommit-verified", "revision_seq": wal["revision_seq"], "active_pointer": new["pointer"]}
        raise ArtifactConflict("active pointer is neither activation WAL value")

    def reconcile_revision_activations(self) -> list[dict[str, Any]]:
        """Reconcile the unique unfinished activation before other transactions."""

        root = self._confined("plan/_s4r")
        if not root.is_dir():
            return []
        unfinished: list[Path] = []
        for path in sorted(root.glob("candidate_*/activation.json"), key=lambda item: item.as_posix().encode("utf-8")):
            relative = path.relative_to(self.root).as_posix()
            wal = self._read_json_artifact(relative, schema_name="plan-activation.schema.json")
            try:
                self._validate_activation_wal_bindings(wal)
            except RunValidationError as exc:
                raise ArtifactConflict(str(exc)) from exc
            if wal["phase"] != "reconciled":
                unfinished.append(path)
        if len(unfinished) > 1:
            raise ArtifactConflict("multiple unfinished revision activations exist")
        if not unfinished:
            return []
        return [self._recover_revision_v2(unfinished[0].relative_to(self.root).as_posix())]

    def recover_revision(self, revision_seq: int | None = None) -> dict[str, Any]:
        """Reconcile one interrupted activation using its immutable WAL."""

        with self.controller_lock():
            v2_paths = sorted(self._confined("plan/_s4r").glob("candidate_*/activation.json")) if self._confined("plan/_s4r").is_dir() else []
            if revision_seq is None:
                matches = [
                    path for path in v2_paths
                    if self._read_json_artifact(path.relative_to(self.root).as_posix(), schema_name="plan-activation.schema.json")["phase"] != "reconciled"
                ]
            else:
                matches = [
                    path for path in v2_paths
                    if self._read_json_artifact(path.relative_to(self.root).as_posix(), schema_name="plan-activation.schema.json")["revision_seq"] == revision_seq
                ]
            if len(matches) != 1:
                raise RunValidationError("recovery requires exactly one identifiable unfinished v2 activation WAL")
            return self._recover_revision_v2(matches[0].relative_to(self.root).as_posix())

    def read_verified_bytes(self, relative_path: str, expected_sha256: str | None = None) -> bytes:
        """Read a confined immutable artifact and optionally verify its raw-byte hash."""

        path = self._confined(relative_path)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise RunValidationError(f"missing artifact {relative_path}") from exc
        if expected_sha256 is not None:
            actual = sha256_bytes(data)
            if actual != expected_sha256:
                raise RunValidationError(
                    f"artifact hash mismatch for {relative_path}: expected {expected_sha256}, got {actual}"
                )
        return data

    def verify_ref(self, ref: ArtifactRef | Mapping[str, Any], *, schema_name: str | None = None) -> None:
        parsed = ArtifactRef.from_value(ref)
        path = self._confined(parsed.path)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise RunValidationError(f"missing artifact {parsed.path}: {exc}") from exc
        actual = sha256_bytes(data)
        if actual != parsed.sha256:
            raise RunValidationError(
                f"artifact hash mismatch for {parsed.path}: expected {parsed.sha256}, got {actual}"
            )
        if schema_name is not None:
            try:
                value = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RunValidationError(f"artifact {parsed.path} is not JSON: {exc}") from exc
            errors = _schema_errors(value, schema_name)
            if errors:
                raise RunValidationError(
                    f"invalid {schema_name}: " + "; ".join(item["message"] for item in errors)
                )

    def verify_stage_refs(self, stage: Mapping[str, Any], stage_name: str | None = None) -> None:
        refs = stage.get("output_refs", {})
        if not isinstance(refs, Mapping):
            raise RunValidationError("stage output_refs must be an object")
        if stage_name == "s4":
            if set(refs) == {"receipt"}:
                self.verify_ref(refs["receipt"])
                return
            expected = {"plan", "active_plan", "delivery_blueprint_sha256", "config_snapshot_sha256"}
            if set(refs) != expected:
                raise RunValidationError("S4 output_refs must contain the complete typed seal")
            for key in ("plan", "active_plan"):
                self.verify_ref(refs[key])
            for key in ("delivery_blueprint_sha256", "config_snapshot_sha256"):
                value = refs[key]
                if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                    raise RunValidationError(f"S4 {key} must be a lowercase SHA-256 anchor")
            return
        if stage_name == "s5":
            required = {"epoch_receipt", "binding_receipt"}
            if set(refs) != required:
                raise RunValidationError("S5 output_refs must contain epoch_receipt and binding_receipt")
        if stage_name == "s6":
            if set(refs) != {"s6_receipt"}:
                raise RunValidationError("S6 output_refs must contain only s6_receipt")
            self.verify_ref(refs["s6_receipt"], schema_name="s6-receipt.schema.json")
            return
        for ref in refs.values():
            self.verify_ref(ref)

    def append_stage_event(self, event: Mapping[str, object]) -> None:
        try:
            data = canonical_json_bytes(dict(event)) + b"\n"
        except (TypeError, ValueError) as exc:
            raise RunStoreError(f"stage event is not canonical JSON: {exc}") from exc
        path = self._confined("trace/stage_events.ndjson")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        self._directory_fsync(path.parent)

    def append_llm_trace(self, event: Mapping[str, object]) -> None:
        """Append one canonical LLM trace row to the dedicated evidence stream."""

        try:
            data = canonical_json_bytes(dict(event)) + b"\n"
        except (TypeError, ValueError) as exc:
            raise RunStoreError(f"LLM trace event is not canonical JSON: {exc}") from exc
        path = self._confined("trace/llm_calls.ndjson")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        self._directory_fsync(path.parent)

    def next_llm_call_sequence(self) -> int:
        """Return the next numeric evidence id, including orphan prompt/output files."""

        highest = 0
        trace_path = self._confined("trace/llm_calls.ndjson")
        if trace_path.exists():
            try:
                rows = trace_path.read_text(encoding="utf-8").splitlines()
                for line in rows:
                    if not line:
                        continue
                    row = json.loads(line)
                    for field in ("prompt_path", "output_path"):
                        value = row.get(field)
                        if isinstance(value, str):
                            highest = max(highest, self._numeric_artifact_id(value))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                raise RunStoreError("cannot inspect existing LLM trace sequence") from exc
        for directory in ("trace/prompts", "trace/outputs"):
            path = self._confined(directory)
            if not path.exists():
                continue
            for artifact in path.iterdir():
                if artifact.is_file():
                    highest = max(highest, self._numeric_artifact_id(artifact.name))
        return highest + 1

    @staticmethod
    def _numeric_artifact_id(value: str) -> int:
        stem = Path(value).name.split(".", 1)[0]
        return int(stem) if stem.isdigit() else 0

    @contextmanager
    def controller_lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self._confined(".controller.lock")
        handle = lock_path.open("a+")
        acquired = False
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ControllerLockError(f"controller lock is already held for {self.run_id}") from exc
            acquired = True
            self._controller_lock_depth += 1
            yield
        finally:
            if acquired:
                self._controller_lock_depth -= 1
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def verify_frozen_inputs(self) -> None:
        run = self.load_run()
        inputs = run["inputs"]
        checks = [
            ("spec/spec.json", inputs["spec"]["sha256"], "spec"),
            ("inputs/target.json", inputs["target_profile"]["sha256"], "target"),
            ("inputs/test_bundle.json", inputs["test_bundle"]["sha256"], "test_bundle"),
        ]
        for relative_path, expected, label in checks:
            path = self._confined(relative_path)
            try:
                raw = path.read_bytes()
            except OSError as exc:
                raise RunValidationError(f"frozen {label} input is missing") from exc
            if sha256_bytes(raw) != expected:
                raise RunValidationError(f"frozen {label} input hash drifted")
        _require_valid(lint_spec(self._confined("spec/spec.json")), "run-local Spec IR")
        _require_valid(
            lint_target(self._confined("inputs/target.json"), self._confined("spec/spec.json")),
            "run-local Target Profile",
        )
        _require_valid(
            lint_test_bundle(self._confined("inputs/test_bundle.json"), self._confined("spec/spec.json")),
            "run-local Test Bundle",
        )

    @classmethod
    def initialize_spec_run(
        cls,
        runs_root: Path | str,
        inputs: SpecRunInputs,
        config: ResolvedConfig,
    ) -> "RunStore":
        root = Path(runs_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        spec, spec_raw, spec_source = _read_source(inputs.spec)
        target, _target_raw, _target_source = _read_source(inputs.target_profile)
        bundle, bundle_raw, _bundle_source = _read_source(inputs.test_bundle)
        _require_valid(lint_spec(spec), "Spec IR")
        _require_valid(lint_target(target, spec), "Target Profile")
        _require_valid(lint_test_bundle(bundle, spec), "Test Bundle")
        target_canonical = canonical_json_bytes(target)
        if bundle_raw != canonical_json_bytes(bundle):
            raise InputValidationError("Test Bundle input bytes must be canonical JSON")

        protocol = spec.get("protocol", {}) if isinstance(spec, Mapping) else {}
        protocol_name = protocol.get("id") or protocol.get("name") or "spec"
        protocol_slug = re.sub(r"[^A-Za-z0-9_-]+", "-", str(protocol_name)).strip("-") or "spec"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = f"{timestamp}_{protocol_slug}_spec-run"
        run_id = base
        suffix = 0
        while (root / run_id).exists():
            suffix += 1
            run_id = f"{base}_{suffix}"

        final = root / run_id
        staging = root / f".{run_id}.staging"
        if staging.exists():
            shutil.rmtree(staging)
        store = cls(staging)
        try:
            for directory in ("inputs", "spec", "report", "trace"):
                (staging / directory).mkdir(parents=True, exist_ok=True)
            (staging / "spec/spec.json").write_bytes(spec_raw)
            (staging / "inputs/target.json").write_bytes(target_canonical)
            (staging / "inputs/test_bundle.json").write_bytes(bundle_raw)
            for relative in ("spec/spec.json", "inputs/target.json", "inputs/test_bundle.json"):
                with (staging / relative).open("rb") as handle:
                    os.fsync(handle.fileno())

            stages = {
                stage: {"status": "skipped" if stage in {"s1", "s2", "s3"} else "pending", "started_at": None, "ended_at": None, "error": None}
                for stage in ("s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9")
            }
            stages["s5"]["instance_id"] = "E0"
            run = {
                "schema_version": "4.0",
                "run_id": run_id,
                "entry": "spec-run",
                "created_at": _utc_now(),
                "inputs": {
                    "spec": {"path": spec_source, "sha256": sha256_bytes(spec_raw)},
                    "target_profile": {"path": "inputs/target.json", "sha256": sha256_bytes(target_canonical)},
                    "test_bundle": {
                        "id": bundle["bundle"]["id"],
                        "version": bundle["bundle"]["version"],
                        "path": "inputs/test_bundle.json",
                        "sha256": sha256_bytes(bundle_raw),
                    },
                },
                "config_snapshot": config.snapshot,
                "config_snapshot_sha256": config.snapshot_sha256,
                "stages": stages,
                "budget_used": {"wall_clock_s": 0, "cost_usd": 0, "tokens_in": 0, "tokens_out": 0},
            }
            run_bytes = store._canonical_run_bytes(run)
            RunStore._write_atomic_at(staging / "run.json", run_bytes)
            for current, dirs, files in os.walk(staging):
                for filename in files:
                    with (Path(current) / filename).open("rb") as handle:
                        os.fsync(handle.fileno())
                RunStore._directory_fsync(Path(current))

            # Re-read file-backed sources so a source changed while staging cannot be silently frozen.
            for source, original in ((inputs.spec, spec_raw), (inputs.target_profile, _target_raw), (inputs.test_bundle, bundle_raw)):
                if not isinstance(source, Mapping):
                    try:
                        if Path(source).read_bytes() != original:
                            raise InputValidationError(f"source input changed during staging: {source}")
                    except OSError as exc:
                        raise InputValidationError(f"source input disappeared during staging: {source}") from exc

            root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.replace(staging, final)
                os.fsync(root_fd)
            finally:
                os.close(root_fd)
        except BaseException:
            if staging.exists():
                shutil.rmtree(staging)
            raise
        return cls(final)
