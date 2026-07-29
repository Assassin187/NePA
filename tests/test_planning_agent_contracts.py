from __future__ import annotations

from nepa.agents.contracts import plan_critic_schema, task_shard_schema


def test_task_planner_contract_uses_local_ids_not_final_task_ids() -> None:
    schema = task_shard_schema()
    task = schema["$defs"]["task"]
    assert "local_id" in task["properties"]
    assert "id" not in task["properties"]


def test_plan_critic_contract_only_allows_structured_issue_list() -> None:
    schema = plan_critic_schema()
    assert schema["required"] == ["schema_version", "verdict", "issues"]
    assert schema["additionalProperties"] is False
