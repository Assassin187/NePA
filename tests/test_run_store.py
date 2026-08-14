import pytest

from nepa.run_store import (
    ArtifactConflict,
    ControllerLockError,
    PathConfinementError,
    RunStore,
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
