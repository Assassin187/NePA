import copy
from pathlib import Path

import pytest

from nepa.agents.base import resolve_route
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
    fallback_config = load_config()

    assert config.providers["anthropic"].api_key_env == "NEPA_CLAUDE_API_KEY"
    assert config.tiers["T1"].provider == "deepseek"
    assert config.tiers["T1"].model == "deepseek-v4-pro"
    assert config.tiers["T2"].model == "deepseek-v4-flash"
    assert config.tiers["T3"].model == "deepseek-v4-flash"
    assert config.tiers["T1"].max_tokens == 16000
    assert resolve_route(config, "architecture_planner").max_tokens == 65536
    assert resolve_route(config, "task_planner").max_tokens == 16000
    assert fallback_config.roles["architecture_planner"] == config.roles["architecture_planner"]
    assert resolve_route(fallback_config, "architecture_planner").max_tokens == 65536
    assert config.budgets.max_cost_usd == 20
    assert public_config_snapshot(config)["run"]["until"] is None
    assert config.smoke.dwell_seconds == 2
    assert config.smoke.term_grace_seconds == 5

    with pytest.raises(ConfigError):
        load_config(overrides={"unknown": True})
    with pytest.raises(ConfigError):
        load_config(overrides={"budgets": {"max_cost_usd": "not-a-number"}})
    with pytest.raises(ConfigError):
        load_config(overrides={"smoke": {"dwell_seconds": 0}})
    with pytest.raises(ConfigError):
        load_config(overrides={"smoke": {"term_grace_seconds": -1}})


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


def test_revision_limits_and_explicit_enabled_configuration():
    disabled = load_config()
    assert disabled.budgets.revision_f2_limit == 0
    assert disabled.budgets.revision_f3_limit == 0
    assert "revision" not in public_config_snapshot(disabled)

    enabled = load_config(overrides={
        "budgets": {"revision_f2_limit": 3, "revision_f3_limit": 1},
        "revision": {
            "theta2": 0.5,
            "theta6": 0.5,
            "rho_min_f2": 0.75,
            "rho_min_f3": 0.5,
            "cost_rates": {"build_usd": 0},
        },
    })
    assert enabled.revision is not None
    assert enabled.revision.cost_rates.build_usd == 0

    for overrides in (
        {"budgets": {"revision_f2_limit": 4}},
        {"budgets": {"revision_f3_limit": 2}},
        {"budgets": {"revision_f2_limit": 1}},
        {"budgets": {"revision_f2_limit": True}},
        {"budgets": {"revision_f2_limit": 1.0}},
        {"budgets": {"revision_f3_limit": "1"}},
        {"revision": {"theta2": 0.5, "theta6": 0.5, "rho_min_f2": -0.1, "rho_min_f3": 0.5, "cost_rates": {"build_usd": 0}}},
        {"revision": {"theta2": 0.5, "theta6": 0.5, "rho_min_f2": True, "rho_min_f3": 0.5, "cost_rates": {"build_usd": 0}}},
        {"revision": {"theta2": 0.5, "theta6": 0.5, "rho_min_f2": 0.5, "rho_min_f3": "0.5", "cost_rates": {"build_usd": 0}}},
        {"revision": {"theta2": 0.5, "theta6": 0.5, "rho_min_f2": 0.5, "rho_min_f3": 0.5, "cost_rates": {"build_usd": False}}},
        {"revision": {"theta2": 0.5, "theta6": 0.5, "rho_min_f2": 0.5, "rho_min_f3": 0.5, "cost_rates": {"build_usd": "0"}}},
    ):
        with pytest.raises(ConfigError):
            load_config(overrides=overrides)


@pytest.mark.parametrize("field,value", [
    ("revision_f2_limit", True),
    ("revision_f2_limit", "3"),
    ("revision_f2_limit", 3.0),
    ("revision_f3_limit", False),
    ("revision_f3_limit", "1"),
    ("revision_f3_limit", 1.0),
])
def test_enabled_revision_activation_limits_are_strict_integers(field, value):
    budgets = {"revision_f2_limit": 3, "revision_f3_limit": 1, field: value}
    with pytest.raises(ConfigError):
        load_config(overrides={
            "budgets": budgets,
            "revision": {
                "theta2": 0.5, "theta6": 0.5,
                "rho_min_f2": 0.0, "rho_min_f3": 1.0,
                "cost_rates": {"build_usd": 0.0},
            },
        })


def test_revision_numeric_boundaries_remain_valid():
    config = load_config(overrides={
        "budgets": {"revision_f2_limit": 3, "revision_f3_limit": 1},
        "revision": {
            "theta2": 0.5, "theta6": 0.5,
            "rho_min_f2": 0.0, "rho_min_f3": 1.0,
            "cost_rates": {"build_usd": 0.0},
        },
    })
    assert config.budgets.revision_f2_limit == 3
    assert config.budgets.revision_f3_limit == 1
    assert config.revision is not None
    assert config.revision.rho_min_f2 == 0.0
    assert config.revision.rho_min_f3 == 1.0
    assert config.revision.cost_rates.build_usd == 0.0
