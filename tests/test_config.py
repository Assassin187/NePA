"""nepa/config.py 单元测试（system_design.md 8.3、4.6、4.7）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from nepa.config import (
    MissingAPIKeyError,
    NepaConfig,
    load_config,
    load_scope,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_YAML = REPO_ROOT / "configs" / "default.yaml"
SCOPE_YAML = REPO_ROOT / "configs" / "scope-mqtt-min.yaml"


def _minimal_cfg(**over: object) -> dict[str, Any]:
    """最小合法配置片段：一个 provider + 一个 tier。"""
    data: dict[str, Any] = {
        "providers": {
            "deepseek": {
                "kind": "openai_compat",
                "base_url": "https://api.deepseek.com",
                "api_key_env": "DS_API",
            }
        },
        "tiers": {
            "T1": {"provider": "deepseek", "model": "m1"},
        },
    }
    data.update(over)
    return data


class TestDefaults:
    def test_bare_config_uses_documented_defaults(self) -> None:
        # 8.3/4.7 的默认值由模型字段承担（加载链第一层）
        cfg = NepaConfig()
        assert cfg.budgets.wall_clock_hours == 4.0
        assert cfg.budgets.max_cost_usd == 20.0
        assert cfg.budgets.plan_architecture_repairs == 1
        assert cfg.budgets.plan_task_shard_repairs == 1
        assert cfg.budgets.plan_critic_repairs == 2
        assert cfg.budgets.plan_global_replans == 1
        assert cfg.budgets.coder_context_max_tokens == 24000
        assert cfg.budgets.task_fix_attempts == 3
        assert cfg.budgets.repair_rounds == 3
        assert cfg.planning.strategy == "layered"
        assert cfg.planning.max_task_files == 4
        assert cfg.planning.context_safety_margin_ratio == 0.15
        assert cfg.run.until is None
        assert cfg.assets.target_profile == "mqtt-client-broker"
        assert cfg.assets.language_profile == "c99-posix"
        assert cfg.assets.test_bundle == "mqtt-3-1-1-min-gold"
        assert cfg.stages.l3_interop is False
        assert cfg.sandbox.image == "nepa-sandbox:latest"
        assert cfg.sandbox.cpu == 2
        assert cfg.sandbox.mem_gb == 4

    def test_asset_ids_follow_global_identifier_rule(self) -> None:
        with pytest.raises(ValueError, match="test_bundle"):
            NepaConfig.model_validate(
                {
                    "assets": {
                        "target_profile": "target",
                        "language_profile": "language",
                        "test_bundle": "mqtt-3.1.1",
                    }
                }
            )

    @pytest.mark.parametrize(
        "budgets",
        [
            {"wall_clock_hours": 0},
            {"max_cost_usd": 0},
            {"wall_clock_hours": -1},
            {"max_cost_usd": -1},
        ],
    )
    def test_global_budget_limits_must_be_positive(self, budgets: dict[str, float]) -> None:
        with pytest.raises(ValueError):
            NepaConfig.model_validate({"budgets": budgets})


class TestLoadDefaultYaml:
    def test_loads_repo_default_yaml(self) -> None:
        cfg = load_config(DEFAULT_YAML)
        assert cfg.providers["deepseek"].kind == "openai_compat"
        assert cfg.providers["deepseek"].api_key_env == "DS_API"
        assert cfg.tiers["T1"].model == "deepseek-reasoner"
        assert cfg.tiers["T2"].temperature == 0.1
        assert cfg.roles["diagnoser"].escalate_to == "T1"
        assert cfg.roles["coder"].tier == "T2"
        assert cfg.roles["architecture_planner"].tier == "T1"
        assert cfg.roles["task_planner"].tier == "T1"
        assert cfg.roles["plan_critic"].tier == "T1"
        assert cfg.roles["flat_plan_baseline"].tier == "T1"
        assert cfg.budgets.max_cost_usd == 20
        assert cfg.planning.strategy == "layered"
        assert cfg.run.until is None
        assert cfg.assets.test_bundle == "mqtt-3-1-1-min-gold"
        assert cfg.pricing["deepseek-chat"].input == 0.27
        assert cfg.pricing["deepseek-reasoner"].output == 2.19
        assert cfg.pricing["deepseek-v4-flash"].output == 2.19

    def test_loads_repo_scope_yaml(self) -> None:
        scope = load_scope(SCOPE_YAML)
        assert scope.protocol == "mqtt"
        assert scope.version == "3.1.1"
        assert scope.roles == ["client", "broker"]
        assert len(scope.features_included) > 0
        assert all(e.feature and e.reason for e in scope.features_excluded)
        assert any("1883" in a for a in scope.assumptions)


class TestMergeChain:
    def test_override_dict_wins_over_yaml(self, tmp_path: Path) -> None:
        cfg = load_config(DEFAULT_YAML, overrides={"budgets": {"max_cost_usd": 5}})
        assert cfg.budgets.max_cost_usd == 5
        # 深合并：同级其它键保留 YAML 层的值
        assert cfg.budgets.repair_rounds == 3
        assert cfg.tiers["T1"].model == "deepseek-reasoner"

    def test_nested_override_replaces_leaf_only(self) -> None:
        cfg = load_config(
            DEFAULT_YAML,
            overrides={"tiers": {"T1": {"provider": "deepseek", "model": "x"}}},
        )
        assert cfg.tiers["T1"].model == "x"
        assert cfg.tiers["T2"].model == "deepseek-chat"

    def test_until_override_is_persistable(self) -> None:
        cfg = load_config(DEFAULT_YAML, overrides={"run": {"until": "s6"}})
        assert cfg.run.until == "s6"
        assert cfg.config_snapshot()["run"]["until"] == "s6"

    def test_yaml_wins_over_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "c.yaml"
        p.write_text("budgets: {repair_rounds: 1}\n", encoding="utf-8")
        cfg = load_config(p)
        assert cfg.budgets.repair_rounds == 1
        assert cfg.budgets.task_fix_attempts == 3  # 未覆盖处保持默认


class TestValidation:
    def test_role_with_unknown_tier_rejected(self) -> None:
        data = _minimal_cfg(roles={"coder": {"tier": "T9"}})
        with pytest.raises(ValidationError, match="T9"):
            NepaConfig.model_validate(data)

    def test_tier_with_unknown_provider_rejected(self) -> None:
        data = _minimal_cfg()
        data["tiers"] = {"T1": {"provider": "nope", "model": "m"}}
        with pytest.raises(ValidationError, match="nope"):
            NepaConfig.model_validate(data)

    def test_escalate_to_unknown_tier_rejected(self) -> None:
        data = _minimal_cfg(roles={"fixer": {"tier": "T1", "escalate_to": "T8"}})
        with pytest.raises(ValidationError, match="T8"):
            NepaConfig.model_validate(data)

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NepaConfig.model_validate({"budgets": {"max_cost_us": 1}})

    def test_unknown_planning_strategy_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NepaConfig.model_validate({"planning": {"strategy": "automatic_fallback"}})

    def test_unknown_until_stage_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NepaConfig.model_validate({"run": {"until": "s7"}})


class TestApiKeys:
    def test_resolve_api_key_reads_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DS_API", "sk-secret-123")
        cfg = load_config(DEFAULT_YAML)
        assert cfg.resolve_api_key("deepseek") == "sk-secret-123"

    def test_default_env_name_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 8.3：未配置 api_key_env 时默认 NEPA_<PROVIDER>_API_KEY
        data = _minimal_cfg()
        data["providers"]["deepseek"].pop("api_key_env")  # type: ignore[union-attr]
        cfg = NepaConfig.model_validate(data)
        monkeypatch.setenv("NEPA_DEEPSEEK_API_KEY", "sk-fallback")
        assert cfg.resolve_api_key("deepseek") == "sk-fallback"

    def test_missing_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DS_API", raising=False)
        cfg = load_config(DEFAULT_YAML)
        with pytest.raises(MissingAPIKeyError, match="DS_API"):
            cfg.resolve_api_key("deepseek")

    def test_unknown_provider_raises(self) -> None:
        cfg = load_config(DEFAULT_YAML)
        with pytest.raises(ValueError, match="openai"):
            cfg.resolve_api_key("openai")


class TestSnapshot:
    def test_snapshot_contains_env_name_not_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        secret = "sk-top-secret-do-not-leak"
        monkeypatch.setenv("DS_API", secret)
        cfg = load_config(DEFAULT_YAML)
        assert cfg.resolve_api_key("deepseek") == secret  # 密钥确已可解析
        dumped = json.dumps(cfg.config_snapshot(), ensure_ascii=False)
        assert secret not in dumped  # 8.3：密钥值禁止出现在任何序列化输出
        assert "DS_API" in dumped  # 5.6.2：只留环境变量名

    def test_snapshot_fills_default_env_name(self) -> None:
        data = _minimal_cfg()
        data["providers"]["deepseek"].pop("api_key_env")  # type: ignore[union-attr]
        cfg = NepaConfig.model_validate(data)
        snap = cfg.config_snapshot()
        assert snap["providers"]["deepseek"]["api_key_env"] == "NEPA_DEEPSEEK_API_KEY"

    def test_snapshot_is_json_serializable_and_complete(self) -> None:
        cfg = load_config(DEFAULT_YAML)
        snap = cfg.config_snapshot()
        json.dumps(snap)  # 可入 run.json
        assert set(snap) == {
            "providers",
            "tiers",
            "roles",
            "budgets",
            "planning",
            "run",
            "stages",
            "assets",
            "sandbox",
            "pricing",
        }
