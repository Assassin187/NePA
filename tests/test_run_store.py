import pytest
from hashlib import sha256

from nepa.run_store import (
    ArtifactConflict,
    ControllerLockError,
    PathConfinementError,
    RunStore,
    RunValidationError,
)


def test_atomic_immutable_publication_and_reference_verification(tmp_path):
    store = RunStore(tmp_path / "run")
    store.root.mkdir()

    first = store.publish_immutable_json("report/value.json", {"b": 2, "a": 1})
    replay = store.publish_immutable_json("report/value.json", {"a": 1, "b": 2})

    assert first == replay
    store.verify_ref(first)
    with pytest.raises(ArtifactConflict):
        store.publish_immutable_json("report/value.json", {"a": 9})


def test_path_confinement_and_append_only_events(tmp_path):
    store = RunStore(tmp_path / "run")
    store.root.mkdir()

    for path in ("../outside.json", "/tmp/outside.json"):
        with pytest.raises(PathConfinementError):
            store.publish_immutable_bytes(path, b"x")
    store.append_stage_event({"stage": "s4", "event": "started"})
    store.append_stage_event({"stage": "s4", "event": "done"})
    assert store.root.joinpath("trace/stage_events.ndjson").read_text() == (
        '{"event":"started","stage":"s4"}\n{"event":"done","stage":"s4"}\n'
    )


def test_controller_lock_is_non_blocking_and_does_not_touch_run_json(tmp_path):
    store = RunStore(tmp_path / "run")
    store.root.mkdir()
    run_path = store.root / "run.json"
    run_path.write_text("original", encoding="utf-8")

    with store.controller_lock():
        with pytest.raises(ControllerLockError):
            with store.controller_lock():
                pass
        assert run_path.read_text(encoding="utf-8") == "original"


def test_read_verified_bytes_checks_missing_and_hash_drift_without_bypassing_publication(tmp_path):
    store = RunStore(tmp_path / "run")
    store.root.mkdir()
    ref = store.publish_immutable_bytes("cache/llm/value.json", b"value")

    assert store.read_verified_bytes(ref.path, ref.sha256) == b"value"
    with pytest.raises(RunValidationError, match="hash mismatch"):
        store.read_verified_bytes(ref.path, sha256(b"other").hexdigest())
    (store.root / ref.path).unlink()
    with pytest.raises(RunValidationError, match="missing artifact"):
        store.read_verified_bytes(ref.path, ref.sha256)


def test_read_verified_path_confinement_is_public_and_strict(tmp_path):
    store = RunStore(tmp_path / "run")
    store.root.mkdir()

    for path in ("../outside.json", "/tmp/outside.json"):
        with pytest.raises(PathConfinementError):
            store.read_verified_bytes(path)


def test_llm_trace_append_is_canonical_and_dedicated(tmp_path):
    store = RunStore(tmp_path / "run")
    store.root.mkdir()
    store.append_llm_trace({"z": 1, "a": "value"})

    assert (store.root / "trace/llm_calls.ndjson").read_text(encoding="utf-8") == '{"a":"value","z":1}\n'


def test_llm_sequence_considers_committed_rows_and_orphan_evidence(tmp_path):
    store = RunStore(tmp_path / "run")
    store.root.mkdir()

    assert store.next_llm_call_sequence() == 1
    (store.root / "trace/prompts").mkdir(parents=True)
    (store.root / "trace/outputs").mkdir(parents=True)
    (store.root / "trace/prompts/000007.txt").write_text("orphan", encoding="utf-8")
    assert store.next_llm_call_sequence() == 8
    (store.root / "trace/outputs/000012.json").write_text("{}", encoding="utf-8")
    assert store.next_llm_call_sequence() == 13
    store.append_llm_trace({"prompt_path": "trace/prompts/000013.txt", "output_path": "trace/outputs/000013.json"})
    assert store.next_llm_call_sequence() == 14
