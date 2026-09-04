"""The closed, protocol-neutral M1-3 Agent role catalog."""

from __future__ import annotations

from typing import Mapping

from .base import AgentRoleError, RoleDefinition


ROLE_REGISTRY: dict[str, RoleDefinition] = {
    "architecture_planner": RoleDefinition(
        role="architecture_planner",
        stages=("S4",),
        template_path="architecture_planner_initial.md",
        required_inputs=("planning_index", "delivery_constraints", "repair_context"),
    ),
    "task_planner": RoleDefinition(
        role="task_planner",
        stages=("S4",),
        template_path="task_planner.md",
        required_inputs=("work_package", "spec_slice", "adjacent_contracts", "test_metadata", "planning_budget"),
    ),
    "plan_critic": RoleDefinition(
        role="plan_critic",
        stages=("S4",),
        template_path="plan_critic.md",
        required_inputs=("candidate_plan_graph", "coverage_matrix", "lint_report"),
    ),
    "flat_plan_baseline": RoleDefinition(
        role="flat_plan_baseline",
        stages=("S4",),
        template_path="flat_plan_baseline.md",
        required_inputs=("planning_index", "delivery_constraints", "manifest_metadata"),
        availability="flat_only",
    ),
    "coder": RoleDefinition(
        role="coder",
        stages=("S6",),
        template_path="coder.md",
        required_inputs=("task", "spec_slice", "interface_files"),
    ),
    "diagnoser": RoleDefinition(
        role="diagnoser",
        stages=("S6", "S8"),
        template_path="diagnoser.md",
        required_inputs=("build_errors", "relevant_code"),
    ),
    "fixer": RoleDefinition(
        role="fixer",
        stages=("S6", "S8"),
        template_path="fixer.md",
        required_inputs=("diagnosis", "target_files"),
    ),
}


def registered_roles() -> tuple[str, ...]:
    """Return the stable catalog order used by the framework."""

    return tuple(ROLE_REGISTRY)


def get_role(role: str) -> RoleDefinition:
    try:
        return ROLE_REGISTRY[role]
    except KeyError as exc:
        raise AgentRoleError(f"unknown Agent role: {role}") from exc


def role_registry() -> Mapping[str, RoleDefinition]:
    """Expose the catalog without allowing callers to replace its mapping."""

    return ROLE_REGISTRY.copy()
