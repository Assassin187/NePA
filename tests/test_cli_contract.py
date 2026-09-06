import json
import subprocess
from pathlib import Path

import pytest

import nepa.cli
from nepa.config import load_config
from nepa.run_store import RunStore, SpecRunInputs
from nepa.speclib.lint import canonical_json_bytes


ROOT = Path(__file__).parents[1]


def _run(*args: str, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["uv", "run", "nepa", *args],
        cwd=ROOT,
        input=input_bytes,
        capture_output=True,
        check=False,
    )


def _write_json(path: Path, value: dict, *, canonical: bool = True) -> Path:
    if canonical:
        path.write_bytes(canonical_json_bytes(value))
    else:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _spec() -> dict:
    return {
        "schema_version": "3.0",
        "protocol": {"name": "MQTT", "version": "3.1.1", "roles": ["server"]},
        "types": [],
        "messages": [],
        "requirements": [{
            "id": "REQ-TEST-001",
            "text": "The requirement is defined.",
            "level": "MUST",
            "source_ref": {"section": "1", "quote": "The requirement is defined."},
        }],
    }


def _bundle(gate: str = "task") -> dict:
    return {
        "schema_version": "3.0",
        "bundle": {
            "id": "test-bundle",
            "version": "1.0.0",
            "default_build_variant_ids": ["san"],
        },
        "tests": [{
            "nodeid": "tests/l1_codec/test_codec.py::test_vector",
            "layer": "l1",
            "req_ids": ["REQ-TEST-001"],
            "description": "A declarative test case.",
            "gate": gate,
        }],
    }


def test_cli_valid_spec_returns_zero_and_structured_json():
    result = _run("lint", "spec", "gold_file/specIR.json")

    assert result.returncode == 0
    assert json.loads(result.stdout)["valid"] is True


def test_cli_plan_requires_explicit_basic_companions():
    result = _run("lint", "plan", str(ROOT / "nepa/schemas/examples/plan.example.json"))

    assert result.returncode == 20
    report = json.loads(result.stdout)
    assert report["valid"] is False
    assert report["errors"][0]["code"] == "PLAN_COMPANION_MISSING"


def test_cli_validation_failure_returns_twenty(tmp_path):
    target = _write_json(tmp_path / "target.json", {
        "roles": ["client", "server"],
        "language": {"name": "C", "version": "C99"},
    })

    result = _run("lint", "target", str(target))

    assert result.returncode == 20
    report = json.loads(result.stdout)
    assert report["valid"] is False
    assert report["errors"][0]["code"] == "TARGET_ROLE_UNSUPPORTED"


def test_cli_noncanonical_test_bundle_returns_twenty(tmp_path):
    bundle = _write_json(tmp_path / "bundle.json", json.loads((ROOT / "gold_file/test_bundle.json").read_bytes()), canonical=False)

    result = _run("lint", "test-bundle", str(bundle))

    assert result.returncode == 20
    report = json.loads(result.stdout)
    assert report["valid"] is False
    assert report["errors"][0]["code"] == "TEST_CANONICAL_JSON_NONCANONICAL"


def test_cli_test_bundle_missing_coverage_returns_twenty(tmp_path):
    spec = _write_json(tmp_path / "spec.json", _spec())
    bundle = _write_json(tmp_path / "bundle.json", _bundle(gate="s5"))

    result = _run("lint", "test-bundle", str(bundle), "--spec", str(spec))

    assert result.returncode == 20
    report = json.loads(result.stdout)
    assert report["valid"] is False
    assert report["errors"][0]["code"] == "SPEC_REQUIREMENT_UNCOVERED"


@pytest.mark.parametrize("invalid_test", ["missing_gate", "missing_req_ids", "non_object"])
def test_cli_test_bundle_schema_error_with_spec_returns_twenty(tmp_path, invalid_test):
    value = _bundle()
    if invalid_test == "missing_gate":
        del value["tests"][0]["gate"]
    elif invalid_test == "missing_req_ids":
        del value["tests"][0]["req_ids"]
    else:
        value["tests"] = ["not-an-object"]
    bundle = _write_json(tmp_path / "bundle.json", value)
    spec = _write_json(tmp_path / "spec.json", _spec())

    result = _run("lint", "test-bundle", str(bundle), "--spec", str(spec))

    assert result.returncode == 20
    report = json.loads(result.stdout)
    assert report["valid"] is False
    assert report["errors"][0]["code"] == "SCHEMA_INVALID"
    assert all(error["code"] != "NEPA_INTERNAL_ERROR" for error in report["errors"])


def test_cli_test_bundle_reads_canonical_stdin():
    raw = (ROOT / "gold_file/test_bundle.json").read_bytes()

    result = _run("lint", "test-bundle", "/dev/stdin", input_bytes=raw)

    assert result.returncode == 0
    assert json.loads(result.stdout)["valid"] is True


def test_cli_internal_error_returns_one(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("unexpected validator failure")

    monkeypatch.setattr(nepa.cli, "lint_spec", fail)

    assert nepa.cli.main(["lint", "spec", "input.json"]) == 1
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "NEPA_INTERNAL_ERROR"


def test_cli_run_until_s6_forwards_boundary_and_keeps_later_stages_pending(monkeypatch, tmp_path, capsys):
    observed = {}

    class StubOrchestrator:
        def run_spec(self, store):
            observed["run_id"] = store.load_run()["run_id"]
            return 0

    def build(config, store):
        observed["until"] = config.snapshot["run"]["until"]
        return StubOrchestrator()

    monkeypatch.setattr(nepa.cli, "build_orchestrator", build)
    assert nepa.cli.main([
        "run", "--spec", str(ROOT / "gold_file/specIR.json"),
        "--target", str(ROOT / "gold_file/target.json"),
        "--test-bundle", str(ROOT / "gold_file/test_bundle.json"),
        "--runs-root", str(tmp_path), "--until", "s6",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert observed["until"] == "s6"
    assert report["run_id"] == observed["run_id"]
    assert report["stages"]["s6"] == "pending"
    assert report["stages"]["s7"] == "pending"
    assert report["stages"]["s9"] == "pending"


def test_cli_resume_and_status_expose_durable_run_state(monkeypatch, tmp_path, capsys):
    store = RunStore.initialize_spec_run(
        tmp_path,
        SpecRunInputs(ROOT / "gold_file/specIR.json", ROOT / "gold_file/target.json", ROOT / "gold_file/test_bundle.json"),
        load_config(overrides={"run": {"until": "s6"}}),
    )
    observed = []

    class StubOrchestrator:
        def resume(self, current):
            observed.append(current.root)
            return 10

    monkeypatch.setattr(nepa.cli, "build_orchestrator", lambda config, current: StubOrchestrator())
    assert nepa.cli.main(["resume", store.load_run()["run_id"], "--runs-root", str(tmp_path)]) == 10
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["exit_code"] == 10
    assert observed == [store.root]

    assert nepa.cli.main(["status", store.load_run()["run_id"], "--runs-root", str(tmp_path)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["exit_code"] is None
    assert status["stages"]["s6"] == "pending"
