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
from typing import Any, Iterator, Mapping

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

    def verify_stage_refs(self, stage: Mapping[str, Any]) -> None:
        refs = stage.get("output_refs", {})
        if not isinstance(refs, Mapping):
            raise RunValidationError("stage output_refs must be an object")
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
