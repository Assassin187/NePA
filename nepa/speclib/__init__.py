"""Specification validation and deterministic planning helpers."""

__all__ = [
    "PreparedArchitectureInputs", "build_planning_index", "build_test_manifest_metadata",
    "compile_delivery_constraints", "prepare_architecture_inputs", "validate_architecture",
]


def __getattr__(name: str):
    if name in {"PreparedArchitectureInputs", "build_planning_index", "build_test_manifest_metadata", "prepare_architecture_inputs"}:
        from .planning import PreparedArchitectureInputs, build_planning_index, build_test_manifest_metadata, prepare_architecture_inputs
        return locals()[name]
    if name == "compile_delivery_constraints":
        from .delivery import compile_delivery_constraints
        return compile_delivery_constraints
    if name == "validate_architecture":
        from .architecture import validate_architecture
        return validate_architecture
    raise AttributeError(name)
