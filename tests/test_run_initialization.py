import hashlib
import json
from pathlib import Path

import pytest

from nepa.config import load_config
from nepa.run_store import InputValidationError, RunStore, SpecRunInputs


ROOT = Path(__file__).parents[1]


def _inputs() -> SpecRunInputs:
    return SpecRunInputs(
        spec=ROOT / "gold_file/specIR.json",
        target_profile=ROOT / "gold_file/target.json",
        test_bundle=ROOT / "gold_file/test_bundle.json",
    )


def test_valid_spec_run_initialization_freezes_inputs_atomically(tmp_path):
    sources = _inputs()
    source_bytes = {name: Path(value).read_bytes() for name, value in vars(sources).items()}
    store = RunStore.initialize_spec_run(tmp_path, sources, load_config(ROOT / "configs/default.yaml"))
    run = store.load_run()

    assert run["entry"] == "spec-run"
    assert [run["stages"][stage]["status"] for stage in ("s1", "s2", "s3")] == ["skipped"] * 3
    assert all(run["stages"][stage]["status"] == "pending" for stage in ("s4", "s5", "s6", "s7", "s8", "s9"))
    assert (store.root / "spec/spec.json").read_bytes() == source_bytes["spec"]
    assert (store.root / "inputs/test_bundle.json").read_bytes() == source_bytes["test_bundle"]
    assert hashlib.sha256((store.root / "inputs/target.json").read_bytes()).hexdigest() == run["inputs"]["target_profile"]["sha256"]
    assert all(Path(value).read_bytes() == source_bytes[name] for name, value in vars(sources).items())


@pytest.mark.parametrize("kind", ["missing", "invalid", "noncanonical"])
def test_invalid_source_inputs_publish_no_run(tmp_path, kind):
    spec = ROOT / "gold_file/specIR.json"
    target = ROOT / "gold_file/target.json"
    bundle = ROOT / "gold_file/test_bundle.json"
    if kind == "missing":
        spec = tmp_path / "missing.json"
    elif kind == "invalid":
        spec = tmp_path / "invalid.json"
        spec.write_text("{}", encoding="utf-8")
    else:
        bundle = tmp_path / "bundle.json"
        bundle.write_text(json.dumps(json.loads((ROOT / "gold_file/test_bundle.json").read_text()), indent=2), encoding="utf-8")

    with pytest.raises(InputValidationError):
        RunStore.initialize_spec_run(
            tmp_path / "runs",
            SpecRunInputs(spec, target, bundle),
            load_config(),
        )
    assert not list((tmp_path / "runs").glob("*/run.json")) if (tmp_path / "runs").exists() else True


def test_invalid_unsupported_target_role_publishes_no_run(tmp_path):
    target = tmp_path / "unsupported-target.json"
    target.write_text(
        json.dumps({"roles": ["client"], "language": {"name": "C", "version": "C99"}}),
        encoding="utf-8",
    )

    with pytest.raises(InputValidationError, match="TARGET_ROLE_UNSUPPORTED"):
        RunStore.initialize_spec_run(
            tmp_path / "runs",
            SpecRunInputs(ROOT / "gold_file/specIR.json", target, ROOT / "gold_file/test_bundle.json"),
            load_config(),
        )
    assert not list((tmp_path / "runs").glob("*/run.json")) if (tmp_path / "runs").exists() else True


def test_committed_frozen_input_drift_is_detected(tmp_path):
    store = RunStore.initialize_spec_run(tmp_path, _inputs(), load_config())
    (store.root / "inputs/test_bundle.json").write_bytes(b"{}")

    with pytest.raises(Exception, match="drifted|validation"):
        store.verify_frozen_inputs()


def test_source_drift_during_staging_and_crash_before_rename_publish_no_run(tmp_path, monkeypatch):
    spec = tmp_path / "spec.json"
    target = tmp_path / "target.json"
    bundle = tmp_path / "bundle.json"
    for source, gold in ((spec, ROOT / "gold_file/specIR.json"), (target, ROOT / "gold_file/target.json"), (bundle, ROOT / "gold_file/test_bundle.json")):
        source.write_bytes(gold.read_bytes())
    inputs = SpecRunInputs(spec, target, bundle)
    original_write = RunStore._write_atomic_at

    def write_and_drift(path, data):
        original_write(path, data)
        if path.name == "run.json":
            spec.write_bytes(spec.read_bytes() + b" ")

    monkeypatch.setattr(RunStore, "_write_atomic_at", staticmethod(write_and_drift))
    with pytest.raises(InputValidationError, match="changed during staging"):
        RunStore.initialize_spec_run(tmp_path / "runs", inputs, load_config())
    assert not list((tmp_path / "runs").glob("*/run.json"))

    monkeypatch.undo()
    # Patch the directory rename by recognizing the staging directory rather than any file publication.
    original_replace = __import__("os").replace

    def crash_any_staging(source, destination):
        if Path(source).name.endswith(".staging"):
            raise RuntimeError("crash")
        return original_replace(source, destination)

    monkeypatch.setattr("nepa.run_store.os.replace", crash_any_staging)
    with pytest.raises(RuntimeError, match="crash"):
        RunStore.initialize_spec_run(tmp_path / "runs2", inputs, load_config())
    assert not list((tmp_path / "runs2").glob("*/run.json"))
