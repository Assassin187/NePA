"""Specification validation and deterministic planning helpers."""

__all__ = [
    "PreparedArchitectureInputs", "build_planning_index", "build_test_manifest_metadata",
    "compile_delivery_constraints", "prepare_architecture_inputs", "validate_architecture",
    "compile_delivery_blueprint", "link_plan", "normalize_plan_draft", "plan_lint", "initialize_plan_state",
    "plan_state_snapshot_lint", "validate_state_transition", "execution_state_lint",
]


def __getattr__(name: str):
    if name in {"PreparedArchitectureInputs", "build_planning_index", "build_test_manifest_metadata", "prepare_architecture_inputs"}:
        from .planning import PreparedArchitectureInputs, build_planning_index, build_test_manifest_metadata, prepare_architecture_inputs
        return locals()[name]
    if name == "compile_delivery_constraints":
        from .delivery import compile_delivery_constraints
        return compile_delivery_constraints
    if name == "compile_delivery_blueprint":
        from .delivery import compile_delivery_blueprint
        return compile_delivery_blueprint
    if name in {"link_plan", "normalize_plan_draft", "plan_lint"}:
        from .plan import link_plan, normalize_plan_draft, plan_lint
        return {"link_plan": link_plan, "normalize_plan_draft": normalize_plan_draft, "plan_lint": plan_lint}[name]
    if name in {"initialize_plan_state", "plan_state_snapshot_lint", "validate_state_transition", "execution_state_lint"}:
        from .plan_state import execution_state_lint, initialize_plan_state, plan_state_snapshot_lint, validate_state_transition
        return {
            "initialize_plan_state": initialize_plan_state,
            "plan_state_snapshot_lint": plan_state_snapshot_lint,
            "validate_state_transition": validate_state_transition,
            "execution_state_lint": execution_state_lint,
        }[name]
    if name == "validate_architecture":
        from .architecture import validate_architecture
        return validate_architecture
    raise AttributeError(name)
