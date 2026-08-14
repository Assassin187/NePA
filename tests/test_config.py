import copy
from pathlib import Path

import pytest

from nepa.config import (
    ConfigError,
    ConfigSnapshotDrift,
    config_snapshot_sha256,
    load_config,
    public_config_snapshot,
    verify_config_snapshot,
)


def test_config_models_are_closed_and_default_yaml_is_loadable():
    config = load_config(Path("configs/default.yaml"))

    assert config.providers["anthropic"].api_key_env == "NEPA_CLAUDE_API_KEY"
    assert config.budgets.max_cost_usd == 20
    assert public_config_snapshot(config)["run"]["until"] is None

    with pytest.raises(ConfigError):
        load_config(overrides={"unknown": True})
    with pytest.raises(ConfigError):
        load_config(overrides={"budgets": {"max_cost_usd": "not-a-number"}})


def test_configuration_precedence_and_stable_snapshot_hash(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("budgets:\n  max_cost_usd: 9\nrun:\n  until: s6\n", encoding="utf-8")

    first = load_config(config_file, {"budgets": {"max_cost_usd": 3}})
    second = load_config(config_file, {"budgets": {"max_cost_usd": 3}})

    assert first.budgets.max_cost_usd == 3
    assert first.run.until == "s6"
    assert first.snapshot_sha256 == second.snapshot_sha256
    assert first.snapshot_sha256 == config_snapshot_sha256(public_config_snapshot(first))


def test_public_snapshot_does_not_persist_environment_secret_values(monkeypatch):
    secret = "do-not-persist"
    monkeypatch.setenv("NEPA_CLAUDE_API_KEY", secret)
    snapshot = public_config_snapshot(load_config())
    serialized = repr(snapshot)

    assert secret not in serialized
    assert snapshot["providers"]["anthropic"]["api_key_env"] == "NEPA_CLAUDE_API_KEY"


def test_snapshot_drift_is_rejected():
    snapshot = public_config_snapshot(load_config())
    changed = copy.deepcopy(snapshot)
    changed["run"]["until"] = "s6"

    with pytest.raises(ConfigSnapshotDrift):
        verify_config_snapshot(changed, config_snapshot_sha256(snapshot))
