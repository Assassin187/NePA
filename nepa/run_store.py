"""Durable, hash-bound filesystem storage for M1-1 runs."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
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
            raise RunValidationError("invalid Run v3: " + "; ".join(item["message"] for item in errors))
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

    def _revision_wal_path(self, revision_seq: int) -> str:
        if not isinstance(revision_seq, int) or revision_seq < 1:
            raise RunValidationError("revision sequence must be a positive integer")
        return f"_s4r/rev_{revision_seq:03d}/activation.json"

    @staticmethod
    def _call_activation_hook(hook: Callable[[str], None] | None, point: str) -> None:
        if hook is not None:
            hook(point)

    def _run_with_active_pointer(self, pointer: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Prepare the sole Run v3 mutation made by revision activation."""

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
        return run, updated

    def _normalize_activation_inputs(
        self,
        candidate_plan: Mapping[str, Any] | None,
        migration_report: Mapping[str, Any] | None,
        projected_state: Mapping[str, Any] | None,
        projected_file_ledger: Mapping[str, Any] | None,
        revision_entry: Mapping[str, Any] | None,
        expected_active_pointer: Mapping[str, Any] | None,
        bundle: Mapping[str, Any] | None,
    ) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
        values: Mapping[str, Any] = bundle or {}
        if bundle is None and isinstance(candidate_plan, Mapping) and "candidate_plan" in candidate_plan:
            values = candidate_plan
        if values:
            candidate_plan = values.get("candidate_plan", candidate_plan)
            migration_report = values.get("migration_report", values.get("report", migration_report))
            projected_state = values.get("new_state", values.get("state", projected_state))
            projected_file_ledger = values.get("new_file_ledger", values.get("file_ledger", projected_file_ledger))
            revision_entry = values.get("revision_entry", revision_entry)
            expected_active_pointer = values.get("old_pointer", values.get("expected_active_pointer", expected_active_pointer))
        if not all(isinstance(value, Mapping) for value in (candidate_plan, migration_report, projected_state, projected_file_ledger, revision_entry, expected_active_pointer)):
            raise RunValidationError("activation requires a complete candidate bundle and expected active pointer")
        return candidate_plan, migration_report, projected_state, projected_file_ledger, revision_entry, expected_active_pointer  # type: ignore[return-value]

    def activate_revision(
        self,
        candidate_plan: Mapping[str, Any] | None = None,
        migration_report: Mapping[str, Any] | None = None,
        projected_state: Mapping[str, Any] | None = None,
        projected_file_ledger: Mapping[str, Any] | None = None,
        revision_entry: Mapping[str, Any] | None = None,
        expected_active_pointer: Mapping[str, Any] | None = None,
        *,
        level: str | None = None,
        new_pointer: Mapping[str, Any] | None = None,
        revalidation_proofs: Mapping[str, Any] | None = None,
        expected_hashes: Mapping[str, str] | None = None,
        activated_at_commit: str | None = None,
        fault_hook: Callable[[str], None] | None = None,
        bundle: Mapping[str, Any] | None = None,
        lineage: Any = None,
    ) -> dict[str, Any]:
        """Activate one fully validated revision through the locked commit order.

        The method deliberately accepts values rather than generating a revision
        candidate.  Candidate generation and trigger policy belong to later
        milestones; this API only binds an already validated bundle to the run.
        """

        from .speclib.plan_revision import (
            PlanRevisionError,
            append_revision_entry,
            classify_migration,
            successor_pointer,
            validate_file_ledger,
            validate_revision_ledger,
        )

        with self.controller_lock():
            if lineage is None and isinstance(bundle, Mapping):
                lineage = bundle.get("lineage")
            candidate_plan, migration_report, projected_state, projected_file_ledger, revision_entry, expected_active_pointer = self._normalize_activation_inputs(
                candidate_plan, migration_report, projected_state, projected_file_ledger, revision_entry, expected_active_pointer, bundle,
            )
            try:
                old_pointer = self._read_json_artifact("plan/active_plan.json", schema_name="active-plan.schema.json")
                old_plan = self._read_json_artifact(old_pointer["path"], schema_name="plan.schema.json")
                old_state = self._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
                old_file_ledger = self._read_json_artifact("plan/file_ledger.json", schema_name="file-ledger.schema.json")
                old_revision_ledger = self._read_json_artifact("plan/revision_ledger.json", schema_name="revision-ledger.schema.json")
                candidate = json.loads(canonical_json_bytes(candidate_plan).decode("utf-8"))
                report = json.loads(canonical_json_bytes(migration_report).decode("utf-8"))
                state = json.loads(canonical_json_bytes(projected_state).decode("utf-8"))
                file_ledger = json.loads(canonical_json_bytes(projected_file_ledger).decode("utf-8"))
                entry = json.loads(canonical_json_bytes(revision_entry).decode("utf-8"))
            except (KeyError, TypeError, ValueError) as exc:
                raise RunValidationError(f"activation input is malformed: {exc}") from exc
            if old_pointer != dict(expected_active_pointer):
                raise RunValidationError("active pointer changed since activation precondition was read")
            if self._json_artifact_hash(old_pointer["path"]) != old_pointer["sha256"]:
                raise RunValidationError("active pointer does not hash-bind the current immutable Plan")
            for label, relative in (("state", "plan/plan_state.json"), ("file_ledger", "plan/file_ledger.json"), ("revision_ledger", "plan/revision_ledger.json"), ("pointer", "plan/active_plan.json")):
                expected = (expected_hashes or {}).get(label)
                if expected is not None and self._json_artifact_hash(relative) != expected:
                    raise RunValidationError(f"activation precondition hash drifted for {label}")
            expected_plan_hash = (expected_hashes or {}).get("plan")
            if expected_plan_hash is not None and old_pointer["sha256"] != expected_plan_hash:
                raise RunValidationError("activation precondition hash drifted for plan")
            try:
                _schema_errors(candidate, "plan.schema.json")
                if _schema_errors(candidate, "plan.schema.json"):
                    raise RunValidationError("candidate Plan failed Schema validation")
                if _schema_errors(report, "migration-report.schema.json"):
                    raise RunValidationError("migration report failed Schema validation")
                if _schema_errors(state, "plan-state.schema.json"):
                    raise RunValidationError("projected Plan State failed Schema validation")
                validate_file_ledger(file_ledger)
                validate_revision_ledger(old_revision_ledger)
            except PlanRevisionError as exc:
                raise RunValidationError(str(exc)) from exc
            if level is None:
                level = entry.get("level")
            if level not in {"F2", "F3"}:
                raise RunValidationError("activation only accepts an explicit F2 or F3 revision level")
            if entry.get("level") != level:
                raise RunValidationError("revision entry level does not match the activation level")
            if entry.get("gates") != {f"RG-{index}": "pass" for index in range(1, 6)}:
                raise RunValidationError("activation requires every revision gate to pass")
            candidate_hash = self._canonical_value_hash(candidate)
            candidate_ref = {"path": f"plan/versions/plan-{candidate_hash}.json", "sha256": candidate_hash}
            if new_pointer is None:
                try:
                    new_pointer = successor_pointer(old_pointer, candidate_ref, level)
                except PlanRevisionError as exc:
                    raise RunValidationError(str(exc)) from exc
            else:
                new_pointer = json.loads(canonical_json_bytes(new_pointer).decode("utf-8"))
            if new_pointer.get("sha256") != candidate_hash or new_pointer.get("path") != f"plan/versions/plan-{new_pointer.get('version')}.json":
                raise RunValidationError("candidate Plan hash or immutable version path does not match the new pointer")
            try:
                from .speclib.plan_revision import validate_plan_successor
                validate_plan_successor(old_pointer, new_pointer, level)
            except PlanRevisionError as exc:
                raise RunValidationError(str(exc)) from exc
            expected_candidate_path = new_pointer["path"]
            existing_candidate = self._confined(expected_candidate_path)
            if existing_candidate.exists() and self._json_artifact_hash(expected_candidate_path) != candidate_hash:
                raise ArtifactConflict(f"immutable activated version differs at {expected_candidate_path}")
            try:
                expected_report = classify_migration(
                    old_plan, candidate, old_state, old_file_ledger,
                    lineage,
                    from_version=old_pointer["version"], to_version=new_pointer["version"],
                )
            except PlanRevisionError as exc:
                raise RunValidationError(str(exc)) from exc
            if report != expected_report:
                raise RunValidationError("migration report is not the deterministic complete classification")
            if state.get("plan_ref") != new_pointer:
                raise RunValidationError("projected Plan State does not bind the new active pointer")
            _run_before, run_after = self._run_with_active_pointer(new_pointer)
            try:
                from .speclib.plan_revision import project_file_ledger, project_plan_state
                expected_state = project_plan_state(
                    old_state, candidate, report, new_pointer,
                    revalidation_proofs=revalidation_proofs,
                    config_snapshot=_run_before["config_snapshot"],
                )
                expected_file_ledger = project_file_ledger(
                    old_file_ledger, candidate, report, epoch=new_pointer["epoch"],
                    new_paths={row["path"] for row in file_ledger["files"]},
                )
            except PlanRevisionError as exc:
                raise RunValidationError(str(exc)) from exc
            if state != expected_state:
                raise RunValidationError("projected Plan State is not the deterministic migration projection")
            if file_ledger != expected_file_ledger:
                raise RunValidationError("projected file ledger is not the deterministic migration projection")
            entry.setdefault("revision_seq", new_pointer["revision_seq"])
            if entry.get("prev_entry_sha256") == "0" * 64 and old_revision_ledger["entries"]:
                entry["prev_entry_sha256"] = self._canonical_value_hash(old_revision_ledger["entries"][-1])
            if entry.get("from_plan_ref") != {"path": old_pointer["path"], "sha256": old_pointer["sha256"]} or entry.get("to_plan_ref") != {"path": new_pointer["path"], "sha256": new_pointer["sha256"]}:
                raise RunValidationError("revision entry Plan refs do not bind the activation pointers")
            if activated_at_commit is not None and entry.get("activated_at_commit") != activated_at_commit:
                raise RunValidationError("revision entry activation commit does not match the supplied activation commit")
            if entry.get("migration") != {key: report[key] for key in ("counts", "tasks", "files")} or entry.get("preservation_rate") != report["preservation_rate"]:
                raise RunValidationError("revision entry migration does not bind the complete migration report")
            try:
                new_revision_ledger = append_revision_entry(old_revision_ledger, entry)
            except PlanRevisionError as exc:
                raise RunValidationError(str(exc)) from exc
            if new_revision_ledger["entries"][-1].get("epoch_after") != new_pointer["epoch"]:
                raise RunValidationError("revision entry epoch does not bind the new pointer")
            wal = {
                "schema_version": "1.0", "revision_seq": new_pointer["revision_seq"],
                "old_pointer": old_pointer, "new_pointer": new_pointer,
                "old_state": old_state, "new_state": state,
                "old_file_ledger": old_file_ledger, "new_file_ledger": file_ledger,
                "old_revision_ledger": old_revision_ledger, "new_revision_ledger": new_revision_ledger,
                "candidate_plan": candidate,
                "old_hashes": {
                    "pointer": self._canonical_value_hash(old_pointer), "state": self._canonical_value_hash(old_state),
                    "file_ledger": self._canonical_value_hash(old_file_ledger), "revision_ledger": self._canonical_value_hash(old_revision_ledger),
                    "plan": self._canonical_value_hash(old_plan),
                },
                "new_hashes": {
                    "pointer": self._canonical_value_hash(new_pointer), "state": self._canonical_value_hash(state),
                    "file_ledger": self._canonical_value_hash(file_ledger), "revision_ledger": self._canonical_value_hash(new_revision_ledger),
                    "plan": candidate_hash,
                },
            }
            wal_path = self._revision_wal_path(new_pointer["revision_seq"])
            self.publish_immutable_json(wal_path, wal, schema_name="plan-activation.schema.json")
            self._call_activation_hook(fault_hook, "wal_written")
            self.publish_immutable_json(new_pointer["path"], candidate, schema_name="plan.schema.json")
            self._call_activation_hook(fault_hook, "version_published")
            self.replace_json("plan/plan_state.json", state, schema_name="plan-state.schema.json")
            self._call_activation_hook(fault_hook, "state_replaced")
            self.replace_json("plan/file_ledger.json", file_ledger, schema_name="file-ledger.schema.json")
            self._call_activation_hook(fault_hook, "file_ledger_replaced")
            self.replace_json("plan/revision_ledger.json", new_revision_ledger, schema_name="revision-ledger.schema.json")
            self._call_activation_hook(fault_hook, "revision_ledger_replaced")
            active_ref = self.replace_json("plan/active_plan.json", new_pointer, schema_name="active-plan.schema.json")
            self._call_activation_hook(fault_hook, "active_pointer_replaced")
            self.replace_run(run_after)
            self._call_activation_hook(fault_hook, "run_reference_updated")
            return {"revision_seq": new_pointer["revision_seq"], "active_pointer": new_pointer, "active_plan_ref": active_ref.as_dict(), "wal_ref": {"path": wal_path, "sha256": self._json_artifact_hash(wal_path)}, "committed": True}

    def recover_revision(self, revision_seq: int | None = None) -> dict[str, Any]:
        """Reconcile one interrupted activation using its immutable WAL."""

        from .speclib.plan_revision import PlanRevisionError, validate_plan_successor, validate_revision_ledger

        with self.controller_lock():
            if revision_seq is None:
                candidates = sorted(self._confined("_s4r").glob("rev_*/activation.json")) if self._confined("_s4r").is_dir() else []
                if len(candidates) != 1:
                    raise RunValidationError("recovery requires exactly one identifiable activation WAL")
                match = re.fullmatch(r"rev_(\d{3})", candidates[0].parent.name)
                if match is None:
                    raise RunValidationError("activation WAL directory is not a revision directory")
                revision_seq = int(match.group(1))
            wal_path = self._revision_wal_path(revision_seq)
            wal = self._read_json_artifact(wal_path, schema_name="plan-activation.schema.json")
            if wal["revision_seq"] != revision_seq:
                raise RunValidationError("activation WAL revision sequence drift")
            old_pointer, new_pointer = wal["old_pointer"], wal["new_pointer"]
            old_state, new_state = wal["old_state"], wal["new_state"]
            old_file, new_file = wal["old_file_ledger"], wal["new_file_ledger"]
            old_revision, new_revision = wal["old_revision_ledger"], wal["new_revision_ledger"]
            for label, value in (("old_pointer", old_pointer), ("new_pointer", new_pointer), ("old_state", old_state), ("new_state", new_state), ("old_file_ledger", old_file), ("new_file_ledger", new_file), ("old_revision_ledger", old_revision), ("new_revision_ledger", new_revision), ("candidate_plan", wal["candidate_plan"])):
                if not isinstance(value, Mapping):
                    raise RunValidationError(f"activation WAL {label} is not an object")
            for side, values in (("old", {"pointer": old_pointer, "state": old_state, "file_ledger": old_file, "revision_ledger": old_revision}), ("new", {"pointer": new_pointer, "state": new_state, "file_ledger": new_file, "revision_ledger": new_revision})):
                for name, value in values.items():
                    if wal[f"{side}_hashes"][name] != self._canonical_value_hash(value):
                        raise RunValidationError(f"activation WAL {side} {name} hash binding is invalid")
            if wal["old_hashes"]["plan"] != self._canonical_value_hash(self._read_json_artifact(old_pointer["path"], schema_name="plan.schema.json")):
                raise RunValidationError("activation WAL old Plan hash binding is invalid")
            if wal["new_hashes"]["plan"] != self._canonical_value_hash(wal["candidate_plan"]):
                raise RunValidationError("activation WAL candidate Plan hash binding is invalid")
            try:
                validate_revision_ledger(old_revision)
                validate_revision_ledger(new_revision)
                validate_plan_successor(old_pointer, new_pointer, new_revision["entries"][-1]["level"])
            except (PlanRevisionError, IndexError, KeyError) as exc:
                raise RunValidationError(str(exc)) from exc
            current_pointer = self._read_json_artifact("plan/active_plan.json", schema_name="active-plan.schema.json")
            current_revision = self._read_json_artifact("plan/revision_ledger.json", schema_name="revision-ledger.schema.json")
            if current_pointer == old_pointer and current_revision == old_revision:
                current_state = self._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
                current_file = self._read_json_artifact("plan/file_ledger.json", schema_name="file-ledger.schema.json")
                if current_state not in (old_state, new_state) or current_file not in (old_file, new_file):
                    raise RunValidationError("pre-commit recovery found conflicting mutable artifact bytes")
                if current_state != old_state:
                    self.replace_json("plan/plan_state.json", old_state, schema_name="plan-state.schema.json")
                if current_file != old_file:
                    self.replace_json("plan/file_ledger.json", old_file, schema_name="file-ledger.schema.json")
                candidate_path = f"_s4r/rev_{revision_seq:03d}/candidate_plan.json"
                self.publish_immutable_json(candidate_path, wal["candidate_plan"], schema_name="plan.schema.json")
                return {"status": "precommit-restored", "revision_seq": revision_seq, "active_pointer": old_pointer}
            if current_pointer == old_pointer and current_revision == new_revision:
                current_state = self._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
                current_file = self._read_json_artifact("plan/file_ledger.json", schema_name="file-ledger.schema.json")
                if current_state not in (old_state, new_state) or current_file not in (old_file, new_file):
                    raise RunValidationError("ledger-new recovery found conflicting mutable artifact bytes")
                self.publish_immutable_json(new_pointer["path"], wal["candidate_plan"], schema_name="plan.schema.json")
                if current_state != new_state:
                    self.replace_json("plan/plan_state.json", new_state, schema_name="plan-state.schema.json")
                if current_file != new_file:
                    self.replace_json("plan/file_ledger.json", new_file, schema_name="file-ledger.schema.json")
                self.replace_json("plan/active_plan.json", new_pointer, schema_name="active-plan.schema.json")
                _run_before, run_after = self._run_with_active_pointer(new_pointer)
                self.replace_run(run_after)
                return {"status": "commit-completed", "revision_seq": revision_seq, "active_pointer": new_pointer}
            if current_pointer == new_pointer:
                if current_revision != new_revision:
                    raise RunValidationError("active pointer advanced while revision ledger disagrees with WAL")
                current_state = self._read_json_artifact("plan/plan_state.json", schema_name="plan-state.schema.json")
                current_file = self._read_json_artifact("plan/file_ledger.json", schema_name="file-ledger.schema.json")
                if current_state != new_state or current_file != new_file:
                    raise RunValidationError("committed pointer has incomplete or conflicting new artifacts")
                self._read_json_artifact(new_pointer["path"], schema_name="plan.schema.json")
                _run_before, run_after = self._run_with_active_pointer(new_pointer)
                if _run_before != run_after:
                    self.replace_run(run_after)
                return {"status": "postcommit-verified", "revision_seq": revision_seq, "active_pointer": new_pointer}
            raise RunValidationError("active pointer and revision ledger do not match any WAL recovery branch")

    # Descriptive aliases keep the public controller vocabulary explicit.
    activate_plan_revision = activate_revision
    recover_plan_revision = recover_revision

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
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ControllerLockError(f"controller lock is already held for {self.run_id}") from exc
            yield
        finally:
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
            run = {
                "schema_version": "3.0",
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
